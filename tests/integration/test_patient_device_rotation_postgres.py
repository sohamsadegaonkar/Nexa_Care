"""Real PostgreSQL qualification for Slice 6D device-key rotation."""

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
from app.services.patient_device_rotation import canonical_device_rotation_payload
from app.services.patient_device_trust import (
    PatientDeviceTrustError,
    canonicalize_p256_public_key,
    enroll_patient_device_key,
    revoke_patient_device,
    rotate_patient_device_key,
)
from tests.helpers.qualification_infra import (
    create_disposable_database,
    drop_disposable_database,
    migrate_database_to_head,
    postgres_database_url,
)

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]

HEAD = "20260909_device_trust_lifecycle"
_DB_NAME = "nexa_qual_patient_device_rotation"


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


def _rotation_proof(
    *,
    current_private,
    patient_id: uuid.UUID,
    device_id: uuid.UUID,
    current_key_version: int,
    new_public_key: bytes,
):
    canonical = canonicalize_p256_public_key(new_public_key)
    now = datetime.now(timezone.utc)
    payload = canonical_device_rotation_payload(
        patient_id=str(patient_id),
        session_id=f"session-{uuid.uuid4().hex}",
        device_id=str(device_id),
        current_key_version=current_key_version,
        new_public_key_fingerprint=canonical.fingerprint,
        challenge_nonce=uuid.uuid4().hex + uuid.uuid4().hex,
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=2)).isoformat(),
    )
    signature = current_private.sign(
        hashlib.sha256(payload).digest(),
        ec.ECDSA(utils.Prehashed(hashes.SHA256())),
    )
    return payload, base64.b64encode(signature).decode("ascii"), canonical


async def _enroll(factory, patient_id: uuid.UUID, public_key: bytes):
    async with factory() as db:
        return await enroll_patient_device_key(
            db,
            patient_id=patient_id,
            raw_public_key=public_key,
            device_label="rotation-qualified-device",
            platform="test",
            actor_id=str(patient_id),
        )


async def _rotate(
    factory,
    *,
    patient_id: uuid.UUID,
    device_id: uuid.UUID,
    expected_key_version: int,
    new_public_key: bytes,
    signing_payload: bytes,
    signature_b64: str,
):
    async with factory() as db:
        return await rotate_patient_device_key(
            db,
            patient_id=patient_id,
            device_id=device_id,
            expected_key_version=expected_key_version,
            raw_new_public_key=new_public_key,
            signing_payload=signing_payload,
            signature_b64=signature_b64,
            actor_id=str(patient_id),
        )


async def test_rotation_advances_version_and_persists_bidirectional_lineage_with_audit(
    session_factory,
):
    patient_id = uuid.uuid4()
    current_private, current_public = _keypair()
    _, new_public = _keypair()
    enrolled = await _enroll(session_factory, patient_id, current_public)
    payload, signature, canonical = _rotation_proof(
        current_private=current_private,
        patient_id=patient_id,
        device_id=enrolled.device_id,
        current_key_version=1,
        new_public_key=new_public,
    )
    result = await _rotate(
        session_factory,
        patient_id=patient_id,
        device_id=enrolled.device_id,
        expected_key_version=1,
        new_public_key=new_public,
        signing_payload=payload,
        signature_b64=signature,
    )
    assert result.old_key_id == enrolled.id
    assert result.old_key_version == 1
    assert result.new_key_version == 2
    assert result.new_public_key_fingerprint == canonical.fingerprint

    async with session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(PatientDeviceKey)
                    .where(PatientDeviceKey.device_id == enrolled.device_id)
                    .order_by(PatientDeviceKey.key_version)
                )
            )
            .scalars()
            .all()
        )
        audit_rows = (
            await db.execute(
                text(
                    "SELECT event_type, payload FROM audit_outbox "
                    "WHERE patient_id=:patient_id "
                    "AND payload->'metadata'->>'operation'='device_key_rotation' "
                    "ORDER BY created_at"
                ),
                {"patient_id": str(patient_id)},
            )
        ).all()
    assert len(rows) == 2
    old, new = rows
    assert old.status == PatientDeviceKeyStatus.REPLACED.value
    assert old.revoked_at is not None
    assert old.revocation_reason_code == "KEY_ROTATED"
    assert old.replaced_by_key_id == new.id
    assert new.status == PatientDeviceKeyStatus.ACTIVE.value
    assert new.revoked_at is None
    assert new.replaces_key_id == old.id
    assert new.device_id == old.device_id
    assert new.key_version == 2
    assert new.public_key_fingerprint == canonical.fingerprint
    assert [row[0] for row in audit_rows] == ["DEVICE_KEY_REVOKED", "DEVICE_KEY_ENROLLED"]


async def test_old_private_key_is_immediately_invalid_after_rotation(session_factory):
    patient_id = uuid.uuid4()
    old_private, old_public = _keypair()
    new_private, new_public = _keypair()
    _, third_public = _keypair()
    enrolled = await _enroll(session_factory, patient_id, old_public)
    payload, signature, _ = _rotation_proof(
        current_private=old_private,
        patient_id=patient_id,
        device_id=enrolled.device_id,
        current_key_version=1,
        new_public_key=new_public,
    )
    await _rotate(
        session_factory,
        patient_id=patient_id,
        device_id=enrolled.device_id,
        expected_key_version=1,
        new_public_key=new_public,
        signing_payload=payload,
        signature_b64=signature,
    )

    stale_payload, stale_signature, _ = _rotation_proof(
        current_private=old_private,
        patient_id=patient_id,
        device_id=enrolled.device_id,
        current_key_version=1,
        new_public_key=third_public,
    )
    with pytest.raises(PatientDeviceTrustError) as stale:
        await _rotate(
            session_factory,
            patient_id=patient_id,
            device_id=enrolled.device_id,
            expected_key_version=1,
            new_public_key=third_public,
            signing_payload=stale_payload,
            signature_b64=stale_signature,
        )
    assert stale.value.code == "DEVICE_KEY_VERSION_STALE"

    old_key_payload, old_key_signature, _ = _rotation_proof(
        current_private=old_private,
        patient_id=patient_id,
        device_id=enrolled.device_id,
        current_key_version=2,
        new_public_key=third_public,
    )
    with pytest.raises(PatientDeviceTrustError) as old_proof:
        await _rotate(
            session_factory,
            patient_id=patient_id,
            device_id=enrolled.device_id,
            expected_key_version=2,
            new_public_key=third_public,
            signing_payload=old_key_payload,
            signature_b64=old_key_signature,
        )
    assert old_proof.value.code == "DEVICE_ROTATION_SIGNATURE_INVALID"

    valid_payload, valid_signature, _ = _rotation_proof(
        current_private=new_private,
        patient_id=patient_id,
        device_id=enrolled.device_id,
        current_key_version=2,
        new_public_key=third_public,
    )
    result = await _rotate(
        session_factory,
        patient_id=patient_id,
        device_id=enrolled.device_id,
        expected_key_version=2,
        new_public_key=third_public,
        signing_payload=valid_payload,
        signature_b64=valid_signature,
    )
    assert result.new_key_version == 3


async def test_concurrent_double_rotation_has_exactly_one_version_two_winner(
    session_factory,
):
    patient_id = uuid.uuid4()
    current_private, current_public = _keypair()
    enrolled = await _enroll(session_factory, patient_id, current_public)
    contenders = [_keypair()[1] for _ in range(8)]

    async def contender(new_public: bytes) -> str:
        payload, signature, _ = _rotation_proof(
            current_private=current_private,
            patient_id=patient_id,
            device_id=enrolled.device_id,
            current_key_version=1,
            new_public_key=new_public,
        )
        try:
            await _rotate(
                session_factory,
                patient_id=patient_id,
                device_id=enrolled.device_id,
                expected_key_version=1,
                new_public_key=new_public,
                signing_payload=payload,
                signature_b64=signature,
            )
            return "rotated"
        except PatientDeviceTrustError as exc:
            return exc.code

    results = await asyncio.gather(*[contender(key) for key in contenders])
    assert results.count("rotated") == 1
    assert all(r in {"rotated", "DEVICE_KEY_VERSION_STALE"} for r in results)
    async with session_factory() as db:
        active_count = await db.scalar(
            select(func.count(PatientDeviceKey.id)).where(
                PatientDeviceKey.device_id == enrolled.device_id,
                PatientDeviceKey.status == PatientDeviceKeyStatus.ACTIVE.value,
            )
        )
        version_two_count = await db.scalar(
            select(func.count(PatientDeviceKey.id)).where(
                PatientDeviceKey.device_id == enrolled.device_id,
                PatientDeviceKey.key_version == 2,
            )
        )
    assert int(active_count or 0) == 1
    assert int(version_two_count or 0) == 1


async def test_rotation_rejects_new_key_owned_by_another_patient(session_factory):
    patient_a, patient_b = uuid.uuid4(), uuid.uuid4()
    private_a, public_a = _keypair()
    _, already_owned_public = _keypair()
    device_a = await _enroll(session_factory, patient_a, public_a)
    await _enroll(session_factory, patient_b, already_owned_public)
    payload, signature, _ = _rotation_proof(
        current_private=private_a,
        patient_id=patient_a,
        device_id=device_a.device_id,
        current_key_version=1,
        new_public_key=already_owned_public,
    )
    with pytest.raises(PatientDeviceTrustError) as exc_info:
        await _rotate(
            session_factory,
            patient_id=patient_a,
            device_id=device_a.device_id,
            expected_key_version=1,
            new_public_key=already_owned_public,
            signing_payload=payload,
            signature_b64=signature,
        )
    assert exc_info.value.code == "DEVICE_KEY_ALREADY_ENROLLED"


async def test_rotation_rejects_terminal_new_key_resurrection(session_factory):
    patient_a, patient_b = uuid.uuid4(), uuid.uuid4()
    private_a, public_a = _keypair()
    _, terminal_public = _keypair()
    device_a = await _enroll(session_factory, patient_a, public_a)
    terminal = await _enroll(session_factory, patient_b, terminal_public)
    async with session_factory() as db:
        await revoke_patient_device(
            db,
            patient_id=patient_b,
            device_id=terminal.device_id,
            actor_id=str(patient_b),
        )
    payload, signature, _ = _rotation_proof(
        current_private=private_a,
        patient_id=patient_a,
        device_id=device_a.device_id,
        current_key_version=1,
        new_public_key=terminal_public,
    )
    with pytest.raises(PatientDeviceTrustError) as exc_info:
        await _rotate(
            session_factory,
            patient_id=patient_a,
            device_id=device_a.device_id,
            expected_key_version=1,
            new_public_key=terminal_public,
            signing_payload=payload,
            signature_b64=signature,
        )
    assert exc_info.value.code == "DEVICE_KEY_RESURRECTION_FORBIDDEN"


async def test_revoked_or_foreign_device_cannot_rotate(session_factory):
    owner, attacker = uuid.uuid4(), uuid.uuid4()
    owner_private, owner_public = _keypair()
    _, new_public = _keypair()
    enrolled = await _enroll(session_factory, owner, owner_public)
    payload, signature, _ = _rotation_proof(
        current_private=owner_private,
        patient_id=attacker,
        device_id=enrolled.device_id,
        current_key_version=1,
        new_public_key=new_public,
    )
    with pytest.raises(PatientDeviceTrustError) as cross_patient:
        await _rotate(
            session_factory,
            patient_id=attacker,
            device_id=enrolled.device_id,
            expected_key_version=1,
            new_public_key=new_public,
            signing_payload=payload,
            signature_b64=signature,
        )
    assert cross_patient.value.code == "DEVICE_NOT_FOUND"

    async with session_factory() as db:
        await revoke_patient_device(
            db,
            patient_id=owner,
            device_id=enrolled.device_id,
            actor_id=str(owner),
        )
    payload, signature, _ = _rotation_proof(
        current_private=owner_private,
        patient_id=owner,
        device_id=enrolled.device_id,
        current_key_version=1,
        new_public_key=new_public,
    )
    with pytest.raises(PatientDeviceTrustError) as revoked:
        await _rotate(
            session_factory,
            patient_id=owner,
            device_id=enrolled.device_id,
            expected_key_version=1,
            new_public_key=new_public,
            signing_payload=payload,
            signature_b64=signature,
        )
    assert revoked.value.code == "DEVICE_NOT_ACTIVE"


async def test_rotation_audit_failure_rolls_back_authority_mutation(session_factory):
    patient_id = uuid.uuid4()
    current_private, current_public = _keypair()
    _, new_public = _keypair()
    enrolled = await _enroll(session_factory, patient_id, current_public)
    payload, signature, _ = _rotation_proof(
        current_private=current_private,
        patient_id=patient_id,
        device_id=enrolled.device_id,
        current_key_version=1,
        new_public_key=new_public,
    )
    with patch(
        "app.services.patient_device_trust.enqueue_audit_event",
        new=AsyncMock(side_effect=RuntimeError("audit outbox unavailable")),
    ):
        with pytest.raises(RuntimeError, match="audit outbox unavailable"):
            await _rotate(
                session_factory,
                patient_id=patient_id,
                device_id=enrolled.device_id,
                expected_key_version=1,
                new_public_key=new_public,
                signing_payload=payload,
                signature_b64=signature,
            )

    async with session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(PatientDeviceKey).where(
                        PatientDeviceKey.device_id == enrolled.device_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].id == enrolled.id
    assert rows[0].key_version == 1
    assert rows[0].status == PatientDeviceKeyStatus.ACTIVE.value
    assert rows[0].revoked_at is None
    assert rows[0].replaced_by_key_id is None
