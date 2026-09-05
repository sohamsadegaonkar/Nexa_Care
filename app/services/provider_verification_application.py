"""Atomic application of server-owned registry observations and system authority (Phase 5E).

This service implements the transactional execution boundary for automated
registry observations.  It locks the authoritative verification target, validates
the structural lookup envelope and source automation policy, reconstructs the pure
Phase-5D decision context, inserts immutable evidence observations, creates manual
review work queue entries when failing closed, applies authorized lifecycle
mutations, links server provenance foreign keys, and stages durable audit events.

Permanent authority invariant:
    REGISTRY OBSERVATION
    != DECISION POLICY
    != AUTOMATION ELIGIBILITY
    != SYSTEM EXECUTION AUTHORITY
    != LIFECYCLE MUTATION
    != CLINICAL AUTHORITY

Internal service only — zero HTTP route exposure.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider import (
    FacilityVerification,
    FacilityVerificationStatus,
    ProfessionalVerification,
    ProfessionalVerificationStatus,
    ProviderTrustVerificationEvidence,
    ProviderTrustVerificationReviewWork,
    VerificationEvidenceOrigin,
    VerificationReviewWorkStatus,
    VerificationSourceFailureReason,
    VerificationEvidenceOutcome,
)
from app.observability.provider_trust_events import ProviderTrustAuditEvent
from app.security.audit_context import AuditContext, AuditDomain
from app.services.audit_outbox import enqueue_audit_event
from app.services.policy_service import validate_idempotency_key
from app.services.provider_trust_lifecycle import (
    FacilityTransitionCommand,
    FacilityTransitionFacts,
    ProfessionalTransitionCommand,
    ProfessionalTransitionFacts,
    plan_facility_transition,
    plan_professional_transition,
)
from app.services.provider_verification_decision_policy import (
    FacilityVerificationContext,
    ProfessionalVerificationContext,
    VerificationDecisionDisposition,
    VerificationDecisionPlan,
    VerificationDecisionReason,
    evaluate_facility_observation,
    evaluate_professional_observation,
)
from app.services.provider_verification_registry import (
    FacilityLookupRequest,
    ProfessionalLookupRequest,
    RegistryObservation,
    RegistryResourceType,
)


SYSTEM_AUTOMATION_ACTOR_ID = "system:registry_verification_automation"
_IDEMPOTENCY_TENANT = "platform-provider-trust"
_OPERATION_NAME = "provider.trust.verification.apply.v1"

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


_ALLOWED_SYSTEM_COMMANDS: dict[RegistryResourceType, set[Any]] = {
    RegistryResourceType.PROFESSIONAL: {
        ProfessionalTransitionCommand.MARK_RECHECK_DUE,
        ProfessionalTransitionCommand.COMPLETE_RECHECK,
        ProfessionalTransitionCommand.CANCEL_RECHECK_GRACE,
    },
    RegistryResourceType.FACILITY: {
        FacilityTransitionCommand.COMPLETE_RECHECK,
        FacilityTransitionCommand.MARK_RECHECK_REQUIRED,
    },
}


class VerificationApplicationError(RuntimeError):
    """Deterministic verification application failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class SystemAutomationActor:
    """Explicit system principal representing registry automation."""

    actor_id: str = SYSTEM_AUTOMATION_ACTOR_ID
    actor_type: str = "SYSTEM_AUTOMATION"
    execution_mode: str = "SYSTEM_AUTOMATION"


@dataclass(frozen=True, slots=True)
class RegistryLookupInvocation:
    """Server-created invocation record for a specific registry lookup."""

    resource_id: UUID
    resource_type: RegistryResourceType
    expected_version: int
    request: ProfessionalLookupRequest | FacilityLookupRequest
    invoked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not isinstance(self.resource_id, UUID):
            raise VerificationApplicationError("INVALID_INVOCATION")
        if not isinstance(self.resource_type, RegistryResourceType):
            raise VerificationApplicationError("INVALID_INVOCATION")
        if not isinstance(self.expected_version, int) or self.expected_version < 1:
            raise VerificationApplicationError("INVALID_INVOCATION")
        if self.invoked_at.tzinfo is None:
            raise VerificationApplicationError("INVALID_INVOCATION")


@dataclass(frozen=True, slots=True)
class ValidatedRegistryLookupEnvelope:
    """Envelope binding an invocation to its resulting observation.

    Guarantees structural lineage between the intended lookup request
    and the resulting observation.
    """

    invocation: RegistryLookupInvocation
    observation: RegistryObservation

    def __post_init__(self) -> None:
        if not isinstance(self.invocation, RegistryLookupInvocation):
            raise VerificationApplicationError("INVALID_ENVELOPE")
        if not isinstance(self.observation, RegistryObservation):
            raise VerificationApplicationError("INVALID_ENVELOPE")
        if self.invocation.resource_type != self.observation.resource_type:
            raise VerificationApplicationError("ENVELOPE_RESOURCE_TYPE_MISMATCH")
        if self.invocation.request.lookup_purpose != self.observation.lookup_purpose:
            raise VerificationApplicationError("ENVELOPE_PURPOSE_MISMATCH")
        if self.observation.observed_at < self.invocation.invoked_at:
            raise VerificationApplicationError("ENVELOPE_OBSERVED_BEFORE_INVOCATION")


@dataclass(frozen=True, slots=True)
class SourceAutomationPolicy:
    """Governed source automation policy.

    By default, automation is disabled for all sources.
    """

    source_id: str
    resource_type: RegistryResourceType | None = None
    registration_authority_code: str | None = None
    approved_adapter_version: str | None = None
    allowed_binding_methods: frozenset[str] = frozenset({"REGISTRY_MATCH"})
    automation_enabled: bool = False
    require_tls: bool = True


class SourceAutomationPolicyRegistry:
    """In-memory source automation policy registry. Default is empty (fail-closed)."""

    def __init__(
        self,
        policies: list[SourceAutomationPolicy]
        | dict[str, SourceAutomationPolicy]
        | None = None,
    ) -> None:
        self._policies: list[SourceAutomationPolicy] = []
        if isinstance(policies, dict):
            self._policies.extend(policies.values())
        elif isinstance(policies, list):
            self._policies.extend(policies)

    def register(self, policy: SourceAutomationPolicy) -> None:
        self._policies.append(policy)

    def get_policy(
        self,
        source_id: str,
        resource_type: RegistryResourceType | None = None,
        registration_authority_code: str | None = None,
    ) -> SourceAutomationPolicy:
        for p in reversed(self._policies):
            if p.source_id == source_id:
                if (
                    p.resource_type is not None
                    and resource_type is not None
                    and p.resource_type != resource_type
                ):
                    continue
                if (
                    p.registration_authority_code is not None
                    and registration_authority_code is not None
                    and p.registration_authority_code != registration_authority_code
                ):
                    continue
                return p
        return SourceAutomationPolicy(source_id=source_id, automation_enabled=False)


@dataclass(frozen=True, slots=True)
class ProviderVerificationApplicationResult:
    """Result of an atomic verification application."""

    resource_id: UUID
    resource_type: str
    decision_disposition: str
    reason_code: str
    evidence_id: UUID
    review_work_id: UUID | None
    resulting_version: int
    lifecycle_mutated: bool
    applied_command: str | None
    idempotent_replay: bool


def _canonical_envelope_hash(
    envelope: ValidatedRegistryLookupEnvelope,
) -> str:
    """Deterministic hash of the envelope for idempotency verification."""
    data = {
        "resource_id": str(envelope.invocation.resource_id),
        "resource_type": envelope.invocation.resource_type.value,
        "expected_version": envelope.invocation.expected_version,
        "source_id": envelope.observation.source_id,
        "outcome": envelope.observation.outcome.value,
        "digest": envelope.observation.response_digest,
        "observed_at": envelope.observation.observed_at.isoformat(),
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class ProviderVerificationApplicationService:
    """Atomic application of server-owned verification observations."""

    def __init__(
        self,
        db: AsyncSession,
        source_policies: SourceAutomationPolicyRegistry | None = None,
        automation_enabled: bool | Callable[[], bool] = False,
    ) -> None:
        self.db = db
        self.source_policies = source_policies or SourceAutomationPolicyRegistry()
        self._automation_enabled_config = automation_enabled

    def _is_global_automation_enabled(self) -> bool:
        """Global automation kill switch via injected dependency."""
        if callable(self._automation_enabled_config):
            return bool(self._automation_enabled_config())
        return bool(self._automation_enabled_config)

    async def apply_verification_observation(
        self,
        envelope: ValidatedRegistryLookupEnvelope,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> ProviderVerificationApplicationResult:
        """Execute atomic verification observation application."""
        try:
            key = validate_idempotency_key(idempotency_key)
        except ValueError as exc:
            raise VerificationApplicationError("INVALID_REQUEST") from exc

        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise VerificationApplicationError("INVALID_REQUEST")

        request_hash = _canonical_envelope_hash(envelope)
        inv = envelope.invocation
        obs = envelope.observation
        resource_id = inv.resource_id

        tx_ctx = self.db.begin_nested() if self.db.in_transaction() else self.db.begin()
        async with tx_ctx:
            # 1. Check idempotency
            existing = (
                await self.db.execute(
                    _IDEMPOTENCY_SELECT,
                    {
                        "tenant_id": _IDEMPOTENCY_TENANT,
                        "operation": _OPERATION_NAME,
                        "key": key,
                    },
                )
            ).first()
            if existing is not None:
                return self._replay(existing, request_hash, resource_id)

            # 2. Reserve idempotency slot
            reserved = (
                await self.db.execute(
                    _IDEMPOTENCY_RESERVE,
                    {
                        "tenant_id": _IDEMPOTENCY_TENANT,
                        "actor_id": SYSTEM_AUTOMATION_ACTOR_ID,
                        "operation": _OPERATION_NAME,
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
                            "operation": _OPERATION_NAME,
                            "key": key,
                        },
                    )
                ).first()
                if existing is None:
                    raise VerificationApplicationError("IDEMPOTENCY_IN_PROGRESS")
                return self._replay(existing, request_hash, resource_id)

            # 3. Lock target lifecycle row
            target = await self._lock_target(inv.resource_type, resource_id)

            # 4. Validate registration identity binding
            self._validate_identity_binding(target, inv.request)

            # 5. Check target version vs expected
            if target.version != inv.expected_version:
                raise VerificationApplicationError("LIFECYCLE_VERSION_CONFLICT")

            # 6. Check open human review queue work
            open_review = await self._has_open_review_work(inv.resource_type, target.id)

            # 7. Check source automation policy & kill switch
            source_policy = self.source_policies.get_policy(
                obs.source_id,
                resource_type=inv.resource_type,
                registration_authority_code=target.registration_authority_code,
            )
            global_enabled = self._is_global_automation_enabled()
            adapter_version_ok = (
                source_policy.approved_adapter_version is None
                or obs.adapter_version == source_policy.approved_adapter_version
            )
            automation_allowed_by_policy = (
                global_enabled
                and source_policy.automation_enabled
                and adapter_version_ok
                and (
                    obs.binding_method is None
                    or obs.binding_method in source_policy.allowed_binding_methods
                )
            )

            # 8. Reconstruct Phase 5D decision context
            decision = self._evaluate_decision(
                inv.resource_type,
                target,
                inv.request,
                obs,
                open_review=open_review,
                now=moment,
            )

            # If candidate command is outside system execution authority, fail closed to review with LIFECYCLE_SEMANTIC_GAP
            if (
                decision.candidate_command is not None
                and decision.candidate_command
                not in _ALLOWED_SYSTEM_COMMANDS.get(inv.resource_type, set())
            ):
                decision = VerificationDecisionPlan(
                    resource_type=inv.resource_type,
                    disposition=VerificationDecisionDisposition.LIFECYCLE_SEMANTIC_GAP,
                    candidate_command=None,
                    expected_resource_version=target.version,
                    reason_code=VerificationDecisionReason.SYSTEM_ACTOR_PROVENANCE_GAP,
                    requires_human_review=True,
                    grace_expires_at=None,
                    source_id=obs.source_id,
                    lookup_purpose=obs.lookup_purpose,
                    outcome=obs.outcome,
                )

            # If policy or kill-switch disallows automation, positive transition candidates fall closed to review
            if (
                decision.disposition
                == VerificationDecisionDisposition.SYSTEM_TRANSITION_CANDIDATE
                and not automation_allowed_by_policy
            ):
                decision = VerificationDecisionPlan(
                    resource_type=inv.resource_type,
                    disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
                    candidate_command=None,
                    expected_resource_version=target.version,
                    reason_code=VerificationDecisionReason.MANUAL_REVIEW_PURPOSE_HUMAN_REQUIRED,
                    requires_human_review=True,
                    grace_expires_at=None,
                    source_id=obs.source_id,
                    lookup_purpose=obs.lookup_purpose,
                    outcome=obs.outcome,
                )

            # Invariant: Reviewer ID preservation. If positive recheck candidate, target MUST have existing human reviewer_id.
            if (
                decision.disposition
                == VerificationDecisionDisposition.SYSTEM_TRANSITION_CANDIDATE
            ):
                if not target.reviewer_id or not target.reviewer_id.strip():
                    decision = VerificationDecisionPlan(
                        resource_type=inv.resource_type,
                        disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
                        candidate_command=None,
                        expected_resource_version=target.version,
                        reason_code=VerificationDecisionReason.MANUAL_REVIEW_PURPOSE_HUMAN_REQUIRED,
                        requires_human_review=True,
                        grace_expires_at=None,
                        source_id=obs.source_id,
                        lookup_purpose=obs.lookup_purpose,
                        outcome=obs.outcome,
                    )

            # 9. Insert immutable verification evidence row
            evidence = self._insert_evidence(
                inv.resource_type, target.id, obs, target.version
            )
            self.db.add(evidence)
            await self.db.flush()

            audit_context = AuditContext.for_tenant(
                tenant_id=_IDEMPOTENCY_TENANT,
                domain=AuditDomain.CONSENT,
            )

            # Audit evidence observation recorded
            evidence_audit_key = f"evidence-obs:{evidence.id}"
            await enqueue_audit_event(
                self.db,
                audit_context=audit_context,
                idempotency_key=evidence_audit_key,
                actor_id=SYSTEM_AUTOMATION_ACTOR_ID,
                event_type=ProviderTrustAuditEvent.PROVIDER_TRUST_VERIFICATION_OBSERVATION_RECORDED.value,
                target_id=str(evidence.id),
                patient_id=None,
                metadata={
                    "resource_type": inv.resource_type.value,
                    "target_id": str(target.id),
                    "source_id": obs.source_id,
                    "outcome": obs.outcome.value,
                    "disposition": decision.disposition.value,
                    "actor_type": "SYSTEM_AUTOMATION",
                    "execution_mode": "SYSTEM_AUTOMATION",
                },
            )

            # 10. Execute disposition
            review_work_id: UUID | None = None
            applied_command: str | None = None
            lifecycle_mutated = False
            old_state = target.status

            if (
                decision.disposition
                == VerificationDecisionDisposition.SYSTEM_TRANSITION_CANDIDATE
            ):
                # Apply positive lifecycle mutation
                applied_command = decision.candidate_command.value
                self._apply_candidate_transition(
                    inv.resource_type,
                    target,
                    decision.candidate_command,
                    obs,
                    evidence.id,
                    moment,
                    grace_expires_at=decision.grace_expires_at,
                )
                lifecycle_mutated = True

                # Enqueue lifecycle transition audit event
                lifecycle_audit_key = f"lifecycle-auto:{target.id}:{target.version}"
                await enqueue_audit_event(
                    self.db,
                    audit_context=audit_context,
                    idempotency_key=lifecycle_audit_key,
                    actor_id=SYSTEM_AUTOMATION_ACTOR_ID,
                    event_type=(
                        ProviderTrustAuditEvent.PROVIDER_REVERIFICATION_PERFORMED.value
                        if inv.resource_type == RegistryResourceType.PROFESSIONAL
                        else ProviderTrustAuditEvent.FACILITY_VERIFIED.value
                    ),
                    target_id=str(target.id),
                    patient_id=None,
                    metadata={
                        "command": applied_command,
                        "resource_type": inv.resource_type.value,
                        "evidence_id": str(evidence.id),
                        "source_id": obs.source_id,
                        "adapter_version": obs.adapter_version,
                        "lookup_purpose": obs.lookup_purpose.value,
                        "outcome": obs.outcome.value,
                        "disposition": decision.disposition.value,
                        "reason_code": decision.reason_code.value,
                        "old_state": old_state,
                        "new_state": target.status,
                        "new_version": target.version,
                        "resulting_version": target.version,
                        "actor_type": "SYSTEM_AUTOMATION",
                        "execution_mode": "SYSTEM_AUTOMATION",
                    },
                )

            elif (
                decision.disposition
                == VerificationDecisionDisposition.SYSTEM_FAIL_CLOSED_AND_REVIEW
            ):
                # MARK_RECHECK_DUE or CANCEL_RECHECK_GRACE.
                applied_command = decision.candidate_command.value
                self._apply_candidate_transition(
                    inv.resource_type,
                    target,
                    decision.candidate_command,
                    obs,
                    evidence.id,
                    moment,
                    grace_expires_at=decision.grace_expires_at,
                )
                lifecycle_mutated = True

                # Create open review work queue entry
                work = ProviderTrustVerificationReviewWork(
                    evidence_id=evidence.id,
                    disposition=decision.disposition.value,
                    status=VerificationReviewWorkStatus.OPEN.value,
                    reason_code=decision.reason_code.value,
                )
                self.db.add(work)
                await self.db.flush()
                review_work_id = work.id

                # Enqueue audit events
                fail_closed_audit_key = f"fail-closed:{target.id}:{target.version}"
                await enqueue_audit_event(
                    self.db,
                    audit_context=audit_context,
                    idempotency_key=fail_closed_audit_key,
                    actor_id=SYSTEM_AUTOMATION_ACTOR_ID,
                    event_type=(
                        ProviderTrustAuditEvent.PROVIDER_RECHECK_GRACE_CANCELLED.value
                        if applied_command
                        == ProfessionalTransitionCommand.CANCEL_RECHECK_GRACE.value
                        else (
                            ProviderTrustAuditEvent.PROVIDER_REVERIFICATION_PERFORMED.value
                            if inv.resource_type == RegistryResourceType.PROFESSIONAL
                            else ProviderTrustAuditEvent.FACILITY_RECHECK_REQUIRED.value
                        )
                    ),
                    target_id=str(target.id),
                    patient_id=None,
                    metadata={
                        "command": applied_command,
                        "resource_type": inv.resource_type.value,
                        "evidence_id": str(evidence.id),
                        "source_id": obs.source_id,
                        "adapter_version": obs.adapter_version,
                        "lookup_purpose": obs.lookup_purpose.value,
                        "outcome": obs.outcome.value,
                        "disposition": decision.disposition.value,
                        "reason_code": decision.reason_code.value,
                        "old_state": old_state,
                        "new_state": target.status,
                        "new_version": target.version,
                        "resulting_version": target.version,
                        "actor_type": "SYSTEM_AUTOMATION",
                        "execution_mode": "SYSTEM_AUTOMATION",
                    },
                )

                review_audit_key = f"review-req:{work.id}"
                await enqueue_audit_event(
                    self.db,
                    audit_context=audit_context,
                    idempotency_key=review_audit_key,
                    actor_id=SYSTEM_AUTOMATION_ACTOR_ID,
                    event_type=ProviderTrustAuditEvent.PROVIDER_TRUST_VERIFICATION_REVIEW_REQUIRED.value,
                    target_id=str(work.id),
                    patient_id=None,
                    metadata={
                        "reason_code": decision.reason_code.value,
                        "disposition": decision.disposition.value,
                        "target_type": inv.resource_type.value,
                        "target_id": str(target.id),
                        "evidence_id": str(evidence.id),
                        "actor_type": "SYSTEM_AUTOMATION",
                        "execution_mode": "SYSTEM_AUTOMATION",
                    },
                )

            elif decision.requires_human_review:
                # Create review work queue entry without lifecycle mutation
                work = ProviderTrustVerificationReviewWork(
                    evidence_id=evidence.id,
                    disposition=decision.disposition.value,
                    status=VerificationReviewWorkStatus.OPEN.value,
                    reason_code=decision.reason_code.value,
                )
                self.db.add(work)
                await self.db.flush()
                review_work_id = work.id

                review_audit_key = f"review-req:{work.id}"
                await enqueue_audit_event(
                    self.db,
                    audit_context=audit_context,
                    idempotency_key=review_audit_key,
                    actor_id=SYSTEM_AUTOMATION_ACTOR_ID,
                    event_type=ProviderTrustAuditEvent.PROVIDER_TRUST_VERIFICATION_REVIEW_REQUIRED.value,
                    target_id=str(work.id),
                    patient_id=None,
                    metadata={
                        "reason_code": decision.reason_code.value,
                        "disposition": decision.disposition.value,
                        "target_type": inv.resource_type.value,
                        "target_id": str(target.id),
                        "evidence_id": str(evidence.id),
                        "actor_type": "SYSTEM_AUTOMATION",
                        "execution_mode": "SYSTEM_AUTOMATION",
                    },
                )

            # 11. Complete idempotency
            result = ProviderVerificationApplicationResult(
                resource_id=resource_id,
                resource_type=inv.resource_type.value,
                decision_disposition=decision.disposition.value,
                reason_code=decision.reason_code.value,
                evidence_id=evidence.id,
                review_work_id=review_work_id,
                resulting_version=target.version,
                lifecycle_mutated=lifecycle_mutated,
                applied_command=applied_command,
                idempotent_replay=False,
            )

            payload = json.dumps(
                {
                    "resource_id": str(resource_id),
                    "resource_type": result.resource_type,
                    "decision_disposition": result.decision_disposition,
                    "reason_code": result.reason_code,
                    "evidence_id": str(result.evidence_id),
                    "review_work_id": (
                        str(result.review_work_id) if result.review_work_id else None
                    ),
                    "resulting_version": result.resulting_version,
                    "lifecycle_mutated": result.lifecycle_mutated,
                    "applied_command": result.applied_command,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            completed = await self.db.execute(
                _IDEMPOTENCY_COMPLETE,
                {
                    "tenant_id": _IDEMPOTENCY_TENANT,
                    "operation": _OPERATION_NAME,
                    "key": key,
                    "payload": payload,
                    "version": result.resulting_version,
                },
            )
            if completed.rowcount != 1:
                raise VerificationApplicationError("TRANSACTION_INTEGRITY_FAILURE")

            return result

    async def _lock_target(
        self, resource_type: RegistryResourceType, resource_id: UUID
    ) -> Any:
        model = (
            ProfessionalVerification
            if resource_type == RegistryResourceType.PROFESSIONAL
            else FacilityVerification
        )
        target = (
            await self.db.execute(
                select(model)
                .where(model.id == resource_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if target is None:
            raise VerificationApplicationError("RESOURCE_NOT_FOUND")
        return target

    def _validate_identity_binding(self, target: Any, request: Any) -> None:
        if (
            target.registration_authority_code != request.registration_authority_code
            or target.registration_number_normalized
            != request.registration_number_normalized
        ):
            raise VerificationApplicationError("REGISTRATION_IDENTITY_MISMATCH")

    async def _has_open_review_work(
        self, resource_type: RegistryResourceType, target_id: UUID
    ) -> bool:
        col = (
            ProviderTrustVerificationEvidence.professional_verification_id
            if resource_type == RegistryResourceType.PROFESSIONAL
            else ProviderTrustVerificationEvidence.facility_verification_id
        )
        stmt = (
            select(ProviderTrustVerificationReviewWork.id)
            .join(
                ProviderTrustVerificationEvidence,
                ProviderTrustVerificationReviewWork.evidence_id
                == ProviderTrustVerificationEvidence.id,
            )
            .where(
                col == target_id,
                ProviderTrustVerificationReviewWork.status
                == VerificationReviewWorkStatus.OPEN.value,
            )
        )
        found = (await self.db.execute(stmt)).first()
        return found is not None

    def _evaluate_decision(
        self,
        resource_type: RegistryResourceType,
        target: Any,
        request: Any,
        observation: RegistryObservation,
        open_review: bool,
        now: datetime,
    ) -> VerificationDecisionPlan:
        if resource_type == RegistryResourceType.PROFESSIONAL:
            ctx = ProfessionalVerificationContext(
                current_status=ProfessionalVerificationStatus(target.status),
                current_version=target.version,
                registration_authority_code=target.registration_authority_code,
                registration_number_normalized=target.registration_number_normalized,
                registration_valid_until=target.registration_valid_until,
                previous_verification_valid=target.previous_verification_valid,
                current_grace_expires_at=target.grace_expires_at,
                current_recheck_failure_reason=(
                    VerificationSourceFailureReason(target.recheck_failure_reason)
                    if target.recheck_failure_reason
                    else None
                ),
                authoritative_adverse_signal_at=target.authoritative_adverse_signal_at,
                server_provenance_established=target.server_provenance_evidence_id
                is not None,
                established_server_source_id=target.verification_source,
                open_human_review_required=open_review,
            )
            return evaluate_professional_observation(
                observation=observation, request=request, context=ctx, now=now
            )
        else:
            ctx = FacilityVerificationContext(
                current_status=FacilityVerificationStatus(target.status),
                current_version=target.version,
                registration_authority_code=target.registration_authority_code,
                registration_number_normalized=target.registration_number_normalized,
                server_provenance_established=target.server_provenance_evidence_id
                is not None,
                established_server_source_id=target.verification_source,
                open_human_review_required=open_review,
            )
            return evaluate_facility_observation(
                observation=observation, request=request, context=ctx, now=now
            )

    def _insert_evidence(
        self,
        resource_type: RegistryResourceType,
        target_id: UUID,
        observation: RegistryObservation,
        resource_version: int,
    ) -> ProviderTrustVerificationEvidence:
        return ProviderTrustVerificationEvidence(
            professional_verification_id=(
                target_id
                if resource_type == RegistryResourceType.PROFESSIONAL
                else None
            ),
            facility_verification_id=(
                target_id if resource_type == RegistryResourceType.FACILITY else None
            ),
            origin=VerificationEvidenceOrigin.SERVER_REGISTRY_OBSERVATION.value,
            source_id=observation.source_id,
            adapter_version=observation.adapter_version,
            observed_at=observation.observed_at,
            lookup_purpose=observation.lookup_purpose.value,
            outcome=observation.outcome.value,
            source_record_reference=observation.source_record_reference,
            observed_valid_from=observation.observed_valid_from,
            observed_valid_until=observation.observed_valid_until,
            identity_binding_result=observation.identity_binding_result.value,
            binding_method=observation.binding_method,
            response_digest=observation.response_digest,
            external_transaction_id=observation.external_transaction_id,
            observed_resource_version=resource_version,
        )

    @staticmethod
    def _map_cancellation_failure_reason(
        outcome: Any,
    ) -> VerificationSourceFailureReason:
        outcome_str = outcome.value if hasattr(outcome, "value") else str(outcome)
        if outcome_str == "NOT_FOUND":
            return VerificationSourceFailureReason.SOURCE_NOT_FOUND
        if outcome_str in {
            "SOURCE_RESPONSE_INVALID",
            "SOURCE_AUTHENTICATION_FAILURE",
            "SOURCE_INTEGRITY_FAILURE",
        }:
            return VerificationSourceFailureReason.SOURCE_RESPONSE_INVALID
        return VerificationSourceFailureReason.REVIEW_REQUIRED

    @staticmethod
    def _compute_authoritative_adverse_signal(
        outcome: Any,
        observed_at: datetime,
    ) -> datetime | None:
        outcome_str = outcome.value if hasattr(outcome, "value") else str(outcome)
        if outcome_str in {
            "CONFIRMED_INACTIVE",
            "NOT_FOUND",
            "IDENTITY_MISMATCH",
        }:
            return observed_at
        return None

    def _apply_candidate_transition(
        self,
        resource_type: RegistryResourceType,
        target: Any,
        command: Any,
        observation: RegistryObservation,
        evidence_id: UUID,
        now: datetime,
        *,
        grace_expires_at: datetime | None,
    ) -> None:
        if resource_type == RegistryResourceType.PROFESSIONAL:
            if command == ProfessionalTransitionCommand.COMPLETE_RECHECK:
                facts = ProfessionalTransitionFacts(
                    registration_authority_code=target.registration_authority_code,
                    registration_number_normalized=target.registration_number_normalized,
                    verification_method=observation.binding_method
                    or target.verification_method
                    or "REGISTRY_MATCH",
                    verification_source=observation.source_id,
                    verification_reference=observation.source_record_reference
                    or target.verification_reference
                    or "REGISTRY_RECORD",
                    identity_binding_method=observation.binding_method
                    or target.identity_binding_method
                    or "REGISTRY_MATCH",
                    identity_binding_status=observation.identity_binding_result.value,
                    registration_valid_from=observation.observed_valid_from
                    or target.registration_valid_from,
                    registration_valid_until=observation.observed_valid_until
                    or target.registration_valid_until,
                    reviewer_id=target.reviewer_id,
                    recheck_attempted_at=observation.observed_at,
                    previous_verification_valid=True,
                    next_review_at=target.next_review_at,
                )
                plan = plan_professional_transition(
                    target.status, command, facts, now, current_version=target.version
                )
            elif command == ProfessionalTransitionCommand.MARK_RECHECK_DUE:
                is_outage = (
                    observation.outcome
                    == VerificationEvidenceOutcome.SOURCE_UNAVAILABLE
                )
                facts = ProfessionalTransitionFacts(
                    registration_valid_until=target.registration_valid_until,
                    recheck_attempted_at=observation.observed_at,
                    recheck_failure_reason=(
                        VerificationSourceFailureReason.SOURCE_UNAVAILABLE
                        if (is_outage and grace_expires_at is not None)
                        else None
                    ),
                    grace_expires_at=grace_expires_at if is_outage else None,
                    previous_verification_valid=True,
                )
                plan = plan_professional_transition(
                    target.status, command, facts, now, current_version=target.version
                )
            elif command == ProfessionalTransitionCommand.CANCEL_RECHECK_GRACE:
                failure_reason = self._map_cancellation_failure_reason(
                    observation.outcome
                )
                adverse_signal = self._compute_authoritative_adverse_signal(
                    observation.outcome, observation.observed_at
                )
                facts = ProfessionalTransitionFacts(
                    recheck_attempted_at=observation.observed_at,
                    recheck_failure_reason=failure_reason,
                    authoritative_adverse_signal_at=adverse_signal,
                    previous_verification_valid=True,
                    grace_expires_at=None,
                )
                plan = plan_professional_transition(
                    target.status, command, facts, now, current_version=target.version
                )
            else:
                raise VerificationApplicationError("COMMAND_DISALLOWED")

            for update in plan.updates:
                setattr(target, update.field, update.value)
            for field_name in plan.clears:
                setattr(target, field_name, None)
            if command != ProfessionalTransitionCommand.MARK_RECHECK_DUE:
                target.server_provenance_evidence_id = evidence_id

        elif resource_type == RegistryResourceType.FACILITY:
            if command == FacilityTransitionCommand.COMPLETE_RECHECK:
                facts = FacilityTransitionFacts(
                    verification_method=observation.binding_method
                    or target.verification_method
                    or "REGISTRY_MATCH",
                    verification_source=observation.source_id,
                    verification_reference=observation.source_record_reference
                    or target.verification_reference
                    or "REGISTRY_RECORD",
                    reviewer_id=target.reviewer_id,
                    next_review_at=target.next_review_at,
                )
                plan = plan_facility_transition(
                    target.status, command, facts, now, current_version=target.version
                )
            elif command == FacilityTransitionCommand.MARK_RECHECK_REQUIRED:
                facts = FacilityTransitionFacts(
                    decision_reason_code="RECHECK_REQUIRED",
                )
                plan = plan_facility_transition(
                    target.status, command, facts, now, current_version=target.version
                )
            else:
                raise VerificationApplicationError("COMMAND_DISALLOWED")

            for update in plan.updates:
                setattr(target, update.field, update.value)
            for field_name in plan.clears:
                setattr(target, field_name, None)
            if command == FacilityTransitionCommand.COMPLETE_RECHECK:
                target.server_provenance_evidence_id = evidence_id

    def _replay(
        self,
        existing: Any,
        request_hash: str,
        resource_id: UUID,
    ) -> ProviderVerificationApplicationResult:
        if existing.request_hash != request_hash:
            raise VerificationApplicationError("IDEMPOTENCY_CONFLICT")
        if existing.response_status is None:
            raise VerificationApplicationError("IDEMPOTENCY_IN_PROGRESS")
        payload = existing.response_payload
        if isinstance(payload, str):
            payload = json.loads(payload)
        return ProviderVerificationApplicationResult(
            resource_id=resource_id,
            resource_type=payload["resource_type"],
            decision_disposition=payload["decision_disposition"],
            reason_code=payload["reason_code"],
            evidence_id=UUID(payload["evidence_id"]),
            review_work_id=(
                UUID(payload["review_work_id"])
                if payload.get("review_work_id")
                else None
            ),
            resulting_version=payload["resulting_version"],
            lifecycle_mutated=payload["lifecycle_mutated"],
            applied_command=payload.get("applied_command"),
            idempotent_replay=True,
        )


async def execute_lookup_and_create_envelope(
    adapter: Any,
    invocation: RegistryLookupInvocation,
) -> ValidatedRegistryLookupEnvelope:
    """Helper to execute registry adapter lookup and construct validated envelope."""
    if invocation.resource_type == RegistryResourceType.PROFESSIONAL:
        obs = await adapter.lookup_professional(invocation.request)
    else:
        obs = await adapter.lookup_facility(invocation.request)
    return ValidatedRegistryLookupEnvelope(
        invocation=invocation,
        observation=obs,
    )
