"""Real Redis replay/expiry contracts, enabled only by TEST_REDIS_URL."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import uuid

import pytest
import pytest_asyncio
from redis.asyncio import ConnectionPool, Redis

from app.api.v2.consent_routes import _resolve_signed_approval_atomic


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
        # Close the client without relying on version-specific automatic
        # connection-pool ownership.
        client_close = getattr(client, "aclose", None)
        if client_close is None:
            client_close = getattr(client, "close", None)
        if client_close is not None:
            try:
                await _await_if_needed(client_close())
            except TypeError:
                # Some older redis-py versions expose close arguments
                # differently. The pool is still explicitly disconnected below.
                pass

        # Explicitly close every idle and in-use connection.
        pool_close = getattr(pool, "aclose", None)
        if pool_close is not None:
            await _await_if_needed(pool_close())
        else:
            await pool.disconnect(inuse_connections=True)

        # Allow Windows/Proactor SSL transports to finish shutdown before
        # pytest closes the event loop.
        await asyncio.sleep(0.1)


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
