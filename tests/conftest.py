"""
Shared test setup.
"""
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

if "document_processor" not in sys.modules:
    _stub = types.ModuleType("document_processor")
    _stub.extract_document_data = lambda file_path: {}
    sys.modules["document_processor"] = _stub

from app.main import app

class FakeRedis:
    def __init__(self):
        self.data = {}
        self.ttls = {}
        self._scripts = {}

    async def get(self, key):
        import time
        if key in self.ttls and self.ttls[key] < time.time():
            del self.data[key]
            del self.ttls[key]
            return None
        return self.data.get(key)

    async def setex(self, key, ttl, value):
        import time
        self.data[key] = value
        self.ttls[key] = time.time() + ttl
        return True

    async def set(self, key, value, ex=None):
        import time
        self.data[key] = value
        if ex:
            self.ttls[key] = time.time() + ex
        return True

    async def delete(self, key):
        self.data.pop(key, None)
        self.ttls.pop(key, None)
        return 1

    async def getdel(self, key):
        val = await self.get(key)
        await self.delete(key)
        return val

    async def incr(self, key):
        val = int(self.data.get(key, 0)) + 1
        self.data[key] = str(val)
        return val

    async def expire(self, key, ttl, nx=False):
        import time
        if nx and key in self.ttls:
             return 0
        self.ttls[key] = time.time() + ttl
        return 1

    def pipeline(self):
        return self

    async def execute(self):
        # Very simple mock for pipeline execution
        return [int(self.data.get(list(self.data.keys())[-1], 0))]

    def register_script(self, script_body):
        import json
        # We'll mock the specific PUSH resolve script logic
        async def run_script(keys=None, args=None):
            key = keys[0]
            current = self.data.get(key)
            if not current:
                return 'EXPIRED'
            data = json.loads(current)
            if data['status'] != 'pending':
                return 'ALREADY_RESOLVED'
            data['status'] = args[0]
            data['responded_at'] = args[1]
            data['biometric_token_hash'] = args[2]
            self.data[key] = json.dumps(data)
            return 'OK'
        return run_script

@pytest.fixture
def mock_redis():
    return FakeRedis()

@pytest.fixture
def test_client():
    return TestClient(app)

@pytest.fixture
def admin_token():
    return "admin-test-token"

@pytest.fixture
def admin_context():
    import uuid
    from app.models.provider_context import ProviderContext, ProviderIdentityContext, HospitalContext, AffiliationContext
    from app.models.provider import AffiliationType
    return ProviderContext(
        provider=ProviderIdentityContext(provider_id=uuid.uuid4(), display_name="Admin", contact_email="a@ex.com"),
        hospital=HospitalContext(hospital_id=uuid.uuid4(), facility_code="H", display_name="H"),
        affiliation=AffiliationContext(affiliation_id=uuid.uuid4(), affiliation_type=AffiliationType.PERMANENT, is_primary=True, roles=["admin"])
    )

@pytest.fixture
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}

@pytest.fixture
def mock_db():
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.execute = AsyncMock()
    session.execute.return_value = MagicMock()
    return session

@pytest.fixture
def test_db(mock_db):
    return mock_db

@pytest.fixture(autouse=True)
def override_deps(mock_db, mock_redis):
    from app.core.database import get_db_session
    from app.api.v2.patient_routes import get_kms_provider
    from app.services.consent_engine import get_consent_redis_client
    from app.core.redis import get_redis_client
    
    get_consent_redis_client.cache_clear()
    get_redis_client.cache_clear()
    
    app.dependency_overrides[get_db_session] = lambda: mock_db
    app.dependency_overrides[get_kms_provider] = lambda: AsyncMock()
    
    # Reset cached scripts in the service singleton
    from app.api.v2.assurance_routes import service
    service._resolve_script = None

    # Mocking Supabase client globally
    mock_supabase = MagicMock()
    mock_supabase.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    mock_supabase.table.return_value.insert.return_value.execute.return_value = MagicMock()
    mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(data={})

    with patch("app.core.redis.get_redis_client", return_value=mock_redis), \
         patch("app.api.v2.assurance_routes.get_redis_client", return_value=mock_redis), \
         patch("app.api.v2.assurance_routes.push_limiter.check_and_acquire", return_value=None), \
         patch("app.api.v2.assurance_routes.push_limiter.release", return_value=None), \
         patch("app.api.v2.assurance_routes.push_service.send_approval_request", return_value=None), \
         patch("app.core.supabase.get_supabase_client", return_value=mock_supabase), \
         patch("app.observability.audit_ledger.get_supabase_client", return_value=mock_supabase), \
         patch("app.services.consent_engine.get_consent_redis_client", return_value=mock_redis), \
         patch("app.services.biometric_signature_verifier.get_supabase_client", return_value=mock_supabase), \
         patch("app.observability.audit_ledger.append_audit_log_or_503", return_value=None), \
         patch("app.observability.audit_ledger.append_audit_log", return_value=True), \
         patch("app.services.consent_engine.append_audit_log_or_503", return_value=None), \
         patch("app.services.consent_engine.append_audit_log", return_value=True):
        yield

    app.dependency_overrides.clear()
