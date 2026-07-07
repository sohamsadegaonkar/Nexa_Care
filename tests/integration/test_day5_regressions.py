"""Regression tests for issues identified during Day 5 Integration."""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.assurance_verifier import RedisAssuranceVerifier
from app.models.assurance import AssuranceLevel
from app.services.crypto_kms import PatientDataErased
from app.main import app
from app.api.v2.patient_routes import get_current_provider, get_kms_provider
from app.services.consent_engine import ConsentCapability


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

    mock_kms = AsyncMock()
    mock_kms.decrypt_field.side_effect = PatientDataErased(patient_id)

    mock_provider = MagicMock()
    mock_provider.actor_uid = "d-1"

    capability = ConsentCapability(
        patient_id=patient_id,
        clinician_id="d-1",
        purpose="t",
        scope=["pii.*"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-06T00:00:00Z",
    )

    app.dependency_overrides[get_current_provider] = lambda: mock_provider
    app.dependency_overrides[get_kms_provider] = lambda: mock_kms
    try:
        with (
            patch("app.services.consent_engine.validate", return_value=capability),
            patch("app.services.consent_engine.consume", new_callable=AsyncMock),
            patch("app.services.consent_gated_crypto.append_audit_log_or_503", new_callable=AsyncMock),
            patch("app.services.consent_gated_crypto.append_audit_log", new_callable=AsyncMock),
        ):
            mock_val = "YWJjZGVmZ2hpamtsbW5vcA==:1"
            mock_row = MagicMock(patient_name=mock_val, phone=None, aadhaar_abha_id=None)
            mock_db.execute.return_value.scalars().first.return_value = mock_row

            headers = {
                "X-Consent-Token": "valid-token",
                "X-Consent-Purpose": "t",
                "X-Hospital-Id": str(uuid.uuid4()),
            }

            resp = await test_client.get(f"/api/v2/patient/{patient_id}/record", headers=headers)

            assert resp.status_code == 410
            assert resp.json()["error_code"] == "PATIENT_DATA_ERASED"
    finally:
        app.dependency_overrides.pop(get_current_provider, None)
        app.dependency_overrides.pop(get_kms_provider, None)


@pytest.mark.asyncio
async def test_regress_sec_003_merge_hospital_id_required(test_client):
    """SEC-003: Backend must reject merge calls missing X-Hospital-Id."""
    resp = await test_client.post("/api/v2/patient/merge", json={
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
