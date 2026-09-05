"""PostgreSQL qualification suite for Slice 5 Phase 5B: Verification Evidence + Facility Trust Schema.

Validates on real disposable loopback PostgreSQL:
1. Append-only immutability trigger (ERRCODE 55000) denying UPDATE and DELETE.
2. Resource target XOR constraint (professional_verification_id XOR facility_verification_id).
3. Foreign key constraints with ON DELETE RESTRICT preventing parent deletion.
4. Origin provenance constraint and adapter_version requirement for SERVER_REGISTRY_OBSERVATION.
5. Observed resource version >= 1 check constraint.
6. SHA-256 evidence digest format check constraint (length = 64).
7. Validity interval check constraints.
8. FacilityVerification schema extensions (9 fields, constraints, default previous_verification_valid=False).
9. Strict clinical authority separation (facility RECHECK_REQUIRED denies clinical eligibility).
10. Migration downgrade to 20260903_trust_authorization and re-upgrade to 20260904_verification_evidence.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
    VerificationEvidenceLookupPurpose,
    VerificationEvidenceOrigin,
    VerificationEvidenceOutcome,
    VerificationIdentityBindingResult,
)
from app.security.clinical_policy import CLINICAL_CONTACT_ASSURANCE_POLICY
from app.security.provider_capabilities import ClinicalCapability
from app.services.clinical_eligibility import (
    ClinicalAuthenticationMethod,
    ClinicalEligibilityService,
    InteractiveClinicalAuthentication,
)
from tests.helpers.qualification_infra import (
    create_disposable_database,
    drop_disposable_database,
    migrate_database_to_head,
    normalize_sync_postgres_url,
    postgres_database_url,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgres,
    pytest.mark.asyncio,
]

HEAD = "20260905_verification_application"
PREVIOUS_HEAD = "20260904_verification_evidence"
PREV_PREV_HEAD = (
    "20260903_trust_authorization"  # pre-evidence; used by test_16 downgrade
)
_DB_NAME = "nexa_qual_slice5_evidence"


def _get_db_url() -> str:
    return postgres_database_url(_DB_NAME)


def _config(db_url: str) -> Config:
    config = Config("alembic.ini")
    sync_url = normalize_sync_postgres_url(db_url)
    config.set_main_option("sqlalchemy.url", sync_url)
    return config


@pytest.fixture(scope="module", autouse=True)
def _setup_database():
    """Ensure disposable database exists and is migrated to HEAD."""
    db_url = _get_db_url()
    # env.py's get_url() reads TEST_DATABASE_URL from os.environ (not from the alembic
    # Config object).  We must expose the disposable-DB async URL there so Alembic
    # routes migrations to the right database via async_engine_from_config.
    _prev = os.environ.get("TEST_DATABASE_URL")
    os.environ["TEST_DATABASE_URL"] = db_url  # asyncpg URL — env.py uses this directly

    asyncio.run(create_disposable_database(_DB_NAME))
    migrate_database_to_head(db_url, target_head=HEAD)
    yield

    # Restore environment and teardown disposable database
    if _prev is None:
        os.environ.pop("TEST_DATABASE_URL", None)
    else:
        os.environ["TEST_DATABASE_URL"] = _prev

    asyncio.run(drop_disposable_database(_DB_NAME))


@pytest.fixture
def session_factory():
    db_url = _get_db_url()
    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory


async def _create_test_facility(
    session_factory, *, name: str = "Test Hospital"
) -> HospitalRegistry:
    fac_id = uuid.uuid4()
    async with session_factory() as db:
        fac = HospitalRegistry(
            id=fac_id,
            facility_code=f"FAC-{fac_id.hex[:8]}",
            legal_name=name,
            display_name=name,
            country_code="IN",
            is_active=True,
        )
        db.add(fac)
        await db.commit()
        await db.refresh(fac)
        return fac


async def _create_test_provider(
    session_factory, *, email: str = "doctor@example.test"
) -> ProviderIdentity:
    prov_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with session_factory() as db:
        prov = ProviderIdentity(
            id=prov_id,
            provider_uid=f"puid-{prov_id.hex[:8]}",
            display_name="Dr. Test Qual",
            contact_email=email,
            contact_phone="+919876543210",
            email_verified_at=now,
            phone_verified_at=now,
            is_active=True,
            status="active",
        )
        cred = ProviderCredential(
            id=uuid.uuid4(),
            provider_id=prov_id,
            login_identifier=email,
            password_hash="argon2-qual-dummy",
            mfa_enabled=True,
            is_active=True,
        )
        db.add_all([prov, cred])
        await db.commit()
        await db.refresh(prov)
        return prov


async def _create_professional_verification(
    session_factory,
    provider_id: uuid.UUID,
    *,
    status: str = ProfessionalVerificationStatus.VERIFIED.value,
) -> ProfessionalVerification:
    now = datetime.now(timezone.utc)
    pv_id = uuid.uuid4()
    # Use UUID-derived suffix to avoid the global unique constraint
    # uq_professional_verification_authority_registration on (authority, number)
    reg_suffix = pv_id.hex[:12]
    async with session_factory() as db:
        pv = ProfessionalVerification(
            id=pv_id,
            provider_id=provider_id,
            status=status,
            registration_authority_code="MCI",
            registration_number_normalized=f"MCI-{reg_suffix}",
            registration_valid_from=now - timedelta(days=365),
            registration_valid_until=now + timedelta(days=365),
            verified_at=now,
            version=1,
        )
        db.add(pv)
        await db.commit()
        await db.refresh(pv)
        return pv


async def _create_facility_verification(
    session_factory,
    facility_id: uuid.UUID,
    *,
    status: str = FacilityVerificationStatus.VERIFIED.value,
) -> FacilityVerification:
    now = datetime.now(timezone.utc)
    fv_id = uuid.uuid4()
    async with session_factory() as db:
        fv = FacilityVerification(
            id=fv_id,
            facility_id=facility_id,
            status=status,
            registration_authority_code="NABH",
            registration_number_normalized="NABH-9988",
            registration_valid_from=now - timedelta(days=365),
            registration_valid_until=now + timedelta(days=365),
            verified_at=now,
            version=1,
            previous_verification_valid=False,
        )
        db.add(fv)
        await db.commit()
        await db.refresh(fv)
        return fv


async def test_01_upgrade_creates_tables_triggers_indexes_constraints(session_factory):
    async with session_factory() as db:
        # Check evidence table exists
        result = await db.scalar(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'provider_trust_verification_evidence'"
            )
        )
        assert result == 1

        # Check facility_verification 9 extended columns exist
        extended_cols = [
            "registration_authority_code",
            "registration_number_normalized",
            "registration_valid_from",
            "registration_valid_until",
            "grace_expires_at",
            "recheck_attempted_at",
            "recheck_failure_reason",
            "previous_verification_valid",
            "authoritative_adverse_signal_at",
        ]
        for col in extended_cols:
            col_exists = await db.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'facility_verification' AND column_name = :c"
                ),
                {"c": col},
            )
            assert col_exists == 1, f"Column {col} missing from facility_verification"

        # Check trigger exists (pg_trigger gives one row per trigger name,
        # unlike information_schema.triggers which gives one row per event type)
        trg_exists = await db.scalar(
            text(
                "SELECT count(*) FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' "
                "AND c.relname = 'provider_trust_verification_evidence' "
                "AND t.tgname = 'trg_provider_trust_verification_evidence_immutable'"
            )
        )
        assert trg_exists == 1

        # Check trigger function exists
        fn_exists = await db.scalar(
            text(
                "SELECT count(*) FROM pg_proc WHERE proname = 'nexa_provider_verification_evidence_immutable'"
            )
        )
        assert fn_exists == 1


async def test_02_evidence_insert_for_professional_verification(session_factory):
    prov = await _create_test_provider(session_factory, email="prof_ev@example.test")
    pv = await _create_professional_verification(session_factory, prov.id)
    now = datetime.now(timezone.utc)
    ev_id = uuid.uuid4()
    digest = "a" * 64

    async with session_factory() as db:
        ev = ProviderTrustVerificationEvidence(
            id=ev_id,
            professional_verification_id=pv.id,
            facility_verification_id=None,
            origin=VerificationEvidenceOrigin.SERVER_REGISTRY_OBSERVATION.value,
            source_id="nmc_india",
            adapter_version="nmc-adapter-1.0.0",
            observed_at=now,
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            source_record_reference="REC-NMC-123456",
            observed_valid_from=now - timedelta(days=100),
            observed_valid_until=now + timedelta(days=365),
            identity_binding_result=VerificationIdentityBindingResult.MATCHED.value,
            binding_method="EXACT_NAME_AND_REGISTRATION",
            response_digest=digest,
            external_transaction_id="TXN-NMC-777",
            observed_resource_version=pv.version,
        )
        db.add(ev)
        await db.commit()

    async with session_factory() as db:
        loaded = await db.get(ProviderTrustVerificationEvidence, ev_id)
        assert loaded is not None
        assert loaded.professional_verification_id == pv.id
        assert loaded.facility_verification_id is None
        assert loaded.origin == "SERVER_REGISTRY_OBSERVATION"
        assert loaded.adapter_version == "nmc-adapter-1.0.0"
        assert loaded.response_digest == digest
        assert loaded.observed_resource_version == 1


async def test_03_evidence_insert_for_facility_verification(session_factory):
    fac = await _create_test_facility(session_factory, name="Fac Evidence Hosp")
    fv = await _create_facility_verification(session_factory, fac.id)
    now = datetime.now(timezone.utc)
    ev_id = uuid.uuid4()

    async with session_factory() as db:
        ev = ProviderTrustVerificationEvidence(
            id=ev_id,
            professional_verification_id=None,
            facility_verification_id=fv.id,
            origin=VerificationEvidenceOrigin.MANUAL_REVIEWER_ATTESTATION.value,
            source_id="manual_reviewer_rev12",
            adapter_version=None,
            observed_at=now,
            lookup_purpose=VerificationEvidenceLookupPurpose.MANUAL_REVIEW.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            source_record_reference="MREV-DOC-999",
            observed_valid_from=now - timedelta(days=30),
            observed_valid_until=now + timedelta(days=365),
            identity_binding_result=VerificationIdentityBindingResult.NOT_EVALUATED.value,
            binding_method=None,
            response_digest=None,
            external_transaction_id=None,
            observed_resource_version=fv.version,
        )
        db.add(ev)
        await db.commit()

    async with session_factory() as db:
        loaded = await db.get(ProviderTrustVerificationEvidence, ev_id)
        assert loaded is not None
        assert loaded.facility_verification_id == fv.id
        assert loaded.professional_verification_id is None
        assert loaded.origin == "MANUAL_REVIEWER_ATTESTATION"
        assert loaded.adapter_version is None


async def test_04_target_xor_constraint_both_set_rejected(session_factory):
    prov = await _create_test_provider(session_factory, email="both_xor@example.test")
    pv = await _create_professional_verification(session_factory, prov.id)
    fac = await _create_test_facility(session_factory, name="XOR Hospital")
    fv = await _create_facility_verification(session_factory, fac.id)
    now = datetime.now(timezone.utc)

    async with session_factory() as db:
        ev = ProviderTrustVerificationEvidence(
            id=uuid.uuid4(),
            professional_verification_id=pv.id,
            facility_verification_id=fv.id,
            origin=VerificationEvidenceOrigin.MANUAL_REVIEWER_ATTESTATION.value,
            source_id="manual_reviewer",
            observed_at=now,
            lookup_purpose=VerificationEvidenceLookupPurpose.MANUAL_REVIEW.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            observed_resource_version=1,
        )
        db.add(ev)
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            await db.commit()
        assert "ck_provider_trust_verification_evidence_resource_target" in str(
            exc_info.value
        )


async def test_05_target_xor_constraint_neither_set_rejected(session_factory):
    now = datetime.now(timezone.utc)

    async with session_factory() as db:
        ev = ProviderTrustVerificationEvidence(
            id=uuid.uuid4(),
            professional_verification_id=None,
            facility_verification_id=None,
            origin=VerificationEvidenceOrigin.MANUAL_REVIEWER_ATTESTATION.value,
            source_id="manual_reviewer",
            observed_at=now,
            lookup_purpose=VerificationEvidenceLookupPurpose.MANUAL_REVIEW.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            observed_resource_version=1,
        )
        db.add(ev)
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            await db.commit()
        assert "ck_provider_trust_verification_evidence_resource_target" in str(
            exc_info.value
        )


async def test_06_foreign_key_violation_rejected(session_factory):
    now = datetime.now(timezone.utc)
    fake_pv_id = uuid.uuid4()

    async with session_factory() as db:
        ev = ProviderTrustVerificationEvidence(
            id=uuid.uuid4(),
            professional_verification_id=fake_pv_id,
            facility_verification_id=None,
            origin=VerificationEvidenceOrigin.MANUAL_REVIEWER_ATTESTATION.value,
            source_id="manual_reviewer",
            observed_at=now,
            lookup_purpose=VerificationEvidenceLookupPurpose.MANUAL_REVIEW.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            observed_resource_version=1,
        )
        db.add(ev)
        with pytest.raises(IntegrityError) as exc_info:
            await db.commit()
        assert (
            "foreign key" in str(exc_info.value).lower()
            or "fk" in str(exc_info.value).lower()
        )


async def test_07_parent_deletion_restricted_by_foreign_key(session_factory):
    prov = await _create_test_provider(
        session_factory, email="fk_restrict@example.test"
    )
    pv = await _create_professional_verification(session_factory, prov.id)
    now = datetime.now(timezone.utc)
    ev_id = uuid.uuid4()

    async with session_factory() as db:
        ev = ProviderTrustVerificationEvidence(
            id=ev_id,
            professional_verification_id=pv.id,
            facility_verification_id=None,
            origin=VerificationEvidenceOrigin.MANUAL_REVIEWER_ATTESTATION.value,
            source_id="reviewer",
            observed_at=now,
            lookup_purpose=VerificationEvidenceLookupPurpose.MANUAL_REVIEW.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            observed_resource_version=1,
        )
        db.add(ev)
        await db.commit()

    # Attempt to delete ProfessionalVerification -> must fail due to ON DELETE RESTRICT
    async with session_factory() as db:
        row = await db.get(ProfessionalVerification, pv.id)
        await db.delete(row)
        with pytest.raises(IntegrityError) as exc_info:
            await db.commit()
        # Verify foreign key restrict violation
        assert (
            "foreign key" in str(exc_info.value).lower()
            or "violates foreign key constraint" in str(exc_info.value).lower()
        )


async def test_08_immutable_trigger_denies_update(session_factory):
    prov = await _create_test_provider(session_factory, email="upd_deny@example.test")
    pv = await _create_professional_verification(session_factory, prov.id)
    now = datetime.now(timezone.utc)
    ev_id = uuid.uuid4()

    async with session_factory() as db:
        ev = ProviderTrustVerificationEvidence(
            id=ev_id,
            professional_verification_id=pv.id,
            facility_verification_id=None,
            origin=VerificationEvidenceOrigin.MANUAL_REVIEWER_ATTESTATION.value,
            source_id="reviewer",
            observed_at=now,
            lookup_purpose=VerificationEvidenceLookupPurpose.MANUAL_REVIEW.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            observed_resource_version=1,
        )
        db.add(ev)
        await db.commit()

    # Attempt UPDATE via raw SQL
    async with session_factory() as db:
        with pytest.raises(DBAPIError) as exc_info:
            await db.execute(
                text(
                    "UPDATE provider_trust_verification_evidence "
                    "SET outcome = 'CONFIRMED_INACTIVE' WHERE id = :id"
                ),
                {"id": ev_id},
            )
            await db.commit()
        assert "PROVIDER_TRUST_VERIFICATION_EVIDENCE_IMMUTABLE" in str(exc_info.value)
        # asyncpg maps ERRCODE 55000 to ObjectNotInPrerequisiteStateError class name
        assert "ObjectNotInPrerequisiteState" in str(exc_info.value)


async def test_09_immutable_trigger_denies_delete(session_factory):
    prov = await _create_test_provider(session_factory, email="del_deny@example.test")
    pv = await _create_professional_verification(session_factory, prov.id)
    now = datetime.now(timezone.utc)
    ev_id = uuid.uuid4()

    async with session_factory() as db:
        ev = ProviderTrustVerificationEvidence(
            id=ev_id,
            professional_verification_id=pv.id,
            facility_verification_id=None,
            origin=VerificationEvidenceOrigin.MANUAL_REVIEWER_ATTESTATION.value,
            source_id="reviewer",
            observed_at=now,
            lookup_purpose=VerificationEvidenceLookupPurpose.MANUAL_REVIEW.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            observed_resource_version=1,
        )
        db.add(ev)
        await db.commit()

    # Attempt DELETE via raw SQL
    async with session_factory() as db:
        with pytest.raises(DBAPIError) as exc_info:
            await db.execute(
                text("DELETE FROM provider_trust_verification_evidence WHERE id = :id"),
                {"id": ev_id},
            )
            await db.commit()
        assert "PROVIDER_TRUST_VERIFICATION_EVIDENCE_IMMUTABLE" in str(exc_info.value)
        # asyncpg maps ERRCODE 55000 to ObjectNotInPrerequisiteStateError class name
        assert "ObjectNotInPrerequisiteState" in str(exc_info.value)


async def test_10_observed_resource_version_must_be_positive(session_factory):
    prov = await _create_test_provider(session_factory, email="ver_pos@example.test")
    pv = await _create_professional_verification(session_factory, prov.id)
    now = datetime.now(timezone.utc)

    async with session_factory() as db:
        ev = ProviderTrustVerificationEvidence(
            id=uuid.uuid4(),
            professional_verification_id=pv.id,
            facility_verification_id=None,
            origin=VerificationEvidenceOrigin.MANUAL_REVIEWER_ATTESTATION.value,
            source_id="reviewer",
            observed_at=now,
            lookup_purpose=VerificationEvidenceLookupPurpose.MANUAL_REVIEW.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            observed_resource_version=0,  # Invalid: must be >= 1
        )
        db.add(ev)
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            await db.commit()
        assert "ck_ptve_observed_resource_version" in str(exc_info.value)


async def test_11_server_observation_requires_non_empty_adapter_version(
    session_factory,
):
    prov = await _create_test_provider(session_factory, email="adp_ver@example.test")
    pv = await _create_professional_verification(session_factory, prov.id)
    now = datetime.now(timezone.utc)

    # 1. Null adapter_version with SERVER_REGISTRY_OBSERVATION fails
    async with session_factory() as db:
        ev = ProviderTrustVerificationEvidence(
            id=uuid.uuid4(),
            professional_verification_id=pv.id,
            facility_verification_id=None,
            origin=VerificationEvidenceOrigin.SERVER_REGISTRY_OBSERVATION.value,
            source_id="registry_api",
            adapter_version=None,  # Invalid for server observation
            observed_at=now,
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            observed_resource_version=1,
        )
        db.add(ev)
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            await db.commit()
        assert "ck_provider_trust_verification_evidence_adapter_version_origin" in str(
            exc_info.value
        )

    # 2. Whitespace adapter_version with SERVER_REGISTRY_OBSERVATION fails
    async with session_factory() as db:
        ev2 = ProviderTrustVerificationEvidence(
            id=uuid.uuid4(),
            professional_verification_id=pv.id,
            facility_verification_id=None,
            origin=VerificationEvidenceOrigin.SERVER_REGISTRY_OBSERVATION.value,
            source_id="registry_api",
            adapter_version="   ",  # Invalid: length(trim()) == 0
            observed_at=now,
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            observed_resource_version=1,
        )
        db.add(ev2)
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            await db.commit()
        assert "ck_provider_trust_verification_evidence_adapter_version_origin" in str(
            exc_info.value
        )


async def test_12_evidence_digest_length_constraint(session_factory):
    """Verify canonical lowercase hex SHA-256 regex constraint (Gate 2)."""
    import hashlib

    prov = await _create_test_provider(session_factory, email="dig_chk@example.test")
    pv = await _create_professional_verification(session_factory, prov.id)
    now = datetime.now(timezone.utc)

    valid_sha256 = hashlib.sha256(
        b"authoritative-registry-response-payload"
    ).hexdigest()

    # 1. Valid lowercase hex SHA-256 succeeds
    async with session_factory() as db:
        ev_valid = ProviderTrustVerificationEvidence(
            id=uuid.uuid4(),
            professional_verification_id=pv.id,
            facility_verification_id=None,
            origin=VerificationEvidenceOrigin.MANUAL_REVIEWER_ATTESTATION.value,
            source_id="reviewer",
            observed_at=now,
            lookup_purpose=VerificationEvidenceLookupPurpose.MANUAL_REVIEW.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            response_digest=valid_sha256,
            observed_resource_version=1,
        )
        db.add(ev_valid)
        await db.commit()

    # 2. Uppercase hex fails canonical lowercase regex constraint
    async with session_factory() as db:
        ev_upper = ProviderTrustVerificationEvidence(
            id=uuid.uuid4(),
            professional_verification_id=pv.id,
            facility_verification_id=None,
            origin=VerificationEvidenceOrigin.MANUAL_REVIEWER_ATTESTATION.value,
            source_id="reviewer",
            observed_at=now,
            lookup_purpose=VerificationEvidenceLookupPurpose.MANUAL_REVIEW.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            response_digest="A" * 64,
            observed_resource_version=1,
        )
        db.add(ev_upper)
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            await db.commit()
        assert "ck_provider_trust_verification_evidence_response_digest" in str(
            exc_info.value
        )

    # 3. Non-hex characters fail constraint
    async with session_factory() as db:
        ev_nonhex = ProviderTrustVerificationEvidence(
            id=uuid.uuid4(),
            professional_verification_id=pv.id,
            facility_verification_id=None,
            origin=VerificationEvidenceOrigin.MANUAL_REVIEWER_ATTESTATION.value,
            source_id="reviewer",
            observed_at=now,
            lookup_purpose=VerificationEvidenceLookupPurpose.MANUAL_REVIEW.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            response_digest="z" * 64,
            observed_resource_version=1,
        )
        db.add(ev_nonhex)
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            await db.commit()
        assert "ck_provider_trust_verification_evidence_response_digest" in str(
            exc_info.value
        )

    # 4. Digest shorter than 64 characters fails
    async with session_factory() as db:
        ev_short = ProviderTrustVerificationEvidence(
            id=uuid.uuid4(),
            professional_verification_id=pv.id,
            facility_verification_id=None,
            origin=VerificationEvidenceOrigin.MANUAL_REVIEWER_ATTESTATION.value,
            source_id="reviewer",
            observed_at=now,
            lookup_purpose=VerificationEvidenceLookupPurpose.MANUAL_REVIEW.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            response_digest="a" * 63,
            observed_resource_version=1,
        )
        db.add(ev_short)
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            await db.commit()
        assert "ck_provider_trust_verification_evidence_response_digest" in str(
            exc_info.value
        )


async def test_13_validity_interval_constraint(session_factory):
    prov = await _create_test_provider(session_factory, email="val_chk@example.test")
    pv = await _create_professional_verification(session_factory, prov.id)
    now = datetime.now(timezone.utc)

    # valid_until < valid_from fails
    async with session_factory() as db:
        ev = ProviderTrustVerificationEvidence(
            id=uuid.uuid4(),
            professional_verification_id=pv.id,
            facility_verification_id=None,
            origin=VerificationEvidenceOrigin.MANUAL_REVIEWER_ATTESTATION.value,
            source_id="reviewer",
            observed_at=now,
            lookup_purpose=VerificationEvidenceLookupPurpose.MANUAL_REVIEW.value,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE.value,
            observed_valid_from=now + timedelta(days=10),
            observed_valid_until=now - timedelta(days=10),  # until < from
            observed_resource_version=1,
        )
        db.add(ev)
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            await db.commit()
        assert "ck_provider_trust_verification_evidence_validity_interval" in str(
            exc_info.value
        )


async def test_14_facility_verification_extended_fields_persistence_and_check_constraints(
    session_factory,
):
    fac = await _create_test_facility(session_factory, name="Extended Fields Hosp")
    fv_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    v_from = now - timedelta(days=365)
    v_until = now + timedelta(days=365)
    recheck_at = now - timedelta(days=1)
    adverse_at = now - timedelta(days=2)

    async with session_factory() as db:
        fv = FacilityVerification(
            id=fv_id,
            facility_id=fac.id,
            status=FacilityVerificationStatus.VERIFIED.value,
            registration_authority_code="NABH",
            registration_number_normalized="NABH-9988",
            registration_valid_from=v_from,
            registration_valid_until=v_until,
            grace_expires_at=None,
            recheck_attempted_at=recheck_at,
            recheck_failure_reason="REVIEW_REQUIRED",
            previous_verification_valid=True,
            authoritative_adverse_signal_at=adverse_at,
            version=1,
        )
        db.add(fv)
        await db.commit()

    async with session_factory() as db:
        loaded = await db.get(FacilityVerification, fv_id)
        assert loaded is not None
        assert loaded.registration_authority_code == "NABH"
        assert loaded.registration_number_normalized == "NABH-9988"
        assert loaded.recheck_failure_reason == "REVIEW_REQUIRED"
        assert loaded.previous_verification_valid is True
        assert loaded.authoritative_adverse_signal_at is not None

    # Invalid recheck_failure_reason check constraint
    fac2 = await _create_test_facility(session_factory, name="Bad Recheck Hosp")
    async with session_factory() as db:
        bad_fv = FacilityVerification(
            id=uuid.uuid4(),
            facility_id=fac2.id,
            status=FacilityVerificationStatus.PENDING_VERIFICATION.value,
            recheck_failure_reason="NOT_A_VALID_REASON",
            version=1,
        )
        db.add(bad_fv)
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            await db.commit()
        assert "ck_facility_verification_recheck_failure_reason" in str(exc_info.value)


async def test_15_facility_recheck_required_clinical_eligibility_denial(
    session_factory,
):
    """Verify that facility RECHECK_REQUIRED denies clinical eligibility."""
    fac = await _create_test_facility(session_factory, name="Recheck Eligibility Hosp")
    fv_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # Create facility verification in RECHECK_REQUIRED
    async with session_factory() as db:
        fv = FacilityVerification(
            id=fv_id,
            facility_id=fac.id,
            status="RECHECK_REQUIRED",
            registration_authority_code="NABH",
            registration_number_normalized="NABH-7711",
            version=1,
            previous_verification_valid=True,
        )
        db.add(fv)
        await db.commit()

    # Create active provider with active credential, verified contacts, verified professional trust, and affiliation
    prov = await _create_test_provider(session_factory, email="elig_check@example.test")
    await _create_professional_verification(session_factory, prov.id, status="VERIFIED")

    async with session_factory() as db:
        affil = ProviderHospitalAffiliation(
            id=uuid.uuid4(),
            provider_id=prov.id,
            hospital_id=fac.id,
            trust_status="ACTIVE",
            roles=["clinician"],
            version=1,
        )
        db.add(affil)
        await db.commit()

    # Now evaluate clinical eligibility — use the actual evaluate_interactive API:
    # ClinicalEligibilityService(contact_assurance_policy=...) and
    # svc.evaluate_interactive(db, provider_identity, auth, capability, now=now)
    auth = InteractiveClinicalAuthentication(
        provider_id=prov.id,
        hospital_id=fac.id,
        method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
        session_authenticated=True,
        mfa_verified_at=now,
    )

    async with session_factory() as db:
        provider_identity = await db.get(ProviderIdentity, prov.id)
        svc = ClinicalEligibilityService(
            contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
        )
        decision = await svc.evaluate_interactive(
            db,
            provider_identity,
            auth,
            ClinicalCapability.RECORD_READ,
            now=now,
        )
        assert decision.allowed is False
        # FACILITY_RECHECK_REQUIRED or FACILITY_NOT_VERIFIED depending on service logic
        assert decision.denial_code is not None
        assert decision.denial_code.value in (
            "FACILITY_NOT_VERIFIED",
            "FACILITY_RECHECK_REQUIRED",
            "FACILITY_SUSPENDED",
            "FACILITY_CLOSED",
        )


async def test_16_migration_downgrade_and_reupgrade(session_factory):
    """Test clean downgrade to 20260903_trust_authorization (pre-evidence) and re-upgrade to 20260904_verification_evidence."""
    db_url = _get_db_url()
    cfg = _config(db_url)

    # Downgrade past the evidence migration to PREV_PREV_HEAD
    await asyncio.to_thread(command.downgrade, cfg, PREV_PREV_HEAD)

    # Verify table and trigger no longer exist
    async with session_factory() as db:
        tbl_count = await db.scalar(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'provider_trust_verification_evidence'"
            )
        )
        assert tbl_count == 0

        trg_count = await db.scalar(
            text(
                "SELECT count(*) FROM information_schema.triggers "
                "WHERE trigger_schema = 'public' AND trigger_name = 'trg_provider_trust_verification_evidence_immutable'"
            )
        )
        assert trg_count == 0

        fn_count = await db.scalar(
            text(
                "SELECT count(*) FROM pg_proc WHERE proname = 'nexa_provider_verification_evidence_immutable'"
            )
        )
        assert fn_count == 0

        # Verify added columns dropped from facility_verification
        col_count = await db.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'facility_verification' AND column_name = 'registration_authority_code'"
            )
        )
        assert col_count == 0

        # Create a provider and facility at PREVIOUS_HEAD with pre-5B manual verification fields
        prov_id = uuid.uuid4()
        fac_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        await db.execute(
            text(
                "INSERT INTO provider_identity (id, provider_uid, role, display_name, contact_email, contact_phone, status, is_active) "
                "VALUES (:id, :puid, 'clinician', 'Dr. Pre-5B', :email, '+919999988888', 'active', true)"
            ),
            {
                "id": prov_id,
                "puid": f"puid-{prov_id.hex[:8]}",
                "email": f"pre5b-{prov_id.hex[:8]}@example.test",
            },
        )
        await db.execute(
            text(
                "INSERT INTO hospital_registry (id, facility_code, legal_name, display_name, country_code, is_active) "
                "VALUES (:id, :code, 'Pre-5B Hospital', 'Pre-5B Hospital', 'IN', true)"
            ),
            {"id": fac_id, "code": f"FAC-{fac_id.hex[:8]}"},
        )
        # Insert pre-5B professional verification with legacy manual verification fields
        pv_id = uuid.uuid4()
        await db.execute(
            text(
                "INSERT INTO professional_verification (id, provider_id, status, registration_authority_code, "
                "registration_number_normalized, verification_method, verification_source, verification_reference, "
                "registration_valid_from, registration_valid_until, verified_at, previous_verification_valid, version) "
                "VALUES (:id, :prov_id, 'VERIFIED', 'MCI', :reg_num, 'MANUAL', 'MANUAL_COUNCIL_SEARCH', 'REF-PRE-5B', "
                ":v_from, :v_until, :now, false, 1)"
            ),
            {
                "id": pv_id,
                "prov_id": prov_id,
                "reg_num": f"REG-{pv_id.hex[:8]}",
                "v_from": now - timedelta(days=100),
                "v_until": now + timedelta(days=200),
                "now": now,
            },
        )
        # Insert pre-5B facility verification (no Phase 5B columns exist at PREVIOUS_HEAD)
        fv_id = uuid.uuid4()
        await db.execute(
            text(
                "INSERT INTO facility_verification (id, facility_id, status, verified_at, version) "
                "VALUES (:id, :fac_id, 'VERIFIED', :now, 1)"
            ),
            {"id": fv_id, "fac_id": fac_id, "now": now},
        )
        await db.commit()

    # Re-upgrade to PREVIOUS_HEAD (20260904_verification_evidence) to verify evidence migration
    await asyncio.to_thread(command.upgrade, cfg, PREVIOUS_HEAD)

    # Verify table and trigger exist again, rows survive intact, and ZERO evidence rows backfilled
    async with session_factory() as db:
        tbl_count = await db.scalar(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'provider_trust_verification_evidence'"
            )
        )
        assert tbl_count == 1

        # Gate 7: ZERO ProviderTrustVerificationEvidence rows backfilled
        ev_count = await db.scalar(
            text("SELECT count(*) FROM provider_trust_verification_evidence")
        )
        assert ev_count == 0, f"Expected 0 backfilled evidence rows, got {ev_count}"

        # Pre-5B professional verification survived intact with its manual provenance
        pv_row = (
            (
                await db.execute(
                    text(
                        "SELECT status, verification_source, verification_reference "
                        "FROM professional_verification WHERE id = :id"
                    ),
                    {"id": pv_id},
                )
            )
            .mappings()
            .one()
        )
        assert pv_row["status"] == "VERIFIED"
        assert pv_row["verification_source"] == "MANUAL_COUNCIL_SEARCH"
        assert pv_row["verification_reference"] == "REF-PRE-5B"

        # Pre-5B facility verification survived with default previous_verification_valid=False and nulls
        fv_row = (
            (
                await db.execute(
                    text(
                        "SELECT status, previous_verification_valid, registration_authority_code, recheck_failure_reason "
                        "FROM facility_verification WHERE id = :id"
                    ),
                    {"id": fv_id},
                )
            )
            .mappings()
            .one()
        )
        assert fv_row["status"] == "VERIFIED"
        assert fv_row["previous_verification_valid"] is False
        assert fv_row["registration_authority_code"] is None
        assert fv_row["recheck_failure_reason"] is None
