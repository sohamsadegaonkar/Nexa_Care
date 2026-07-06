"""End-to-end round-trip tests for Push Approval (Squad B)."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from uuid import uuid4
from app.main import app

@pytest.fixture
def mock_provider_context():
    from app.models.provider_context import ProviderContext, ProviderIdentityContext, HospitalContext, AffiliationContext
    from app.models.provider import AffiliationType
    return ProviderContext(
        provider=ProviderIdentityContext(provider_id=uuid4(), display_name="Dr. Integration", contact_email="i@ex.com"),
        hospital=HospitalContext(hospital_id=uuid4(), facility_code="H", display_name="H"),
        affiliation=AffiliationContext(affiliation_id=uuid4(), affiliation_type=AffiliationType.PERMANENT, is_primary=True, roles=["clinician"])
    )

@pytest.mark.integration
@pytest.mark.asyncio
async def test_push_approval_roundtrip(test_client, mock_provider_context, mock_db):
    """Full round-trip: request -> status -> respond -> status -> replay."""
    from app.core.dependencies import get_current_provider, get_scoped_session
    patient_id = str(uuid4())
    provider_id = str(mock_provider_context.provider.provider_id)

    app.dependency_overrides[get_current_provider] = lambda: mock_provider_context
    app.dependency_overrides[get_scoped_session] = lambda: patient_id

    try:
        mock_db.execute.return_value.scalar_one_or_none.return_value = None

        # 2. POST /api/v2/push/request
        req_resp = test_client.post("/api/v2/push/request", json={
            "patient_id": patient_id,
            "provider_id": provider_id,
            "purpose": "Integration Test",
            "scope": "pii.*"
        })
        assert req_resp.status_code == 201
        data = req_resp.json()
        assert data["status"] == "pending"
        request_id = data["request_id"]

        # 3. GET /api/v2/push/{request_id}/status
        status_resp = test_client.get(f"/api/v2/push/{request_id}/status")
        assert status_resp.status_code == 200
        assert status_resp.json()["status"] == "pending"

        # 4. POST /api/v2/push/{request_id}/respond (Approved)
        from app.api.v2.assurance_routes import bio_verifier
        with patch.object(bio_verifier, "verify_signature", return_value=AsyncMock(verified=True)):
            respond_resp = test_client.post(f"/api/v2/push/{request_id}/respond", json={
                "decision": "approved",
                "signature": "sig",
                "nonce": "nonce"
            })
            assert respond_resp.status_code == 200

        # 5. GET /api/v2/push/{request_id}/status (Approved)
        status_resp = test_client.get(f"/api/v2/push/{request_id}/status")
        assert status_resp.json()["status"] == "approved"

        # 6. Attempt respond again (Replay Rejected)
        with patch.object(bio_verifier, "verify_signature", return_value=AsyncMock(verified=True)):
            replay_resp = test_client.post(f"/api/v2/push/{request_id}/respond", json={
                "decision": "denied",
                "signature": "sig2",
                "nonce": "nonce2"
            })
            assert replay_resp.status_code == 409
    finally:
        app.dependency_overrides.pop(get_current_provider, None)
        app.dependency_overrides.pop(get_scoped_session, None)

@pytest.mark.integration
@pytest.mark.asyncio
async def test_push_timeout_roundtrip(test_client, mock_provider_context, mock_redis, mock_db):
    """Test: request -> TTL expiry -> status (timeout)."""
    from app.core.dependencies import get_current_provider
    patient_id = str(uuid4())
    provider_id = str(mock_provider_context.provider.provider_id)

    app.dependency_overrides[get_current_provider] = lambda: mock_provider_context

    try:
        mock_db.execute.return_value.scalar_one_or_none.return_value = None

        # 1. Create request
        req_resp = test_client.post("/api/v2/push/request", json={
            "patient_id": patient_id,
            "provider_id": provider_id,
            "purpose": "Timeout Test",
            "scope": "clinical.*"
        })
        request_id = req_resp.json()["request_id"]

        # 2. Simulate timeout in mock redis
        await mock_redis.delete(f"push_request:{request_id}")
        
        # Mock DB log entry
        mock_log = MagicMock()
        mock_log.status = "pending"
        mock_log.patient_id = patient_id
        mock_db.execute.return_value.scalar_one_or_none.return_value = mock_log
        
        # 3. Check status
        status_resp = test_client.get(f"/api/v2/push/{request_id}/status")
        assert status_resp.json()["status"] == "timeout"
    finally:
        app.dependency_overrides.pop(get_current_provider, None)
