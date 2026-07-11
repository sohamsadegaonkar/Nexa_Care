"""Comprehensive tests for Consent Assurance verification."""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models.assurance import AssuranceLevel
from app.core.dependencies import get_db_session, get_provider_context

class AsyncFakeRedisClient:
    def __init__(self):
        self._store = {}
    async def get(self, k):
        return self._store.get(k)
    async def set(self, k, v, ex=None):
        self._store[k] = v
        return True
    async def delete(self, k):
        return self._store.pop(k, None)
    async def rpush(self, k, v):
        self._store.setdefault(k, []).append(v)
        return 1

@pytest.fixture
def mock_provider():
    m = MagicMock()
    m.actor_uid = "clinician-123"
    m.hospital_id = "hosp-456"
    return m

@pytest.fixture
def fake_redis():
    return AsyncFakeRedisClient()

@pytest.fixture
def client(fake_redis, mock_provider):
    # Mock audit globally in the verifier and engine
    m_audit = AsyncMock(return_value=True)
    m_audit_503 = AsyncMock(return_value=None)
    
    with patch("app.services.consent_engine.get_consent_redis_client", return_value=fake_redis), \
         patch("app.observability.audit_ledger.append_audit_log", side_effect=m_audit), \
         patch("app.observability.audit_ledger.append_audit_log_or_503", side_effect=m_audit_503), \
         patch("app.services.consent_engine.append_audit_log", side_effect=m_audit), \
         patch("app.services.consent_engine.append_audit_log_or_503", side_effect=m_audit_503), \
         patch("app.api.v2.consent_routes._break_glass_limiter", new=AsyncMock(return_value=None)):
        
        app.dependency_overrides[get_provider_context] = lambda: mock_provider
        db = AsyncMock(spec=AsyncSession)
        db.add = lambda row: None
        app.dependency_overrides[get_db_session] = lambda: db
        
        yield TestClient(app), fake_redis, m_audit
        
        app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_forged_push_biometric_no_redis_record(client):
    test_client, _, m_audit = client
    resp = test_client.post("/api/v2/consent/routine/issue", json={
        "patient_id": "p1",
        "assurance_level": AssuranceLevel.PUSH_BIOMETRIC,
        "assurance_evidence": {"request_id": "missing-id"}
    })
    assert resp.status_code == 403
    assert any(call.kwargs.get("event_type") == "ASSURANCE_VERIFICATION_FAILED" for call in m_audit.call_args_list)

@pytest.mark.asyncio
async def test_forged_push_biometric_wrong_patient(client):
    test_client, redis, _ = client
    await redis.set("push_request:req-1", json.dumps({
        "status": "approved", "patient_id": "wrong", "approved_at": datetime.now(timezone.utc).isoformat()
    }))
    resp = test_client.post("/api/v2/consent/routine/issue", json={
        "patient_id": "p1", "assurance_level": AssuranceLevel.PUSH_BIOMETRIC, "assurance_evidence": {"request_id": "req-1"}
    })
    assert resp.status_code == 403

@pytest.mark.asyncio
async def test_forged_push_biometric_status_pending(client):
    test_client, redis, _ = client
    await redis.set("push_request:req-p", json.dumps({"status": "pending", "patient_id": "p1"}))
    resp = test_client.post("/api/v2/consent/routine/issue", json={
        "patient_id": "p1", "assurance_level": AssuranceLevel.PUSH_BIOMETRIC, "assurance_evidence": {"request_id": "req-p"}
    })
    assert resp.status_code == 403

@pytest.mark.asyncio
async def test_forged_push_biometric_status_denied(client):
    test_client, redis, _ = client
    await redis.set("push_request:req-d", json.dumps({"status": "denied", "patient_id": "p1"}))
    resp = test_client.post("/api/v2/consent/routine/issue", json={
        "patient_id": "p1", "assurance_level": AssuranceLevel.PUSH_BIOMETRIC, "assurance_evidence": {"request_id": "req-d"}
    })
    assert resp.status_code == 403

@pytest.mark.asyncio
async def test_forged_push_biometric_expired_approval(client):
    test_client, redis, _ = client
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    await redis.set("push_request:req-e", json.dumps({"status": "approved", "patient_id": "p1", "approved_at": old}))
    resp = test_client.post("/api/v2/consent/routine/issue", json={
        "patient_id": "p1", "assurance_level": AssuranceLevel.PUSH_BIOMETRIC, "assurance_evidence": {"request_id": "req-e"}
    })
    assert resp.status_code == 403

@pytest.mark.asyncio
async def test_replay_attack_single_use(client):
    test_client, redis, _ = client
    request_id = "req-replay"
    await redis.set(f"push_request:{request_id}", json.dumps({
        "status": "approved", "patient_id": "p1", "approved_at": datetime.now(timezone.utc).isoformat()
    }))
    assert test_client.post("/api/v2/consent/routine/issue", json={
        "patient_id": "p1", "assurance_level": AssuranceLevel.PUSH_BIOMETRIC, "assurance_evidence": {"request_id": request_id}
    }).status_code == 200
    assert test_client.post("/api/v2/consent/routine/issue", json={
        "patient_id": "p1", "assurance_level": AssuranceLevel.PUSH_BIOMETRIC, "assurance_evidence": {"request_id": request_id}
    }).status_code == 403

@pytest.mark.asyncio
async def test_valid_push_biometric_happy_path(client):
    test_client, redis, _ = client
    await redis.set("push_request:req-v", json.dumps({
        "status": "approved", "patient_id": "p1", "approved_at": datetime.now(timezone.utc).isoformat()
    }))
    resp = test_client.post("/api/v2/consent/routine/issue", json={
        "patient_id": "p1", "assurance_level": AssuranceLevel.PUSH_BIOMETRIC, "assurance_evidence": {"request_id": "req-v"}
    })
    assert resp.status_code == 200

@pytest.mark.asyncio
async def test_standard_assurance_happy_path(client):
    test_client, _, _ = client
    assert test_client.post("/api/v2/consent/routine/issue", json={
        "patient_id": "p1", "assurance_level": AssuranceLevel.STANDARD, "assurance_evidence": {}
    }).status_code == 200

@pytest.mark.asyncio
async def test_break_glass_assurance_happy_path(client):
    test_client, _, _ = client
    assert test_client.post("/api/v2/consent/break-glass/issue", json={
        "patient_id": "p1", "reason_code": "EMERGENCY"
    }).status_code == 200

@pytest.mark.asyncio
async def test_redis_unavailable_during_verification(client):
    test_client, redis, _ = client
    with patch.object(redis, 'get', side_effect=RuntimeError("Redis down")):
        assert test_client.post("/api/v2/consent/routine/issue", json={
            "patient_id": "p1", "assurance_level": AssuranceLevel.PUSH_BIOMETRIC, "assurance_evidence": {"request_id": "any"}
        }).status_code == 503

@pytest.mark.asyncio
async def test_invalid_assurance_level_string(client):
    test_client, _, _ = client
    assert test_client.post("/api/v2/consent/routine/issue", json={
        "patient_id": "p1", "assurance_level": "unknown", "assurance_evidence": {}
    }).status_code == 422

@pytest.mark.asyncio
async def test_missing_evidence_for_push_biometric(client):
    test_client, _, _ = client
    assert test_client.post("/api/v2/consent/routine/issue", json={
        "patient_id": "p1", "assurance_level": AssuranceLevel.PUSH_BIOMETRIC, "assurance_evidence": {}
    }).status_code == 403
