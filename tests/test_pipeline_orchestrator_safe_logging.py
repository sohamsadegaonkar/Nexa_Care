"""DEFECT 4: pipeline_orchestrator must never log raw exception text.

Proves that when process_extraction_job hits an unexpected internal
exception whose message contains fake PII, a signed storage URL, and an
access token, none of those values reach the logs -- only the sanitized
safe-exception fields (exception class, stable error code, subsystem,
operation, correlation id, retryability) do.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.models.pipeline import DocumentStorage as DocumentStorageRecord
from app.models.pipeline import ExtractionJob
from app.models.ai_models import ExtractedMedicalDocument
from app.services.pipeline_orchestrator import process_extraction_job

SENSITIVE_EXCEPTION_TEXT = (
    "Failed for patient Asha Rao (ABHA 91-2345-6789-0001), "
    "signed URL https://storage.example.com/bucket/doc.pdf"
    "?X-Amz-Signature=deadbeefcafebabe1234567890&X-Amz-Credential=AKIAFAKEKEYID, "
    "access token Bearer eyJhbGciOiJIUzI1NiJ9.fake.token.value"
)


class _FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDB:
    """Minimal AsyncSession stand-in: first execute() returns the job,
    second returns the document, then raises inside document extraction."""

    def __init__(self, job: ExtractionJob, document: DocumentStorageRecord):
        self._results = [job, document]

    async def execute(self, *_args, **_kwargs):
        return _FakeScalarResult(self._results.pop(0))

    async def commit(self):
        return None

    async def flush(self):
        return None


def _make_job_and_document() -> tuple[ExtractionJob, DocumentStorageRecord]:
    patient_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    document_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    document = DocumentStorageRecord(
        id=document_id,
        patient_id=patient_id,
        tenant_id=tenant_id,
        uploader_id="doctor-1",
        storage_ref="local://fake-ref",
        content_type="application/pdf",
        size=1024,
        content_hash="a" * 64,
        uploaded_at=now,
    )
    job = ExtractionJob(
        id=uuid.uuid4(),
        patient_id=patient_id,
        tenant_id=tenant_id,
        uploader_id="doctor-1",
        document_id=document_id,
        document_type="lab_report",
        status="queued",
        request_id="req-defect4-test",
        attempt_count=0,
        retryable=False,
        created_at=now,
    )
    return job, document


@pytest.mark.asyncio
async def test_internal_failure_log_never_leaks_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    job, document = _make_job_and_document()
    db = _FakeDB(job, document)

    fake_storage = AsyncMock()
    fake_storage.get_document_bytes = AsyncMock(
        side_effect=RuntimeError(SENSITIVE_EXCEPTION_TEXT)
    )

    with (
        patch(
            "app.services.pipeline_orchestrator.get_document_storage",
            return_value=fake_storage,
        ),
        patch(
            "app.services.pipeline_orchestrator.append_audit_log_or_503",
            AsyncMock(return_value=True),
        ),
        caplog.at_level(logging.ERROR, logger="nexa_logger"),
    ):
        result = await process_extraction_job(str(job.id), db)

    assert result["status"] == "extraction_failed_terminal"
    assert result["error_code"] == "EXTRACTION_INTERNAL_ERROR"

    emitted = "\n".join(record.getMessage() for record in caplog.records)

    # The exception class and a stable, non-secret error code are fine to log.
    assert "RuntimeError" in emitted

    # None of the sensitive substrings embedded in the exception message may
    # ever reach the log output.
    assert "Asha Rao" not in emitted
    assert "91-2345-6789-0001" not in emitted
    assert "X-Amz-Signature" not in emitted
    assert "X-Amz-Credential" not in emitted
    assert "storage.example.com" not in emitted
    assert "eyJhbGciOiJIUzI1NiJ9.fake.token.value" not in emitted
    assert SENSITIVE_EXCEPTION_TEXT not in emitted

    # And no record's raw formatted output (which would include a traceback
    # if exc_info were set) leaks it either -- guards against exc_info=True.
    for record in caplog.records:
        assert "Asha Rao" not in record.getMessage()
        formatted = logging.Formatter().format(record)
        assert "Asha Rao" not in formatted
        assert "X-Amz-Signature" not in formatted


@pytest.mark.asyncio
async def test_document_only_confidence_fails_before_staging_field_persistence():
    job, document = _make_job_and_document()
    db = _FakeDB(job, document)
    fake_storage = AsyncMock()
    fake_storage.get_document_bytes = AsyncMock(return_value=b"%PDF-1.7")
    fake_extractor = AsyncMock()
    fake_extractor.extract_bytes = AsyncMock(
        return_value=ExtractedMedicalDocument(
            patient_name="",
            aadhaar_abha_id="",
            phone="",
            diagnoses=["Hypertension"],
            lab_results=[],
            prescriptions=[],
            extraction_confidence=0.99,
        )
    )

    with (
        patch(
            "app.services.pipeline_orchestrator.get_document_storage",
            return_value=fake_storage,
        ),
        patch(
            "app.services.pipeline_orchestrator.get_medical_document_extractor",
            return_value=fake_extractor,
        ),
        patch(
            "app.services.pipeline_orchestrator.append_audit_log_or_503",
            AsyncMock(return_value=True),
        ),
    ):
        result = await process_extraction_job(str(job.id), db)

    assert result["status"] == "validation_failed"
    assert result["error_code"] == "FIELD_EVIDENCE_INCOMPLETE"
    assert job.status == "validation_failed"
