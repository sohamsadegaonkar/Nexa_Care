"""Remote medical document extraction client for Nexa Care.

The extractor intentionally contains no local PyTorch or Transformer imports.
It prepares uploads for a hosted Vision-Language Model API and validates the
remote output through ``ExtractedMedicalDocument`` before the pipeline can act
on it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from pydantic import ValidationError

from app.models.ai_models import ExtractedMedicalDocument

logger = logging.getLogger("nexa_logger")


class DocumentExtractionError(RuntimeError):
    """Raised when remote document extraction cannot produce valid data."""


class MedicalDocumentExtractor:
    """HTTP-client wrapper for hosted medical document extraction.

    The current MVP uses a high-confidence mock when ``DOCUMENT_AI_API_KEY`` is
    absent. When the key is configured, this class is the boundary where the
    hosted VLM request/response implementation belongs. Raw document bytes and
    extracted PII are never logged.
    """

    def __init__(self, api_key: str | None = None, api_url: str | None = None) -> None:
        """Configure the remote extraction client without loading local ML."""

        self.api_key = api_key or os.getenv("DOCUMENT_AI_API_KEY")
        self.api_url = api_url or os.getenv("DOCUMENT_AI_API_URL")
        logger.info(json.dumps({
            "event": "medical_document_extractor_configured",
            "mode": "remote_api" if self.api_key else "mock_fallback",
            "api_url_configured": bool(self.api_url),
        }))

    async def extract_data(self, file_path: str) -> ExtractedMedicalDocument:
        """Extract structured medical data using a hosted VLM API.

        The file is read locally only to prepare the outbound API request. If no
        API key is configured, a deterministic two-second mock response is
        returned for development and CI. The method logs operational metadata
        only; it never logs file contents or extracted PII.
        """

        path = Path(file_path)
        try:
            document_bytes = path.read_bytes()
        except OSError as exc:
            logger.critical(json.dumps({
                "event": "document_extraction_file_read_failed",
                "file_suffix": path.suffix.lower(),
                "exception_type": type(exc).__name__,
            }))
            raise DocumentExtractionError("Uploaded document could not be read.") from exc

        try:
            if not self.api_key:
                await asyncio.sleep(2)
                return self._mock_extraction_result()

            payload = await self._call_remote_vlm_api(
                document_bytes=document_bytes,
                file_suffix=path.suffix.lower(),
            )
            return ExtractedMedicalDocument.model_validate(payload)
        except ValidationError as exc:
            logger.critical(json.dumps({
                "event": "document_extraction_validation_failed",
                "error_count": len(exc.errors()),
            }))
            raise DocumentExtractionError("Extracted document data failed validation.") from exc
        finally:
            # Explicitly release the buffer before the pipeline cleanup removes
            # the temp file. No document bytes are logged or retained here.
            del document_bytes

    async def _call_remote_vlm_api(
        self,
        *,
        document_bytes: bytes,
        file_suffix: str,
    ) -> dict[str, object]:
        """Placeholder for the hosted VLM HTTP call.

        A future implementation can use an async HTTP client here. The method is
        deliberately isolated so credentials and raw bytes do not leak into the
        rest of the application.
        """

        _ = (document_bytes, file_suffix)
        raise DocumentExtractionError("DOCUMENT_AI_API_URL integration is not implemented yet.")

    @staticmethod
    def _mock_extraction_result() -> ExtractedMedicalDocument:
        """Return realistic fake data for local development without a VLM key."""

        return ExtractedMedicalDocument(
            patient_name="Asha Raman",
            aadhaar_abha_id="12-3456-7890-1234",
            phone="9876543210",
            diagnoses=["Type 2 Diabetes Mellitus", "Hypertension"],
            lab_results=["HbA1c 7.2%", "Blood pressure 142/90 mmHg"],
            prescriptions=["Metformin 500mg twice daily", "Telmisartan 40mg once daily"],
            extraction_confidence=0.96,
        )


_extractor_singleton: MedicalDocumentExtractor | None = None


def get_medical_document_extractor() -> MedicalDocumentExtractor:
    """Return a lightweight process-wide extractor client."""

    global _extractor_singleton
    if _extractor_singleton is None:
        _extractor_singleton = MedicalDocumentExtractor()
    return _extractor_singleton
