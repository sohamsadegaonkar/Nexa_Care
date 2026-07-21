"""Tests for the Key Management System (KMS)."""

from __future__ import annotations

import os
import uuid
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.crypto_kms import (
    LocalEnvelopeProvider,
    EncryptedField,
    EncryptionError,
)
from app.models.dek_store import PatientDEKStore


@pytest.fixture
def mock_db():
    db = AsyncMock(spec=AsyncSession)
    mock_res = MagicMock()
    db.execute.return_value = mock_res
    return db


@pytest.fixture
def env_setup():
    with patch.dict(
        os.environ,
        {
            "KEK_ROOT_SECRET": "test-root-secret-long-enough-32-chars-!!",
            "ENCRYPTION_BACKEND": "local",
        },
    ):
        yield


@pytest.mark.asyncio
async def test_kek_derivation_deterministic(env_setup):
    provider1 = LocalEnvelopeProvider()
    provider2 = LocalEnvelopeProvider()

    kek1 = provider1._get_kek()
    kek2 = provider2._get_kek()

    assert kek1 == kek2
    assert len(kek1) == 32


@pytest.mark.asyncio
async def test_generate_dek(env_setup, mock_db):
    provider = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())

    bundle = await provider.generate_dek(patient_id, mock_db)

    assert bundle.patient_id == patient_id
    assert bundle.dek_version == 1
    assert mock_db.add.called
    assert mock_db.commit.called


@pytest.mark.asyncio
async def test_encrypt_decrypt_roundtrip(env_setup, mock_db):
    provider = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())
    field_name = "patient_name"
    plaintext = "John Doe"

    # Mock existing DEK in DB
    dek = os.urandom(32)
    dek_iv = os.urandom(12)
    aesgcm = AESGCM(provider._get_kek())
    wrapped_dek = aesgcm.encrypt(dek_iv, dek, None)

    mock_row = MagicMock(spec=PatientDEKStore)
    mock_row.patient_id = uuid.UUID(patient_id)
    mock_row.wrapped_dek = wrapped_dek
    mock_row.dek_iv = dek_iv
    mock_row.dek_version = 1
    mock_row.is_active = True
    mock_row.destroyed_at = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_row
    mock_db.execute.return_value = mock_result
    mock_db.scalar.return_value = 1

    encrypted = await provider.encrypt_field(patient_id, field_name, plaintext, mock_db)
    assert encrypted.field_name == field_name
    assert encrypted.dek_version == 1

    decrypted = await provider.decrypt_field(patient_id, field_name, encrypted, mock_db)
    assert decrypted == plaintext


@pytest.mark.asyncio
async def test_rotate_dek(env_setup, mock_db):
    provider = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())

    mock_result = MagicMock()
    mock_result.scalar.return_value = 1
    mock_db.execute.return_value = mock_result

    new_bundle = await provider.rotate_dek(patient_id, mock_db)

    assert new_bundle.dek_version == 2
    assert mock_db.commit.called


@pytest.mark.asyncio
async def test_destroy_dek(env_setup, mock_db):
    provider = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())

    success = await provider.destroy_dek(patient_id, mock_db)
    assert success is True
    assert mock_db.execute.called
    assert mock_db.commit.called


@pytest.mark.asyncio
async def test_cache_invalidation_on_rotation(env_setup, mock_db):
    provider = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())

    # Inject into cache
    plaintext_dek = os.urandom(32)
    provider._set_cached_dek(patient_id, 1, plaintext_dek)

    assert provider._get_cached_dek(patient_id, 1) == plaintext_dek

    # Mock rotate_dek prerequisites
    mock_result = MagicMock()
    mock_result.scalar.return_value = 1
    mock_db.execute.return_value = mock_result

    await provider.rotate_dek(patient_id, mock_db)

    # Cache should be updated with new DEK, not old one
    cached = provider._get_cached_dek(patient_id, 2)
    assert cached is not None
    assert cached != plaintext_dek


@pytest.mark.asyncio
async def test_wrong_patient_dek_rejected(env_setup, mock_db):
    provider = LocalEnvelopeProvider()
    patient2_id = str(uuid.uuid4())

    dek1 = os.urandom(32)
    encrypted = EncryptedField(
        ciphertext=AESGCM(dek1).encrypt(os.urandom(12), b"secret", None),
        iv=os.urandom(12),
        field_name="test",
        dek_version=1,
        algorithm="AES-256-GCM",
    )

    # Mock patient2's DEK
    dek2 = os.urandom(32)
    dek_iv2 = os.urandom(12)
    wrapped_dek2 = AESGCM(provider._get_kek()).encrypt(dek_iv2, dek2, None)

    mock_row2 = MagicMock(spec=PatientDEKStore)
    mock_row2.wrapped_dek = wrapped_dek2
    mock_row2.dek_iv = dek_iv2
    mock_row2.dek_version = 1
    mock_row2.destroyed_at = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_row2
    mock_db.execute.return_value = mock_result

    # decryption should fail with AESGCM exception if we use dek2 to decrypt dek1's data
    with pytest.raises(EncryptionError):
        await provider.decrypt_field(patient2_id, "test", encrypted, mock_db)


@pytest.mark.asyncio
async def test_concurrent_encrypt_operations(env_setup, mock_db):
    provider = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())

    # Setup DEK row for DB
    dek = os.urandom(32)
    dek_iv = os.urandom(12)
    wrapped_dek = AESGCM(provider._get_kek()).encrypt(dek_iv, dek, None)

    mock_row = MagicMock(spec=PatientDEKStore)
    mock_row.dek_version = 1
    mock_row.is_active = True
    mock_row.destroyed_at = None
    mock_row.dek_iv = dek_iv
    mock_row.wrapped_dek = wrapped_dek

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_row
    mock_db.execute.return_value = mock_result
    mock_db.scalar.return_value = 1

    # Concurrent encryptions
    tasks = [
        provider.encrypt_field(patient_id, f"field_{i}", f"val_{i}", mock_db)
        for i in range(10)
    ]
    results = await asyncio.gather(*tasks)

    assert len(results) == 10
    for i, res in enumerate(results):
        assert res.field_name == f"field_{i}"
