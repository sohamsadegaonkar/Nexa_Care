"""Fail-closed source-safety boundary for patient external-record imports.

This module decides whether retained patient-supplied bytes are structurally
safe enough to hand to an extraction provider. It does not decide clinical
truth and it does not grant provider/treatment authority.

Production malware scanning is never faked. The executable scanner is selected
through a closed server-owned configuration. An explicit unavailable mode
remains fail-closed for non-production development/test contexts; production-
like startup requires a ready scanner before traffic is accepted.
"""

from __future__ import annotations

import hashlib
import io
import os
import warnings
from dataclasses import dataclass

from PIL import Image
from pypdf import PdfReader

from app.ai.extractor import TEXTRACT_MAX_SYNC_BYTES
from app.core.config import ConfigError, get_document_extraction_config
from app.core.production_runtime import MAX_UPLOAD_BYTES_HARD_LIMIT
from app.security.patient_source_malware_scanner import (
    MalwareScanOutcome,
    MalwareScanResult,
    PatientSourceMalwareScanner,
    UnavailablePatientSourceMalwareScanner,
    get_patient_source_malware_scanner,
)

_DEFAULT_MAX_PDF_PAGES = 500
_DEFAULT_MAX_PDF_PAGE_POINTS = 14_400
_DEFAULT_MAX_IMAGE_PIXELS = 25_000_000
_DEFAULT_MAX_IMAGE_DIMENSION = 10_000


class PatientSourceSafetyError(RuntimeError):
    """Stable value-free source-safety failure."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class PatientSourceSafetyDecision:
    content_hash: str
    mime_type: str
    page_count: int | None = None
    width: int | None = None
    height: int | None = None


def _bounded_positive_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise PatientSourceSafetyError(
            "SOURCE_SAFETY_CONFIGURATION_UNAVAILABLE",
            retryable=True,
        ) from exc
    if value <= 0:
        raise PatientSourceSafetyError(
            "SOURCE_SAFETY_CONFIGURATION_UNAVAILABLE",
            retryable=True,
        )
    return value


def effective_patient_source_max_bytes() -> int:
    """Return the current patient-import byte ceiling used before extraction."""
    configured = _bounded_positive_env(
        "MAX_UPLOAD_BYTES",
        MAX_UPLOAD_BYTES_HARD_LIMIT,
    )
    if configured > MAX_UPLOAD_BYTES_HARD_LIMIT:
        raise PatientSourceSafetyError(
            "SOURCE_SAFETY_CONFIGURATION_UNAVAILABLE",
            retryable=True,
        )
    try:
        extraction_config = get_document_extraction_config()
    except ConfigError as exc:
        raise PatientSourceSafetyError(
            "SOURCE_SAFETY_CONFIGURATION_UNAVAILABLE",
            retryable=True,
        ) from exc
    return (
        min(configured, TEXTRACT_MAX_SYNC_BYTES)
        if extraction_config.provider == "aws_textract"
        else configured
    )


def _validate_pdf(data: bytes) -> PatientSourceSafetyDecision:
    max_pages = _bounded_positive_env(
        "PATIENT_SOURCE_MAX_PDF_PAGES",
        _DEFAULT_MAX_PDF_PAGES,
    )
    max_page_points = _bounded_positive_env(
        "PATIENT_SOURCE_MAX_PDF_PAGE_POINTS",
        _DEFAULT_MAX_PDF_PAGE_POINTS,
    )
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise PatientSourceSafetyError(
                "SOURCE_PDF_ENCRYPTED",
                retryable=False,
            )
        page_count = len(reader.pages)
        if not 1 <= page_count <= max_pages:
            raise PatientSourceSafetyError(
                "SOURCE_PDF_PAGE_LIMIT_EXCEEDED",
                retryable=False,
            )
        for page in reader.pages:
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)
            if (
                width <= 0
                or height <= 0
                or width > max_page_points
                or height > max_page_points
            ):
                raise PatientSourceSafetyError(
                    "SOURCE_PDF_PAGE_DIMENSIONS_INVALID",
                    retryable=False,
                )
    except PatientSourceSafetyError:
        raise
    except Exception as exc:
        raise PatientSourceSafetyError(
            "SOURCE_PDF_DECODER_REJECTED",
            retryable=False,
        ) from exc

    return PatientSourceSafetyDecision(
        content_hash=hashlib.sha256(data).hexdigest(),
        mime_type="application/pdf",
        page_count=page_count,
    )


def _validate_image(data: bytes, *, mime_type: str) -> PatientSourceSafetyDecision:
    max_pixels = _bounded_positive_env(
        "PATIENT_SOURCE_MAX_IMAGE_PIXELS",
        _DEFAULT_MAX_IMAGE_PIXELS,
    )
    max_dimension = _bounded_positive_env(
        "PATIENT_SOURCE_MAX_IMAGE_DIMENSION",
        _DEFAULT_MAX_IMAGE_DIMENSION,
    )
    expected_format = {
        "image/png": "PNG",
        "image/jpeg": "JPEG",
    }.get(mime_type)
    if expected_format is None:
        raise PatientSourceSafetyError(
            "SOURCE_TYPE_UNSUPPORTED",
            retryable=False,
        )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != expected_format:
                    raise PatientSourceSafetyError(
                        "SOURCE_IMAGE_FORMAT_MISMATCH",
                        retryable=False,
                    )
                width, height = image.size
                if (
                    width <= 0
                    or height <= 0
                    or width > max_dimension
                    or height > max_dimension
                    or width * height > max_pixels
                ):
                    raise PatientSourceSafetyError(
                        "SOURCE_IMAGE_DIMENSIONS_EXCEEDED",
                        retryable=False,
                    )
                image.verify()

            # verify() intentionally invalidates the decoder state. Re-open and
            # force a full decode so truncated/corrupt pixel data cannot pass.
            with Image.open(io.BytesIO(data)) as decoded:
                if decoded.format != expected_format:
                    raise PatientSourceSafetyError(
                        "SOURCE_IMAGE_FORMAT_MISMATCH",
                        retryable=False,
                    )
                decoded.load()
    except PatientSourceSafetyError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise PatientSourceSafetyError(
            "SOURCE_IMAGE_DIMENSIONS_EXCEEDED",
            retryable=False,
        ) from exc
    except Exception as exc:
        raise PatientSourceSafetyError(
            "SOURCE_IMAGE_DECODER_REJECTED",
            retryable=False,
        ) from exc

    return PatientSourceSafetyDecision(
        content_hash=hashlib.sha256(data).hexdigest(),
        mime_type=mime_type,
        width=width,
        height=height,
    )


def validate_patient_source_decoder(
    data: bytes,
    *,
    mime_type: str,
) -> PatientSourceSafetyDecision:
    """Decode the exact source bytes with bounded, type-specific parsers."""
    if not data:
        raise PatientSourceSafetyError(
            "SOURCE_DOCUMENT_EMPTY",
            retryable=False,
        )
    if mime_type == "application/pdf":
        return _validate_pdf(data)
    if mime_type in {"image/png", "image/jpeg"}:
        return _validate_image(data, mime_type=mime_type)
    raise PatientSourceSafetyError(
        "SOURCE_TYPE_UNSUPPORTED",
        retryable=False,
    )


async def qualify_patient_source_for_extraction(
    data: bytes,
    *,
    mime_type: str,
    expected_hash: str,
    scanner: PatientSourceMalwareScanner | None = None,
) -> PatientSourceSafetyDecision:
    """Fail closed unless exact bytes pass decoder and malware policy."""
    max_bytes = effective_patient_source_max_bytes()
    if not data or len(data) > max_bytes:
        raise PatientSourceSafetyError(
            "SOURCE_SIZE_POLICY_REJECTED",
            retryable=False,
        )

    digest = hashlib.sha256(data).hexdigest()
    if not expected_hash or digest != expected_hash:
        raise PatientSourceSafetyError(
            "SOURCE_INTEGRITY_MISMATCH",
            retryable=False,
        )

    decision = validate_patient_source_decoder(data, mime_type=mime_type)
    if decision.content_hash != digest:
        raise PatientSourceSafetyError(
            "SOURCE_INTEGRITY_MISMATCH",
            retryable=False,
        )

    scanner = scanner or get_patient_source_malware_scanner()
    try:
        result = await scanner.scan(
            data,
            content_hash=digest,
            mime_type=mime_type,
        )
    except Exception as exc:
        raise PatientSourceSafetyError(
            "SOURCE_MALWARE_SCANNER_UNAVAILABLE",
            retryable=True,
        ) from exc

    if not isinstance(result, MalwareScanResult) or result.content_hash != digest:
        raise PatientSourceSafetyError(
            "SOURCE_MALWARE_SCANNER_INVALID_RESULT",
            retryable=True,
        )
    if result.outcome is MalwareScanOutcome.MALICIOUS:
        raise PatientSourceSafetyError(
            "SOURCE_MALWARE_DETECTED",
            retryable=False,
        )
    if result.outcome is MalwareScanOutcome.UNAVAILABLE:
        raise PatientSourceSafetyError(
            "SOURCE_MALWARE_SCANNER_UNAVAILABLE",
            retryable=True,
        )
    if result.outcome is not MalwareScanOutcome.CLEAN:
        raise PatientSourceSafetyError(
            "SOURCE_MALWARE_SCANNER_INVALID_RESULT",
            retryable=True,
        )

    return decision
