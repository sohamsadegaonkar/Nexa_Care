from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException
import pytest

import app.services.patient_external_record_finalization as finalization_module
from app.models.patient_records import DocumentReference, LabResult, Medication, TimelineEvent
from app.services.patient_external_record_finalization import (
    _assert_review_complete,
    _assert_source_integrity_metadata,
    finalize_patient_external_record,
)


def _row(*, status: str = "READY_TO_SAVE") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        patient_id=uuid.uuid4(),
        source_document_id=uuid.uuid4(),
        category="LAB_REPORT",
        status=status,
        content_hash="a" * 64,
        created_at=datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc),
        final_record_type=None,
        final_record_id=None,
        timeline_event_id=None,
        completed_at=None,
        error_code=None,
        retryable=False,
    )


def _source(row: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        id=row.source_document_id,
        patient_id=row.patient_id,
        tenant_id=None,
        content_hash=row.content_hash,
        storage_ref="patient-self/synthetic/encrypted.bin",
        uploaded_at=datetime(2026, 9, 18, 9, 0, tzinfo=timezone.utc),
        source_system="patient_self",
        upload_purpose="patient_external_record_import",
    )


def _candidate(status: str) -> SimpleNamespace:
    return SimpleNamespace(
        patient_reviewed=True,
        review_status=status,
        encrypted_raw_value="encrypted-only",
        encrypted_reviewed_value=(
            "encrypted-correction" if status == "CORRECTED" else None
        ),
    )


def _db() -> SimpleNamespace:
    return SimpleNamespace(
        add=MagicMock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )


def test_finalization_authority_surface_has_no_provider_inputs() -> None:
    parameters = set(finalize_patient_external_record.__annotations__)
    assert {"patient_id", "import_id"} <= parameters
    assert {
        "provider_id",
        "hospital_id",
        "tenant_id",
        "consent_token",
        "clinical_access_session_id",
    }.isdisjoint(parameters)


def test_review_completion_requires_explicit_patient_resolution() -> None:
    _assert_review_complete([_candidate("ACCEPTED"), _candidate("CORRECTED")])

    with pytest.raises(HTTPException) as empty:
        _assert_review_complete([])
    assert empty.value.detail["error_code"] == "EXTERNAL_RECORD_REVIEW_EMPTY"

    unresolved = _candidate("NEEDS_REVIEW")
    unresolved.patient_reviewed = False
    with pytest.raises(HTTPException) as incomplete:
        _assert_review_complete([unresolved])
    assert incomplete.value.detail["error_code"] == "EXTERNAL_RECORD_REVIEW_INCOMPLETE"


def test_source_integrity_metadata_must_match_retained_source() -> None:
    row = _row()
    source = _source(row)
    _assert_source_integrity_metadata(row, source)

    source.content_hash = "b" * 64
    with pytest.raises(HTTPException) as mismatch:
        _assert_source_integrity_metadata(row, source)
    assert mismatch.value.detail["error_code"] == "SOURCE_INTEGRITY_UNAVAILABLE"


@pytest.mark.asyncio
async def test_finalization_creates_document_and_timeline_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row()
    source = _source(row)
    candidates = [_candidate("ACCEPTED"), _candidate("CORRECTED"), _candidate("REJECTED")]
    db = _db()

    async def load_import(*args, **kwargs):
        return row

    async def active(*args, **kwargs):
        return None

    async def load_source(*args, **kwargs):
        return source

    async def load_candidates(*args, **kwargs):
        return candidates

    audit = AsyncMock()
    monkeypatch.setattr(
        finalization_module,
        "_load_owned_import_for_finalization",
        load_import,
    )
    monkeypatch.setattr(finalization_module, "_assert_patient_active", active)
    monkeypatch.setattr(
        finalization_module,
        "_load_owned_source_for_finalization",
        load_source,
    )
    monkeypatch.setattr(
        finalization_module,
        "_load_owned_candidates_for_finalization",
        load_candidates,
    )
    monkeypatch.setattr(finalization_module, "enqueue_audit_event", audit)
    monkeypatch.setattr(
        finalization_module,
        "current_audit_context",
        lambda domain: SimpleNamespace(),
    )

    result = await finalize_patient_external_record(
        db,
        patient_id=str(row.patient_id),
        import_id=row.id,
    )

    assert result is row
    assert row.status == "COMPLETED"
    assert row.final_record_type == "DOCUMENT_REFERENCE"
    assert isinstance(row.final_record_id, uuid.UUID)
    assert isinstance(row.timeline_event_id, uuid.UUID)
    assert row.completed_at is not None

    added = [call.args[0] for call in db.add.call_args_list]
    documents = [item for item in added if isinstance(item, DocumentReference)]
    timeline = [item for item in added if isinstance(item, TimelineEvent)]
    assert len(documents) == 1
    assert len(timeline) == 1
    assert not any(isinstance(item, (Medication, LabResult)) for item in added)

    document = documents[0]
    assert document.patient_id == row.patient_id
    assert document.document_type == row.category
    assert document.storage_ref == source.storage_ref
    assert document.extraction_job_id is None

    event = timeline[0]
    assert event.event_type == "DOCUMENT"
    assert event.event_ref_id == document.id
    assert event.source == "patient_uploaded"
    assert event.summary == "Imported by you from an external report"

    metadata = audit.await_args.kwargs["metadata"]
    assert metadata == {
        "authority": "patient_self",
        "category": "LAB_REPORT",
        "final_record_type": "DOCUMENT_REFERENCE",
        "accepted_count": 1,
        "corrected_count": 1,
        "rejected_count": 1,
    }
    assert "encrypted-only" not in str(metadata)
    assert "encrypted-correction" not in str(metadata)
    db.flush.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_completed_finalization_is_idempotent_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row(status="COMPLETED")
    row.final_record_type = "DOCUMENT_REFERENCE"
    row.final_record_id = uuid.uuid4()
    row.timeline_event_id = uuid.uuid4()
    row.completed_at = datetime.now(timezone.utc)
    db = _db()

    async def load_import(*args, **kwargs):
        return row

    monkeypatch.setattr(
        finalization_module,
        "_load_owned_import_for_finalization",
        load_import,
    )

    result = await finalize_patient_external_record(
        db,
        patient_id=str(row.patient_id),
        import_id=row.id,
    )

    assert result is row
    db.add.assert_not_called()
    db.flush.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_rejects_import_before_ready_to_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row(status="REVIEW_REQUIRED")
    db = _db()

    async def load_import(*args, **kwargs):
        return row

    monkeypatch.setattr(
        finalization_module,
        "_load_owned_import_for_finalization",
        load_import,
    )

    with pytest.raises(HTTPException) as exc_info:
        await finalize_patient_external_record(
            db,
            patient_id=str(row.patient_id),
            import_id=row.id,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "error_code": "EXTERNAL_RECORD_NOT_READY_TO_SAVE"
    }
    db.rollback.assert_awaited_once()
