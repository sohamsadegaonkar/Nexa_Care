"""Adversarial real-PostgreSQL qualification for Slice 5 Phase 5G-2.

This module deliberately owns one loopback-only disposable database.  It uses
database transactions, row locks, and asyncio barriers; it never relies on
time-based race guesses or external registry traffic.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.provider import (
    FacilityVerification,
    FacilityVerificationStatus,
    HospitalRegistry,
    ProfessionalVerification,
    ProfessionalVerificationStatus,
    ProviderCredential,
    ProviderIdentity,
    ProviderTrustPermissionGrant,
    ProviderTrustVerificationEvidence,
    ProviderTrustVerificationReviewWork,
    ProviderVerificationWork,
    VerificationEvidenceLookupPurpose,
    VerificationEvidenceOrigin,
    VerificationEvidenceOutcome,
    VerificationIdentityBindingResult,
    VerificationReviewWorkStatus,
    VerificationWorkStatus,
)
from app.services.clinical_eligibility import ClinicalAuthenticationMethod
from app.services.provider_trust_authorization import TrustManagementAuthentication
from app.services.provider_trust_lifecycle import (
    FacilityTransitionCommand,
    FacilityTransitionFacts,
    ProfessionalTransitionCommand,
    ProfessionalTransitionFacts,
)
from app.services.provider_trust_lifecycle_application import (
    ProviderTrustLifecycleApplicationError,
    ProviderTrustLifecycleApplicationService,
)
from app.services.provider_verification_application import (
    FacilityLookupRequest,
    ProfessionalLookupRequest,
    ProviderVerificationApplicationService,
    RegistryLookupInvocation,
    RegistryResourceType,
    SourceAutomationPolicy,
    SourceAutomationPolicyRegistry,
    ValidatedRegistryLookupEnvelope,
    VerificationApplicationError,
)
from app.services.provider_verification_registry import (
    RegistryObservation,
    RegistrySourceDescriptor,
    SyntheticRegistryAdapter,
)
from app.services.provider_verification_scheduler import (
    ProviderVerificationSchedulerService,
)
from app.services.provider_verification_worker import ProviderVerificationWorkerService
from tests.helpers.qualification_infra import (
    create_disposable_database,
    drop_disposable_database,
    migrate_database_to_head,
    postgres_database_url,
)


pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]

HEAD = "20260906_verification_scheduler"
_DB_NAME = "nexa_qual_trust_adversarial"
_AUDIT_TRIGGER = "phase5g2_reject_audit_insert"
_AUDIT_FUNCTION = "phase5g2_reject_audit_insert"


def _url() -> str:
    return postgres_database_url(_DB_NAME)


@pytest.fixture(scope="module", autouse=True)
def _setup_database():
    """Create and always remove only the dedicated loopback qualification DB."""
    previous = os.environ.get("TEST_DATABASE_URL")
    db_url = _url()
    os.environ["TEST_DATABASE_URL"] = db_url
    asyncio.run(create_disposable_database(_DB_NAME))
    migrate_database_to_head(db_url, target_head=HEAD)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TEST_DATABASE_URL", None)
        else:
            os.environ["TEST_DATABASE_URL"] = previous
        asyncio.run(drop_disposable_database(_DB_NAME))


@pytest.fixture
async def session_factory():
    engine = create_async_engine(_url(), pool_size=8, max_overflow=0)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def _professional_policy(*, enabled: bool = True) -> SourceAutomationPolicy:
    return SourceAutomationPolicy(
        source_id="QUAL_PROFESSIONAL_SOURCE",
        resource_type=RegistryResourceType.PROFESSIONAL,
        registration_authority_code="QUAL_AUTH",
        approved_adapter_version="1.0.0",
        allowed_binding_methods=frozenset({"SYNTHETIC_EXACT"}),
        automation_enabled=enabled,
        recheck_interval_seconds=86400,
    )


def _facility_policy(*, enabled: bool = True) -> SourceAutomationPolicy:
    return SourceAutomationPolicy(
        source_id="QUAL_FACILITY_SOURCE",
        resource_type=RegistryResourceType.FACILITY,
        registration_authority_code="QUAL_FAC_AUTH",
        approved_adapter_version="1.0.0",
        allowed_binding_methods=frozenset({"SYNTHETIC_EXACT"}),
        automation_enabled=enabled,
        recheck_interval_seconds=86400,
    )


async def _seed_professional(
    factory,
    *,
    status: ProfessionalVerificationStatus = ProfessionalVerificationStatus.RECHECK_DUE,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, datetime]:
    now = datetime.now(timezone.utc)
    facility_id, provider_id, verification_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with factory() as db:
        db.add(
            HospitalRegistry(
                id=facility_id,
                facility_code=f"QF-{facility_id.hex[:12]}",
                legal_name="Qualification facility",
                display_name="Qualification facility",
                country_code="IN",
                is_active=True,
            )
        )
        db.add_all(
            (
                ProviderIdentity(
                    id=provider_id,
                    provider_uid=f"qp-{provider_id.hex}",
                    hospital_id=facility_id,
                    contact_email=f"qp-{provider_id.hex}@example.test",
                    contact_phone="+910000000000",
                    email_verified_at=now,
                    phone_verified_at=now,
                    status="active",
                    is_active=True,
                ),
                ProviderCredential(
                    provider_id=provider_id,
                    login_identifier=f"qp-{provider_id.hex}@example.test",
                    password_hash="qualification-only",
                    mfa_enabled=True,
                    is_active=True,
                ),
                ProfessionalVerification(
                    id=verification_id,
                    provider_id=provider_id,
                    status=status.value,
                    registration_authority_code="QUAL_AUTH",
                    registration_number_normalized=f"QP{verification_id.hex.upper()}",
                    registration_valid_from=now - timedelta(days=90),
                    registration_valid_until=now + timedelta(days=90),
                    previous_verification_valid=True,
                    reviewer_id="qualification-reviewer",
                    version=1,
                ),
            )
        )
        await db.flush()
        anchor = ProviderTrustVerificationEvidence(
            professional_verification_id=verification_id,
            origin=VerificationEvidenceOrigin.SERVER_REGISTRY_OBSERVATION.value,
            source_id="QUAL_PROFESSIONAL_SOURCE",
            adapter_version="1.0.0",
            observed_at=now - timedelta(days=1),
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            identity_binding_result=VerificationIdentityBindingResult.MATCHED.value,
            binding_method="SYNTHETIC_EXACT",
            observed_resource_version=1,
        )
        db.add(anchor)
        await db.flush()
        verification = await db.get(ProfessionalVerification, verification_id)
        assert verification is not None
        verification.server_provenance_evidence_id = anchor.id
        await db.commit()
    return facility_id, provider_id, verification_id, now


async def _seed_facility(
    factory,
    *,
    status: FacilityVerificationStatus = FacilityVerificationStatus.RECHECK_REQUIRED,
) -> tuple[uuid.UUID, uuid.UUID, datetime]:
    now = datetime.now(timezone.utc)
    facility_id, verification_id = uuid.uuid4(), uuid.uuid4()
    async with factory() as db:
        db.add(
            HospitalRegistry(
                id=facility_id,
                facility_code=f"QF-{facility_id.hex[:12]}",
                legal_name="Qualification facility",
                display_name="Qualification facility",
                country_code="IN",
                is_active=True,
            )
        )
        db.add(
            FacilityVerification(
                id=verification_id,
                facility_id=facility_id,
                status=status.value,
                registration_authority_code="QUAL_FAC_AUTH",
                registration_number_normalized=f"QF-{verification_id.hex}",
                registration_valid_from=now - timedelta(days=90),
                registration_valid_until=now + timedelta(days=90),
                previous_verification_valid=True,
                reviewer_id="qualification-reviewer",
                version=1,
            )
        )
        await db.flush()
        anchor = ProviderTrustVerificationEvidence(
            facility_verification_id=verification_id,
            origin=VerificationEvidenceOrigin.SERVER_REGISTRY_OBSERVATION.value,
            source_id="QUAL_FACILITY_SOURCE",
            adapter_version="1.0.0",
            observed_at=now - timedelta(days=1),
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            identity_binding_result=VerificationIdentityBindingResult.MATCHED.value,
            binding_method="SYNTHETIC_EXACT",
            observed_resource_version=1,
        )
        db.add(anchor)
        await db.flush()
        verification = await db.get(FacilityVerification, verification_id)
        assert verification is not None
        verification.server_provenance_evidence_id = anchor.id
        await db.commit()
    return facility_id, verification_id, now


async def _trusted_reviewer(
    factory, facility_id: uuid.UUID, permission: str
) -> uuid.UUID:
    now = datetime.now(timezone.utc)
    reviewer_id = uuid.uuid4()
    async with factory() as db:
        db.add(
            ProviderIdentity(
                id=reviewer_id,
                provider_uid=f"qr-{reviewer_id.hex}",
                hospital_id=facility_id,
                contact_email=f"qr-{reviewer_id.hex}@example.test",
                contact_phone="+910000000000",
                email_verified_at=now,
                phone_verified_at=now,
                status="active",
                is_active=True,
            )
        )
        db.add_all(
            (
                ProviderCredential(
                    provider_id=reviewer_id,
                    login_identifier=f"qr-{reviewer_id.hex}@example.test",
                    password_hash="qualification-only",
                    mfa_enabled=True,
                    is_active=True,
                ),
                ProviderTrustPermissionGrant(
                    provider_id=reviewer_id,
                    permission=permission,
                    scope_type="GLOBAL"
                    if permission == "PROFESSIONAL_REVIEW"
                    else "FACILITY",
                    facility_id=None
                    if permission == "PROFESSIONAL_REVIEW"
                    else facility_id,
                    granted_by_actor_id="qualification-governance",
                ),
            )
        )
        await db.commit()
    return reviewer_id


def _auth(actor_id: uuid.UUID, now: datetime) -> TrustManagementAuthentication:
    return TrustManagementAuthentication(
        provider_id=actor_id,
        method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
        session_authenticated=True,
        mfa_verified_at=now,
    )


def _professional_envelope(
    verification_id: uuid.UUID, registration: str, now: datetime
):
    return ValidatedRegistryLookupEnvelope(
        RegistryLookupInvocation(
            resource_id=verification_id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=1,
            request=ProfessionalLookupRequest(
                registration_authority_code="QUAL_AUTH",
                registration_number_normalized=registration,
                lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
            ),
            invoked_at=now - timedelta(minutes=1),
        ),
        RegistryObservation(
            resource_type=RegistryResourceType.PROFESSIONAL,
            source_id="QUAL_PROFESSIONAL_SOURCE",
            adapter_version="1.0.0",
            observed_at=now,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            identity_binding_result=VerificationIdentityBindingResult.MATCHED,
            binding_method="SYNTHETIC_EXACT",
        ),
    )


def _facility_envelope(verification_id: uuid.UUID, registration: str, now: datetime):
    return ValidatedRegistryLookupEnvelope(
        RegistryLookupInvocation(
            resource_id=verification_id,
            resource_type=RegistryResourceType.FACILITY,
            expected_version=1,
            request=FacilityLookupRequest(
                registration_authority_code="QUAL_FAC_AUTH",
                registration_number_normalized=registration,
                lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
            ),
            invoked_at=now - timedelta(minutes=1),
        ),
        RegistryObservation(
            resource_type=RegistryResourceType.FACILITY,
            source_id="QUAL_FACILITY_SOURCE",
            adapter_version="1.0.0",
            observed_at=now,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            identity_binding_result=VerificationIdentityBindingResult.MATCHED,
            binding_method="SYNTHETIC_EXACT",
        ),
    )


async def _barrier_pair(first, second):
    ready_one, ready_two, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def run(ready, operation):
        ready.set()
        await release.wait()
        try:
            return ("ok", await operation())
        except (
            ProviderTrustLifecycleApplicationError,
            VerificationApplicationError,
        ) as error:
            return ("error", getattr(error, "code", type(error).__name__))

    first_task = asyncio.create_task(run(ready_one, first))
    second_task = asyncio.create_task(run(ready_two, second))
    await asyncio.wait_for(
        asyncio.gather(ready_one.wait(), ready_two.wait()), timeout=5
    )
    release.set()
    return await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=10)


async def test_professional_terminal_human_race_has_one_winner_and_no_resurrection(
    session_factory,
):
    """A valid same-version REVOKE races positive system recheck under real row locks.

    SUSPEND is intentionally not legal from RECHECK_DUE; REVOKE is the valid
    terminal human command from the same source state and proves the requested
    winner-only/non-resurrection property without changing lifecycle policy.
    """
    facility_id, _, verification_id, now = await _seed_professional(session_factory)
    reviewer_id = await _trusted_reviewer(
        session_factory, facility_id, "PROFESSIONAL_REVIEW"
    )
    async with session_factory() as db:
        row = await db.get(ProfessionalVerification, verification_id)
        assert row is not None
        envelope = _professional_envelope(
            verification_id, row.registration_number_normalized, now
        )

    async def system_apply():
        async with session_factory() as db:
            return await ProviderVerificationApplicationService(
                db,
                source_policies=SourceAutomationPolicyRegistry(
                    [_professional_policy()]
                ),
                automation_enabled=True,
            ).apply_verification_observation(
                envelope, idempotency_key=str(uuid.uuid4()), now=now
            )

    async def human_revoke():
        async with session_factory() as db:
            return await ProviderTrustLifecycleApplicationService(
                db
            ).apply_professional(
                actor_id=reviewer_id,
                authentication=_auth(reviewer_id, now),
                resource_id=verification_id,
                command=ProfessionalTransitionCommand.REVOKE,
                facts=ProfessionalTransitionFacts(
                    decision_reason_code="QUALIFICATION_TERMINAL"
                ),
                expected_version=1,
                idempotency_key=str(uuid.uuid4()),
                now=now,
            )

    outcomes = await _barrier_pair(system_apply, human_revoke)
    assert sum(kind == "ok" for kind, _ in outcomes) == 1
    assert any(
        value == "LIFECYCLE_VERSION_CONFLICT"
        for kind, value in outcomes
        if kind == "error"
    )
    async with session_factory() as db:
        row = await db.get(ProfessionalVerification, verification_id)
        assert row is not None and row.version == 2
        assert row.status in {
            ProfessionalVerificationStatus.VERIFIED.value,
            ProfessionalVerificationStatus.REVOKED.value,
        }
        evidence_count = await db.scalar(
            select(func.count())
            .select_from(ProviderTrustVerificationEvidence)
            .where(
                ProviderTrustVerificationEvidence.professional_verification_id
                == verification_id
            )
        )
        assert evidence_count in {1, 2}


async def test_facility_terminal_human_race_has_one_winner_and_no_resurrection(
    session_factory,
):
    """A valid same-version CLOSE races positive facility recheck on real PostgreSQL."""
    facility_id, verification_id, now = await _seed_facility(session_factory)
    reviewer_id = await _trusted_reviewer(
        session_factory, facility_id, "FACILITY_REVIEW"
    )
    async with session_factory() as db:
        row = await db.get(FacilityVerification, verification_id)
        assert row is not None
        envelope = _facility_envelope(
            verification_id, row.registration_number_normalized, now
        )

    async def system_apply():
        async with session_factory() as db:
            return await ProviderVerificationApplicationService(
                db,
                source_policies=SourceAutomationPolicyRegistry([_facility_policy()]),
                automation_enabled=True,
            ).apply_verification_observation(
                envelope, idempotency_key=str(uuid.uuid4()), now=now
            )

    async def human_close():
        async with session_factory() as db:
            return await ProviderTrustLifecycleApplicationService(db).apply_facility(
                actor_id=reviewer_id,
                authentication=_auth(reviewer_id, now),
                resource_id=verification_id,
                command=FacilityTransitionCommand.CLOSE,
                facts=FacilityTransitionFacts(
                    decision_reason_code="QUALIFICATION_TERMINAL"
                ),
                expected_version=1,
                idempotency_key=str(uuid.uuid4()),
                now=now,
            )

    outcomes = await _barrier_pair(system_apply, human_close)
    assert sum(kind == "ok" for kind, _ in outcomes) == 1
    assert any(
        value == "LIFECYCLE_VERSION_CONFLICT"
        for kind, value in outcomes
        if kind == "error"
    )
    async with session_factory() as db:
        row = await db.get(FacilityVerification, verification_id)
        assert row is not None and row.version == 2
        assert row.status in {
            FacilityVerificationStatus.VERIFIED.value,
            FacilityVerificationStatus.CLOSED.value,
        }


@pytest.mark.parametrize(
    "terminal_status",
    (
        ProfessionalVerificationStatus.REJECTED,
        ProfessionalVerificationStatus.REVOKED,
        ProfessionalVerificationStatus.EXPIRED,
    ),
)
async def test_professional_terminal_states_never_resurrect_from_positive_observation(
    session_factory, terminal_status
):
    """A current positive observation cannot restore any professional terminal state."""
    _, _, verification_id, now = await _seed_professional(
        session_factory, status=terminal_status
    )
    async with session_factory() as db:
        row = await db.get(ProfessionalVerification, verification_id)
        assert row is not None
        envelope = _professional_envelope(
            verification_id, row.registration_number_normalized, now
        )
        result = await ProviderVerificationApplicationService(
            db,
            source_policies=SourceAutomationPolicyRegistry([_professional_policy()]),
            automation_enabled=True,
        ).apply_verification_observation(
            envelope, idempotency_key=str(uuid.uuid4()), now=now
        )
        assert result.lifecycle_mutated is False
    async with session_factory() as db:
        row = await db.get(ProfessionalVerification, verification_id)
        assert row is not None
        assert row.status == terminal_status.value and row.version == 1


@pytest.mark.parametrize(
    "terminal_status",
    (FacilityVerificationStatus.REJECTED, FacilityVerificationStatus.CLOSED),
)
async def test_facility_terminal_states_never_resurrect_from_positive_observation(
    session_factory, terminal_status
):
    """A current positive observation cannot restore any facility terminal state."""
    _, verification_id, now = await _seed_facility(
        session_factory, status=terminal_status
    )
    async with session_factory() as db:
        row = await db.get(FacilityVerification, verification_id)
        assert row is not None
        envelope = _facility_envelope(
            verification_id, row.registration_number_normalized, now
        )
        result = await ProviderVerificationApplicationService(
            db,
            source_policies=SourceAutomationPolicyRegistry([_facility_policy()]),
            automation_enabled=True,
        ).apply_verification_observation(
            envelope, idempotency_key=str(uuid.uuid4()), now=now
        )
        assert result.lifecycle_mutated is False
    async with session_factory() as db:
        row = await db.get(FacilityVerification, verification_id)
        assert row is not None
        assert row.status == terminal_status.value and row.version == 1


async def test_open_review_blocks_scheduler_real_postgres(session_factory):
    """A real open review row suppresses scheduling without mutating the target."""
    _, _, verification_id, now = await _seed_professional(
        session_factory, status=ProfessionalVerificationStatus.VERIFIED
    )
    async with session_factory() as db:
        row = await db.get(ProfessionalVerification, verification_id)
        assert row is not None
        row.next_review_at = now - timedelta(minutes=1)
        evidence = ProviderTrustVerificationEvidence(
            professional_verification_id=verification_id,
            origin=VerificationEvidenceOrigin.SERVER_REGISTRY_OBSERVATION.value,
            source_id="QUAL_PROFESSIONAL_SOURCE",
            adapter_version="1.0.0",
            observed_at=now,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_INACTIVE.value,
            identity_binding_result=VerificationIdentityBindingResult.MATCHED.value,
            binding_method="SYNTHETIC_EXACT",
            observed_resource_version=1,
        )
        db.add(evidence)
        await db.flush()
        db.add(
            ProviderTrustVerificationReviewWork(
                evidence_id=evidence.id,
                disposition="HUMAN_REVIEW_REQUIRED",
                reason_code="QUALIFICATION_OPEN_REVIEW",
                status=VerificationReviewWorkStatus.OPEN.value,
            )
        )
        await db.commit()
    async with session_factory() as db:
        result = await ProviderVerificationSchedulerService(
            db,
            source_policies=SourceAutomationPolicyRegistry([_professional_policy()]),
            automation_enabled=True,
        ).sweep_due_verifications(now=now)
        assert all(
            work.professional_verification_id != verification_id for work in result
        )
    async with session_factory() as db:
        row = await db.get(ProfessionalVerification, verification_id)
        count = await db.scalar(
            select(func.count())
            .select_from(ProviderVerificationWork)
            .where(
                ProviderVerificationWork.professional_verification_id == verification_id
            )
        )
        assert (
            row is not None
            and row.status == ProfessionalVerificationStatus.VERIFIED.value
            and row.version == 1
        )
        assert count == 0


async def test_two_schedulers_create_exactly_one_work_item_under_real_row_lock(
    session_factory,
):
    """Concurrent schedulers linearize to one scheduled transition and one work row."""
    _, _, verification_id, now = await _seed_professional(
        session_factory, status=ProfessionalVerificationStatus.VERIFIED
    )
    async with session_factory() as db:
        row = await db.get(ProfessionalVerification, verification_id)
        assert row is not None
        row.next_review_at = now - timedelta(minutes=1)
        await db.commit()

    async def sweep():
        async with session_factory() as db:
            return await ProviderVerificationSchedulerService(
                db,
                source_policies=SourceAutomationPolicyRegistry(
                    [_professional_policy()]
                ),
                automation_enabled=True,
            ).sweep_due_verifications(now=now)

    outcomes = await _barrier_pair(sweep, sweep)
    assert [kind for kind, _ in outcomes] == ["ok", "ok"]
    assert sum(len(result) for _, result in outcomes) == 1
    async with session_factory() as db:
        row = await db.get(ProfessionalVerification, verification_id)
        work_count = await db.scalar(
            select(func.count())
            .select_from(ProviderVerificationWork)
            .where(
                ProviderVerificationWork.professional_verification_id == verification_id
            )
        )
        assert row is not None
        assert row.status == ProfessionalVerificationStatus.RECHECK_DUE.value
        assert row.version == 2 and work_count == 1


async def test_policy_disable_before_worker_call_cancels_without_adapter_or_evidence(
    session_factory,
):
    """The worker rechecks source policy before outbound I/O and cancels terminally."""
    _, _, verification_id, now = await _seed_professional(session_factory)
    work_id = uuid.uuid4()
    async with session_factory() as db:
        row = await db.get(ProfessionalVerification, verification_id)
        assert row is not None
        db.add(
            ProviderVerificationWork(
                id=work_id,
                professional_verification_id=verification_id,
                lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK.value,
                source_id="QUAL_PROFESSIONAL_SOURCE",
                adapter_version="1.0.0",
                registration_authority_code=row.registration_authority_code,
                registration_number_normalized=row.registration_number_normalized,
                expected_resource_version=1,
                scheduler_reason="QUALIFICATION",
                status=VerificationWorkStatus.PENDING.value,
                next_attempt_at=now,
            )
        )
        await db.commit()
    adapter = SyntheticRegistryAdapter(
        RegistrySourceDescriptor(
            source_id="QUAL_PROFESSIONAL_SOURCE",
            adapter_version="1.0.0",
            supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
        )
    )
    async with session_factory() as db:
        worker = ProviderVerificationWorkerService(
            db,
            worker_id="qualification-policy-disable",
            adapters={"QUAL_PROFESSIONAL_SOURCE": adapter},
            source_policies=SourceAutomationPolicyRegistry(
                [_professional_policy(enabled=False)]
            ),
            automation_enabled=True,
        )
        claimed = await worker.claim_work_batch(now=now)
        selected = next(item for item in claimed if item.id == work_id)
        assert (
            await worker.process_work_item(selected, now=now)
            is VerificationWorkStatus.CANCELLED_POLICY
        )
    async with session_factory() as db:
        work = await db.get(ProviderVerificationWork, work_id)
        row = await db.get(ProfessionalVerification, verification_id)
        evidence_count = await db.scalar(
            select(func.count())
            .select_from(ProviderTrustVerificationEvidence)
            .where(
                ProviderTrustVerificationEvidence.professional_verification_id
                == verification_id
            )
        )
        assert (
            work is not None
            and work.status == VerificationWorkStatus.CANCELLED_POLICY.value
        )
        assert (
            work.attempt_count == 0 and work.last_error_code == "SOURCE_POLICY_DISABLED"
        )
        assert (
            row is not None
            and row.status == ProfessionalVerificationStatus.RECHECK_DUE.value
            and row.version == 1
        )
        assert evidence_count == 1


async def _install_audit_failure_trigger(factory) -> None:
    async with factory() as db:
        await db.execute(
            text(
                f"CREATE FUNCTION public.{_AUDIT_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS "
                "$$ BEGIN RAISE EXCEPTION 'phase5g2 audit boundary'; END; $$"
            )
        )
        await db.execute(
            text(
                f"CREATE TRIGGER {_AUDIT_TRIGGER} BEFORE INSERT ON public.audit_outbox "
                f"FOR EACH ROW EXECUTE FUNCTION public.{_AUDIT_FUNCTION}()"
            )
        )
        await db.commit()


async def _remove_audit_failure_trigger(factory) -> None:
    async with factory() as db:
        await db.execute(
            text(f"DROP TRIGGER IF EXISTS {_AUDIT_TRIGGER} ON public.audit_outbox")
        )
        await db.execute(text(f"DROP FUNCTION IF EXISTS public.{_AUDIT_FUNCTION}()"))
        await db.commit()


async def test_database_audit_trigger_rolls_back_application_evidence_lifecycle_and_idempotency(
    session_factory,
):
    """A physical outbox failure rolls back every Phase-5E write as one transaction."""
    _, _, verification_id, now = await _seed_professional(session_factory)
    async with session_factory() as db:
        row = await db.get(ProfessionalVerification, verification_id)
        assert row is not None
        envelope = _professional_envelope(
            verification_id, row.registration_number_normalized, now
        )
    await _install_audit_failure_trigger(session_factory)
    key = str(uuid.uuid4())
    try:
        async with session_factory() as db:
            service = ProviderVerificationApplicationService(
                db,
                source_policies=SourceAutomationPolicyRegistry(
                    [_professional_policy()]
                ),
                automation_enabled=True,
            )
            with pytest.raises(DBAPIError):
                await service.apply_verification_observation(
                    envelope, idempotency_key=key, now=now
                )
    finally:
        await _remove_audit_failure_trigger(session_factory)
    async with session_factory() as db:
        row = await db.get(ProfessionalVerification, verification_id)
        evidence_count = await db.scalar(
            select(func.count())
            .select_from(ProviderTrustVerificationEvidence)
            .where(
                ProviderTrustVerificationEvidence.professional_verification_id
                == verification_id
            )
        )
        idem_count = await db.scalar(
            text(
                "SELECT count(*) FROM public.mutation_idempotency WHERE idempotency_key = :key"
            ),
            {"key": key},
        )
        assert (
            row is not None
            and row.status == ProfessionalVerificationStatus.RECHECK_DUE.value
            and row.version == 1
        )
        assert evidence_count == 1 and idem_count == 0


async def test_database_audit_trigger_rolls_back_human_lifecycle_and_idempotency(
    session_factory,
):
    """The Phase-3E human lifecycle path shares the physical outbox transaction."""
    facility_id, _, verification_id, now = await _seed_professional(
        session_factory, status=ProfessionalVerificationStatus.VERIFIED
    )
    reviewer_id = await _trusted_reviewer(
        session_factory, facility_id, "PROFESSIONAL_REVIEW"
    )
    await _install_audit_failure_trigger(session_factory)
    key = str(uuid.uuid4())
    try:
        async with session_factory() as db:
            with pytest.raises(
                ProviderTrustLifecycleApplicationError,
                match="TRANSACTION_INTEGRITY_FAILURE",
            ):
                await ProviderTrustLifecycleApplicationService(db).apply_professional(
                    actor_id=reviewer_id,
                    authentication=_auth(reviewer_id, now),
                    resource_id=verification_id,
                    command=ProfessionalTransitionCommand.SUSPEND,
                    facts=ProfessionalTransitionFacts(
                        decision_reason_code="QUALIFICATION"
                    ),
                    expected_version=1,
                    idempotency_key=key,
                    now=now,
                )
    finally:
        await _remove_audit_failure_trigger(session_factory)
    async with session_factory() as db:
        row = await db.get(ProfessionalVerification, verification_id)
        idem_count = await db.scalar(
            text(
                "SELECT count(*) FROM public.mutation_idempotency WHERE idempotency_key = :key"
            ),
            {"key": key},
        )
        assert (
            row is not None
            and row.status == ProfessionalVerificationStatus.VERIFIED.value
            and row.version == 1
        )
        assert idem_count == 0
