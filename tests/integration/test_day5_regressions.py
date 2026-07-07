"""Regression tests for issues identified during Day 5 Integration."""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.assurance_verifier import RedisAssuranceVerifier
from app.models.assurance import AssuranceLevel
from app.services.crypto_kms import PatientDataErased


@pytest.mark.asyncio
async def test_regress_sec_001_redis_prefix(mock_redis):
    """SEC-001: Ensure AssuranceVerifier uses the standardized 'push_request:' prefix."""
    request_id = str(uuid.uuid4())
    patient_id = "p-123"
    
    # Manually seed Redis with the correct prefix
    key = f"push_request:{request_id}"
    await mock_redis.setex(key, 120, json.dumps({
        "status": "approved",
        "patient_id": patient_id
    }))
    
    verifier = RedisAssuranceVerifier()
    result = await verifier.verify(
        level=AssuranceLevel.PUSH_BIOMETRIC,
        patient_id=patient_id,
        evidence={"request_id": request_id},
        redis=mock_redis
    )
    
    assert result.verified is True
    # Verify it was consumed (single-use requirement)
    assert await mock_redis.get(key) is None


@pytest.mark.asyncio
async def test_regress_sec_002_erased_handler(test_client, mock_db):
    """SEC-002: Ensure PatientDataErased results in a 410 GONE response."""
    patient_id = str(uuid.uuid4())
    
    # Mocking KMS to raise the specific error
    mock_kms = AsyncMock()
    mock_kms.decrypt_field.side_effect = PatientDataErased(patient_id)
    
    # Mock dependencies
    mock_provider = MagicMock()
    
    # 1. Issue a valid token first
    with patch("app.services.consent_engine.get_consent_redis_client") as mock_redis_factory:
        mock_redis_client = AsyncMock()
        mock_redis_factory.return_value = mock_redis_client
        
        # Simulate a valid capability in Redis
        mock_redis_client.get.return_value = json.dumps({
            "patient_id": patient_id,
            "clinician_id": "d-1",
            "purpose": "t",
            "scope": ["pii.*"],
            "issued_at": "2026-07-06T00:00:00Z"
        })
        # Simulate single-use consumption success
        mock_redis_client.getdel.return_value = mock_redis_client.get.return_value

        with patch("app.api.v2.patient_routes.get_current_provider", return_value=mock_provider), \
             patch("app.api.v2.patient_routes.get_provider_context", return_value=mock_provider), \
             patch("app.api.v2.patient_routes.get_kms_provider", return_value=mock_kms):
            
            # Mock DB to return a row so we reach the decrypt step
            mock_row = MagicMock(patient_name="enc-data:1", phone=None, aadhaar_abha_id=None)
            mock_db.execute.return_value.scalars().first.return_value = mock_row
            
            headers = {
                "X-Consent-Token": "valid-token",
                "X-Consent-Purpose": "t",
                "X-Hospital-Id": str(uuid.uuid4())
            }
            
            resp = test_client.get(f"/api/v2/patient/{patient_id}/record", headers=headers)
            
            assert resp.status_code == 410
            assert resp.json()["error_code"] == "PATIENT_DATA_ERASED"


@pytest.mark.asyncio
async def test_regress_sec_003_merge_hospital_id_required(test_client):
    """SEC-003: Backend must reject merge calls missing X-Hospital-Id."""
    resp = test_client.post("/api/v2/patient/merge", json={
        "old_patient_uuid": str(uuid.uuid4()),
        "canonical_patient_uuid": str(uuid.uuid4()),
        "reason": "test"
    }, headers={
        "Authorization": "Bearer some-token",
        "X-Merge-Challenge": "some-challenge"
        # X-Hospital-Id missing
    })
    
    # 401 because get_current_provider needs it to resolve affiliation 
    # OR 400 because get_provider_context requires it if multiple affiliations exist.
    # In either case, it should NOT be 200.
    assert resp.status_code in [400, 401, 422]
