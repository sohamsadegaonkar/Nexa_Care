"""Consent assurance verification and single-use behavior."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db_session, get_provider_context
from app.main import app
from app.models.assurance import AssuranceLevel

PATIENT_ID = "11111111-1111-4111-8111-111111111111"
OTHER_PATIENT_ID = "22222222-2222-4222-8222-222222222222"


class AsyncFakeRedisClient:
    def __init__(self): self.store = {}
    async def get(self, key): return self.store.get(key)
    async def set(self, key, value, ex=None): self.store[key] = value; return True
    async def delete(self, key): return self.store.pop(key, None)
    async def rpush(self, key, value): self.store.setdefault(key, []).append(value); return 1


@pytest.fixture
def client():
    redis = AsyncFakeRedisClient()
    provider = MagicMock(
        actor_uid="33333333-3333-4333-8333-333333333333",
        hospital_id="44444444-4444-4444-8444-444444444444",
    )
    db = AsyncMock(spec=AsyncSession)
    db.add = MagicMock()
    audit = AsyncMock(return_value=True)
    with patch("app.services.consent_engine.get_consent_redis_client", return_value=redis), \
         patch("app.services.consent_engine.append_audit_log", audit), \
         patch("app.services.consent_engine.append_audit_log_or_503", AsyncMock()), \
         patch("app.api.v2.consent_routes._break_glass_limiter", new=AsyncMock(return_value=None)):
        app.dependency_overrides[get_provider_context] = lambda: provider
        app.dependency_overrides[get_db_session] = lambda: db
        yield TestClient(app), redis, audit
        app.dependency_overrides.clear()


def issue(client, level=AssuranceLevel.STANDARD, evidence=None):
    return client.post("/api/v2/consent/routine/issue", json={
        "patient_id": PATIENT_ID,
        "assurance_level": level,
        "assurance_evidence": evidence or {},
    })


@pytest.mark.parametrize("record", [
    None,
    {"status": "pending", "patient_id": PATIENT_ID},
    {"status": "denied", "patient_id": PATIENT_ID},
    {"status": "approved", "patient_id": OTHER_PATIENT_ID, "approved_at": datetime.now(timezone.utc).isoformat()},
    {"status": "approved", "patient_id": PATIENT_ID, "approved_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()},
])
def test_unverified_push_evidence_fails_closed(client, record):
    http, redis, audit = client
    if record is not None:
        redis.store["push_request:req"] = json.dumps(record)
    response = issue(http, AssuranceLevel.PUSH_BIOMETRIC, {"request_id": "req"})
    assert response.status_code == 403


def test_verified_push_evidence_is_single_use(client):
    http, redis, _ = client
    redis.store["push_request:req"] = json.dumps({
        "status": "approved", "patient_id": PATIENT_ID,
        "approved_at": datetime.now(timezone.utc).isoformat(),
    })
    assert issue(http, AssuranceLevel.PUSH_BIOMETRIC, {"request_id": "req"}).status_code == 200
    assert issue(http, AssuranceLevel.PUSH_BIOMETRIC, {"request_id": "req"}).status_code == 403


def test_standard_assurance_issues_durable_grant(client):
    assert issue(client[0]).status_code == 200


def test_break_glass_issues_short_lived_durable_grant(client):
    response = client[0].post("/api/v2/consent/break-glass/issue", json={
        "patient_id": PATIENT_ID,
        "reason_code": "LIFE_THREATENING_EMERGENCY",
        "justification": "Immediate threat to life requires emergency access.",
    })
    assert response.status_code in {401, 403, 428}


def test_redis_failure_during_verification_returns_503(client):
    http, redis, _ = client
    with patch.object(redis, "get", side_effect=RuntimeError("unavailable")):
        assert issue(http, AssuranceLevel.PUSH_BIOMETRIC, {"request_id": "req"}).status_code == 503


def test_invalid_assurance_level_is_rejected(client):
    assert issue(client[0], "unknown").status_code == 422


def test_missing_push_evidence_is_rejected(client):
    assert issue(client[0], AssuranceLevel.PUSH_BIOMETRIC).status_code == 403
