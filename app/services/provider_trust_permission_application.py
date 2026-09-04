"""Atomic application of server-owned provider-trust permission administration plans.

Lock order is strictly deterministic: all involved ProviderIdentity rows in sorted
UUID order, then their ProviderCredential rows in that same order, then their
ProviderTrustPermissionGrant rows by provider UUID ascending then grant UUID ascending.
The transaction owns idempotency, current authorization, step-up MFA freshness,
Phase-4B domain planning, grant/revoke mutation, and audit staging; no component is durable
independently.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider import (
    HospitalRegistry,
    ProviderCredential,
    ProviderIdentity,
    ProviderTrustPermissionGrant,
)
from app.security.audit_context import AuditContext, AuditDomain
from app.security.trust_management_permissions import (
    TrustManagementPermission,
    TrustPermissionScope,
    scope_for_permission,
)
from app.services.audit_outbox import enqueue_audit_event
from app.services.policy_service import validate_idempotency_key
from app.services.provider_trust_authorization import (
    ProviderTrustAuthorizationService,
    TrustManagementAuthentication,
)
from app.services.provider_trust_permission_policy import (
    ExistingGrantSlotFacts,
    GrantPlan,
    GrantRequestFacts,
    RevocationReasonCode,
    RevokePlan,
    RevokeRequestFacts,
    TargetProviderEligibilityFacts,
    TrustPermissionCommand,
    TrustPermissionPolicyError,
    plan_grant_permission,
    plan_revoke_permission,
)

_IDEMPOTENCY_TENANT = "platform-provider-trust"
_OPERATION_GRANT = "provider.trust.permission.grant.v1"
_OPERATION_REVOKE = "provider.trust.permission.revoke.v1"

_MFA_FRESHNESS_WINDOW = timedelta(minutes=15)

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
    SET response_status = 200, response_payload = CAST(:payload AS JSONB), resulting_resource_version = NULL
    WHERE tenant_id = :tenant_id AND operation = :operation AND idempotency_key = :key
""")


class ProviderTrustPermissionApplicationError(RuntimeError):
    """Stable failure for permission administration application."""

    def __init__(self, code: str, *, policy_code: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.policy_code = policy_code


@dataclass(frozen=True, slots=True)
class ProviderTrustPermissionApplicationResult:
    """Safe structural result of an administrative grant or revoke mutation."""

    command: str
    grant_id: UUID
    target_provider_id: UUID
    permission: str
    scope_type: str
    facility_id: UUID | None
    superseded_grant_id: UUID | None
    event_types: list[str]
    idempotent_replay: bool


def _canonical_dt(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ProviderTrustPermissionApplicationError("INVALID_REQUEST")
    return dt.astimezone(timezone.utc).isoformat()


def _request_hash_grant(
    *,
    actor_id: UUID,
    target_provider_id: UUID,
    permission: TrustManagementPermission,
    scope_type: TrustPermissionScope,
    facility_id: UUID | None,
    valid_from: datetime | None,
    valid_until: datetime | None,
    governance_reference: str | None,
) -> str:
    canonical = {
        "actor_id": str(actor_id),
        "facility_id": str(facility_id) if facility_id else None,
        "governance_reference": governance_reference.strip()
        if governance_reference
        else None,
        "operation": _OPERATION_GRANT,
        "permission": permission.value
        if isinstance(permission, Enum)
        else str(permission),
        "scope_type": scope_type.value
        if isinstance(scope_type, Enum)
        else str(scope_type),
        "target_provider_id": str(target_provider_id),
        "valid_from": _canonical_dt(valid_from),
        "valid_until": _canonical_dt(valid_until),
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _request_hash_revoke(
    *,
    actor_id: UUID,
    grant_id: UUID,
    revocation_reason_code: RevocationReasonCode,
    governance_reference: str | None,
) -> str:
    canonical = {
        "actor_id": str(actor_id),
        "governance_reference": governance_reference.strip()
        if governance_reference
        else None,
        "grant_id": str(grant_id),
        "operation": _OPERATION_REVOKE,
        "revocation_reason_code": (
            revocation_reason_code.value
            if isinstance(revocation_reason_code, Enum)
            else str(revocation_reason_code)
        ),
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class ProviderTrustPermissionApplicationService:
    """Transactional boundary for subordinate permission grant and revoke operations."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._authorization = ProviderTrustAuthorizationService()

    def _replay(
        self,
        existing_row: Any,
        expected_hash: str,
        operation: str,
        resource_id: UUID,
    ) -> ProviderTrustPermissionApplicationResult:
        if existing_row.request_hash != expected_hash:
            raise ProviderTrustPermissionApplicationError("IDEMPOTENCY_KEY_REUSED")
        if existing_row.response_status != 200 or not existing_row.response_payload:
            raise ProviderTrustPermissionApplicationError("IDEMPOTENCY_IN_PROGRESS")
        try:
            payload = (
                existing_row.response_payload
                if isinstance(existing_row.response_payload, dict)
                else json.loads(existing_row.response_payload)
            )
            return ProviderTrustPermissionApplicationResult(
                command=payload["command"],
                grant_id=UUID(payload["grant_id"]),
                target_provider_id=UUID(payload["target_provider_id"]),
                permission=payload["permission"],
                scope_type=payload["scope_type"],
                facility_id=UUID(payload["facility_id"])
                if payload.get("facility_id")
                else None,
                superseded_grant_id=(
                    UUID(payload["superseded_grant_id"])
                    if payload.get("superseded_grant_id")
                    else None
                ),
                event_types=list(payload["event_types"]),
                idempotent_replay=True,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderTrustPermissionApplicationError(
                "TRANSACTION_INTEGRITY_FAILURE"
            ) from exc

    async def _lock_involved_providers(
        self,
        provider_ids: set[UUID],
    ) -> tuple[
        dict[UUID, ProviderIdentity | None],
        dict[UUID, ProviderCredential | None],
        dict[UUID, list[ProviderTrustPermissionGrant]],
    ]:
        sorted_ids = sorted(provider_ids)
        identities: dict[UUID, ProviderIdentity | None] = {}
        credentials: dict[UUID, ProviderCredential | None] = {}
        grants: dict[UUID, list[ProviderTrustPermissionGrant]] = {}

        for pid in sorted_ids:
            ident_res = await self.db.execute(
                select(ProviderIdentity)
                .where(ProviderIdentity.id == pid)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            ident = ident_res.scalar_one_or_none()
            identities[pid] = ident

            cred_res = await self.db.execute(
                select(ProviderCredential)
                .where(ProviderCredential.provider_id == pid)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            cred = cred_res.scalar_one_or_none()
            credentials[pid] = cred
            if ident is not None:
                ident.credential = cred

            grants_res = await self.db.execute(
                select(ProviderTrustPermissionGrant)
                .where(ProviderTrustPermissionGrant.provider_id == pid)
                .order_by(ProviderTrustPermissionGrant.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            grants[pid] = list(grants_res.scalars().all())

        return identities, credentials, grants

    def _authorize_actor_authority(
        self,
        actor: ProviderIdentity | None,
        actor_grants: list[ProviderTrustPermissionGrant],
        authentication: TrustManagementAuthentication,
        moment: datetime,
    ) -> None:
        # Strong authentication check
        auth_denial = self._authorization._strong_auth(actor, authentication, moment)
        if auth_denial is not None:
            raise ProviderTrustPermissionApplicationError("AUTHORIZATION_DENIED")

        # Step-Up MFA Freshness check (15 minutes)
        mfa_at = authentication.mfa_verified_at
        if (
            mfa_at is None
            or mfa_at.tzinfo is None
            or mfa_at.utcoffset() is None
            or mfa_at > moment
        ):
            raise ProviderTrustPermissionApplicationError("AUTHORIZATION_DENIED")
        if moment - mfa_at > _MFA_FRESHNESS_WINDOW:
            raise ProviderTrustPermissionApplicationError("MFA_STEP_UP_REQUIRED")

        # Trust authorization check for TRUST_PERMISSION_MANAGE
        decision = self._authorization._matching_grant(
            actor_grants,
            TrustManagementPermission.TRUST_PERMISSION_MANAGE,
            None,
            moment,
        )
        if not decision.allowed or decision.scope is not TrustPermissionScope.GLOBAL:
            raise ProviderTrustPermissionApplicationError("AUTHORIZATION_DENIED")

    async def apply_grant(
        self,
        *,
        actor_id: UUID,
        authentication: TrustManagementAuthentication,
        target_provider_id: UUID,
        permission: TrustManagementPermission,
        facility_id: UUID | None = None,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        governance_reference: str | None = None,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> ProviderTrustPermissionApplicationResult:
        try:
            key = validate_idempotency_key(idempotency_key)
        except ValueError as exc:
            raise ProviderTrustPermissionApplicationError("INVALID_REQUEST") from exc

        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ProviderTrustPermissionApplicationError("INVALID_REQUEST")

        if not isinstance(permission, TrustManagementPermission):
            try:
                permission = TrustManagementPermission(permission)
            except (ValueError, TypeError) as exc:
                raise ProviderTrustPermissionApplicationError(
                    "INVALID_REQUEST"
                ) from exc

        # Phase 4C rejects root permission administration
        if permission is TrustManagementPermission.TRUST_PERMISSION_MANAGE:
            raise ProviderTrustPermissionApplicationError(
                "TRUST_PERMISSION_POLICY_DENIED",
                policy_code="ROOT_PERMISSION_OFFLINE_ONLY",
            )

        try:
            expected_scope = scope_for_permission(permission)
        except (KeyError, TypeError) as exc:
            raise ProviderTrustPermissionApplicationError("INVALID_REQUEST") from exc

        request_hash = _request_hash_grant(
            actor_id=actor_id,
            target_provider_id=target_provider_id,
            permission=permission,
            scope_type=expected_scope,
            facility_id=facility_id,
            valid_from=valid_from,
            valid_until=valid_until,
            governance_reference=governance_reference,
        )

        try:
            async with self.db.begin():
                # Idempotency select/reserve
                existing = (
                    await self.db.execute(
                        _IDEMPOTENCY_SELECT,
                        {
                            "tenant_id": _IDEMPOTENCY_TENANT,
                            "operation": _OPERATION_GRANT,
                            "key": key,
                        },
                    )
                ).first()
                if existing is not None:
                    return self._replay(
                        existing, request_hash, _OPERATION_GRANT, target_provider_id
                    )

                reserved = (
                    await self.db.execute(
                        _IDEMPOTENCY_RESERVE,
                        {
                            "tenant_id": _IDEMPOTENCY_TENANT,
                            "actor_id": str(actor_id),
                            "operation": _OPERATION_GRANT,
                            "resource_id": str(target_provider_id),
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
                                "operation": _OPERATION_GRANT,
                                "key": key,
                            },
                        )
                    ).first()
                    if existing is None:
                        raise ProviderTrustPermissionApplicationError(
                            "IDEMPOTENCY_IN_PROGRESS"
                        )
                    return self._replay(
                        existing, request_hash, _OPERATION_GRANT, target_provider_id
                    )

                # Lock involved providers in sorted order
                involved_ids = {actor_id, target_provider_id}
                identities, credentials, grants = await self._lock_involved_providers(
                    involved_ids
                )

                # Authorize actor authority
                actor = identities.get(actor_id)
                actor_grants = grants.get(actor_id, [])
                self._authorize_actor_authority(
                    actor, actor_grants, authentication, moment
                )

                # Validate facility existence if facility permission
                if expected_scope is TrustPermissionScope.FACILITY:
                    if facility_id is None:
                        raise ProviderTrustPermissionApplicationError("INVALID_REQUEST")
                    fac = await self.db.get(HospitalRegistry, facility_id)
                    if fac is None:
                        raise ProviderTrustPermissionApplicationError(
                            "RESOURCE_NOT_FOUND"
                        )

                # Construct target eligibility from locked rows
                target_ident = identities.get(target_provider_id)
                target_cred = credentials.get(target_provider_id)
                eligibility = TargetProviderEligibilityFacts(
                    provider_exists=target_ident is not None,
                    account_is_active=bool(target_ident and target_ident.is_active),
                    account_status=target_ident.status if target_ident else "",
                    credential_exists=target_cred is not None,
                    credential_is_active=bool(target_cred and target_cred.is_active),
                )

                # Discover matching unrevoked grant slot in target's locked grants
                target_grants = grants.get(target_provider_id, [])
                perm_str = permission.value
                scope_str = expected_scope.value
                matching_unrevoked = [
                    g
                    for g in target_grants
                    if g.revoked_at is None
                    and g.permission == perm_str
                    and g.scope_type == scope_str
                    and (g.facility_id == facility_id)
                ]
                if len(matching_unrevoked) > 1:
                    raise ProviderTrustPermissionApplicationError(
                        "TRANSACTION_INTEGRITY_FAILURE"
                    )

                existing_slot = matching_unrevoked[0] if matching_unrevoked else None
                existing_slot_facts = (
                    ExistingGrantSlotFacts(
                        grant_id=existing_slot.id,
                        provider_id=existing_slot.provider_id,
                        permission=existing_slot.permission,
                        scope_type=existing_slot.scope_type,
                        facility_id=existing_slot.facility_id,
                        valid_from=existing_slot.valid_from,
                        valid_until=existing_slot.valid_until,
                        revoked_at=existing_slot.revoked_at,
                    )
                    if existing_slot is not None
                    else None
                )

                facts = GrantRequestFacts(
                    actor_provider_id=actor_id,
                    target_provider_id=target_provider_id,
                    permission=permission,
                    facility_id=facility_id,
                    valid_from=valid_from,
                    valid_until=valid_until,
                    governance_reference=governance_reference,
                    target_eligibility=eligibility,
                    existing_slot_grant=existing_slot_facts,
                )

                # Invoke pure policy
                try:
                    plan: GrantPlan = plan_grant_permission(facts, moment)
                except TrustPermissionPolicyError as exc:
                    raise ProviderTrustPermissionApplicationError(
                        "TRUST_PERMISSION_POLICY_DENIED", policy_code=exc.code
                    ) from exc

                # Apply mutations
                new_grant_id = uuid4()
                event_types: list[str] = []

                # 1. Supersession of expired unrevoked slot if planned
                if plan.superseded_grant_id is not None:
                    if (
                        existing_slot is None
                        or existing_slot.id != plan.superseded_grant_id
                    ):
                        raise ProviderTrustPermissionApplicationError(
                            "TRANSACTION_INTEGRITY_FAILURE"
                        )
                    existing_slot.revoked_at = moment
                    event_types.append(plan.superseded_revoke_event.value)  # type: ignore

                # 2. Create new grant row
                new_grant = ProviderTrustPermissionGrant(
                    id=new_grant_id,
                    provider_id=target_provider_id,
                    permission=plan.permission.value,
                    scope_type=plan.scope_type.value,
                    facility_id=plan.facility_id,
                    granted_at=moment,
                    valid_from=plan.valid_from,
                    valid_until=plan.valid_until,
                    revoked_at=None,
                    granted_by_actor_id=str(actor_id),
                    governance_reference=plan.governance_reference,
                )
                self.db.add(new_grant)
                event_types.append(plan.grant_event.value)

                # Derive audit context
                if plan.scope_type is TrustPermissionScope.GLOBAL:
                    audit_context = AuditContext.platform(domain=AuditDomain.PLATFORM)
                else:
                    audit_context = AuditContext.for_hospital(
                        hospital_id=str(plan.facility_id), domain=AuditDomain.PLATFORM
                    )

                # Stage supersession revoke audit event if applicable
                if plan.superseded_grant_id is not None:
                    supersede_audit_key = f"provider-trust-permission:supersede-revoke:{plan.superseded_grant_id}:{key}"
                    await enqueue_audit_event(
                        self.db,
                        audit_context=audit_context,
                        idempotency_key=supersede_audit_key,
                        actor_id=str(actor_id),
                        event_type=plan.superseded_revoke_event.value,  # type: ignore
                        target_id=str(plan.superseded_grant_id),
                        patient_id=None,
                        metadata={
                            "command": "REVOKE",
                            "facility_id": str(plan.facility_id)
                            if plan.facility_id
                            else None,
                            "permission": plan.permission.value,
                            "revocation_reason_code": plan.superseded_revocation_reason.value,  # type: ignore
                            "scope_type": plan.scope_type.value,
                            "superseded_by_grant_id": str(new_grant_id),
                        },
                    )

                # Stage grant audit event
                grant_audit_key = (
                    f"provider-trust-permission:grant:{new_grant_id}:{key}"
                )
                await enqueue_audit_event(
                    self.db,
                    audit_context=audit_context,
                    idempotency_key=grant_audit_key,
                    actor_id=str(actor_id),
                    event_type=plan.grant_event.value,
                    target_id=str(new_grant_id),
                    patient_id=None,
                    metadata={
                        "command": "GRANT",
                        "facility_id": str(plan.facility_id)
                        if plan.facility_id
                        else None,
                        "governance_reference": plan.governance_reference,
                        "permission": plan.permission.value,
                        "scope_type": plan.scope_type.value,
                        "superseded_grant_id": (
                            str(plan.superseded_grant_id)
                            if plan.superseded_grant_id
                            else None
                        ),
                        "target_provider_id": str(target_provider_id),
                        "valid_from": plan.valid_from.isoformat()
                        if plan.valid_from
                        else None,
                        "valid_until": plan.valid_until.isoformat()
                        if plan.valid_until
                        else None,
                    },
                )

                # Complete idempotency record
                result = ProviderTrustPermissionApplicationResult(
                    command=TrustPermissionCommand.GRANT.value,
                    grant_id=new_grant_id,
                    target_provider_id=target_provider_id,
                    permission=plan.permission.value,
                    scope_type=plan.scope_type.value,
                    facility_id=plan.facility_id,
                    superseded_grant_id=plan.superseded_grant_id,
                    event_types=event_types,
                    idempotent_replay=False,
                )
                payload = json.dumps(
                    {
                        "command": result.command,
                        "event_types": result.event_types,
                        "facility_id": str(result.facility_id)
                        if result.facility_id
                        else None,
                        "grant_id": str(result.grant_id),
                        "permission": result.permission,
                        "scope_type": result.scope_type,
                        "superseded_grant_id": (
                            str(result.superseded_grant_id)
                            if result.superseded_grant_id
                            else None
                        ),
                        "target_provider_id": str(result.target_provider_id),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                await self.db.execute(
                    _IDEMPOTENCY_COMPLETE,
                    {
                        "tenant_id": _IDEMPOTENCY_TENANT,
                        "operation": _OPERATION_GRANT,
                        "key": key,
                        "payload": payload,
                    },
                )
                return result
        except ProviderTrustPermissionApplicationError:
            raise
        except Exception as exc:
            raise ProviderTrustPermissionApplicationError(
                "TRANSACTION_INTEGRITY_FAILURE"
            ) from exc

    async def apply_revoke(
        self,
        *,
        actor_id: UUID,
        authentication: TrustManagementAuthentication,
        grant_id: UUID,
        revocation_reason_code: RevocationReasonCode,
        governance_reference: str | None = None,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> ProviderTrustPermissionApplicationResult:
        try:
            key = validate_idempotency_key(idempotency_key)
        except ValueError as exc:
            raise ProviderTrustPermissionApplicationError("INVALID_REQUEST") from exc

        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ProviderTrustPermissionApplicationError("INVALID_REQUEST")

        if not isinstance(revocation_reason_code, RevocationReasonCode):
            try:
                revocation_reason_code = RevocationReasonCode(revocation_reason_code)
            except (ValueError, TypeError) as exc:
                raise ProviderTrustPermissionApplicationError(
                    "INVALID_REQUEST"
                ) from exc

        request_hash = _request_hash_revoke(
            actor_id=actor_id,
            grant_id=grant_id,
            revocation_reason_code=revocation_reason_code,
            governance_reference=governance_reference,
        )

        try:
            async with self.db.begin():
                # Idempotency select/reserve
                existing = (
                    await self.db.execute(
                        _IDEMPOTENCY_SELECT,
                        {
                            "tenant_id": _IDEMPOTENCY_TENANT,
                            "operation": _OPERATION_REVOKE,
                            "key": key,
                        },
                    )
                ).first()
                if existing is not None:
                    return self._replay(
                        existing, request_hash, _OPERATION_REVOKE, grant_id
                    )

                reserved = (
                    await self.db.execute(
                        _IDEMPOTENCY_RESERVE,
                        {
                            "tenant_id": _IDEMPOTENCY_TENANT,
                            "actor_id": str(actor_id),
                            "operation": _OPERATION_REVOKE,
                            "resource_id": str(grant_id),
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
                                "operation": _OPERATION_REVOKE,
                                "key": key,
                            },
                        )
                    ).first()
                    if existing is None:
                        raise ProviderTrustPermissionApplicationError(
                            "IDEMPOTENCY_IN_PROGRESS"
                        )
                    return self._replay(
                        existing, request_hash, _OPERATION_REVOKE, grant_id
                    )

                # Non-locking discovery of grant to find target_provider_id
                target_provider_id = (
                    await self.db.execute(
                        select(ProviderTrustPermissionGrant.provider_id).where(
                            ProviderTrustPermissionGrant.id == grant_id
                        )
                    )
                ).scalar_one_or_none()
                if target_provider_id is None:
                    raise ProviderTrustPermissionApplicationError("RESOURCE_NOT_FOUND")

                # Lock involved providers in sorted order
                involved_ids = {actor_id, target_provider_id}
                identities, credentials, grants = await self._lock_involved_providers(
                    involved_ids
                )

                # Authorize actor authority
                actor = identities.get(actor_id)
                actor_grants = grants.get(actor_id, [])
                self._authorize_actor_authority(
                    actor, actor_grants, authentication, moment
                )

                # Find target grant from locked rows
                target_grants = grants.get(target_provider_id, [])
                locked_target_grant = next(
                    (g for g in target_grants if g.id == grant_id), None
                )
                if locked_target_grant is None:
                    raise ProviderTrustPermissionApplicationError("RESOURCE_NOT_FOUND")

                # Construct ExistingGrantSlotFacts from locked grant
                target_grant_facts = ExistingGrantSlotFacts(
                    grant_id=locked_target_grant.id,
                    provider_id=locked_target_grant.provider_id,
                    permission=locked_target_grant.permission,
                    scope_type=locked_target_grant.scope_type,
                    facility_id=locked_target_grant.facility_id,
                    valid_from=locked_target_grant.valid_from,
                    valid_until=locked_target_grant.valid_until,
                    revoked_at=locked_target_grant.revoked_at,
                )

                facts = RevokeRequestFacts(
                    actor_provider_id=actor_id,
                    target_grant_id=grant_id,
                    target_provider_id=target_provider_id,
                    target_grant=target_grant_facts,
                    revocation_reason_code=revocation_reason_code,
                    governance_reference=governance_reference,
                )

                # Invoke pure policy
                try:
                    plan: RevokePlan = plan_revoke_permission(facts, moment)
                except TrustPermissionPolicyError as exc:
                    raise ProviderTrustPermissionApplicationError(
                        "TRUST_PERMISSION_POLICY_DENIED", policy_code=exc.code
                    ) from exc

                # Apply mutation: update revoked_at only
                locked_target_grant.revoked_at = moment

                # Derive audit context
                if plan.scope_type is TrustPermissionScope.GLOBAL:
                    audit_context = AuditContext.platform(domain=AuditDomain.PLATFORM)
                else:
                    audit_context = AuditContext.for_hospital(
                        hospital_id=str(plan.facility_id), domain=AuditDomain.PLATFORM
                    )

                # Stage revoke audit event
                revoke_audit_key = f"provider-trust-permission:revoke:{grant_id}:{key}"
                await enqueue_audit_event(
                    self.db,
                    audit_context=audit_context,
                    idempotency_key=revoke_audit_key,
                    actor_id=str(actor_id),
                    event_type=plan.revoke_event.value,
                    target_id=str(grant_id),
                    patient_id=None,
                    metadata={
                        "command": "REVOKE",
                        "facility_id": str(plan.facility_id)
                        if plan.facility_id
                        else None,
                        "governance_reference": plan.governance_reference,
                        "permission": plan.permission.value,
                        "revocation_reason_code": plan.revocation_reason_code.value,
                        "scope_type": plan.scope_type.value,
                        "target_provider_id": str(target_provider_id),
                    },
                )

                # Complete idempotency record
                result = ProviderTrustPermissionApplicationResult(
                    command=TrustPermissionCommand.REVOKE.value,
                    grant_id=grant_id,
                    target_provider_id=target_provider_id,
                    permission=plan.permission.value,
                    scope_type=plan.scope_type.value,
                    facility_id=plan.facility_id,
                    superseded_grant_id=None,
                    event_types=[plan.revoke_event.value],
                    idempotent_replay=False,
                )
                payload = json.dumps(
                    {
                        "command": result.command,
                        "event_types": result.event_types,
                        "facility_id": str(result.facility_id)
                        if result.facility_id
                        else None,
                        "grant_id": str(result.grant_id),
                        "permission": result.permission,
                        "scope_type": result.scope_type,
                        "superseded_grant_id": None,
                        "target_provider_id": str(result.target_provider_id),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                await self.db.execute(
                    _IDEMPOTENCY_COMPLETE,
                    {
                        "tenant_id": _IDEMPOTENCY_TENANT,
                        "operation": _OPERATION_REVOKE,
                        "key": key,
                        "payload": payload,
                    },
                )
                return result
        except ProviderTrustPermissionApplicationError:
            raise
        except Exception as exc:
            raise ProviderTrustPermissionApplicationError(
                "TRANSACTION_INTEGRITY_FAILURE"
            ) from exc
