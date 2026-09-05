"""Real PostgreSQL 16 qualification for Milestone 1B.1.

Proves on a live disposable PostgreSQL database:
1. Migration lifecycle: upgrade to current head, downgrade to previous head, re-upgrade
2. Foreign key and CHECK constraints:
   - patient_profiles.patient_id -> patients.patient_uuid (RESTRICT)
   - patient_legal_acceptances.patient_id -> patients.patient_uuid (RESTRICT)
   - ck_legal_acceptances_document_type (rejects invalid document_type)
   - ck_legal_acceptances_sha256_hex (rejects non-64-hex lowercase)
   - uq_legal_acceptance_patient_doc_version uniqueness
3. Profile concurrency serialization on first write
4. Transactional atomicity:
   - Staged DEK + Profile + Audit outbox rolled back together on failure
   - Multi-document legal acceptance rollback on partial conflict
5. Erasure durability:
   - DEK destruction denies profile decryption
   - Legal acceptance rows survive DEK destruction
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextvars import ContextVar
from datetime import date

from urllib.parse import urlsplit

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.dek_store import PatientDEKStore
from app.models.patient import Patient
from app.models.patient_auth_identity import PatientAuthIdentity
from app.models.patient_legal_acceptance import PatientLegalAcceptance
from app.models.patient_profile import PatientProfile
from app.services.crypto_kms import (
    EncryptionError,
    LocalEnvelopeProvider,
    PatientDataErased,
)
from app.services.patient_legal_service import (
    LegalAcceptanceError,
    accept_legal_documents,
    get_onboarding_status,
)
from app.services.patient_profile_service import (
    create_or_update_profile,
    get_profile,
)
from tests.helpers.qualification_infra import (
    create_disposable_database,
    drop_disposable_database,
    normalize_async_postgres_url,
    normalize_sync_postgres_url,
    postgres_database_url,
    require_disposable_database_name,
    require_loopback_postgres_url,
)

pytestmark = pytest.mark.postgres

PREVIOUS_HEAD = "20260819_widen_vault_pii_columns"
CURRENT_HEAD = "20260819_patient_profile_legal"


def _url() -> str:
    value = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL") or ""
    if not value:
        value = postgres_database_url("nexa_qual_ci_shared")
    normalized = normalize_async_postgres_url(value)
    require_loopback_postgres_url(normalized)
    require_disposable_database_name(urlsplit(normalized).path.lstrip("/"))
    return normalized


@pytest.fixture
def env_setup():
    from unittest.mock import patch

    with patch.dict(
        os.environ,
        {
            "KEK_ROOT_SECRET": "test-root-secret-long-enough-32-chars-!!",
            "ENCRYPTION_BACKEND": "local",
            "PATIENT_TERMS_VERSION": "2026.1",
            "PATIENT_TERMS_SHA256": "a" * 64,
            "PATIENT_TERMS_URL": "https://legal.nexa.test/terms/2026.1",
            "PATIENT_PRIVACY_VERSION": "2026.1",
            "PATIENT_PRIVACY_SHA256": "b" * 64,
            "PATIENT_PRIVACY_URL": "https://legal.nexa.test/privacy/2026.1",
            "PATIENT_JWT_SECRET": "test-secret-at-least-32-chars-long-here!!",
        },
    ):
        yield


async def _cleanup(factory) -> None:
    async with factory() as db:
        await db.execute(text("DELETE FROM public.audit_outbox"))
        await db.execute(delete(PatientLegalAcceptance))
        await db.execute(delete(PatientProfile))
        await db.execute(delete(PatientAuthIdentity))
        await db.execute(delete(PatientDEKStore))
        await db.execute(delete(Patient))
        await db.commit()


async def _audit_event_count(db, patient_id: uuid.UUID, event_type: str) -> int:
    return int(
        await db.scalar(
            text(
                "SELECT count(*) FROM public.audit_outbox "
                "WHERE patient_id = :patient_id AND event_type = :event_type"
            ),
            {"patient_id": str(patient_id), "event_type": event_type},
        )
        or 0
    )


async def _profile_count(db, patient_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            select(text("count(*)"))
            .select_from(PatientProfile)
            .where(PatientProfile.patient_id == patient_id)
        )
        or 0
    )


async def _legal_count(db, patient_id: uuid.UUID, document_type: str) -> int:
    return int(
        await db.scalar(
            select(text("count(*)"))
            .select_from(PatientLegalAcceptance)
            .where(
                PatientLegalAcceptance.patient_id == patient_id,
                PatientLegalAcceptance.document_type == document_type,
            )
        )
        or 0
    )


@pytest.mark.asyncio
async def test_postgres_patient_profile_legal_migration_lifecycle(
    env_setup, monkeypatch
):
    """Prove clean upgrade, downgrade, and re-upgrade on PostgreSQL."""
    db_name = "nexa_qual_profile_legal_mig"
    url = await create_disposable_database(db_name)
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", normalize_sync_postgres_url(url))

    engine = None
    try:
        # Upgrade to previous head, then to current head
        await asyncio.to_thread(command.upgrade, alembic_cfg, PREVIOUS_HEAD)
        await asyncio.to_thread(command.upgrade, alembic_cfg, CURRENT_HEAD)

        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        # Create test patient
        pid = uuid.uuid4()
        async with factory() as db:
            await db.execute(
                text(
                    "INSERT INTO patients (patient_uuid, is_deleted) VALUES (:pid, FALSE)"
                ),
                {"pid": pid},
            )
            await db.commit()

        # Insert valid profile and legal acceptance
        async with factory() as db:
            db.add(
                PatientProfile(
                    patient_id=pid,
                    full_name_encrypted="test_encrypted_name:1",
                    date_of_birth_encrypted="test_encrypted_dob:1",
                )
            )
            db.add(
                PatientLegalAcceptance(
                    patient_id=pid,
                    document_type="TERMS_OF_SERVICE",
                    document_version="2026.1",
                    document_sha256="a" * 64,
                )
            )
            await db.commit()

        # Verify rows exist
        async with factory() as db:
            profile = await db.scalar(
                select(PatientProfile).where(PatientProfile.patient_id == pid)
            )
            assert profile is not None
            acceptance = await db.scalar(
                select(PatientLegalAcceptance).where(
                    PatientLegalAcceptance.patient_id == pid
                )
            )
            assert acceptance is not None

        # Clean up before downgrade
        await _cleanup(factory)

        # Downgrade to PREVIOUS_HEAD and re-upgrade
        await asyncio.to_thread(command.downgrade, alembic_cfg, PREVIOUS_HEAD)
        await asyncio.to_thread(command.upgrade, alembic_cfg, CURRENT_HEAD)
    finally:
        if engine is not None:
            await engine.dispose()
        await drop_disposable_database(db_name)


@pytest.mark.asyncio
async def test_postgres_schema_constraints(env_setup, monkeypatch):
    """Prove foreign keys, CHECK constraints, and uniqueness on PostgreSQL."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", url)
    await asyncio.to_thread(command.upgrade, alembic_cfg, CURRENT_HEAD)

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        pid = uuid.uuid4()
        async with factory() as db:
            db.add(Patient(patient_uuid=pid, is_deleted=False))
            await db.commit()

        # FK constraint: non-existent patient cannot have profile
        fake_pid = uuid.uuid4()
        async with factory() as db:
            db.add(
                PatientProfile(
                    patient_id=fake_pid,
                    full_name_encrypted="enc:1",
                    date_of_birth_encrypted="enc:1",
                )
            )
            with pytest.raises(IntegrityError):
                await db.commit()
            await db.rollback()

        # FK constraint: non-existent patient cannot have legal acceptance
        async with factory() as db:
            db.add(
                PatientLegalAcceptance(
                    patient_id=fake_pid,
                    document_type="TERMS_OF_SERVICE",
                    document_version="2026.1",
                    document_sha256="a" * 64,
                )
            )
            with pytest.raises(IntegrityError):
                await db.commit()
            await db.rollback()

        # CHECK constraint: invalid document_type rejected
        async with factory() as db:
            db.add(
                PatientLegalAcceptance(
                    patient_id=pid,
                    document_type="INVALID_TYPE",
                    document_version="2026.1",
                    document_sha256="a" * 64,
                )
            )
            with pytest.raises(IntegrityError):
                await db.commit()
            await db.rollback()

        # CHECK constraint: invalid sha256 hex rejected (uppercase, wrong length)
        async with factory() as db:
            db.add(
                PatientLegalAcceptance(
                    patient_id=pid,
                    document_type="TERMS_OF_SERVICE",
                    document_version="2026.1",
                    document_sha256="A" * 64,  # uppercase not allowed
                )
            )
            with pytest.raises(IntegrityError):
                await db.commit()
            await db.rollback()

        async with factory() as db:
            db.add(
                PatientLegalAcceptance(
                    patient_id=pid,
                    document_type="TERMS_OF_SERVICE",
                    document_version="2026.1",
                    document_sha256="tooshort",
                )
            )
            with pytest.raises(IntegrityError):
                await db.commit()
            await db.rollback()

        # UNIQUE constraint: duplicate (patient_id, document_type, document_version) rejected
        async with factory() as db:
            db.add(
                PatientLegalAcceptance(
                    patient_id=pid,
                    document_type="TERMS_OF_SERVICE",
                    document_version="2026.1",
                    document_sha256="a" * 64,
                )
            )
            await db.commit()

        async with factory() as db:
            db.add(
                PatientLegalAcceptance(
                    patient_id=pid,
                    document_type="TERMS_OF_SERVICE",
                    document_version="2026.1",
                    document_sha256="b" * 64,
                )
            )
            with pytest.raises(IntegrityError):
                await db.commit()
            await db.rollback()

        await _cleanup(factory)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_profile_transactional_atomicity(env_setup, monkeypatch):
    """Prove that profile creation + DEK + audit rollback together on error."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", url)
    await asyncio.to_thread(command.upgrade, alembic_cfg, CURRENT_HEAD)

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        pid = uuid.uuid4()
        pid_str = str(pid)
        async with factory() as db:
            db.add(Patient(patient_uuid=pid, is_deleted=False))
            await db.commit()

        class _SyntheticFailure(Exception):
            pass

        # Simulate profile creation that rolls back before commit
        with pytest.raises(_SyntheticFailure):
            async with factory() as db:
                await create_or_update_profile(
                    pid_str, "Aarav Sharma", date(1990, 5, 15), db
                )
                raise _SyntheticFailure("Simulated outbox/commit failure")

        # Verify NOTHING was persisted (profile row absent, staged DEK absent)
        async with factory() as db:
            profile = await db.scalar(
                select(PatientProfile).where(PatientProfile.patient_id == pid)
            )
            assert profile is None

            dek = await db.scalar(
                select(PatientDEKStore).where(PatientDEKStore.patient_id == pid)
            )
            assert dek is None
            assert await _audit_event_count(db, pid, "PATIENT_PROFILE_CREATED") == 0

        # Now do successful creation and commit
        async with factory() as db:
            pdata, created = await create_or_update_profile(
                pid_str, "Aarav Sharma", date(1990, 5, 15), db
            )
            assert created is True
            await db.commit()

        # Verify durable state
        async with factory() as db:
            profile = await db.scalar(
                select(PatientProfile).where(PatientProfile.patient_id == pid)
            )
            assert profile is not None
            assert "Aarav Sharma" not in profile.full_name_encrypted
            assert await _audit_event_count(db, pid, "PATIENT_PROFILE_CREATED") == 1

            # Read back and decrypt
            read_data = await get_profile(pid_str, db)
            assert read_data is not None
            assert read_data.full_name == "Aarav Sharma"
            assert read_data.date_of_birth == "1990-05-15"

        # Update profile
        async with factory() as db:
            pdata, created = await create_or_update_profile(
                pid_str, "Aarav K. Sharma", date(1990, 5, 15), db
            )
            assert created is False
            await db.commit()

        async with factory() as db:
            read_data = await get_profile(pid_str, db)
            assert read_data.full_name == "Aarav K. Sharma"

        await _cleanup(factory)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_profile_first_write_concurrency_has_one_profile_dek_and_audit(
    env_setup, monkeypatch
):
    """Two same-profile first writes serialize on PostgreSQL's patient-row lock."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", url)
    await asyncio.to_thread(command.upgrade, alembic_cfg, CURRENT_HEAD)

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        pid = uuid.uuid4()
        async with factory() as db:
            db.add(Patient(patient_uuid=pid, is_deleted=False))
            await db.commit()

        async def _write_once() -> bool:
            async with factory() as db:
                _data, created = await create_or_update_profile(
                    str(pid), "Aarav Sharma", date(1990, 5, 15), db
                )
                await db.commit()
                return created

        created_results = await asyncio.gather(_write_once(), _write_once())
        assert sorted(created_results) == [False, True]
        async with factory() as db:
            assert await _profile_count(db, pid) == 1
            assert (
                int(
                    await db.scalar(
                        select(text("count(*)"))
                        .select_from(PatientDEKStore)
                        .where(
                            PatientDEKStore.patient_id == pid,
                            PatientDEKStore.is_active.is_(True),
                            PatientDEKStore.destroyed_at.is_(None),
                        )
                    )
                    or 0
                )
                == 1
            )
            assert await _audit_event_count(db, pid, "PATIENT_PROFILE_CREATED") == 1

        await _cleanup(factory)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_legal_acceptance_atomic_rollback(env_setup, monkeypatch):
    """Prove multi-document legal acceptance atomicity on PostgreSQL."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", url)
    await asyncio.to_thread(command.upgrade, alembic_cfg, CURRENT_HEAD)

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        pid = uuid.uuid4()
        pid_str = str(pid)
        async with factory() as db:
            db.add(Patient(patient_uuid=pid, is_deleted=False))
            # Seed a conflicting privacy notice acceptance with different digest
            db.add(
                PatientLegalAcceptance(
                    patient_id=pid,
                    document_type="PRIVACY_NOTICE",
                    document_version="2026.1",
                    document_sha256="c" * 64,  # conflict! (expected 'b'*64)
                )
            )
            await db.commit()

        # Request to accept TERMS_OF_SERVICE and PRIVACY_NOTICE
        # TERMS_OF_SERVICE is valid, but PRIVACY_NOTICE has digest conflict
        async with factory() as db:
            with pytest.raises(LegalAcceptanceError) as exc:
                await accept_legal_documents(
                    pid_str, ["TERMS_OF_SERVICE", "PRIVACY_NOTICE"], db
                )
            assert exc.value.code == "LEGAL_DOCUMENT_VERSION_DIGEST_CONFLICT"
            await db.rollback()

        # Verify NO new terms acceptance survived (entire request rolled back)
        async with factory() as db:
            terms = await db.scalar(
                select(PatientLegalAcceptance).where(
                    PatientLegalAcceptance.patient_id == pid,
                    PatientLegalAcceptance.document_type == "TERMS_OF_SERVICE",
                )
            )
            assert terms is None
            assert await _audit_event_count(db, pid, "PATIENT_TERMS_ACCEPTED") == 0

        # Inverse ordering test: PRIVACY_NOTICE first, then TERMS_OF_SERVICE
        async with factory() as db:
            with pytest.raises(LegalAcceptanceError) as exc:
                await accept_legal_documents(
                    pid_str, ["PRIVACY_NOTICE", "TERMS_OF_SERVICE"], db
                )
            assert exc.value.code == "LEGAL_DOCUMENT_VERSION_DIGEST_CONFLICT"
            await db.rollback()

        async with factory() as db:
            terms = await db.scalar(
                select(PatientLegalAcceptance).where(
                    PatientLegalAcceptance.patient_id == pid,
                    PatientLegalAcceptance.document_type == "TERMS_OF_SERVICE",
                )
            )
            assert terms is None

        await _cleanup(factory)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_legal_identical_concurrency_has_one_acceptance_and_one_audit(
    env_setup, monkeypatch
):
    """The uniqueness constraint plus savepoint handles cross-session retries."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", url)
    await asyncio.to_thread(command.upgrade, alembic_cfg, CURRENT_HEAD)

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        pid = uuid.uuid4()
        barrier = asyncio.Barrier(2)
        async with factory() as db:
            db.add(Patient(patient_uuid=pid, is_deleted=False))
            await db.commit()

        async def _accept_once() -> str:
            async with factory() as db:
                original_execute = db.execute
                paused = False

                async def _execute(statement, *args, **kwargs):
                    nonlocal paused
                    if not paused and "FROM patient_legal_acceptances" in str(
                        statement
                    ):
                        paused = True
                        await barrier.wait()
                    return await original_execute(statement, *args, **kwargs)

                db.execute = _execute
                await accept_legal_documents(str(pid), ["TERMS_OF_SERVICE"], db)
                await db.commit()
                return "ok"

        assert await asyncio.gather(_accept_once(), _accept_once()) == ["ok", "ok"]
        async with factory() as db:
            assert await _legal_count(db, pid, "TERMS_OF_SERVICE") == 1
            assert await _audit_event_count(db, pid, "PATIENT_TERMS_ACCEPTED") == 1

        await _cleanup(factory)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_legal_conflicting_concurrency_returns_stable_conflict(
    env_setup, monkeypatch
):
    """Concurrent same-version/different-digest acceptance fails closed."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", url)
    await asyncio.to_thread(command.upgrade, alembic_cfg, CURRENT_HEAD)

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    config_for_task: ContextVar = ContextVar("config_for_task")
    from app.core.config import PatientLegalDocumentConfig

    config_a = PatientLegalDocumentConfig(
        terms_version="2026.1",
        terms_sha256="a" * 64,
        terms_url="https://legal.nexa.test/terms/2026.1",
        privacy_version="2026.1",
        privacy_sha256="b" * 64,
        privacy_url="https://legal.nexa.test/privacy/2026.1",
    )
    config_b = PatientLegalDocumentConfig(
        terms_version="2026.1",
        terms_sha256="c" * 64,
        terms_url="https://legal.nexa.test/terms/2026.1",
        privacy_version="2026.1",
        privacy_sha256="b" * 64,
        privacy_url="https://legal.nexa.test/privacy/2026.1",
    )
    monkeypatch.setattr(
        "app.services.patient_legal_service.get_patient_legal_config",
        lambda: config_for_task.get(),
    )
    try:
        pid = uuid.uuid4()
        barrier = asyncio.Barrier(2)
        async with factory() as db:
            db.add(Patient(patient_uuid=pid, is_deleted=False))
            await db.commit()

        async def _accept_once(config) -> str:
            context_token = config_for_task.set(config)
            try:
                async with factory() as db:
                    original_execute = db.execute
                    paused = False

                    async def _execute(statement, *args, **kwargs):
                        nonlocal paused
                        if not paused and "FROM patient_legal_acceptances" in str(
                            statement
                        ):
                            paused = True
                            await barrier.wait()
                        return await original_execute(statement, *args, **kwargs)

                    db.execute = _execute
                    try:
                        await accept_legal_documents(str(pid), ["TERMS_OF_SERVICE"], db)
                        await db.commit()
                        return "ok"
                    except LegalAcceptanceError as exc:
                        await db.rollback()
                        return exc.code
            finally:
                config_for_task.reset(context_token)

        results = await asyncio.gather(_accept_once(config_a), _accept_once(config_b))
        assert sorted(results) == ["LEGAL_DOCUMENT_VERSION_DIGEST_CONFLICT", "ok"]
        async with factory() as db:
            assert await _legal_count(db, pid, "TERMS_OF_SERVICE") == 1
            assert await _audit_event_count(db, pid, "PATIENT_TERMS_ACCEPTED") == 1

        await _cleanup(factory)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_maximum_legal_version_uses_bounded_audit_key(
    env_setup, monkeypatch
):
    """A schema-maximum legal version cannot overflow audit_outbox.idempotency_key."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", url)
    await asyncio.to_thread(command.upgrade, alembic_cfg, CURRENT_HEAD)
    monkeypatch.setenv("PATIENT_TERMS_VERSION", "v" * 64)

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        pid = uuid.uuid4()
        async with factory() as db:
            db.add(Patient(patient_uuid=pid, is_deleted=False))
            await db.commit()

        async with factory() as db:
            await accept_legal_documents(str(pid), ["TERMS_OF_SERVICE"], db)
            await db.commit()

        async with factory() as db:
            acceptance = await db.scalar(
                select(PatientLegalAcceptance).where(
                    PatientLegalAcceptance.patient_id == pid,
                    PatientLegalAcceptance.document_type == "TERMS_OF_SERVICE",
                )
            )
            assert acceptance is not None
            assert acceptance.document_version == "v" * 64
            audit_key_length = await db.scalar(
                text(
                    "SELECT length(idempotency_key) FROM public.audit_outbox "
                    "WHERE patient_id = :patient_id AND event_type = :event_type"
                ),
                {
                    "patient_id": str(pid),
                    "event_type": "PATIENT_TERMS_ACCEPTED",
                },
            )
            assert audit_key_length == 70

        await _cleanup(factory)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_erasure_durability(env_setup, monkeypatch):
    """Prove DEK destruction blocks profile read, while legal acceptances survive."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", url)
    await asyncio.to_thread(command.upgrade, alembic_cfg, CURRENT_HEAD)

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    kms = LocalEnvelopeProvider()
    try:
        pid = uuid.uuid4()
        pid_str = str(pid)
        async with factory() as db:
            db.add(Patient(patient_uuid=pid, is_deleted=False))
            await db.commit()

        # Create profile and legal acceptances
        async with factory() as db:
            await create_or_update_profile(
                pid_str, "Priya Patel", date(1995, 3, 20), db
            )
            await accept_legal_documents(
                pid_str, ["TERMS_OF_SERVICE", "PRIVACY_NOTICE"], db
            )
            await db.commit()

        # Verify onboarding is COMPLETE
        async with factory() as db:
            status = await get_onboarding_status(pid_str, db)
            assert status.complete is True
            assert status.next_step == "COMPLETE"

        # Destroy DEK (cryptographic erasure)
        async with factory() as db:
            await kms.destroy_dek(pid_str, db)
            await db.commit()

        # Profile decryption is blocked
        async with factory() as db:
            with pytest.raises((PatientDataErased, EncryptionError)):
                await get_profile(pid_str, db)

        # Legal acceptance records SURVIVE DEK destruction (compliance evidence)
        async with factory() as db:
            acceptances = list(
                (
                    await db.scalars(
                        select(PatientLegalAcceptance).where(
                            PatientLegalAcceptance.patient_id == pid
                        )
                    )
                ).all()
            )
            assert len(acceptances) == 2

        await _cleanup(factory)
    finally:
        await engine.dispose()
