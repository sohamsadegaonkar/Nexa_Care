from __future__ import annotations

import inspect
from types import SimpleNamespace
import uuid
from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest
from sqlalchemy.exc import SQLAlchemyError

import app.services.patient_external_record_cancellation as cancellation_module
from app.services.patient_external_record_cancellation import (
    _CANCEL_ALLOWED_STATES,
    cancel_patient_external_record,
)


class _Result:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


def _row(state: str):
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=state,
        retryable=state == "FAILED_RETRYABLE",
        cancelled_at=None,
        source_document_id=uuid.uuid4(),
    )


def test_cancel_service_authority_surface_and_transition_set_are_closed() -> None:
    assert set(inspect.signature(cancel_patient_external_record).parameters) == {
        "db",
        "patient_id",
        "import_id",
    }
    assert _CANCEL_ALLOWED_STATES == frozenset(
        {"UPLOADED", "FAILED_RETRYABLE", "REVIEW_REQUIRED", "READY_TO_SAVE"}
    )
    source = inspect.getsource(cancel_patient_external_record)
    assert "delete_patient_document" not in source
    assert "get_document_storage" not in source
    assert "provider_id" not in source
    assert "hospital_id" not in source
    assert "clinical_access_session_id" not in source
    assert "treatment_token" not in source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    ["UPLOADED", "FAILED_RETRYABLE", "REVIEW_REQUIRED", "READY_TO_SAVE"],
)
async def test_cancel_valid_precompletion_states_retain_source_and_audit_structurally(
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    patient_id = str(uuid.uuid4())
    import_id = uuid.uuid4()
    row = _row(state)
    source_document_id = row.source_document_id
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_Result(row)),
        flush=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    gate = AsyncMock(return_value=None)
    audit = AsyncMock(return_value=None)
    monkeypatch.setattr(
        cancellation_module, "assert_patient_external_record_access_active", gate
    )
    monkeypatch.setattr(
        cancellation_module, "current_audit_context", lambda domain: "audit-context"
    )
    monkeypatch.setattr(cancellation_module, "enqueue_audit_event", audit)

    result = await cancel_patient_external_record(
        db,
        patient_id=patient_id,
        import_id=import_id,
    )

    assert result is row
    assert row.status == "CANCELLED"
    assert row.cancelled_at is not None
    assert row.retryable is False
    assert row.source_document_id == source_document_id
    assert gate.await_count == 2
    db.flush.assert_awaited_once()
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()
    audit.assert_awaited_once()
    call = audit.await_args.kwargs
    assert call["event_type"] == "PATIENT_EXTERNAL_RECORD_CANCELLED"
    assert call["patient_id"] == patient_id
    assert call["metadata"] == {
        "authority": "patient_self",
        "previous_status": state,
    }
    serialized = str(call["metadata"]).lower()
    for forbidden in (
        "storage_ref",
        "filename",
        "diagnosis",
        "medication",
        "source_text",
        "ocr",
        "bucket",
    ):
        assert forbidden not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["PROCESSING", "COMPLETED", "FAILED_TERMINAL"])
async def test_cancel_rejects_invalid_states_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    row = _row(state)
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_Result(row)),
        flush=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    audit = AsyncMock()
    monkeypatch.setattr(
        cancellation_module,
        "assert_patient_external_record_access_active",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(cancellation_module, "enqueue_audit_event", audit)

    with pytest.raises(HTTPException) as exc_info:
        await cancel_patient_external_record(
            db,
            patient_id=str(uuid.uuid4()),
            import_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "error_code": "EXTERNAL_RECORD_CANCEL_NOT_AVAILABLE",
        "retryable": False,
    }
    assert row.status == state
    assert row.cancelled_at is None
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_is_idempotent_for_already_cancelled_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row("CANCELLED")
    row.cancelled_at = object()
    original_cancelled_at = row.cancelled_at
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_Result(row)),
        flush=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    audit = AsyncMock()
    monkeypatch.setattr(
        cancellation_module,
        "assert_patient_external_record_access_active",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(cancellation_module, "enqueue_audit_event", audit)

    result = await cancel_patient_external_record(
        db,
        patient_id=str(uuid.uuid4()),
        import_id=uuid.uuid4(),
    )

    assert result is row
    assert row.cancelled_at is original_cancelled_at
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_guessed_or_cross_patient_import_is_not_found_and_unlocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patient_id = str(uuid.uuid4())
    import_id = uuid.uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_Result(None)),
        flush=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    monkeypatch.setattr(
        cancellation_module,
        "assert_patient_external_record_access_active",
        AsyncMock(return_value=None),
    )

    with pytest.raises(HTTPException) as exc_info:
        await cancel_patient_external_record(
            db,
            patient_id=patient_id,
            import_id=import_id,
        )

    assert exc_info.value.status_code == 404
    statement = db.execute.await_args.args[0]
    params = set(statement.compile().params.values())
    assert uuid.UUID(patient_id) in params
    assert import_id in params
    assert "patient_external_record_imports.patient_id" in str(statement)
    assert "FOR UPDATE" in str(statement).upper()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "detail",
    [
        {"error_code": "PATIENT_DATA_ERASED"},
        {"error_code": "PATIENT_RECORD_RETIRED"},
    ],
)
async def test_cancel_denies_inactive_patient_before_import_lookup(
    monkeypatch: pytest.MonkeyPatch,
    detail: dict[str, str],
) -> None:
    db = SimpleNamespace(
        execute=AsyncMock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    monkeypatch.setattr(
        cancellation_module,
        "assert_patient_external_record_access_active",
        AsyncMock(side_effect=HTTPException(status_code=410, detail=detail)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await cancel_patient_external_record(
            db,
            patient_id=str(uuid.uuid4()),
            import_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 410
    assert exc_info.value.detail == detail
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_lifecycle_race_rolls_back_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row("UPLOADED")
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_Result(row)),
        flush=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    gate = AsyncMock(
        side_effect=[
            None,
            HTTPException(
                status_code=410,
                detail={"error_code": "PATIENT_RECORD_RETIRED"},
            ),
        ]
    )
    audit = AsyncMock()
    monkeypatch.setattr(
        cancellation_module, "assert_patient_external_record_access_active", gate
    )
    monkeypatch.setattr(cancellation_module, "enqueue_audit_event", audit)

    with pytest.raises(HTTPException) as exc_info:
        await cancel_patient_external_record(
            db,
            patient_id=str(uuid.uuid4()),
            import_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 410
    assert row.status == "UPLOADED"
    assert row.cancelled_at is None
    db.rollback.assert_awaited_once()
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_audit_failure_rolls_back_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row("READY_TO_SAVE")
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_Result(row)),
        flush=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    monkeypatch.setattr(
        cancellation_module,
        "assert_patient_external_record_access_active",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        cancellation_module, "current_audit_context", lambda domain: "audit-context"
    )
    monkeypatch.setattr(
        cancellation_module,
        "enqueue_audit_event",
        AsyncMock(side_effect=RuntimeError("synthetic audit failure")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await cancel_patient_external_record(
            db,
            patient_id=str(uuid.uuid4()),
            import_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "error_code": "AUDIT_UNAVAILABLE",
        "retryable": True,
    }
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_persistence_failure_rolls_back_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=SQLAlchemyError("synthetic db failure")),
        flush=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    monkeypatch.setattr(
        cancellation_module,
        "assert_patient_external_record_access_active",
        AsyncMock(return_value=None),
    )

    with pytest.raises(HTTPException) as exc_info:
        await cancel_patient_external_record(
            db,
            patient_id=str(uuid.uuid4()),
            import_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "error_code": "IMPORT_PERSISTENCE_UNAVAILABLE",
        "retryable": True,
    }
    db.rollback.assert_awaited_once()
