"""Slice 6G real PostgreSQL + Redis cross-boundary qualification."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from redis.asyncio import ConnectionPool, Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.patient_device_keys import PatientDeviceKey, PatientDeviceKeyStatus
from app.services.patient_auth_service import (
    _CLAIM_PREFIX,
    _token_key,
    claim_device_enrollment_token,
    finalize_device_enrollment_token,
    issue_device_enrollment_token,
)
from app.services.patient_device_recovery import (
    PatientRecoveryCapabilityError,
    _recovery_key,
    _recovery_slot_key,
    consume_patient_recovery_capability,
    issue_patient_recovery_capability,
)
from app.services.patient_device_recovery_transactions import recover_patient_device_authority
from app.services.patient_device_rotation import (
    DeviceRotationChallengeError,
    _challenge_key,
    consume_device_rotation_challenge,
    issue_device_rotation_challenge,
)
from app.services.patient_device_trust import (
    canonicalize_p256_public_key,
    enroll_patient_device_key,
    rotate_patient_device_key,
)
from app.services.patient_discovery_service import (
    DiscoveryHandleInvalid,
    PatientDiscoveryService,
    _handle_key,
)
from app.services.patient_session_authority import (
    _epoch_key,
    _session_key,
    create_patient_session,
    get_or_create_patient_session_epoch,
    resolve_patient_session_id,
    revoke_all_patient_sessions,
)
from tests.helpers.qualification_infra import (
    create_disposable_database,
    drop_disposable_database,
    get_qualification_redis_url,
    migrate_database_to_head,
    postgres_database_url,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgres,
    pytest.mark.redis,
    pytest.mark.asyncio,
]

HEAD = "20260909_device_trust_lifecycle"
_DB_NAME = "nexa_qual_slice6g_cross_boundary"


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


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(_url(), pool_size=12, max_overflow=0)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def real_redis():
    pool = ConnectionPool.from_url(get_qualification_redis_url(), decode_responses=True)
    client = Redis(connection_pool=pool)
    await client.ping()
    try:
        yield client
    finally:
        await client.close()
        await pool.disconnect(inuse_connections=True)


def _keypair():
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private, public


async def _create_live_session(redis, patient_id: str, subject: str, session_id: str) -> int:
    with patch("app.services.patient_session_authority.get_redis_client", return_value=redis):
        epoch = await get_or_create_patient_session_epoch(patient_id)
        now = datetime.now(timezone.utc)
        await create_patient_session(
            patient_id=patient_id,
            supabase_user_id=subject,
            session_id=session_id,
            session_epoch=epoch,
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
        )
    return epoch


async def _enroll(factory, patient_id: uuid.UUID, public_key: bytes):
    async with factory() as db:
        return await enroll_patient_device_key(
            db,
            patient_id=patient_id,
            raw_public_key=public_key,
            device_label="slice6g-device",
            platform="test",
            actor_id=str(patient_id),
        )


async def test_session_created_from_stale_epoch_after_logout_all_is_not_authoritative(real_redis):
    patient = str(uuid.uuid4())
    subject = "slice6g-subject"
    stale_epoch = await _create_live_session(
        real_redis, patient, subject, f"session-{uuid.uuid4().hex}"
    )
    stale_session = f"session-{uuid.uuid4().hex}"
    with patch("app.services.patient_session_authority.get_redis_client", return_value=real_redis):
        new_epoch = await revoke_all_patient_sessions(patient)
        assert new_epoch == stale_epoch + 1
        now = datetime.now(timezone.utc)
        await create_patient_session(
            patient_id=patient,
            supabase_user_id=subject,
            session_id=stale_session,
            session_epoch=stale_epoch,
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
        )
        assert (
            await resolve_patient_session_id(patient_id=patient, session_id=stale_session)
            is None
        )
    await real_redis.delete(_epoch_key(patient), _session_key(stale_session))


async def test_bootstrap_grant_is_burned_when_postgres_enrollment_rolls_back(
    real_redis, session_factory
):
    patient_uuid = uuid.uuid4()
    patient = str(patient_uuid)
    subject = "slice6g-bootstrap"
    session_id = f"session-{uuid.uuid4().hex}"
    _, public_key = _keypair()
    await _create_live_session(real_redis, patient, subject, session_id)

    with (
        patch("app.services.patient_session_authority.get_redis_client", return_value=real_redis),
        patch("app.services.patient_auth_service.get_redis_client", return_value=real_redis),
    ):
        grant = await issue_device_enrollment_token(patient, session_id)
        claim = await claim_device_enrollment_token(grant, patient, session_id)
        assert claim is not None
        assert await finalize_device_enrollment_token(
            grant,
            claim,
            patient_id=patient,
            auth_session_id=session_id,
        )

        async with session_factory() as db:
            with patch(
                "app.services.patient_device_trust.enqueue_audit_event",
                new=AsyncMock(side_effect=RuntimeError("slice6g audit failure")),
            ):
                with pytest.raises(RuntimeError, match="slice6g audit failure"):
                    await enroll_patient_device_key(
                        db,
                        patient_id=patient_uuid,
                        raw_public_key=public_key,
                        device_label="failed-bootstrap",
                        platform="test",
                        actor_id=patient,
                    )

        assert await claim_device_enrollment_token(grant, patient, session_id) is None

    async with session_factory() as db:
        count = await db.scalar(
            select(func.count(PatientDeviceKey.id)).where(
                PatientDeviceKey.patient_id == patient_uuid
            )
        )
        assert int(count or 0) == 0
    await real_redis.delete(
        _epoch_key(patient), _session_key(session_id), _token_key(grant), _CLAIM_PREFIX + _token_key(grant)
    )


async def test_rotation_challenge_is_burned_when_postgres_rotation_rolls_back(
    real_redis, session_factory
):
    patient_uuid = uuid.uuid4()
    patient = str(patient_uuid)
    subject = "slice6g-rotation"
    session_id = f"session-{uuid.uuid4().hex}"
    current_private, current_public = _keypair()
    _, new_public = _keypair()
    enrolled = await _enroll(session_factory, patient_uuid, current_public)
    canonical = canonicalize_p256_public_key(new_public)
    await _create_live_session(real_redis, patient, subject, session_id)

    with (
        patch("app.services.patient_session_authority.get_redis_client", return_value=real_redis),
        patch("app.services.patient_device_rotation.get_redis_client", return_value=real_redis),
    ):
        challenge = await issue_device_rotation_challenge(
            patient_id=patient,
            session_id=session_id,
            device_id=str(enrolled.device_id),
            current_key_version=1,
            new_public_key_fingerprint=canonical.fingerprint,
        )
        consumed = await consume_device_rotation_challenge(
            challenge_nonce=challenge.nonce,
            patient_id=patient,
            session_id=session_id,
            device_id=str(enrolled.device_id),
            current_key_version=1,
            new_public_key_fingerprint=canonical.fingerprint,
        )
        signature = current_private.sign(
            hashlib.sha256(consumed.signing_payload).digest(),
            ec.ECDSA(utils.Prehashed(hashes.SHA256())),
        )
        signature_b64 = base64.b64encode(signature).decode("ascii")

        async with session_factory() as db:
            with patch(
                "app.services.patient_device_trust.enqueue_audit_event",
                new=AsyncMock(side_effect=RuntimeError("slice6g rotation audit failure")),
            ):
                with pytest.raises(RuntimeError, match="slice6g rotation audit failure"):
                    await rotate_patient_device_key(
                        db,
                        patient_id=patient_uuid,
                        device_id=enrolled.device_id,
                        expected_key_version=1,
                        raw_new_public_key=new_public,
                        signing_payload=consumed.signing_payload,
                        signature_b64=signature_b64,
                        actor_id=patient,
                    )

        with pytest.raises(DeviceRotationChallengeError) as replay:
            await consume_device_rotation_challenge(
                challenge_nonce=challenge.nonce,
                patient_id=patient,
                session_id=session_id,
                device_id=str(enrolled.device_id),
                current_key_version=1,
                new_public_key_fingerprint=canonical.fingerprint,
            )
        assert replay.value.code == "DEVICE_ROTATION_CHALLENGE_INVALID"

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
        assert rows[0].status == PatientDeviceKeyStatus.ACTIVE.value
        assert rows[0].key_version == 1
    await real_redis.delete(
        _epoch_key(patient), _session_key(session_id), _challenge_key(challenge.nonce)
    )


async def test_recovery_capability_and_old_sessions_are_burned_when_postgres_recovery_rolls_back(
    real_redis, session_factory
):
    patient_uuid = uuid.uuid4()
    patient = str(patient_uuid)
    subject = "slice6g-recovery"
    session_id = f"session-{uuid.uuid4().hex}"
    _, old_public = _keypair()
    _, new_public = _keypair()
    old_device = await _enroll(session_factory, patient_uuid, old_public)
    await _create_live_session(real_redis, patient, subject, session_id)

    with (
        patch("app.services.patient_session_authority.get_redis_client", return_value=real_redis),
        patch("app.services.patient_device_recovery.get_redis_client", return_value=real_redis),
    ):
        capability = await issue_patient_recovery_capability(
            patient_id=patient,
            session_id=session_id,
            supabase_user_id=subject,
        )
        await consume_patient_recovery_capability(
            token=capability.token,
            patient_id=patient,
            session_id=session_id,
            supabase_user_id=subject,
        )
        await revoke_all_patient_sessions(patient)

        async with session_factory() as db:
            with patch(
                "app.services.patient_device_recovery_transactions.enqueue_audit_event",
                new=AsyncMock(side_effect=RuntimeError("slice6g recovery audit failure")),
            ):
                with pytest.raises(RuntimeError, match="slice6g recovery audit failure"):
                    await recover_patient_device_authority(
                        db,
                        patient_id=patient_uuid,
                        raw_new_public_key=new_public,
                        device_label="failed-recovery",
                        platform="test",
                        actor_id=patient,
                    )

        assert (
            await resolve_patient_session_id(patient_id=patient, session_id=session_id)
            is None
        )
        with pytest.raises(PatientRecoveryCapabilityError) as replay:
            await consume_patient_recovery_capability(
                token=capability.token,
                patient_id=patient,
                session_id=session_id,
                supabase_user_id=subject,
            )
        assert replay.value.code in {
            "PATIENT_RECOVERY_SESSION_INACTIVE",
            "PATIENT_RECOVERY_CAPABILITY_INVALID",
        }

    async with session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(PatientDeviceKey).where(
                        PatientDeviceKey.patient_id == patient_uuid
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].id == old_device.id
        assert rows[0].status == PatientDeviceKeyStatus.ACTIVE.value
    await real_redis.delete(
        _epoch_key(patient),
        _session_key(session_id),
        _recovery_key(capability.token),
        _recovery_slot_key(patient, session_id),
    )


async def test_discovery_consume_vs_revoke_has_at_most_one_disclosure(real_redis):
    patient = SimpleNamespace(patient_uuid=uuid.uuid4(), is_deleted=False)
    provider_id = str(uuid.uuid4())
    hospital_id = str(uuid.uuid4())
    service = PatientDiscoveryService(AsyncMock(), real_redis)
    with patch.object(
        PatientDiscoveryService,
        "resolve_patient_id",
        new=AsyncMock(return_value=(patient, False)),
    ):
        handle = await service.issue_handle(
            patient=patient,
            provider_id=provider_id,
            hospital_id=hospital_id,
            session_binding="slice6g-session-binding",
            identifier_type="SLICE6G_TEST",
        )
        assert await service.activate_handle(raw_handle=handle.value)

        async def consume():
            try:
                return await service.consume_handle(
                    raw_handle=handle.value,
                    provider_id=provider_id,
                    hospital_id=hospital_id,
                    session_binding="slice6g-session-binding",
                )
            except DiscoveryHandleInvalid:
                return None

        consumed, _ = await asyncio.gather(
            consume(), service.revoke_handle(raw_handle=handle.value)
        )
        assert consumed is None or consumed.patient_uuid == patient.patient_uuid
        with pytest.raises(DiscoveryHandleInvalid):
            await service.consume_handle(
                raw_handle=handle.value,
                provider_id=provider_id,
                hospital_id=hospital_id,
                session_binding="slice6g-session-binding",
            )
    await real_redis.delete(_handle_key(handle.value))
