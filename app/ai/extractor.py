"""Fail-closed document extraction providers.

Provider selection is explicit application configuration. The demo provider is
available only in enumerated safe environments and is never selected merely
because remote credentials are absent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any, Literal

import boto3
import httpx
from botocore.config import Config as BotoConfig
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    PartialCredentialsError,
)
from pydantic import ValidationError

from app.ai.textract_parser import parse_textract_blocks
from app.core.config import DocumentExtractionConfig, get_document_extraction_config
from app.models.ai_models import (
    ExtractedMedicalDocument,
    ProviderAttemptOutcome,
    ProviderAttemptTrace,
)

logger = logging.getLogger("nexa_logger")


@dataclass(frozen=True, slots=True)
class ExtractionProviderResult:
    """Complete result returned only by a validated Nexa adapter invocation.

    This is deliberately constructible Python data, not a secret capability.
    The orchestrator accepts it only after comparing its server-owned metadata
    to the actual configured adapter instance that it invoked.
    """

    document: ExtractedMedicalDocument
    provider_adapter: str
    provider_contract_version: str
    provider_model_version: str | None
    response_complete: Literal[True]
    provider_attempt_traces: tuple[ProviderAttemptTrace, ...]

    def __post_init__(self) -> None:
        if not self.provider_adapter or not self.provider_contract_version:
            raise ValueError("provider result provenance must be present")
        if self.response_complete is not True:
            raise ValueError("provider result must represent complete success")
        if self.provider_attempt_traces and (
            self.provider_attempt_traces[-1].outcome
            is not ProviderAttemptOutcome.SUCCEEDED
        ):
            raise ValueError("provider result must end with successful subattempt")


class DocumentExtractionError(RuntimeError):
    """Sanitized provider failure safe to persist on a job."""

    error_code = "EXTRACTION_FAILED"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        upstream_status: int | None = None,
        provider_attempt_traces: tuple[ProviderAttemptTrace, ...] = (),
    ) -> None:
        super().__init__(message)
        self.upstream_status = upstream_status
        self.provider_attempt_traces = provider_attempt_traces


class RetryableDocumentExtractionError(DocumentExtractionError):
    error_code = "EXTRACTION_UPSTREAM_RETRYABLE"
    retryable = True


class InvalidDocumentError(DocumentExtractionError):
    error_code = "INVALID_DOCUMENT"


class ProviderTimeoutError(RetryableDocumentExtractionError):
    error_code = "EXTRACTION_PROVIDER_TIMEOUT"


class ProviderThrottledError(RetryableDocumentExtractionError):
    error_code = "EXTRACTION_PROVIDER_THROTTLED"


class ProviderResponseError(DocumentExtractionError):
    error_code = "EXTRACTION_RESPONSE_INVALID"


class ProviderCredentialsUnavailableError(DocumentExtractionError):
    error_code = "EXTRACTION_CREDENTIALS_UNAVAILABLE"


TEXTRACT_MAX_SYNC_BYTES = 10 * 1024 * 1024
TEXTRACT_SUPPORTED_MIME_TYPES = frozenset(
    {"application/pdf", "image/png", "image/jpeg", "image/tiff"}
)

# Nine controlled, extraction-only questions. This remains below Textract's
# synchronous limit of 15 queries per page.
TEXTRACT_PILOT_QUERY_SET_VERSION = "pilot-v2-diagnosis-label"
REMOTE_MEDICAL_DOCUMENT_CONTRACT_VERSION = "remote-medical-document/1.0"
DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION = "demo-medical-document/1.0"
TEXTRACT_PILOT_QUERIES: tuple[tuple[str, str], ...] = (
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

_IDENTITY_FIELDS = frozenset({"patient_name", "phone", "aadhaar_abha_id"})
_RETRYABLE_TEXTRACT_CODES = frozenset(
    {
        "InternalServerError",
        "ProvisionedThroughputExceededException",
        "ThrottlingException",
        "RequestTimeout",
        "RequestTimeoutException",
        "ServiceUnavailable",
    }
)
_INVALID_DOCUMENT_CODES = frozenset(
    {
        "BadDocumentException",
        "DocumentTooLargeException",
        "InvalidParameterException",
        "UnsupportedDocumentException",
    }
)


def _trace(
    *,
    subattempt: int,
    adapter: str,
    contract_version: str,
    outcome: ProviderAttemptOutcome,
    error_code: str | None = None,
    model_version: str | None = None,
) -> ProviderAttemptTrace:
    return ProviderAttemptTrace(
        provider_subattempt_number=subattempt,
        provider_adapter=adapter,
        provider_contract_version=contract_version,
        provider_model_version=model_version,
        outcome=outcome,
        error_code=error_code,
        response_complete=outcome is ProviderAttemptOutcome.SUCCEEDED,
        occurred_at=datetime.now(timezone.utc),
    )


def _complete_provider_result(
    document: ExtractedMedicalDocument,
    *,
    adapter: str,
    contract_version: str,
    model_version: str | None,
    traces: tuple[ProviderAttemptTrace, ...],
) -> ExtractionProviderResult:
    """Create a complete envelope after checked-in adapter validation."""

    evidence_pairs = {
        (item.provider_name, item.provider_api_version)
        for item in document.field_evidence
    }
    if len(evidence_pairs) > 1:
        raise ProviderResponseError("Extraction response failed provenance validation")
    if adapter == "aws_textract" and evidence_pairs:
        provider_name, evidence_version = next(iter(evidence_pairs))
        if provider_name != "aws_textract" or evidence_version != model_version:
            raise ProviderResponseError(
                "Extraction response failed provenance validation"
            )
    if adapter == "remote" and evidence_pairs:
        _, evidence_version = next(iter(evidence_pairs))
        model_version = evidence_version
        document.field_evidence = [
            item.model_copy(update={"provider_name": "remote"})
            for item in document.field_evidence
        ]
    if traces and traces[-1].outcome is ProviderAttemptOutcome.SUCCEEDED:
        traces = (
            *traces[:-1],
            replace(traces[-1], provider_model_version=model_version),
        )
    return ExtractionProviderResult(
        document=document,
        provider_adapter=adapter,
        provider_contract_version=contract_version,
        provider_model_version=model_version,
        response_complete=True,
        provider_attempt_traces=traces,
    )


class ExtractionProvider(ABC):
    """Configured Nexa extraction adapter; no general plugin trust is implied."""

    @property
    @abstractmethod
    def adapter_identity(self) -> str:
        """Return the closed Nexa-controlled adapter identity."""

    @property
    @abstractmethod
    def contract_version(self) -> str:
        """Return the Nexa-controlled adapter contract version."""

    @abstractmethod
    async def extract_bytes(
        self, document_bytes: bytes, *, mime_type: str, request_id: str
    ) -> ExtractionProviderResult:
        """Return validated extraction output without persisting it."""

    async def extract_data(self, file_path: str) -> ExtractionProviderResult:
        path = Path(file_path)
        try:
            data = await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            raise InvalidDocumentError("Document could not be read") from exc
        mime = (
            "application/pdf"
            if path.suffix.lower() == ".pdf"
            else "application/octet-stream"
        )
        return await self.extract_bytes(
            data, mime_type=mime, request_id="legacy-file-adapter"
        )


class RemoteExtractionProvider(ExtractionProvider):
    @property
    def adapter_identity(self) -> str:
        return "remote"

    @property
    def contract_version(self) -> str:
        return REMOTE_MEDICAL_DOCUMENT_CONTRACT_VERSION

    def __init__(
        self, config: DocumentExtractionConfig, client: httpx.AsyncClient | None = None
    ) -> None:
        if config.provider != "remote" or not config.api_url or not config.api_key:
            raise ValueError(
                "Remote extraction requires validated remote configuration"
            )
        self.config = config
        self._client = client

    async def extract_bytes(
        self, document_bytes: bytes, *, mime_type: str, request_id: str
    ) -> ExtractionProviderResult:
        started = time.perf_counter()
        last_error: DocumentExtractionError | None = None
        traces: list[ProviderAttemptTrace] = []
        for attempt in range(1, self.config.provider_max_attempts + 1):
            try:
                client = self._client or httpx.AsyncClient(
                    timeout=self.config.timeout_seconds
                )
                should_close = self._client is None
                try:
                    response = await client.post(
                        self.config.api_url,
                        headers={
                            "Authorization": f"Bearer {self.config.api_key}",
                            "X-Request-Id": request_id,
                        },
                        files={"document": ("document", document_bytes, mime_type)},
                    )
                finally:
                    if should_close:
                        await client.aclose()
                if (
                    response.status_code in {408, 425, 429}
                    or response.status_code >= 500
                ):
                    raise RetryableDocumentExtractionError(
                        "Extraction provider temporarily unavailable",
                        upstream_status=response.status_code,
                    )
                if response.status_code >= 400:
                    raise InvalidDocumentError(
                        "Extraction provider rejected document",
                        upstream_status=response.status_code,
                    )
                try:
                    result = ExtractedMedicalDocument.model_validate(response.json())
                except (ValueError, ValidationError) as exc:
                    traces.append(
                        _trace(
                            subattempt=attempt,
                            adapter="remote",
                            contract_version=REMOTE_MEDICAL_DOCUMENT_CONTRACT_VERSION,
                            outcome=ProviderAttemptOutcome.INVALID_RESPONSE,
                            error_code=ProviderResponseError.error_code,
                        )
                    )
                    raise ProviderResponseError(
                        "Extraction response failed schema validation",
                        provider_attempt_traces=tuple(traces),
                    ) from exc
                try:
                    result = _complete_provider_result(
                        result,
                        adapter="remote",
                        contract_version=REMOTE_MEDICAL_DOCUMENT_CONTRACT_VERSION,
                        model_version=None,
                        traces=tuple(
                            traces
                            + [
                                _trace(
                                    subattempt=attempt,
                                    adapter="remote",
                                    contract_version=REMOTE_MEDICAL_DOCUMENT_CONTRACT_VERSION,
                                    outcome=ProviderAttemptOutcome.SUCCEEDED,
                                )
                            ]
                        ),
                    )
                except ProviderResponseError as exc:
                    traces.append(
                        _trace(
                            subattempt=attempt,
                            adapter="remote",
                            contract_version=REMOTE_MEDICAL_DOCUMENT_CONTRACT_VERSION,
                            outcome=ProviderAttemptOutcome.INVALID_RESPONSE,
                            error_code=exc.error_code,
                        )
                    )
                    raise ProviderResponseError(
                        "Extraction response failed provenance validation",
                        provider_attempt_traces=tuple(traces),
                    ) from exc
                logger.info(
                    json.dumps(
                        {
                            "event": "document_extraction_succeeded",
                            "request_id": request_id,
                            "attempt": attempt,
                            "latency_ms": round((time.perf_counter() - started) * 1000),
                        }
                    )
                )
                return result
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = RetryableDocumentExtractionError(
                    "Extraction provider network failure"
                )
                last_error.__cause__ = exc
                traces.append(
                    _trace(
                        subattempt=attempt,
                        adapter="remote",
                        contract_version=REMOTE_MEDICAL_DOCUMENT_CONTRACT_VERSION,
                        outcome=(
                            ProviderAttemptOutcome.TIMEOUT
                            if isinstance(exc, httpx.TimeoutException)
                            else ProviderAttemptOutcome.RETRYABLE_ERROR
                        ),
                        error_code=last_error.error_code,
                    )
                )
            except RetryableDocumentExtractionError as exc:
                last_error = exc
                traces.append(
                    _trace(
                        subattempt=attempt,
                        adapter="remote",
                        contract_version=REMOTE_MEDICAL_DOCUMENT_CONTRACT_VERSION,
                        outcome=ProviderAttemptOutcome.RETRYABLE_ERROR,
                        error_code=last_error.error_code,
                    )
                )
            except DocumentExtractionError as exc:
                if exc.provider_attempt_traces:
                    raise
                outcome = (
                    ProviderAttemptOutcome.INVALID_DOCUMENT
                    if exc.error_code == InvalidDocumentError.error_code
                    else ProviderAttemptOutcome.CREDENTIALS_UNAVAILABLE
                    if exc.error_code == ProviderCredentialsUnavailableError.error_code
                    else ProviderAttemptOutcome.INVALID_RESPONSE
                )
                traces.append(
                    _trace(
                        subattempt=attempt,
                        adapter="remote",
                        contract_version=REMOTE_MEDICAL_DOCUMENT_CONTRACT_VERSION,
                        outcome=outcome,
                        error_code=exc.error_code,
                    )
                )
                raise type(exc)(
                    str(exc),
                    upstream_status=exc.upstream_status,
                    provider_attempt_traces=tuple(traces),
                ) from exc

            if attempt < self.config.provider_max_attempts:
                delay = min(4.0, 0.25 * (2 ** (attempt - 1))) + random.uniform(0, 0.2)
                await asyncio.sleep(delay)

        assert last_error is not None
        logger.warning(
            json.dumps(
                {
                    "event": "document_extraction_retry_exhausted",
                    "request_id": request_id,
                    "attempt_count": self.config.provider_max_attempts,
                    "error_code": last_error.error_code,
                    "upstream_status": last_error.upstream_status,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                }
            )
        )
        raise type(last_error)(
            str(last_error),
            upstream_status=last_error.upstream_status,
            provider_attempt_traces=tuple(traces),
        ) from last_error


class AwsTextractExtractionProvider(ExtractionProvider):
    """Synchronous single-page Textract Queries adapter."""

    @property
    def adapter_identity(self) -> str:
        return "aws_textract"

    @property
    def contract_version(self) -> str:
        return TEXTRACT_PILOT_QUERY_SET_VERSION

    def __init__(
        self,
        config: DocumentExtractionConfig,
        client: Any | None = None,
        *,
        successful_response_observer: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if config.provider != "aws_textract":
            raise ValueError("Textract extraction requires aws_textract configuration")
        self.config = config
        self._client = client
        self._successful_response_observer = successful_response_observer

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = boto3.client(
                "textract",
                region_name=self.config.aws_region,
                config=BotoConfig(
                    connect_timeout=self.config.timeout_seconds,
                    read_timeout=self.config.timeout_seconds,
                    retries={"max_attempts": 0},
                ),
            )
        return self._client

    @staticmethod
    def _parse_response(
        response: Any, *, require_single_page: bool = True
    ) -> ExtractedMedicalDocument:
        if not isinstance(response, dict):
            raise ProviderResponseError("Extraction response failed schema validation")
        blocks = response.get("Blocks")
        metadata = response.get("DocumentMetadata")
        if not isinstance(blocks, list) or not isinstance(metadata, dict):
            raise ProviderResponseError("Extraction response failed schema validation")
        if (
            not isinstance(metadata.get("Pages"), int)
            or metadata.get("Pages") <= 0
            or (require_single_page and metadata.get("Pages") != 1)
        ):
            raise InvalidDocumentError("Document must contain exactly one page")

        extracted_at = datetime.now(timezone.utc)
        model_version = response.get("AnalyzeDocumentModelVersion")
        if not isinstance(model_version, str) or not model_version.strip():
            model_version = "unknown"

        evidence = parse_textract_blocks(
            blocks,
            extracted_at=extracted_at,
            model_version=model_version,
            validated_document_page_count=metadata["Pages"],
        )
        identity = {
            field: next(
                (
                    item.raw_value
                    for item in evidence
                    if item.canonical_field_name == field
                ),
                "",
            )
            for field in _IDENTITY_FIELDS
        }

        return ExtractedMedicalDocument(
            patient_name=identity.get("patient_name", ""),
            phone=identity.get("phone", ""),
            aadhaar_abha_id=identity.get("aadhaar_abha_id", ""),
            # Compatibility summaries only; field_evidence is authoritative.
            diagnoses=[
                item.raw_value
                for item in evidence
                if item.canonical_field_name == "diagnosis"
            ],
            lab_results=[
                item.raw_value
                for item in evidence
                if item.source_type == "CELL"
                and item.canonical_field_name != "medication"
            ],
            prescriptions=[
                item.raw_value
                for item in evidence
                if item.canonical_field_name == "medication"
            ],
            extraction_confidence=None,
            field_evidence=evidence,
        )

    async def extract_bytes(
        self, document_bytes: bytes, *, mime_type: str, request_id: str
    ) -> ExtractionProviderResult:
        if mime_type not in TEXTRACT_SUPPORTED_MIME_TYPES:
            raise InvalidDocumentError("Document type is not supported")
        if not document_bytes or len(document_bytes) > TEXTRACT_MAX_SYNC_BYTES:
            raise InvalidDocumentError("Document size is not supported")

        started = time.perf_counter()
        last_error: DocumentExtractionError | None = None
        traces: list[ProviderAttemptTrace] = []
        for attempt in range(1, self.config.provider_max_attempts + 1):
            try:
                client = self._get_client()
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        client.analyze_document,
                        Document={"Bytes": document_bytes},
                        FeatureTypes=["QUERIES", "FORMS", "TABLES"],
                        QueriesConfig={
                            "Queries": [
                                {"Alias": alias, "Text": question, "Pages": ["1"]}
                                for alias, question in TEXTRACT_PILOT_QUERIES
                            ]
                        },
                    ),
                    timeout=self.config.timeout_seconds,
                )
                try:
                    result = self._parse_response(response)
                    model_version = response.get("AnalyzeDocumentModelVersion")
                    result = _complete_provider_result(
                        result,
                        adapter="aws_textract",
                        contract_version=TEXTRACT_PILOT_QUERY_SET_VERSION,
                        model_version=(
                            model_version.strip()
                            if isinstance(model_version, str) and model_version.strip()
                            else "unknown"
                        ),
                        traces=tuple(
                            traces
                            + [
                                _trace(
                                    subattempt=attempt,
                                    adapter="aws_textract",
                                    contract_version=TEXTRACT_PILOT_QUERY_SET_VERSION,
                                    model_version=(
                                        model_version.strip()
                                        if isinstance(model_version, str)
                                        and model_version.strip()
                                        else "unknown"
                                    ),
                                    outcome=ProviderAttemptOutcome.SUCCEEDED,
                                )
                            ]
                        ),
                    )
                except (ProviderResponseError, InvalidDocumentError) as exc:
                    traces.append(
                        _trace(
                            subattempt=attempt,
                            adapter="aws_textract",
                            contract_version=TEXTRACT_PILOT_QUERY_SET_VERSION,
                            outcome=(
                                ProviderAttemptOutcome.INVALID_DOCUMENT
                                if exc.error_code == InvalidDocumentError.error_code
                                else ProviderAttemptOutcome.INVALID_RESPONSE
                            ),
                            error_code=exc.error_code,
                        )
                    )
                    raise type(exc)(
                        "Extraction provider returned an invalid document"
                        if exc.error_code == InvalidDocumentError.error_code
                        else "Extraction response failed schema or provenance validation",
                        provider_attempt_traces=tuple(traces),
                    ) from exc
                if self._successful_response_observer is not None:
                    self._successful_response_observer(response)
                logger.info(
                    json.dumps(
                        {
                            "event": "document_extraction_succeeded",
                            "request_id": request_id,
                            "provider": "aws_textract",
                            "attempt": attempt,
                            "candidate_count": len(result.document.field_evidence),
                            "latency_ms": round((time.perf_counter() - started) * 1000),
                        }
                    )
                )
                return result
            except asyncio.TimeoutError as exc:
                last_error = ProviderTimeoutError("Extraction provider timed out")
                last_error.__cause__ = exc
                traces.append(
                    _trace(
                        subattempt=attempt,
                        adapter="aws_textract",
                        contract_version=TEXTRACT_PILOT_QUERY_SET_VERSION,
                        outcome=ProviderAttemptOutcome.TIMEOUT,
                        error_code=last_error.error_code,
                    )
                )
            except (NoCredentialsError, PartialCredentialsError) as exc:
                traces.append(
                    _trace(
                        subattempt=attempt,
                        adapter="aws_textract",
                        contract_version=TEXTRACT_PILOT_QUERY_SET_VERSION,
                        outcome=ProviderAttemptOutcome.CREDENTIALS_UNAVAILABLE,
                        error_code=ProviderCredentialsUnavailableError.error_code,
                    )
                )
                raise ProviderCredentialsUnavailableError(
                    "Extraction provider credentials are unavailable",
                    provider_attempt_traces=tuple(traces),
                ) from exc
            except ClientError as exc:
                code = str(exc.response.get("Error", {}).get("Code", ""))
                if code in _RETRYABLE_TEXTRACT_CODES:
                    last_error = (
                        ProviderThrottledError(
                            "Extraction provider temporarily throttled"
                        )
                        if "Throttl" in code or "Throughput" in code
                        else RetryableDocumentExtractionError(
                            "Extraction provider temporarily unavailable"
                        )
                    )
                    last_error.__cause__ = exc
                    traces.append(
                        _trace(
                            subattempt=attempt,
                            adapter="aws_textract",
                            contract_version=TEXTRACT_PILOT_QUERY_SET_VERSION,
                            outcome=(
                                ProviderAttemptOutcome.THROTTLED
                                if "Throttl" in code or "Throughput" in code
                                else ProviderAttemptOutcome.RETRYABLE_ERROR
                            ),
                            error_code=last_error.error_code,
                        )
                    )
                elif code in _INVALID_DOCUMENT_CODES:
                    traces.append(
                        _trace(
                            subattempt=attempt,
                            adapter="aws_textract",
                            contract_version=TEXTRACT_PILOT_QUERY_SET_VERSION,
                            outcome=ProviderAttemptOutcome.INVALID_DOCUMENT,
                            error_code=InvalidDocumentError.error_code,
                        )
                    )
                    raise InvalidDocumentError(
                        "Extraction provider rejected document",
                        provider_attempt_traces=tuple(traces),
                    ) from exc
                elif code in {"AccessDeniedException", "UnrecognizedClientException"}:
                    traces.append(
                        _trace(
                            subattempt=attempt,
                            adapter="aws_textract",
                            contract_version=TEXTRACT_PILOT_QUERY_SET_VERSION,
                            outcome=ProviderAttemptOutcome.CREDENTIALS_UNAVAILABLE,
                            error_code=ProviderCredentialsUnavailableError.error_code,
                        )
                    )
                    raise ProviderCredentialsUnavailableError(
                        "Extraction provider credentials are unavailable",
                        provider_attempt_traces=tuple(traces),
                    ) from exc
                else:
                    traces.append(
                        _trace(
                            subattempt=attempt,
                            adapter="aws_textract",
                            contract_version=TEXTRACT_PILOT_QUERY_SET_VERSION,
                            outcome=ProviderAttemptOutcome.INVALID_RESPONSE,
                            error_code=ProviderResponseError.error_code,
                        )
                    )
                    raise ProviderResponseError(
                        "Extraction provider request failed safely",
                        provider_attempt_traces=tuple(traces),
                    ) from exc
            except BotoCoreError as exc:
                last_error = RetryableDocumentExtractionError(
                    "Extraction provider temporarily unavailable"
                )
                last_error.__cause__ = exc
                traces.append(
                    _trace(
                        subattempt=attempt,
                        adapter="aws_textract",
                        contract_version=TEXTRACT_PILOT_QUERY_SET_VERSION,
                        outcome=ProviderAttemptOutcome.RETRYABLE_ERROR,
                        error_code=last_error.error_code,
                    )
                )
            except DocumentExtractionError:
                raise

            if attempt < self.config.provider_max_attempts:
                delay = min(4.0, 0.25 * (2 ** (attempt - 1))) + random.uniform(0, 0.2)
                await asyncio.sleep(delay)

        assert last_error is not None
        logger.warning(
            json.dumps(
                {
                    "event": "document_extraction_retry_exhausted",
                    "request_id": request_id,
                    "provider": "aws_textract",
                    "attempt_count": self.config.provider_max_attempts,
                    "error_code": last_error.error_code,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                }
            )
        )
        raise type(last_error)(
            str(last_error),
            upstream_status=last_error.upstream_status,
            provider_attempt_traces=tuple(traces),
        ) from last_error


class DemoExtractionProvider(ExtractionProvider):
    """Deterministic synthetic provider for explicit test/demo environments."""

    @property
    def adapter_identity(self) -> str:
        return "demo"

    @property
    def contract_version(self) -> str:
        return DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION

    async def extract_bytes(
        self, document_bytes: bytes, *, mime_type: str, request_id: str
    ) -> ExtractionProviderResult:
        _ = (document_bytes, mime_type, request_id)
        result = ExtractedMedicalDocument(
            patient_name="Synthetic Patient",
            aadhaar_abha_id="SYNTHETIC-ID",
            phone="0000000000",
            diagnoses=[],
            lab_results=[],
            prescriptions=[],
            extraction_confidence=0.50,
        )
        return _complete_provider_result(
            result,
            adapter="demo",
            contract_version=DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION,
            model_version=None,
            traces=(),
        )


# Compatibility name for callers/tests that explicitly construct the remote adapter.
MedicalDocumentExtractor = RemoteExtractionProvider

_extractor_singleton: ExtractionProvider | None = None
_extractor_fingerprint: tuple[Any, ...] | None = None


def get_medical_document_extractor(
    config: DocumentExtractionConfig | None = None,
) -> ExtractionProvider:
    global _extractor_singleton, _extractor_fingerprint
    config = config or get_document_extraction_config()
    fingerprint = (
        config.provider,
        config.environment,
        config.api_url,
        config.aws_region,
        config.timeout_seconds,
        config.provider_max_attempts,
        config.job_max_attempts,
    )
    if _extractor_singleton is None or _extractor_fingerprint != fingerprint:
        if config.provider == "demo":
            _extractor_singleton = DemoExtractionProvider()
        elif config.provider == "aws_textract":
            _extractor_singleton = AwsTextractExtractionProvider(config)
        else:
            _extractor_singleton = RemoteExtractionProvider(config)
        _extractor_fingerprint = fingerprint
    return _extractor_singleton
