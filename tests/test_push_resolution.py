"""Atomic resolution tests for canonical signed consent decisions."""

from unittest.mock import AsyncMock

import pytest

from app.api.v2.consent_routes import _resolve_signed_approval_atomic


@pytest.mark.asyncio
async def test_atomic_resolution_success():
    redis = AsyncMock()
    redis.eval.return_value = 1
    resolved = await _resolve_signed_approval_atomic(
        redis,
        "request-id",
        "nonce",
        {"status": "approved"},
        300,
    )
    assert resolved is True
    args = redis.eval.await_args.args
    assert args[1] == 2
    assert args[2] == "consent_request:request-id"
    assert args[3] == "biometric_nonce:nonce:used"


@pytest.mark.asyncio
async def test_already_resolved_rejection():
    redis = AsyncMock()
    redis.eval.return_value = 0
    resolved = await _resolve_signed_approval_atomic(
        redis,
        "request-id",
        "nonce",
        {"status": "denied"},
        300,
    )
    assert resolved is False
