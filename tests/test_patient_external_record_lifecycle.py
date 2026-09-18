from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException
import pytest
from sqlalchemy.exc import SQLAlchemyError

import app.services.patient_external_record_import as import_module
import app.services.patient_external_record_lifecycle as lifecycle_module
from app.services.patient_external_record_lifecycle import (
    PatientExternalSourceErasureUnavailable,
    assert_patient_external_record_access_active,
    delete_patient_external_record_sources,
)


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)


@pytest.mark.asyncio
async def test_lifecycle_access_gate_maps_erased_patient_to_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def erased(*args, **kwargs):
        raise lifecycle_module._PatientErasedSignal("patient")

    monkeypatch.setattr(lifecycle_module, "check_erasure_registry", erased)
    db = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(is_deleted=False))
    )

    with pytest.raises(HTTPException) as exc_info:
        await assert_patient_external_record_access_active(
            db,
            patient_id=str(uuid.uuid4()),
        )

    assert exc_info.value.status_code == 410
    assert exc_info.value.detail == {"error_code": "PATIENT_DATA_ERASED"}



@pytest.mark.asyncio
async def test_lifecycle_access_gate_denies_retired_or_merged_old_patient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = AsyncMock()
    monkeypatch.setattr(lifecycle_module, "check_erasure_registry", registry)
    db = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(is_deleted=True))
    )

    with pytest.raises(HTTPException) as exc_info:
        await assert_patient_external_record_access_active(
            db,
            patient_id=str(uuid.uuid4()),
        )

    assert exc_info.value.status_code == 410
    assert exc_info.value.detail == {"error_code": "PATIENT_RECORD_RETIRED"}
    registry.assert_not_awaited()


@pytest.mark.asyncio
async def test_lifecycle_access_gate_fails_closed_when_patient_store_unavailable() -> None:
    db = SimpleNamespace(
        get=AsyncMock(side_effect=SQLAlchemyError("synthetic db failure"))
    )

    with pytest.raises(HTTPException) as exc_info:
        await assert_patient_external_record_access_active(
            db,
            patient_id=str(uuid.uuid4()),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "error_code": "PATIENT_DATA_UNAVAILABLE",
        "retryable": True,
    }


@pytest.mark.asyncio
async def test_delete_patient_sources_deletes_and_neutralizes_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patient_id = str(uuid.uuid4())
    patient_uuid = uuid.UUID(patient_id)
    doc_id = uuid.uuid4()
    source_rows = [
        SimpleNamespace(
            storage_ref="patient-self/ref-1",
            original_filename="patient-name-lab.pdf",
            content_hash="a" * 64,
            uploader_id=f"patient:{patient_id}",
        ),
        SimpleNamespace(
            storage_ref="patient-self/ref-2",
            original_filename="scan.jpg",
            content_hash="b" * 64,
            uploader_id=f"patient:{patient_id}",
        ),
    ]
    import_rows = [
        SimpleNamespace(
            final_record_type="DOCUMENT_REFERENCE",
            final_record_id=doc_id,
            content_hash="a" * 64,
        ),
        SimpleNamespace(
            final_record_type=None,
            final_record_id=None,
            content_hash="b" * 64,
        ),
    ]
    execute = AsyncMock(
        side_effect=[
            _Result(source_rows),
            _Result(import_rows),
            _Result([]),
        ]
    )
    db = SimpleNamespace(
        execute=execute,
        flush=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    storage = SimpleNamespace(delete_patient_document=AsyncMock())
    monkeypatch.setattr(lifecycle_module, "get_document_storage", lambda: storage)

    deleted = await delete_patient_external_record_sources(db, patient_id=patient_id)

    assert deleted == 2
    assert storage.delete_patient_document.await_count == 2
    storage.delete_patient_document.assert_any_await(
        "patient-self/ref-1", patient_id=patient_id
    )
    storage.delete_patient_document.assert_any_await(
        "patient-self/ref-2", patient_id=patient_id
    )
    assert all(
        row.storage_ref == lifecycle_module._ERASED_SOURCE_REF for row in source_rows
    )
    assert all(row.original_filename is None for row in source_rows)
    assert all(row.content_hash is None for row in source_rows)
    assert all(row.uploader_id is None for row in source_rows)
    assert all(row.content_hash is None for row in import_rows)
    assert "document_references" in str(execute.await_args_list[2].args[0]).lower()
    assert str(patient_uuid) not in lifecycle_module._ERASED_DOCUMENT_REF
    db.flush.assert_awaited_once()
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_metadata_neutralization_runs_when_source_rows_are_already_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patient_id = str(uuid.uuid4())
    doc_id = uuid.uuid4()
    import_row = SimpleNamespace(
        final_record_type="DOCUMENT_REFERENCE",
        final_record_id=doc_id,
        content_hash="a" * 64,
    )
    execute = AsyncMock(
        side_effect=[
            _Result([]),
            _Result([import_row]),
            _Result([]),
        ]
    )
    db = SimpleNamespace(
        execute=execute,
        flush=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    storage_factory = MagicMock()
    monkeypatch.setattr(lifecycle_module, "get_document_storage", storage_factory)

    deleted = await delete_patient_external_record_sources(db, patient_id=patient_id)

    assert deleted == 0
    assert import_row.content_hash is None
    assert "document_references" in str(execute.await_args_list[2].args[0]).lower()
    storage_factory.assert_not_called()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_patient_sources_fails_closed_on_object_delete_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patient_id = str(uuid.uuid4())
    source_rows = [
        SimpleNamespace(
            storage_ref="patient-self/ref-1",
            original_filename="report.pdf",
            content_hash="a" * 64,
            uploader_id=f"patient:{patient_id}",
        )
    ]
    import_rows = []
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[_Result(source_rows), _Result(import_rows)]),
        flush=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    storage = SimpleNamespace(
        delete_patient_document=AsyncMock(side_effect=RuntimeError("synthetic"))
    )
    monkeypatch.setattr(lifecycle_module, "get_document_storage", lambda: storage)

    with pytest.raises(PatientExternalSourceErasureUnavailable):
        await delete_patient_external_record_sources(db, patient_id=patient_id)

    assert source_rows[0].storage_ref == "patient-self/ref-1"
    assert source_rows[0].original_filename == "report.pdf"
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_erasure_race_deletes_just_written_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patient_id = str(uuid.uuid4())
    stored = SimpleNamespace(
        storage_ref="patient-self/synthetic.bin",
        content_hash="a" * 64,
        size=128,
    )
    storage = SimpleNamespace(
        put_patient_document=AsyncMock(return_value=stored),
        delete_patient_document=AsyncMock(),
    )
    db = SimpleNamespace()

    monkeypatch.setattr(
        import_module,
        "validate_patient_upload_type",
        lambda filename, content_type, data: ("report.pdf", "application/pdf"),
    )
    monkeypatch.setattr(
        import_module,
        "_find_import_by_request",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(import_module, "get_document_storage", lambda: storage)
    gate = AsyncMock(
        side_effect=[
            None,
            HTTPException(
                status_code=410,
                detail={"error_code": "PATIENT_DATA_ERASED"},
            ),
        ]
    )
    monkeypatch.setattr(
        import_module,
        "assert_patient_external_record_access_active",
        gate,
    )

    with pytest.raises(HTTPException) as exc_info:
        await import_module.stage_patient_external_record(
            db,
            patient_id=patient_id,
            category_slug="lab_report",
            filename="report.pdf",
            content_type="application/pdf",
            data=b"%PDF-1.4\n%%EOF",
            request_id="req-erasure-race",
        )

    assert exc_info.value.status_code == 410
    storage.delete_patient_document.assert_awaited_once_with(
        stored.storage_ref,
        patient_id=patient_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "args"),
    [
        ("list", {}),
        (
            "detail",
            {
                "import_id": uuid.UUID(
                    "00000000-0000-0000-0000-000000000001"
                )
            },
        ),
    ],
)
async def test_import_metadata_reads_deny_erased_patient_before_query(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    args: dict[str, uuid.UUID],
) -> None:
    patient_id = str(uuid.uuid4())
    gate = AsyncMock(
        side_effect=HTTPException(
            status_code=410,
            detail={"error_code": "PATIENT_DATA_ERASED"},
        )
    )
    monkeypatch.setattr(
        import_module,
        "assert_patient_external_record_access_active",
        gate,
    )
    db = SimpleNamespace(execute=AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        if operation == "list":
            await import_module.list_patient_external_records(
                db,
                patient_id=patient_id,
            )
        else:
            await import_module.get_patient_external_record(
                db,
                patient_id=patient_id,
                **args,
            )

    assert exc_info.value.status_code == 410
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_read_denies_erased_patient_before_storage_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patient_id = str(uuid.uuid4())
    import_id = uuid.uuid4()
    row = SimpleNamespace(source_document_id=uuid.uuid4())
    monkeypatch.setattr(
        import_module,
        "get_patient_external_record",
        AsyncMock(return_value=row),
    )
    monkeypatch.setattr(
        import_module,
        "assert_patient_external_record_access_active",
        AsyncMock(
            side_effect=HTTPException(
                status_code=410,
                detail={"error_code": "PATIENT_DATA_ERASED"},
            )
        ),
    )
    storage_factory = MagicMock()
    monkeypatch.setattr(import_module, "get_document_storage", storage_factory)

    with pytest.raises(HTTPException) as exc_info:
        await import_module.read_patient_external_record_source(
            SimpleNamespace(),
            patient_id=patient_id,
            import_id=import_id,
        )

    assert exc_info.value.status_code == 410
    storage_factory.assert_not_called()
