"""Tests for Identity Vault per-patient envelope encryption."""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.sharding import encrypt_vault_payload, decrypt_vault_field
from app.services.crypto_kms import LocalEnvelopeProvider
from app.models.dek_store import PatientDEKStore
from app.models.shards import NexaVault


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
            "PII_ENCRYPTION_KEY": "test-fernet-key-32-bytes-base64=",
        },
    ):
        yield


@pytest.mark.asyncio
async def test_vault_encrypt_decrypt_roundtrip(env_setup, mock_db):
    kms = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())

    # Generate DEK
    await kms.generate_dek(patient_id, mock_db)

    # Mock active DEK lookup
    mock_row = MagicMock(spec=PatientDEKStore)
    mock_row.dek_version = 1
    mock_row.destroyed_at = None
    mock_db.execute.return_value.scalar_one_or_none.return_value = mock_row
    mock_db.scalar = AsyncMock(return_value=1)

    payload = {
        "patient_name": "John Doe",
        "phone": "1234567890",
        "aadhaar_abha_id": "A123",
    }

    # 1. Encrypt
    encrypted = await encrypt_vault_payload(payload, patient_id, mock_db, kms)

    assert "patient_name" in encrypted
    assert encrypted["patient_name"].endswith(":1")  # version suffix

    # 2. Decrypt
    decrypted_name = await decrypt_vault_field(
        patient_id, "patient_name", encrypted["patient_name"], mock_db, kms
    )
    assert decrypted_name == "John Doe"


@pytest.mark.asyncio
async def test_auto_migration_on_read(env_setup, mock_db):
    kms = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())

    # Generate new DEK
    await kms.generate_dek(patient_id, mock_db)

    # Legacy Fernet token (starts with gAAAAA)
    legacy_token = "gAAAAABm..."

    # Mock existing DEK lookup for re-encryption
    mock_row = MagicMock(spec=PatientDEKStore)
    mock_row.dek_version = 1
    mock_row.destroyed_at = None
    mock_db.execute.return_value.scalar_one_or_none.return_value = mock_row
    mock_db.scalar = AsyncMock(return_value=1)

    with (
        patch(
            "app.services.sharding.decrypt_pii_field",
            return_value="decrypted-plaintext",
        ),
        patch(
            "app.observability.audit_ledger.append_audit_log", new_callable=AsyncMock
        ) as mock_audit,
    ):
        # Act
        val = await decrypt_vault_field(
            patient_id, "patient_name", legacy_token, mock_db, kms
        )

        # Assert
        assert val == "decrypted-plaintext"
        # Verify DB update was triggered
        assert any(
            "UPDATE nexa_vault" in str(call) or "update" in str(call).lower()
            for call in mock_db.execute.call_args_list
        )
        # Verify audit
        assert any(
            call.kwargs.get("event_type") == "PII_ENCRYPTION_MIGRATED"
            for call in mock_audit.call_args_list
        )


@pytest.mark.asyncio
async def test_batch_migration_idempotent(env_setup, mock_db):
    from scripts.migrate_fernet_to_envelope import migrate_row

    kms = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())

    # Case 1: Legacy row
    row = NexaVault(
        masked_internal_id=patient_id,
        patient_name="gAAAAA...",
        phone=None,
        aadhaar_abha_id=None,
    )

    with (
        patch(
            "scripts.migrate_fernet_to_envelope.decrypt_pii_field", return_value="plain"
        ),
        patch.object(kms, "encrypt_field") as mock_encrypt,
    ):
        mock_field = MagicMock()
        mock_field.serialize.return_value = "new-format:1"
        mock_encrypt.return_value = mock_field

        await migrate_row(row, mock_db, kms)
        assert mock_db.execute.called

    # Case 2: Already migrated row
    mock_db.reset_mock()
    row_migrated = NexaVault(
        masked_internal_id=patient_id,
        patient_name="new-format:1",
        phone=None,
        aadhaar_abha_id=None,
    )
    await migrate_row(row_migrated, mock_db, kms)
    assert not mock_db.execute.called  # No update should be triggered
