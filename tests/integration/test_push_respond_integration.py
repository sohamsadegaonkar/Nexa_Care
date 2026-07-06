"""Integration tests for Squad B: Push Request & Response.

Contract:
- Create push request (status pending).
- Respond to request (approved/denied).
- Replay protection.
"""

import pytest
from unittest.mock import patch, AsyncMock
from uuid import uuid4
from app.main import app

@pytest.mark.integration
@pytest.mark.asyncio
async def test_push_approval_flow(test_client, mock_db, mock_redis):
    """Test: Create push request -> Respond with approval -> Verify status.
    
    The doctor creates a request, polling begins. The patient submits 
    their response on their device.
    """
    from app.core.dependencies import get_current_provider, get_scoped_session
    from app.models.provider_context import ProviderContext, ProviderIdentityContext, HospitalContext, AffiliationContext
    from app.models.provider import AffiliationType

    patient_id = str(uuid4())
    provider_id = str(uuid4())
    
    mock_provider = ProviderContext(
        provider=ProviderIdentityContext(provider_id=provider_id, display_name="Dr. House", contact_email="h@ex.com"),
        hospital=HospitalContext(hospital_id=uuid4(), facility_code="P", display_name="Princeton"),
        affiliation=AffiliationContext(affiliation_id=uuid4(), affiliation_type=AffiliationType.PERMANENT, is_primary=True, roles=["clinician"])
    )

    app.dependency_overrides[get_current_provider] = lambda: mock_provider
    app.dependency_overrides[get_scoped_session] = lambda: patient_id

    try:
        # 1. Doctor creates request
        # Mocking DB response for push token lookup
        mock_db.execute.return_value.scalar_one_or_none.return_value = None # No token found

        req_resp = test_client.post("/api/v2/push/request", json={
            "patient_id": patient_id,
            "provider_id": provider_id,
            "purpose": "Diagnosis",
            "scope": "clinical.*"
        })
        assert req_resp.status_code == 201
        request_id = req_resp.json()["request_id"]
    
        # 2. Check initial status
        status_resp = test_client.get(f"/api/v2/push/{request_id}/status")
        assert status_resp.json()["status"] == "pending"
    
        # 3. Patient responds
        from app.api.v2.assurance_routes import bio_verifier
        with patch.object(bio_verifier, "verify_signature", return_value=AsyncMock(verified=True)):
            respond_resp = test_client.post(f"/api/v2/push/{request_id}/respond", json={
                "decision": "approved",
                "signature": "valid-sig",
                "nonce": "valid-nonce"
            })
            assert respond_resp.status_code == 200
    
        # 4. Check final status
        status_resp = test_client.get(f"/api/v2/push/{request_id}/status")
        assert status_resp.json()["status"] == "approved"
    finally:
        app.dependency_overrides.pop(get_current_provider, None)
        app.dependency_overrides.pop(get_scoped_session, None)

@pytest.mark.integration
@pytest.mark.asyncio
async def test_push_denial_flow(test_client, mock_db):
    """Test: Respond with denial -> Status changes to denied."""
    from app.core.dependencies import get_current_provider, get_scoped_session
    from app.models.provider_context import ProviderContext, ProviderIdentityContext, HospitalContext, AffiliationContext
    from app.models.provider import AffiliationType

    patient_id = str(uuid4())
    provider_id = str(uuid4())
    mock_provider = ProviderContext(
        provider=ProviderIdentityContext(provider_id=provider_id, display_name="Dr. House", contact_email="h@ex.com"),
        hospital=HospitalContext(hospital_id=uuid4(), facility_code="P", display_name="Princeton"),
        affiliation=AffiliationContext(affiliation_id=uuid4(), affiliation_type=AffiliationType.PERMANENT, is_primary=True, roles=["clinician"])
    )

    app.dependency_overrides[get_current_provider] = lambda: mock_provider
    app.dependency_overrides[get_scoped_session] = lambda: patient_id

    try:
        mock_db.execute.return_value.scalar_one_or_none.return_value = None
        req_resp = test_client.post("/api/v2/push/request", json={
            "patient_id": patient_id,
            "provider_id": provider_id,
            "purpose": "t",
            "scope": "s"
        })
        request_id = req_resp.json()["request_id"]
    
        from app.api.v2.assurance_routes import bio_verifier
        with patch.object(bio_verifier, "verify_signature", return_value=AsyncMock(verified=True)):
            test_client.post(f"/api/v2/push/{request_id}/respond", json={
                "decision": "denied",
                "signature": "sig",
                "nonce": "nonce"
            })
    
        status_resp = test_client.get(f"/api/v2/push/{request_id}/status")
        assert status_resp.json()["status"] == "denied"
    finally:
        app.dependency_overrides.pop(get_current_provider, None)
        app.dependency_overrides.pop(get_scoped_session, None)

@pytest.mark.integration
@pytest.mark.asyncio
async def test_push_respond_twice_fails(test_client, mock_db):
    """Test: Respond twice -> Second call returns 409 (Conflict)."""
    from app.core.dependencies import get_current_provider, get_scoped_session
    from app.models.provider_context import ProviderContext, ProviderIdentityContext, HospitalContext, AffiliationContext
    from app.models.provider import AffiliationType

    patient_id = str(uuid4())
    provider_id = str(uuid4())
    mock_provider = ProviderContext(
        provider=ProviderIdentityContext(provider_id=provider_id, display_name="Dr. House", contact_email="h@ex.com"),
        hospital=HospitalContext(hospital_id=uuid4(), facility_code="P", display_name="Princeton"),
        affiliation=AffiliationContext(affiliation_id=uuid4(), affiliation_type=AffiliationType.PERMANENT, is_primary=True, roles=["clinician"])
    )

    app.dependency_overrides[get_current_provider] = lambda: mock_provider
    app.dependency_overrides[get_scoped_session] = lambda: patient_id

    try:
        mock_db.execute.return_value.scalar_one_or_none.return_value = None
        req_resp = test_client.post("/api/v2/push/request", json={
            "patient_id": patient_id,
            "provider_id": provider_id,
            "purpose": "t",
            "scope": "s"
        })
        request_id = req_resp.json()["request_id"]
    
        from app.api.v2.assurance_routes import bio_verifier
        with patch.object(bio_verifier, "verify_signature", return_value=AsyncMock(verified=True)):
            test_client.post(f"/api/v2/push/{request_id}/respond", json={
                "decision": "approved",
                "signature": "sig",
                "nonce": "nonce"
            })
        
            # Second attempt
            respond_resp = test_client.post(f"/api/v2/push/{request_id}/respond", json={
                "decision": "denied",
                "signature": "sig",
                "nonce": "nonce"
            })
            assert respond_resp.status_code == 409
    finally:
        app.dependency_overrides.pop(get_current_provider, None)
        app.dependency_overrides.pop(get_scoped_session, None)
