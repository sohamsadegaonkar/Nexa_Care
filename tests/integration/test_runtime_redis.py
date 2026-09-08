"""Real Redis replay/expiry and patient-session authority contracts."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from redis.asyncio import ConnectionPool, Redis

from app.api.v2.consent_routes import _resolve_signed_approval_atomic
from app.services.patient_session_authority import (
    create_patient_session,
    get_or_create_patient_session_epoch,
    resolve_patient_session_authority,
    revoke_all_patient_sessions,
    revoke_patient_session,
)


pytestmark = pytest.mark.redis


async def _await_if_needed(result: object) -> None:
    if inspect.isawaitable(result):
        await result


@pytest_asyncio.fixture
async def real_redis():
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL is not configured")
    pool = ConnectionPool.from_url(url, decode_responses=True)
    client = Redis(connection_pool=pool)

    await client.ping()

    try:
        yield client
    finally:
        client_close = getattr(client, "aclose", None)
        if client_close is None:
            client_close = getattr(client, "close", None)
        if client_close is not None:
            try:
                await _await_if_needed(client_close())
            except TypeError:
                pass

        pool_close = getattr(pool, "aclose", None)
        if pool_close is not None:
            await _await_if_needed(pool_close())
        else:
            await pool.disconnect(inuse_connections=True)

        await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_signed_challenge_is_consumed_atomically_once(real_redis):
    prefix = os.getenv("TEST_REDIS_PREFIX", "")
    request_id = f"{prefix}{uuid.uuid4()}"
    nonce = f"{prefix}{uuid.uuid4().hex}"
    request_key = f"consent_request:{request_id}"
    nonce_key = f"biometric_nonce:{nonce}:used"
    pending = {"status": "pending", "challenge_nonce": nonce}
    resolved = {"status": "approved", "challenge_nonce": nonce}
    await real_redis.set(request_key, json.dumps(pending), ex=30)
    try:
        first = await _resolve_signed_approval_atomic(
            real_redis, request_id, nonce, resolved, 30
        )
        replay = await _resolve_signed_approval_atomic(
            real_redis, request_id, nonce, resolved, 30
        )
        assert first is True
        assert replay is False
        assert await real_redis.exists(nonce_key) == 1
    finally:
        await real_redis.delete(request_key, nonce_key)


@pytest.mark.asyncio
async def test_expired_challenge_fails_closed(real_redis):
    prefix = os.getenv("TEST_REDIS_PREFIX", "")
    request_id = f"{prefix}{uuid.uuid4()}"
    nonce = f"{prefix}{uuid.uuid4().hex}"
    assert (
        await _resolve_signed_approval_atomic(
            real_redis,
            request_id,
            nonce,
            {"status": "approved", "challenge_nonce": nonce},
            30,
        )
        is False
    )


@pytest.mark.asyncio
async def test_patient_session_single_revoke_is_immediate(real_redis):
    patient_id = str(uuid.uuid4())
    subject = f"subject-{uuid.uuid4()}"
    session_id = f"session-{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)
    with patch(
        "app.services.patient_session_authority.get_redis_client",
        return_value=real_redis,
    ):
        epoch = await get_or_create_patient_session_epoch(patient_id)
        await create_patient_session(
            patient_id=patient_id,
            supabase_user_id=subject,
            session_id=session_id,
            session_epoch=epoch,
            issued_at=now,
            expires_at=now + timedelta(seconds=60),
        )
        claims = {
            "patient_id": patient_id,
            "supabase_user_id": subject,
            "sid": session_id,
            "session_epoch": epoch,
        }
        try:
            assert await resolve_patient_session_authority(claims) is not None
            assert await revoke_patient_session(
                patient_id=patient_id, session_id=session_id
            )
            assert await resolve_patient_session_authority(claims) is None
        finally:
            await real_redis.delete(
                f"nexa:patient_session_epoch:{patient_id}"
            )


@pytest.mark.asyncio
async def test_patient_logout_all_invalidates_every_prior_epoch_session(real_redis):
    patient_id = str(uuid.uuid4())
    subject = f"subject-{uuid.uuid4()}"
    session_ids = [f"session-{uuid.uuid4().hex}" for _ in range(2)]
    now = datetime.now(timezone.utc)
    with patch(
        "app.services.patient_session_authority.get_redis_client",
        return_value=real_redis,
    ):
        epoch = await get_or_create_patient_session_epoch(patient_id)
        for session_id in session_ids:
            await create_patient_session(
                patient_id=patient_id,
                supabase_user_id=subject,
                session_id=session_id,
                session_epoch=epoch,
                issued_at=now,
                expires_at=now + timedelta(seconds=60),
            )
        claims = [
            {
                "patient_id": patient_id,
                "supabase_user_id": subject,
                "sid": session_id,
                "session_epoch": epoch,
            }
            for session_id in session_ids
        ]
        try:
            assert all(
                [await resolve_patient_session_authority(item) is not None for item in claims]
            )
            new_epoch = await revoke_all_patient_sessions(patient_id)
            assert new_epoch == epoch + 1
            assert all(
                [await resolve_patient_session_authority(item) is None for item in claims]
            )
        finally:
            keys = [
                "nexa:patient_session:"
                + __import__("hashlib").sha256(item.encode("utf-8")).hexdigest()
                for item in session_ids
            ]
            keys.append(f"nexa:patient_session_epoch:{patient_id}")
            await real_redis.delete(*keys)


@pytest.mark.asyncio
async def test_patient_session_rejects_subject_or_epoch_confusion(real_redis):
    patient_id = str(uuid.uuid4())
    subject = f"subject-{uuid.uuid4()}"
    session_id = f"session-{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)
    with patch(
        "app.services.patient_session_authority.get_redis_client",
        return_value=real_redis,
    ):
        epoch = await get_or_create_patient_session_epoch(patient_id)
        await create_patient_session(
            patient_id=patient_id,
            supabase_user_id=subject,
            session_id=session_id,
            session_epoch=epoch,
            issued_at=now,
            expires_at=now + timedelta(seconds=60),
        )
        session_key = "nexa:patient_session:" + __import__("hashlib").sha256(
            session_id.encode("utf-8")
        ).hexdigest()
        try:
            assert (
                await resolve_patient_session_authority(
                    {
                        "patient_id": patient_id,
                        "supabase_user_id": "different-subject",
                        "sid": session_id,
                        "session_epoch": epoch,
                    }
                )
                is None
            )
            assert (
                await resolve_patient_session_authority(
                    {
                        "patient_id": patient_id,
                        "supabase_user_id": subject,
                        "sid": session_id,
                        "session_epoch": epoch + 1,
                    }
                )
                is None
            )
        finally:
            await real_redis.delete(
                session_key,
                f"nexa:patient_session_epoch:{patient_id}",
            )
