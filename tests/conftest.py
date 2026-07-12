"""
Shared test setup.
"""
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

if "document_processor" not in sys.modules:
    _stub = types.ModuleType("document_processor")
    _stub.extract_document_data = lambda file_path: {}
    sys.modules["document_processor"] = _stub


os.environ.setdefault("KEK_ROOT_SECRET", "test-kek-root-secret-32-bytes-minimum")
os.environ.setdefault("NEXA_PEPPER_KEY", "test-pepper-key")
os.environ.setdefault("ENVIRONMENT", "test")

from app.main import app


class AwaitableResponse:
    """Response proxy that can be used directly or awaited."""

    def __init__(self, response):
        self._response = response

    def __await__(self):
        async def _return_response():
            return self._response

        return _return_response().__await__()

    def __getattr__(self, name):
        return getattr(self._response, name)


class DualModeTestClient:
    """Expose TestClient methods in both sync and async test styles."""

    def __init__(self, app):
        self._client = TestClient(app)

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            response = attr(*args, **kwargs)
            try:
                import asyncio
                asyncio.get_running_loop()
            except RuntimeError:
                return response
            return AwaitableResponse(response)

        return call


class FakeRedisPipeline:
    def __init__(self, redis):
        self.redis = redis
        self.ops = []

    def incr(self, key):
        self.ops.append(("incr", key))
        return self

    def expire(self, key, ttl):
        self.ops.append(("expire", key, ttl))
        return self

    async def execute(self):
        import time
        results = []
        for op in self.ops:
            if op[0] == "incr":
                key = op[1]
                val = int(self.redis.data.get(key, 0)) + 1
                self.redis.data[key] = str(val)
                results.append(val)
            elif op[0] == "expire":
                _, key, ttl = op
                self.redis.ttls[key] = time.time() + ttl
                results.append(True)
        return results


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

    async def set(self, key, value, ex=None, nx=False):
        import time
        if nx and key in self.data:
            return False
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
        return FakeRedisPipeline(self)

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

class FakeSyncRedis:
    def __init__(self, async_redis):
        self._a = async_redis

    def get(self, key):
        import time
        if key in self._a.ttls and self._a.ttls[key] < time.time():
            del self._a.data[key]
            del self._a.ttls[key]
            return None
        return self._a.data.get(key)

    def setex(self, key, ttl, value):
        import time
        self._a.data[key] = value
        self._a.ttls[key] = time.time() + ttl
        return True

    def set(self, key, value, ex=None, nx=False):
        import time
        if nx and key in self._a.data:
            return False
        self._a.data[key] = value
        if ex:
            self._a.ttls[key] = time.time() + ex
        return True

    def delete(self, key):
        self._a.data.pop(key, None)
        self._a.ttls.pop(key, None)
        return 1

    def pipeline(self):
        return FakeRedisPipeline(self._a)


@pytest.fixture
def mock_redis():
    return FakeRedis()

@pytest.fixture
def test_client():
    return DualModeTestClient(app)

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
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.execute = AsyncMock()
    session.execute.return_value = MagicMock()
    return session

@pytest.fixture
def test_db(mock_db):
    return mock_db

@pytest.fixture(autouse=True)
def override_deps(request, mock_db, mock_redis):
    from app.core.database import get_db_session
    from app.api.v2.patient_routes import get_kms_provider
    from app.services.consent_engine import get_consent_redis_client
    from app.core.redis import get_redis_client
    from contextlib import ExitStack
    
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

    sync_redis = FakeSyncRedis(mock_redis)
    patches = [
         patch("app.core.redis.get_redis_client", return_value=sync_redis),
         patch("app.api.v2.merge_routes.get_redis_client", return_value=sync_redis),
         patch("app.api.v2.auth_routes.get_redis_client", return_value=sync_redis),
         patch("app.api.v2.consent_routes.get_redis_client", return_value=sync_redis),
         patch("app.services.provider_auth_service.get_redis_client", return_value=sync_redis),
         patch("app.api.v2.assurance_routes.get_redis_client", return_value=mock_redis),
         patch("app.api.v2.assurance_routes.push_service.send_approval_request", return_value=None),
         patch("app.core.supabase.get_supabase_client", return_value=mock_supabase),
         patch("app.observability.audit_ledger.get_supabase_client", return_value=mock_supabase),
         patch("app.services.consent_engine.get_consent_redis_client", return_value=mock_redis),
         patch("app.services.biometric_signature_verifier.get_supabase_client", return_value=mock_supabase),
         patch("app.observability.audit_ledger.append_audit_log_or_503", return_value=None),
         patch("app.observability.audit_ledger.append_audit_log", return_value=True),
         patch("app.services.consent_engine.append_audit_log_or_503", return_value=None),
         patch("app.services.consent_engine.append_audit_log", return_value=True),
    ]
    if "test_push_rate_limits" not in str(request.node.nodeid):
        patches.append(patch("app.api.v2.assurance_routes.push_limiter.check_and_acquire", return_value=None))
        patches.append(patch("app.api.v2.assurance_routes.push_limiter.release", return_value=None))

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield

    app.dependency_overrides.clear()
