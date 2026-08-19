"""Tests for per-patient envelope encryption (KMS)."""

from __future__ import annotations

import os
import uuid
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.crypto_kms import (
    LocalEnvelopeProvider,
    EncryptionError,
    LegacyFernetError,
    EncryptedField,
)
from app.services.sharding import decrypt_vault_field


@pytest.fixture
def mock_db():
    db = AsyncMock(spec=AsyncSession)
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = None
    db.execute.return_value = mock_res
    return db


@pytest.fixture
def env_setup():
    with patch.dict(
        os.environ,
        {
            "KEK_ROOT_SECRET": "test-root-secret-long-enough-32-chars-!!",
            "PII_ENCRYPTION_KEY": "test-fernet-key-32-bytes-base64=",  # placeholder
        },
    ):
        yield


@pytest.mark.asyncio
async def test_encrypt_decrypt_roundtrip(env_setup, mock_db):
    provider = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())

    # 1. Generate DEK
    await provider.generate_dek(patient_id, mock_db)

    # The cache is reusable only when the authoritative row identity matches.
    mock_row = mock_db.add.call_args.args[0]

    mock_db.execute.return_value.scalar_one_or_none.return_value = mock_row

    # 2. Encrypt
    plaintext = "Sensitive Data"
    encrypted = await provider.encrypt_field(
        patient_id, "test_field", plaintext, mock_db
    )

    assert encrypted.dek_version == 1

    # 3. Decrypt
    decrypted = await provider.decrypt_field(
        patient_id, "test_field", encrypted, mock_db
    )
    assert decrypted == plaintext


@pytest.mark.asyncio
async def test_version_mismatch_handling(env_setup, mock_db):
    provider = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())

    # Generate v1
    await provider.generate_dek(patient_id, mock_db)
    v1_row = mock_db.add.call_args.args[0]
    v1_dek = provider._get_cached_dek(patient_id, 1, provider._cache_identity(v1_row))

    # Mock destroyed-marker lookup, latest-version lookup, then deactivation.
    no_destroyed = MagicMock()
    no_destroyed.scalar_one_or_none.return_value = None
    latest = MagicMock()
    latest.scalar_one_or_none.return_value = 1
    mock_db.execute.side_effect = [no_destroyed, latest, MagicMock()]
    await provider.rotate_dek(patient_id, mock_db)
    v2_row = mock_db.add.call_args.args[0]
    v2_dek = provider._get_cached_dek(patient_id, 2, provider._cache_identity(v2_row))

    assert v1_dek != v2_dek

    # Encrypt with v1
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    iv = os.urandom(12)
    ciphertext = AESGCM(v1_dek).encrypt(iv, b"v1 data", None)
    encrypted_v1 = EncryptedField(ciphertext, iv, "f", 1, "AES-256-GCM")

    mock_db.execute.side_effect = None
    mock_db.execute.return_value.scalar_one_or_none.return_value = v1_row
    decrypted = await provider.decrypt_field(patient_id, "f", encrypted_v1, mock_db)
    assert decrypted == "v1 data"


@pytest.mark.asyncio
async def test_fernet_migration_detection(env_setup, mock_db):
    fernet_token = "gAAAAABm..."
    with pytest.raises(LegacyFernetError):
        EncryptedField.deserialize(fernet_token, "name")

    patient_id = str(uuid.uuid4())
    with patch(
        "app.services.sharding.decrypt_pii_field", return_value="decrypted-by-fernet"
    ):
        val = await decrypt_vault_field(
            patient_id, "patient_name", fernet_token, mock_db
        )
        assert val == "decrypted-by-fernet"


@pytest.mark.asyncio
async def test_destroy_dek_fails_decrypt(env_setup, mock_db):
    provider = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())

    await provider.generate_dek(patient_id, mock_db)

    # Mock row for encrypt_field
    mock_row = mock_db.add.call_args.args[0]
    mock_db.execute.return_value.scalar_one_or_none.return_value = mock_row

    encrypted = await provider.encrypt_field(patient_id, "f", "data", mock_db)

    # destroy_dek first queries the authoritative tombstone registry, then
    # loads the patient's DEK rows.
    mock_db.execute.return_value.scalar_one_or_none.return_value = None
    mock_db.execute.return_value.scalars.return_value.all.return_value = [mock_row]

    # Destroy DEK
    await provider.destroy_dek(patient_id, mock_db)

    # Cache should be cleared
    assert f"{patient_id}:1" not in provider._cache

    # DB lookup should return None (mock it)
    mock_db.execute.return_value.scalar_one_or_none.return_value = None

    with pytest.raises(EncryptionError, match="not found or destroyed"):
        await provider.decrypt_field(patient_id, "f", encrypted, mock_db)


@pytest.mark.asyncio
async def test_concurrent_encryptions(env_setup, mock_db):
    provider = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())
    await provider.generate_dek(patient_id, mock_db)

    # Mock active DEK fetch
    mock_row = mock_db.add.call_args.args[0]
    mock_db.execute.return_value.scalar_one_or_none.return_value = mock_row

    tasks = [
        provider.encrypt_field(patient_id, f"field_{i}", "data", mock_db)
        for i in range(5)
    ]
    results = await asyncio.gather(*tasks)

    assert len(results) == 5
    ivs = [r.iv for r in results]
    assert len(set(ivs)) == 5
