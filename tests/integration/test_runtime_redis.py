"""Real Redis replay/expiry contracts, enabled only by TEST_REDIS_URL."""

from __future__ import annotations

import json
import os
import uuid

import pytest
from redis.asyncio import Redis

from app.api.v2.consent_routes import _resolve_signed_approval_atomic


pytestmark = pytest.mark.redis


@pytest.fixture
async def real_redis():
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL is not configured")
    client = Redis.from_url(url, decode_responses=True)
    await client.ping()
    try:
        yield client
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_signed_challenge_is_consumed_atomically_once(real_redis):
    request_id = str(uuid.uuid4())
    nonce = uuid.uuid4().hex
    request_key = f"consent_request:{request_id}"
    nonce_key = f"biometric_nonce:{nonce}:used"
    pending = {"status": "pending", "challenge_nonce": nonce}
    resolved = {"status": "approved", "challenge_nonce": nonce}
    await real_redis.set(request_key, json.dumps(pending), ex=30)
    try:
        first = await _resolve_signed_approval_atomic(real_redis, request_id, nonce, resolved, 30)
        replay = await _resolve_signed_approval_atomic(real_redis, request_id, nonce, resolved, 30)
        assert first is True
        assert replay is False
        assert await real_redis.exists(nonce_key) == 1
    finally:
        await real_redis.delete(request_key, nonce_key)


@pytest.mark.asyncio
async def test_expired_challenge_fails_closed(real_redis):
    request_id = str(uuid.uuid4())
    nonce = uuid.uuid4().hex
    assert await _resolve_signed_approval_atomic(
        real_redis, request_id, nonce, {"status": "approved", "challenge_nonce": nonce}, 30,
    ) is False
