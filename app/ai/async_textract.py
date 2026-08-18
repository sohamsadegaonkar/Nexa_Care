"""Provider-specific asynchronous Textract boundary for Scenario 6.

The adapter is intentionally dormant: it exposes a typed, provider-specific
transport for later feature-gated orchestration, but it does not start a
worker, mutate lifecycle rows, or create clinical projections. Partial
responses are accumulated only in memory and are discarded on any failure.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    PartialCredentialsError,
)

from app.ai.extractor import (
    AwsTextractExtractionProvider,
    ExtractionProviderResult,
    InvalidDocumentError,
    ProviderCredentialsUnavailableError,
    ProviderResponseError,
    ProviderThrottledError,
    ProviderTimeoutError,
    RetryableDocumentExtractionError,
    TEXTRACT_PILOT_QUERIES,
    _complete_provider_result,
    _trace,
)
from app.models.ai_models import ProviderAttemptOutcome

ASYNC_TEXTRACT_CONTRACT_VERSION = "textract-async-analysis/1.0"
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_RESULTS = 1000
_MAX_PAGES = 1000


class AsyncTextractStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"


class AsyncTextractContractError(ProviderResponseError):
    """Value-free, stable async-provider contract failure."""

    def __init__(self, code: str):
        self.error_code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ControlledS3Location:
    """An already-authorized S3 object location."""

    bucket: str
    key: str
    version_id: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.bucket, str)
            or not self.bucket.strip()
            or "://" in self.bucket
            or "/" in self.bucket
            or not isinstance(self.key, str)
            or not self.key.strip()
            or self.key.startswith("/")
        ):
            raise AsyncTextractContractError("ASYNC_TEXTRACT_S3_LOCATION_INVALID")
        if self.version_id is not None and (
            not isinstance(self.version_id, str) or not self.version_id.strip()
        ):
            raise AsyncTextractContractError("ASYNC_TEXTRACT_S3_VERSION_INVALID")


@dataclass(frozen=True, slots=True)
class AsyncTextractStartResult:
    provider_job_id: str
    provider_adapter: str = "aws_textract"
    provider_contract_version: str = ASYNC_TEXTRACT_CONTRACT_VERSION


class AsyncTextractNotReady(RetryableDocumentExtractionError):
    error_code = "ASYNC_TEXTRACT_NOT_READY"


def _safe_client_error(exc: ClientError) -> Exception:
    code = str(exc.response.get("Error", {}).get("Code", ""))
    if code in {
        "ThrottlingException",
        "ProvisionedThroughputExceededException",
        "LimitExceededException",
        "InternalServerError",
        "ServiceUnavailable",
    }:
        return ProviderThrottledError("Async Textract request was throttled")
    if code in {
        "UnrecognizedClientException",
        "AccessDeniedException",
        "InvalidClientTokenId",
    }:
        return ProviderCredentialsUnavailableError(
            "Async Textract credentials are unavailable"
        )
    if code in {
        "BadDocumentException",
        "DocumentTooLargeException",
        "InvalidParameterException",
        "UnsupportedDocumentException",
    }:
        return InvalidDocumentError("Async Textract rejected the document")
    return ProviderResponseError("Async Textract request failed safely")


def _validate_job_id(value: object) -> str:
    if not isinstance(value, str) or not _JOB_ID_RE.fullmatch(value):
        raise AsyncTextractContractError("ASYNC_TEXTRACT_JOB_ID_INVALID")
    return value


def _validate_token(value: object) -> str:
    if not isinstance(value, str) or not _JOB_ID_RE.fullmatch(value):
        raise AsyncTextractContractError("ASYNC_TEXTRACT_CLIENT_TOKEN_INVALID")
    return value


class AsyncTextractProvider:
    """Small, injectable Start/GetDocumentAnalysis adapter."""

    adapter_identity = "aws_textract"
    contract_version = ASYNC_TEXTRACT_CONTRACT_VERSION

    def __init__(
        self,
        *,
        region: str,
        timeout_seconds: float = 30.0,
        client: Any | None = None,
        max_result_pages: int = _MAX_PAGES,
    ) -> None:
        if not isinstance(region, str) or not region.strip():
            raise ValueError("region is required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not 1 <= max_result_pages <= _MAX_PAGES:
            raise ValueError("max_result_pages is out of bounds")
        self.region = region
        self.timeout_seconds = timeout_seconds
        self._client = client
        self.max_result_pages = max_result_pages
        self.last_observed_page_count: int | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            import boto3
            from botocore.config import Config as BotoConfig

            self._client = boto3.client(
                "textract",
                region_name=self.region,
                config=BotoConfig(
                    connect_timeout=self.timeout_seconds,
                    read_timeout=self.timeout_seconds,
                    retries={"max_attempts": 0},
                ),
            )
        return self._client

    async def _call(self, method: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(getattr(self._get_client(), method), **kwargs),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise ProviderTimeoutError("Async Textract request timed out") from exc
        except (NoCredentialsError, PartialCredentialsError) as exc:
            raise ProviderCredentialsUnavailableError(
                "Async Textract credentials are unavailable"
            ) from exc
        except ClientError as exc:
            raise _safe_client_error(exc) from exc
        except BotoCoreError as exc:
            raise RetryableDocumentExtractionError(
                "Async Textract is temporarily unavailable"
            ) from exc
        if not isinstance(response, dict):
            raise ProviderResponseError("Async Textract response failed validation")
        return response

    async def start(
        self,
        *,
        location: ControlledS3Location,
        client_request_token: str,
        provider_request_fingerprint: str,
        provider_attempt_id: str,
    ) -> AsyncTextractStartResult:
        """Start an idempotent operation without returning provider payloads."""

        if not isinstance(location, ControlledS3Location):
            raise AsyncTextractContractError("ASYNC_TEXTRACT_S3_LOCATION_INVALID")
        token = _validate_token(client_request_token)
        if not isinstance(provider_request_fingerprint, str) or not re.fullmatch(
            r"[a-fA-F0-9]{64}", provider_request_fingerprint
        ):
            raise AsyncTextractContractError(
                "ASYNC_TEXTRACT_REQUEST_FINGERPRINT_INVALID"
            )
        if not isinstance(provider_attempt_id, str) or not provider_attempt_id.strip():
            raise AsyncTextractContractError("ASYNC_TEXTRACT_ATTEMPT_ID_INVALID")
        s3_object: dict[str, str] = {"Bucket": location.bucket, "Name": location.key}
        if location.version_id is not None:
            s3_object["Version"] = location.version_id
        response = await self._call(
            "start_document_analysis",
            DocumentLocation={"S3Object": s3_object},
            FeatureTypes=["QUERIES", "FORMS", "TABLES"],
            QueriesConfig={
                "Queries": [
                    {"Alias": alias, "Text": text, "Pages": ["*"]}
                    for alias, text in TEXTRACT_PILOT_QUERIES
                ]
            },
            ClientRequestToken=token,
            JobTag=f"nexa-{provider_attempt_id}",
        )
        return AsyncTextractStartResult(
            provider_job_id=_validate_job_id(response.get("JobId"))
        )

    async def check_status(self, *, provider_job_id: str):
        from app.services.provider_job_lifecycle import (
            ProviderReconciliationOutcome,
            ReconciliationOutcomeType,
        )

        response = await self._call(
            "get_document_analysis", JobId=_validate_job_id(provider_job_id)
        )
        try:
            state = AsyncTextractStatus(response.get("JobStatus"))
        except ValueError as exc:
            raise AsyncTextractContractError("ASYNC_TEXTRACT_STATUS_INVALID") from exc
        if state is AsyncTextractStatus.IN_PROGRESS:
            outcome = ReconciliationOutcomeType.IN_PROGRESS
        elif state is AsyncTextractStatus.SUCCEEDED:
            outcome = ReconciliationOutcomeType.SUCCEEDED
        else:
            outcome = ReconciliationOutcomeType.FAILED_TERMINAL
        return ProviderReconciliationOutcome(outcome)

    async def retrieve_complete_result(
        self, *, provider_job_id: str, expected_page_count: int
    ) -> ExtractionProviderResult:
        """Retrieve and validate every page before exposing an extraction result."""

        if not isinstance(expected_page_count, int) or expected_page_count <= 0:
            raise AsyncTextractContractError(
                "ASYNC_TEXTRACT_EXPECTED_PAGE_COUNT_INVALID"
            )
        job_id = _validate_job_id(provider_job_id)
        blocks: list[dict[str, Any]] = []
        block_ids: set[str] = set()
        seen_tokens: set[str] = set()
        observed_pages: set[int] = set()
        model_version: str | None = None
        next_token: str | None = None
        calls = 0
        while True:
            calls += 1
            if calls > self.max_result_pages:
                raise AsyncTextractContractError("ASYNC_TEXTRACT_PAGINATION_LIMIT")
            kwargs: dict[str, Any] = {"JobId": job_id, "MaxResults": 1000}
            if next_token is not None:
                kwargs["NextToken"] = next_token
            response = await self._call("get_document_analysis", **kwargs)
            try:
                status = AsyncTextractStatus(response.get("JobStatus"))
            except ValueError as exc:
                raise AsyncTextractContractError(
                    "ASYNC_TEXTRACT_STATUS_INVALID"
                ) from exc
            if status is AsyncTextractStatus.IN_PROGRESS:
                raise AsyncTextractNotReady("Async Textract result is not complete")
            if status is not AsyncTextractStatus.SUCCEEDED:
                raise AsyncTextractContractError("ASYNC_TEXTRACT_RESULT_FAILED")
            metadata = response.get("DocumentMetadata")
            page_count = metadata.get("Pages") if isinstance(metadata, dict) else None
            if page_count != expected_page_count:
                raise AsyncTextractContractError("ASYNC_TEXTRACT_PAGE_COUNT_MISMATCH")
            raw_model = response.get("AnalyzeDocumentModelVersion")
            if not isinstance(raw_model, str) or not raw_model.strip():
                raise AsyncTextractContractError("ASYNC_TEXTRACT_MODEL_VERSION_INVALID")
            if model_version is None:
                model_version = raw_model
            elif model_version != raw_model:
                raise AsyncTextractContractError("ASYNC_TEXTRACT_PROVENANCE_MISMATCH")
            raw_blocks = response.get("Blocks")
            if not isinstance(raw_blocks, list):
                raise AsyncTextractContractError("ASYNC_TEXTRACT_BLOCKS_INVALID")
            for block in raw_blocks:
                if not isinstance(block, dict):
                    raise AsyncTextractContractError("ASYNC_TEXTRACT_BLOCKS_INVALID")
                block_id = block.get("Id")
                if not isinstance(block_id, str) or not block_id.strip():
                    raise AsyncTextractContractError("ASYNC_TEXTRACT_BLOCK_ID_INVALID")
                if block_id in block_ids:
                    raise AsyncTextractContractError("ASYNC_TEXTRACT_DUPLICATE_BLOCK")
                block_ids.add(block_id)
                page = block.get("Page")
                if not isinstance(page, int) or not 1 <= page <= expected_page_count:
                    raise AsyncTextractContractError("ASYNC_TEXTRACT_PAGE_INVALID")
                observed_pages.add(page)
                blocks.append(block)
            candidate = response.get("NextToken")
            if candidate is None:
                break
            if not isinstance(candidate, str) or not candidate.strip():
                raise AsyncTextractContractError("ASYNC_TEXTRACT_NEXT_TOKEN_INVALID")
            if candidate in seen_tokens:
                raise AsyncTextractContractError("ASYNC_TEXTRACT_NEXT_TOKEN_REPEATED")
            seen_tokens.add(candidate)
            next_token = candidate
        if observed_pages != set(range(1, expected_page_count + 1)):
            raise AsyncTextractContractError("ASYNC_TEXTRACT_PAGE_SET_INCOMPLETE")
        if not blocks or model_version is None:
            raise AsyncTextractContractError("ASYNC_TEXTRACT_RESULT_EMPTY")
        self.last_observed_page_count = len(observed_pages)
        document = AwsTextractExtractionProvider._parse_response(
            {
                "AnalyzeDocumentModelVersion": model_version,
                "DocumentMetadata": {"Pages": expected_page_count},
                "Blocks": blocks,
            },
            require_single_page=False,
        )
        trace = _trace(
            subattempt=1,
            adapter=self.adapter_identity,
            contract_version=self.contract_version,
            model_version=model_version,
            outcome=ProviderAttemptOutcome.SUCCEEDED,
        )
        return _complete_provider_result(
            document,
            adapter=self.adapter_identity,
            contract_version=self.contract_version,
            model_version=model_version,
            traces=(trace,),
        )


AsyncTextractExtractionProvider = AsyncTextractProvider
