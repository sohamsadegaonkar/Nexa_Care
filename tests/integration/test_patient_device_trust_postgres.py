"""Real PostgreSQL qualification for Slice 6C patient device trust."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.patient_device_keys import PatientDeviceKey, PatientDeviceKeyStatus
from app.services.patient_device_trust import (
    MAX_ACTIVE_PATIENT_DEVICES,
    PatientDeviceTrustError,
    canonicalize_p256_public_key,
    enroll_patient_device_key,
    revoke_patient_device,
)
from tests.helpers.qualification_infra import (
    create_disposable_database,
    drop_disposable_database,
    migrate_database_to_head,
    postgres_database_url,
)

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]

HEAD = "20260909_device_trust_lifecycle"
_DB_NAME = "nexa_qual_patient_device_trust"


def _url() -> str:
    return postgres_database_url(_DB_NAME)


@pytest.fixture(scope="module", autouse=True)
def _setup_database():
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
    engine = create_async_engine(_url(), pool_size=16, max_overflow=0)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def _key() -> bytes:
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


async def _enroll(factory, patient_id: uuid.UUID, raw_key: bytes):
    async with factory() as db:
        return await enroll_patient_device_key(
            db,
            patient_id=patient_id,
            raw_public_key=raw_key,
            device_label="qualification-device",
            platform="test",
            actor_id=str(patient_id),
        )


async def _count_active(factory, patient_id: uuid.UUID) -> int:
    async with factory() as db:
        value = await db.scalar(
            select(func.count(PatientDeviceKey.id)).where(
                PatientDeviceKey.patient_id == patient_id,
                PatientDeviceKey.status == PatientDeviceKeyStatus.ACTIVE.value,
            )
        )
        return int(value or 0)


async def test_migration_has_global_fingerprint_and_one_active_version_constraints(
    session_factory,
):
    async with session_factory() as db:
        indexes = {
            row[0]
            for row in (
                await db.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname='public' AND tablename='patient_device_keys'"
                    )
                )
            ).all()
        }
        columns = {
            row[0]
            for row in (
                await db.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='patient_device_keys'"
                    )
                )
            ).all()
        }
    assert {
        "device_id",
        "key_version",
        "public_key_fingerprint",
        "revocation_reason_code",
        "revocation_actor",
        "replaces_key_id",
        "replaced_by_key_id",
    } <= columns
    assert "uq_patient_device_key_fingerprint_global" in indexes
    assert "uq_patient_device_key_device_version" in indexes
    assert "uq_patient_device_key_one_active_version" in indexes


async def test_enrollment_stores_canonical_version_one_and_transactional_audit(
    session_factory,
):
    patient_id = uuid.uuid4()
    raw = _key()
    canonical = canonicalize_p256_public_key(raw)
    row = await _enroll(session_factory, patient_id, raw)
    assert row.patient_id == patient_id
    assert row.device_id != row.id
    assert row.key_version == 1
    assert row.status == PatientDeviceKeyStatus.ACTIVE.value
    assert row.device_public_key == canonical.der
    assert row.public_key_fingerprint == canonical.fingerprint
    async with session_factory() as db:
        audit_count = await db.scalar(
            text(
                "SELECT count(*) FROM audit_outbox "
                "WHERE event_type='DEVICE_KEY_ENROLLED' AND patient_id=:patient_id"
            ),
            {"patient_id": str(patient_id)},
        )
    assert int(audit_count or 0) == 1


async def test_concurrent_fifth_device_limit_never_commits_six(session_factory):
    patient_id = uuid.uuid4()
    for _ in range(MAX_ACTIVE_PATIENT_DEVICES - 1):
        await _enroll(session_factory, patient_id, _key())

    async def contender(raw: bytes) -> str:
        try:
            await _enroll(session_factory, patient_id, raw)
            return "enrolled"
        except PatientDeviceTrustError as exc:
            return exc.code

    results = await asyncio.gather(*[contender(_key()) for _ in range(8)])
    assert results.count("enrolled") == 1
    assert all(
        result in {"enrolled", "DEVICE_ACTIVE_LIMIT_REACHED"} for result in results
    )
    assert await _count_active(session_factory, patient_id) == MAX_ACTIVE_PATIENT_DEVICES


async def test_identical_key_race_same_patient_has_one_owner(session_factory):
    patient_id = uuid.uuid4()
    raw = _key()

    async def contender() -> str:
        try:
            await _enroll(session_factory, patient_id, raw)
            return "enrolled"
        except PatientDeviceTrustError as exc:
            return exc.code

    results = await asyncio.gather(*[contender() for _ in range(6)])
    assert results.count("enrolled") == 1
    assert all(result in {"enrolled", "DEVICE_KEY_ALREADY_ENROLLED"} for result in results)


async def test_identical_key_race_across_patients_has_one_global_owner(session_factory):
    patient_a, patient_b = uuid.uuid4(), uuid.uuid4()
    raw = _key()

    async def contender(patient_id: uuid.UUID) -> str:
        try:
            await _enroll(session_factory, patient_id, raw)
            return "enrolled"
        except PatientDeviceTrustError as exc:
            return exc.code

    results = await asyncio.gather(contender(patient_a), contender(patient_b))
    assert results.count("enrolled") == 1
    assert results.count("DEVICE_KEY_ALREADY_ENROLLED") == 1
    canonical = canonicalize_p256_public_key(raw)
    async with session_factory() as db:
        count = await db.scalar(
            select(func.count(PatientDeviceKey.id)).where(
                PatientDeviceKey.public_key_fingerprint == canonical.fingerprint
            )
        )
    assert int(count or 0) == 1


async def test_revoked_key_is_terminal_and_cannot_resurrect(session_factory):
    patient_id = uuid.uuid4()
    raw = _key()
    enrolled = await _enroll(session_factory, patient_id, raw)
    async with session_factory() as db:
        revoked = await revoke_patient_device(
            db,
            patient_id=patient_id,
            device_id=enrolled.device_id,
            actor_id=str(patient_id),
            reason_code="PATIENT_REVOKED",
        )
    assert revoked.status == PatientDeviceKeyStatus.REVOKED.value
    assert revoked.revoked_at is not None
    assert revoked.revocation_reason_code == "PATIENT_REVOKED"
    with pytest.raises(PatientDeviceTrustError) as exc_info:
        await _enroll(session_factory, patient_id, raw)
    assert exc_info.value.code == "DEVICE_KEY_RESURRECTION_FORBIDDEN"


async def test_cross_patient_revoke_cannot_target_foreign_device(session_factory):
    owner, attacker = uuid.uuid4(), uuid.uuid4()
    enrolled = await _enroll(session_factory, owner, _key())
    async with session_factory() as db:
        with pytest.raises(PatientDeviceTrustError) as exc_info:
            await revoke_patient_device(
                db,
                patient_id=attacker,
                device_id=enrolled.device_id,
                actor_id=str(attacker),
            )
    assert exc_info.value.code == "DEVICE_NOT_FOUND"
    assert await _count_active(session_factory, owner) == 1


async def test_terminal_row_cannot_be_reactivated_by_direct_update(session_factory):
    patient_id = uuid.uuid4()
    enrolled = await _enroll(session_factory, patient_id, _key())
    async with session_factory() as db:
        await revoke_patient_device(
            db,
            patient_id=patient_id,
            device_id=enrolled.device_id,
            actor_id=str(patient_id),
        )
    async with session_factory() as db:
        row = await db.scalar(
            select(PatientDeviceKey).where(PatientDeviceKey.id == enrolled.id)
        )
        assert row is not None
        row.status = PatientDeviceKeyStatus.ACTIVE.value
        row.revoked_at = datetime.now(timezone.utc)
        with pytest.raises(Exception):
            await db.commit()
        await db.rollback()
