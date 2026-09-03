"""Atomic application of server-owned provider-trust lifecycle plans.

Lock order is fixed for every mutation: target lifecycle row, actor identity,
actor credential, then the actor's permission grants by UUID.  The transaction
owns idempotency, current authorization, CAS, plan application, and audit
staging; no component is durable independently.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider import (
    FacilityVerification,
    ProfessionalVerification,
    ProviderCredential,
    ProviderHospitalAffiliation,
    ProviderIdentity,
    ProviderTrustPermissionGrant,
)
from app.security.audit_context import AuditContext, AuditDomain
from app.services.audit_outbox import enqueue_audit_event
from app.services.policy_service import validate_idempotency_key
from app.services.provider_trust_authorization import (
    ProviderTrustAuthorizationService,
    TrustAuthorizationDecision,
    TrustAuthorizationDenialCode,
    TrustManagementAuthentication,
    affiliation_command_permission,
    facility_command_permission,
    professional_command_permission,
)
from app.services.provider_trust_lifecycle import (
    AffiliationTransitionCommand,
    AffiliationTransitionFacts,
    FacilityTransitionCommand,
    FacilityTransitionFacts,
    LifecyclePolicyError,
    LifecycleTransitionPlan,
    ProfessionalTransitionCommand,
    ProfessionalTransitionFacts,
    plan_affiliation_transition,
    plan_facility_transition,
    plan_professional_transition,
)


_IDEMPOTENCY_TENANT = "platform-provider-trust"
_OPERATIONS = {
    "professional": "provider.trust.professional.transition.v1",
    "facility": "provider.trust.facility.transition.v1",
    "affiliation": "provider.trust.affiliation.transition.v1",
}
_IDEMPOTENCY_SELECT = text("""
    SELECT request_hash, response_status, response_payload
    FROM public.mutation_idempotency
    WHERE tenant_id = :tenant_id AND operation = :operation AND idempotency_key = :key
""")
_IDEMPOTENCY_RESERVE = text("""
    INSERT INTO public.mutation_idempotency
      (tenant_id, actor_id, operation, resource_id, idempotency_key, request_hash, created_at, retention_expires_at)
    VALUES (:tenant_id, :actor_id, :operation, :resource_id, :key, :request_hash, now(), now() + interval '90 days')
    ON CONFLICT (tenant_id, operation, idempotency_key) DO NOTHING
    RETURNING id
""")
_IDEMPOTENCY_COMPLETE = text("""
    UPDATE public.mutation_idempotency
    SET response_status = 200, response_payload = CAST(:payload AS JSONB), resulting_resource_version = :version
    WHERE tenant_id = :tenant_id AND operation = :operation AND idempotency_key = :key
""")


class ProviderTrustLifecycleApplicationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ProviderTrustLifecycleResult:
    resource_id: UUID
    lifecycle_type: str
    old_state: str
    new_state: str
    version: int
    event_type: str
    idempotent_replay: bool


def _canonical(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ProviderTrustLifecycleApplicationError("INVALID_REQUEST")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _request_hash(
    *,
    actor_id: UUID,
    lifecycle_type: str,
    resource_id: UUID,
    command: Enum,
    expected_version: int,
    facts: object,
) -> str:
    canonical = {
        "actor_id": str(actor_id),
        "command": command.value,
        "expected_version": expected_version,
        "facts": _canonical(asdict(facts)),
        "lifecycle_type": lifecycle_type,
        "resource_id": str(resource_id),
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class ProviderTrustLifecycleApplicationService:
    """Internal-only transactional service; Phase 3F owns HTTP adaptation."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._authorization = ProviderTrustAuthorizationService()

    async def apply_professional(
        self,
        *,
        actor_id: UUID,
        authentication: TrustManagementAuthentication,
        resource_id: UUID,
        command: ProfessionalTransitionCommand,
        facts: ProfessionalTransitionFacts,
        expected_version: int,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> ProviderTrustLifecycleResult:
        return await self._apply(
            "professional",
            actor_id,
            authentication,
            resource_id,
            command,
            facts,
            expected_version,
            idempotency_key,
            now,
        )

    async def apply_facility(
        self,
        *,
        actor_id: UUID,
        authentication: TrustManagementAuthentication,
        resource_id: UUID,
        command: FacilityTransitionCommand,
        facts: FacilityTransitionFacts,
        expected_version: int,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> ProviderTrustLifecycleResult:
        return await self._apply(
            "facility",
            actor_id,
            authentication,
            resource_id,
            command,
            facts,
            expected_version,
            idempotency_key,
            now,
        )

    async def apply_affiliation(
        self,
        *,
        actor_id: UUID,
        authentication: TrustManagementAuthentication,
        resource_id: UUID,
        command: AffiliationTransitionCommand,
        facts: AffiliationTransitionFacts,
        expected_version: int,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> ProviderTrustLifecycleResult:
        return await self._apply(
            "affiliation",
            actor_id,
            authentication,
            resource_id,
            command,
            facts,
            expected_version,
            idempotency_key,
            now,
        )

    async def _apply(
        self,
        lifecycle_type: str,
        actor_id: UUID,
        authentication: TrustManagementAuthentication,
        resource_id: UUID,
        command: Enum,
        facts: object,
        expected_version: int,
        idempotency_key: str,
        now: datetime | None,
    ) -> ProviderTrustLifecycleResult:
        try:
            return await self._apply_transaction(
                lifecycle_type,
                actor_id,
                authentication,
                resource_id,
                command,
                facts,
                expected_version,
                idempotency_key,
                now,
            )
        except ProviderTrustLifecycleApplicationError:
            raise
        except Exception as exc:
            # An infrastructure failure must never be distinguishable as a
            # partially successful lifecycle decision.  The transaction block
            # beneath has already rolled back its reservation, lifecycle
            # mutation, and audit staging before this stable error escapes.
            raise ProviderTrustLifecycleApplicationError(
                "TRANSACTION_INTEGRITY_FAILURE"
            ) from exc

    async def _apply_transaction(
        self,
        lifecycle_type: str,
        actor_id: UUID,
        authentication: TrustManagementAuthentication,
        resource_id: UUID,
        command: Enum,
        facts: object,
        expected_version: int,
        idempotency_key: str,
        now: datetime | None,
    ) -> ProviderTrustLifecycleResult:
        try:
            key = validate_idempotency_key(idempotency_key)
        except ValueError as exc:
            raise ProviderTrustLifecycleApplicationError("INVALID_REQUEST") from exc
        if (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or expected_version < 1
        ):
            raise ProviderTrustLifecycleApplicationError("INVALID_REQUEST")
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ProviderTrustLifecycleApplicationError("INVALID_REQUEST")
        operation = _OPERATIONS[lifecycle_type]
        request_hash = _request_hash(
            actor_id=actor_id,
            lifecycle_type=lifecycle_type,
            resource_id=resource_id,
            command=command,
            expected_version=expected_version,
            facts=facts,
        )
        async with self.db.begin():
            existing = (
                await self.db.execute(
                    _IDEMPOTENCY_SELECT,
                    {
                        "tenant_id": _IDEMPOTENCY_TENANT,
                        "operation": operation,
                        "key": key,
                    },
                )
            ).first()
            if existing is not None:
                return self._replay(existing, request_hash, lifecycle_type, resource_id)
            reserved = (
                await self.db.execute(
                    _IDEMPOTENCY_RESERVE,
                    {
                        "tenant_id": _IDEMPOTENCY_TENANT,
                        "actor_id": str(actor_id),
                        "operation": operation,
                        "resource_id": str(resource_id),
                        "key": key,
                        "request_hash": request_hash,
                    },
                )
            ).first()
            if reserved is None:
                existing = (
                    await self.db.execute(
                        _IDEMPOTENCY_SELECT,
                        {
                            "tenant_id": _IDEMPOTENCY_TENANT,
                            "operation": operation,
                            "key": key,
                        },
                    )
                ).first()
                if existing is None:
                    raise ProviderTrustLifecycleApplicationError(
                        "IDEMPOTENCY_IN_PROGRESS"
                    )
                return self._replay(existing, request_hash, lifecycle_type, resource_id)

            (
                target,
                target_provider_id,
                facility_id,
                audit_context,
            ) = await self._lock_target(lifecycle_type, resource_id)
            actor, credential, grants = await self._lock_actor_authority(actor_id)
            decision = self._authorize_locked(
                lifecycle_type,
                command,
                actor,
                credential,
                grants,
                target_provider_id,
                facility_id,
                authentication,
                moment,
            )
            if not decision.allowed:
                raise ProviderTrustLifecycleApplicationError("AUTHORIZATION_DENIED")
            if target.version != expected_version:
                raise ProviderTrustLifecycleApplicationError(
                    "LIFECYCLE_VERSION_CONFLICT"
                )
            plan = self._plan(lifecycle_type, target, command, facts, actor_id, moment)
            if (
                plan.expected_version != target.version
                or plan.next_version != target.version + 1
            ):
                raise ProviderTrustLifecycleApplicationError(
                    "TRANSACTION_INTEGRITY_FAILURE"
                )
            self._apply_plan(lifecycle_type, target, plan)
            result = ProviderTrustLifecycleResult(
                resource_id=resource_id,
                lifecycle_type=lifecycle_type,
                old_state=plan.old_state,
                new_state=plan.new_state,
                version=plan.next_version,
                event_type=plan.event_type.value,
                idempotent_replay=False,
            )
            audit_key = (
                "provider-trust:"
                + hashlib.sha256(
                    f"{lifecycle_type}:{resource_id}:{key}".encode()
                ).hexdigest()[:48]
            )
            await enqueue_audit_event(
                self.db,
                audit_context=audit_context,
                idempotency_key=audit_key,
                actor_id=str(actor_id),
                event_type=plan.event_type.value,
                target_id=str(resource_id),
                patient_id=None,
                metadata={
                    "command": plan.command,
                    "lifecycle_type": lifecycle_type,
                    "new_version": plan.next_version,
                    "old_state": plan.old_state,
                    "new_state": plan.new_state,
                    **({"facility_id": str(facility_id)} if facility_id else {}),
                },
            )
            payload = json.dumps(
                {
                    "event_type": result.event_type,
                    "lifecycle_type": lifecycle_type,
                    "new_state": result.new_state,
                    "old_state": result.old_state,
                    "resource_id": str(resource_id),
                    "version": result.version,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            completed = await self.db.execute(
                _IDEMPOTENCY_COMPLETE,
                {
                    "tenant_id": _IDEMPOTENCY_TENANT,
                    "operation": operation,
                    "key": key,
                    "payload": payload,
                    "version": result.version,
                },
            )
            if completed.rowcount != 1:
                raise ProviderTrustLifecycleApplicationError(
                    "TRANSACTION_INTEGRITY_FAILURE"
                )
            return result

    async def _lock_target(self, kind: str, resource_id: UUID):
        model = {
            "professional": ProfessionalVerification,
            "facility": FacilityVerification,
            "affiliation": ProviderHospitalAffiliation,
        }[kind]
        target = (
            await self.db.execute(
                select(model).where(model.id == resource_id).with_for_update()
            )
        ).scalar_one_or_none()
        if target is None:
            raise ProviderTrustLifecycleApplicationError("RESOURCE_NOT_FOUND")
        if kind == "professional":
            return (
                target,
                target.provider_id,
                None,
                AuditContext.platform(domain=AuditDomain.PLATFORM),
            )
        facility_id = target.facility_id if kind == "facility" else target.hospital_id
        target_provider_id = None if kind == "facility" else target.provider_id
        return (
            target,
            target_provider_id,
            facility_id,
            AuditContext.for_hospital(
                hospital_id=str(facility_id), domain=AuditDomain.PLATFORM
            ),
        )

    async def _lock_actor_authority(self, actor_id: UUID):
        actor = (
            await self.db.execute(
                select(ProviderIdentity)
                .where(ProviderIdentity.id == actor_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        credential = (
            None
            if actor is None
            else (
                await self.db.execute(
                    select(ProviderCredential)
                    .where(ProviderCredential.provider_id == actor_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
        )
        if actor is not None:
            actor.credential = credential
        grants = (
            []
            if actor is None
            else list(
                (
                    await self.db.execute(
                        select(ProviderTrustPermissionGrant)
                        .where(ProviderTrustPermissionGrant.provider_id == actor_id)
                        .order_by(ProviderTrustPermissionGrant.id)
                        .with_for_update()
                    )
                ).scalars()
            )
        )
        return actor, credential, grants

    def _authorize_locked(
        self,
        kind: str,
        command: Enum,
        actor,
        credential,
        grants,
        target_provider_id,
        facility_id,
        authentication,
        now: datetime,
    ) -> TrustAuthorizationDecision:
        if actor is not None:
            actor.credential = credential
        denial = self._authorization._strong_auth(actor, authentication, now)
        if denial is not None:
            return self._authorization._deny(denial)
        if kind == "professional" and command is ProfessionalTransitionCommand.SUBMIT:
            if actor.id != target_provider_id:
                return self._authorization._deny(
                    TrustAuthorizationDenialCode.TARGET_SCOPE_INVALID
                )
            return TrustAuthorizationDecision(True, None, None, None)
        if kind == "professional":
            if actor.id == target_provider_id:
                return self._authorization._deny(
                    TrustAuthorizationDenialCode.SELF_REVIEW_PROHIBITED
                )
            permission = professional_command_permission(command)
        elif kind == "facility":
            permission = facility_command_permission(command)
        else:
            if target_provider_id == actor.id:
                return self._authorization._deny(
                    TrustAuthorizationDenialCode.SELF_AFFILIATION_MANAGEMENT_PROHIBITED
                )
            permission = affiliation_command_permission(command)
        return self._authorization._matching_grant(grants, permission, facility_id, now)

    def _plan(
        self,
        kind: str,
        target,
        command: Enum,
        facts: object,
        actor_id: UUID,
        now: datetime,
    ) -> LifecycleTransitionPlan:
        try:
            if kind == "professional":
                if command is not ProfessionalTransitionCommand.SUBMIT:
                    facts = ProfessionalTransitionFacts(
                        **{**asdict(facts), "reviewer_id": str(actor_id)}
                    )
                return plan_professional_transition(
                    target.status, command, facts, now, current_version=target.version
                )
            if kind == "facility":
                facts = FacilityTransitionFacts(
                    **{**asdict(facts), "reviewer_id": str(actor_id)}
                )
                return plan_facility_transition(
                    target.status, command, facts, now, current_version=target.version
                )
            return plan_affiliation_transition(
                target.trust_status, command, facts, now, current_version=target.version
            )
        except (LifecyclePolicyError, TypeError, ValueError) as exc:
            raise ProviderTrustLifecycleApplicationError(
                "LIFECYCLE_POLICY_DENIED"
            ) from exc

    def _apply_plan(self, kind: str, target, plan: LifecycleTransitionPlan) -> None:
        allowed = {
            "professional": {
                column.name for column in ProfessionalVerification.__table__.columns
            }
            - {"id", "provider_id", "created_at", "updated_at"},
            "facility": {
                column.name for column in FacilityVerification.__table__.columns
            }
            - {"id", "facility_id", "created_at", "updated_at"},
            "affiliation": {
                "trust_status",
                "valid_from",
                "valid_until",
                "decision_reason_code",
                "version",
            },
        }[kind]
        status_field = "trust_status" if kind == "affiliation" else "status"
        for update in plan.updates:
            if update.field not in allowed:
                raise ProviderTrustLifecycleApplicationError(
                    "TRANSACTION_INTEGRITY_FAILURE"
                )
            setattr(target, update.field, update.value)
        for field in plan.clears:
            if field not in allowed:
                raise ProviderTrustLifecycleApplicationError(
                    "TRANSACTION_INTEGRITY_FAILURE"
                )
            setattr(target, field, None)
        if (
            getattr(target, status_field) != plan.new_state
            or target.version != plan.next_version
        ):
            raise ProviderTrustLifecycleApplicationError(
                "TRANSACTION_INTEGRITY_FAILURE"
            )

    def _replay(
        self, row, request_hash: str, kind: str, resource_id: UUID
    ) -> ProviderTrustLifecycleResult:
        mapping = row._mapping
        if mapping["request_hash"] != request_hash:
            raise ProviderTrustLifecycleApplicationError("IDEMPOTENCY_KEY_REUSED")
        payload = mapping["response_payload"]
        if (
            mapping["response_status"] != 200
            or not isinstance(payload, dict)
            or payload.get("resource_id") != str(resource_id)
            or payload.get("lifecycle_type") != kind
        ):
            raise ProviderTrustLifecycleApplicationError("IDEMPOTENCY_IN_PROGRESS")
        return ProviderTrustLifecycleResult(
            resource_id=resource_id,
            lifecycle_type=kind,
            old_state=str(payload["old_state"]),
            new_state=str(payload["new_state"]),
            version=int(payload["version"]),
            event_type=str(payload["event_type"]),
            idempotent_replay=True,
        )
