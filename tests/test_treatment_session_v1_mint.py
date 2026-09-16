from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.services import treatment_session_v1_mint as mint
from app.services.signed_treatment_session_v1 import (
    SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
)


class _MemoryRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    async def eval(self, _script, _num_keys, claim_key, capability_key, payload, digest, ttl):
        assert int(ttl) > 0
        if claim_key in self.values:
            return 0
        self.values[capability_key] = payload
        self.values[claim_key] = digest
        return 1

    async def get(self, key: str):
        return self.values.get(key)

    async def delete(self, key: str):
        self.values.pop(key, None)
        return 1


def _approved_request(*, binding: str = "provider-session-a") -> dict:
    now = datetime.now(timezone.utc)
    return {
        "protocol_version": SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
        "status": "approved",
        "request_id": str(uuid.uuid4()),
        "patient_id": str(uuid.uuid4()),
        "provider_id": str(uuid.uuid4()),
        "hospital_id": str(uuid.uuid4()),
        "purpose": "treatment",
        "allowed_operations": [
            "CREATE_ENCOUNTER",
            "READ_CLINICAL_HISTORY",
            "WRITE_PRESCRIPTION",
        ],
        "provider_session_binding_hash": hashlib.sha256(binding.encode()).hexdigest(),
        "approval_expires_at": (now + timedelta(minutes=10)).isoformat(),
    }


@pytest.mark.asyncio
async def test_mint_binds_exact_provider_session_and_never_stores_raw_binding(monkeypatch):
    redis = _MemoryRedis()
    monkeypatch.setattr(mint, "get_async_redis_client", lambda: redis)
    request = _approved_request()

    token, capability = await mint.mint_treatment_session_v1_capability(
        request_data=request,
        provider_session_binding="provider-session-a",
    )

    digest = mint.treatment_token_hash(token)
    raw = redis.values[f"{mint.TREATMENT_CAPABILITY_PREFIX}{digest}"]
    payload = json.loads(raw)
    assert payload["provider_session_binding_hash"] == hashlib.sha256(
        b"provider-session-a"
    ).hexdigest()
    assert "provider-session-a" not in raw
    assert capability.allowed_operations == (
        "CREATE_ENCOUNTER",
        "READ_CLINICAL_HISTORY",
        "WRITE_PRESCRIPTION",
    )


@pytest.mark.asyncio
async def test_mint_rejects_provider_session_rebinding(monkeypatch):
    redis = _MemoryRedis()
    monkeypatch.setattr(mint, "get_async_redis_client", lambda: redis)

    with pytest.raises(mint.TreatmentSessionV1MintError) as caught:
        await mint.mint_treatment_session_v1_capability(
            request_data=_approved_request(),
            provider_session_binding="provider-session-b",
        )
    assert caught.value.code == "TREATMENT_PROVIDER_SESSION_MISMATCH"
    assert not redis.values


@pytest.mark.asyncio
async def test_mint_is_one_time_per_signed_request(monkeypatch):
    redis = _MemoryRedis()
    monkeypatch.setattr(mint, "get_async_redis_client", lambda: redis)
    request = _approved_request()

    await mint.mint_treatment_session_v1_capability(
        request_data=request,
        provider_session_binding="provider-session-a",
    )
    with pytest.raises(mint.TreatmentSessionV1AlreadyClaimed):
        await mint.mint_treatment_session_v1_capability(
            request_data=request,
            provider_session_binding="provider-session-a",
        )


@pytest.mark.asyncio
async def test_invalidate_removes_claim_and_capability(monkeypatch):
    redis = _MemoryRedis()
    monkeypatch.setattr(mint, "get_async_redis_client", lambda: redis)
    request = _approved_request()
    token, _ = await mint.mint_treatment_session_v1_capability(
        request_data=request,
        provider_session_binding="provider-session-a",
    )
    digest = mint.treatment_token_hash(token)

    await mint.invalidate_treatment_session_v1_request(request["request_id"])

    assert f"{mint.TREATMENT_CLAIM_PREFIX}{request['request_id']}" not in redis.values
    assert f"{mint.TREATMENT_CAPABILITY_PREFIX}{digest}" not in redis.values
