import pytest

from app.api.v2.patient_routes import get_kms_provider
from app.core.config import ConfigError
from app.services.crypto_kms import AWSKMSProvider, LocalEnvelopeProvider, get_encryption_provider


@pytest.mark.asyncio
async def test_route_dependency_resolves_configured_safe_test_provider(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("ENCRYPTION_BACKEND", "local")
    monkeypatch.setenv("KEK_ROOT_SECRET", "test-only-root-secret-with-sufficient-entropy")
    provider = await get_kms_provider()
    assert isinstance(provider, LocalEnvelopeProvider)


def test_local_provider_is_rejected_in_pilot(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "pilot")
    monkeypatch.setenv("ENCRYPTION_BACKEND", "local")
    monkeypatch.setenv("KEK_ROOT_SECRET", "not-for-production")
    with pytest.raises(ConfigError):
        get_encryption_provider()


def test_kms_provider_requires_key_and_region(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "pilot")
    monkeypatch.setenv("ENCRYPTION_BACKEND", "kms")
    monkeypatch.delenv("KMS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    with pytest.raises(ConfigError):
        get_encryption_provider()


def test_kms_factory_wires_aws_adapter_without_network(monkeypatch) -> None:
    boto3 = pytest.importorskip("boto3")
    monkeypatch.setenv("ENVIRONMENT", "pilot")
    monkeypatch.setenv("ENCRYPTION_BACKEND", "kms")
    monkeypatch.setenv("KMS_KEY_ID", "alias/nexa-care-pilot")
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: object())
    assert isinstance(get_encryption_provider(), AWSKMSProvider)
