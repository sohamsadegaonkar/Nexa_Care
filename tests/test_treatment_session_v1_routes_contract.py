from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

from app.api.v2 import treatment_session_v1_routes as routes
from app.services.signed_treatment_session_v1 import (
    SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
    treatment_context_hash_v1,
)


class _MemoryRedis:
    def __init__(self, values: dict[str, str] | None = None):
        self.values = values or {}

    async def get(self, key: str):
        return self.values.get(key)


def _pending_request(*, patient_id: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    patient_id = patient_id or str(uuid.uuid4())
    context = {
        "request_id": str(uuid.uuid4()),
        "patient_id": patient_id,
        "provider_id": str(uuid.uuid4()),
        "hospital_id": str(uuid.uuid4()),
        "provider_session_binding_hash": hashlib.sha256(b"provider-session-a").hexdigest(),
        "challenge_nonce": "ab" * 32,
        "purpose": "treatment",
        "allowed_operations": ["CREATE_ENCOUNTER", "READ_CLINICAL_HISTORY"],
        "access_duration": 900,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=120)).isoformat(),
    }
    return {
        **context,
        "created_at": context["issued_at"],
        "protocol_version": SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
        "treatment_context_hash": treatment_context_hash_v1(**context),
        "provider_name": "Provider",
        "hospital_name": "Hospital",
        "status": "pending",
    }


def _patient_session(patient_id: str):
    return SimpleNamespace(patient_id=patient_id)


def test_signed_lifecycle_router_does_not_duplicate_claim_router():
    paths = {route.path for route in routes.router.routes}
    assert "/api/v2/treatment-session/v1/request" in paths
    assert "/api/v2/treatment-session/v1/challenge/{request_id}" in paths
    assert "/api/v2/treatment-session/v1/approve-signed" in paths
    assert "/api/v2/treatment-session/v1/{request_id}/claim" not in paths


def test_provider_session_binding_is_one_way_and_stable():
    provider = SimpleNamespace(session_binding="secret-provider-session")
    digest = routes._provider_session_binding_hash(provider)
    assert digest == hashlib.sha256(b"secret-provider-session").hexdigest()
    assert "secret-provider-session" not in digest


def test_provider_session_binding_is_required():
    provider = SimpleNamespace(session_binding="")
    with pytest.raises(HTTPException) as caught:
        routes._provider_session_binding_hash(provider)
    assert caught.value.status_code == 403
    assert caught.value.detail["error_code"] == "TREATMENT_PROVIDER_SESSION_BINDING_REQUIRED"

@pytest.mark.asyncio
async def test_challenge_is_patient_bound(monkeypatch):
    data = _pending_request()
    redis = _MemoryRedis({routes._request_key(data["request_id"]): json.dumps(data)})
    monkeypatch.setattr(routes, "get_async_redis_client", lambda: redis)

    with pytest.raises(HTTPException) as caught:
        await routes.get_treatment_session_v1_challenge(
            request_id=data["request_id"],
            response=Response(),
            session=_patient_session(str(uuid.uuid4())),
        )
    assert caught.value.status_code == 403
    assert caught.value.detail["error_code"] == "TREATMENT_PATIENT_MISMATCH"



@pytest.mark.asyncio
async def test_challenge_exposes_only_provider_session_binding_hash(monkeypatch):
    data = _pending_request()
    raw_binding = "provider-session-a"
    expected_hash = hashlib.sha256(raw_binding.encode("utf-8")).hexdigest()
    assert data["provider_session_binding_hash"] == expected_hash

    redis = _MemoryRedis({routes._request_key(data["request_id"]): json.dumps(data)})
    monkeypatch.setattr(routes, "get_async_redis_client", lambda: redis)

    challenge = await routes.get_treatment_session_v1_challenge(
        request_id=data["request_id"],
        response=Response(),
        session=_patient_session(data["patient_id"]),
    )

    assert challenge.provider_session_binding_hash == expected_hash
    assert raw_binding not in challenge.model_dump_json()

@pytest.mark.asyncio
async def test_challenge_rejects_operation_set_tampering(monkeypatch):
    data = _pending_request()
    data["allowed_operations"] = [
        "CREATE_ENCOUNTER",
        "READ_CLINICAL_HISTORY",
        "WRITE_PRESCRIPTION",
    ]
    redis = _MemoryRedis({routes._request_key(data["request_id"]): json.dumps(data)})
    monkeypatch.setattr(routes, "get_async_redis_client", lambda: redis)

    with pytest.raises(HTTPException) as caught:
        await routes.get_treatment_session_v1_challenge(
            request_id=data["request_id"],
            response=Response(),
            session=_patient_session(data["patient_id"]),
        )
    assert caught.value.status_code == 409
    assert caught.value.detail["error_code"] == "TREATMENT_CONTEXT_INTEGRITY_FAILURE"


@pytest.mark.asyncio
async def test_resolved_request_is_not_exposed_as_pending_challenge(monkeypatch):
    data = _pending_request()
    data["status"] = "approved"
    redis = _MemoryRedis({routes._request_key(data["request_id"]): json.dumps(data)})
    monkeypatch.setattr(routes, "get_async_redis_client", lambda: redis)

    with pytest.raises(HTTPException) as caught:
        await routes.get_treatment_session_v1_challenge(
            request_id=data["request_id"],
            response=Response(),
            session=_patient_session(data["patient_id"]),
        )
    assert caught.value.status_code == 404
    assert caught.value.detail["error_code"] == "TREATMENT_CHALLENGE_NOT_FOUND"
