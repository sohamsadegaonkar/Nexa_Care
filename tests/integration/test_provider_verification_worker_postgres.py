"""PostgreSQL qualification suite for Phase 5F: Provider Verification Worker.

Validates on real disposable loopback PostgreSQL:
1. Professional Real E2E: RECHECK_DUE -> SyntheticRegistryAdapter -> COMPLETED,
   VERIFIED, evidence linked, next_review_at computed, reviewer preserved.
2. Facility Real E2E: RECHECK_REQUIRED -> SyntheticRegistryAdapter -> COMPLETED,
   VERIFIED, evidence linked, next_review_at computed, reviewer preserved.
3. Retry Exhaustion Real Proof: RegistryTransientUnavailableError on each attempt ->
   attempts 1..N-1 return PENDING with ZERO evidence rows, attempt N returns EXHAUSTED
   with exactly ONE SOURCE_UNAVAILABLE evidence row.
4. Contract Failure Real Proof: RegistryAdapterContractError -> FAILED_TERMINAL,
   ZERO evidence row, ZERO lifecycle mutation, ZERO review work.
5. Lost Lease Real Proof: Stolen/expired lease -> worker discards observation before 5E,
   ZERO evidence row, ZERO lifecycle mutation.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.provider import (
    FacilityVerification,
    FacilityVerificationStatus,
    HospitalRegistry,
    ProfessionalVerification,
    ProfessionalVerificationStatus,
    ProviderCredential,
    ProviderIdentity,
    ProviderTrustVerificationEvidence,
    ProviderTrustVerificationReviewWork,
    ProviderVerificationWork,
    VerificationEvidenceLookupPurpose,
    VerificationEvidenceOrigin,
    VerificationEvidenceOutcome,
    VerificationIdentityBindingResult,
    VerificationWorkStatus,
)
from app.services.provider_verification_application import (
    SourceAutomationPolicy,
    SourceAutomationPolicyRegistry,
)
from app.services.provider_verification_registry import (
    RegistryAdapterContractError,
    RegistryResourceType,
    RegistrySourceDescriptor,
    RegistryTransientUnavailableError,
    SyntheticRegistryAdapter,
)
from app.services.provider_verification_worker import ProviderVerificationWorkerService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgres,
    pytest.mark.asyncio,
]

HEAD = "20260906_verification_scheduler"
_DB_NAME = "nexa_qual_worker"


# ---------------------------------------------------------------------------
# DB setup helpers
# ---------------------------------------------------------------------------


def _get_db_url() -> str:
    test_url = os.getenv("TEST_DATABASE_URL")
    if test_url:
        url = test_url
    else:
        db_url = os.getenv("DATABASE_URL")
        if db_url and "nexa_qual_" in db_url:
            url = db_url
        elif db_url and "nexa_qual_" not in db_url:
            pytest.skip(
                "No disposable nexa_qual_ database configured in TEST_DATABASE_URL"
            )
        else:
            url = f"postgresql+asyncpg://nexa:nexa_test@127.0.0.1:55439/{_DB_NAME}"
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
    """Create disposable database and migrate to scheduler HEAD."""
    db_url = _get_db_url()
    _prev = os.environ.get("TEST_DATABASE_URL")
    os.environ["TEST_DATABASE_URL"] = db_url

    async def _init_db():
        admin_engine = create_async_engine(
            _get_async_admin_url(), isolation_level="AUTOCOMMIT"
        )
        async with admin_engine.connect() as conn:
            await conn.execute(text(f"DROP DATABASE IF EXISTS {_DB_NAME} WITH (FORCE)"))
            await conn.execute(text(f"CREATE DATABASE {_DB_NAME}"))
        await admin_engine.dispose()

    asyncio.run(_init_db())
    cfg = _config(db_url)
    command.upgrade(cfg, HEAD)
    yield

    if _prev is None:
        os.environ.pop("TEST_DATABASE_URL", None)
    else:
        os.environ["TEST_DATABASE_URL"] = _prev

    async def _cleanup_db():
        admin_engine = create_async_engine(
            _get_async_admin_url(), isolation_level="AUTOCOMMIT"
        )
        async with admin_engine.connect() as conn:
            await conn.execute(text(f"DROP DATABASE IF EXISTS {_DB_NAME} WITH (FORCE)"))
        await admin_engine.dispose()

    asyncio.run(_cleanup_db())


@pytest.fixture
def session_factory():
    db_url = _get_db_url()
    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory


# ---------------------------------------------------------------------------
# Data creation helpers
# ---------------------------------------------------------------------------


async def _create_provider(session_factory) -> ProviderIdentity:
    prov_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with session_factory() as db:
        prov = ProviderIdentity(
            id=prov_id,
            provider_uid=f"puid-{prov_id.hex[:8]}",
            display_name="Dr. Worker Test",
            contact_email=f"worker-{prov_id.hex[:8]}@example.test",
            contact_phone="+919876543210",
            email_verified_at=now,
            phone_verified_at=now,
            is_active=True,
            status="active",
        )
        cred = ProviderCredential(
            id=uuid.uuid4(),
            provider_id=prov_id,
            login_identifier=f"worker-{prov_id.hex[:8]}@example.test",
            password_hash="argon2-qual-dummy",
            mfa_enabled=True,
            is_active=True,
        )
        db.add_all([prov, cred])
        await db.commit()
        await db.refresh(prov)
        return prov


async def _create_facility(session_factory) -> HospitalRegistry:
    fac_id = uuid.uuid4()
    async with session_factory() as db:
        fac = HospitalRegistry(
            id=fac_id,
            facility_code=f"FAC-{fac_id.hex[:8]}",
            legal_name=f"Worker Hospital {fac_id.hex[:4]}",
            display_name=f"Worker Hospital {fac_id.hex[:4]}",
            country_code="IN",
            is_active=True,
        )
        db.add(fac)
        await db.commit()
        await db.refresh(fac)
        return fac


def _worker_policy(
    resource_type: RegistryResourceType, authority: str, source: str
) -> SourceAutomationPolicy:
    return SourceAutomationPolicy(
        source_id=source,
        resource_type=resource_type,
        registration_authority_code=authority,
        approved_adapter_version="1.0.0",
        allowed_binding_methods=frozenset({"SYNTHETIC_EXACT"}),
        automation_enabled=True,
        recheck_interval_seconds=86400,
    )


async def _create_anchored_professional_work(
    session_factory,
    *,
    max_attempts: int = 5,
) -> tuple[uuid.UUID, uuid.UUID, datetime]:
    provider = await _create_provider(session_factory)
    now = datetime.now(timezone.utc)
    async with session_factory() as db:
        verification = ProfessionalVerification(
            id=uuid.uuid4(),
            provider_id=provider.id,
            status=ProfessionalVerificationStatus.RECHECK_DUE.value,
            registration_authority_code="NMC",
            registration_number_normalized=f"NMC{uuid.uuid4().hex[:10].upper()}",
            registration_valid_from=now - timedelta(days=365),
            registration_valid_until=now + timedelta(days=365),
            previous_verification_valid=True,
            reviewer_id="synthetic-human-reviewer",
            version=1,
        )
        db.add(verification)
        await db.flush()
        anchor = ProviderTrustVerificationEvidence(
            professional_verification_id=verification.id,
            origin=VerificationEvidenceOrigin.SERVER_REGISTRY_OBSERVATION.value,
            source_id="NMC_REGISTRY",
            adapter_version="1.0.0",
            observed_at=now - timedelta(days=30),
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            identity_binding_result=VerificationIdentityBindingResult.MATCHED.value,
            observed_resource_version=1,
        )
        db.add(anchor)
        await db.flush()
        verification.server_provenance_evidence_id = anchor.id
        work = ProviderVerificationWork(
            id=uuid.uuid4(),
            professional_verification_id=verification.id,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK.value,
            source_id="NMC_REGISTRY",
            adapter_version="1.0.0",
            registration_authority_code=verification.registration_authority_code,
            registration_number_normalized=verification.registration_number_normalized,
            expected_resource_version=1,
            scheduler_reason="RECHECK_DUE_BOOTSTRAP",
            status=VerificationWorkStatus.PENDING.value,
            next_attempt_at=now,
            max_attempts=max_attempts,
            created_at=now - timedelta(seconds=1),
        )
        db.add(work)
        await db.commit()
        return verification.id, work.id, now


async def _create_anchored_facility_work(
    session_factory,
    *,
    max_attempts: int = 5,
) -> tuple[uuid.UUID, uuid.UUID, datetime]:
    facility = await _create_facility(session_factory)
    now = datetime.now(timezone.utc)
    async with session_factory() as db:
        verification = FacilityVerification(
            id=uuid.uuid4(),
            facility_id=facility.id,
            status=FacilityVerificationStatus.RECHECK_REQUIRED.value,
            registration_authority_code="NHA",
            registration_number_normalized=f"NHA{uuid.uuid4().hex[:10].upper()}",
            registration_valid_from=now - timedelta(days=365),
            registration_valid_until=now + timedelta(days=365),
            reviewer_id="synthetic-human-reviewer",
            version=1,
        )
        db.add(verification)
        await db.flush()
        anchor = ProviderTrustVerificationEvidence(
            facility_verification_id=verification.id,
            origin=VerificationEvidenceOrigin.SERVER_REGISTRY_OBSERVATION.value,
            source_id="NHA_REGISTRY",
            adapter_version="1.0.0",
            observed_at=now - timedelta(days=30),
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            identity_binding_result=VerificationIdentityBindingResult.MATCHED.value,
            observed_resource_version=1,
        )
        db.add(anchor)
        await db.flush()
        verification.server_provenance_evidence_id = anchor.id
        work = ProviderVerificationWork(
            id=uuid.uuid4(),
            facility_verification_id=verification.id,
            lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK.value,
            source_id="NHA_REGISTRY",
            adapter_version="1.0.0",
            registration_authority_code=verification.registration_authority_code,
            registration_number_normalized=verification.registration_number_normalized,
            expected_resource_version=1,
            scheduler_reason="RECHECK_REQUIRED_BOOTSTRAP",
            status=VerificationWorkStatus.PENDING.value,
            next_attempt_at=now,
            max_attempts=max_attempts,
            created_at=now - timedelta(seconds=1),
        )
        db.add(work)
        await db.commit()
        return verification.id, work.id, now


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------


async def test_worker_professional_e2e_real_postgres(session_factory) -> None:
    """1. Professional Real E2E: RECHECK_DUE -> CONFIRMED_ACTIVE -> VERIFIED."""
    verification_id, work_id, now = await _create_anchored_professional_work(
        session_factory
    )
    adapter = SyntheticRegistryAdapter(
        RegistrySourceDescriptor(
            source_id="NMC_REGISTRY",
            adapter_version="1.0.0",
            supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
        )
    )
    async with session_factory() as db:
        worker = ProviderVerificationWorkerService(
            db,
            worker_id="worker:real-prof-e2e",
            adapters={"NMC_REGISTRY": adapter},
            source_policies=SourceAutomationPolicyRegistry(
                [
                    _worker_policy(
                        RegistryResourceType.PROFESSIONAL, "NMC", "NMC_REGISTRY"
                    )
                ]
            ),
            automation_enabled=True,
        )
        claimed = await worker.claim_work_batch(now=now)
        target = next(row for row in claimed if row.id == work_id)
        result = await worker.process_work_item(target, now=now)
        assert result is VerificationWorkStatus.COMPLETED

    async with session_factory() as db:
        verification = await db.get(ProfessionalVerification, verification_id)
        work = await db.get(ProviderVerificationWork, work_id)
        assert verification is not None and work is not None
        assert verification.status == ProfessionalVerificationStatus.VERIFIED.value
        assert verification.version == 2
        assert verification.reviewer_id == "synthetic-human-reviewer"
        assert verification.next_review_at is not None
        assert verification.next_review_at > now
        assert work.status == VerificationWorkStatus.COMPLETED.value
        assert work.attempt_count == 1
        assert work.result_evidence_id == verification.server_provenance_evidence_id
        assert work.result_evidence_id is not None


async def test_worker_facility_e2e_real_postgres(session_factory) -> None:
    """2. Facility Real E2E: RECHECK_REQUIRED -> CONFIRMED_ACTIVE -> VERIFIED."""
    verification_id, work_id, now = await _create_anchored_facility_work(
        session_factory
    )
    adapter = SyntheticRegistryAdapter(
        RegistrySourceDescriptor(
            source_id="NHA_REGISTRY",
            adapter_version="1.0.0",
            supported_resource_types=(RegistryResourceType.FACILITY,),
        )
    )
    async with session_factory() as db:
        worker = ProviderVerificationWorkerService(
            db,
            worker_id="worker:real-fac-e2e",
            adapters={"NHA_REGISTRY": adapter},
            source_policies=SourceAutomationPolicyRegistry(
                [_worker_policy(RegistryResourceType.FACILITY, "NHA", "NHA_REGISTRY")]
            ),
            automation_enabled=True,
        )
        claimed = await worker.claim_work_batch(now=now)
        target = next(row for row in claimed if row.id == work_id)
        result = await worker.process_work_item(target, now=now)
        assert result is VerificationWorkStatus.COMPLETED

    async with session_factory() as db:
        verification = await db.get(FacilityVerification, verification_id)
        work = await db.get(ProviderVerificationWork, work_id)
        assert verification is not None and work is not None
        assert verification.status == FacilityVerificationStatus.VERIFIED.value
        assert verification.version == 2
        assert verification.reviewer_id == "synthetic-human-reviewer"
        assert verification.next_review_at is not None
        assert verification.next_review_at > now
        assert work.status == VerificationWorkStatus.COMPLETED.value
        assert work.attempt_count == 1
        assert work.result_evidence_id == verification.server_provenance_evidence_id
        assert work.result_evidence_id is not None


async def test_worker_retry_exhaustion_real_postgres(session_factory) -> None:
    """3. Retry Exhaustion: RegistryTransientUnavailableError on each attempt.

    Attempts 1..N-1 return PENDING with NO evidence rows.
    Attempt N returns EXHAUSTED with exactly ONE SOURCE_UNAVAILABLE evidence row.
    """
    verification_id, work_id, _ = await _create_anchored_professional_work(
        session_factory, max_attempts=2
    )

    class TransientUnavailableAdapter(SyntheticRegistryAdapter):
        async def _lookup_professional(self, request):
            raise RegistryTransientUnavailableError("transient network timeout")

    adapter = TransientUnavailableAdapter(
        RegistrySourceDescriptor(
            source_id="NMC_REGISTRY",
            adapter_version="1.0.0",
            supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
        )
    )
    policy = SourceAutomationPolicyRegistry(
        [_worker_policy(RegistryResourceType.PROFESSIONAL, "NMC", "NMC_REGISTRY")]
    )

    # Attempt 1 -> PENDING, NO new evidence created.
    now1 = datetime.now(timezone.utc)
    async with session_factory() as db:
        worker1 = ProviderVerificationWorkerService(
            db,
            worker_id="worker:retry-qual",
            adapters={"NMC_REGISTRY": adapter},
            source_policies=policy,
            automation_enabled=True,
        )
        claimed1 = await worker1.claim_work_batch(now=now1)
        target1 = next(row for row in claimed1 if row.id == work_id)
        assert (
            await worker1.process_work_item(target1, now=now1)
            is VerificationWorkStatus.PENDING
        )

    async with session_factory() as db:
        work1 = await db.get(ProviderVerificationWork, work_id)
        assert work1 is not None
        assert work1.attempt_count == 1
        assert work1.status == VerificationWorkStatus.PENDING.value
        assert work1.result_evidence_id is None
        evidence_count1 = await db.scalar(
            select(func.count())
            .select_from(ProviderTrustVerificationEvidence)
            .where(
                ProviderTrustVerificationEvidence.professional_verification_id
                == verification_id
            )
        )
        assert evidence_count1 == 1  # Only the pre-existing anchor, NO new evidence!

    # Attempt 2 -> EXHAUSTED, exactly ONE SOURCE_UNAVAILABLE evidence created.
    now2 = now1 + timedelta(minutes=5)
    async with session_factory() as db:
        worker2 = ProviderVerificationWorkerService(
            db,
            worker_id="worker:retry-qual",
            adapters={"NMC_REGISTRY": adapter},
            source_policies=policy,
            automation_enabled=True,
        )
        claimed2 = await worker2.claim_work_batch(now=now2)
        target2 = next(row for row in claimed2 if row.id == work_id)
        assert (
            await worker2.process_work_item(target2, now=now2)
            is VerificationWorkStatus.EXHAUSTED
        )

    async with session_factory() as db:
        work2 = await db.get(ProviderVerificationWork, work_id)
        assert work2 is not None
        assert work2.attempt_count == 2
        assert work2.status == VerificationWorkStatus.EXHAUSTED.value
        assert work2.result_evidence_id is not None

        evidence_count2 = await db.scalar(
            select(func.count())
            .select_from(ProviderTrustVerificationEvidence)
            .where(
                ProviderTrustVerificationEvidence.professional_verification_id
                == verification_id
            )
        )
        assert (
            evidence_count2 == 2
        )  # Pre-existing anchor + exactly ONE exhaustion evidence!

        exhaustion_ev = await db.get(
            ProviderTrustVerificationEvidence, work2.result_evidence_id
        )
        assert exhaustion_ev is not None
        assert (
            exhaustion_ev.outcome
            == VerificationEvidenceOutcome.SOURCE_UNAVAILABLE.value
        )


async def test_worker_contract_failure_is_terminal_with_zero_evidence(
    session_factory,
) -> None:
    """4. Contract Failure: RegistryAdapterContractError -> FAILED_TERMINAL.

    ZERO evidence rows created, ZERO lifecycle mutation, ZERO review work.
    """
    verification_id, work_id, now = await _create_anchored_professional_work(
        session_factory
    )

    class ContractFailingAdapter(SyntheticRegistryAdapter):
        async def _lookup_professional(self, request):
            raise RegistryAdapterContractError("corrupted upstream schema")

    adapter = ContractFailingAdapter(
        RegistrySourceDescriptor(
            source_id="NMC_REGISTRY",
            adapter_version="1.0.0",
            supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
        )
    )
    policy = SourceAutomationPolicyRegistry(
        [_worker_policy(RegistryResourceType.PROFESSIONAL, "NMC", "NMC_REGISTRY")]
    )

    async with session_factory() as db:
        worker = ProviderVerificationWorkerService(
            db,
            worker_id="worker:contract-fail",
            adapters={"NMC_REGISTRY": adapter},
            source_policies=policy,
            automation_enabled=True,
        )
        claimed = await worker.claim_work_batch(now=now)
        target = next(row for row in claimed if row.id == work_id)
        result = await worker.process_work_item(target, now=now)
        assert result is VerificationWorkStatus.FAILED_TERMINAL

    async with session_factory() as db:
        work = await db.get(ProviderVerificationWork, work_id)
        verification = await db.get(ProfessionalVerification, verification_id)
        assert work is not None and verification is not None
        assert work.status == VerificationWorkStatus.FAILED_TERMINAL.value
        assert work.last_error_code == "REGISTRY_CONTRACT_ERROR"
        assert work.result_evidence_id is None
        assert verification.status == ProfessionalVerificationStatus.RECHECK_DUE.value
        assert verification.version == 1

        evidence_count = await db.scalar(
            select(func.count())
            .select_from(ProviderTrustVerificationEvidence)
            .where(
                ProviderTrustVerificationEvidence.professional_verification_id
                == verification_id
            )
        )
        assert evidence_count == 1  # ZERO new evidence!

        review_count = await db.scalar(
            select(func.count())
            .select_from(ProviderTrustVerificationReviewWork)
            .join(
                ProviderTrustVerificationEvidence,
                ProviderTrustVerificationReviewWork.evidence_id
                == ProviderTrustVerificationEvidence.id,
            )
            .where(
                ProviderTrustVerificationEvidence.professional_verification_id
                == verification_id
            )
        )
        assert review_count == 0  # ZERO review work for this target!


async def test_worker_lost_lease_discards_observation(session_factory) -> None:
    """5. Lost Lease: Stolen lease -> observation discarded before Phase 5E."""
    verification_id, work_id, now = await _create_anchored_professional_work(
        session_factory
    )
    policy = SourceAutomationPolicyRegistry(
        [_worker_policy(RegistryResourceType.PROFESSIONAL, "NMC", "NMC_REGISTRY")]
    )

    class LeaseStealingAdapter(SyntheticRegistryAdapter):
        async def _lookup_professional(self, request):
            # Simulate another worker stealing the lease while this adapter is executing
            async with session_factory() as lease_db:
                await lease_db.execute(
                    text(
                        "UPDATE provider_verification_work SET lease_expires_at = now() - interval '1 second' WHERE id = :id"
                    ),
                    {"id": work_id},
                )
                await lease_db.commit()
            async with session_factory() as lease_db:
                worker_b = ProviderVerificationWorkerService(
                    lease_db,
                    worker_id="worker:lease-owner-b",
                    source_policies=policy,
                    automation_enabled=True,
                )
                assert [
                    row.id
                    for row in await worker_b.claim_work_batch(
                        now=datetime.now(timezone.utc)
                    )
                ] == [work_id]
            return await super()._lookup_professional(request)

    adapter = LeaseStealingAdapter(
        RegistrySourceDescriptor(
            source_id="NMC_REGISTRY",
            adapter_version="1.0.0",
            supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
        )
    )
    async with session_factory() as db:
        worker_a = ProviderVerificationWorkerService(
            db,
            worker_id="worker:lease-owner-a",
            adapters={"NMC_REGISTRY": adapter},
            source_policies=policy,
            automation_enabled=True,
        )
        claimed = await worker_a.claim_work_batch(now=now)
        # Worker A detects lost lease and aborts before Phase 5E
        assert (
            await worker_a.process_work_item(claimed[0], now=now)
            is VerificationWorkStatus.CLAIMED
        )

    async with session_factory() as db:
        verification = await db.get(ProfessionalVerification, verification_id)
        work = await db.get(ProviderVerificationWork, work_id)
        evidence_count = await db.scalar(
            select(func.count())
            .select_from(ProviderTrustVerificationEvidence)
            .where(
                ProviderTrustVerificationEvidence.professional_verification_id
                == verification_id
            )
        )
        assert verification is not None and work is not None
        assert verification.status == ProfessionalVerificationStatus.RECHECK_DUE.value
        assert verification.version == 1
        assert evidence_count == 1  # ZERO new evidence!
        assert work.lease_owner == "worker:lease-owner-b"
        assert work.result_evidence_id is None


async def test_worker_generic_sensitive_exception_is_terminal_and_not_persisted(
    session_factory,
) -> None:
    """6. Generic Sensitive Exception: RuntimeError with secret -> FAILED_TERMINAL.

    Verifies on real PostgreSQL:
    - work.status is FAILED_TERMINAL
    - work.last_error_code is REGISTRY_CONTRACT_ERROR (sanitized by adapter template method)
    - Database row contains ZERO occurrence of the secret token
    - ZERO new evidence rows created
    - ZERO lifecycle mutation
    """
    verification_id, work_id, now = await _create_anchored_professional_work(
        session_factory
    )

    class CrashingSecretAdapter(SyntheticRegistryAdapter):
        async def _lookup_professional(self, request):
            raise RuntimeError("Authorization: Bearer SUPER_SECRET_INTERNAL_KEY")

    adapter = CrashingSecretAdapter(
        RegistrySourceDescriptor(
            source_id="NMC_REGISTRY",
            adapter_version="1.0.0",
            supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
        )
    )
    policy = SourceAutomationPolicyRegistry(
        [_worker_policy(RegistryResourceType.PROFESSIONAL, "NMC", "NMC_REGISTRY")]
    )

    async with session_factory() as db:
        worker = ProviderVerificationWorkerService(
            db,
            worker_id="worker:secret-fail",
            adapters={"NMC_REGISTRY": adapter},
            source_policies=policy,
            automation_enabled=True,
        )
        claimed = await worker.claim_work_batch(now=now)
        target = next(row for row in claimed if row.id == work_id)
        result = await worker.process_work_item(target, now=now)
        assert result is VerificationWorkStatus.FAILED_TERMINAL

    async with session_factory() as db:
        work = await db.get(ProviderVerificationWork, work_id)
        verification = await db.get(ProfessionalVerification, verification_id)
        assert work is not None and verification is not None
        assert work.status == VerificationWorkStatus.FAILED_TERMINAL.value
        assert work.last_error_code == "REGISTRY_CONTRACT_ERROR"
        assert work.result_evidence_id is None

        # Verify NO secret leaked into the database row
        row_values = [
            str(val)
            for val in [
                work.last_error_code,
                work.scheduler_reason,
                work.lease_owner,
                work.status,
            ]
            if val is not None
        ]
        row_text = " ".join(row_values)
        assert "SUPER_SECRET" not in row_text
        assert "Bearer" not in row_text
        assert "Authorization" not in row_text

        # Verify NO last_error_message column on ORM or table
        assert not hasattr(work, "last_error_message")

        # Verify ZERO new evidence
        evidence_count = await db.scalar(
            select(func.count())
            .select_from(ProviderTrustVerificationEvidence)
            .where(
                ProviderTrustVerificationEvidence.professional_verification_id
                == verification_id
            )
        )
        assert evidence_count == 1

        # Verify ZERO lifecycle mutation
        assert verification.status == ProfessionalVerificationStatus.RECHECK_DUE.value
        assert verification.version == 1
