"""Tests for the Key Management System (KMS)."""

from __future__ import annotations

import os
import uuid
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from botocore.exceptions import EndpointConnectionError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.crypto_kms import (
    AWSKMSProvider,
    LocalEnvelopeProvider,
    EncryptedField,
    EncryptionError,
    PatientDataErased,
    TransactionalPatientSpecificKMSProvisioningUnsupported,
    get_encryption_provider,
)
from app.models.dek_store import PatientDEKStore


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
    # Legacy generation remains an explicit create operation; it does not use
    # the get-or-create active-key lookup used by ensure_active_dek().
    assert mock_db.execute.call_count == 1


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

    no_destroyed = MagicMock()
    no_destroyed.scalar_one_or_none.return_value = None
    latest = MagicMock()
    latest.scalar_one_or_none.return_value = 1
    mock_db.execute.side_effect = [no_destroyed, latest, MagicMock()]

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
    cached_row = PatientDEKStore(
        patient_id=uuid.UUID(patient_id),
        wrapped_dek=b"cached-wrapped-dek",
        dek_iv=b"cached-iv",
        dek_version=1,
        algorithm="AES-256-GCM",
        wrapping_backend="local-aes-gcm",
        is_active=True,
        wrapping_key_type="patient",
        patient_wrapping_key_id="cached-epoch",
    )
    provider._set_cached_dek(patient_id, 1, plaintext_dek, cached_row)

    assert (
        provider._get_cached_dek(patient_id, 1, provider._cache_identity(cached_row))
        == plaintext_dek
    )

    # Mock rotate_dek prerequisites
    no_destroyed = MagicMock()
    no_destroyed.scalar_one_or_none.return_value = None
    latest = MagicMock()
    latest.scalar_one_or_none.return_value = 1
    mock_db.execute.side_effect = [no_destroyed, latest, MagicMock()]

    await provider.rotate_dek(patient_id, mock_db)

    # Cache should be updated with a separately bound new DEK, not old one.
    new_row = mock_db.add.call_args.args[0]
    cached = provider._get_cached_dek(patient_id, 2, provider._cache_identity(new_row))
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


@pytest.mark.asyncio
async def test_ensure_active_dek_stages_without_commit(env_setup, mock_db):
    provider = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())

    # mock_db has execute and scalar_one_or_none returning None initially
    mock_db.execute.return_value.scalar_one_or_none.return_value = None

    bundle = await provider.ensure_active_dek(patient_id, mock_db)

    assert bundle.patient_id == patient_id
    assert bundle.dek_version == 1
    assert bundle.algorithm == "AES-256-GCM"
    assert mock_db.add.called
    assert mock_db.flush.called
    assert not mock_db.commit.called


@pytest.mark.asyncio
async def test_ensure_active_dek_flush_failure_does_not_cache_key(env_setup, mock_db):
    provider = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())
    mock_db.execute.return_value.scalar_one_or_none.return_value = None
    mock_db.flush.side_effect = RuntimeError("synthetic flush failure")

    with pytest.raises(RuntimeError, match="synthetic flush failure"):
        await provider.ensure_active_dek(patient_id, mock_db)

    assert provider._cache == {}
    assert not mock_db.commit.called


@pytest.mark.asyncio
async def test_ensure_active_dek_returns_existing_active_bundle(env_setup, mock_db):
    provider = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())
    pid = uuid.UUID(patient_id)

    mock_row = PatientDEKStore(
        patient_id=pid,
        wrapped_dek=b"dummy-wrapped-dek",
        dek_iv=b"dummy-iv-12b",
        dek_version=1,
        algorithm="AES-256-GCM",
        is_active=True,
    )
    mock_db.execute.return_value.scalar_one_or_none.return_value = mock_row

    bundle = await provider.ensure_active_dek(patient_id, mock_db)

    assert bundle.patient_id == patient_id
    assert bundle.wrapped_dek == b"dummy-wrapped-dek"
    assert bundle.dek_version == 1
    # Should not add a new row
    assert not mock_db.add.called
    assert not mock_db.commit.called


@pytest.mark.asyncio
async def test_ensure_active_dek_fails_closed_when_erased(env_setup, mock_db):
    provider = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())

    with patch.object(
        provider,
        "_check_erasure_registry",
        side_effect=PatientDataErased(patient_id),
    ):
        with pytest.raises(PatientDataErased):
            await provider.ensure_active_dek(patient_id, mock_db)


@pytest.mark.asyncio
async def test_rotate_dek_fails_closed_when_erased(env_setup, mock_db):
    provider = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())

    with patch.object(
        provider,
        "_check_erasure_registry",
        side_effect=PatientDataErased(patient_id),
    ):
        with pytest.raises(PatientDataErased):
            await provider.rotate_dek(patient_id, mock_db)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", ["generate_dek", "ensure_active_dek", "rotate_dek"]
)
async def test_destroyed_dek_marker_denies_lifecycle_mutations(
    env_setup, mock_db, operation
):
    provider = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())
    destroyed = PatientDEKStore(
        patient_id=uuid.UUID(patient_id),
        wrapped_dek=b"destroyed-wrapped-dek",
        dek_iv=b"destroyed-iv",
        dek_version=1,
        algorithm="AES-256-GCM",
        wrapping_backend="local-aes-gcm",
        is_active=False,
        destroyed_at=datetime.now(timezone.utc),
        wrapping_key_type="patient",
        patient_wrapping_key_id="destroyed-epoch",
    )
    no_active = MagicMock()
    no_active.scalar_one_or_none.return_value = None
    destroyed_result = MagicMock()
    destroyed_result.scalar_one_or_none.return_value = destroyed
    if operation == "ensure_active_dek":
        mock_db.execute.side_effect = [no_active, destroyed_result]
    else:
        mock_db.execute.return_value = destroyed_result

    with pytest.raises(PatientDataErased):
        await getattr(provider, operation)(patient_id, mock_db)

    assert not mock_db.add.called
    assert not mock_db.commit.called


def _aws_provider(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_BACKEND", "kms")
    monkeypatch.setenv("KMS_KEY_ID", "alias/synthetic-kms")
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    kms_client = MagicMock()
    with patch("boto3.client", return_value=kms_client):
        return AWSKMSProvider(), kms_client


def test_kms_provider_factory_normalizes_botocore_initialization_failure(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_BACKEND", "kms")
    monkeypatch.setenv("KMS_KEY_ID", "alias/synthetic-kms")
    monkeypatch.setenv("AWS_REGION", "ap-south-1")

    with patch(
        "boto3.client",
        side_effect=EndpointConnectionError(endpoint_url="https://kms.invalid"),
    ):
        with pytest.raises(
            EncryptionError, match="Encryption provider initialization failed"
        ):
            get_encryption_provider()


@pytest.mark.asyncio
async def test_aws_ensure_patient_specific_creation_fails_before_aws_mutation(
    monkeypatch, mock_db
):
    monkeypatch.setenv("AWS_PATIENT_SPECIFIC_KMS_KEYS", "true")
    provider, kms_client = _aws_provider(monkeypatch)
    patient_id = str(uuid.uuid4())
    mock_db.execute.return_value.scalar_one_or_none.return_value = None

    with pytest.raises(TransactionalPatientSpecificKMSProvisioningUnsupported):
        await provider.ensure_active_dek(patient_id, mock_db)

    kms_client.create_key.assert_not_called()
    kms_client.create_alias.assert_not_called()
    kms_client.generate_data_key.assert_not_called()
    assert not mock_db.add.called


@pytest.mark.asyncio
async def test_aws_ensure_existing_key_never_creates_patient_cmk(monkeypatch, mock_db):
    monkeypatch.setenv("AWS_PATIENT_SPECIFIC_KMS_KEYS", "true")
    provider, kms_client = _aws_provider(monkeypatch)
    patient_id = str(uuid.uuid4())
    existing = PatientDEKStore(
        patient_id=uuid.UUID(patient_id),
        wrapped_dek=b"aws-wrapped-dek",
        dek_iv=b"",
        dek_version=1,
        algorithm="AES-256-GCM",
        wrapping_backend="aws-kms",
        is_active=True,
        created_at=datetime.now(timezone.utc),
        wrapping_key_type="patient",
        patient_wrapping_key_id="existing-kms-key",
    )
    mock_db.execute.return_value.scalar_one_or_none.return_value = existing

    bundle = await provider.ensure_active_dek(patient_id, mock_db)

    assert bundle.dek_version == 1
    kms_client.create_key.assert_not_called()
    kms_client.create_alias.assert_not_called()
    kms_client.generate_data_key.assert_not_called()
    assert not mock_db.add.called


@pytest.mark.asyncio
async def test_aws_shared_kms_ensure_stages_without_commit(monkeypatch, mock_db):
    monkeypatch.setenv("AWS_PATIENT_SPECIFIC_KMS_KEYS", "false")
    provider, kms_client = _aws_provider(monkeypatch)
    patient_id = str(uuid.uuid4())
    mock_db.execute.return_value.scalar_one_or_none.return_value = None
    kms_client.generate_data_key.return_value = {
        "Plaintext": b"p" * 32,
        "CiphertextBlob": b"synthetic-wrapped-dek",
    }

    bundle = await provider.ensure_active_dek(patient_id, mock_db)

    assert bundle.dek_version == 1
    kms_client.generate_data_key.assert_called_once()
    kms_client.create_key.assert_not_called()
    assert mock_db.add.called
    assert mock_db.flush.called
    assert not mock_db.commit.called
