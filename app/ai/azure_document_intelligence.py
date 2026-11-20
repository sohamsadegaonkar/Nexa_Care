"""Managed-identity Azure Layout candidate: one synthetic form page only."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from importlib import import_module
from io import BytesIO
from typing import Any

from PIL import Image
from pypdf import PdfReader

from app.ai.extraction_normalization import normalize_extracted_value
from app.ai.extractor import (
    ExtractionProvider,
    ExtractionProviderResult,
    InvalidDocumentError,
    ProviderResponseError,
    _complete_provider_result,
    _trace,
)
from app.core.config import DocumentExtractionConfig, get_document_extraction_config
from app.models.ai_models import (
    ExtractedMedicalDocument,
    ProviderAttemptOutcome,
    ProviderFieldEvidence,
)
from app.models.field_evidence import NormalizedBoundingBox

_PROVIDER = "azure_document_intelligence"
_API = "2024-11-30"
_MODEL = "prebuilt-layout"
_CONTRACT = "azure-layout-form/1.0"
_MODEL_ID = _API + "/" + _MODEL
_MAX_BYTES = 2 * 1024 * 1024
_LABELS = {
    "patient name": "patient_name",
    "name of patient": "patient_name",
    "patient phone": "phone",
    "patient mobile": "phone",
    "abha id": "aadhaar_abha_id",
    "hba1c": "hba1c",
    "blood glucose": "blood_glucose",
    "blood pressure": "blood_pressure",
    "heart rate": "heart_rate",
    "diagnosis": "diagnosis",
    "provisional diagnosis": "diagnosis",
    "medication": "medication",
}
_IDENTITY = frozenset({"patient_name", "phone", "aadhaar_abha_id"})


def _single_page(data: bytes, mime: str) -> None:
    if not data or len(data) > _MAX_BYTES:
        raise InvalidDocumentError("Azure demo accepts documents up to 2 MiB")
    try:
        if mime == "application/pdf":
            reader = PdfReader(BytesIO(data), strict=True)
            if reader.is_encrypted or len(reader.pages) != 1:
                raise ValueError("Unsupported PDF")
        elif mime in {"image/png", "image/jpeg"}:
            with Image.open(BytesIO(data)) as image:
                if image.format != {"image/png": "PNG", "image/jpeg": "JPEG"}[mime]:
                    raise ValueError("Image type mismatch")
                if getattr(image, "n_frames", 1) != 1 or image.width * image.height > 16_000_000:
                    raise ValueError("Unsupported image")
                image.verify()
        else:
            raise ValueError("Unsupported type")
    except Exception:  # noqa: BLE001
        raise InvalidDocumentError("Azure demo requires one valid PDF or image page") from None


def _source(element: Any, text: str) -> tuple[str, tuple[tuple[int, int], ...]]:
    value = getattr(element, "content", None)
    if not isinstance(value, str) or not value.strip():
        raise ProviderResponseError("Missing Azure form content")
    ranges: list[tuple[int, int]] = []
    for span in getattr(element, "spans", None) or []:
        offset, length = span.offset, span.length
        if type(offset) is not int or type(length) is not int or (
            offset < 0 or length <= 0 or offset + length > len(text)
        ):
            raise ProviderResponseError("Invalid Azure source span")
        ranges.append((offset, length))
    if len(ranges) != 1:
        raise ProviderResponseError("Azure form content lacks exact source span")
    offset, length = ranges[0]
    if text[offset:offset + length] != value:
        raise ProviderResponseError("Azure form content does not match source span")
    return value, tuple(ranges)


def _geometry(elements: tuple[Any, Any], page: Any) -> NormalizedBoundingBox | None:
    width, height = getattr(page, "width", None), getattr(page, "height", None)
    if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
        return None
    if not math.isfinite(width) or not math.isfinite(height) or min(width, height) <= 0:
        return None
    points: list[tuple[float, float]] = []
    for element in elements:
        regions = getattr(element, "bounding_regions", None) or []
        if len(regions) != 1 or regions[0].page_number != 1:
            return None
        polygon = regions[0].polygon
        if not isinstance(polygon, list) or len(polygon) != 8:
            return None
        if any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in polygon):
            return None
        points.extend((polygon[i] / width, polygon[i + 1] / height) for i in range(0, 8, 2))
    try:
        return NormalizedBoundingBox(
            left=min(x for x, _ in points), top=min(y for _, y in points),
            right=max(x for x, _ in points), bottom=max(y for _, y in points),
        )
    except ValueError:
        return None


def _parse(result: Any) -> ExtractedMedicalDocument:
    pages = getattr(result, "pages", None)
    text = getattr(result, "content", None)
    pairs = getattr(result, "key_value_pairs", None)
    if (
        getattr(result, "api_version", None) != _API
        or getattr(result, "model_id", None) != _MODEL
        or not isinstance(pages, list) or len(pages) != 1
        or pages[0].page_number != 1
        or not isinstance(text, str) or not text.strip() or len(text) > 100_000
        or not isinstance(pairs, list) or len(pairs) > 500
    ):
        raise ProviderResponseError("Azure response failed source/schema validation")
    evidence = []
    seen = set()
    for pair in pairs:
        key = getattr(pair, "key", None)
        value = getattr(pair, "value", None)
        label = getattr(key, "content", None)
        if not isinstance(label, str):
            raise ProviderResponseError("Invalid Azure form key")
        field = _LABELS.get(re.sub(r"[^a-z0-9]+", " ", label.casefold()).strip())
        if not field or value is None:
            continue
        raw_key, key_ranges = _source(key, text)
        raw_value, value_ranges = _source(value, text)
        if len(raw_value) > 4096:
            raise ProviderResponseError("Azure form value exceeds review boundary")
        identity = (field, key_ranges, value_ranges)
        if identity in seen:
            continue
        seen.add(identity)
        normalized = normalize_extracted_value(field, raw_value)
        box = _geometry((key, value), pages[0])
        digest = None
        if field not in _IDENTITY:
            digest = hashlib.sha256(
                json.dumps([field, raw_key, raw_value, key_ranges, value_ranges],
                           ensure_ascii=True, separators=(",", ":")).encode()
            ).hexdigest()
        evidence.append(ProviderFieldEvidence(
            canonical_field_name=field,
            raw_value=raw_value,
            source_text=raw_value,
            page_number=0,
            bounding_box=box,
            field_confidence=None,
            provider_name=_PROVIDER,
            provider_api_version=_MODEL_ID,
            extraction_timestamp=datetime.now(timezone.utc),
            evidence_hash=digest,
            source_type="KEY_VALUE_SET",
            normalized_value=normalized.value,
            raw_unit=normalized.raw_unit,
            normalized_unit=normalized.unit,
            incomplete=True,
        ))
    if not evidence:
        raise ProviderResponseError("No supported exact-label Azure form evidence")
    identity_values = {
        name: next((item.raw_value for item in evidence if item.canonical_field_name == name), "")
        for name in _IDENTITY
    }
    return ExtractedMedicalDocument(
        patient_name=identity_values.get("patient_name", ""),
        phone=identity_values.get("phone", ""),
        aadhaar_abha_id=identity_values.get("aadhaar_abha_id", ""),
        diagnoses=[], lab_results=[], prescriptions=[],
        extraction_confidence=None, field_evidence=evidence,
    )


class AzureDocumentIntelligenceProvider(ExtractionProvider):
    @property
    def adapter_identity(self) -> str:
        return _PROVIDER

    @property
    def contract_version(self) -> str:
        return _CONTRACT

    def __init__(self, config: DocumentExtractionConfig) -> None:
        validated = get_document_extraction_config()
        if (
            validated.provider != _PROVIDER or config != validated
            or not config.azure_endpoint or not config.azure_identity_client_id
        ):
            raise ValueError("Explicit validated synthetic Azure extraction config required")
        self.config = config

    def _analyze(self, data: bytes) -> Any:
        try:
            identity = import_module("azure.identity").ManagedIdentityCredential
            client_type = import_module("azure.ai.documentintelligence").DocumentIntelligenceClient
            request = import_module("azure.ai.documentintelligence.models").AnalyzeDocumentRequest
            with identity(client_id=self.config.azure_identity_client_id) as credential:
                with client_type(
                    endpoint=self.config.azure_endpoint, credential=credential,
                    api_version=_API, logging_enable=False, retry_total=0,
                    connection_timeout=10, read_timeout=min(30, self.config.timeout_seconds),
                ) as client:
                    poller = client.begin_analyze_document(
                        _MODEL, body=request(bytes_source=data),
                        features=["keyValuePairs"], string_index_type="unicodeCodePoint",
                        logging_enable=False,
                    )
                    return poller.result(timeout=self.config.timeout_seconds)
        except Exception:  # noqa: BLE001
            raise ProviderResponseError("Azure extraction unavailable or incomplete") from None

    async def extract_bytes(
        self, document_bytes: bytes, *, mime_type: str, request_id: str
    ) -> ExtractionProviderResult:
        del request_id
        _single_page(document_bytes, mime_type)
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(self._analyze, document_bytes),
                timeout=self.config.timeout_seconds + 5,
            )
        except TimeoutError:
            raise ProviderResponseError("Azure extraction did not complete within deadline") from None
        document = _parse(response)
        return _complete_provider_result(
            document, adapter=_PROVIDER, contract_version=_CONTRACT,
            model_version=_MODEL_ID, traces=(
                _trace(subattempt=1, adapter=_PROVIDER, contract_version=_CONTRACT,
                       outcome=ProviderAttemptOutcome.SUCCEEDED, model_version=_MODEL_ID),
            ),
        )
