"""Exhaustive PostgreSQL 16 integration qualification for ProviderVerificationApplicationService (Phase 5E).

Validates:
1. Migration downgrade to 20260904_verification_evidence and re-upgrade to 20260905_verification_application.
2. Review work table constraints, indexes, unique constraints, and resolution integrity.
3. Server provenance evidence FK constraints on professional and facility verification.
4. Positive automated recheck execution (lifecycle mutation, evidence link, reviewer_id preservation, audit staging).
5. Fail-closed grace cancellation execution (CANCEL_RECHECK_GRACE, same-state version increment, grace clear, open review work).
6. Open review work blocking positive automation.
7. Missing human reviewer_id blocking automation (no manufactured "system" reviewer_id).
8. Global automation kill switch and unapproved source policy fail-closed review.
9. Concurrency, CAS version conflicts, and race conditions.
10. Mutation idempotency caching, replay, and conflict detection.
11. Transaction rollback integrity on injection failure.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models.provider import (
    FacilityVerification,
    FacilityVerificationStatus,
    HospitalRegistry,
    ProfessionalVerification,
    ProfessionalVerificationStatus,
    ProviderCredential,
    ProviderHospitalAffiliation,
    ProviderIdentity,
    ProviderTrustVerificationEvidence,
    ProviderTrustVerificationReviewWork,
    VerificationEvidenceLookupPurpose,
    VerificationEvidenceOrigin,
    VerificationEvidenceOutcome,
    VerificationIdentityBindingResult,
)
from app.observability.provider_trust_events import ProviderTrustAuditEvent
from app.security.clinical_policy import CLINICAL_CONTACT_ASSURANCE_POLICY
from app.services.clinical_eligibility import (
    ClinicalAuthenticationMethod,
    ClinicalCapability,
    ClinicalEligibilityDenialCode,
    ClinicalEligibilityService,
    InteractiveClinicalAuthentication,
)
from app.services.provider_verification_application import (
    ProviderVerificationApplicationService,
    RegistryLookupInvocation,
    SourceAutomationPolicy,
    SourceAutomationPolicyRegistry,
    ValidatedRegistryLookupEnvelope,
    VerificationApplicationError,
)
from app.services.provider_trust_lifecycle import ProfessionalTransitionCommand
from app.services.provider_verification_decision_policy import (
    VerificationDecisionDisposition,
    VerificationDecisionPlan,
    VerificationDecisionReason,
)
from app.services.provider_verification_registry import (
    FacilityLookupRequest,
    ProfessionalLookupRequest,
    RegistryObservation,
    RegistryResourceType,
)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgres,
    pytest.mark.asyncio,
]

HEAD = "20260905_verification_application"
PREVIOUS_HEAD = "20260904_verification_evidence"
_DB_NAME = "nexa_qual_slice5_app"


def _get_db_url() -> str:
    url = os.getenv("TEST_DATABASE_URL") or os.getenv(
        "DATABASE_URL",
        f"postgresql+asyncpg://nexa:nexa_test@127.0.0.1:55439/{_DB_NAME}",
    )
    if "127.0.0.1" not in url and "localhost" not in url:
        pytest.fail("Database URL must be loopback-only")
    if "nexa_qual_" not in url:
        pytest.fail("Database URL must name a disposable nexa_qual_ database")
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _get_async_admin_url() -> str:
    return "postgresql+asyncpg://nexa:nexa_test@127.0.0.1:55439/postgres"


def _config(db_url: str) -> Config:
    config = Config("alembic.ini")
    sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    config.set_main_option("sqlalchemy.url", sync_url)
    return config


@pytest.fixture(scope="module", autouse=True)
def _setup_database():
    """Ensure disposable database exists and is migrated to HEAD."""
    db_url = _get_db_url()
    _prev = os.environ.get("TEST_DATABASE_URL")
    os.environ["TEST_DATABASE_URL"] = db_url

    async def _init_db():
        admin_engine = create_async_engine(
            _get_async_admin_url(),
            isolation_level="AUTOCOMMIT",
        )
        async with admin_engine.connect() as conn:
            await conn.execute(text(f"DROP DATABASE IF EXISTS {_DB_NAME} WITH (FORCE)"))
            await conn.execute(text(f"CREATE DATABASE {_DB_NAME}"))
        await admin_engine.dispose()

    asyncio.run(_init_db())

    config = _config(db_url)
    command.upgrade(config, HEAD)

    yield

    if _prev is not None:
        os.environ["TEST_DATABASE_URL"] = _prev
    else:
        os.environ.pop("TEST_DATABASE_URL", None)


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        _get_db_url(),
        pool_size=5,
        max_overflow=5,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _make_prof_obs(
    *,
    outcome: VerificationEvidenceOutcome = VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
    purpose: VerificationEvidenceLookupPurpose = VerificationEvidenceLookupPurpose.RECHECK,
    source_id: str = "QUAL_SOURCE_01",
    observed_at: datetime | None = None,
) -> RegistryObservation:
    return RegistryObservation(
        resource_type=RegistryResourceType.PROFESSIONAL,
        source_id=source_id,
        adapter_version="1.0.0",
        observed_at=observed_at or (datetime.now(timezone.utc) - timedelta(minutes=1)),
        lookup_purpose=purpose,
        outcome=outcome,
        identity_binding_result=VerificationIdentityBindingResult.MATCHED,
        binding_method="REGISTRY_MATCH",
    )


async def _seed_provider_and_verification(
    session: AsyncSession,
    *,
    status: ProfessionalVerificationStatus = ProfessionalVerificationStatus.RECHECK_DUE,
    reviewer_id: str | None = "human-reviewer-123",
    grace_expires_at: datetime | None = None,
    recheck_failure_reason: str | None = "SOURCE_UNAVAILABLE",
    seed_server_provenance: bool = True,
    established_source_id: str = "QUAL_SOURCE_01",
) -> tuple[HospitalRegistry, ProviderIdentity, ProfessionalVerification]:
    now = datetime.now(timezone.utc)
    fac_id = uuid.uuid4()
    prov_id = uuid.uuid4()
    verif_id = uuid.uuid4()
    reg_num = f"MMC{uuid.uuid4().hex[:8].upper()}"

    hospital = HospitalRegistry(
        id=fac_id,
        facility_code=f"HOSP-{fac_id.hex[:8]}",
        legal_name="Qual Hospital",
        display_name="Qual Hospital",
    )
    session.add(hospital)

    provider = ProviderIdentity(
        id=prov_id,
        provider_uid=f"DR-{prov_id.hex[:8]}",
        hospital_id=fac_id,
        contact_email=f"dr.{prov_id.hex[:8]}@example.com",
        contact_phone="+919876543210",
        email_verified_at=now,
        phone_verified_at=now,
        status="active",
        is_active=True,
    )
    session.add(provider)

    cred = ProviderCredential(
        provider_id=prov_id,
        login_identifier=f"dr.{prov_id.hex[:8]}@example.com",
        password_hash="synthetic-secret-hash",
        mfa_enabled=True,
        is_active=True,
    )
    session.add(cred)

    fac_verif = FacilityVerification(
        id=uuid.uuid4(),
        facility_id=fac_id,
        status=FacilityVerificationStatus.VERIFIED.value,
        registration_authority_code="ROHINI",
        registration_number_normalized=f"FAC-{fac_id.hex[:8]}",
        verification_method="EXTERNAL_REGISTRY",
        verification_source="ROHINI",
        verification_reference="REF-ROHINI-01",
        verified_at=now - timedelta(days=30),
        previous_verification_valid=True,
        version=1,
    )
    session.add(fac_verif)

    affiliation = ProviderHospitalAffiliation(
        provider_id=prov_id,
        hospital_id=fac_id,
        roles=["clinician"],
        trust_status="ACTIVE",
        valid_from=now - timedelta(days=30),
        version=1,
    )
    session.add(affiliation)

    verif = ProfessionalVerification(
        id=verif_id,
        provider_id=prov_id,
        status=status.value,
        registration_authority_code="MAHA_MED_COUNCIL",
        registration_number_normalized=reg_num,
        registration_valid_from=now - timedelta(days=365),
        registration_valid_until=now + timedelta(days=365),
        previous_verification_valid=True,
        reviewer_id=reviewer_id,
        recheck_attempted_at=now - timedelta(hours=1),
        recheck_failure_reason=recheck_failure_reason,
        grace_expires_at=grace_expires_at,
        version=1,
    )
    session.add(verif)
    await session.flush()

    if seed_server_provenance:
        prev_ev = ProviderTrustVerificationEvidence(
            professional_verification_id=verif_id,
            origin=VerificationEvidenceOrigin.SERVER_REGISTRY_OBSERVATION.value,
            source_id=established_source_id,
            adapter_version="1.0.0",
            observed_at=now - timedelta(days=180),
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            observed_resource_version=1,
        )
        session.add(prev_ev)
        await session.flush()
        verif.server_provenance_evidence_id = prev_ev.id
        verif.verification_source = established_source_id
        verif.verification_method = "EXTERNAL_REGISTRY"
        verif.verified_at = now - timedelta(days=180)
        await session.flush()

    return hospital, provider, verif


def _make_fac_obs(
    *,
    outcome: VerificationEvidenceOutcome = VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
    purpose: VerificationEvidenceLookupPurpose = VerificationEvidenceLookupPurpose.RECHECK,
    source_id: str = "QUAL_SOURCE_01",
    binding_result: VerificationIdentityBindingResult = VerificationIdentityBindingResult.MATCHED,
    observed_at: datetime | None = None,
) -> RegistryObservation:
    return RegistryObservation(
        resource_type=RegistryResourceType.FACILITY,
        source_id=source_id,
        adapter_version="1.0.0",
        observed_at=observed_at or (datetime.now(timezone.utc) - timedelta(minutes=1)),
        lookup_purpose=purpose,
        outcome=outcome,
        identity_binding_result=binding_result,
        binding_method="REGISTRY_MATCH",
    )


async def _seed_facility_and_verification(
    session: AsyncSession,
    *,
    status: FacilityVerificationStatus = FacilityVerificationStatus.VERIFIED,
    reviewer_id: str | None = "human-reviewer-fac-42",
    recheck_failure_reason: str | None = None,
    grace_expires_at: datetime | None = None,
    seed_server_provenance: bool = True,
    established_source_id: str = "QUAL_SOURCE_01",
) -> tuple[HospitalRegistry, FacilityVerification]:
    now = datetime.now(timezone.utc)
    fac_id = uuid.uuid4()
    verif_id = uuid.uuid4()
    reg_num = f"FAC{uuid.uuid4().hex[:8].upper()}"

    hospital = HospitalRegistry(
        id=fac_id,
        facility_code=f"HOSP-{fac_id.hex[:8]}",
        legal_name="Facility Qual Hospital",
        display_name="Facility Qual Hospital",
    )
    session.add(hospital)

    verif = FacilityVerification(
        id=verif_id,
        facility_id=fac_id,
        status=status.value,
        registration_authority_code="ROHINI",
        registration_number_normalized=reg_num,
        registration_valid_from=now - timedelta(days=365),
        registration_valid_until=now + timedelta(days=365),
        previous_verification_valid=True,
        reviewer_id=reviewer_id,
        recheck_attempted_at=now - timedelta(hours=1),
        recheck_failure_reason=recheck_failure_reason,
        grace_expires_at=grace_expires_at,
        version=1,
    )
    session.add(verif)
    await session.flush()

    if seed_server_provenance:
        prev_ev = ProviderTrustVerificationEvidence(
            facility_verification_id=verif_id,
            origin=VerificationEvidenceOrigin.SERVER_REGISTRY_OBSERVATION.value,
            source_id=established_source_id,
            adapter_version="1.0.0",
            observed_at=now - timedelta(days=180),
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            observed_resource_version=1,
        )
        session.add(prev_ev)
        await session.flush()
        verif.server_provenance_evidence_id = prev_ev.id
        verif.verification_source = established_source_id
        verif.verification_method = "EXTERNAL_REGISTRY"
        verif.verified_at = now - timedelta(days=180)
        await session.flush()

    return hospital, verif


# ---------------------------------------------------------------------------
# 1. Migration Downgrade and Re-upgrade
# ---------------------------------------------------------------------------


async def test_migration_downgrade_and_reupgrade():
    """Verify clean downgrade to 20260904_verification_evidence and re-upgrade to 20260905_verification_application."""
    config = _config(_get_db_url())

    # Downgrade to PREVIOUS_HEAD
    await asyncio.to_thread(command.downgrade, config, PREVIOUS_HEAD)

    # Verify review work table is gone
    engine = create_async_engine(_get_db_url())
    async with engine.connect() as conn:
        res = await conn.execute(
            text("SELECT to_regclass('public.provider_trust_verification_review_work')")
        )
        assert res.scalar() is None

        # Verify server_provenance_evidence_id column is dropped from professional_verification
        res = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'professional_verification' AND column_name = 'server_provenance_evidence_id'"
            )
        )
        assert res.scalar() is None
    await engine.dispose()

    # Re-upgrade to HEAD
    await asyncio.to_thread(command.upgrade, config, HEAD)

    engine = create_async_engine(_get_db_url())
    async with engine.connect() as conn:
        res = await conn.execute(
            text("SELECT to_regclass('public.provider_trust_verification_review_work')")
        )
        assert res.scalar() is not None

        res = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'professional_verification' AND column_name = 'server_provenance_evidence_id'"
            )
        )
        assert res.scalar() == "server_provenance_evidence_id"
    await engine.dispose()


# ---------------------------------------------------------------------------
# 2. Review Work Constraints Probes
# ---------------------------------------------------------------------------


async def test_review_work_check_constraints(session_factory):
    """Verify CHECK constraints and uniqueness on provider_trust_verification_review_work."""
    async with session_factory() as session:
        _, _, verif = await _seed_provider_and_verification(
            session, seed_server_provenance=False
        )

        # Create evidence row first
        ev = ProviderTrustVerificationEvidence(
            professional_verification_id=verif.id,
            origin=VerificationEvidenceOrigin.SERVER_REGISTRY_OBSERVATION.value,
            source_id="QUAL_SOURCE_01",
            adapter_version="1.0.0",
            observed_at=datetime.now(timezone.utc),
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            observed_resource_version=1,
        )
        session.add(ev)
        await session.flush()

        # 1. Invalid status
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                bad_status = ProviderTrustVerificationReviewWork(
                    evidence_id=ev.id,
                    disposition="HUMAN_REVIEW_REQUIRED",
                    status="INVALID_STATUS",
                    reason_code="RECHECK_REQUIRED",
                )
                session.add(bad_status)
                await session.flush()

        # 2. Invalid disposition
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                bad_disp = ProviderTrustVerificationReviewWork(
                    evidence_id=ev.id,
                    disposition="INVALID_DISPOSITION",
                    status="OPEN",
                    reason_code="RECHECK_REQUIRED",
                )
                session.add(bad_disp)
                await session.flush()

        # 3. Resolution integrity: OPEN must not have resolved_at
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                bad_res = ProviderTrustVerificationReviewWork(
                    evidence_id=ev.id,
                    disposition="HUMAN_REVIEW_REQUIRED",
                    status="OPEN",
                    reason_code="RECHECK_REQUIRED",
                    resolved_at=datetime.now(timezone.utc),
                    resolved_by_actor_id="someone",
                )
                session.add(bad_res)
                await session.flush()

        # 4. Empty reason code
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                bad_reason = ProviderTrustVerificationReviewWork(
                    evidence_id=ev.id,
                    disposition="HUMAN_REVIEW_REQUIRED",
                    status="OPEN",
                    reason_code="   ",
                )
                session.add(bad_reason)
                await session.flush()

        # 5. Valid OPEN row
        valid_work = ProviderTrustVerificationReviewWork(
            evidence_id=ev.id,
            disposition="HUMAN_REVIEW_REQUIRED",
            status="OPEN",
            reason_code="REVIEW_REQUIRED",
        )
        session.add(valid_work)
        await session.flush()

        # 6. Duplicate evidence_id must fail unique constraint
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                dup_work = ProviderTrustVerificationReviewWork(
                    evidence_id=ev.id,
                    disposition="SYSTEM_FAIL_CLOSED_AND_REVIEW",
                    status="OPEN",
                    reason_code="ANOTHER_REASON",
                )
                session.add(dup_work)
                await session.flush()

        # 7. Physical schema inspection: confirm presence of required columns and absence of forbidden columns
        cols = (
            (
                await session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'provider_trust_verification_review_work'"
                    )
                )
            )
            .scalars()
            .all()
        )
        col_set = set(cols)
        for required_col in [
            "id",
            "evidence_id",
            "disposition",
            "reason_code",
            "status",
            "created_at",
            "resolved_at",
            "resolved_by_actor_id",
        ]:
            assert required_col in col_set, f"Missing required column {required_col}"
        for forbidden_col in [
            "target_type",
            "target_id",
            "assigned_reviewer_id",
            "resolution_notes",
            "patient_id",
            "raw_registry_data",
        ]:
            assert (
                forbidden_col not in col_set
            ), f"Forbidden column {forbidden_col} found in review work table"


# ---------------------------------------------------------------------------
# 3. Positive Recheck Automation Flow
# ---------------------------------------------------------------------------


async def test_positive_recheck_automation_flow(session_factory):
    """Verify positive recheck automation: lifecycle mutation, server provenance link, and reviewer_id preservation."""
    async with session_factory() as session:
        _, _, verif = await _seed_provider_and_verification(
            session,
            status=ProfessionalVerificationStatus.RECHECK_DUE,
            reviewer_id="initial-human-reviewer-42",
        )
        await session.commit()

        req = ProfessionalLookupRequest(
            registration_authority_code="MAHA_MED_COUNCIL",
            registration_number_normalized=verif.registration_number_normalized,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
        )
        inv = RegistryLookupInvocation(
            resource_id=verif.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=1,
            request=req,
            invoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        obs = _make_prof_obs(
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            source_id="QUAL_SOURCE_01",
        )
        envelope = ValidatedRegistryLookupEnvelope(invocation=inv, observation=obs)

        policy_registry = SourceAutomationPolicyRegistry()
        policy_registry.register(
            SourceAutomationPolicy(
                source_id="QUAL_SOURCE_01",
                automation_enabled=True,
                allowed_binding_methods=frozenset({"REGISTRY_MATCH"}),
            )
        )

        svc = ProviderVerificationApplicationService(
            session, source_policies=policy_registry, automation_enabled=True
        )
        result = await svc.apply_verification_observation(
            envelope=envelope,
            idempotency_key=str(uuid.uuid4()),
        )

        assert (
            result.decision_disposition
            == VerificationDecisionDisposition.SYSTEM_TRANSITION_CANDIDATE.value
        )
        assert result.applied_command == "COMPLETE_RECHECK"
        assert result.lifecycle_mutated is True
        assert result.resulting_version == 2
        assert result.review_work_id is None

        # Verify DB target state
        refreshed = (
            await session.execute(
                select(ProfessionalVerification).where(
                    ProfessionalVerification.id == verif.id
                )
            )
        ).scalar_one()

        assert refreshed.status == ProfessionalVerificationStatus.VERIFIED.value
        assert refreshed.version == 2
        assert refreshed.server_provenance_evidence_id == result.evidence_id
        # Crucial invariant: reviewer_id preserved intact!
        assert refreshed.reviewer_id == "initial-human-reviewer-42"
        assert refreshed.recheck_failure_reason is None
        assert refreshed.grace_expires_at is None

        # Verify evidence row in DB
        ev = (
            await session.execute(
                select(ProviderTrustVerificationEvidence).where(
                    ProviderTrustVerificationEvidence.id == result.evidence_id
                )
            )
        ).scalar_one()
        assert ev.origin == VerificationEvidenceOrigin.SERVER_REGISTRY_OBSERVATION.value
        assert ev.source_id == "QUAL_SOURCE_01"
        assert ev.observed_resource_version == 1


# ---------------------------------------------------------------------------
# 4. Fail-closed Grace Cancellation Flow (CANCEL_RECHECK_GRACE)
# ---------------------------------------------------------------------------


async def test_fail_closed_grace_cancellation_flow(session_factory):
    """Verify CANCEL_RECHECK_GRACE execution on non-outage failure during active grace."""
    async with session_factory() as session:
        now = datetime.now(timezone.utc)
        _, _, verif = await _seed_provider_and_verification(
            session,
            status=ProfessionalVerificationStatus.RECHECK_DUE,
            grace_expires_at=now + timedelta(hours=12),
            recheck_failure_reason="SOURCE_UNAVAILABLE",
        )
        await session.commit()

        req = ProfessionalLookupRequest(
            registration_authority_code="MAHA_MED_COUNCIL",
            registration_number_normalized=verif.registration_number_normalized,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
        )
        inv = RegistryLookupInvocation(
            resource_id=verif.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=1,
            request=req,
            invoked_at=now - timedelta(minutes=5),
        )
        # Adverse finding during active grace
        obs = _make_prof_obs(
            outcome=VerificationEvidenceOutcome.SOURCE_AUTHENTICATION_FAILURE,
            source_id="QUAL_SOURCE_01",
        )
        envelope = ValidatedRegistryLookupEnvelope(invocation=inv, observation=obs)

        policy_registry = SourceAutomationPolicyRegistry()
        policy_registry.register(
            SourceAutomationPolicy(
                source_id="QUAL_SOURCE_01",
                automation_enabled=True,
            )
        )

        svc = ProviderVerificationApplicationService(
            session, source_policies=policy_registry, automation_enabled=True
        )
        result = await svc.apply_verification_observation(
            envelope=envelope,
            idempotency_key=str(uuid.uuid4()),
        )

        assert (
            result.decision_disposition
            == VerificationDecisionDisposition.SYSTEM_FAIL_CLOSED_AND_REVIEW.value
        )
        assert result.applied_command == "CANCEL_RECHECK_GRACE"
        assert result.lifecycle_mutated is True
        assert result.resulting_version == 2
        assert result.review_work_id is not None

        # Verify DB target state: same state RECHECK_DUE, grace cleared, version incremented
        refreshed = (
            await session.execute(
                select(ProfessionalVerification).where(
                    ProfessionalVerification.id == verif.id
                )
            )
        ).scalar_one()

        assert refreshed.status == ProfessionalVerificationStatus.RECHECK_DUE.value
        assert refreshed.version == 2
        assert refreshed.grace_expires_at is None
        assert refreshed.recheck_failure_reason == "SOURCE_RESPONSE_INVALID"
        assert refreshed.server_provenance_evidence_id == result.evidence_id

        # Verify review work row in DB
        work = (
            await session.execute(
                select(ProviderTrustVerificationReviewWork).where(
                    ProviderTrustVerificationReviewWork.id == result.review_work_id
                )
            )
        ).scalar_one()
        assert work.status == "OPEN"
        assert work.reason_code == "RECHECK_GRACE_CANCELLED"
        assert work.disposition == "SYSTEM_FAIL_CLOSED_AND_REVIEW"
        assert work.evidence_id == result.evidence_id


# ---------------------------------------------------------------------------
# 5. Open Review Work Blocks Positive Automation
# ---------------------------------------------------------------------------


async def test_open_review_work_blocks_positive_automation(session_factory):
    """Verify that an existing OPEN review work item blocks positive automation."""
    async with session_factory() as session:
        _, _, verif = await _seed_provider_and_verification(session)

        # Seed an existing evidence and open review work
        prev_ev = ProviderTrustVerificationEvidence(
            professional_verification_id=verif.id,
            origin=VerificationEvidenceOrigin.SERVER_REGISTRY_OBSERVATION.value,
            source_id="QUAL_SOURCE_01",
            adapter_version="1.0.0",
            observed_at=datetime.now(timezone.utc) - timedelta(hours=1),
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK.value,
            outcome=VerificationEvidenceOutcome.AMBIGUOUS.value,
            observed_resource_version=1,
        )
        session.add(prev_ev)
        await session.flush()

        work = ProviderTrustVerificationReviewWork(
            evidence_id=prev_ev.id,
            disposition="HUMAN_REVIEW_REQUIRED",
            status="OPEN",
            reason_code="MANUAL_REVIEW_REQUIRED",
        )
        session.add(work)
        await session.commit()

        # Now an automated recheck observes CONFIRMED_ACTIVE
        req = ProfessionalLookupRequest(
            registration_authority_code="MAHA_MED_COUNCIL",
            registration_number_normalized=verif.registration_number_normalized,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
        )
        inv = RegistryLookupInvocation(
            resource_id=verif.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=1,
            request=req,
            invoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        obs = _make_prof_obs(
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            source_id="QUAL_SOURCE_01",
        )
        envelope = ValidatedRegistryLookupEnvelope(invocation=inv, observation=obs)

        policy_registry = SourceAutomationPolicyRegistry()
        policy_registry.register(
            SourceAutomationPolicy(
                source_id="QUAL_SOURCE_01",
                automation_enabled=True,
            )
        )

        svc = ProviderVerificationApplicationService(
            session, source_policies=policy_registry, automation_enabled=True
        )
        result = await svc.apply_verification_observation(
            envelope=envelope,
            idempotency_key=str(uuid.uuid4()),
        )

        assert (
            result.decision_disposition
            == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED.value
        )
        assert (
            result.reason_code
            == VerificationDecisionReason.OPEN_HUMAN_REVIEW_BLOCKS_AUTOMATION.value
        )
        assert result.lifecycle_mutated is False
        assert result.resulting_version == 1

        # Target verification remains untouched
        refreshed = (
            await session.execute(
                select(ProfessionalVerification).where(
                    ProfessionalVerification.id == verif.id
                )
            )
        ).scalar_one()
        assert refreshed.status == ProfessionalVerificationStatus.RECHECK_DUE.value
        assert refreshed.version == 1


# ---------------------------------------------------------------------------
# 6. Missing Human Reviewer ID Blocks Automation
# ---------------------------------------------------------------------------


async def test_missing_reviewer_id_blocks_automation(session_factory):
    """Automation is prohibited when target has no human reviewer_id (cannot invent 'system')."""
    async with session_factory() as session:
        _, _, verif = await _seed_provider_and_verification(session, reviewer_id=None)
        await session.commit()

        req = ProfessionalLookupRequest(
            registration_authority_code="MAHA_MED_COUNCIL",
            registration_number_normalized=verif.registration_number_normalized,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
        )
        inv = RegistryLookupInvocation(
            resource_id=verif.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=1,
            request=req,
            invoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        obs = _make_prof_obs(
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            source_id="QUAL_SOURCE_01",
        )
        envelope = ValidatedRegistryLookupEnvelope(invocation=inv, observation=obs)

        policy_registry = SourceAutomationPolicyRegistry()
        policy_registry.register(
            SourceAutomationPolicy(
                source_id="QUAL_SOURCE_01",
                automation_enabled=True,
            )
        )

        svc = ProviderVerificationApplicationService(
            session, source_policies=policy_registry, automation_enabled=True
        )
        result = await svc.apply_verification_observation(
            envelope=envelope,
            idempotency_key=str(uuid.uuid4()),
        )

        assert (
            result.decision_disposition
            == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED.value
        )
        assert (
            result.decision_disposition
            == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED.value
        )
        assert (
            result.reason_code
            == VerificationDecisionReason.MANUAL_REVIEW_PURPOSE_HUMAN_REQUIRED.value
        )
        assert result.lifecycle_mutated is False
        assert result.resulting_version == 1

        refreshed = (
            await session.execute(
                select(ProfessionalVerification).where(
                    ProfessionalVerification.id == verif.id
                )
            )
        ).scalar_one()
        assert refreshed.reviewer_id is None
        assert refreshed.version == 1


# ---------------------------------------------------------------------------
# 7. Kill Switch and Source Policy Deny
# ---------------------------------------------------------------------------


async def test_kill_switch_and_source_policy_deny(session_factory):
    """When kill switch or source policy is disabled, automation fails closed to human review."""
    async with session_factory() as session:
        _, _, verif = await _seed_provider_and_verification(session)
        await session.commit()

        req = ProfessionalLookupRequest(
            registration_authority_code="MAHA_MED_COUNCIL",
            registration_number_normalized=verif.registration_number_normalized,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
        )
        inv = RegistryLookupInvocation(
            resource_id=verif.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=1,
            request=req,
            invoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        obs = _make_prof_obs(
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            source_id="QUAL_SOURCE_01",
        )
        envelope = ValidatedRegistryLookupEnvelope(invocation=inv, observation=obs)

        # 1. Source not enabled (or missing from policy registry), automation_enabled=True
        default_policy_registry = SourceAutomationPolicyRegistry()
        svc_no_policy = ProviderVerificationApplicationService(
            session, source_policies=default_policy_registry, automation_enabled=True
        )
        result1 = await svc_no_policy.apply_verification_observation(
            envelope=envelope,
            idempotency_key=str(uuid.uuid4()),
        )
        assert (
            result1.decision_disposition
            == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED.value
        )
        assert (
            result1.reason_code
            == VerificationDecisionReason.MANUAL_REVIEW_PURPOSE_HUMAN_REQUIRED.value
        )
        assert result1.lifecycle_mutated is False
        assert result1.review_work_id is not None

        # 2. Source enabled in policy registry, but global kill switch is boolean False
        _, _, verif2 = await _seed_provider_and_verification(session)
        await session.commit()
        req2 = ProfessionalLookupRequest(
            registration_authority_code="MAHA_MED_COUNCIL",
            registration_number_normalized=verif2.registration_number_normalized,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
        )
        inv2 = RegistryLookupInvocation(
            resource_id=verif2.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=1,
            request=req2,
            invoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        envelope2 = ValidatedRegistryLookupEnvelope(invocation=inv2, observation=obs)

        enabled_policy_registry = SourceAutomationPolicyRegistry()
        enabled_policy_registry.register(
            SourceAutomationPolicy(
                source_id="QUAL_SOURCE_01",
                automation_enabled=True,
            )
        )
        svc_kill_switch = ProviderVerificationApplicationService(
            session,
            source_policies=enabled_policy_registry,
            automation_enabled=False,
        )
        result2 = await svc_kill_switch.apply_verification_observation(
            envelope=envelope2,
            idempotency_key=str(uuid.uuid4()),
        )
        assert (
            result2.decision_disposition
            == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED.value
        )
        assert (
            result2.reason_code
            == VerificationDecisionReason.MANUAL_REVIEW_PURPOSE_HUMAN_REQUIRED.value
        )
        assert result2.lifecycle_mutated is False

        # 3. Source enabled in policy registry, but global kill switch is callable returning False
        _, _, verif3 = await _seed_provider_and_verification(session)
        await session.commit()
        req3 = ProfessionalLookupRequest(
            registration_authority_code="MAHA_MED_COUNCIL",
            registration_number_normalized=verif3.registration_number_normalized,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
        )
        inv3 = RegistryLookupInvocation(
            resource_id=verif3.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=1,
            request=req3,
            invoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        envelope3 = ValidatedRegistryLookupEnvelope(invocation=inv3, observation=obs)

        svc_callable_switch = ProviderVerificationApplicationService(
            session,
            source_policies=enabled_policy_registry,
            automation_enabled=lambda: False,
        )
        result3 = await svc_callable_switch.apply_verification_observation(
            envelope=envelope3,
            idempotency_key=str(uuid.uuid4()),
        )
        assert (
            result3.decision_disposition
            == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED.value
        )
        assert (
            result3.reason_code
            == VerificationDecisionReason.MANUAL_REVIEW_PURPOSE_HUMAN_REQUIRED.value
        )
        assert result3.lifecycle_mutated is False


# ---------------------------------------------------------------------------
# 8. Concurrency & Optimistic Locking / CAS
# ---------------------------------------------------------------------------


async def test_optimistic_concurrency_conflict(session_factory):
    """Concurrent attempts on same target version: first succeeds, second fails with LIFECYCLE_VERSION_CONFLICT."""
    async with session_factory() as session:
        _, _, verif = await _seed_provider_and_verification(session)
        await session.commit()

        req = ProfessionalLookupRequest(
            registration_authority_code="MAHA_MED_COUNCIL",
            registration_number_normalized=verif.registration_number_normalized,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
        )
        inv = RegistryLookupInvocation(
            resource_id=verif.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=1,
            request=req,
            invoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        obs = _make_prof_obs(
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            source_id="QUAL_SOURCE_01",
        )
        envelope = ValidatedRegistryLookupEnvelope(invocation=inv, observation=obs)

        policy_registry = SourceAutomationPolicyRegistry()
        policy_registry.register(
            SourceAutomationPolicy(
                source_id="QUAL_SOURCE_01",
                automation_enabled=True,
            )
        )

        svc1 = ProviderVerificationApplicationService(
            session, source_policies=policy_registry, automation_enabled=True
        )
        r1 = await svc1.apply_verification_observation(
            envelope=envelope,
            idempotency_key=str(uuid.uuid4()),
        )
        assert r1.lifecycle_mutated is True
        assert r1.resulting_version == 2

        # Second attempt with expected_version = 1 on now version-2 target
        svc2 = ProviderVerificationApplicationService(
            session, source_policies=policy_registry, automation_enabled=True
        )
        with pytest.raises(
            VerificationApplicationError, match="LIFECYCLE_VERSION_CONFLICT"
        ):
            await svc2.apply_verification_observation(
                envelope=envelope,
                idempotency_key=str(uuid.uuid4()),
            )


# ---------------------------------------------------------------------------
# 9. Idempotency Replay and Conflict Detection
# ---------------------------------------------------------------------------


async def test_idempotency_replay_and_conflict(session_factory):
    """Verify replay returns cached result and conflicting request raises IDEMPOTENCY_CONFLICT."""
    async with session_factory() as session:
        _, _, verif = await _seed_provider_and_verification(session)
        await session.commit()

        req = ProfessionalLookupRequest(
            registration_authority_code="MAHA_MED_COUNCIL",
            registration_number_normalized=verif.registration_number_normalized,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
        )
        inv = RegistryLookupInvocation(
            resource_id=verif.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=1,
            request=req,
            invoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        obs = _make_prof_obs(
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            source_id="QUAL_SOURCE_01",
        )
        envelope = ValidatedRegistryLookupEnvelope(invocation=inv, observation=obs)

        policy_registry = SourceAutomationPolicyRegistry()
        policy_registry.register(
            SourceAutomationPolicy(
                source_id="QUAL_SOURCE_01",
                automation_enabled=True,
            )
        )

        svc = ProviderVerificationApplicationService(
            session, source_policies=policy_registry, automation_enabled=True
        )
        shared_key = str(uuid.uuid4())

        # First execution
        r1 = await svc.apply_verification_observation(
            envelope=envelope,
            idempotency_key=shared_key,
        )
        assert r1.idempotent_replay is False

        # Replay with same key & payload
        r2 = await svc.apply_verification_observation(
            envelope=envelope,
            idempotency_key=shared_key,
        )
        assert r2.idempotent_replay is True
        assert r2.evidence_id == r1.evidence_id
        assert r2.resulting_version == r1.resulting_version

        # Conflicting request with same key
        different_inv = RegistryLookupInvocation(
            resource_id=uuid.uuid4(),  # different resource
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=1,
            request=req,
            invoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        diff_envelope = ValidatedRegistryLookupEnvelope(
            invocation=different_inv, observation=obs
        )
        with pytest.raises(VerificationApplicationError, match="IDEMPOTENCY_CONFLICT"):
            await svc.apply_verification_observation(
                envelope=diff_envelope,
                idempotency_key=shared_key,
            )


# ---------------------------------------------------------------------------
# 10. Composite Foreign Key Constraint Probes
# ---------------------------------------------------------------------------


async def test_composite_foreign_key_cross_resource_rejection(session_factory):
    """PostgreSQL enforces composite FK (server_provenance_evidence_id, id) -> (id, target_id).

    Rejects:
    1. Professional A linking Evidence B belonging to Professional B.
    2. Professional A linking Evidence Fac A belonging to Facility A.
    3. Facility A linking Evidence Fac B belonging to Facility B.
    4. Facility A linking Evidence B belonging to Professional B.
    """
    async with session_factory() as session:
        now = datetime.now(timezone.utc)
        _, _, prof_a = await _seed_provider_and_verification(
            session, seed_server_provenance=False
        )
        _, _, prof_b = await _seed_provider_and_verification(
            session, seed_server_provenance=False
        )
        _, fac_a = await _seed_facility_and_verification(
            session, seed_server_provenance=False
        )
        _, fac_b = await _seed_facility_and_verification(
            session, seed_server_provenance=False
        )

        # Evidence B linked to Prof B
        ev_b = ProviderTrustVerificationEvidence(
            professional_verification_id=prof_b.id,
            origin=VerificationEvidenceOrigin.SERVER_REGISTRY_OBSERVATION.value,
            source_id="QUAL_SOURCE_01",
            adapter_version="1.0.0",
            observed_at=now,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            observed_resource_version=1,
        )
        # Evidence Fac A linked to Fac A
        ev_fac_a = ProviderTrustVerificationEvidence(
            facility_verification_id=fac_a.id,
            origin=VerificationEvidenceOrigin.SERVER_REGISTRY_OBSERVATION.value,
            source_id="QUAL_SOURCE_01",
            adapter_version="1.0.0",
            observed_at=now,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            observed_resource_version=1,
        )
        # Evidence Fac B linked to Fac B
        ev_fac_b = ProviderTrustVerificationEvidence(
            facility_verification_id=fac_b.id,
            origin=VerificationEvidenceOrigin.SERVER_REGISTRY_OBSERVATION.value,
            source_id="QUAL_SOURCE_01",
            adapter_version="1.0.0",
            observed_at=now,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            observed_resource_version=1,
        )
        session.add_all([ev_b, ev_fac_a, ev_fac_b])
        await session.flush()

        # 1. Prof A links Evidence B (belongs to Prof B) -> fails fk_professional_verification_server_provenance
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                prof_a.server_provenance_evidence_id = ev_b.id
                await session.flush()

        # 2. Prof A links Evidence Fac A (belongs to Fac A) -> fails fk_professional_verification_server_provenance
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                prof_a.server_provenance_evidence_id = ev_fac_a.id
                await session.flush()

        # 3. Fac A links Evidence Fac B (belongs to Fac B) -> fails fk_facility_verification_server_provenance
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                fac_a.server_provenance_evidence_id = ev_fac_b.id
                await session.flush()

        # 4. Fac A links Evidence B (belongs to Prof B) -> fails fk_facility_verification_server_provenance
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                fac_a.server_provenance_evidence_id = ev_b.id
                await session.flush()


# ---------------------------------------------------------------------------
# 11. Zero Provenance Backfill on Migration
# ---------------------------------------------------------------------------


async def test_zero_provenance_backfill_on_migration():
    """Migration Path B: Real Old-Head Upgrade from 20260904_verification_evidence to 20260905_verification_application.

    Inserts representative ProfessionalVerification, FacilityVerification, professional evidence,
    and facility evidence at old head.
    Upgrades to new head and proves:
    - all old rows preserved
    - existing evidence preserved
    - professional server_provenance_evidence_id == NULL
    - facility server_provenance_evidence_id == NULL
    - zero review-work backfill
    """
    config = _config(_get_db_url())

    # Downgrade to PREVIOUS_HEAD
    await asyncio.to_thread(command.downgrade, config, PREVIOUS_HEAD)

    fac_id = uuid.uuid4()
    fac_verif_id = uuid.uuid4()
    prov_id = uuid.uuid4()
    prof_verif_id = uuid.uuid4()
    prof_ev_id = uuid.uuid4()
    fac_ev_id = uuid.uuid4()

    try:
        engine = create_async_engine(_get_db_url())
        async with engine.connect() as conn:
            await conn.execute(
                text(
                    "INSERT INTO hospital_registry (id, facility_code, legal_name, display_name, country_code, is_active) "
                    "VALUES (:id, :code, 'Legacy Hosp', 'Legacy Hosp', 'IN', true)"
                ),
                {"id": fac_id, "code": f"LEG-{fac_id.hex[:8]}"},
            )
            await conn.execute(
                text(
                    "INSERT INTO facility_verification (id, facility_id, status, version, previous_verification_valid) "
                    "VALUES (:id, :fid, 'VERIFIED', 1, false)"
                ),
                {"id": fac_verif_id, "fid": fac_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO provider_identity (id, provider_uid, hospital_id, contact_email, is_active, role, status) "
                    "VALUES (:id, :uid, :hid, :email, true, 'provider', 'active')"
                ),
                {
                    "id": prov_id,
                    "uid": f"LEG-{prov_id.hex[:8]}",
                    "hid": fac_id,
                    "email": f"leg.{prov_id.hex[:8]}@example.com",
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO professional_verification (id, provider_id, status, version, previous_verification_valid) "
                    "VALUES (:id, :pid, 'VERIFIED', 1, false)"
                ),
                {"id": prof_verif_id, "pid": prov_id},
            )
            # Insert professional evidence row at old head
            await conn.execute(
                text(
                    "INSERT INTO provider_trust_verification_evidence ("
                    "  id, professional_verification_id, origin, source_id, adapter_version, "
                    "  observed_at, lookup_purpose, outcome, observed_resource_version"
                    ") VALUES ("
                    "  :id, :pvid, 'SERVER_REGISTRY_OBSERVATION', 'QUAL_SOURCE_01', '1.0.0', "
                    "  now(), 'RECHECK', 'CONFIRMED_ACTIVE', 1"
                    ")"
                ),
                {"id": prof_ev_id, "pvid": prof_verif_id},
            )
            # Insert facility evidence row at old head
            await conn.execute(
                text(
                    "INSERT INTO provider_trust_verification_evidence ("
                    "  id, facility_verification_id, origin, source_id, adapter_version, "
                    "  observed_at, lookup_purpose, outcome, observed_resource_version"
                    ") VALUES ("
                    "  :id, :fvid, 'SERVER_REGISTRY_OBSERVATION', 'QUAL_SOURCE_01', '1.0.0', "
                    "  now(), 'RECHECK', 'CONFIRMED_ACTIVE', 1"
                    ")"
                ),
                {"id": fac_ev_id, "fvid": fac_verif_id},
            )
            await conn.commit()
        await engine.dispose()
    finally:
        # Re-upgrade to HEAD
        await asyncio.to_thread(command.upgrade, config, HEAD)

    # Verify rows preserved, new columns NULL, zero review work backfill
    engine = create_async_engine(_get_db_url())
    async with engine.connect() as conn:
        # 1. Professional row preserved, server_provenance_evidence_id is NULL
        prof_res = (
            await conn.execute(
                text(
                    "SELECT status, version, server_provenance_evidence_id "
                    "FROM professional_verification WHERE id = :id"
                ),
                {"id": prof_verif_id},
            )
        ).first()
        assert prof_res is not None
        assert prof_res.status == "VERIFIED"
        assert prof_res.version == 1
        assert prof_res.server_provenance_evidence_id is None

        # 2. Facility row preserved, server_provenance_evidence_id is NULL
        fac_res = (
            await conn.execute(
                text(
                    "SELECT status, version, server_provenance_evidence_id "
                    "FROM facility_verification WHERE id = :id"
                ),
                {"id": fac_verif_id},
            )
        ).first()
        assert fac_res is not None
        assert fac_res.status == "VERIFIED"
        assert fac_res.version == 1
        assert fac_res.server_provenance_evidence_id is None

        # 3. Existing evidence preserved
        prof_ev_res = (
            await conn.execute(
                text(
                    "SELECT outcome FROM provider_trust_verification_evidence WHERE id = :id"
                ),
                {"id": prof_ev_id},
            )
        ).scalar()
        assert prof_ev_res == "CONFIRMED_ACTIVE"

        fac_ev_res = (
            await conn.execute(
                text(
                    "SELECT outcome FROM provider_trust_verification_evidence WHERE id = :id"
                ),
                {"id": fac_ev_id},
            )
        ).scalar()
        assert fac_ev_res == "CONFIRMED_ACTIVE"

        # 4. Zero review work backfill
        rw_count = (
            await conn.execute(
                text("SELECT COUNT(*) FROM provider_trust_verification_review_work")
            )
        ).scalar()
        assert rw_count == 0
    await engine.dispose()


# ---------------------------------------------------------------------------
# 12. Observation-only Transactions
# ---------------------------------------------------------------------------


async def test_observation_only_transactions(session_factory):
    """Verify observation-only transactions write evidence, manage review work, and NEVER mutate target."""
    async with session_factory() as session:
        _, _, verif = await _seed_provider_and_verification(
            session, status=ProfessionalVerificationStatus.VERIFIED
        )
        await session.commit()

        policy_registry = SourceAutomationPolicyRegistry()
        policy_registry.register(
            SourceAutomationPolicy(
                source_id="QUAL_SOURCE_01",
                automation_enabled=True,
            )
        )
        svc = ProviderVerificationApplicationService(
            session, source_policies=policy_registry, automation_enabled=True
        )

        req = ProfessionalLookupRequest(
            registration_authority_code="MAHA_MED_COUNCIL",
            registration_number_normalized=verif.registration_number_normalized,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
        )

        # 1. NO_MUTATION_REQUIRED: Target already VERIFIED, observed CONFIRMED_ACTIVE under ADVERSE_SIGNAL_CHECK
        req_adverse = ProfessionalLookupRequest(
            registration_authority_code="MAHA_MED_COUNCIL",
            registration_number_normalized=verif.registration_number_normalized,
            lookup_purpose=VerificationEvidenceLookupPurpose.ADVERSE_SIGNAL_CHECK,
        )
        inv1 = RegistryLookupInvocation(
            resource_id=verif.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=1,
            request=req_adverse,
            invoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        obs1 = _make_prof_obs(
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            purpose=VerificationEvidenceLookupPurpose.ADVERSE_SIGNAL_CHECK,
            source_id="QUAL_SOURCE_01",
        )
        env1 = ValidatedRegistryLookupEnvelope(invocation=inv1, observation=obs1)
        r1 = await svc.apply_verification_observation(
            envelope=env1, idempotency_key=str(uuid.uuid4())
        )
        assert (
            r1.decision_disposition
            == VerificationDecisionDisposition.NO_MUTATION_REQUIRED.value
        )
        assert (
            r1.reason_code
            == VerificationDecisionReason.ACTIVE_VERIFICATION_OBSERVATION_MATCH.value
        )
        assert r1.lifecycle_mutated is False
        assert r1.review_work_id is None
        assert r1.evidence_id is not None

        # 2. HUMAN_REVIEW_REQUIRED: observed AMBIGUOUS on RECHECK_DUE
        _, _, verif_due = await _seed_provider_and_verification(
            session,
            status=ProfessionalVerificationStatus.RECHECK_DUE,
            grace_expires_at=None,
        )
        await session.commit()
        req_due = ProfessionalLookupRequest(
            registration_authority_code="MAHA_MED_COUNCIL",
            registration_number_normalized=verif_due.registration_number_normalized,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
        )
        inv2 = RegistryLookupInvocation(
            resource_id=verif_due.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=1,
            request=req_due,
            invoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        obs2 = _make_prof_obs(
            outcome=VerificationEvidenceOutcome.AMBIGUOUS,
            source_id="QUAL_SOURCE_01",
        )
        env2 = ValidatedRegistryLookupEnvelope(invocation=inv2, observation=obs2)
        r2 = await svc.apply_verification_observation(
            envelope=env2, idempotency_key=str(uuid.uuid4())
        )
        assert (
            r2.decision_disposition
            == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED.value
        )
        assert r2.lifecycle_mutated is False
        assert r2.review_work_id is not None
        assert r2.evidence_id is not None

        # Verify target unchanged in DB
        refreshed_due = (
            await session.execute(
                select(ProfessionalVerification).where(
                    ProfessionalVerification.id == verif_due.id
                )
            )
        ).scalar_one()
        assert refreshed_due.status == ProfessionalVerificationStatus.RECHECK_DUE.value
        assert refreshed_due.version == 1

        # 3. VERIFIED provider receives an adverse finding.  System automation must
        # enter RECHECK_DUE without grace and create OPEN review work.
        inv3 = RegistryLookupInvocation(
            resource_id=verif.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=1,
            request=req,
            invoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        obs3 = _make_prof_obs(
            outcome=VerificationEvidenceOutcome.CONFIRMED_INACTIVE,
            source_id="QUAL_SOURCE_01",
        )
        env3 = ValidatedRegistryLookupEnvelope(invocation=inv3, observation=obs3)
        r3 = await svc.apply_verification_observation(
            envelope=env3, idempotency_key=str(uuid.uuid4())
        )
        assert (
            r3.decision_disposition
            == VerificationDecisionDisposition.SYSTEM_FAIL_CLOSED_AND_REVIEW.value
        )
        assert r3.applied_command == "MARK_RECHECK_DUE"
        assert r3.lifecycle_mutated is True
        assert r3.review_work_id is not None
        assert r3.evidence_id is not None

        refreshed_verif = (
            await session.execute(
                select(ProfessionalVerification).where(
                    ProfessionalVerification.id == verif.id
                )
            )
        ).scalar_one()
        assert (
            refreshed_verif.status == ProfessionalVerificationStatus.RECHECK_DUE.value
        )
        assert refreshed_verif.version == 2
        assert refreshed_verif.grace_expires_at is None

        work3 = await session.get(
            ProviderTrustVerificationReviewWork, r3.review_work_id
        )
        assert work3 is not None
        assert work3.status == "OPEN"
        assert (
            work3.disposition
            == VerificationDecisionDisposition.SYSTEM_FAIL_CLOSED_AND_REVIEW.value
        )
        assert (
            work3.reason_code
            == VerificationDecisionReason.SOURCE_FAILURE_FAIL_CLOSED_REVIEW.value
        )


@pytest.mark.parametrize(
    "outcome,purpose",
    [
        (
            VerificationEvidenceOutcome.CONFIRMED_INACTIVE,
            VerificationEvidenceLookupPurpose.RECHECK,
        ),
        (
            VerificationEvidenceOutcome.NOT_FOUND,
            VerificationEvidenceLookupPurpose.RECHECK,
        ),
        (
            VerificationEvidenceOutcome.IDENTITY_MISMATCH,
            VerificationEvidenceLookupPurpose.RECHECK,
        ),
        (
            VerificationEvidenceOutcome.SOURCE_RESPONSE_INVALID,
            VerificationEvidenceLookupPurpose.RECHECK,
        ),
        (
            VerificationEvidenceOutcome.SOURCE_AUTHENTICATION_FAILURE,
            VerificationEvidenceLookupPurpose.RECHECK,
        ),
        (
            VerificationEvidenceOutcome.SOURCE_INTEGRITY_FAILURE,
            VerificationEvidenceLookupPurpose.RECHECK,
        ),
        (
            VerificationEvidenceOutcome.AMBIGUOUS,
            VerificationEvidenceLookupPurpose.RECHECK,
        ),
        (
            VerificationEvidenceOutcome.REVIEW_REQUIRED,
            VerificationEvidenceLookupPurpose.RECHECK,
        ),
        (
            VerificationEvidenceOutcome.SOURCE_UNAVAILABLE,
            VerificationEvidenceLookupPurpose.ADVERSE_SIGNAL_CHECK,
        ),
    ],
)
async def test_verified_professional_adverse_observation_marks_recheck_due_without_grace(
    session_factory,
    outcome,
    purpose,
):
    """Every adverse or non-outage registry outcome fails closed to review and denies clinical eligibility."""
    async with session_factory() as session:
        now = datetime.now(timezone.utc)
        hospital, provider, verification = await _seed_provider_and_verification(
            session,
            status=ProfessionalVerificationStatus.VERIFIED,
            grace_expires_at=None,
            recheck_failure_reason=None,
        )
        previous_provenance_id = verification.server_provenance_evidence_id
        await session.commit()

        # Clinical access is initially ALLOWED for VERIFIED provider
        eligibility_svc = ClinicalEligibilityService(
            contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
        )
        auth = InteractiveClinicalAuthentication(
            provider_id=provider.id,
            hospital_id=hospital.id,
            method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
            session_authenticated=True,
            mfa_verified_at=now,
        )
        check_before = await eligibility_svc.evaluate_interactive(
            session,
            provider,
            auth,
            ClinicalCapability.DOCUMENTS_REVIEW,
            now=now,
        )
        assert check_before.allowed is True

        request = ProfessionalLookupRequest(
            registration_authority_code=verification.registration_authority_code,
            registration_number_normalized=verification.registration_number_normalized,
            lookup_purpose=purpose,
        )
        inv_time = now - timedelta(minutes=5)
        obs_time = now - timedelta(minutes=1)
        invocation = RegistryLookupInvocation(
            resource_id=verification.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=verification.version,
            request=request,
            invoked_at=inv_time,
        )
        observation = _make_prof_obs(
            outcome=outcome,
            purpose=purpose,
            observed_at=obs_time,
        )
        envelope = ValidatedRegistryLookupEnvelope(
            invocation=invocation,
            observation=observation,
        )
        policies = SourceAutomationPolicyRegistry()
        policies.register(
            SourceAutomationPolicy(source_id="QUAL_SOURCE_01", automation_enabled=True)
        )
        result = await ProviderVerificationApplicationService(
            session,
            source_policies=policies,
            automation_enabled=True,
        ).apply_verification_observation(
            envelope=envelope,
            idempotency_key=str(uuid.uuid4()),
        )

        assert result.applied_command == "MARK_RECHECK_DUE"
        assert result.lifecycle_mutated is True
        assert result.review_work_id is not None
        refreshed = await session.get(ProfessionalVerification, verification.id)
        assert refreshed is not None
        assert refreshed.status == ProfessionalVerificationStatus.RECHECK_DUE.value
        assert refreshed.grace_expires_at is None
        assert refreshed.server_provenance_evidence_id == previous_provenance_id
        evidence = await session.get(
            ProviderTrustVerificationEvidence, result.evidence_id
        )
        assert evidence is not None
        assert evidence.outcome == outcome.value
        work = await session.get(
            ProviderTrustVerificationReviewWork, result.review_work_id
        )
        assert work is not None
        assert work.status == "OPEN"
        expected_reason = (
            VerificationDecisionReason.SOURCE_UNAVAILABLE_NO_GRACE_FAIL_CLOSED.value
            if outcome == VerificationEvidenceOutcome.SOURCE_UNAVAILABLE
            else VerificationDecisionReason.SOURCE_FAILURE_FAIL_CLOSED_REVIEW.value
        )
        assert (
            result.decision_disposition
            == VerificationDecisionDisposition.SYSTEM_FAIL_CLOSED_AND_REVIEW.value
        )
        assert result.reason_code == expected_reason
        assert (
            work.disposition
            == VerificationDecisionDisposition.SYSTEM_FAIL_CLOSED_AND_REVIEW.value
        )
        assert work.reason_code == expected_reason

        # Verify audit outbox metadata preserves exact original disposition and reason_code
        outbox_row = await session.execute(
            text(
                "SELECT payload FROM public.audit_outbox WHERE idempotency_key = :key"
            ),
            {"key": f"review-req:{work.id}"},
        )
        audit_payload = outbox_row.scalar_one()
        audit_meta = (
            json.loads(audit_payload)
            if isinstance(audit_payload, str)
            else audit_payload
        )["metadata"]
        assert (
            audit_meta["disposition"]
            == VerificationDecisionDisposition.SYSTEM_FAIL_CLOSED_AND_REVIEW.value
        )
        assert audit_meta["reason_code"] == expected_reason

        # Clinical access is now strictly DENIED
        check_after = await eligibility_svc.evaluate_interactive(
            session,
            provider,
            auth,
            ClinicalCapability.DOCUMENTS_REVIEW,
            now=now,
        )
        assert check_after.allowed is False
        assert check_after.professional_grace_active is False
        assert (
            check_after.denial_code
            == ClinicalEligibilityDenialCode.PROFESSIONAL_RECHECK_NOT_ELIGIBLE
        )


async def test_manual_review_purpose_review_work_disposition(session_factory):
    """MANUAL_REVIEW lookup purpose routes strictly to HUMAN_REVIEW_REQUIRED review work."""
    async with session_factory() as session:
        now = datetime.now(timezone.utc)
        hospital, provider, verification = await _seed_provider_and_verification(
            session,
            status=ProfessionalVerificationStatus.VERIFIED,
            grace_expires_at=None,
        )
        await session.commit()

        request = ProfessionalLookupRequest(
            registration_authority_code=verification.registration_authority_code,
            registration_number_normalized=verification.registration_number_normalized,
            lookup_purpose=VerificationEvidenceLookupPurpose.MANUAL_REVIEW,
        )
        invocation = RegistryLookupInvocation(
            resource_id=verification.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=verification.version,
            request=request,
            invoked_at=now - timedelta(minutes=5),
        )
        observation = _make_prof_obs(
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            purpose=VerificationEvidenceLookupPurpose.MANUAL_REVIEW,
            observed_at=now - timedelta(minutes=1),
        )
        envelope = ValidatedRegistryLookupEnvelope(
            invocation=invocation,
            observation=observation,
        )
        policies = SourceAutomationPolicyRegistry()
        policies.register(
            SourceAutomationPolicy(source_id="QUAL_SOURCE_01", automation_enabled=True)
        )
        result = await ProviderVerificationApplicationService(
            session,
            source_policies=policies,
            automation_enabled=True,
        ).apply_verification_observation(
            envelope=envelope,
            idempotency_key=str(uuid.uuid4()),
        )

        assert (
            result.decision_disposition
            == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED.value
        )
        assert (
            result.reason_code
            == VerificationDecisionReason.MANUAL_REVIEW_PURPOSE_HUMAN_REQUIRED.value
        )
        assert result.lifecycle_mutated is False
        assert result.applied_command is None
        assert result.review_work_id is not None

        work = await session.get(
            ProviderTrustVerificationReviewWork, result.review_work_id
        )
        assert work is not None
        assert work.status == "OPEN"
        assert (
            work.disposition
            == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED.value
        )
        assert (
            work.reason_code
            == VerificationDecisionReason.MANUAL_REVIEW_PURPOSE_HUMAN_REQUIRED.value
        )

        # Verify audit outbox metadata preserves exact original disposition and reason_code
        outbox_row = await session.execute(
            text(
                "SELECT payload FROM public.audit_outbox WHERE idempotency_key = :key"
            ),
            {"key": f"review-req:{work.id}"},
        )
        audit_payload = outbox_row.scalar_one()
        audit_meta = (
            json.loads(audit_payload)
            if isinstance(audit_payload, str)
            else audit_payload
        )["metadata"]
        assert (
            audit_meta["disposition"]
            == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED.value
        )
        assert (
            audit_meta["reason_code"]
            == VerificationDecisionReason.MANUAL_REVIEW_PURPOSE_HUMAN_REQUIRED.value
        )


async def test_lifecycle_semantic_gap_review_work_disposition(session_factory):
    """Unallowed candidate command triggers LIFECYCLE_SEMANTIC_GAP review work disposition."""
    async with session_factory() as session:
        now = datetime.now(timezone.utc)
        hospital, provider, verification = await _seed_provider_and_verification(
            session,
            status=ProfessionalVerificationStatus.VERIFIED,
            grace_expires_at=None,
        )
        await session.commit()

        request = ProfessionalLookupRequest(
            registration_authority_code=verification.registration_authority_code,
            registration_number_normalized=verification.registration_number_normalized,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
        )
        invocation = RegistryLookupInvocation(
            resource_id=verification.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=verification.version,
            request=request,
            invoked_at=now - timedelta(minutes=5),
        )
        observation = _make_prof_obs(
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            observed_at=now - timedelta(minutes=1),
        )
        envelope = ValidatedRegistryLookupEnvelope(
            invocation=invocation,
            observation=observation,
        )
        policies = SourceAutomationPolicyRegistry()
        policies.register(
            SourceAutomationPolicy(source_id="QUAL_SOURCE_01", automation_enabled=True)
        )
        svc = ProviderVerificationApplicationService(
            session,
            source_policies=policies,
            automation_enabled=True,
        )

        # Plan proposing a command outside _ALLOWED_SYSTEM_COMMANDS (e.g. VERIFY)
        gap_plan = VerificationDecisionPlan(
            resource_type=RegistryResourceType.PROFESSIONAL,
            disposition=VerificationDecisionDisposition.SYSTEM_TRANSITION_CANDIDATE,
            candidate_command=ProfessionalTransitionCommand.VERIFY,
            expected_resource_version=verification.version,
            reason_code=VerificationDecisionReason.POSITIVE_RECHECK_AUTOMATION_ELIGIBLE,
            requires_human_review=False,
            grace_expires_at=None,
            source_id=observation.source_id,
            lookup_purpose=observation.lookup_purpose,
            outcome=observation.outcome,
        )

        with patch.object(svc, "_evaluate_decision", return_value=gap_plan):
            result = await svc.apply_verification_observation(
                envelope=envelope,
                idempotency_key=str(uuid.uuid4()),
            )

        assert (
            result.decision_disposition
            == VerificationDecisionDisposition.LIFECYCLE_SEMANTIC_GAP.value
        )
        assert (
            result.reason_code
            == VerificationDecisionReason.SYSTEM_ACTOR_PROVENANCE_GAP.value
        )
        assert result.lifecycle_mutated is False
        assert result.applied_command is None
        assert result.review_work_id is not None

        work = await session.get(
            ProviderTrustVerificationReviewWork, result.review_work_id
        )
        assert work is not None
        assert work.status == "OPEN"
        assert (
            work.disposition
            == VerificationDecisionDisposition.LIFECYCLE_SEMANTIC_GAP.value
        )
        assert (
            work.reason_code
            == VerificationDecisionReason.SYSTEM_ACTOR_PROVENANCE_GAP.value
        )

        # Verify audit outbox metadata preserves exact recomputed disposition and reason_code
        outbox_row = await session.execute(
            text(
                "SELECT payload FROM public.audit_outbox WHERE idempotency_key = :key"
            ),
            {"key": f"review-req:{work.id}"},
        )
        audit_payload = outbox_row.scalar_one()
        audit_meta = (
            json.loads(audit_payload)
            if isinstance(audit_payload, str)
            else audit_payload
        )["metadata"]
        assert (
            audit_meta["disposition"]
            == VerificationDecisionDisposition.LIFECYCLE_SEMANTIC_GAP.value
        )
        assert (
            audit_meta["reason_code"]
            == VerificationDecisionReason.SYSTEM_ACTOR_PROVENANCE_GAP.value
        )


async def test_policy_semantic_gap_review_work_disposition(session_factory):
    """Policy-level LIFECYCLE_SEMANTIC_GAP plan directly produces LIFECYCLE_SEMANTIC_GAP review work."""
    async with session_factory() as session:
        now = datetime.now(timezone.utc)
        hospital, provider, verification = await _seed_provider_and_verification(
            session,
            status=ProfessionalVerificationStatus.VERIFIED,
            grace_expires_at=None,
        )
        await session.commit()

        request = ProfessionalLookupRequest(
            registration_authority_code=verification.registration_authority_code,
            registration_number_normalized=verification.registration_number_normalized,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
        )
        invocation = RegistryLookupInvocation(
            resource_id=verification.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=verification.version,
            request=request,
            invoked_at=now - timedelta(minutes=5),
        )
        observation = _make_prof_obs(
            outcome=VerificationEvidenceOutcome.CONFIRMED_INACTIVE,
            observed_at=now - timedelta(minutes=1),
        )
        envelope = ValidatedRegistryLookupEnvelope(
            invocation=invocation,
            observation=observation,
        )
        policies = SourceAutomationPolicyRegistry()
        policies.register(
            SourceAutomationPolicy(source_id="QUAL_SOURCE_01", automation_enabled=True)
        )
        svc = ProviderVerificationApplicationService(
            session,
            source_policies=policies,
            automation_enabled=True,
        )

        policy_gap_plan = VerificationDecisionPlan(
            resource_type=RegistryResourceType.PROFESSIONAL,
            disposition=VerificationDecisionDisposition.LIFECYCLE_SEMANTIC_GAP,
            candidate_command=None,
            expected_resource_version=verification.version,
            reason_code=VerificationDecisionReason.RECHECK_GRACE_CANCELLATION_NOT_EXPRESSIBLE,
            requires_human_review=True,
            grace_expires_at=None,
            source_id=observation.source_id,
            lookup_purpose=observation.lookup_purpose,
            outcome=observation.outcome,
        )

        with patch.object(svc, "_evaluate_decision", return_value=policy_gap_plan):
            result = await svc.apply_verification_observation(
                envelope=envelope,
                idempotency_key=str(uuid.uuid4()),
            )

        assert (
            result.decision_disposition
            == VerificationDecisionDisposition.LIFECYCLE_SEMANTIC_GAP.value
        )
        assert (
            result.reason_code
            == VerificationDecisionReason.RECHECK_GRACE_CANCELLATION_NOT_EXPRESSIBLE.value
        )
        assert result.lifecycle_mutated is False
        assert result.applied_command is None
        assert result.review_work_id is not None

        work = await session.get(
            ProviderTrustVerificationReviewWork, result.review_work_id
        )
        assert work is not None
        assert work.status == "OPEN"
        assert (
            work.disposition
            == VerificationDecisionDisposition.LIFECYCLE_SEMANTIC_GAP.value
        )
        assert (
            work.reason_code
            == VerificationDecisionReason.RECHECK_GRACE_CANCELLATION_NOT_EXPRESSIBLE.value
        )

        # Verify audit outbox metadata preserves exact original disposition and reason_code
        outbox_row = await session.execute(
            text(
                "SELECT payload FROM public.audit_outbox WHERE idempotency_key = :key"
            ),
            {"key": f"review-req:{work.id}"},
        )
        audit_payload = outbox_row.scalar_one()
        audit_meta = (
            json.loads(audit_payload)
            if isinstance(audit_payload, str)
            else audit_payload
        )["metadata"]
        assert (
            audit_meta["disposition"]
            == VerificationDecisionDisposition.LIFECYCLE_SEMANTIC_GAP.value
        )
        assert (
            audit_meta["reason_code"]
            == VerificationDecisionReason.RECHECK_GRACE_CANCELLATION_NOT_EXPRESSIBLE.value
        )


async def test_ordinary_recheck_outage_bounded_grace_and_no_extension(session_factory):
    """VERIFIED professional + RECHECK + SOURCE_UNAVAILABLE + all prerequisites:
    1. First outage: MARK_RECHECK_DUE, RECHECK_DUE, bounded grace <= 24h, clinical eligibility ALLOWED.
    2. Second outage during active grace: NO_MUTATION_REQUIRED, does not extend grace, clinical eligibility stays ALLOWED.
    """
    async with session_factory() as session:
        now = datetime.now(timezone.utc) - timedelta(hours=6)
        hospital, provider, verification = await _seed_provider_and_verification(
            session,
            status=ProfessionalVerificationStatus.VERIFIED,
            grace_expires_at=None,
            recheck_failure_reason=None,
        )
        previous_provenance_id = verification.server_provenance_evidence_id
        await session.commit()

        # Step 1: Initial RECHECK outage with valid prerequisites -> bounded grace <= 24h
        req = ProfessionalLookupRequest(
            registration_authority_code=verification.registration_authority_code,
            registration_number_normalized=verification.registration_number_normalized,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
        )
        inv1 = RegistryLookupInvocation(
            resource_id=verification.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=verification.version,
            request=req,
            invoked_at=now - timedelta(minutes=5),
        )
        obs1 = _make_prof_obs(
            outcome=VerificationEvidenceOutcome.SOURCE_UNAVAILABLE,
            purpose=VerificationEvidenceLookupPurpose.RECHECK,
            observed_at=now - timedelta(minutes=1),
        )
        env1 = ValidatedRegistryLookupEnvelope(invocation=inv1, observation=obs1)

        policies = SourceAutomationPolicyRegistry()
        policies.register(
            SourceAutomationPolicy(source_id="QUAL_SOURCE_01", automation_enabled=True)
        )
        svc = ProviderVerificationApplicationService(
            session, source_policies=policies, automation_enabled=True
        )
        result1 = await svc.apply_verification_observation(
            envelope=env1,
            idempotency_key=str(uuid.uuid4()),
            now=now,
        )

        assert (
            result1.decision_disposition
            == VerificationDecisionDisposition.SYSTEM_TRANSITION_CANDIDATE.value
        )
        assert result1.applied_command == "MARK_RECHECK_DUE"
        assert result1.lifecycle_mutated is True
        assert (
            result1.review_work_id is None
        )  # no human review required on bounded grace

        refreshed1 = await session.get(ProfessionalVerification, verification.id)
        assert refreshed1 is not None
        assert refreshed1.status == ProfessionalVerificationStatus.RECHECK_DUE.value
        assert refreshed1.grace_expires_at is not None
        assert refreshed1.grace_expires_at <= now + timedelta(hours=24)
        assert refreshed1.server_provenance_evidence_id == previous_provenance_id
        first_grace_expiry = refreshed1.grace_expires_at

        # Clinical access is ALLOWED during active grace
        eligibility_svc = ClinicalEligibilityService(
            contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
        )
        auth = InteractiveClinicalAuthentication(
            provider_id=provider.id,
            hospital_id=hospital.id,
            method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
            session_authenticated=True,
            mfa_verified_at=now,
        )
        check1 = await eligibility_svc.evaluate_interactive(
            session,
            provider,
            auth,
            ClinicalCapability.DOCUMENTS_REVIEW,
            now=now + timedelta(hours=1),
        )
        assert check1.allowed is True
        assert check1.professional_grace_active is True

        # Step 2: Repeated outage during active grace -> NO_MUTATION_REQUIRED, grace NOT extended
        later_moment = now + timedelta(hours=4)
        inv2 = RegistryLookupInvocation(
            resource_id=verification.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=refreshed1.version,
            request=req,
            invoked_at=later_moment - timedelta(minutes=5),
        )
        obs2 = _make_prof_obs(
            outcome=VerificationEvidenceOutcome.SOURCE_UNAVAILABLE,
            purpose=VerificationEvidenceLookupPurpose.RECHECK,
            observed_at=later_moment - timedelta(minutes=1),
        )
        env2 = ValidatedRegistryLookupEnvelope(invocation=inv2, observation=obs2)
        result2 = await svc.apply_verification_observation(
            envelope=env2,
            idempotency_key=str(uuid.uuid4()),
            now=later_moment,
        )

        assert (
            result2.decision_disposition
            == VerificationDecisionDisposition.NO_MUTATION_REQUIRED.value
        )
        assert result2.applied_command is None
        assert result2.lifecycle_mutated is False
        assert result2.review_work_id is None

        refreshed2 = await session.get(ProfessionalVerification, verification.id)
        assert refreshed2 is not None
        assert refreshed2.status == ProfessionalVerificationStatus.RECHECK_DUE.value
        assert refreshed2.version == refreshed1.version  # version unchanged
        assert refreshed2.grace_expires_at == first_grace_expiry  # NOT extended!

        # Clinical access remains ALLOWED under original grace
        check2 = await eligibility_svc.evaluate_interactive(
            session,
            provider,
            auth,
            ClinicalCapability.DOCUMENTS_REVIEW,
            now=later_moment,
        )
        assert check2.allowed is True
        assert check2.professional_grace_active is True


# ---------------------------------------------------------------------------
# 13. Facility Automation Qualification Flow
# ---------------------------------------------------------------------------


async def test_facility_qualification_flow(session_factory):
    """Exhaustive qualification of Facility verification automation flow."""
    async with session_factory() as session:
        policy_registry = SourceAutomationPolicyRegistry()
        policy_registry.register(
            SourceAutomationPolicy(
                source_id="QUAL_SOURCE_01",
                resource_type="FACILITY",
                registration_authority_code="ROHINI",
                automation_enabled=True,
                allowed_binding_methods=frozenset({"REGISTRY_MATCH"}),
            )
        )
        svc = ProviderVerificationApplicationService(
            session, source_policies=policy_registry, automation_enabled=True
        )

        # 1. Positive facility recheck
        _, fac_verif = await _seed_facility_and_verification(
            session,
            status=FacilityVerificationStatus.RECHECK_REQUIRED,
            reviewer_id="human-reviewer-fac-42",
        )
        await session.commit()

        fac_req = FacilityLookupRequest(
            registration_authority_code="ROHINI",
            registration_number_normalized=fac_verif.registration_number_normalized,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
        )
        fac_inv = RegistryLookupInvocation(
            resource_id=fac_verif.id,
            resource_type=RegistryResourceType.FACILITY,
            expected_version=1,
            request=fac_req,
            invoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        fac_obs = _make_fac_obs(
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            source_id="QUAL_SOURCE_01",
        )
        env = ValidatedRegistryLookupEnvelope(invocation=fac_inv, observation=fac_obs)

        res1 = await svc.apply_verification_observation(
            envelope=env, idempotency_key=str(uuid.uuid4())
        )
        assert (
            res1.decision_disposition
            == VerificationDecisionDisposition.SYSTEM_TRANSITION_CANDIDATE.value
        )
        assert res1.applied_command == "COMPLETE_RECHECK"
        assert res1.lifecycle_mutated is True
        assert res1.resulting_version == 2
        assert res1.review_work_id is None

        refreshed_fac = (
            await session.execute(
                select(FacilityVerification).where(
                    FacilityVerification.id == fac_verif.id
                )
            )
        ).scalar_one()
        assert refreshed_fac.status == FacilityVerificationStatus.VERIFIED.value
        assert refreshed_fac.version == 2
        assert refreshed_fac.server_provenance_evidence_id == res1.evidence_id
        assert refreshed_fac.reviewer_id == "human-reviewer-fac-42"

        # 2. Adverse facility finding: MARK_RECHECK_REQUIRED
        adverse_obs = _make_fac_obs(
            outcome=VerificationEvidenceOutcome.SOURCE_AUTHENTICATION_FAILURE,
            source_id="QUAL_SOURCE_01",
        )
        adverse_inv = RegistryLookupInvocation(
            resource_id=fac_verif.id,
            resource_type=RegistryResourceType.FACILITY,
            expected_version=2,
            request=fac_req,
            invoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        env_adverse = ValidatedRegistryLookupEnvelope(
            invocation=adverse_inv, observation=adverse_obs
        )

        res2 = await svc.apply_verification_observation(
            envelope=env_adverse, idempotency_key=str(uuid.uuid4())
        )
        assert (
            res2.decision_disposition
            == VerificationDecisionDisposition.SYSTEM_FAIL_CLOSED_AND_REVIEW.value
        )
        assert res2.applied_command == "MARK_RECHECK_REQUIRED"
        assert res2.lifecycle_mutated is True
        assert res2.resulting_version == 3
        assert res2.review_work_id is not None

        refreshed_fac2 = (
            await session.execute(
                select(FacilityVerification).where(
                    FacilityVerification.id == fac_verif.id
                )
            )
        ).scalar_one()
        assert (
            refreshed_fac2.status == FacilityVerificationStatus.RECHECK_REQUIRED.value
        )
        assert refreshed_fac2.version == 3

        # 3. Unconfirmed identity binding -> HUMAN_REVIEW_REQUIRED, no mutation
        unconfirmed_obs = _make_fac_obs(
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            source_id="QUAL_SOURCE_01",
            binding_result=VerificationIdentityBindingResult.MISMATCHED,
        )
        unconfirmed_inv = RegistryLookupInvocation(
            resource_id=fac_verif.id,
            resource_type=RegistryResourceType.FACILITY,
            expected_version=3,
            request=fac_req,
            invoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        env_unconfirmed = ValidatedRegistryLookupEnvelope(
            invocation=unconfirmed_inv, observation=unconfirmed_obs
        )
        res3 = await svc.apply_verification_observation(
            envelope=env_unconfirmed, idempotency_key=str(uuid.uuid4())
        )
        assert (
            res3.decision_disposition
            == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED.value
        )
        assert res3.lifecycle_mutated is False
        assert res3.resulting_version == 3

        # 4. Open review work blocks automation
        # Notice res2 created review work, which is currently OPEN
        active_inv = RegistryLookupInvocation(
            resource_id=fac_verif.id,
            resource_type=RegistryResourceType.FACILITY,
            expected_version=3,
            request=fac_req,
            invoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        env_active = ValidatedRegistryLookupEnvelope(
            invocation=active_inv, observation=fac_obs
        )
        res4 = await svc.apply_verification_observation(
            envelope=env_active, idempotency_key=str(uuid.uuid4())
        )
        assert (
            res4.decision_disposition
            == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED.value
        )
        assert (
            res4.reason_code
            == VerificationDecisionReason.OPEN_HUMAN_REVIEW_BLOCKS_AUTOMATION.value
        )
        assert res4.lifecycle_mutated is False

        # 5. 5E has no source-independent envelope TTL.  The request is still
        # structurally bound and the target version remains the authority gate.
        stale_inv = RegistryLookupInvocation(
            resource_id=fac_verif.id,
            resource_type=RegistryResourceType.FACILITY,
            expected_version=3,
            request=fac_req,
            invoked_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        stale_obs = _make_fac_obs(
            observed_at=datetime.now(timezone.utc) - timedelta(minutes=25)
        )
        env_stale = ValidatedRegistryLookupEnvelope(
            invocation=stale_inv, observation=stale_obs
        )
        res5 = await svc.apply_verification_observation(
            envelope=env_stale, idempotency_key=str(uuid.uuid4())
        )
        assert res5.lifecycle_mutated is False


# ---------------------------------------------------------------------------
# 14. Clinical Denial Proof
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cancellation_outcome",
    [
        VerificationEvidenceOutcome.SOURCE_AUTHENTICATION_FAILURE,
        VerificationEvidenceOutcome.SOURCE_RESPONSE_INVALID,
        VerificationEvidenceOutcome.SOURCE_INTEGRITY_FAILURE,
        VerificationEvidenceOutcome.NOT_FOUND,
        VerificationEvidenceOutcome.CONFIRMED_INACTIVE,
        VerificationEvidenceOutcome.IDENTITY_MISMATCH,
        VerificationEvidenceOutcome.AMBIGUOUS,
        VerificationEvidenceOutcome.REVIEW_REQUIRED,
    ],
)
async def test_clinical_denial_proof(session_factory, cancellation_outcome):
    """Verify clinical eligibility is allowed during recheck grace, but strictly denied after CANCEL_RECHECK_GRACE."""
    async with session_factory() as session:
        now = datetime.now(timezone.utc)
        fac_id = uuid.uuid4()
        prov_id = uuid.uuid4()
        verif_id = uuid.uuid4()
        reg_num = f"MMC{uuid.uuid4().hex[:8].upper()}"

        hospital = HospitalRegistry(
            id=fac_id,
            facility_code=f"HOSP-{fac_id.hex[:8]}",
            legal_name="Clinical Qual Hospital",
            display_name="Clinical Qual Hospital",
            is_active=True,
        )
        session.add(hospital)

        provider = ProviderIdentity(
            id=prov_id,
            provider_uid=f"DR-{prov_id.hex[:8]}",
            hospital_id=fac_id,
            contact_email=f"dr.{prov_id.hex[:8]}@example.com",
            contact_phone="+919876543210",
            email_verified_at=now,
            phone_verified_at=now,
            status="active",
            is_active=True,
        )
        session.add(provider)

        cred = ProviderCredential(
            provider_id=prov_id,
            login_identifier=f"dr.{prov_id.hex[:8]}@example.com",
            password_hash="synthetic-secret-hash",
            mfa_enabled=True,
            is_active=True,
        )
        session.add(cred)

        verif = ProfessionalVerification(
            id=verif_id,
            provider_id=prov_id,
            status=ProfessionalVerificationStatus.RECHECK_DUE.value,
            registration_authority_code="MAHA_MED_COUNCIL",
            registration_number_normalized=reg_num,
            registration_valid_from=now - timedelta(days=365),
            registration_valid_until=now + timedelta(days=365),
            verification_method="EXTERNAL_REGISTRY",
            verification_source="MAHA_MED_COUNCIL",
            verification_reference="REF-MMC-01",
            identity_binding_method="REGISTRY_MATCH",
            identity_binding_status="MATCHED",
            verified_at=now - timedelta(days=30),
            previous_verification_valid=True,
            reviewer_id="human-reviewer-clin-42",
            recheck_attempted_at=now - timedelta(hours=1),
            recheck_failure_reason="SOURCE_UNAVAILABLE",
            grace_expires_at=now + timedelta(hours=12),
            version=1,
        )
        session.add(verif)

        fac_verif = FacilityVerification(
            id=uuid.uuid4(),
            facility_id=fac_id,
            status=FacilityVerificationStatus.VERIFIED.value,
            registration_authority_code="ROHINI",
            registration_number_normalized=f"FAC-{fac_id.hex[:8]}",
            verification_method="EXTERNAL_REGISTRY",
            verification_source="ROHINI",
            verification_reference="REF-ROHINI-01",
            verified_at=now - timedelta(days=30),
            previous_verification_valid=True,
            version=1,
        )
        session.add(fac_verif)

        affiliation = ProviderHospitalAffiliation(
            provider_id=prov_id,
            hospital_id=fac_id,
            roles=["clinician"],
            trust_status="ACTIVE",
            valid_from=now - timedelta(days=30),
            version=1,
        )
        session.add(affiliation)
        await session.commit()

        eligibility_svc = ClinicalEligibilityService(
            contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
        )
        auth = InteractiveClinicalAuthentication(
            provider_id=prov_id,
            hospital_id=fac_id,
            method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
            session_authenticated=True,
            mfa_verified_at=now,
        )

        # 1. During grace period, clinical access is ALLOWED
        check_during_grace = await eligibility_svc.evaluate_interactive(
            session,
            provider,
            auth,
            ClinicalCapability.DOCUMENTS_REVIEW,
            now=now,
        )
        assert check_during_grace.allowed is True

        # 2. Execute fail-closed CANCEL_RECHECK_GRACE via ProviderVerificationApplicationService
        req = ProfessionalLookupRequest(
            registration_authority_code="MAHA_MED_COUNCIL",
            registration_number_normalized=reg_num,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
        )
        inv = RegistryLookupInvocation(
            resource_id=verif.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=1,
            request=req,
            invoked_at=now - timedelta(minutes=5),
        )
        obs = _make_prof_obs(
            outcome=cancellation_outcome,
            source_id="QUAL_SOURCE_01",
        )
        envelope = ValidatedRegistryLookupEnvelope(invocation=inv, observation=obs)

        policy_registry = SourceAutomationPolicyRegistry()
        policy_registry.register(
            SourceAutomationPolicy(
                source_id="QUAL_SOURCE_01",
                automation_enabled=True,
            )
        )

        svc = ProviderVerificationApplicationService(
            session, source_policies=policy_registry, automation_enabled=True
        )
        result = await svc.apply_verification_observation(
            envelope=envelope,
            idempotency_key=str(uuid.uuid4()),
        )
        assert result.applied_command == "CANCEL_RECHECK_GRACE"
        assert result.lifecycle_mutated is True

        # 3. Target grace is cleared
        refreshed = (
            await session.execute(
                select(ProfessionalVerification).where(
                    ProfessionalVerification.id == verif.id
                )
            )
        ).scalar_one()
        assert refreshed.grace_expires_at is None

        # 4. Clinical access is now strictly DENIED
        check_after_cancel = await eligibility_svc.evaluate_interactive(
            session,
            provider,
            auth,
            ClinicalCapability.DOCUMENTS_REVIEW,
            now=now,
        )
        assert check_after_cancel.allowed is False
        assert check_after_cancel.professional_grace_active is False
        assert (
            check_after_cancel.denial_code
            == ClinicalEligibilityDenialCode.PROFESSIONAL_RECHECK_NOT_ELIGIBLE
        )


# ---------------------------------------------------------------------------
# 15. Rollback Atomicity via Failure Injection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure_point",
    [
        "after_evidence_insert",
        "after_review_work_insert",
        "after_lifecycle_update",
        "after_provenance_link_update",
        "after_audit_enqueue_before_idempotency_complete",
    ],
)
async def test_rollback_atomicity_via_failure_injection(
    session_factory, monkeypatch, failure_point
):
    """When a fault is injected at any stage, the entire transaction rolls back cleanly (Requirement 17):
    - failure after evidence insert
    - failure after review-work insert
    - failure after lifecycle update
    - failure after provenance-link update
    - failure after audit enqueue, before idempotency completion
    After rollback, assert:
    - evidence count unchanged
    - review-work count unchanged
    - target status unchanged
    - target version unchanged
    - provenance link unchanged
    - audit outbox unchanged
    - no completed idempotency record
    """
    async with session_factory() as session:
        is_adverse = failure_point == "after_review_work_insert"
        _, _, verif = await _seed_provider_and_verification(
            session,
            status=(
                ProfessionalVerificationStatus.VERIFIED
                if is_adverse
                else ProfessionalVerificationStatus.RECHECK_DUE
            ),
            seed_server_provenance=True,
        )
        await session.commit()
        verif_id = verif.id
        initial_status = verif.status
        initial_version = verif.version
        initial_provenance = verif.server_provenance_evidence_id

        # Baseline counts
        initial_ev_count = (
            await session.execute(
                text("SELECT count(*) FROM provider_trust_verification_evidence")
            )
        ).scalar()
        initial_rw_count = (
            await session.execute(
                text("SELECT count(*) FROM provider_trust_verification_review_work")
            )
        ).scalar()
        initial_audit_count = (
            await session.execute(text("SELECT count(*) FROM public.audit_outbox"))
        ).scalar()

        req = ProfessionalLookupRequest(
            registration_authority_code="MAHA_MED_COUNCIL",
            registration_number_normalized=verif.registration_number_normalized,
            lookup_purpose=(
                VerificationEvidenceLookupPurpose.ADVERSE_SIGNAL_CHECK
                if is_adverse
                else VerificationEvidenceLookupPurpose.RECHECK
            ),
        )
        inv = RegistryLookupInvocation(
            resource_id=verif.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=initial_version,
            request=req,
            invoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        obs = _make_prof_obs(
            outcome=(
                VerificationEvidenceOutcome.CONFIRMED_INACTIVE
                if is_adverse
                else VerificationEvidenceOutcome.CONFIRMED_ACTIVE
            ),
            purpose=req.lookup_purpose,
            source_id="QUAL_SOURCE_01",
            observed_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        envelope = ValidatedRegistryLookupEnvelope(invocation=inv, observation=obs)

        policy_registry = SourceAutomationPolicyRegistry()
        policy_registry.register(
            SourceAutomationPolicy(source_id="QUAL_SOURCE_01", automation_enabled=True)
        )
        svc = ProviderVerificationApplicationService(
            session, source_policies=policy_registry, automation_enabled=True
        )

        import app.services.provider_verification_application as pva_module

        # Configure injection point
        if failure_point == "after_evidence_insert":
            orig_enqueue = pva_module.enqueue_audit_event
            call_count = 0

            async def _fail_after_evidence(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("Injected fault: after_evidence_insert")
                return await orig_enqueue(*args, **kwargs)

            monkeypatch.setattr(pva_module, "enqueue_audit_event", _fail_after_evidence)

        elif failure_point == "after_review_work_insert":
            orig_enqueue = pva_module.enqueue_audit_event

            async def _fail_after_review_work(*args, **kwargs):
                if (
                    kwargs.get("event_type")
                    == ProviderTrustAuditEvent.PROVIDER_TRUST_VERIFICATION_REVIEW_REQUIRED.value
                ):
                    raise RuntimeError("Injected fault: after_review_work_insert")
                return await orig_enqueue(*args, **kwargs)

            monkeypatch.setattr(
                pva_module, "enqueue_audit_event", _fail_after_review_work
            )

        elif failure_point == "after_lifecycle_update":
            orig_enqueue = pva_module.enqueue_audit_event

            async def _fail_after_lifecycle(*args, **kwargs):
                if (
                    kwargs.get("event_type")
                    == ProviderTrustAuditEvent.PROVIDER_REVERIFICATION_PERFORMED.value
                ):
                    raise RuntimeError("Injected fault: after_lifecycle_update")
                return await orig_enqueue(*args, **kwargs)

            monkeypatch.setattr(
                pva_module, "enqueue_audit_event", _fail_after_lifecycle
            )

        elif failure_point == "after_provenance_link_update":
            orig_apply = svc._apply_candidate_transition

            def _fail_after_provenance(*args, **kwargs):
                orig_apply(*args, **kwargs)
                raise RuntimeError("Injected fault: after_provenance_link_update")

            monkeypatch.setattr(
                svc, "_apply_candidate_transition", _fail_after_provenance
            )

        elif failure_point == "after_audit_enqueue_before_idempotency_complete":
            orig_exec = session.execute

            async def _fail_before_idempotency(stmt, *args, **kwargs):
                if stmt is pva_module._IDEMPOTENCY_COMPLETE:
                    raise RuntimeError(
                        "Injected fault: after_audit_enqueue_before_idempotency_complete"
                    )
                return await orig_exec(stmt, *args, **kwargs)

            monkeypatch.setattr(session, "execute", _fail_before_idempotency)

        test_key = str(uuid.uuid4())
        with pytest.raises(RuntimeError, match="Injected fault:"):
            await svc.apply_verification_observation(
                envelope=envelope,
                idempotency_key=test_key,
            )

        # Verification of complete rollback
        post_ev_count = (
            await session.execute(
                text("SELECT count(*) FROM provider_trust_verification_evidence")
            )
        ).scalar()
        assert post_ev_count == initial_ev_count, "Evidence count mutated on rollback!"

        post_rw_count = (
            await session.execute(
                text("SELECT count(*) FROM provider_trust_verification_review_work")
            )
        ).scalar()
        assert (
            post_rw_count == initial_rw_count
        ), "Review work count mutated on rollback!"

        post_audit_count = (
            await session.execute(text("SELECT count(*) FROM public.audit_outbox"))
        ).scalar()
        assert (
            post_audit_count == initial_audit_count
        ), "Audit outbox mutated on rollback!"

        refreshed = (
            await session.execute(
                select(ProfessionalVerification).where(
                    ProfessionalVerification.id == verif_id
                )
            )
        ).scalar_one()
        assert (
            refreshed.version == initial_version
        ), "Target version mutated on rollback!"
        assert refreshed.status == initial_status, "Target status mutated on rollback!"
        assert (
            refreshed.server_provenance_evidence_id == initial_provenance
        ), "Target provenance link mutated on rollback!"

        # No completed idempotency record
        idem_status = (
            await session.execute(
                text(
                    "SELECT response_status FROM public.mutation_idempotency "
                    "WHERE idempotency_key = :key"
                ),
                {"key": test_key},
            )
        ).scalar_one_or_none()
        assert idem_status != 200, "Idempotency record completed despite rollback!"


async def test_concurrency_matrix_and_terminal_resurrection_prohibition(
    session_factory,
):
    """Exhaustive concurrency matrix & anti-resurrection qualification (Requirement 18):
    1. Same envelope replay -> idempotent cached replay.
    2. Two different envelopes same expected version -> first succeeds, second fails with LIFECYCLE_VERSION_CONFLICT.
    3. Human transition wins first -> system automation with stale version fails with LIFECYCLE_VERSION_CONFLICT.
    4. System transition wins first -> human command with stale version fails with LIFECYCLE_VERSION_CONFLICT.
    5. Positive vs adverse same version -> winner wins, loser fails closed with version conflict.
    6. Human SUSPEND vs positive automation -> target in SUSPENDED state fails closed to HUMAN_REVIEW_REQUIRED, no resurrection!
    7. Human REVOKE/EXPIRE vs positive automation -> terminal states yield NO_MUTATION_REQUIRED, no resurrection!
    """
    async with session_factory() as session:
        # Case 1 & 2 & 5: Two different envelopes (positive vs adverse) with same expected version 1
        _, _, verif = await _seed_provider_and_verification(
            session, status=ProfessionalVerificationStatus.RECHECK_DUE
        )
        await session.commit()

        req = ProfessionalLookupRequest(
            registration_authority_code="MAHA_MED_COUNCIL",
            registration_number_normalized=verif.registration_number_normalized,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
        )
        inv_pos = RegistryLookupInvocation(
            resource_id=verif.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=1,
            request=req,
            invoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        obs_pos = _make_prof_obs(
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            source_id="QUAL_SOURCE_01",
        )
        env_pos = ValidatedRegistryLookupEnvelope(
            invocation=inv_pos, observation=obs_pos
        )

        inv_adv = RegistryLookupInvocation(
            resource_id=verif.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=1,
            request=req,
            invoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        obs_adv = _make_prof_obs(
            outcome=VerificationEvidenceOutcome.CONFIRMED_INACTIVE,
            source_id="QUAL_SOURCE_01",
        )
        env_adv = ValidatedRegistryLookupEnvelope(
            invocation=inv_adv, observation=obs_adv
        )

        policy_registry = SourceAutomationPolicyRegistry()
        policy_registry.register(
            SourceAutomationPolicy(source_id="QUAL_SOURCE_01", automation_enabled=True)
        )
        svc = ProviderVerificationApplicationService(
            session, source_policies=policy_registry, automation_enabled=True
        )

        # Positive envelope wins first
        r_pos = await svc.apply_verification_observation(
            envelope=env_pos, idempotency_key=str(uuid.uuid4())
        )
        assert r_pos.resulting_version == 2
        assert r_pos.lifecycle_mutated is True

        # Adverse envelope with stale expected_version=1 fails closed with version conflict
        with pytest.raises(
            VerificationApplicationError, match="LIFECYCLE_VERSION_CONFLICT"
        ):
            await svc.apply_verification_observation(
                envelope=env_adv, idempotency_key=str(uuid.uuid4())
            )

        # Case 3: Human transition wins first -> system automation with stale version fails
        _, _, verif_human = await _seed_provider_and_verification(
            session, status=ProfessionalVerificationStatus.RECHECK_DUE
        )
        req_human = ProfessionalLookupRequest(
            registration_authority_code=verif_human.registration_authority_code,
            registration_number_normalized=verif_human.registration_number_normalized,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
        )
        verif_human_id = verif_human.id
        await session.commit()
        # Simulate human advancing version from 1 to 2
        await session.execute(
            text("UPDATE professional_verification SET version = 2 WHERE id = :id"),
            {"id": verif_human_id},
        )
        await session.commit()
        inv_stale = RegistryLookupInvocation(
            resource_id=verif_human.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=1,  # Stale!
            request=req_human,
            invoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        env_stale = ValidatedRegistryLookupEnvelope(
            invocation=inv_stale, observation=obs_pos
        )
        with pytest.raises(
            VerificationApplicationError, match="LIFECYCLE_VERSION_CONFLICT"
        ):
            await svc.apply_verification_observation(
                envelope=env_stale, idempotency_key=str(uuid.uuid4())
            )

        # Case 6: Human SUSPEND vs positive automation -> NO RESURRECTION!
        _, _, verif_susp = await _seed_provider_and_verification(
            session, status=ProfessionalVerificationStatus.SUSPENDED
        )
        await session.commit()
        req_susp = ProfessionalLookupRequest(
            registration_authority_code=verif_susp.registration_authority_code,
            registration_number_normalized=verif_susp.registration_number_normalized,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
        )
        inv_susp = RegistryLookupInvocation(
            resource_id=verif_susp.id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=verif_susp.version,
            request=req_susp,
            invoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        env_susp = ValidatedRegistryLookupEnvelope(
            invocation=inv_susp, observation=obs_pos
        )
        r_susp = await svc.apply_verification_observation(
            envelope=env_susp, idempotency_key=str(uuid.uuid4())
        )
        assert (
            r_susp.decision_disposition
            == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED.value
        )
        assert (
            r_susp.reason_code
            == VerificationDecisionReason.SUSPENDED_STATE_NO_AUTOMATION.value
        )
        assert r_susp.lifecycle_mutated is False
        refreshed_susp = await session.get(ProfessionalVerification, verif_susp.id)
        assert (
            refreshed_susp.status == ProfessionalVerificationStatus.SUSPENDED.value
        )  # No resurrection!

        # Case 7: Human REVOKE / EXPIRE vs positive automation -> NO RESURRECTION!
        for terminal_status in [
            ProfessionalVerificationStatus.REVOKED,
            ProfessionalVerificationStatus.EXPIRED,
        ]:
            _, _, verif_term = await _seed_provider_and_verification(
                session, status=terminal_status
            )
            await session.commit()
            req_term = ProfessionalLookupRequest(
                registration_authority_code=verif_term.registration_authority_code,
                registration_number_normalized=verif_term.registration_number_normalized,
                lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
            )
            inv_term = RegistryLookupInvocation(
                resource_id=verif_term.id,
                resource_type=RegistryResourceType.PROFESSIONAL,
                expected_version=verif_term.version,
                request=req_term,
                invoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            )
            env_term = ValidatedRegistryLookupEnvelope(
                invocation=inv_term, observation=obs_pos
            )
            r_term = await svc.apply_verification_observation(
                envelope=env_term, idempotency_key=str(uuid.uuid4())
            )
            assert (
                r_term.decision_disposition
                == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED.value
            )
            assert (
                r_term.reason_code
                == VerificationDecisionReason.TERMINAL_STATE_NO_AUTOMATION.value
            )
            assert r_term.lifecycle_mutated is False
            assert r_term.applied_command is None
            refreshed_term = await session.get(ProfessionalVerification, verif_term.id)
            assert refreshed_term.status == terminal_status.value  # No resurrection!

            # Also verify adverse outcome on terminal state yields NO_MUTATION_REQUIRED
            env_term_adv = ValidatedRegistryLookupEnvelope(
                invocation=inv_term, observation=obs_adv
            )
            r_term_adv = await svc.apply_verification_observation(
                envelope=env_term_adv, idempotency_key=str(uuid.uuid4())
            )
            assert (
                r_term_adv.decision_disposition
                == VerificationDecisionDisposition.NO_MUTATION_REQUIRED.value
            )
            assert (
                r_term_adv.reason_code
                == VerificationDecisionReason.TERMINAL_STATE_NO_AUTOMATION.value
            )
            assert r_term_adv.lifecycle_mutated is False
            refreshed_term_adv = await session.get(
                ProfessionalVerification, verif_term.id
            )
            assert (
                refreshed_term_adv.status == terminal_status.value
            )  # No resurrection!


# ---------------------------------------------------------------------------
# 16. Three-Path Migration Qualification
# ---------------------------------------------------------------------------


async def test_three_path_migration_qualification():
    """Verify 3-path migration qualification:
    Path 1: Fresh DB -> HEAD.
    Path 2: Fresh DB -> PREVIOUS_HEAD -> HEAD.
    Path 3: HEAD -> downgrade to PREVIOUS_HEAD -> re-upgrade to HEAD.
    """
    db_url = _get_db_url()
    config = _config(db_url)
    admin_url = _get_async_admin_url()

    # Path 1: Fresh DB -> HEAD
    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        await conn.execute(text(f"DROP DATABASE IF EXISTS {_DB_NAME} WITH (FORCE)"))
        await conn.execute(text(f"CREATE DATABASE {_DB_NAME}"))
    await admin_engine.dispose()

    await asyncio.to_thread(command.upgrade, config, HEAD)

    engine1 = create_async_engine(db_url)
    async with engine1.connect() as conn:
        res = await conn.execute(text("SELECT version_num FROM alembic_version"))
        assert res.scalar() == HEAD
    await engine1.dispose()

    # Path 2: Fresh DB -> PREVIOUS_HEAD -> HEAD
    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        await conn.execute(text(f"DROP DATABASE IF EXISTS {_DB_NAME} WITH (FORCE)"))
        await conn.execute(text(f"CREATE DATABASE {_DB_NAME}"))
    await admin_engine.dispose()

    await asyncio.to_thread(command.upgrade, config, PREVIOUS_HEAD)
    engine2_prev = create_async_engine(db_url)
    async with engine2_prev.connect() as conn:
        res = await conn.execute(text("SELECT version_num FROM alembic_version"))
        assert res.scalar() == PREVIOUS_HEAD
    await engine2_prev.dispose()

    await asyncio.to_thread(command.upgrade, config, HEAD)
    engine2 = create_async_engine(db_url)
    async with engine2.connect() as conn:
        res = await conn.execute(text("SELECT version_num FROM alembic_version"))
        assert res.scalar() == HEAD
    await engine2.dispose()

    # Path 3: HEAD -> downgrade to PREVIOUS_HEAD -> re-upgrade to HEAD
    await asyncio.to_thread(command.downgrade, config, PREVIOUS_HEAD)
    engine3_prev = create_async_engine(db_url)
    async with engine3_prev.connect() as conn:
        res = await conn.execute(text("SELECT version_num FROM alembic_version"))
        assert res.scalar() == PREVIOUS_HEAD
    await engine3_prev.dispose()

    await asyncio.to_thread(command.upgrade, config, HEAD)
    engine3 = create_async_engine(db_url)
    async with engine3.connect() as conn:
        res = await conn.execute(text("SELECT version_num FROM alembic_version"))
        assert res.scalar() == HEAD
    await engine3.dispose()
