import pytest
import json
import uuid
import base64
import time
from unittest.mock import AsyncMock, patch, MagicMock

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from fastapi.testclient import TestClient

from app.main import app
from app.core.dependencies import get_scoped_session, get_db_session

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def key_pair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    return private_key, public_key

@pytest.fixture
def public_key_der(key_pair):
    _, public_key = key_pair
    return public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )

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
        async def delete(self, k):
            self.data.pop(k, None)
            return 1
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
    db.execute = AsyncMock()
    return db

@pytest.mark.asyncio
async def test_valid_signature_happy_path(client, key_pair, public_key_der, mock_redis, mock_db):
    private_key, _ = key_pair
    patient_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    nonce = "test-nonce"
    
    mock_res = MagicMock()
    mock_res.data = {"device_public_key": base64.b64encode(public_key_der).decode(), "revoked_at": None}
    
    with patch("app.services.biometric_signature_verifier.get_supabase_client") as mock_supabase, \
         patch("app.api.v2.assurance_routes.get_redis_client", return_value=mock_redis), \
         patch("app.api.v2.assurance_routes.append_audit_log_or_503", new_callable=AsyncMock, return_value=None), \
         patch("app.services.assurance_service.append_audit_log_or_503", new_callable=AsyncMock, return_value=None), \
         patch("app.services.assurance_service.AssuranceService._get_resolve_script", return_value=mock_redis.register_script("")):
        
        mock_supabase.return_value.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = mock_res
        await mock_redis.setex(f"push_request:{request_id}", 90, json.dumps({"status": "pending", "patient_id": patient_id}))
        
        message = f"{nonce}{request_id}{patient_id}".encode("utf-8")
        signature = private_key.sign(message, ec.ECDSA(hashes.SHA256()))
        signature_b64 = base64.b64encode(signature).decode()
        
        app.dependency_overrides[get_scoped_session] = lambda: patient_id
        app.dependency_overrides[get_db_session] = lambda: mock_db
        
        payload = {"decision": "approved", "signature": signature_b64, "nonce": nonce}
        response = client.post(f"/api/v2/push/{request_id}/respond", json=payload)
        assert response.status_code == 200

    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_invalid_signature_rejection(client, public_key_der, mock_redis):
    patient_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    nonce = "test-nonce"
    mock_res = MagicMock()
    mock_res.data = {"device_public_key": base64.b64encode(public_key_der).decode(), "revoked_at": None}
    
    with patch("app.services.biometric_signature_verifier.get_supabase_client") as mock_supabase, \
         patch("app.api.v2.assurance_routes.get_redis_client", return_value=mock_redis), \
         patch("app.api.v2.assurance_routes.append_audit_log_or_503", new_callable=AsyncMock, return_value=None):
        
        mock_supabase.return_value.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = mock_res
        app.dependency_overrides[get_scoped_session] = lambda: patient_id
        
        payload = {"decision": "approved", "signature": "YmFkLXNpZ25hdHVyZQ==", "nonce": nonce}
        response = client.post(f"/api/v2/push/{request_id}/respond", json=payload)
        assert response.status_code == 401

    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_replayed_nonce_rejection(client, key_pair, public_key_der, mock_redis):
    patient_id = "p1"
    nonce = "replayed"
    await mock_redis.setex(f"biometric_nonce:{nonce}:used", 120, "1")
    app.dependency_overrides[get_scoped_session] = lambda: patient_id
    
    with patch("app.api.v2.assurance_routes.get_redis_client", return_value=mock_redis), \
         patch("app.api.v2.assurance_routes.append_audit_log_or_503", new_callable=AsyncMock, return_value=None):
        payload = {"decision": "approved", "signature": "YmFkLXNpZ25hdHVyZQ==", "nonce": nonce}
        response = client.post("/api/v2/push/req/respond", json=payload)
        assert response.status_code == 401
        assert "Nonce already used" in response.json()["detail"]

    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_timing_side_channel_enforced(mock_redis):
    from app.services.biometric_signature_verifier import BiometricSignatureVerifier
    verifier = BiometricSignatureVerifier()
    start = time.monotonic()
    with patch("app.services.biometric_signature_verifier.get_supabase_client") as mock_s:
        mock_s.return_value.table.return_value.select.return_value.eq.return_value.single.return_value.execute.side_effect = Exception("Fast")
        await verifier.verify_signature("p", "r", "c2ln", "nonce", mock_redis, AsyncMock())
    assert time.monotonic() - start >= 0.05

@pytest.mark.asyncio
async def test_device_not_enrolled(client, mock_redis):
    app.dependency_overrides[get_scoped_session] = lambda: "p1"
    with patch("app.services.biometric_signature_verifier.get_supabase_client") as mock_supabase, \
         patch("app.api.v2.assurance_routes.get_redis_client", return_value=mock_redis), \
         patch("app.api.v2.assurance_routes.append_audit_log_or_503", new_callable=AsyncMock, return_value=None):
        mock_supabase.return_value.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = None
        payload = {"decision": "approved", "signature": "c2ln", "nonce": "nonce"}
        response = client.post("/api/v2/push/req/respond", json=payload)
        assert response.status_code == 401
        assert "Device not enrolled" in response.json()["detail"]
    app.dependency_overrides.clear()
