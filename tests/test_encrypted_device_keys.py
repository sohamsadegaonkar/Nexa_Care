"""Tests for encrypted device public keys in biometric_registry."""

from __future__ import annotations

import base64
import os
import time
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.biometric_registry import enroll_biometric_binding
from app.services.biometric_signature_verifier import BiometricSignatureVerifier
from app.services.crypto_kms import LocalEnvelopeProvider

@pytest.fixture
def mock_db():
    db = AsyncMock(spec=AsyncSession)
    mock_res = MagicMock()
    # Mock result.scalars().all() to return a list
    mock_res.scalars.return_value.all.return_value = []
    # Mock result.scalar_one_or_none() to return a single row
    mock_res.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=mock_res)
    db.scalar = AsyncMock(return_value=1)
    return db

@pytest.fixture
def kms():
    return LocalEnvelopeProvider()

@pytest.fixture
def env_setup():
    with patch.dict(os.environ, {"KEK_ROOT_SECRET": "test-secret-long-enough-32-chars-!!"}):
        yield

@pytest.mark.asyncio
async def test_enroll_verifies_with_encrypted_key(env_setup, mock_db, kms):
    patient_id = str(uuid.uuid4())
    nfc_uid = "NFC-123"
    bio_seed = "SEED-456"
    
    # Generate P-256 key pair
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    public_key_der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )

    # 1. Enroll (should encrypt key)
    with patch("app.services.biometric_registry.get_supabase_client") as mock_supabase, \
         patch("app.services.biometric_registry.get_encryption_provider", return_value=kms):
        
        mock_reg_res = MagicMock()
        mock_reg_res.error = None
        mock_supabase.return_value.table.return_value.insert.return_value.execute.return_value = mock_reg_res
        
        # Setup DEK
        await kms.generate_dek(patient_id, mock_db)
        
        # Mock active DEK fetch for encrypt_field
        from app.models.dek_store import PatientDEKStore
        mock_dek_row = MagicMock(spec=PatientDEKStore)
        mock_dek_row.dek_version = 1
        mock_dek_row.is_active = True
        mock_dek_row.destroyed_at = None
        mock_db.execute.return_value.scalar_one_or_none.return_value = mock_dek_row
        mock_db.scalar = AsyncMock(return_value=1)
        
        success = await enroll_biometric_binding(nfc_uid, bio_seed, patient_id, mock_db, public_key_der)
        assert success is True
        
        # Verify insert data contained encrypted key (not raw DER)
        insert_args = mock_supabase.return_value.table.return_value.insert.call_args[0][0]
        assert insert_args["device_public_key"] != public_key_der
        assert ":" in insert_args["device_public_key"]

    # 2. Verify Signature
    verifier = BiometricSignatureVerifier()
    request_id = "req-1"
    nonce = "nonce-1"
    message = f"{nonce}{request_id}{patient_id}".encode("utf-8")
    signature = private_key.sign(message, ec.ECDSA(hashes.SHA256()))
    sig_b64 = base64.b64encode(signature).decode()
    
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None # Nonce not used
    
    # Mock registry fetch returning encrypted key
    mock_reg_fetch_res = MagicMock()
    mock_reg_fetch_res.data = {"device_public_key": insert_args["device_public_key"], "revoked_at": None}
    
    with patch("app.services.biometric_signature_verifier.get_supabase_client") as mock_supabase_v, \
         patch("app.services.biometric_signature_verifier.get_encryption_provider", return_value=kms):
        
        mock_supabase_v.return_value.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = mock_reg_fetch_res
        
        # mock_db is already setup to return mock_dek_row for version lookup
        result = await verifier.verify_signature(patient_id, request_id, sig_b64, nonce, mock_redis, mock_db)
        assert result.verified is True

@pytest.mark.asyncio
async def test_erased_patient_fails_verification(env_setup, mock_db, kms):
    patient_id = str(uuid.uuid4())
    
    # 1. Setup a valid encrypted key string
    # We need to mock generate_dek so we have a key in cache to bypass DB lookup for encryption
    await kms.generate_dek(patient_id, mock_db)
    
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    pk_der = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    
    # Mock for encrypt_field
    mock_row = MagicMock()
    mock_row.dek_version = 1
    mock_row.is_active = True
    mock_row.destroyed_at = None
    mock_db.execute.return_value.scalar_one_or_none.return_value = mock_row
    mock_db.scalar.return_value = 1
    
    enc_field = await kms.encrypt_field(patient_id, "device_public_key", base64.b64encode(pk_der).decode(), mock_db)
    serialized_key = enc_field.serialize()

    # 2. Simulate erasure
    # Mock DB to return destroyed row for ALL subsequent lookups
    destroyed_row = MagicMock()
    destroyed_row.patient_id = uuid.UUID(patient_id)
    destroyed_row.dek_version = 1
    destroyed_row.destroyed_at = datetime.now()
    mock_db.execute.return_value.scalar_one_or_none.return_value = destroyed_row
    # Clear cache to force DB lookup
    kms._cache.clear()
    
    # 3. Verify Signature
    verifier = BiometricSignatureVerifier()
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None
    
    mock_reg_res = MagicMock()
    mock_reg_res.data = {"device_public_key": serialized_key, "revoked_at": None}
    
    with patch("app.services.biometric_signature_verifier.get_supabase_client") as mock_supabase, \
         patch("app.services.biometric_signature_verifier.get_encryption_provider", return_value=kms):
        
        mock_supabase.return_value.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = mock_reg_res
        
        result = await verifier.verify_signature(patient_id, "r", "c2ln", "nonce", mock_redis, mock_db)
        assert result.verified is False
        assert result.error == "PATIENT_DATA_ERASED"

@pytest.mark.asyncio
async def test_timing_budget_maintained(env_setup, mock_db, kms):
    patient_id = str(uuid.uuid4())
    verifier = BiometricSignatureVerifier()
    
    start = time.monotonic()
    with patch("app.services.biometric_signature_verifier.get_supabase_client") as mock_s:
        mock_s.return_value.table.return_value.select.return_value.eq.return_value.single.return_value.execute.side_effect = Exception("Fast")
        await verifier.verify_signature(patient_id, "r", "sig", "nonce", AsyncMock(), mock_db)
        
    duration = time.monotonic() - start
    assert duration >= 0.05

@pytest.mark.asyncio
async def test_cached_dek_performance_overhead(env_setup, mock_db, kms):
    patient_id = str(uuid.uuid4())
    await kms.generate_dek(patient_id, mock_db)
    
    # Mock row for encrypt_field lookup
    mock_row = MagicMock()
    mock_row.dek_version = 1
    mock_row.is_active = True
    mock_row.destroyed_at = None
    mock_db.execute.return_value.scalar_one_or_none.return_value = mock_row
    mock_db.scalar = AsyncMock(return_value=1)

    # Pre-setup cache
    kms._set_cached_dek(patient_id, 1, os.urandom(32))
    
    # Setup mock registry result with encrypted key
    enc_field = await kms.encrypt_field(patient_id, "k", "plain", mock_db)
    
    start_decrypt = time.perf_counter()
    await kms.decrypt_field(patient_id, "k", enc_field, mock_db)
    decrypt_duration = time.perf_counter() - start_decrypt
    
    assert decrypt_duration < 0.005 # < 5ms
