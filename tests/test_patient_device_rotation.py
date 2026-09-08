"""Pure-unit qualification for Slice 6D device-key rotation authority."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

from app.api.v2.device_routes import (
    DeviceRotateRequest,
    DeviceRotationChallengeRequest,
    issue_rotation_challenge,
    rotate_device,
)
from app.core.dependencies import AuthenticatedPatientSession
from app.services.patient_device_rotation import (
    DEVICE_ROTATION_OPERATION,
    DEVICE_ROTATION_PROTOCOL_VERSION,
    DeviceRotationAuthorityUnavailable,
    DeviceRotationChallengeError,
    canonical_device_rotation_payload,
    consume_device_rotation_challenge,
    issue_device_rotation_challenge,
    verify_device_rotation_signature,
)
from app.services.patient_device_trust import (
    PatientDeviceRotationResult,
    canonicalize_p256_public_key,
)


class _FakeRedisNoEval:
    def __init__(self):
        self.data: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, key):
        return 1 if self.data.pop(key, None) is not None else 0


class _UnavailableRedis:
    async def set(self, *_args, **_kwargs):
        raise RuntimeError("redis unavailable")

    async def get(self, *_args, **_kwargs):
        raise RuntimeError("redis unavailable")

    async def delete(self, *_args, **_kwargs):
        raise RuntimeError("redis unavailable")


def _keypair():
    private = ec.generate_private_key(ec.SECP256R1())
    public_der = private.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private, public_der


def _sign(private_key, payload: bytes) -> str:
    signature = private_key.sign(
        hashlib.sha256(payload).digest(),
        ec.ECDSA(utils.Prehashed(hashes.SHA256())),
    )
    return base64.b64encode(signature).decode("ascii")


def _canonical_kwargs() -> dict[str, object]:
    now = datetime.now(timezone.utc)
    return {
        "patient_id": str(uuid.uuid4()),
        "session_id": f"session-{uuid.uuid4().hex}",
        "device_id": str(uuid.uuid4()),
        "current_key_version": 3,
        "new_public_key_fingerprint": "a" * 64,
        "challenge_nonce": "n" * 43,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=2)).isoformat(),
    }


def test_canonical_rotation_payload_is_versioned_and_binds_every_authority_field():
    kwargs = _canonical_kwargs()
    payload = canonical_device_rotation_payload(**kwargs)
    decoded = json.loads(payload)
    assert decoded["protocol_version"] == DEVICE_ROTATION_PROTOCOL_VERSION
    assert decoded["operation"] == DEVICE_ROTATION_OPERATION
    assert decoded["patient_id"] == kwargs["patient_id"]
    assert decoded["device_id"] == kwargs["device_id"]
    assert decoded["current_key_version"] == 3
    assert decoded["new_public_key_fingerprint"] == "a" * 64
    assert decoded["challenge_nonce"] == "n" * 43
    assert decoded["session_binding_sha256"] == hashlib.sha256(
        str(kwargs["session_id"]).encode("utf-8")
    ).hexdigest()

    mutations = {
        "patient_id": str(uuid.uuid4()),
        "session_id": f"session-{uuid.uuid4().hex}",
        "device_id": str(uuid.uuid4()),
        "current_key_version": 4,
        "new_public_key_fingerprint": "b" * 64,
        "challenge_nonce": "m" * 43,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat(),
    }
    for field, value in mutations.items():
        changed = dict(kwargs)
        changed[field] = value
        assert canonical_device_rotation_payload(**changed) != payload


@pytest.mark.asyncio
async def test_one_time_challenge_consumption_and_replay_denial():
    redis = _FakeRedisNoEval()
    patient_id = str(uuid.uuid4())
    session_id = f"session-{uuid.uuid4().hex}"
    device_id = str(uuid.uuid4())
    fingerprint = "c" * 64
    with (
        patch(
            "app.services.patient_device_rotation.get_redis_client",
            return_value=redis,
        ),
        patch(
            "app.services.patient_device_rotation.resolve_patient_session_id",
            new=AsyncMock(return_value={"status": "active"}),
        ),
    ):
        challenge = await issue_device_rotation_challenge(
            patient_id=patient_id,
            session_id=session_id,
            device_id=device_id,
            current_key_version=1,
            new_public_key_fingerprint=fingerprint,
        )
        consumed = await consume_device_rotation_challenge(
            challenge_nonce=challenge.nonce,
            patient_id=patient_id,
            session_id=session_id,
            device_id=device_id,
            current_key_version=1,
            new_public_key_fingerprint=fingerprint,
        )
        assert consumed.signing_payload == challenge.signing_payload
        with pytest.raises(DeviceRotationChallengeError) as replay:
            await consume_device_rotation_challenge(
                challenge_nonce=challenge.nonce,
                patient_id=patient_id,
                session_id=session_id,
                device_id=device_id,
                current_key_version=1,
                new_public_key_fingerprint=fingerprint,
            )
    assert replay.value.code == "DEVICE_ROTATION_CHALLENGE_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("patient_id", str(uuid.uuid4())),
        ("session_id", f"session-{uuid.uuid4().hex}"),
        ("device_id", str(uuid.uuid4())),
        ("current_key_version", 9),
        ("new_public_key_fingerprint", "d" * 64),
    ],
)
async def test_challenge_rejects_wrong_binding(field, wrong_value):
    redis = _FakeRedisNoEval()
    values = {
        "patient_id": str(uuid.uuid4()),
        "session_id": f"session-{uuid.uuid4().hex}",
        "device_id": str(uuid.uuid4()),
        "current_key_version": 2,
        "new_public_key_fingerprint": "e" * 64,
    }
    with (
        patch(
            "app.services.patient_device_rotation.get_redis_client",
            return_value=redis,
        ),
        patch(
            "app.services.patient_device_rotation.resolve_patient_session_id",
            new=AsyncMock(return_value={"status": "active"}),
        ),
    ):
        challenge = await issue_device_rotation_challenge(**values)
        attempted = dict(values)
        attempted[field] = wrong_value
        with pytest.raises(DeviceRotationChallengeError) as exc_info:
            await consume_device_rotation_challenge(
                challenge_nonce=challenge.nonce, **attempted
            )
    assert exc_info.value.code == "DEVICE_ROTATION_BINDING_MISMATCH"


def test_signature_requires_current_private_key_and_exact_payload():
    current_private, current_public = _keypair()
    wrong_private, _ = _keypair()
    kwargs = _canonical_kwargs()
    payload = canonical_device_rotation_payload(**kwargs)
    signature = _sign(current_private, payload)
    assert verify_device_rotation_signature(
        public_key_der=current_public,
        signing_payload=payload,
        signature_b64=signature,
    )
    assert not verify_device_rotation_signature(
        public_key_der=current_public,
        signing_payload=payload,
        signature_b64=_sign(wrong_private, payload),
    )
    tampered = dict(kwargs)
    tampered["current_key_version"] = 4
    assert not verify_device_rotation_signature(
        public_key_der=current_public,
        signing_payload=canonical_device_rotation_payload(**tampered),
        signature_b64=signature,
    )


@pytest.mark.asyncio
async def test_rotation_authority_redis_unavailable_fails_closed():
    with (
        patch(
            "app.services.patient_device_rotation.get_redis_client",
            return_value=_UnavailableRedis(),
        ),
        patch(
            "app.services.patient_device_rotation.resolve_patient_session_id",
            new=AsyncMock(return_value={"status": "active"}),
        ),
    ):
        with pytest.raises(DeviceRotationAuthorityUnavailable):
            await issue_device_rotation_challenge(
                patient_id=str(uuid.uuid4()),
                session_id=f"session-{uuid.uuid4().hex}",
                device_id=str(uuid.uuid4()),
                current_key_version=1,
                new_public_key_fingerprint="f" * 64,
            )


@pytest.mark.asyncio
async def test_challenge_route_binds_authoritative_session_and_current_version():
    patient_id = str(uuid.uuid4())
    session_id = f"session-{uuid.uuid4().hex}"
    device_id = uuid.uuid4()
    _, new_public = _keypair()
    canonical = canonicalize_p256_public_key(new_public)
    patient = AuthenticatedPatientSession(
        patient_id=patient_id,
        patient=MagicMock(),
        session_id=session_id,
        session_epoch=7,
        supabase_user_id="subject-1",
    )
    current = MagicMock(key_version=4)
    issued = MagicMock(
        nonce="challenge-" + "x" * 32,
        current_key_version=4,
        new_public_key_fingerprint=canonical.fingerprint,
        issued_at=datetime.now(timezone.utc).isoformat(),
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
        signing_payload=b"canonical-payload",
    )
    with (
        patch(
            "app.api.v2.device_routes.get_active_patient_device_key",
            new=AsyncMock(return_value=current),
        ),
        patch(
            "app.api.v2.device_routes.assert_rotation_new_key_available",
            new=AsyncMock(),
        ),
        patch(
            "app.api.v2.device_routes.issue_device_rotation_challenge",
            new=AsyncMock(return_value=issued),
        ) as issue,
    ):
        response = await issue_rotation_challenge(
            str(device_id),
            DeviceRotationChallengeRequest(
                new_device_public_key=base64.b64encode(new_public).decode("ascii")
            ),
            patient=patient,
            db=AsyncMock(),
        )
    issue.assert_awaited_once_with(
        patient_id=patient_id,
        session_id=session_id,
        device_id=str(device_id),
        current_key_version=4,
        new_public_key_fingerprint=canonical.fingerprint,
    )
    assert response.current_key_version == 4
    assert base64.b64decode(response.signing_payload_b64) == b"canonical-payload"


@pytest.mark.asyncio
async def test_rotate_route_consumes_exact_session_authority_before_db_mutation():
    patient_id = str(uuid.uuid4())
    session_id = f"session-{uuid.uuid4().hex}"
    device_id = uuid.uuid4()
    _, new_public = _keypair()
    canonical = canonicalize_p256_public_key(new_public)
    patient = AuthenticatedPatientSession(
        patient_id=patient_id,
        patient=MagicMock(),
        session_id=session_id,
        session_epoch=3,
        supabase_user_id="subject-1",
    )
    challenge = MagicMock(signing_payload=b"canonical-rotation")
    result = PatientDeviceRotationResult(
        device_id=device_id,
        old_key_id=uuid.uuid4(),
        new_key_id=uuid.uuid4(),
        old_key_version=1,
        new_key_version=2,
        new_public_key_fingerprint=canonical.fingerprint,
        rotated_at=datetime.now(timezone.utc),
        status="active",
    )
    payload = DeviceRotateRequest(
        challenge_nonce="n" * 43,
        current_key_version=1,
        new_device_public_key=base64.b64encode(new_public).decode("ascii"),
        signature="s" * 64,
    )
    with (
        patch(
            "app.api.v2.device_routes.consume_device_rotation_challenge",
            new=AsyncMock(return_value=challenge),
        ) as consume,
        patch(
            "app.api.v2.device_routes.rotate_patient_device_key",
            new=AsyncMock(return_value=result),
        ) as rotate,
    ):
        response = await rotate_device(
            str(device_id), payload, patient=patient, db=AsyncMock()
        )
    consume.assert_awaited_once_with(
        challenge_nonce="n" * 43,
        patient_id=patient_id,
        session_id=session_id,
        device_id=str(device_id),
        current_key_version=1,
        new_public_key_fingerprint=canonical.fingerprint,
    )
    rotate.assert_awaited_once()
    assert consume.await_count == 1
    assert rotate.await_count == 1
    assert response.new_key_version == 2
