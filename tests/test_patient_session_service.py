from __future__ import annotations

import hashlib
import time
from collections import defaultdict

import pytest

from app.services.patient_session_service import (
    PATIENT_SESSION_INDEX_PREFIX,
    PATIENT_SESSION_PREFIX,
    PatientSessionStoreUnavailable,
    activate_patient_session,
    revoke_all_patient_sessions,
    revoke_patient_session,
    session_binding_from_claims,
    validate_patient_session,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = defaultdict(set)
        self.expiries: dict[str, int] = {}

    def setex(self, key: str, ttl: int, value: str) -> bool:
        self.values[key] = value
        self.expiries[key] = ttl
        return True

    def get(self, key: str):
        return self.values.get(key)

    def sadd(self, key: str, value: str) -> int:
        before = len(self.sets[key])
        self.sets[key].add(value)
        return int(len(self.sets[key]) != before)

    def expire(self, key: str, ttl: int) -> bool:
        self.expiries[key] = ttl
        return True

    def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    def srem(self, key: str, value: str) -> int:
        if value not in self.sets.get(key, set()):
            return 0
        self.sets[key].remove(value)
        return 1

    def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self.values:
                del self.values[key]
                deleted += 1
            if key in self.sets:
                del self.sets[key]
                deleted += 1
            self.expiries.pop(key, None)
        return deleted


class FailingRedis:
    def get(self, _key: str):
        raise RuntimeError("redis unavailable")

    def setex(self, _key: str, _ttl: int, _value: str):
        raise RuntimeError("redis unavailable")


def _claims(*, patient_id: str = "patient-1", jti: str = "session-jti-1") -> dict:
    now = int(time.time())
    return {
        "sub": patient_id,
        "actor_type": "patient",
        "patient_id": patient_id,
        "supabase_user_id": "supabase-user-1",
        "auth_method": "phone_otp",
        "iat": now,
        "exp": now + 900,
        "jti": jti,
    }


@pytest.mark.asyncio
async def test_session_activation_creates_server_authority_and_binding(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(
        "app.services.patient_session_service.get_async_redis_client",
        lambda: redis,
    )
    claims = _claims()

    session = await activate_patient_session(claims)

    assert session.patient_id == "patient-1"
    assert session.session_binding == hashlib.sha256(b"session-jti-1").hexdigest()
    assert session_binding_from_claims(claims) == session.session_binding
    assert await validate_patient_session(claims) == session

    session_keys = [key for key in redis.values if key.startswith(PATIENT_SESSION_PREFIX)]
    assert len(session_keys) == 1
    assert "session-jti-1" not in session_keys[0]
    assert any(key.startswith(PATIENT_SESSION_INDEX_PREFIX) for key in redis.sets)


@pytest.mark.asyncio
async def test_missing_or_mismatched_server_session_is_not_authority(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(
        "app.services.patient_session_service.get_async_redis_client",
        lambda: redis,
    )
    claims = _claims()
    assert await validate_patient_session(claims) is None

    await activate_patient_session(claims)
    tampered = dict(claims)
    tampered["supabase_user_id"] = "different-subject"
    assert await validate_patient_session(tampered) is None


@pytest.mark.asyncio
async def test_single_session_revocation_is_immediate(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(
        "app.services.patient_session_service.get_async_redis_client",
        lambda: redis,
    )
    claims = _claims()
    await activate_patient_session(claims)
    assert await validate_patient_session(claims) is not None

    assert await revoke_patient_session(claims) is True
    assert await validate_patient_session(claims) is None
    assert await revoke_patient_session(claims) is False


@pytest.mark.asyncio
async def test_all_session_revocation_invalidates_every_patient_session(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(
        "app.services.patient_session_service.get_async_redis_client",
        lambda: redis,
    )
    first = _claims(jti="session-jti-1")
    second = _claims(jti="session-jti-2")
    await activate_patient_session(first)
    await activate_patient_session(second)

    assert await revoke_all_patient_sessions("patient-1") == 2
    assert await validate_patient_session(first) is None
    assert await validate_patient_session(second) is None


@pytest.mark.asyncio
async def test_session_store_failure_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "app.services.patient_session_service.get_async_redis_client",
        lambda: FailingRedis(),
    )
    claims = _claims()

    with pytest.raises(PatientSessionStoreUnavailable):
        await activate_patient_session(claims)
    with pytest.raises(PatientSessionStoreUnavailable):
        await validate_patient_session(claims)


def test_structurally_invalid_claims_have_no_session_binding():
    claims = _claims()
    claims.pop("jti")
    assert session_binding_from_claims(claims) is None
