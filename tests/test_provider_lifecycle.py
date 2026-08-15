from __future__ import annotations

import asyncio
import time
from threading import Event
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from app.models import ai_models
from app.ai.extractor import (
    AwsTextractExtractionProvider,
    ExtractionProviderResult,
    ProviderResponseError,
    ProviderTimeoutError,
    RemoteExtractionProvider,
)
from app.core.config import DocumentExtractionConfig, get_document_extraction_config
from app.models.ai_models import ExtractedMedicalDocument
from app.services.pipeline_orchestrator import _validated_provider_result


def _aws_config(**overrides: object) -> DocumentExtractionConfig:
    values: dict[str, object] = {
        "provider": "aws_textract",
        "environment": "test",
        "aws_region": "ap-south-1",
        "timeout_seconds": 0.03,
        "provider_max_attempts": 2,
        "job_max_attempts": 3,
    }
    values.update(overrides)
    return DocumentExtractionConfig(**values)


def _remote_config(**overrides: object) -> DocumentExtractionConfig:
    values: dict[str, object] = {
        "provider": "remote",
        "environment": "test",
        "api_url": "https://extract.example.test/v1",
        "api_key": "synthetic-test-key",
        "provider_max_attempts": 2,
        "job_max_attempts": 3,
    }
    values.update(overrides)
    return DocumentExtractionConfig(**values)


def _textract_response() -> dict:
    return {
        "AnalyzeDocumentModelVersion": "1.0",
        "DocumentMetadata": {"Pages": 1},
        "Blocks": [],
    }


def _remote_document(*, provider: str, version: str) -> dict:
    return {
        "patient_name": "",
        "phone": "",
        "aadhaar_abha_id": "",
        "diagnoses": [],
        "lab_results": [],
        "prescriptions": [],
        "extraction_confidence": None,
        "field_evidence": [
            {
                "canonical_field_name": "hba1c",
                "raw_value": "synthetic",
                "provider_name": provider,
                "provider_api_version": version,
                "extraction_timestamp": datetime.now(timezone.utc),
            }
        ],
    }


def test_legacy_retry_budget_is_deprecated_fallback(monkeypatch, caplog) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DOCUMENT_EXTRACTION_PROVIDER", "aws_textract")
    monkeypatch.setenv("DOCUMENT_AI_MAX_ATTEMPTS", "4")
    monkeypatch.delenv("DOCUMENT_AI_PROVIDER_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("DOCUMENT_AI_JOB_MAX_ATTEMPTS", raising=False)

    config = get_document_extraction_config()

    assert config.provider_max_attempts == config.job_max_attempts == 4
    assert "DOCUMENT_AI_MAX_ATTEMPTS is deprecated" in caplog.text


def test_explicit_retry_budgets_override_legacy(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DOCUMENT_EXTRACTION_PROVIDER", "aws_textract")
    monkeypatch.setenv("DOCUMENT_AI_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("DOCUMENT_AI_PROVIDER_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("DOCUMENT_AI_JOB_MAX_ATTEMPTS", "3")

    config = get_document_extraction_config()

    assert config.provider_max_attempts == 2
    assert config.job_max_attempts == 3


@pytest.mark.asyncio
async def test_provider_json_cannot_set_trusted_adapter_authority() -> None:
    payload = _remote_document(provider="untrusted-provider", version="X")
    payload.update(
        {
            "provider_adapter": "aws_textract",
            "provider_contract_version": "forged",
            "response_complete": True,
            "trusted_provenance": {"response_complete": True},
            "_trusted_provenance": {"response_complete": True},
        }
    )
    response = type(
        "Response", (), {"status_code": 200, "json": lambda self: payload}
    )()
    client = AsyncMock()
    client.post.return_value = response

    result = await RemoteExtractionProvider(_remote_config(), client).extract_bytes(
        b"synthetic", mime_type="application/pdf", request_id="payload-forgery"
    )

    assert result.provider_adapter == "remote"
    assert result.provider_contract_version != "forged"
    legacy_binder = "_bind_" + "trusted_provenance"
    assert not hasattr(ExtractedMedicalDocument, legacy_binder)
    legacy_capability = "_TRUSTED_" + "PROVENANCE_BINDING_CAPABILITY"
    assert legacy_capability not in vars(ai_models)


def test_document_copy_and_serialization_cannot_create_provider_result() -> None:
    document = ExtractedMedicalDocument(
        patient_name="",
        phone="",
        aadhaar_abha_id="",
        diagnoses=[],
        lab_results=[],
        prescriptions=[],
    )
    copied = document.model_copy()
    reconstructed = ExtractedMedicalDocument.model_validate(document.model_dump())

    assert not isinstance(copied, ExtractionProviderResult)
    assert not isinstance(reconstructed, ExtractionProviderResult)
    assert not hasattr(document, "trusted_provenance")


def test_forged_envelope_from_custom_provider_is_not_trusted() -> None:
    document = ExtractedMedicalDocument(
        patient_name="",
        phone="",
        aadhaar_abha_id="",
        diagnoses=[],
        lab_results=[],
        prescriptions=[],
    )
    forged = ExtractionProviderResult(
        document=document,
        provider_adapter="aws_textract",
        provider_contract_version="pilot-v2-diagnosis-label",
        provider_model_version=None,
        response_complete=True,
        provider_attempt_traces=(),
    )

    class CustomProvider:
        adapter_identity = "aws_textract"
        contract_version = "pilot-v2-diagnosis-label"

    with pytest.raises(ProviderResponseError):
        _validated_provider_result(
            forged,
            extractor=CustomProvider(),
            configured_provider="aws_textract",
        )


@pytest.mark.asyncio
async def test_remote_mixed_model_versions_fail_closed() -> None:
    first = _remote_document(provider="one", version="X")
    first["field_evidence"].append(
        {
            **first["field_evidence"][0],
            "canonical_field_name": "blood_glucose",
            "provider_api_version": "Y",
        }
    )
    response = type("Response", (), {"status_code": 200, "json": lambda self: first})()
    client = AsyncMock()
    client.post.return_value = response

    with pytest.raises(ProviderResponseError) as error:
        await RemoteExtractionProvider(_remote_config(), client).extract_bytes(
            b"synthetic", mime_type="application/pdf", request_id="mixed-version"
        )

    assert error.value.error_code == "EXTRACTION_RESPONSE_INVALID"
    assert error.value.provider_attempt_traces[-1].outcome.value == "INVALID_RESPONSE"


@pytest.mark.asyncio
async def test_remote_mixed_provider_names_fail_closed() -> None:
    first = _remote_document(provider="one", version="X")
    first["field_evidence"].append(
        {
            **first["field_evidence"][0],
            "canonical_field_name": "blood_glucose",
            "provider_name": "two",
        }
    )
    response = type("Response", (), {"status_code": 200, "json": lambda self: first})()
    client = AsyncMock()
    client.post.return_value = response

    with pytest.raises(ProviderResponseError):
        await RemoteExtractionProvider(_remote_config(), client).extract_bytes(
            b"synthetic", mime_type="application/pdf", request_id="mixed-provider"
        )


@pytest.mark.asyncio
async def test_remote_internal_retry_records_only_complete_second_result() -> None:
    response = type(
        "Response",
        (),
        {
            "status_code": 200,
            "json": lambda self: _remote_document(provider="x", version="Y"),
        },
    )()
    client = AsyncMock()
    client.post.side_effect = [httpx.ConnectError("controlled"), response]

    result = await RemoteExtractionProvider(_remote_config(), client).extract_bytes(
        b"synthetic", mime_type="application/pdf", request_id="internal-retry"
    )

    assert result.provider_model_version == "Y"
    assert [trace.outcome.value for trace in result.provider_attempt_traces] == [
        "RETRYABLE_ERROR",
        "SUCCEEDED",
    ]
    assert [
        trace.provider_model_version for trace in result.provider_attempt_traces
    ] == [
        None,
        "Y",
    ]


@pytest.mark.asyncio
async def test_late_textract_thread_cannot_observe_or_return_after_timeout() -> None:
    started = Event()
    release = Event()
    observed: list[dict] = []

    class BlockingClient:
        def analyze_document(self, **_kwargs):
            started.set()
            while not release.is_set():
                time.sleep(0.005)
            return _textract_response()

    provider = AwsTextractExtractionProvider(
        _aws_config(provider_max_attempts=1),
        BlockingClient(),
        successful_response_observer=observed.append,
    )
    task = asyncio.create_task(
        provider.extract_bytes(
            b"synthetic", mime_type="image/png", request_id="timeout"
        )
    )
    await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=1)
    with pytest.raises(ProviderTimeoutError) as error:
        await task
    assert error.value.provider_attempt_traces[0].outcome.value == "TIMEOUT"
    release.set()
    await asyncio.sleep(0.05)
    assert observed == []
