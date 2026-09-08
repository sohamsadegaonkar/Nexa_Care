"""Direct contracts for authoritative patient session state."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.services.patient_session_authority import (
    PatientSessionAuthorityUnavailable,
    _epoch_key,
    _session_key,
    create_patient_session,
    get_or_create_patient_session_epoch,
    resolve_patient_session_authority,
    resolve_patient_session_id,
    revoke_all_patient_sessions,
    revoke_patient_session,
)


PATIENT = "123e4567-e89b-12d3-a456-426614174001"
OTHER_PATIENT = "123e4567-e89b-12d3-a456-426614174002"
SUBJECT = "supabase-subject-a"
OTHER_SUBJECT = "supabase-subject-b"
SESSION = "session-0123456789abcdef"


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.expiry: dict[str, int] = {}
        self.fail_reads = False
        self.fail_writes = False

    async def get(self, key):
        if self.fail_reads:
            raise RuntimeError("redis read unavailable")
        return self.values.get(key)

    async def set(self, key, value, *, nx=False):
        if self.fail_writes:
            raise RuntimeError("redis write unavailable")
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def setex(self, key, ttl, value):
        if self.fail_writes:
            raise RuntimeError("redis write unavailable")
        self.values[key] = value
        self.expiry[key] = int(ttl)
        return True

    async def delete(self, key):
        if self.fail_writes:
            raise RuntimeError("redis write unavailable")
        existed = key in self.values
        self.values.pop(key, None)
        self.expiry.pop(key, None)
        return int(existed)

    async def incr(self, key):
        if self.fail_writes:
            raise RuntimeError("redis write unavailable")
        current = int(self.values.get(key, 0)) + 1
        self.values[key] = str(current)
        return current


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _claims(*, patient=PATIENT, subject=SUBJECT, sid=SESSION, epoch=0):
    return {
        "patient_id": patient,
        "supabase_user_id": subject,
        "sid": sid,
        "session_epoch": epoch,
    }


async def _seed(redis: _Redis, *, patient=PATIENT, subject=SUBJECT, sid=SESSION, epoch=0):
    redis.values[_epoch_key(patient)] = str(epoch)
    now = _now()
    await create_patient_session(
        patient_id=patient,
        supabase_user_id=subject,
        session_id=sid,
        session_epoch=epoch,
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
    )


@pytest.mark.asyncio
async def test_create_and_resolve_current_session() -> None:
    redis = _Redis()
    with patch("app.services.patient_session_authority.get_redis_client", return_value=redis):
        await _seed(redis)
        resolved = await resolve_patient_session_authority(_claims())
    assert resolved is not None
    assert resolved["patient_id"] == PATIENT
    assert resolved["supabase_user_id"] == SUBJECT
    assert resolved["session_epoch"] == 0
    assert redis.expiry[_session_key(SESSION)] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims",
    [
        {"patient_id": PATIENT, "supabase_user_id": SUBJECT, "session_epoch": 0},
        _claims(sid="short"),
        {"patient_id": PATIENT, "supabase_user_id": SUBJECT, "sid": SESSION},
        _claims(epoch="0"),
        _claims(epoch=-1),
        _claims(epoch=True),
    ],
)
async def test_missing_or_malformed_session_claims_fail_closed(claims) -> None:
    redis = _Redis()
    with patch("app.services.patient_session_authority.get_redis_client", return_value=redis):
        assert await resolve_patient_session_authority(claims) is None


@pytest.mark.asyncio
async def test_patient_mismatch_fails_closed() -> None:
    redis = _Redis()
    with patch("app.services.patient_session_authority.get_redis_client", return_value=redis):
        await _seed(redis)
        assert await resolve_patient_session_authority(_claims(patient=OTHER_PATIENT)) is None


@pytest.mark.asyncio
async def test_supabase_subject_mismatch_fails_closed() -> None:
    redis = _Redis()
    with patch("app.services.patient_session_authority.get_redis_client", return_value=redis):
        await _seed(redis)
        assert await resolve_patient_session_authority(_claims(subject=OTHER_SUBJECT)) is None


@pytest.mark.asyncio
async def test_record_epoch_mismatch_fails_closed() -> None:
    redis = _Redis()
    with patch("app.services.patient_session_authority.get_redis_client", return_value=redis):
        await _seed(redis)
        record = json.loads(redis.values[_session_key(SESSION)])
        record["session_epoch"] = 1
        redis.values[_session_key(SESSION)] = json.dumps(record)
        assert await resolve_patient_session_authority(_claims()) is None


@pytest.mark.asyncio
async def test_current_epoch_mismatch_fails_closed() -> None:
    redis = _Redis()
    with patch("app.services.patient_session_authority.get_redis_client", return_value=redis):
        await _seed(redis)
        redis.values[_epoch_key(PATIENT)] = "1"
        assert await resolve_patient_session_authority(_claims()) is None


@pytest.mark.asyncio
async def test_missing_session_fails_closed() -> None:
    redis = _Redis()
    redis.values[_epoch_key(PATIENT)] = "0"
    with patch("app.services.patient_session_authority.get_redis_client", return_value=redis):
        assert await resolve_patient_session_authority(_claims()) is None


@pytest.mark.asyncio
async def test_exact_revoke_invalidates_only_target_session() -> None:
    redis = _Redis()
    other_session = "session-fedcba9876543210"
    with patch("app.services.patient_session_authority.get_redis_client", return_value=redis):
        await _seed(redis)
        await _seed(redis, sid=other_session)
        assert await revoke_patient_session(patient_id=PATIENT, session_id=SESSION)
        assert await resolve_patient_session_authority(_claims()) is None
        assert await resolve_patient_session_authority(_claims(sid=other_session)) is not None


@pytest.mark.asyncio
async def test_revoke_all_invalidates_old_sessions_and_new_epoch_session_succeeds() -> None:
    redis = _Redis()
    new_session = "session-abcdef0123456789"
    with patch("app.services.patient_session_authority.get_redis_client", return_value=redis):
        await _seed(redis)
        assert await revoke_all_patient_sessions(PATIENT) == 1
        assert await resolve_patient_session_authority(_claims()) is None
        await _seed(redis, sid=new_session, epoch=1)
        assert await resolve_patient_session_authority(_claims(sid=new_session, epoch=1)) is not None


@pytest.mark.asyncio
async def test_redis_read_failure_raises_unavailable() -> None:
    redis = _Redis()
    redis.fail_reads = True
    with patch("app.services.patient_session_authority.get_redis_client", return_value=redis):
        with pytest.raises(PatientSessionAuthorityUnavailable):
            await resolve_patient_session_authority(_claims())


@pytest.mark.asyncio
async def test_redis_write_failure_raises_unavailable() -> None:
    redis = _Redis()
    redis.fail_writes = True
    now = _now()
    with patch("app.services.patient_session_authority.get_redis_client", return_value=redis):
        with pytest.raises(PatientSessionAuthorityUnavailable):
            await create_patient_session(
                patient_id=PATIENT,
                supabase_user_id=SUBJECT,
                session_id=SESSION,
                session_epoch=0,
                issued_at=now,
                expires_at=now + timedelta(minutes=5),
            )


@pytest.mark.asyncio
async def test_corrupt_redis_session_record_fails_closed() -> None:
    redis = _Redis()
    redis.values[_epoch_key(PATIENT)] = "0"
    redis.values[_session_key(SESSION)] = "not-json"
    with patch("app.services.patient_session_authority.get_redis_client", return_value=redis):
        assert await resolve_patient_session_authority(_claims()) is None


@pytest.mark.asyncio
async def test_corrupt_redis_epoch_fails_closed() -> None:
    redis = _Redis()
    with patch("app.services.patient_session_authority.get_redis_client", return_value=redis):
        await _seed(redis)
        redis.values[_epoch_key(PATIENT)] = "corrupt"
        assert await resolve_patient_session_authority(_claims()) is None
        with pytest.raises(PatientSessionAuthorityUnavailable):
            await get_or_create_patient_session_epoch(PATIENT)


@pytest.mark.asyncio
async def test_exact_session_resolver_rejects_patient_substitution() -> None:
    redis = _Redis()
    with patch("app.services.patient_session_authority.get_redis_client", return_value=redis):
        await _seed(redis)
        assert await resolve_patient_session_id(patient_id=OTHER_PATIENT, session_id=SESSION) is None


@pytest.mark.asyncio
async def test_create_rejects_expired_session() -> None:
    redis = _Redis()
    now = _now()
    with patch("app.services.patient_session_authority.get_redis_client", return_value=redis):
        with pytest.raises(ValueError, match="expiry must be in the future"):
            await create_patient_session(
                patient_id=PATIENT,
                supabase_user_id=SUBJECT,
                session_id=SESSION,
                session_epoch=0,
                issued_at=now - timedelta(minutes=2),
                expires_at=now - timedelta(seconds=1),
            )


@pytest.mark.asyncio
async def test_create_rejects_naive_timestamps() -> None:
    redis = _Redis()
    now = datetime.now()
    with patch("app.services.patient_session_authority.get_redis_client", return_value=redis):
        with pytest.raises(ValueError, match="timezone-aware"):
            await create_patient_session(
                patient_id=PATIENT,
                supabase_user_id=SUBJECT,
                session_id=SESSION,
                session_epoch=0,
                issued_at=now,
                expires_at=now + timedelta(minutes=5),
            )
