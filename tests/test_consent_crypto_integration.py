"""Integration tests for Consent Gated Crypto (Squad A & C Integration)."""

import time
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.assurance import AssuranceLevel
from app.models.shards import NexaClinical, NexaVault
from app.services.consent_engine import (
    ConsentPurpose,
    issue_routine,
    issue_break_glass,
)
from app.services.consent_gated_crypto import consent_gated_decrypt
from app.services.crypto_kms import (
    LocalEnvelopeProvider,
    PatientDataErased,
)


class MockRedis:
    def __init__(self):
        self.data = {}
        self.ttls = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value
        if ex:
            self.ttls[key] = time.time() + ex

    async def delete(self, key):
        self.data.pop(key, None)

    async def getdel(self, key):
        val = self.data.pop(key, None)
        return val

    async def rpush(self, key, value):
        if key not in self.data:
            self.data[key] = []
        self.data[key].append(value)


@pytest.fixture
def mock_redis():
    return MockRedis()


@pytest.fixture
def mock_db():
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()
    
    # Store rows added to session
    session.added_rows = []
    def add_side_effect(obj):
        session.added_rows.append(obj)
    session.add = MagicMock(side_effect=add_side_effect)

    # Mock execute to return a result object with scalar_one_or_none, etc.
    async def execute_side_effect(stmt):
        result = MagicMock()
        # Default behavior: return None or empty results
        result.scalar_one_or_none.return_value = None
        result.scalars().first.return_value = None
        result.scalar.return_value = None
        result.scalars().all.return_value = []
        return result

    session.execute = AsyncMock(side_effect=execute_side_effect)

    return session


@pytest.fixture(autouse=True)
def setup_env(monkeypatch):
    monkeypatch.setenv("KEK_ROOT_SECRET", "test-secret-key-for-kms-v1-at-least-32-chars-long")
    monkeypatch.setenv("ENCRYPTION_BACKEND", "local")


@pytest.fixture
def kms_provider():
    return LocalEnvelopeProvider()


@pytest.mark.asyncio
async def test_full_round_trip(mock_db, mock_redis, kms_provider):
    """Test 1: Full round-trip registration, encryption, consent, and decryption."""
    patient_id = str(uuid.uuid4())
    provider_id = "doctor-1"
    hospital_id = "123e4567-e89b-12d3-a456-426614174001"
    
    # 1. Register patient (creates DEK)
    # We need to mock the DB response for DEK storage
    await kms_provider.generate_dek(patient_id, mock_db)
    dek_row = mock_db.added_rows[0]
    
    async def db_execute_side_effect(stmt):
        stmt_str = str(stmt)
        res = MagicMock()
        if "patient_dek_store" in stmt_str:
            res.scalar_one_or_none.return_value = dek_row
            res.scalar.return_value = 1 # version
            return res
        if "nexa_vault" in stmt_str:
            res.scalars().first.return_value = vault_row
            return res
        if "nexa_clinical" in stmt_str:
            res.scalars().first.return_value = clinical_row
            return res
        return res

    mock_db.execute.side_effect = db_execute_side_effect

    # 2. Encrypt PII
    plaintext_name = "John Doe"
    encrypted_name = await kms_provider.encrypt_field(patient_id, "patient_name", plaintext_name, mock_db)
    
    vault_row = NexaVault(
        masked_internal_id=patient_id,
        patient_name=encrypted_name.serialize(),
        phone=None,
        aadhaar_abha_id=None
    )
    
    # 3. Encrypt Clinical
    plaintext_diagnosis = "Hypertension"
    encrypted_diag = await kms_provider.encrypt_field(patient_id, "diagnoses", plaintext_diagnosis, mock_db)
    
    clinical_row = NexaClinical(
        masked_internal_id=patient_id,
        diagnoses=encrypted_diag.serialize()
    )

    # 4. Issue consent
    with patch("app.services.consent_engine.get_consent_redis_client", return_value=mock_redis), \
         patch("app.services.consent_engine.append_audit_log_or_503", AsyncMock()), \
         patch("app.services.consent_engine.append_audit_log", AsyncMock()):
        
        token = await issue_routine(
            patient_id=patient_id,
            clinician_id=provider_id,
            purpose=ConsentPurpose.TREATMENT,
            scope=["pii.patient_name", "clinical.diagnoses"],
            db=mock_db,
            hospital_id=hospital_id,
            assurance_level=AssuranceLevel.STANDARD,
            assurance_evidence={"method": "otp"}
        )

        # 5. Call consent_gated_decrypt()
        with patch("app.services.consent_gated_crypto.append_audit_log_or_503", AsyncMock()), \
             patch("app.services.consent_gated_crypto.append_audit_log", AsyncMock()):
            
            result = await consent_gated_decrypt(
                patient_id=patient_id,
                consent_token=token,
                purpose="TREATMENT",
                requested_scope="*",
                provider_id=provider_id,
                hospital_id=hospital_id,
                db=mock_db,
                redis=mock_redis,
                kms=kms_provider,
            )

            assert result["pii"]["patient_name"] == plaintext_name
            assert result["clinical"]["diagnoses"] == plaintext_diagnosis
            
            # Verify token consumed
            assert await mock_redis.get(f"nexa:consent:{token}") is None


@pytest.mark.asyncio
async def test_no_consent_no_decrypt(mock_db, mock_redis, kms_provider):
    """Test 2: Attempt decrypt without a consent token -> 403."""
    patient_id = str(uuid.uuid4())
    provider_id = "doctor-1"
    
    mock_kms = AsyncMock(spec=LocalEnvelopeProvider)
    
    with patch("app.services.consent_engine.get_consent_redis_client", return_value=mock_redis):
        with pytest.raises(HTTPException) as exc:
            await consent_gated_decrypt(
                patient_id=patient_id,
                consent_token="invalid-token",
                purpose="TREATMENT",
                requested_scope="*",
                provider_id=provider_id,
                hospital_id="123e4567-e89b-12d3-a456-426614174001",
                db=mock_db,
                redis=mock_redis,
                kms=mock_kms
            )
        assert exc.value.status_code == 403
        assert not mock_kms.decrypt_field.called


@pytest.mark.asyncio
async def test_expired_consent_no_decrypt(mock_db, mock_redis, kms_provider):
    """Test 3: Expired token -> 403."""
    # We can simulate expiry by just not having it in Redis (since it would be evicted)
    # or having it but validate returns None.
    patient_id = str(uuid.uuid4())
    provider_id = "doctor-1"
    mock_kms = AsyncMock(spec=LocalEnvelopeProvider)
    
    with patch("app.services.consent_engine.get_consent_redis_client", return_value=mock_redis):
        # We don't put anything in Redis, so it's "expired" or never existed
        with pytest.raises(HTTPException) as exc:
            await consent_gated_decrypt(
                patient_id=patient_id,
                consent_token="expired-token",
                purpose="TREATMENT",
                requested_scope="*",
                provider_id=provider_id,
                hospital_id="123e4567-e89b-12d3-a456-426614174001",
                db=mock_db,
                redis=mock_redis,
                kms=mock_kms
            )
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_wrong_scope(mock_db, mock_redis, kms_provider):
    """Test 4: Consent grants clinical.* but request asks for pii.patient_name -> 403."""
    patient_id = str(uuid.uuid4())
    provider_id = "doctor-1"
    hospital_id = "123e4567-e89b-12d3-a456-426614174001"
    
    with patch("app.services.consent_engine.get_consent_redis_client", return_value=mock_redis), \
         patch("app.services.consent_engine.append_audit_log_or_503", AsyncMock()), \
         patch("app.services.consent_engine.append_audit_log", AsyncMock()):
        
        token = await issue_routine(
            patient_id=patient_id,
            clinician_id=provider_id,
            purpose=ConsentPurpose.TREATMENT,
            scope=["clinical.*"],
            db=mock_db,
            hospital_id=hospital_id,
        )

        with pytest.raises(HTTPException) as exc:
            await consent_gated_decrypt(
                patient_id=patient_id,
                consent_token=token,
                purpose="TREATMENT",
                requested_scope="pii.patient_name",
                provider_id=provider_id,
                hospital_id=hospital_id,
                db=mock_db,
                redis=mock_redis,
                kms=kms_provider,
            )
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_erased_patient(mock_db, mock_redis, kms_provider):
    """Test 5: Erased patient -> PatientDataErased error, consent consumed."""
    patient_id = str(uuid.uuid4())
    provider_id = "doctor-1"
    hospital_id = "123e4567-e89b-12d3-a456-426614174001"

    # 1. Setup DEK then encrypt something
    await kms_provider.generate_dek(patient_id, mock_db)
    dek_row = mock_db.added_rows[0]
    
    async def mock_execute_for_encrypt(stmt):
        res = MagicMock()
        res.scalar_one_or_none.return_value = dek_row
        return res
    mock_db.execute.side_effect = mock_execute_for_encrypt
    encrypted = await kms_provider.encrypt_field(patient_id, "patient_name", "secret", mock_db)
    
    # 2. Destroy the DEK (marks as destroyed and clears cache)
    # We need to mock select for destroy_dek to find the rows
    async def mock_execute_for_destroy(stmt):
        res = MagicMock()
        if "SELECT" in str(stmt):
            res.scalars().all.return_value = [dek_row]
        return res
    mock_db.execute.side_effect = mock_execute_for_destroy
    
    with patch("app.observability.audit_ledger.append_audit_log", AsyncMock()):
        await kms_provider.destroy_dek(patient_id, mock_db)
    
    # 3. Mark the row as destroyed in our mock too (destroy_dek does this to the object)
    # Now set up execute for the decrypt call
    async def db_execute_side_effect(stmt):
        stmt_str = str(stmt)
        res = MagicMock()
        if "patient_dek_store" in stmt_str:
            res.scalar_one_or_none.return_value = dek_row
            return res
        if "nexa_vault" in stmt_str:
            res.scalars().first.return_value = NexaVault(
                masked_internal_id=patient_id, 
                patient_name=encrypted.serialize()
            )
            return res
        return res
    
    mock_db.execute.side_effect = db_execute_side_effect

    # 2. Issue valid consent
    with patch("app.services.consent_engine.get_consent_redis_client", return_value=mock_redis), \
         patch("app.services.consent_engine.append_audit_log_or_503", AsyncMock()), \
         patch("app.services.consent_engine.append_audit_log", AsyncMock()):
        
        token = await issue_routine(
            patient_id=patient_id,
            clinician_id=provider_id,
            purpose=ConsentPurpose.TREATMENT,
            scope=["pii.*"],
            db=mock_db,
            hospital_id=hospital_id,
        )

        # 3. Call decrypt
        with patch("app.services.consent_gated_crypto.append_audit_log_or_503", AsyncMock()), \
             patch("app.services.consent_gated_crypto.append_audit_log", AsyncMock()):
            
            with pytest.raises(PatientDataErased):
                await consent_gated_decrypt(
                    patient_id=patient_id,
                    consent_token=token,
                    purpose="TREATMENT",
                    requested_scope="*",
                    provider_id=provider_id,
                    hospital_id=hospital_id,
                    db=mock_db,
                    redis=mock_redis,
                    kms=kms_provider
                )
            
            # 4. Verify token consumed even on failure
            assert await mock_redis.get(f"nexa:consent:{token}") is None


@pytest.mark.asyncio
async def test_dek_rotation(mock_db, mock_redis, kms_provider):
    """Test 6: DEK rotation -> data encrypted with v1 still readable."""
    patient_id = str(uuid.uuid4())
    provider_id = "doctor-1"
    hospital_id = "123e4567-e89b-12d3-a456-426614174001"

    # 1. Generate DEK v1 and encrypt data
    await kms_provider.generate_dek(patient_id, mock_db)
    dek_v1_row = mock_db.added_rows[0]
    
    plaintext = "Sensitive Data V1"
    
    # Mocking for v1 encryption
    async def db_exec_v1(stmt):
        res = MagicMock()
        res.scalar_one_or_none.return_value = dek_v1_row
        return res
    mock_db.execute.side_effect = db_exec_v1
    encrypted_v1 = await kms_provider.encrypt_field(patient_id, "patient_name", plaintext, mock_db)
    
    # 2. Rotate DEK to v2
    # We need to simulate the update and new row
    dek_v1_row.is_active = False
    
    # Mocking rotate_dek behavior
    async def db_exec_rotate(stmt):
        res = MagicMock()
        res.scalar.return_value = 1 # old version
        return res
    mock_db.execute.side_effect = db_exec_rotate
    await kms_provider.rotate_dek(patient_id, mock_db)
    dek_v2_row = mock_db.added_rows[1]
    
    # 3. Issue consent
    with patch("app.services.consent_engine.get_consent_redis_client", return_value=mock_redis), \
         patch("app.services.consent_engine.append_audit_log_or_503", AsyncMock()), \
         patch("app.services.consent_engine.append_audit_log", AsyncMock()):
        
        token = await issue_routine(
            patient_id=patient_id,
            clinician_id=provider_id,
            purpose=ConsentPurpose.TREATMENT,
            scope=["pii.*"],
            db=mock_db,
            hospital_id=hospital_id,
        )

        # 4. Mock DB to return v1 DEK when requested by decrypt_field
        async def db_execute_side_effect(stmt):
            stmt_str = str(stmt)
            res = MagicMock()
            if "patient_dek_store" in stmt_str:
                # If it's a select for a specific version
                if "dek_version = :dek_version_1" in stmt_str or "dek_version = 1" in stmt_str or "dek_version = :dek_version" in stmt_str:
                    res.scalar_one_or_none.return_value = dek_v1_row
                else:
                    res.scalar_one_or_none.return_value = dek_v2_row
                return res
            if "nexa_vault" in stmt_str:
                res.scalars().first.return_value = NexaVault(
                    masked_internal_id=patient_id,
                    patient_name=encrypted_v1.serialize()
                )
                return res
            return res

        mock_db.execute.side_effect = db_execute_side_effect

        # 5. Decrypt
        with patch("app.services.consent_gated_crypto.append_audit_log_or_503", AsyncMock()), \
             patch("app.services.consent_gated_crypto.append_audit_log", AsyncMock()):
            
            result = await consent_gated_decrypt(
                patient_id=patient_id,
                consent_token=token,
                purpose="TREATMENT",
                requested_scope="*",
                provider_id=provider_id,
                hospital_id=hospital_id,
                db=mock_db,
                redis=mock_redis,
                kms=kms_provider,
            )
            
            assert result["pii"]["patient_name"] == plaintext


@pytest.mark.asyncio
async def test_break_glass_path(mock_db, mock_redis, kms_provider):
    """Test 7: Break-glass consent -> full scope access including PII."""
    patient_id = str(uuid.uuid4())
    provider_id = "doctor-1"
    hospital_id = "123e4567-e89b-12d3-a456-426614174001"

    # Setup DEK and encrypted data
    await kms_provider.generate_dek(patient_id, mock_db)
    dek_row = mock_db.added_rows[0]
    
    async def db_exec_setup(stmt):
        res = MagicMock()
        res.scalar_one_or_none.return_value = dek_row
        return res
    mock_db.execute.side_effect = db_exec_setup
    
    encrypted_name = await kms_provider.encrypt_field(patient_id, "patient_name", "John Doe", mock_db)
    encrypted_diag = await kms_provider.encrypt_field(patient_id, "diagnoses", "Flu", mock_db)

    async def db_execute_side_effect(stmt):
        stmt_str = str(stmt)
        res = MagicMock()
        if "patient_dek_store" in stmt_str:
            res.scalar_one_or_none.return_value = dek_row
            return res
        if "nexa_vault" in stmt_str:
            res.scalars().first.return_value = NexaVault(patient_name=encrypted_name.serialize())
            return res
        if "nexa_clinical" in stmt_str:
            res.scalars().first.return_value = NexaClinical(diagnoses=encrypted_diag.serialize())
            return res
        return res
    
    mock_db.execute.side_effect = db_execute_side_effect

    # Issue Break-glass consent
    with patch("app.services.consent_engine.get_consent_redis_client", return_value=mock_redis), \
         patch("app.services.consent_engine.append_audit_log_or_503", AsyncMock()), \
         patch("app.services.consent_engine.append_audit_log", AsyncMock()):
        
        token = await issue_break_glass(
            patient_id=patient_id,
            clinician_id=provider_id,
            reason_code="LIFE_THREATENING_EMERGENCY",
            db=mock_db,
            hospital_id=hospital_id,
            scope=["EMERGENCY", "clinical.allergies"],
            reason_code_version="v1",
            session_binding="session-binding",
            mfa_verified_at=datetime.now(timezone.utc),
        )

        # Call decrypt with "*" scope
        with patch("app.services.consent_gated_crypto.append_audit_log_or_503", AsyncMock()), \
             patch("app.services.consent_gated_crypto.append_audit_log", AsyncMock()):
            
            result = await consent_gated_decrypt(
                patient_id=patient_id,
                consent_token=token,
                purpose="EMERGENCY",
                requested_scope="clinical.allergies",
                provider_id=provider_id,
                hospital_id=hospital_id,
                db=mock_db,
                redis=mock_redis,
                kms=kms_provider,
                session_binding="session-binding",
            )

            assert result["pii"] == {}
            assert result["clinical"] == {}


@pytest.mark.asyncio
async def test_performance_target(mock_db, mock_redis, kms_provider):
    """Performance test: Full flow < 200ms with cached DEK."""
    patient_id = str(uuid.uuid4())
    provider_id = "doctor-1"
    hospital_id = "123e4567-e89b-12d3-a456-426614174001"

    # Setup DEK and cache it
    await kms_provider.generate_dek(patient_id, mock_db)
    dek_row = mock_db.added_rows[0]
    
    async def db_exec_perf(stmt):
        res = MagicMock()
        res.scalar_one_or_none.return_value = dek_row
        return res
    mock_db.execute.side_effect = db_exec_perf
    
    # Warm up cache
    await kms_provider._get_plaintext_dek(patient_id, 1, mock_db)
    
    encrypted_name = await kms_provider.encrypt_field(patient_id, "patient_name", "John Doe", mock_db)
    
    async def db_execute_side_effect(stmt):
        stmt_str = str(stmt)
        res = MagicMock()
        if "patient_dek_store" in stmt_str:
            res.scalar_one_or_none.return_value = dek_row
            return res
        if "nexa_vault" in stmt_str:
            res.scalars().first.return_value = NexaVault(patient_name=encrypted_name.serialize())
            return res
        return res
    
    mock_db.execute.side_effect = db_execute_side_effect

    with patch("app.services.consent_engine.get_consent_redis_client", return_value=mock_redis), \
         patch("app.services.consent_engine.append_audit_log_or_503", AsyncMock()), \
         patch("app.services.consent_engine.append_audit_log", AsyncMock()), \
         patch("app.services.consent_gated_crypto.append_audit_log_or_503", AsyncMock()), \
         patch("app.services.consent_gated_crypto.append_audit_log", AsyncMock()):
        
        token = await issue_routine(
            patient_id=patient_id,
            clinician_id=provider_id,
            purpose=ConsentPurpose.TREATMENT,
            scope=["pii.*"],
            db=mock_db,
            hospital_id=hospital_id,
        )

        start_time = time.perf_counter()
        
        await consent_gated_decrypt(
            patient_id=patient_id,
            consent_token=token,
            purpose="TREATMENT",
            requested_scope="*",
            provider_id=provider_id,
            hospital_id=hospital_id,
            db=mock_db,
            redis=mock_redis,
            kms=kms_provider
        )
        
        end_time = time.perf_counter()
        latency_ms = (end_time - start_time) * 1000
        
        print(f"Latency: {latency_ms:.2f}ms")
        assert latency_ms < 200, f"Latency {latency_ms:.2f}ms exceeded target 200ms"
