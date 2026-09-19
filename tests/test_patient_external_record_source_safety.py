from __future__ import annotations

import hashlib
import inspect
import io
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from PIL import Image
from pypdf import PdfWriter

import app.services.patient_external_record_extraction as extraction_module
import app.services.patient_external_record_import as import_module
import app.services.patient_external_record_source_safety as safety_module
from app.services.patient_external_record_extraction import _commit_failure
from app.services.patient_external_record_source_safety import (
    MalwareScanOutcome,
    MalwareScanResult,
    PatientSourceSafetyError,
    UnavailablePatientSourceMalwareScanner,
    qualify_patient_source_for_extraction,
    validate_patient_source_decoder,
)


def _pdf(page_count: int = 1, *, encrypted: bool = False) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=612, height=792)
    if encrypted:
        writer.encrypt("secret")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _png(size: tuple[int, int] = (2, 2)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg(size: tuple[int, int] = (2, 2)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="JPEG")
    return buffer.getvalue()


class _Scanner:
    def __init__(
        self,
        outcome: MalwareScanOutcome,
        *,
        hash_override: str | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.outcome = outcome
        self.hash_override = hash_override
        self.exc = exc
        self.calls: list[tuple[bytes, str, str]] = []

    async def scan(
        self,
        data: bytes,
        *,
        content_hash: str,
        mime_type: str,
    ) -> MalwareScanResult:
        self.calls.append((data, content_hash, mime_type))
        if self.exc is not None:
            raise self.exc
        return MalwareScanResult(
            outcome=self.outcome,
            content_hash=self.hash_override or content_hash,
        )


class _MalformedScanner:
    async def scan(self, data: bytes, *, content_hash: str, mime_type: str):
        _ = (data, content_hash, mime_type)
        return SimpleNamespace(outcome="CLEAN", content_hash=content_hash)


def test_valid_pdf_decoder_accepts_real_structural_pdf() -> None:
    decision = validate_patient_source_decoder(_pdf(), mime_type="application/pdf")
    assert decision.page_count == 1
    assert decision.width is None
    assert decision.height is None


@pytest.mark.parametrize(
    ("data", "expected_code"),
    [
        (b"%PDF-1.7\ntruncated", "SOURCE_PDF_DECODER_REJECTED"),
        (b"%PDF-1.7\nnot-a-real-xref\n%%EOF", "SOURCE_PDF_DECODER_REJECTED"),
    ],
)
def test_pdf_decoder_rejects_truncated_and_structurally_corrupt_sources(
    data: bytes,
    expected_code: str,
) -> None:
    with pytest.raises(PatientSourceSafetyError) as caught:
        validate_patient_source_decoder(data, mime_type="application/pdf")
    assert caught.value.code == expected_code
    assert caught.value.retryable is False


def test_pdf_decoder_rejects_encrypted_source() -> None:
    with pytest.raises(PatientSourceSafetyError) as caught:
        validate_patient_source_decoder(
            _pdf(encrypted=True),
            mime_type="application/pdf",
        )
    assert caught.value.code == "SOURCE_PDF_ENCRYPTED"
    assert caught.value.retryable is False


def test_pdf_decoder_rejects_zero_page_source() -> None:
    with pytest.raises(PatientSourceSafetyError) as caught:
        validate_patient_source_decoder(_pdf(page_count=0), mime_type="application/pdf")
    assert caught.value.code == "SOURCE_PDF_PAGE_LIMIT_EXCEEDED"


def test_pdf_decoder_rejects_page_count_over_configured_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATIENT_SOURCE_MAX_PDF_PAGES", "1")
    with pytest.raises(PatientSourceSafetyError) as caught:
        validate_patient_source_decoder(_pdf(page_count=2), mime_type="application/pdf")
    assert caught.value.code == "SOURCE_PDF_PAGE_LIMIT_EXCEEDED"


def test_valid_png_and_jpeg_use_real_decoder() -> None:
    png = validate_patient_source_decoder(_png(), mime_type="image/png")
    jpeg = validate_patient_source_decoder(_jpeg(), mime_type="image/jpeg")
    assert (png.width, png.height) == (2, 2)
    assert (jpeg.width, jpeg.height) == (2, 2)


@pytest.mark.parametrize(
    ("data", "mime_type"),
    [
        (b"\x89PNG\r\n\x1a\ncorrupt", "image/png"),
        (b"\xff\xd8\xfftruncated", "image/jpeg"),
    ],
)
def test_image_decoder_rejects_corrupt_or_truncated_sources(
    data: bytes,
    mime_type: str,
) -> None:
    with pytest.raises(PatientSourceSafetyError) as caught:
        validate_patient_source_decoder(data, mime_type=mime_type)
    assert caught.value.code == "SOURCE_IMAGE_DECODER_REJECTED"
    assert caught.value.retryable is False


def test_image_decoder_rejects_format_mismatch_polyglot_shape() -> None:
    with pytest.raises(PatientSourceSafetyError) as caught:
        validate_patient_source_decoder(_jpeg(), mime_type="image/png")
    assert caught.value.code == "SOURCE_IMAGE_FORMAT_MISMATCH"


def test_image_decoder_rejects_dimension_or_decompression_bomb_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATIENT_SOURCE_MAX_IMAGE_PIXELS", "3")
    with pytest.raises(PatientSourceSafetyError) as caught:
        validate_patient_source_decoder(_png((2, 2)), mime_type="image/png")
    assert caught.value.code == "SOURCE_IMAGE_DIMENSIONS_EXCEEDED"


@pytest.mark.asyncio
async def test_clean_scanner_is_bound_to_exact_source_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _pdf()
    digest = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(
        safety_module,
        "effective_patient_source_max_bytes",
        lambda: len(data) + 1,
    )
    scanner = _Scanner(MalwareScanOutcome.CLEAN)

    decision = await qualify_patient_source_for_extraction(
        data,
        mime_type="application/pdf",
        expected_hash=digest,
        scanner=scanner,
    )

    assert decision.content_hash == digest
    assert scanner.calls == [(data, digest, "application/pdf")]


@pytest.mark.asyncio
async def test_changed_source_hash_never_reuses_clean_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _pdf()
    monkeypatch.setattr(
        safety_module,
        "effective_patient_source_max_bytes",
        lambda: len(data) + 1,
    )
    scanner = _Scanner(MalwareScanOutcome.CLEAN)

    changed = data[:-1] + (b"X" if data[-1:] != b"X" else b"Y")
    with pytest.raises(PatientSourceSafetyError) as caught:
        await qualify_patient_source_for_extraction(
            changed,
            mime_type="application/pdf",
            expected_hash=hashlib.sha256(data).hexdigest(),
            scanner=scanner,
        )

    assert caught.value.code == "SOURCE_INTEGRITY_MISMATCH"
    assert scanner.calls == []


@pytest.mark.asyncio
async def test_scanner_clean_result_with_wrong_hash_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _pdf()
    digest = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(
        safety_module,
        "effective_patient_source_max_bytes",
        lambda: len(data) + 1,
    )
    scanner = _Scanner(MalwareScanOutcome.CLEAN, hash_override="0" * 64)

    with pytest.raises(PatientSourceSafetyError) as caught:
        await qualify_patient_source_for_extraction(
            data,
            mime_type="application/pdf",
            expected_hash=digest,
            scanner=scanner,
        )
    assert caught.value.code == "SOURCE_MALWARE_SCANNER_INVALID_RESULT"
    assert caught.value.retryable is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "code", "retryable"),
    [
        (MalwareScanOutcome.MALICIOUS, "SOURCE_MALWARE_DETECTED", False),
        (
            MalwareScanOutcome.UNAVAILABLE,
            "SOURCE_MALWARE_SCANNER_UNAVAILABLE",
            True,
        ),
    ],
)
async def test_closed_scanner_outcomes_block_extraction(
    monkeypatch: pytest.MonkeyPatch,
    outcome: MalwareScanOutcome,
    code: str,
    retryable: bool,
) -> None:
    data = _pdf()
    digest = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(
        safety_module,
        "effective_patient_source_max_bytes",
        lambda: len(data) + 1,
    )

    with pytest.raises(PatientSourceSafetyError) as caught:
        await qualify_patient_source_for_extraction(
            data,
            mime_type="application/pdf",
            expected_hash=digest,
            scanner=_Scanner(outcome),
        )
    assert caught.value.code == code
    assert caught.value.retryable is retryable


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [TimeoutError("timeout"), ConnectionError("offline")])
async def test_scanner_transport_failures_are_unavailable_not_clean(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
) -> None:
    data = _pdf()
    digest = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(
        safety_module,
        "effective_patient_source_max_bytes",
        lambda: len(data) + 1,
    )

    with pytest.raises(PatientSourceSafetyError) as caught:
        await qualify_patient_source_for_extraction(
            data,
            mime_type="application/pdf",
            expected_hash=digest,
            scanner=_Scanner(MalwareScanOutcome.CLEAN, exc=exc),
        )
    assert caught.value.code == "SOURCE_MALWARE_SCANNER_UNAVAILABLE"
    assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_malformed_scanner_result_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _pdf()
    digest = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(
        safety_module,
        "effective_patient_source_max_bytes",
        lambda: len(data) + 1,
    )

    with pytest.raises(PatientSourceSafetyError) as caught:
        await qualify_patient_source_for_extraction(
            data,
            mime_type="application/pdf",
            expected_hash=digest,
            scanner=_MalformedScanner(),
        )
    assert caught.value.code == "SOURCE_MALWARE_SCANNER_INVALID_RESULT"
    assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_default_production_scanner_is_explicitly_unavailable() -> None:
    scanner = UnavailablePatientSourceMalwareScanner()
    digest = hashlib.sha256(b"source").hexdigest()
    result = await scanner.scan(
        b"source",
        content_hash=digest,
        mime_type="application/pdf",
    )
    assert result == MalwareScanResult(
        outcome=MalwareScanOutcome.UNAVAILABLE,
        content_hash=digest,
    )


@pytest.mark.asyncio
async def test_oversized_source_is_rejected_before_scanner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _pdf()
    scanner = _Scanner(MalwareScanOutcome.CLEAN)
    monkeypatch.setattr(
        safety_module,
        "effective_patient_source_max_bytes",
        lambda: len(data) - 1,
    )

    with pytest.raises(PatientSourceSafetyError) as caught:
        await qualify_patient_source_for_extraction(
            data,
            mime_type="application/pdf",
            expected_hash=hashlib.sha256(data).hexdigest(),
            scanner=scanner,
        )
    assert caught.value.code == "SOURCE_SIZE_POLICY_REJECTED"
    assert scanner.calls == []


@pytest.mark.asyncio
async def test_upload_decoder_failure_blocks_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patient_id = str(uuid.uuid4())
    storage = MagicMock()
    monkeypatch.setattr(
        import_module,
        "assert_patient_external_record_access_active",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        import_module,
        "validate_patient_upload_type",
        lambda filename, content_type, data: ("report.pdf", "application/pdf"),
    )
    monkeypatch.setattr(
        import_module,
        "validate_patient_source_decoder",
        MagicMock(
            side_effect=PatientSourceSafetyError(
                "SOURCE_PDF_DECODER_REJECTED",
                retryable=False,
            )
        ),
    )
    monkeypatch.setattr(import_module, "get_document_storage", storage)

    with pytest.raises(HTTPException) as caught:
        await import_module.stage_patient_external_record(
            AsyncMock(),
            patient_id=patient_id,
            category_slug="lab_report",
            filename="report.pdf",
            content_type="application/pdf",
            data=b"bad",
            request_id="decoder-rejected",
        )

    assert caught.value.status_code == 422
    assert caught.value.detail == {
        "error_code": "SOURCE_PDF_DECODER_REJECTED",
        "retryable": False,
    }
    storage.assert_not_called()


@pytest.mark.asyncio
async def test_known_malicious_source_is_not_returned_to_patient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = MagicMock()
    monkeypatch.setattr(
        import_module,
        "get_patient_external_record",
        AsyncMock(
            return_value=SimpleNamespace(error_code="SOURCE_MALWARE_DETECTED")
        ),
    )
    monkeypatch.setattr(import_module, "get_document_storage", storage)

    with pytest.raises(HTTPException) as caught:
        await import_module.read_patient_external_record_source(
            AsyncMock(),
            patient_id=str(uuid.uuid4()),
            import_id=uuid.uuid4(),
        )

    assert caught.value.status_code == 409
    assert caught.value.detail == {
        "error_code": "SOURCE_DOCUMENT_QUARANTINED",
        "retryable": False,
    }
    storage.assert_not_called()


def test_process_gate_precedes_any_extraction_provider_call() -> None:
    source = inspect.getsource(extraction_module.process_patient_external_record)
    gate = source.index("qualify_patient_source_for_extraction")
    provider = source.index("extractor.extract_bytes")
    assert gate < provider


def test_retry_reuses_same_process_gate_without_second_extraction_path() -> None:
    source = inspect.getsource(extraction_module.retry_patient_external_record)
    assert "process_patient_external_record" in source
    assert "extract_bytes" not in source


@pytest.mark.asyncio
async def test_safety_failure_audit_is_structural_and_value_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit = AsyncMock()
    monkeypatch.setattr(extraction_module, "enqueue_audit_event", audit)
    db = AsyncMock()
    row = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_count=3,
        status="PROCESSING",
        error_code=None,
        retryable=False,
    )

    result = await _commit_failure(
        db,
        row=row,
        patient_id=str(uuid.uuid4()),
        error_code="SOURCE_MALWARE_DETECTED",
        retryable=False,
    )

    assert result is row
    metadata = audit.await_args.kwargs["metadata"]
    assert metadata == {
        "authority": "patient_self",
        "error_code": "SOURCE_MALWARE_DETECTED",
        "retryable": False,
    }
    assert not {
        "document",
        "content",
        "source",
        "source_text",
        "filename",
        "storage_ref",
        "bucket",
        "signature",
        "malware_signature",
    }.intersection(metadata)


@pytest.mark.asyncio
async def test_safety_failure_audit_error_prevents_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        extraction_module,
        "enqueue_audit_event",
        AsyncMock(side_effect=RuntimeError("audit unavailable")),
    )
    db = AsyncMock()
    row = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_count=1,
        status="PROCESSING",
        error_code=None,
        retryable=False,
    )

    with pytest.raises(RuntimeError):
        await _commit_failure(
            db,
            row=row,
            patient_id=str(uuid.uuid4()),
            error_code="SOURCE_MALWARE_DETECTED",
            retryable=False,
        )

    db.commit.assert_not_awaited()
