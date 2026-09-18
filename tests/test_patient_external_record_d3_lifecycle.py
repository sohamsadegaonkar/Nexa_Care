from __future__ import annotations

from types import SimpleNamespace
import uuid
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException
import pytest

import app.services.patient_external_record_finalization as finalization_module
import app.services.patient_external_record_import as import_module
import app.services.patient_external_record_review as review_module

_RETIRED = HTTPException(
    status_code=410,
    detail={"error_code": "PATIENT_RECORD_RETIRED"},
)


@pytest.mark.asyncio
async def test_merged_old_patient_cannot_upload_external_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = AsyncMock(side_effect=_RETIRED)
    storage = MagicMock()
    monkeypatch.setattr(
        import_module, "assert_patient_external_record_access_active", gate
    )
    monkeypatch.setattr(import_module, "get_document_storage", storage)

    with pytest.raises(HTTPException) as exc_info:
        await import_module.stage_patient_external_record(
            SimpleNamespace(),
            patient_id=str(uuid.uuid4()),
            category_slug="lab_report",
            filename="report.pdf",
            content_type="application/pdf",
            data=b"%PDF-1.4\n%%EOF",
            request_id="merged-old-upload",
        )

    assert exc_info.value.status_code == 410
    assert exc_info.value.detail == {"error_code": "PATIENT_RECORD_RETIRED"}
    storage.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["list", "detail", "source"])
async def test_merged_old_patient_cannot_read_external_record_authority(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    patient_id = str(uuid.uuid4())
    import_id = uuid.uuid4()
    gate = AsyncMock(side_effect=_RETIRED)
    db = SimpleNamespace(execute=AsyncMock())
    storage = MagicMock()
    monkeypatch.setattr(
        import_module, "assert_patient_external_record_access_active", gate
    )
    monkeypatch.setattr(import_module, "get_document_storage", storage)

    with pytest.raises(HTTPException) as exc_info:
        if operation == "list":
            await import_module.list_patient_external_records(
                db,
                patient_id=patient_id,
            )
        elif operation == "detail":
            await import_module.get_patient_external_record(
                db,
                patient_id=patient_id,
                import_id=import_id,
            )
        else:
            await import_module.read_patient_external_record_source(
                db,
                patient_id=patient_id,
                import_id=import_id,
            )

    assert exc_info.value.status_code == 410
    assert exc_info.value.detail == {"error_code": "PATIENT_RECORD_RETIRED"}
    db.execute.assert_not_awaited()
    storage.assert_not_called()


@pytest.mark.asyncio
async def test_merged_old_patient_cannot_review_external_record() -> None:
    patient_uuid = uuid.uuid4()
    db = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(is_deleted=True)),
        rollback=AsyncMock(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await review_module._assert_patient_active(
            db,
            patient_uuid=patient_uuid,
            patient_id=str(patient_uuid),
        )

    assert exc_info.value.status_code == 410
    assert exc_info.value.detail == {"error_code": "PATIENT_RECORD_RETIRED"}
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_merged_old_patient_cannot_save_external_record() -> None:
    patient_uuid = uuid.uuid4()
    db = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(is_deleted=True)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await finalization_module._assert_patient_active(
            db,
            patient_uuid=patient_uuid,
            patient_id=str(patient_uuid),
        )

    assert exc_info.value.status_code == 410
    assert exc_info.value.detail == {"error_code": "PATIENT_RECORD_RETIRED"}
