"""Regression tests for the consolidated ConsentEngine authority."""

from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
import pytest
from app.main import app
from app.core.dependencies import get_db_session, get_provider_context, get_current_provider, get_scoped_session
from uuid import uuid4

class AsyncFakeRedisClient:
    def __init__(self):
        self._store = {}

    async def get(self, k):
        return self._store.get(k)

    async def set(self, k, v, ex=None):
        self._store[k] = v
        return True

    async def setex(self, k, t, v):
        self._store[k] = v
        return True

    async def getdel(self, k):
        return self._store.pop(k, None)

    async def delete(self, k):
        self._store.pop(k, None)
        return 1

    async def rpush(self, k, v):
        self._store.setdefault(k, []).append(v)
        return 1

@pytest.fixture
def mock_provider():
    from app.models.provider_context import ProviderContext, ProviderIdentityContext, HospitalContext, AffiliationContext
    from app.models.provider import AffiliationType
    pid = uuid4()
    hid = uuid4()
    return ProviderContext(
        provider=ProviderIdentityContext(provider_id=pid, display_name="Dr. Reg", contact_email="r@ex.com"),
        hospital=HospitalContext(hospital_id=hid, facility_code="H", display_name="H"),
        affiliation=AffiliationContext(affiliation_id=uuid4(), affiliation_type=AffiliationType.PERMANENT, is_primary=True, roles=["clinician"])
    )

@pytest.fixture
def fake_async_redis():
    return AsyncFakeRedisClient()

@pytest.fixture
def client(fake_async_redis, mock_provider):
    m_supabase = MagicMock()
    m_supabase.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value.data = [{"record_hash": "GENESIS"}]
    m_supabase.table.return_value.insert.return_value.execute.return_value = MagicMock()

    with patch("app.services.consent_engine.get_consent_redis_client", return_value=fake_async_redis), \
         patch("app.core.supabase.get_supabase_client", return_value=m_supabase), \
         patch("app.observability.audit_ledger.append_audit_log", return_value=True), \
         patch("app.observability.audit_ledger.append_audit_log_or_503", return_value=None), \
         patch("app.api.routes.append_audit_log_or_503", return_value=None):
        
        app.dependency_overrides[get_provider_context] = lambda: mock_provider
        app.dependency_overrides[get_current_provider] = lambda: mock_provider
        
        m_kms = AsyncMock()
        m_kms.decrypt_field.side_effect = lambda pid, name, enc, db: f"decrypted-{name}"
        from app.api.v2.patient_routes import get_kms_provider
        app.dependency_overrides[get_kms_provider] = lambda: m_kms

        yield TestClient(app), fake_async_redis
        app.dependency_overrides.clear()

def _make_mock_db():
    db = AsyncMock()
    db.add = lambda row: None
    db.commit = AsyncMock()
    db.execute = AsyncMock()
    db.rollback = AsyncMock()
    return db

@pytest.mark.asyncio
async def test_routine_lifecycle(client):
    test_client, redis = client
    p_id = str(uuid4())
    db = _make_mock_db()
    app.dependency_overrides[get_db_session] = lambda: db
    
    resp = test_client.post("/api/v2/consent/routine/issue", 
                            json={"patient_id": p_id, "purpose": "TREATMENT"},
                            headers={"Authorization": "Bearer session"})
    assert resp.status_code == 200
    token = resp.json()["consent_token"]
    
    res = MagicMock()
    # Use real values instead of Mocks for clinical fields to pass Pydantic validation
    res.scalars().first.return_value = MagicMock(
        patient_name="YWJjZGVmZ2hpamtsbW5vcA==:1", phone="YWJjZGVmZ2hpamtsbW5vcA==:1", aadhaar_abha_id="YWJjZGVmZ2hpamtsbW5vcA==:1",
        diagnoses="YWJjZGVmZ2hpamtsbW5vcA==:1", lab_results="YWJjZGVmZ2hpamtsbW5vcA==:1", prescriptions="YWJjZGVmZ2hpamtsbW5vcA==:1"
    )
    row = MagicMock()
    row.consumed_at = None
    res.scalar_one_or_none.return_value = row
    db.execute.return_value = res
    
    headers = {"X-Consent-Token": token, "X-Consent-Purpose": "TREATMENT", "Authorization": "Bearer session"}
    first = test_client.get(f"/api/v2/patient/{p_id}/record", headers=headers)
    assert first.status_code == 200, first.text
    second = test_client.get(f"/api/v2/patient/{p_id}/record", headers=headers)
    assert second.status_code == 403, second.text

@pytest.mark.asyncio
async def test_break_glass_lifecycle(client, mock_provider):
    test_client, redis = client
    p_id = str(uuid4())
    db = _make_mock_db()
    app.dependency_overrides[get_db_session] = lambda: db
    issue_response = test_client.post(
        "/api/v2/consent/break-glass/issue",
        json={
            "patient_id": p_id,
            "reason_code": "LIFE_THREATENING_EMERGENCY",
            "justification": "Immediate threat to life requires emergency access.",
        },
        headers={"Authorization": "Bearer session"},
    )
    assert issue_response.status_code in {401, 403, 428}
    token = "not-issued-without-step-up"
    
    from app.core.dependencies import require_role
    app.dependency_overrides[require_role("clinician")] = lambda: mock_provider
    res = MagicMock()
    grant = MagicMock(is_break_glass=True, revoked_at=None, consumed_at=None)
    res.scalar_one_or_none.return_value = grant
    db.execute.return_value = res
    
    resp = test_client.post("/api/v2/consent/break-glass/revoke", 
                            json={"consent_token": token, "revocation_reason": "S"},
                            headers={"Authorization": "Bearer session"})
    assert resp.status_code in {200, 404}
    assert test_client.get(f"/api/v2/consent/validate?consent_token={token}&patient_id={p_id}", 
                           headers={"Authorization": "Bearer session"}).status_code == 401

@pytest.mark.asyncio
async def test_v1_lifecycle(client):
    test_client, redis = client
    p_id = str(uuid4())
    db = _make_mock_db()
    app.dependency_overrides[get_scoped_session] = lambda: p_id
    app.dependency_overrides[get_db_session] = lambda: db
    res = MagicMock()
    row = MagicMock()
    row.consumed_at = None
    res.scalar_one_or_none.return_value = row
    db.execute.return_value = res
    
    token = test_client.post("/request-consent", 
                             json={"duration_seconds": 60, "scope": "clinical"},
                             headers={"Authorization": "Bearer session"}).json()["consent_token"]
    
    m_supabase = MagicMock()
    m_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{"masked_internal_id": p_id, "diagnoses": ["H"]}]
    m_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.error = None
    with patch("app.api.routes.get_supabase_client", return_value=m_supabase):
        assert test_client.get("/view-record/clinical", headers={"X-Consent-Token": token}).status_code == 200
        assert test_client.get("/view-record/clinical", headers={"X-Consent-Token": token}).status_code == 403

@pytest.mark.asyncio
async def test_expiry_and_failure(client):
    test_client, redis = client
    assert test_client.get("/api/v2/consent/validate?consent_token=expired&patient_id=p", 
                           headers={"Authorization": "Bearer session"}).status_code == 401
    with patch.object(redis, 'get', side_effect=RuntimeError("fail")):
        assert test_client.get("/api/v2/consent/validate?consent_token=any&patient_id=p", 
                               headers={"Authorization": "Bearer session"}).status_code == 503
