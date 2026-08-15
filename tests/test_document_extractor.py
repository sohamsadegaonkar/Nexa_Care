import asyncio
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from botocore.exceptions import ClientError, NoCredentialsError

from app.ai.identity_decision import IdentityDecisionState
from app.ai.extractor import (
    AwsTextractExtractionProvider,
    DemoExtractionProvider,
    ProviderCredentialsUnavailableError,
    ProviderResponseError,
    ProviderThrottledError,
    ProviderTimeoutError,
    RemoteExtractionProvider,
    RetryableDocumentExtractionError,
    TEXTRACT_PILOT_QUERIES,
    TEXTRACT_PILOT_QUERY_SET_VERSION,
)
from app.core.config import (
    ConfigError,
    DocumentExtractionConfig,
    get_document_extraction_config,
)
from app.models.field_evidence import (
    EvidenceIssue,
    IdentityBindingStatus,
)
from app.services.extraction_evidence_adapter import (
    CurrentExtractionBinding,
    adapt_current_extracted_field,
)


def remote_config(**overrides):
    values = dict(
        provider="remote",
        environment="test",
        api_url="https://extract.example/v1",
        api_key="secret",
        timeout_seconds=1,
        provider_max_attempts=2,
        job_max_attempts=2,
    )
    legacy_attempts = overrides.pop("max_attempts", None)
    if legacy_attempts is not None:
        overrides.update(
            provider_max_attempts=legacy_attempts,
            job_max_attempts=legacy_attempts,
        )
    values.update(overrides)
    return DocumentExtractionConfig(**values)


def test_pilot_query_registry_changes_only_versioned_diagnosis_query():
    assert TEXTRACT_PILOT_QUERY_SET_VERSION == "pilot-v2-diagnosis-label"
    assert TEXTRACT_PILOT_QUERIES == (
        ("patient_name", "What is the patient name?"),
        ("phone", "What patient phone or mobile number is directly written?"),
        ("aadhaar_abha_id", "What is the ABHA identifier?"),
        ("hba1c", "What is the HbA1c result, including units?"),
        ("blood_glucose", "What is the blood glucose result, including units?"),
        ("blood_pressure", "What blood pressure is directly written?"),
        ("heart_rate", "What heart rate is directly written, including units?"),
        ("medication", "What medication name and dose are directly written?"),
        (
            "diagnosis",
            "What text is written next to the label Diagnosis or Provisional Diagnosis?",
        ),
    )


def textract_config(**overrides):
    values = dict(
        provider="aws_textract",
        environment="test",
        aws_region="ap-south-1",
        timeout_seconds=1,
        provider_max_attempts=2,
        job_max_attempts=2,
    )
    legacy_attempts = overrides.pop("max_attempts", None)
    if legacy_attempts is not None:
        overrides.update(
            provider_max_attempts=legacy_attempts,
            job_max_attempts=legacy_attempts,
        )
    values.update(overrides)
    return DocumentExtractionConfig(**values)


def textract_response(*, text="7.2 %", confidence=97.4, bbox=None, pages=1):
    bbox = bbox or {"Left": 0.1, "Top": 0.2, "Width": 0.3, "Height": 0.1}
    return {
        "AnalyzeDocumentModelVersion": "1.0",
        "DocumentMetadata": {"Pages": pages},
        "Blocks": [
            {
                "BlockType": "QUERY",
                "Id": "q1",
                "Query": {"Alias": "hba1c", "Text": "What is the HbA1c?"},
                "Relationships": [{"Type": "ANSWER", "Ids": ["a1"]}],
            },
            {
                "BlockType": "QUERY_RESULT",
                "Id": "a1",
                "Text": text,
                "Confidence": confidence,
                "Page": 1,
                "Geometry": {"BoundingBox": bbox},
            },
        ],
    }


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
    assert result.document.extraction_confidence == 0.7


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
    with pytest.raises(ProviderResponseError) as exc:
        await RemoteExtractionProvider(remote_config(), client).extract_bytes(
            b"%PDF-1.7", mime_type="application/pdf", request_id="req-invalid"
        )
    assert exc.value.error_code == "EXTRACTION_RESPONSE_INVALID"


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
    assert results[0].document.diagnoses == []


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


def test_textract_config_uses_default_region_without_api_key(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "pilot")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("DOCUMENT_EXTRACTION_PROVIDER", "aws_textract")
    monkeypatch.delenv("DOCUMENT_AI_API_KEY", raising=False)
    monkeypatch.delenv("DOCUMENT_AI_API_URL", raising=False)
    monkeypatch.delenv("DOCUMENT_AI_AWS_REGION", raising=False)
    config = get_document_extraction_config()
    assert config.aws_region == "ap-south-1"


def test_textract_client_uses_normal_sdk_chain_without_static_credentials():
    with patch("app.ai.extractor.boto3.client", return_value=Mock()) as create:
        AwsTextractExtractionProvider(textract_config())._get_client()
    kwargs = create.call_args.kwargs
    assert "aws_access_key_id" not in kwargs
    assert "aws_secret_access_key" not in kwargs
    assert "aws_session_token" not in kwargs


def test_textract_provider_passes_validated_page_count_to_parser_without_aws():
    response = textract_response(pages=1)
    with patch("app.ai.extractor.parse_textract_blocks", return_value=[]) as parser:
        AwsTextractExtractionProvider._parse_response(response)
    assert parser.call_args.kwargs["validated_document_page_count"] == 1


@pytest.mark.asyncio
async def test_textract_query_maps_authentic_field_evidence():
    client = Mock()
    client.analyze_document.return_value = textract_response()
    result = await AwsTextractExtractionProvider(
        textract_config(), client
    ).extract_bytes(b"%PDF-1.7", mime_type="application/pdf", request_id="req-map")
    item = result.document.field_evidence[0]
    assert item.canonical_field_name == "hba1c"
    assert item.raw_value == item.source_text == "7.2 %"
    assert item.page_number == 0
    assert item.bounding_box.model_dump() == {
        "left": 0.1,
        "top": 0.2,
        "right": 0.4,
        "bottom": 0.30000000000000004,
    }
    assert item.field_confidence == pytest.approx(0.974)
    assert result.document.extraction_confidence is None
    request = client.analyze_document.call_args.kwargs
    assert request["FeatureTypes"] == ["QUERIES", "FORMS", "TABLES"]
    assert request["QueriesConfig"]["Queries"][0]["Pages"] == ["1"]


@pytest.mark.asyncio
async def test_textract_invalid_geometry_is_retained_as_incomplete_not_fabricated():
    client = Mock()
    client.analyze_document.return_value = textract_response(
        bbox={"Left": 0.9, "Top": 0.2, "Width": 0.3, "Height": 0.1}
    )
    document = await AwsTextractExtractionProvider(
        textract_config(), client
    ).extract_bytes(b"image", mime_type="image/png", request_id="req-geometry")
    item = document.document.field_evidence[0]
    assert item.bounding_box is None
    now = datetime.now(timezone.utc)
    evidence = adapt_current_extracted_field(
        document=document.document,
        field_name=item.canonical_field_name,
        raw_value=item.raw_value,
        provider_evidence=item,
        binding=CurrentExtractionBinding(
            patient_id="11111111-1111-4111-8111-111111111111",
            tenant_id="22222222-2222-4222-8222-222222222222",
            source_document_id="33333333-3333-4333-8333-333333333333",
            source_document_hash="a" * 64,
            ingestion_id="33333333-3333-4333-8333-333333333333",
            job_id="44444444-4444-4444-8444-444444444444",
            attempt_number=1,
            attempt_id="attempt-1",
            created_at=now,
            extracted_at=now,
        ),
    )
    assert not evidence.visual_evidence_complete
    assert EvidenceIssue.BOUNDING_BOX_UNAVAILABLE in evidence.visual.issues


@pytest.mark.parametrize(
    ("identity_state", "expected_issue"),
    [
        (IdentityDecisionState.IDENTITY_CONFIRMED, None),
        (
            IdentityDecisionState.IDENTITY_DISCREPANCY,
            EvidenceIssue.IDENTITY_MISMATCH,
        ),
        (
            IdentityDecisionState.IDENTITY_CONFLICTING,
            EvidenceIssue.IDENTITY_MISMATCH,
        ),
        (
            IdentityDecisionState.IDENTITY_INSUFFICIENT,
            EvidenceIssue.IDENTITY_UNAVAILABLE,
        ),
    ],
)
@pytest.mark.asyncio
async def test_adapter_maps_document_identity_state_without_weakening_binding(
    identity_state, expected_issue
):
    client = Mock()
    client.analyze_document.return_value = textract_response()
    document = await AwsTextractExtractionProvider(
        textract_config(), client
    ).extract_bytes(b"image", mime_type="image/png", request_id="req-identity")
    item = document.document.field_evidence[0]
    now = datetime.now(timezone.utc)

    evidence = adapt_current_extracted_field(
        document=document.document,
        field_name=item.canonical_field_name,
        raw_value=item.raw_value,
        provider_evidence=item,
        binding=CurrentExtractionBinding(
            patient_id="11111111-1111-4111-8111-111111111111",
            tenant_id="22222222-2222-4222-8222-222222222222",
            source_document_id="33333333-3333-4333-8333-333333333333",
            source_document_hash="a" * 64,
            ingestion_id="33333333-3333-4333-8333-333333333333",
            job_id="44444444-4444-4444-8444-444444444444",
            attempt_number=1,
            attempt_id="attempt-1",
            created_at=now,
            extracted_at=now,
            document_identity_state=identity_state,
        ),
    )

    assert evidence.identity.binding_status is IdentityBindingStatus.VERIFIED
    if expected_issue is None:
        assert EvidenceIssue.IDENTITY_MISMATCH not in evidence.identity.issues
        assert EvidenceIssue.IDENTITY_UNAVAILABLE not in evidence.identity.issues
    else:
        assert expected_issue in evidence.identity.issues


@pytest.mark.asyncio
async def test_textract_empty_answer_is_omitted():
    client = Mock()
    client.analyze_document.return_value = textract_response(text="  ")
    result = await AwsTextractExtractionProvider(
        textract_config(), client
    ).extract_bytes(b"image", mime_type="image/jpeg", request_id="req-empty")
    assert result.document.field_evidence == []


@pytest.mark.asyncio
async def test_textract_timeout_is_stable_and_retryable():
    provider = AwsTextractExtractionProvider(textract_config(max_attempts=1), Mock())

    async def fail_wait(awaitable, *, timeout):
        _ = timeout
        awaitable.close()
        raise asyncio.TimeoutError

    with patch("app.ai.extractor.asyncio.wait_for", new=fail_wait):
        with pytest.raises(ProviderTimeoutError) as exc:
            await provider.extract_bytes(
                b"image", mime_type="image/png", request_id="req-timeout"
            )
    assert exc.value.error_code == "EXTRACTION_PROVIDER_TIMEOUT"


@pytest.mark.asyncio
async def test_textract_throttling_is_stable_and_retryable():
    client = Mock()
    client.analyze_document.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "sensitive"}},
        "AnalyzeDocument",
    )
    provider = AwsTextractExtractionProvider(textract_config(max_attempts=1), client)
    with pytest.raises(ProviderThrottledError) as exc:
        await provider.extract_bytes(
            b"image", mime_type="image/png", request_id="req-throttle"
        )
    assert exc.value.error_code == "EXTRACTION_PROVIDER_THROTTLED"
    assert "sensitive" not in str(exc.value)


@pytest.mark.asyncio
async def test_textract_malformed_response_is_terminal():
    client = Mock()
    client.analyze_document.return_value = {"Blocks": "provider payload"}
    with pytest.raises(ProviderResponseError):
        await AwsTextractExtractionProvider(textract_config(), client).extract_bytes(
            b"image", mime_type="image/png", request_id="req-malformed"
        )


@pytest.mark.asyncio
async def test_textract_unavailable_credentials_is_stable():
    client = Mock()
    client.analyze_document.side_effect = NoCredentialsError()
    with pytest.raises(ProviderCredentialsUnavailableError) as exc:
        await AwsTextractExtractionProvider(textract_config(), client).extract_bytes(
            b"image", mime_type="image/png", request_id="req-creds"
        )
    assert exc.value.error_code == "EXTRACTION_CREDENTIALS_UNAVAILABLE"


@pytest.mark.asyncio
async def test_textract_logs_neither_extracted_value_nor_provider_payload(caplog):
    client = Mock()
    client.analyze_document.return_value = textract_response(
        text="SYNTHETIC_PRIVATE_LAB_VALUE"
    )
    await AwsTextractExtractionProvider(textract_config(), client).extract_bytes(
        b"image", mime_type="image/png", request_id="safe-request-id"
    )
    assert "SYNTHETIC_PRIVATE_LAB_VALUE" not in caplog.text
    assert "BoundingBox" not in caplog.text


def test_extractor_does_not_import_local_ml():
    assert "torch" not in sys.modules
    assert "transformers" not in sys.modules
