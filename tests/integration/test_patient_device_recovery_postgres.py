"""Real PostgreSQL qualification for Slice 6E trusted enrollment and recovery."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.patient_device_keys import PatientDeviceKey, PatientDeviceKeyStatus
from app.services.patient_device_recovery import canonical_trusted_enrollment_payload
from app.services.patient_device_recovery_transactions import (
    enroll_patient_device_from_trusted_authorizer,
    recover_patient_device_authority,
)
from app.services.patient_device_trust import (
    PatientDeviceTrustError,
    enroll_patient_device_key,
)
from tests.helpers.qualification_infra import (
    create_disposable_database,
    drop_disposable_database,
    migrate_database_to_head,
    postgres_database_url,
)

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]

HEAD = "20260909_device_trust_lifecycle"
_DB_NAME = "nexa_qual_patient_device_recovery"


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


def _keypair():
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private, public


async def _enroll(factory, patient_id: uuid.UUID, public_key: bytes):
    async with factory() as db:
        return await enroll_patient_device_key(
            db,
            patient_id=patient_id,
            raw_public_key=public_key,
            device_label="recovery-qualified-device",
            platform="test",
            actor_id=str(patient_id),
        )


def _trusted_proof(
    *,
    private_key,
    patient_id: uuid.UUID,
    authorizer_device_id: uuid.UUID,
    authorizer_key_version: int,
    new_public_key: bytes,
):
    fingerprint = hashlib.sha256(new_public_key).hexdigest()
    now = datetime.now(timezone.utc)
    payload = canonical_trusted_enrollment_payload(
        patient_id=str(patient_id),
        session_id=f"session-{uuid.uuid4().hex}",
        authorizer_device_id=str(authorizer_device_id),
        authorizer_key_version=authorizer_key_version,
        new_public_key_fingerprint=fingerprint,
        challenge_nonce=uuid.uuid4().hex + uuid.uuid4().hex,
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=2)).isoformat(),
    )
    signature = private_key.sign(
        hashlib.sha256(payload).digest(),
        ec.ECDSA(utils.Prehashed(hashes.SHA256())),
    )
    return payload, base64.b64encode(signature).decode("ascii")


async def test_trusted_device_can_enroll_new_device_with_transactional_proof(session_factory):
    patient_id = uuid.uuid4()
    authorizer_private, authorizer_public = _keypair()
    _, new_public = _keypair()
    authorizer = await _enroll(session_factory, patient_id, authorizer_public)
    payload, signature = _trusted_proof(
        private_key=authorizer_private,
        patient_id=patient_id,
        authorizer_device_id=authorizer.device_id,
        authorizer_key_version=1,
        new_public_key=new_public,
    )
    async with session_factory() as db:
        row = await enroll_patient_device_from_trusted_authorizer(
            db,
            patient_id=patient_id,
            authorizer_device_id=authorizer.device_id,
            expected_authorizer_key_version=1,
            raw_new_public_key=new_public,
            signing_payload=payload,
            signature_b64=signature,
            device_label="new phone",
            platform="test",
            actor_id=str(patient_id),
        )
    assert row.device_id != authorizer.device_id
    assert row.key_version == 1
    assert row.status == PatientDeviceKeyStatus.ACTIVE.value

    async with session_factory() as db:
        active_count = await db.scalar(
            select(func.count(PatientDeviceKey.id)).where(
                PatientDeviceKey.patient_id == patient_id,
                PatientDeviceKey.status == PatientDeviceKeyStatus.ACTIVE.value,
            )
        )
        audits = (
            await db.execute(
                text(
                    "SELECT event_type FROM audit_outbox WHERE patient_id=:patient_id "
                    "AND payload->'metadata'->>'operation'='trusted_device_enrollment'"
                ),
                {"patient_id": str(patient_id)},
            )
        ).all()
    assert int(active_count or 0) == 2
    assert [row[0] for row in audits] == ["DEVICE_KEY_ENROLLED"]


async def test_wrong_or_stale_authorizer_private_key_cannot_enroll(session_factory):
    patient_id = uuid.uuid4()
    authorizer_private, authorizer_public = _keypair()
    wrong_private, _ = _keypair()
    _, new_public = _keypair()
    authorizer = await _enroll(session_factory, patient_id, authorizer_public)
    payload, wrong_signature = _trusted_proof(
        private_key=wrong_private,
        patient_id=patient_id,
        authorizer_device_id=authorizer.device_id,
        authorizer_key_version=1,
        new_public_key=new_public,
    )
    async with session_factory() as db:
        with pytest.raises(PatientDeviceTrustError) as exc_info:
            await enroll_patient_device_from_trusted_authorizer(
                db,
                patient_id=patient_id,
                authorizer_device_id=authorizer.device_id,
                expected_authorizer_key_version=1,
                raw_new_public_key=new_public,
                signing_payload=payload,
                signature_b64=wrong_signature,
                device_label="attacker",
                platform="test",
                actor_id=str(patient_id),
            )
    assert exc_info.value.code == "TRUSTED_ENROLLMENT_SIGNATURE_INVALID"

    # The correct key still works, proving the failed attempt did not mutate authority.
    payload, signature = _trusted_proof(
        private_key=authorizer_private,
        patient_id=patient_id,
        authorizer_device_id=authorizer.device_id,
        authorizer_key_version=1,
        new_public_key=new_public,
    )
    async with session_factory() as db:
        row = await enroll_patient_device_from_trusted_authorizer(
            db,
            patient_id=patient_id,
            authorizer_device_id=authorizer.device_id,
            expected_authorizer_key_version=1,
            raw_new_public_key=new_public,
            signing_payload=payload,
            signature_b64=signature,
            device_label="new phone",
            platform="test",
            actor_id=str(patient_id),
        )
    assert row.status == PatientDeviceKeyStatus.ACTIVE.value


async def test_recovery_revokes_all_active_keys_and_installs_one_fresh_device(session_factory):
    patient_id = uuid.uuid4()
    old_keys = [_keypair()[1] for _ in range(3)]
    old_rows = [await _enroll(session_factory, patient_id, key) for key in old_keys]
    _, fresh_public = _keypair()

    async with session_factory() as db:
        result = await recover_patient_device_authority(
            db,
            patient_id=patient_id,
            raw_new_public_key=fresh_public,
            device_label="recovered phone",
            platform="test",
            actor_id=str(patient_id),
        )
    assert result.revoked_device_count == 3
    assert result.status == PatientDeviceKeyStatus.ACTIVE.value
    assert result.device_id not in {row.device_id for row in old_rows}

    async with session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(PatientDeviceKey).where(
                        PatientDeviceKey.patient_id == patient_id
                    )
                )
            )
            .scalars()
            .all()
        )
        recovery_audits = (
            await db.execute(
                text(
                    "SELECT event_type, payload FROM audit_outbox "
                    "WHERE patient_id=:patient_id "
                    "AND payload->'metadata'->>'operation'='account_recovery'"
                ),
                {"patient_id": str(patient_id)},
            )
        ).all()
    active = [row for row in rows if row.status == PatientDeviceKeyStatus.ACTIVE.value]
    revoked = [row for row in rows if row.status == PatientDeviceKeyStatus.REVOKED.value]
    assert len(active) == 1
    assert active[0].device_id == result.device_id
    assert len(revoked) == 3
    assert all(row.revocation_reason_code == "ACCOUNT_RECOVERY" for row in revoked)
    assert [row[0] for row in recovery_audits].count("DEVICE_KEY_REVOKED") == 3
    assert [row[0] for row in recovery_audits].count("DEVICE_KEY_ENROLLED") == 1


async def test_recovery_rejects_old_terminal_key_resurrection(session_factory):
    patient_id = uuid.uuid4()
    _, old_public = _keypair()
    old = await _enroll(session_factory, patient_id, old_public)
    _, fresh_public = _keypair()
    async with session_factory() as db:
        await recover_patient_device_authority(
            db,
            patient_id=patient_id,
            raw_new_public_key=fresh_public,
            device_label="fresh",
            platform="test",
            actor_id=str(patient_id),
        )
    async with session_factory() as db:
        with pytest.raises(PatientDeviceTrustError) as exc_info:
            await recover_patient_device_authority(
                db,
                patient_id=patient_id,
                raw_new_public_key=old_public,
                device_label="resurrection",
                platform="test",
                actor_id=str(patient_id),
            )
    assert exc_info.value.code == "DEVICE_KEY_RESURRECTION_FORBIDDEN"
    assert old.device_id is not None


async def test_recovery_audit_failure_rolls_back_device_authority(session_factory):
    patient_id = uuid.uuid4()
    _, old_public = _keypair()
    old = await _enroll(session_factory, patient_id, old_public)
    _, fresh_public = _keypair()
    with patch(
        "app.services.patient_device_recovery_transactions.enqueue_audit_event",
        new=AsyncMock(side_effect=RuntimeError("audit outbox unavailable")),
    ):
        async with session_factory() as db:
            with pytest.raises(RuntimeError, match="audit outbox unavailable"):
                await recover_patient_device_authority(
                    db,
                    patient_id=patient_id,
                    raw_new_public_key=fresh_public,
                    device_label="fresh",
                    platform="test",
                    actor_id=str(patient_id),
                )

    async with session_factory() as db:
        active = (
            (
                await db.execute(
                    select(PatientDeviceKey).where(
                        PatientDeviceKey.patient_id == patient_id,
                        PatientDeviceKey.status == PatientDeviceKeyStatus.ACTIVE.value,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(active) == 1
    assert active[0].device_id == old.device_id


async def test_concurrent_recovery_transactions_serialize_to_one_current_device(session_factory):
    patient_id = uuid.uuid4()
    _, old_public = _keypair()
    await _enroll(session_factory, patient_id, old_public)
    contenders = [_keypair()[1] for _ in range(6)]

    async def contender(public_key: bytes):
        async with session_factory() as db:
            try:
                return await recover_patient_device_authority(
                    db,
                    patient_id=patient_id,
                    raw_new_public_key=public_key,
                    device_label="race",
                    platform="test",
                    actor_id=str(patient_id),
                )
            except PatientDeviceTrustError as exc:
                return exc.code

    results = await asyncio.gather(*[contender(key) for key in contenders])
    # DB serialization guarantees a single active row even if higher-level
    # one-time capability gating is bypassed in this direct transaction test.
    assert any(not isinstance(result, str) for result in results)
    async with session_factory() as db:
        active_count = await db.scalar(
            select(func.count(PatientDeviceKey.id)).where(
                PatientDeviceKey.patient_id == patient_id,
                PatientDeviceKey.status == PatientDeviceKeyStatus.ACTIVE.value,
            )
        )
    assert int(active_count or 0) == 1
