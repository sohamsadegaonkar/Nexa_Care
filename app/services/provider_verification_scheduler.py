"""Durable provider verification scheduler service (Phase 5F).

Responsible for:
- Periodic sweeping of due/overdue verified credentials and recheck-due items
- Checking source automation policy and kill switch
- Checking open manual review queue work (blocks automated scheduling)
- Checking existing active work items (prevents duplicate work rows)
- Executing atomic scheduled-due lifecycle transition for VERIFIED records
  (VERIFIED -> RECHECK_DUE / RECHECK_REQUIRED)
- Inserting durable ProviderVerificationWork queue rows with expected_resource_version
- Staging PROVIDER_VERIFICATION_WORK_SCHEDULED audit events

Permanent authority invariant:
    REGISTRY SCHEDULER
    != DURABLE VERIFICATION WORK
    != NETWORK ATTEMPT
    != REGISTRY ADAPTER
    != LOOKUP INVOCATION
    != REGISTRY OBSERVATION
    != DECISION POLICY
    != SYSTEM EXECUTION AUTHORITY
    != LIFECYCLE MUTATION
    != CLINICAL AUTHORITY
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider import (
    FacilityVerification,
    FacilityVerificationStatus,
    ProfessionalVerification,
    ProfessionalVerificationStatus,
    ProviderTrustVerificationEvidence,
    ProviderTrustVerificationReviewWork,
    VerificationEvidenceLookupPurpose,
    VerificationEvidenceOrigin,
    VerificationEvidenceOutcome,
    VerificationIdentityBindingResult,
    VerificationReviewWorkStatus,
    VerificationWorkStatus,
    ProviderVerificationWork,
)
from app.observability.provider_trust_events import ProviderTrustAuditEvent
from app.security.audit_context import AuditContext, AuditDomain
from app.services.audit_outbox import enqueue_audit_event
from app.services.provider_trust_lifecycle import (
    FacilityTransitionCommand,
    FacilityTransitionFacts,
    ProfessionalTransitionCommand,
    ProfessionalTransitionFacts,
    plan_facility_transition,
    plan_professional_transition,
)
from app.services.provider_verification_application import (
    SYSTEM_AUTOMATION_ACTOR_ID,
    SourceAutomationPolicy,
    SourceAutomationPolicyRegistry,
)
from app.services.provider_verification_registry import RegistryResourceType

logger = logging.getLogger(__name__)


class ProviderVerificationSchedulerError(RuntimeError):
    """Deterministic scheduler failure."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


class ProviderVerificationSchedulerService:
    """Evaluates credential lifecycle states and schedules durable verification work."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        source_policies: SourceAutomationPolicyRegistry | None = None,
        automation_enabled: bool | Callable[[], bool] = False,
    ) -> None:
        self.db = db
        self.source_policies = source_policies or SourceAutomationPolicyRegistry()
        self._automation_enabled = automation_enabled

    def is_automation_enabled(self) -> bool:
        if callable(self._automation_enabled):
            return bool(self._automation_enabled())
        return bool(self._automation_enabled)

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

    async def _has_active_work(
        self, resource_type: RegistryResourceType, target_id: UUID
    ) -> bool:
        col = (
            ProviderVerificationWork.professional_verification_id
            if resource_type == RegistryResourceType.PROFESSIONAL
            else ProviderVerificationWork.facility_verification_id
        )
        stmt = select(ProviderVerificationWork.id).where(
            col == target_id,
            ProviderVerificationWork.status.in_(
                (
                    VerificationWorkStatus.PENDING.value,
                    VerificationWorkStatus.CLAIMED.value,
                )
            ),
        )
        found = (await self.db.execute(stmt)).first()
        return found is not None

    async def _resolve_applicable_source_policy(
        self,
        resource_type: RegistryResourceType,
        target: Any,
        now: datetime,
    ) -> SourceAutomationPolicy | None:
        # 1. If server provenance evidence is linked, check source from evidence
        if target.server_provenance_evidence_id is not None:
            linked_evidence = await self.db.get(
                ProviderTrustVerificationEvidence,
                target.server_provenance_evidence_id,
            )
            if (
                linked_evidence is not None
                and linked_evidence.origin
                == VerificationEvidenceOrigin.SERVER_REGISTRY_OBSERVATION.value
                and linked_evidence.outcome
                == VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value
                and linked_evidence.identity_binding_result
                == VerificationIdentityBindingResult.MATCHED.value
            ):
                policy = self.source_policies.get_policy(
                    linked_evidence.source_id,
                    resource_type=resource_type,
                    registration_authority_code=target.registration_authority_code,
                )
                if (
                    policy.automation_enabled
                    and policy.source_id == linked_evidence.source_id
                    and policy.resource_type == resource_type
                    and policy.registration_authority_code
                    == target.registration_authority_code
                    and policy.approved_adapter_version
                ):
                    return policy

        # 2. Otherwise search policy registry for matching authority
        policy = self.source_policies.get_unique_automation_policy_for_authority(
            resource_type=resource_type,
            registration_authority_code=target.registration_authority_code,
        )
        if (
            policy is not None
            and policy.automation_enabled
            and policy.resource_type == resource_type
            and policy.registration_authority_code == target.registration_authority_code
            and policy.approved_adapter_version
        ):
            return policy

        return None

    async def _add_work(self, work: ProviderVerificationWork) -> None:
        """Stage work creation inside the caller's scheduler transaction.

        This narrow seam keeps lifecycle transition, work insertion, and both
        audit events in the same transaction and permits fault-injection
        qualification without a separate persistence path.
        """
        self.db.add(work)
        await self.db.flush()

    async def sweep_due_verifications(
        self,
        *,
        batch_size: int = 50,
        now: datetime | None = None,
    ) -> list[ProviderVerificationWork]:
        """Atomically transition due resources, enqueue work, and stage audit."""
        if not self.is_automation_enabled():
            return []
        transaction = (
            self.db.begin_nested() if self.db.in_transaction() else self.db.begin()
        )
        async with transaction:
            return await self._sweep_due_verifications_in_transaction(
                batch_size=batch_size,
                now=now,
            )

    async def _sweep_due_verifications_in_transaction(
        self,
        *,
        batch_size: int = 50,
        now: datetime | None = None,
    ) -> list[ProviderVerificationWork]:
        """Scan for due credentials, perform scheduled-due transition, and enqueue work."""
        if not self.is_automation_enabled():
            return []

        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)

        scheduled_work: list[ProviderVerificationWork] = []

        # -------------------------------------------------------------------
        # 1. Sweep Professional Verifications
        # -------------------------------------------------------------------
        prof_stmt = (
            select(ProfessionalVerification)
            .where(
                or_(
                    # A. VERIFIED and due
                    ProfessionalVerification.status
                    == ProfessionalVerificationStatus.VERIFIED.value,
                    # B. Already in RECHECK_DUE
                    ProfessionalVerification.status
                    == ProfessionalVerificationStatus.RECHECK_DUE.value,
                )
            )
            .limit(batch_size)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        prof_rows = (await self.db.execute(prof_stmt)).scalars().all()

        for prof in prof_rows:
            # Check open review work
            if await self._has_open_review_work(
                RegistryResourceType.PROFESSIONAL, prof.id
            ):
                continue

            # Check existing active work
            if await self._has_active_work(RegistryResourceType.PROFESSIONAL, prof.id):
                continue

            # Check source automation policy
            policy = await self._resolve_applicable_source_policy(
                RegistryResourceType.PROFESSIONAL, prof, moment
            )
            if policy is None or not policy.automation_enabled:
                continue

            is_verified = prof.status == ProfessionalVerificationStatus.VERIFIED.value
            scheduler_reason: str

            if is_verified:
                # Check due condition
                if prof.next_review_at is not None:
                    if prof.next_review_at > moment:
                        continue
                else:
                    # Legacy null next_review_at: only due if linked evidence observed_at + cadence <= moment
                    if prof.server_provenance_evidence_id is None:
                        continue
                    linked_ev = await self.db.get(
                        ProviderTrustVerificationEvidence,
                        prof.server_provenance_evidence_id,
                    )
                    if linked_ev is None or policy.recheck_interval_seconds is None:
                        continue
                    if (
                        linked_ev.observed_at
                        + timedelta(seconds=policy.recheck_interval_seconds)
                        > moment
                    ):
                        continue

                # Execute internal scheduled-due lifecycle transition
                facts = ProfessionalTransitionFacts(
                    recheck_attempted_at=moment,
                    previous_verification_valid=True,
                )
                plan = plan_professional_transition(
                    prof.status,
                    ProfessionalTransitionCommand.MARK_RECHECK_DUE,
                    facts,
                    moment,
                    current_version=prof.version,
                )
                for update in plan.updates:
                    setattr(prof, update.field, update.value)
                for fld in plan.clears:
                    setattr(prof, fld, None)
                prof.version = plan.next_version

                # Enqueue lifecycle audit event
                audit_ctx = AuditContext.platform(domain=AuditDomain.PLATFORM)
                await enqueue_audit_event(
                    self.db,
                    audit_context=audit_ctx,
                    idempotency_key=f"prof-scheduled-due:{prof.id}:{prof.version}",
                    actor_id=SYSTEM_AUTOMATION_ACTOR_ID,
                    event_type=ProviderTrustAuditEvent.PROVIDER_REVERIFICATION_PERFORMED.value,
                    target_id=str(prof.id),
                    patient_id=None,
                    metadata={
                        "command": ProfessionalTransitionCommand.MARK_RECHECK_DUE.value,
                        "resource_type": "PROFESSIONAL",
                        "old_state": ProfessionalVerificationStatus.VERIFIED.value,
                        "new_state": prof.status,
                        "new_version": prof.version,
                        "actor_type": "SYSTEM_AUTOMATION",
                    },
                )
                scheduler_reason = "SCHEDULED_REVIEW_DUE"
            else:
                scheduler_reason = "RECHECK_DUE_BOOTSTRAP"

            # Create durable verification work row
            work = ProviderVerificationWork(
                id=uuid4(),
                professional_verification_id=prof.id,
                facility_verification_id=None,
                lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK.value,
                source_id=policy.source_id,
                adapter_version=policy.approved_adapter_version,
                registration_authority_code=prof.registration_authority_code,
                registration_number_normalized=prof.registration_number_normalized,
                expected_resource_version=prof.version,
                scheduler_reason=scheduler_reason,
                status=VerificationWorkStatus.PENDING.value,
                priority=100,
                next_attempt_at=moment,
                attempt_count=0,
                max_attempts=5,
            )
            await self._add_work(work)

            # Enqueue work scheduled audit event
            audit_ctx = AuditContext.platform(domain=AuditDomain.PLATFORM)
            await enqueue_audit_event(
                self.db,
                audit_context=audit_ctx,
                idempotency_key=f"work-scheduled:{work.id}",
                actor_id=SYSTEM_AUTOMATION_ACTOR_ID,
                event_type=ProviderTrustAuditEvent.PROVIDER_VERIFICATION_WORK_SCHEDULED.value,
                target_id=str(work.id),
                patient_id=None,
                metadata={
                    "work_id": str(work.id),
                    "resource_type": "PROFESSIONAL",
                    "resource_id": str(prof.id),
                    "source_id": work.source_id,
                    "scheduler_reason": work.scheduler_reason,
                    "expected_resource_version": work.expected_resource_version,
                },
            )
            scheduled_work.append(work)

        # -------------------------------------------------------------------
        # 2. Sweep Facility Verifications
        # -------------------------------------------------------------------
        fac_stmt = (
            select(FacilityVerification)
            .where(
                or_(
                    FacilityVerification.status
                    == FacilityVerificationStatus.VERIFIED.value,
                    FacilityVerification.status
                    == FacilityVerificationStatus.RECHECK_REQUIRED.value,
                )
            )
            .limit(batch_size)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        fac_rows = (await self.db.execute(fac_stmt)).scalars().all()

        for fac in fac_rows:
            if await self._has_open_review_work(RegistryResourceType.FACILITY, fac.id):
                continue

            if await self._has_active_work(RegistryResourceType.FACILITY, fac.id):
                continue

            policy = await self._resolve_applicable_source_policy(
                RegistryResourceType.FACILITY, fac, moment
            )
            if policy is None or not policy.automation_enabled:
                continue

            is_verified = fac.status == FacilityVerificationStatus.VERIFIED.value

            if is_verified:
                if fac.next_review_at is not None:
                    if fac.next_review_at > moment:
                        continue
                else:
                    if fac.server_provenance_evidence_id is None:
                        continue
                    linked_ev = await self.db.get(
                        ProviderTrustVerificationEvidence,
                        fac.server_provenance_evidence_id,
                    )
                    if linked_ev is None or policy.recheck_interval_seconds is None:
                        continue
                    if (
                        linked_ev.observed_at
                        + timedelta(seconds=policy.recheck_interval_seconds)
                        > moment
                    ):
                        continue

                facts = FacilityTransitionFacts()
                plan = plan_facility_transition(
                    fac.status,
                    FacilityTransitionCommand.MARK_RECHECK_REQUIRED,
                    facts,
                    moment,
                    current_version=fac.version,
                )
                for update in plan.updates:
                    setattr(fac, update.field, update.value)
                for fld in plan.clears:
                    setattr(fac, fld, None)
                fac.version = plan.next_version

                audit_ctx = AuditContext.for_hospital(
                    hospital_id=str(fac.facility_id),
                    domain=AuditDomain.PLATFORM,
                )
                await enqueue_audit_event(
                    self.db,
                    audit_context=audit_ctx,
                    idempotency_key=f"fac-scheduled-due:{fac.id}:{fac.version}",
                    actor_id=SYSTEM_AUTOMATION_ACTOR_ID,
                    event_type=ProviderTrustAuditEvent.FACILITY_RECHECK_REQUIRED.value,
                    target_id=str(fac.id),
                    patient_id=None,
                    metadata={
                        "command": FacilityTransitionCommand.MARK_RECHECK_REQUIRED.value,
                        "resource_type": "FACILITY",
                        "old_state": FacilityVerificationStatus.VERIFIED.value,
                        "new_state": fac.status,
                        "new_version": fac.version,
                        "actor_type": "SYSTEM_AUTOMATION",
                    },
                )
                scheduler_reason = "SCHEDULED_REVIEW_DUE"
            else:
                scheduler_reason = "RECHECK_REQUIRED_BOOTSTRAP"

            work = ProviderVerificationWork(
                id=uuid4(),
                professional_verification_id=None,
                facility_verification_id=fac.id,
                lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK.value,
                source_id=policy.source_id,
                adapter_version=policy.approved_adapter_version,
                registration_authority_code=fac.registration_authority_code,
                registration_number_normalized=fac.registration_number_normalized,
                expected_resource_version=fac.version,
                scheduler_reason=scheduler_reason,
                status=VerificationWorkStatus.PENDING.value,
                priority=100,
                next_attempt_at=moment,
                attempt_count=0,
                max_attempts=5,
            )
            await self._add_work(work)

            audit_ctx = AuditContext.for_hospital(
                hospital_id=str(fac.facility_id),
                domain=AuditDomain.PLATFORM,
            )
            await enqueue_audit_event(
                self.db,
                audit_context=audit_ctx,
                idempotency_key=f"work-scheduled:{work.id}",
                actor_id=SYSTEM_AUTOMATION_ACTOR_ID,
                event_type=ProviderTrustAuditEvent.PROVIDER_VERIFICATION_WORK_SCHEDULED.value,
                target_id=str(work.id),
                patient_id=None,
                metadata={
                    "work_id": str(work.id),
                    "resource_type": "FACILITY",
                    "resource_id": str(fac.id),
                    "source_id": work.source_id,
                    "scheduler_reason": work.scheduler_reason,
                    "expected_resource_version": work.expected_resource_version,
                },
            )
            scheduled_work.append(work)

        return scheduled_work
