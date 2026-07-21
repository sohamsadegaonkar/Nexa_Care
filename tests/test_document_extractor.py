import sys
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.ai.extractor import (
    DemoExtractionProvider,
    InvalidDocumentError,
    RemoteExtractionProvider,
    RetryableDocumentExtractionError,
)
from app.core.config import (
    ConfigError,
    DocumentExtractionConfig,
    get_document_extraction_config,
)


def remote_config(**overrides):
    values = dict(
        provider="remote",
        environment="test",
        api_url="https://extract.example/v1",
        api_key="secret",
        timeout_seconds=1,
        max_attempts=2,
    )
    values.update(overrides)
    return DocumentExtractionConfig(**values)


@pytest.mark.asyncio
async def test_remote_provider_validates_response_schema():
    response = type(
        "Response",
        (),
        {
            "status_code": 200,
            "json": lambda self: {
                "patient_name": "Extracted Name",
                "aadhaar_abha_id": "id",
                "phone": "phone",
                "diagnoses": [],
                "lab_results": [],
                "prescriptions": [],
                "extraction_confidence": 0.7,
            },
        },
    )()
    client = AsyncMock()
    client.post.return_value = response
    result = await RemoteExtractionProvider(remote_config(), client).extract_bytes(
        b"%PDF-1.7", mime_type="application/pdf", request_id="req-1"
    )
    assert result.extraction_confidence == 0.7


@pytest.mark.asyncio
async def test_timeout_is_retryable_and_never_returns_clinical_output():
    client = AsyncMock()
    client.post.side_effect = httpx.ReadTimeout("timeout")
    with patch("app.ai.extractor.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RetryableDocumentExtractionError) as exc:
            await RemoteExtractionProvider(remote_config(), client).extract_bytes(
                b"%PDF-1.7", mime_type="application/pdf", request_id="req-timeout"
            )
    assert exc.value.error_code == "EXTRACTION_UPSTREAM_RETRYABLE"
    assert client.post.await_count == 2


@pytest.mark.asyncio
async def test_invalid_upstream_payload_is_terminal():
    response = type(
        "Response", (), {"status_code": 200, "json": lambda self: {"fake": "clinical"}}
    )()
    client = AsyncMock()
    client.post.return_value = response
    with pytest.raises(InvalidDocumentError):
        await RemoteExtractionProvider(remote_config(), client).extract_bytes(
            b"%PDF-1.7", mime_type="application/pdf", request_id="req-invalid"
        )


@pytest.mark.asyncio
async def test_demo_provider_has_no_filename_behavior():
    provider = DemoExtractionProvider()
    results = [
        await provider.extract_bytes(
            b"same", mime_type="application/pdf", request_id=name
        )
        for name in ("panel.pdf", "demo.pdf", "aarav.pdf")
    ]
    assert results[0] == results[1] == results[2]
    assert results[0].diagnoses == []


@pytest.mark.parametrize("environment", ["production", "staging", "preview", "pilot"])
def test_demo_provider_rejected_in_production_like_environments(
    monkeypatch, environment
):
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("DOCUMENT_EXTRACTION_PROVIDER", "demo")
    with pytest.raises(ConfigError):
        get_document_extraction_config()


def test_remote_provider_requires_key_and_url(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DOCUMENT_EXTRACTION_PROVIDER", "remote")
    monkeypatch.delenv("DOCUMENT_AI_API_KEY", raising=False)
    monkeypatch.delenv("DOCUMENT_AI_API_URL", raising=False)
    with pytest.raises(ConfigError):
        get_document_extraction_config()


def test_extractor_does_not_import_local_ml():
    assert "torch" not in sys.modules
    assert "transformers" not in sys.modules
