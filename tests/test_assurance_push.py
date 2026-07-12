import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch, MagicMock
import json
import uuid
from pathlib import Path

from app.main import app
from app.core.dependencies import get_current_provider, get_scoped_session, get_db_session

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def mock_redis():
    class FakeRedis:
        def __init__(self):
            self.data = {}
        async def set(self, key, value, nx=False, ex=None):
            if nx and key in self.data:
                return False
            self.data[key] = value
            return True
        async def setex(self, key, ttl, value):
            self.data[key] = value
            return True
        async def get(self, key):
            return self.data.get(key)
        async def ttl(self, key):
            return 90 if key in self.data else -2
        async def delete(self, key):
            return self.data.pop(key, None) is not None
    return FakeRedis()

@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=res)
    return db

@pytest.fixture(autouse=True)
def mock_audit():
    with patch("app.api.v2.assurance_routes.append_audit_log_or_503", new_callable=AsyncMock, return_value=None), \
         patch("app.services.assurance_service.append_audit_log_or_503", new_callable=AsyncMock, return_value=None):
        yield

@pytest.mark.asyncio
async def test_initiate_request_creates_pending_record(client, mock_redis, mock_db):
    app.dependency_overrides[get_current_provider] = lambda: MagicMock(actor_uid=str(uuid.uuid4()))
    app.dependency_overrides[get_db_session] = lambda: mock_db
    with patch("app.api.v2.assurance_routes.get_redis_client", return_value=mock_redis):
        payload = {"patient_id": str(uuid.uuid4()), "provider_id": str(uuid.uuid4()), "purpose": "t", "scope": "s"}
        response = client.post("/api/v2/push/request", json=payload, headers={"Authorization": "Bearer doc"})
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "pending"
        assert "challenge_nonce" in body
        assert body["notification_dispatch"] == "unavailable"
        assert body["delivery_status"] == "unavailable"
        assert "notification_sent" not in body
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_respond_approves(client, mock_redis, mock_db):
    request_id = str(uuid.uuid4())
    patient_id = str(uuid.uuid4())
    await mock_redis.setex(f"push_request:{request_id}", 90, json.dumps({"patient_id": patient_id, "status": "pending"}))
    app.dependency_overrides[get_scoped_session] = lambda: patient_id
    app.dependency_overrides[get_db_session] = lambda: mock_db
    with patch("app.api.v2.assurance_routes.get_redis_client", return_value=mock_redis), \
         patch("app.api.v2.assurance_routes.bio_verifier.verify_signature", return_value=MagicMock(verified=True)), \
         patch("app.services.assurance_service.AssuranceService._get_resolve_script", return_value=mock_redis.register_script if hasattr(mock_redis, 'register_script') else AsyncMock()):
        
        # mock resolve_push_approval since mock_redis might not have scripts in this simplified fake
        with patch("app.services.assurance_service.AssuranceService.resolve_push_approval", return_value={"status": "approved"}):
            payload = {"decision": "approved", "signature": "sig", "nonce": "nonce"}
            response = client.post(f"/api/v2/push/{request_id}/respond", json=payload, headers={"Authorization": "Bearer pat"})
            assert response.status_code == 200
            assert response.json()["status"] == "approved"
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_delivery_result_updates_status_metadata(mock_redis, mock_db):
    from app.services.assurance_service import AssuranceService

    service = AssuranceService()
    created = await service.create_push_request(
        redis=mock_redis,
        db=mock_db,
        patient_id=str(uuid.uuid4()),
        provider_id=str(uuid.uuid4()),
        purpose="t",
        scope="s",
    )
    await service.mark_delivery_result(mock_redis, created["request_id"], success=False, error="Expo down")
    status = await service.get_push_status(mock_redis, mock_db, created["request_id"])

    assert status["status"] == "pending"
    assert status["doctor_status"] == "delivery_failed"
    assert status["delivery_status"] == "failed"
    assert status["delivery_error"] == "Expo down"


def test_push_notification_service_does_not_log_raw_patient_id():
    source = Path("app/services/push_notification_service.py").read_text()
    assert "to {patient_id}" not in source
    assert "for {patient_id}" not in source
    assert "patient_ref" in source
