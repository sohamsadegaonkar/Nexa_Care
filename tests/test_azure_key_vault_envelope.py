"""Offline contract tests using an RSA SDK double; no vault access."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.core.config import ConfigError, get_kms_config
from app.services import azure_key_vault_envelope as azure
from app.services.crypto_kms import EncryptionError, get_encryption_provider

_KEY_ID = "https://nexa-demo-test.vault.azure.net/keys/nexa-demo-dek/" + "a" * 32
_PATIENT = "11111111-1111-4111-8111-111111111111"
_OTHER = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("ENCRYPTION_BACKEND", "azure_key_vault")
    monkeypatch.setenv("AZURE_KMS_SYNTHETIC_ONLY", "YES")
    monkeypatch.setenv("AZURE_DEK_KEY_ID", _KEY_ID)
    monkeypatch.setenv("AZURE_KMS_MANAGED_IDENTITY_CLIENT_ID", _PATIENT)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    oaep = padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None
    )

    class Credential:
        def __init__(self, **kwargs):
            assert kwargs == {"client_id": _PATIENT}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Client(Credential):
        def __init__(self, key_id, **kwargs):
            assert key_id == _KEY_ID
            assert kwargs["logging_enable"] is False

        def wrap_key(self, algorithm, data, **kwargs):
            assert algorithm == "RSA-OAEP-256"
            return SimpleNamespace(
                key_id=_KEY_ID, encrypted_key=private.public_key().encrypt(data, oaep)
            )

        def unwrap_key(self, algorithm, data, **kwargs):
            assert algorithm == "RSA-OAEP-256"
            return SimpleNamespace(key_id=_KEY_ID, key=private.decrypt(data, oaep))

    def load(name):
        if name == "azure.identity":
            return SimpleNamespace(ManagedIdentityCredential=Credential)
        assert name == "azure.keyvault.keys.crypto"
        return SimpleNamespace(CryptographyClient=Client)

    monkeypatch.setattr(azure, "import_module", load)
    return get_encryption_provider()


@pytest.mark.asyncio
async def test_real_rsa_roundtrip_and_binding(provider, monkeypatch):
    db = AsyncMock()
    db.add = lambda row: None
    row = await provider._new_row(_PATIENT, 1, db)
    assert row.wrapping_key_type == "shared"
    assert row.patient_wrapping_key_id == _KEY_ID
    assert len(row.wrapped_dek) == 256
    gate = AsyncMock()
    monkeypatch.setattr(provider, "_check_erasure_registry", gate)
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: row)
    assert len(await provider._get_plaintext_dek(_PATIENT, 1, db)) == 32
    gate.assert_awaited_once()
    with pytest.raises(EncryptionError, match="binding mismatch"):
        await provider._get_plaintext_dek(_OTHER, 1, db)
    with pytest.raises(EncryptionError, match="binding mismatch"):
        await provider._get_plaintext_dek(_PATIENT, 2, db)
    row.wrapping_backend = "aws-kms"
    with pytest.raises(EncryptionError, match="Incompatible"):
        await provider._get_plaintext_dek(_PATIENT, 1, db)


def test_opt_in_and_production_guard(provider, monkeypatch):
    assert get_kms_config().azure_key_id == _KEY_ID
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(ConfigError, match="not qualified"):
        get_kms_config()
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.delenv("AZURE_KMS_SYNTHETIC_ONLY")
    with pytest.raises(ConfigError, match="synthetic-only"):
        get_kms_config()


def test_unversioned_or_unrelated_vault_rejected(provider, monkeypatch):
    for key in (_KEY_ID.rsplit("/", 1)[0], _KEY_ID.replace("nexa-demo-test", "campuspool")):
        monkeypatch.setenv("AZURE_DEK_KEY_ID", key)
        with pytest.raises(ConfigError, match="version-pinned"):
            get_kms_config()
