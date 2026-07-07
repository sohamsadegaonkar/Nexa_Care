import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch, MagicMock
import json
import uuid

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
        assert response.json()["status"] == "pending"
        assert "challenge_nonce" in response.json()
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
