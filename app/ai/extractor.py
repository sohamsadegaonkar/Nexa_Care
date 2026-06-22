"""Medical document extraction wrapper for Nexa Care.

The extractor isolates Hugging Face / PyTorch concerns from API routes and from
persistence. It returns a strict Pydantic model, never raw model output, so the
rest of the platform only handles typed extraction data.
"""

from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import torch
from pdf2image import convert_from_path
from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError
from transformers import pipeline

from app.models.ai_models import ExtractedMedicalDocument

logger = logging.getLogger("nexa_logger")

_DEFAULT_MODEL_NAME = "naver-clova-ix/donut-base-finetuned-cord-v2"
_NEXT_FIELD_LOOKAHEAD = (
    r"(?=\s+(?:abha|aadhaar|phone|mobile|diagnosis|diagnoses|impression|"
    r"clinical impression|lab results?|investigations?|test results?|"
    r"prescriptions?|medications?|rx)\b|$)"
)
_SECTION_PATTERNS = {
    "diagnoses": re.compile(
        r"(?:diagnosis|diagnoses|impression|clinical impression)\s*[:\-]\s*(.+?)"
        + _NEXT_FIELD_LOOKAHEAD,
        re.IGNORECASE,
    ),
    "lab_results": re.compile(
        r"(?:lab results?|investigations?|test results?)\s*[:\-]\s*(.+?)"
        + _NEXT_FIELD_LOOKAHEAD,
        re.IGNORECASE,
    ),
    "prescriptions": re.compile(
        r"(?:prescriptions?|medications?|rx)\s*[:\-]\s*(.+?)" + _NEXT_FIELD_LOOKAHEAD,
        re.IGNORECASE,
    ),
}


class DocumentExtractionError(RuntimeError):
    """Raised when a document cannot be converted or processed by the model."""


def _split_items(value: str) -> list[str]:
    """Split section text into compact list items without logging content."""

    items = re.split(r"[,;\n\u2022]+", value)
    return [item.strip(" .\t") for item in items if item.strip(" .\t")]


class MedicalDocumentExtractor:
    """Hugging Face-backed medical document extractor.

    The model is loaded on CUDA when available and otherwise on CPU. This makes
    the same code usable in local CPU environments and future GPU deployments.
    """

    def __init__(self, model_name: str | None = None) -> None:
        """Load the configured image-to-text model once for this extractor."""

        self.model_name = model_name or os.getenv("DOCUMENT_AI_MODEL", _DEFAULT_MODEL_NAME)
        self.device = 0 if torch.cuda.is_available() else -1
        self.device_label = "cuda" if self.device == 0 else "cpu"
        logger.info(json.dumps({
            "event": "medical_document_extractor_initializing",
            "model_name": self.model_name,
            "device": self.device_label,
        }))
        self._pipeline = pipeline(
            task="image-to-text",
            model=self.model_name,
            device=self.device,
        )

    def extract_data(self, file_path: str) -> ExtractedMedicalDocument:
        """Extract structured medical data from a PDF or image file.

        PDF files are converted from the first page only. Images are opened via
        PIL. Raw text and extracted PII are never logged; failures log only
        structured operational metadata.
        """

        try:
            image = self._load_first_page_image(file_path)
            try:
                model_output = self._pipeline(image)
            finally:
                image.close()
            extracted_text = self._coerce_model_output_to_text(model_output)
            parsed = self._parse_medical_text(extracted_text)
            return ExtractedMedicalDocument(**parsed)
        except UnidentifiedImageError as exc:
            logger.critical(json.dumps({
                "event": "document_extraction_unidentified_image",
                "file_suffix": Path(file_path).suffix.lower(),
                "exception_type": type(exc).__name__,
            }))
            raise DocumentExtractionError("Uploaded document is not a readable image.") from exc
        except RuntimeError as exc:
            if self._is_oom_error(exc):
                logger.critical(json.dumps({
                    "event": "document_extraction_oom",
                    "device": self.device_label,
                    "exception_type": type(exc).__name__,
                }))
                raise DocumentExtractionError("Document extraction exceeded available memory.") from exc
            raise
        except ValidationError as exc:
            logger.critical(json.dumps({
                "event": "document_extraction_validation_failed",
                "error_count": len(exc.errors()),
            }))
            raise DocumentExtractionError("Extracted document data failed validation.") from exc

    def _load_first_page_image(self, file_path: str) -> Image.Image:
        suffix = Path(file_path).suffix.lower()
        if suffix == ".pdf":
            pages = convert_from_path(file_path, first_page=1, last_page=1)
            if not pages:
                raise DocumentExtractionError("PDF did not contain a readable first page.")
            return pages[0].convert("RGB")

        with Image.open(file_path) as image:
            return image.convert("RGB")

    @staticmethod
    def _coerce_model_output_to_text(model_output: Any) -> str:
        if isinstance(model_output, str):
            return model_output
        if isinstance(model_output, list):
            chunks: list[str] = []
            for item in model_output:
                if isinstance(item, dict):
                    for key in ("generated_text", "answer", "text"):
                        value = item.get(key)
                        if isinstance(value, str):
                            chunks.append(value)
                            break
                elif isinstance(item, str):
                    chunks.append(item)
            return "\n".join(chunks)
        if isinstance(model_output, dict):
            for key in ("generated_text", "answer", "text"):
                value = model_output.get(key)
                if isinstance(value, str):
                    return value
        return str(model_output or "")

    @staticmethod
    def _parse_medical_text(text: str) -> dict[str, object]:
        normalized = text.replace("<s>", " ").replace("</s>", " ")
        normalized = re.sub(r"<[^>]+>", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()

        patient_name = ""
        name_match = re.search(
            r"(?:patient\s*name|name)\s*[:\-]\s*([A-Za-z][A-Za-z .]{1,80}?)"
            + _NEXT_FIELD_LOOKAHEAD,
            normalized,
            re.IGNORECASE,
        )
        if name_match:
            patient_name = name_match.group(1).strip(" .")

        aadhaar_abha_id = ""
        abha_match = re.search(
            r"(?:abha|aadhaar|aadhaar\s*/\s*abha)\s*(?:id|number|no)?\s*[:\-]?\s*([0-9][0-9\- ]{8,24}[0-9])",
            normalized,
            re.IGNORECASE,
        )
        if abha_match:
            aadhaar_abha_id = re.sub(r"\s+", "", abha_match.group(1).strip())

        phone = ""
        phone_match = re.search(r"(?:\+91[\-\s]?)?[6-9]\d{9}\b", normalized)
        if phone_match:
            phone = re.sub(r"[\s\-]", "", phone_match.group(0))

        section_values: dict[str, list[str]] = {
            "diagnoses": [],
            "lab_results": [],
            "prescriptions": [],
        }
        for field_name, pattern in _SECTION_PATTERNS.items():
            match = pattern.search(normalized)
            if match:
                section_values[field_name] = _split_items(match.group(1))

        return {
            "patient_name": patient_name,
            "aadhaar_abha_id": aadhaar_abha_id,
            "phone": phone,
            **section_values,
        }

    @staticmethod
    def _is_oom_error(exc: RuntimeError) -> bool:
        if isinstance(exc, torch.cuda.OutOfMemoryError):
            return True
        message = str(exc).lower()
        return "out of memory" in message or "cuda oom" in message


@lru_cache(maxsize=1)
def get_medical_document_extractor() -> MedicalDocumentExtractor:
    """Return the process-wide extractor singleton, loading the model lazily."""

    return MedicalDocumentExtractor()
