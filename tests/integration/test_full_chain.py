"""Cross-squad full-chain integration tests for Nexa Care V2."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.main import app
from app.core.dependencies import get_current_provider, get_scoped_session, get_provider_context
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)


@pytest.fixture
def mock_provider_context():
    provider_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=provider_id,
            display_name="Dr. Integration",
            contact_email="i@ex.com",
            medical_registration_number="MCI-123"
        ),
        hospital=HospitalContext(
            hospital_id=hospital_id,
            facility_code="H1",
            display_name="Hospital One"
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            is_primary=True,
            roles=["clinician", "admin"],
            department="OPD"
        )
    )


@pytest.fixture
def happy_path_headers(mock_provider_context):
    return {
        "Authorization": "Bearer session-token",
        "X-Hospital-Id": str(mock_provider_context.hospital.hospital_id)
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_chain_happy_path(test_client, mock_db, mock_redis, mock_provider_context, happy_path_headers):
    """Scenario 1: Happy Path (Demo Scenario)"""
    from app.api.v2.assurance_routes import bio_verifier

    # Apply global dependency overrides for this test
    app.dependency_overrides[get_current_provider] = lambda: mock_provider_context
    app.dependency_overrides[get_provider_context] = lambda: mock_provider_context

    patient_id = str(uuid.uuid4())
    provider_id = str(mock_provider_context.provider.provider_id)

    # 1. Register Patient (Step 1 & 2)
    with patch("app.services.sharding.get_encryption_provider") as mock_kms_factory, \
         patch("app.api.routes.get_supabase_client") as mock_supabase_factory, \
         patch("app.api.routes.require_role", return_value=lambda: mock_provider_context):
        
        mock_kms = AsyncMock()
        mock_kms.generate_dek.return_value = MagicMock(dek_version=1)
        mock_kms_factory.return_value = mock_kms
        
        async def mock_encrypt(pid, col, val, db):
            m = MagicMock()
            m.serialize.return_value = "MDEyMzQ1Njc4OTAxMjM0NQ==:1"
            return m
        mock_kms.encrypt_field.side_effect = mock_encrypt

        mock_supabase = MagicMock()
        mock_supabase_factory.return_value = mock_supabase
        
        reg_payload = {
            "patient_name": "John Doe",
            "phone": "1234567890",
            "aadhaar_abha_id": "ABHA-123",
            "diagnoses": ["Flu"],
            "lab_results": [],
            "prescriptions": []
        }
        
        resp = test_client.post("/register", json=reg_payload, headers=happy_path_headers)
        assert resp.status_code == 200, resp.text
        patient_id = resp.json()["pii_vault"]["masked_internal_id"]

    # 3. Doctor initiates push request (Step 3)
    mock_db.execute.return_value.scalar_one_or_none.return_value = None # No token for patient yet
    
    req_resp = test_client.post("/api/v2/push/request", json={
        "patient_id": patient_id,
        "provider_id": provider_id,
        "purpose": "TREATMENT",
        "scope": "pii.*"
    }, headers=happy_path_headers)
    assert req_resp.status_code == 201, req_resp.text
    request_id = req_resp.json()["request_id"]
    nonce = req_resp.json()["challenge_nonce"]

    # 4. Patient responds with approval (Step 4)
    app.dependency_overrides[get_scoped_session] = lambda: patient_id
    with patch.object(bio_verifier, "verify_signature", return_value=AsyncMock(verified=True)):
        respond_resp = test_client.post(f"/api/v2/push/{request_id}/respond", json={
            "decision": "approved",
            "signature": "valid-sig",
            "nonce": nonce
        })
        assert respond_resp.status_code == 200, respond_resp.text

    # 5. Doctor polls and sees approval (Step 5)
    status_resp = test_client.get(f"/api/v2/push/{request_id}/status", headers=happy_path_headers)
    assert status_resp.json()["status"] == "approved"

    # 6. Doctor issues consent (Step 6)
    consent_resp = test_client.post("/api/v2/consent/routine/issue", json={
        "patient_id": patient_id,
        "purpose": "TREATMENT",
        "scope": ["pii.patient_name", "clinical.diagnoses"],
        "assurance_level": "push_biometric",
        "assurance_evidence": {"request_id": request_id}
    }, headers=happy_path_headers)
    assert consent_resp.status_code == 200, consent_resp.text
    consent_token = consent_resp.json()["consent_token"]

    # 7. Doctor reads patient record (Step 7 & 8)
    # Use valid B64 for deserialization check
    mock_vault_row = MagicMock(patient_name="MDEyMzQ1Njc4OTAxMjM0NQ==:1", phone=None, aadhaar_abha_id=None)
    mock_clinical_row = MagicMock(diagnoses="MDEyMzQ1Njc4OTAxMjM0NQ==:1", lab_results=[], prescriptions=[])
    
    async def db_execute_mock(stmt):
        stmt_str = str(stmt)
        res = MagicMock()
        if "nexa_vault" in stmt_str:
            res.scalars().first.return_value = mock_vault_row
        elif "nexa_clinical" in stmt_str:
            res.scalars().first.return_value = mock_clinical_row
        elif "consent_grant_log" in stmt_str:
            res.scalar_one_or_none.return_value = MagicMock(consumed_at=None)
        return res
    
    mock_db.execute.side_effect = db_execute_mock

    # Mocking KMS for decryption
    from app.api.v2.patient_routes import get_kms_provider
    mock_kms_read = AsyncMock()
    
    async def mock_decrypt(pid, col, enc, db):
        if col == "patient_name":
            return "John Doe"
        if col == "diagnoses":
            return ["Flu"]
        return "mock-value"
        
    mock_kms_read.decrypt_field.side_effect = mock_decrypt
    app.dependency_overrides[get_kms_provider] = lambda: mock_kms_read

    read_headers = {
        **happy_path_headers,
        "X-Consent-Token": consent_token,
        "X-Consent-Purpose": "TREATMENT"
    }
    
    read_resp = test_client.get(f"/api/v2/patient/{patient_id}/record", headers=read_headers)
    assert read_resp.status_code == 200, read_resp.text
    data = read_resp.json()
    assert data["pii"]["patient_name"] == "John Doe"
    assert data["clinical"]["diagnoses"] == ["Flu"]

    # 9. Verify consent token consumption (Step 9)
    read_resp2 = test_client.get(f"/api/v2/patient/{patient_id}/record", headers=read_headers)
    assert read_resp2.status_code == 403

    app.dependency_overrides.clear()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_chain_denial_path(test_client, mock_db, mock_redis, mock_provider_context, happy_path_headers):
    """Scenario 2: Denial Path"""
    from app.api.v2.assurance_routes import bio_verifier

    patient_id = str(uuid.uuid4())
    provider_id = str(mock_provider_context.provider.provider_id)
    app.dependency_overrides[get_current_provider] = lambda: mock_provider_context
    app.dependency_overrides[get_provider_context] = lambda: mock_provider_context

    # 1. Doctor initiates push
    req_resp = test_client.post("/api/v2/push/request", json={
        "patient_id": patient_id,
        "provider_id": provider_id,
        "purpose": "TREATMENT",
        "scope": "pii.*"
    }, headers=happy_path_headers)
    request_id = req_resp.json()["request_id"]
    nonce = req_resp.json()["challenge_nonce"]

    # 2. Patient responds with denial
    app.dependency_overrides[get_scoped_session] = lambda: patient_id
    with patch.object(bio_verifier, "verify_signature", return_value=AsyncMock(verified=True)):
        test_client.post(f"/api/v2/push/{request_id}/respond", json={
            "decision": "denied",
            "signature": "valid-sig",
            "nonce": nonce
        })

    # 3. Doctor attempts to issue consent -> Fails
    consent_resp = test_client.post("/api/v2/consent/routine/issue", json={
        "patient_id": patient_id,
        "purpose": "TREATMENT",
        "scope": ["pii.patient_name"],
        "assurance_level": "push_biometric",
        "assurance_evidence": {"request_id": request_id}
    }, headers=happy_path_headers)
    assert consent_resp.status_code == 403
    
    app.dependency_overrides.clear()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_chain_timeout_path(test_client, mock_db, mock_redis, mock_provider_context, happy_path_headers):
    """Scenario 3: Timeout Path"""
    patient_id = str(uuid.uuid4())
    provider_id = str(mock_provider_context.provider.provider_id)
    app.dependency_overrides[get_current_provider] = lambda: mock_provider_context
    app.dependency_overrides[get_provider_context] = lambda: mock_provider_context

    # 1. Initiate push
    req_resp = test_client.post("/api/v2/push/request", json={
        "patient_id": patient_id,
        "provider_id": provider_id,
        "purpose": "TREATMENT",
        "scope": "pii.*"
    }, headers=happy_path_headers)
    request_id = req_resp.json()["request_id"]

    # 2. Simulate timeout (expire in Redis)
    await mock_redis.delete(f"push_request:{request_id}")
    
    # Mock DB log entry for timeout logic in get_push_status
    mock_log = MagicMock()
    mock_log.status = "pending"
    mock_log.patient_id = patient_id
    mock_db.execute.return_value.scalar_one_or_none.return_value = mock_log

    # 3. Check status
    status_resp = test_client.get(f"/api/v2/push/{request_id}/status", headers=happy_path_headers)
    assert status_resp.json()["status"] == "timeout"
    
    app.dependency_overrides.clear()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_chain_forged_assurance(test_client, mock_db, mock_redis, mock_provider_context, happy_path_headers):
    """Scenario 4: Forged Assurance"""
    patient_id = str(uuid.uuid4())
    app.dependency_overrides[get_current_provider] = lambda: mock_provider_context
    app.dependency_overrides[get_provider_context] = lambda: mock_provider_context

    # Attempt to issue consent with fake request_id
    consent_resp = test_client.post("/api/v2/consent/routine/issue", json={
        "patient_id": patient_id,
        "purpose": "TREATMENT",
        "scope": ["pii.patient_name"],
        "assurance_level": "push_biometric",
        "assurance_evidence": {"request_id": "fake-request-id"}
    }, headers=happy_path_headers)
    
    assert consent_resp.status_code == 403
    assert "Assurance verification failed" in consent_resp.json()["detail"]
    
    app.dependency_overrides.clear()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_chain_cryptographic_erasure(test_client, mock_db, mock_redis, mock_provider_context, happy_path_headers):
    """Scenario 5: Cryptographic Erasure"""
    from app.services.crypto_kms import PatientDataErased

    patient_id = str(uuid.uuid4())
    app.dependency_overrides[get_current_provider] = lambda: mock_provider_context
    app.dependency_overrides[get_provider_context] = lambda: mock_provider_context

    # 1. Admin triggers erasure
    mock_kms = AsyncMock()
    from app.api.v2.patient_routes import get_kms_provider
    app.dependency_overrides[get_kms_provider] = lambda: mock_kms
    
    with patch("app.api.v2.patient_routes.require_role", return_value=lambda: mock_provider_context):
        erase_resp = test_client.post(f"/api/v2/patient/{patient_id}/erase", json={
            "confirmation": f"ERASE-{patient_id}",
            "reason": "Integration Test"
        }, headers=happy_path_headers)
        assert erase_resp.status_code == 200
        assert mock_kms.destroy_dek.called

    # 2. Doctor attempts to read -> Decrypt fails
    # Issue a valid consent first (Standard)
    consent_resp = test_client.post("/api/v2/consent/routine/issue", json={
        "patient_id": patient_id,
        "purpose": "TREATMENT",
        "scope": ["pii.*", "clinical.*"],
        "assurance_level": "standard",
    }, headers=happy_path_headers)
    assert consent_resp.status_code == 200, consent_resp.text
    token = consent_resp.json()["consent_token"]
    
    # Mock KMS to raise PatientDataErased
    with patch("app.api.v2.patient_routes.get_kms_provider", return_value=mock_kms):
        mock_kms.decrypt_field.side_effect = PatientDataErased(patient_id)
        
        # Mock DB for shards
        mock_vault_row = MagicMock(patient_name="MDEyMzQ1Njc4OTAxMjM0NQ==:1", phone=None, aadhaar_abha_id=None)
        mock_clinical_row = MagicMock(diagnoses=["Flu"], clinical_data={})
        
        async def db_execute_mock(stmt):
            stmt_str = str(stmt)
            res = MagicMock()
            if "nexa_vault" in stmt_str:
                res.scalars().first.return_value = mock_vault_row
            elif "nexa_clinical" in stmt_str:
                res.scalars().first.return_value = mock_clinical_row
            elif "consent_grant_log" in stmt_str:
                res.scalar_one_or_none.return_value = MagicMock(consumed_at=None)
            return res
        
        mock_db.execute.side_effect = db_execute_mock

        read_headers = {
            **happy_path_headers,
            "X-Consent-Token": token,
            "X-Consent-Purpose": "TREATMENT"
        }
        
        read_resp = test_client.get(f"/api/v2/patient/{patient_id}/record", headers=read_headers)
        assert read_resp.status_code == 410
        assert "erased" in str(read_resp.json()).lower()
    
    app.dependency_overrides.clear()
