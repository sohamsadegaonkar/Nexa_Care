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
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any

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
from app.models.ai_models import ExtractedMedicalDocument

logger = logging.getLogger("nexa_logger")


class DocumentExtractionError(RuntimeError):
    """Sanitized provider failure safe to persist on a job."""

    error_code = "EXTRACTION_FAILED"
    retryable = False

    def __init__(self, message: str, *, upstream_status: int | None = None) -> None:
        super().__init__(message)
        self.upstream_status = upstream_status


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


class ExtractionProvider(ABC):
    @abstractmethod
    async def extract_bytes(
        self, document_bytes: bytes, *, mime_type: str, request_id: str
    ) -> ExtractedMedicalDocument:
        """Return validated extraction output without persisting it."""

    async def extract_data(self, file_path: str) -> ExtractedMedicalDocument:
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
    ) -> ExtractedMedicalDocument:
        started = time.perf_counter()
        last_error: DocumentExtractionError | None = None
        for attempt in range(1, self.config.max_attempts + 1):
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
                    raise InvalidDocumentError(
                        "Extraction response failed schema validation"
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
            except RetryableDocumentExtractionError as exc:
                last_error = exc
            except DocumentExtractionError:
                raise

            if attempt < self.config.max_attempts:
                delay = min(4.0, 0.25 * (2 ** (attempt - 1))) + random.uniform(0, 0.2)
                await asyncio.sleep(delay)

        assert last_error is not None
        logger.warning(
            json.dumps(
                {
                    "event": "document_extraction_retry_exhausted",
                    "request_id": request_id,
                    "attempt_count": self.config.max_attempts,
                    "error_code": last_error.error_code,
                    "upstream_status": last_error.upstream_status,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                }
            )
        )
        raise last_error


class AwsTextractExtractionProvider(ExtractionProvider):
    """Synchronous single-page Textract Queries adapter."""

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
    def _parse_response(response: Any) -> ExtractedMedicalDocument:
        if not isinstance(response, dict):
            raise ProviderResponseError("Extraction response failed schema validation")
        blocks = response.get("Blocks")
        metadata = response.get("DocumentMetadata")
        if not isinstance(blocks, list) or not isinstance(metadata, dict):
            raise ProviderResponseError("Extraction response failed schema validation")
        if metadata.get("Pages") != 1:
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
    ) -> ExtractedMedicalDocument:
        if mime_type not in TEXTRACT_SUPPORTED_MIME_TYPES:
            raise InvalidDocumentError("Document type is not supported")
        if not document_bytes or len(document_bytes) > TEXTRACT_MAX_SYNC_BYTES:
            raise InvalidDocumentError("Document size is not supported")

        started = time.perf_counter()
        last_error: DocumentExtractionError | None = None
        for attempt in range(1, self.config.max_attempts + 1):
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
                result = self._parse_response(response)
                if self._successful_response_observer is not None:
                    self._successful_response_observer(response)
                logger.info(
                    json.dumps(
                        {
                            "event": "document_extraction_succeeded",
                            "request_id": request_id,
                            "provider": "aws_textract",
                            "attempt": attempt,
                            "candidate_count": len(result.field_evidence),
                            "latency_ms": round((time.perf_counter() - started) * 1000),
                        }
                    )
                )
                return result
            except asyncio.TimeoutError as exc:
                last_error = ProviderTimeoutError("Extraction provider timed out")
                last_error.__cause__ = exc
            except (NoCredentialsError, PartialCredentialsError) as exc:
                raise ProviderCredentialsUnavailableError(
                    "Extraction provider credentials are unavailable"
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
                elif code in _INVALID_DOCUMENT_CODES:
                    raise InvalidDocumentError(
                        "Extraction provider rejected document"
                    ) from exc
                elif code in {"AccessDeniedException", "UnrecognizedClientException"}:
                    raise ProviderCredentialsUnavailableError(
                        "Extraction provider credentials are unavailable"
                    ) from exc
                else:
                    raise ProviderResponseError(
                        "Extraction provider request failed safely"
                    ) from exc
            except BotoCoreError as exc:
                last_error = RetryableDocumentExtractionError(
                    "Extraction provider temporarily unavailable"
                )
                last_error.__cause__ = exc
            except DocumentExtractionError:
                raise

            if attempt < self.config.max_attempts:
                delay = min(4.0, 0.25 * (2 ** (attempt - 1))) + random.uniform(0, 0.2)
                await asyncio.sleep(delay)

        assert last_error is not None
        logger.warning(
            json.dumps(
                {
                    "event": "document_extraction_retry_exhausted",
                    "request_id": request_id,
                    "provider": "aws_textract",
                    "attempt_count": self.config.max_attempts,
                    "error_code": last_error.error_code,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                }
            )
        )
        raise last_error


class DemoExtractionProvider(ExtractionProvider):
    """Deterministic synthetic provider for explicit test/demo environments."""

    async def extract_bytes(
        self, document_bytes: bytes, *, mime_type: str, request_id: str
    ) -> ExtractedMedicalDocument:
        _ = (document_bytes, mime_type, request_id)
        return ExtractedMedicalDocument(
            patient_name="Synthetic Patient",
            aadhaar_abha_id="SYNTHETIC-ID",
            phone="0000000000",
            diagnoses=[],
            lab_results=[],
            prescriptions=[],
            extraction_confidence=0.50,
        )


# Compatibility name for callers/tests that explicitly construct the remote adapter.
MedicalDocumentExtractor = RemoteExtractionProvider

_extractor_singleton: ExtractionProvider | None = None
_extractor_fingerprint: tuple[Any, ...] | None = None


def get_medical_document_extractor() -> ExtractionProvider:
    global _extractor_singleton, _extractor_fingerprint
    config = get_document_extraction_config()
    fingerprint = (
        config.provider,
        config.environment,
        config.api_url,
        config.aws_region,
        config.timeout_seconds,
        config.max_attempts,
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
