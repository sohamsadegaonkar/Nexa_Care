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
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

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
        config.max_attempts,
    )
    if _extractor_singleton is None or _extractor_fingerprint != fingerprint:
        _extractor_singleton = (
            DemoExtractionProvider()
            if config.provider == "demo"
            else RemoteExtractionProvider(config)
        )
        _extractor_fingerprint = fingerprint
    return _extractor_singleton
