import pytest
import json
import uuid
from unittest.mock import AsyncMock, patch, MagicMock

from app.main import app
from fastapi.testclient import TestClient
from app.core.dependencies import get_scoped_session, get_db_session

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def mock_redis():
    class FakeRedis:
        def __init__(self):
            self.data = {}
        async def get(self, k):
            return self.data.get(k)
        async def setex(self, k, t, v):
            self.data[k] = v
            return True
        async def ttl(self, k):
            return 90 if k in self.data else -2
        def register_script(self, s):
            async def run(keys, args):
                k = keys[0]
                if k not in self.data:
                    return 'EXPIRED'
                d = json.loads(self.data[k])
                if d["status"] != "pending":
                    return 'ALREADY_RESOLVED'
                d["status"] = args[0]
                self.data[k] = json.dumps(d)
                return 'OK'
            return run
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
def mock_global_audit():
    with patch("app.services.assurance_service.append_audit_log", new_callable=AsyncMock, return_value=True), \
         patch("app.services.assurance_service.append_audit_log_or_503", new_callable=AsyncMock, return_value=None), \
         patch("app.api.v2.assurance_routes.append_audit_log_or_503", new_callable=AsyncMock, return_value=None):
        yield

@pytest.mark.asyncio
async def test_atomic_resolution_success(client, mock_redis, mock_db):
    request_id = str(uuid.uuid4())
    patient_id = str(uuid.uuid4())
    await mock_redis.setex(f"push_request:{request_id}", 90, json.dumps({"status": "pending", "patient_id": patient_id}))
    app.dependency_overrides[get_scoped_session] = lambda: patient_id
    app.dependency_overrides[get_db_session] = lambda: mock_db
    with patch("app.api.v2.assurance_routes.get_redis_client", return_value=mock_redis), \
         patch("app.api.v2.assurance_routes.bio_verifier.verify_signature", return_value=MagicMock(verified=True)), \
         patch("app.services.assurance_service.AssuranceService._get_resolve_script", return_value=mock_redis.register_script("")):
        payload = {"decision": "approved", "signature": "sig", "nonce": "nonce"}
        response = client.post(f"/api/v2/push/{request_id}/respond", json=payload)
        assert response.status_code == 200
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_already_resolved_rejection(client, mock_redis, mock_db):
    request_id = str(uuid.uuid4())
    patient_id = str(uuid.uuid4())
    await mock_redis.setex(f"push_request:{request_id}", 90, json.dumps({"status": "approved", "patient_id": patient_id}))
    app.dependency_overrides[get_scoped_session] = lambda: patient_id
    app.dependency_overrides[get_db_session] = lambda: mock_db
    with patch("app.api.v2.assurance_routes.get_redis_client", return_value=mock_redis), \
         patch("app.api.v2.assurance_routes.bio_verifier.verify_signature", return_value=MagicMock(verified=True)), \
         patch("app.services.assurance_service.AssuranceService._get_resolve_script", return_value=mock_redis.register_script("")):
        payload = {"decision": "denied", "signature": "sig", "nonce": "nonce"}
        response = client.post(f"/api/v2/push/{request_id}/respond", json=payload)
        assert response.status_code == 409
    app.dependency_overrides.clear()
