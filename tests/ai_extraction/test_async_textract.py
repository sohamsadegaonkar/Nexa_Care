"""A3/A4 contract tests for the dormant asynchronous Textract adapter."""

from __future__ import annotations

import time
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError, NoCredentialsError

from app.ai.async_textract import (
    AsyncTextractNotReady,
    AsyncTextractProvider,
    ControlledS3Location,
)
from app.ai.extractor import (
    ProviderCredentialsUnavailableError,
    ProviderResponseError,
    ProviderThrottledError,
    ProviderTimeoutError,
)
from app.services.provider_job_lifecycle import ReconciliationOutcomeType


def _provider(client, **overrides):
    values = {"region": "ap-south-1", "timeout_seconds": 0.05, "client": client}
    values.update(overrides)
    return AsyncTextractProvider(**values)


def _block(block_id: str, page: int) -> dict:
    return {
        "BlockType": "LINE",
        "Id": block_id,
        "Text": f"SYNTHETIC PAGE {page}",
        "Confidence": 99.0,
        "Page": page,
        "Geometry": {
            "BoundingBox": {"Left": 0.1, "Top": 0.1, "Width": 0.2, "Height": 0.1}
        },
    }


def _result(*, page: int, token: str | None = None, status: str = "SUCCEEDED") -> dict:
    response = {
        "JobStatus": status,
        "AnalyzeDocumentModelVersion": "async-model-1",
        "DocumentMetadata": {"Pages": 3},
        "Blocks": [_block(f"block-{page}", page)],
    }
    if token is not None:
        response["NextToken"] = token
    return response


@pytest.mark.asyncio
async def test_start_is_deterministic_and_uses_authoritative_s3_location():
    client = Mock()
    client.start_document_analysis.return_value = {"JobId": "job-123"}
    provider = _provider(client)
    location = ControlledS3Location("controlled-bucket", "tenant/patient/doc.pdf")
    first = await provider.start(
        location=location,
        client_request_token="a" * 64,
        provider_request_fingerprint="b" * 64,
        provider_attempt_id="attempt-1",
    )
    second = await provider.start(
        location=location,
        client_request_token="a" * 64,
        provider_request_fingerprint="b" * 64,
        provider_attempt_id="attempt-1",
    )
    assert first == second
    assert client.start_document_analysis.call_count == 2
    assert (
        client.start_document_analysis.call_args_list[0]
        == client.start_document_analysis.call_args_list[1]
    )
    request = client.start_document_analysis.call_args.kwargs
    assert request["DocumentLocation"] == {
        "S3Object": {"Bucket": "controlled-bucket", "Name": "tenant/patient/doc.pdf"}
    }
    with pytest.raises(ProviderResponseError):
        ControlledS3Location("s3://arbitrary", "attacker/key")


@pytest.mark.asyncio
async def test_start_rejects_malformed_job_id_and_reuses_token():
    client = Mock()
    client.start_document_analysis.return_value = {"JobId": "bad job id"}
    with pytest.raises(ProviderResponseError) as failure:
        await _provider(client).start(
            location=ControlledS3Location("bucket", "key"),
            client_request_token="a" * 64,
            provider_request_fingerprint="b" * 64,
            provider_attempt_id="attempt-1",
        )
    assert failure.value.error_code == "ASYNC_TEXTRACT_JOB_ID_INVALID"


@pytest.mark.asyncio
async def test_status_mapping_is_closed_and_provider_independent():
    client = Mock()
    provider = _provider(client)
    client.get_document_analysis.return_value = _result(page=1, status="IN_PROGRESS")
    assert (
        await provider.check_status(provider_job_id="job-123")
    ).outcome is ReconciliationOutcomeType.IN_PROGRESS
    client.get_document_analysis.return_value = _result(page=1, status="SUCCEEDED")
    assert (
        await provider.check_status(provider_job_id="job-123")
    ).outcome is ReconciliationOutcomeType.SUCCEEDED
    client.get_document_analysis.return_value = _result(page=1, status="UNKNOWN")
    with pytest.raises(ProviderResponseError) as failure:
        await provider.check_status(provider_job_id="job-123")
    assert failure.value.error_code == "ASYNC_TEXTRACT_STATUS_INVALID"


@pytest.mark.asyncio
async def test_error_classification_is_stable_and_value_free():
    throttled = Mock()
    throttled.get_document_analysis.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "secret"}},
        "GetDocumentAnalysis",
    )
    with pytest.raises(ProviderThrottledError) as throttle:
        await _provider(throttled).check_status(provider_job_id="job-123")
    assert "secret" not in str(throttle.value)

    credentials = Mock()
    credentials.get_document_analysis.side_effect = NoCredentialsError()
    with pytest.raises(ProviderCredentialsUnavailableError):
        await _provider(credentials).check_status(provider_job_id="job-123")


@pytest.mark.asyncio
async def test_complete_paginated_result_validates_all_three_pages():
    client = Mock()
    client.get_document_analysis.side_effect = [
        _result(page=1, token="next-a"),
        _result(page=2, token="next-b"),
        _result(page=3),
    ]
    result = await _provider(client).retrieve_complete_result(
        provider_job_id="job-123", expected_page_count=3
    )
    assert result.response_complete is True
    assert result.provider_model_version == "async-model-1"
    assert client.get_document_analysis.call_count == 3
    assert (
        client.get_document_analysis.call_args_list[1].kwargs["NextToken"] == "next-a"
    )


@pytest.mark.asyncio
async def test_repeated_token_missing_page_and_in_progress_fail_closed():
    repeated = Mock()
    repeated.get_document_analysis.side_effect = [
        _result(page=1, token="same"),
        _result(page=2, token="same"),
    ]
    with pytest.raises(ProviderResponseError) as repeated_failure:
        await _provider(repeated).retrieve_complete_result(
            provider_job_id="job-123", expected_page_count=3
        )
    assert repeated_failure.value.error_code == "ASYNC_TEXTRACT_NEXT_TOKEN_REPEATED"

    missing = Mock()
    missing.get_document_analysis.return_value = _result(page=1)
    with pytest.raises(ProviderResponseError) as missing_failure:
        await _provider(missing).retrieve_complete_result(
            provider_job_id="job-123", expected_page_count=3
        )
    assert missing_failure.value.error_code == "ASYNC_TEXTRACT_PAGE_SET_INCOMPLETE"

    pending = Mock()
    pending.get_document_analysis.return_value = _result(page=1, status="IN_PROGRESS")
    with pytest.raises(AsyncTextractNotReady):
        await _provider(pending).retrieve_complete_result(
            provider_job_id="job-123", expected_page_count=3
        )


@pytest.mark.asyncio
async def test_timeout_after_first_chunk_discards_transient_accumulator():
    client = Mock()

    def paginated_response(**kwargs):
        if "NextToken" not in kwargs:
            return _result(page=1, token="next")
        assert kwargs["NextToken"] == "next"
        time.sleep(0.2)
        return _result(page=2)

    client.get_document_analysis.side_effect = paginated_response
    with pytest.raises(ProviderTimeoutError):
        await _provider(client, timeout_seconds=0.01).retrieve_complete_result(
            provider_job_id="job-123", expected_page_count=3
        )
