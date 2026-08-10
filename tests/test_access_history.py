"""Access-history results remain authoritative, meaningful, and fail closed."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.v2.patient_record_routes import (
    get_my_access_history,
    get_patient_audit_trail,
)
from app.observability.audit_ledger import read_patient_access_history_events


def _result(*rows):
    return SimpleNamespace(all=lambda: list(rows))


def _mapping_result(*rows):
    return SimpleNamespace(
        mappings=lambda: SimpleNamespace(all=lambda: list(rows)),
    )


def _provider_access_row(provider_id, hospital_id, **overrides):
    row = {
        "audit_id": str(uuid.uuid4()),
        "event_type": "PATIENT_RECORD_READ_SUCCESS",
        "actor_uid": str(provider_id),
        "status": "SUCCESS",
        "created_at": "2026-07-26T10:00:00+00:00",
        "metadata": {
            "hospital_id": str(hospital_id),
            "purpose": "treatment",
            "scope": ["clinical_summary"],
        },
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_patient_self_access_events_are_excluded():
    patient_id = str(uuid.uuid4())
    rows = [
        {
            "event_type": "PATIENT_RECORD_READ_SUCCESS",
            "actor_uid": patient_id,
            "status": "SUCCESS",
            "metadata": {"access_type": "self_access"},
        }
    ]
    db = AsyncMock()
    with patch(
        "app.api.v2.patient_record_routes.read_patient_access_history_events",
        new=AsyncMock(return_value=rows),
    ):
        result = await get_my_access_history(patient_id=patient_id, db=db)

    assert result["access_history"] == []
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_refreshing_access_history_does_not_create_another_visible_card():
    patient_id = str(uuid.uuid4())
    provider_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    rows = [
        _provider_access_row(provider_id, hospital_id),
        {
            "event_type": "PATIENT_RECORD_READ_SUCCESS",
            "actor_uid": patient_id,
            "status": "SUCCESS",
            "metadata": {"access_type": "self_access"},
        },
    ]
    db = AsyncMock()
    db.execute.side_effect = [
        _result((provider_id, "Dr. Registry Name", hospital_id)),
        _result((hospital_id, "Registry Hospital")),
    ]
    with patch(
        "app.api.v2.patient_record_routes.read_patient_access_history_events",
        new=AsyncMock(return_value=rows),
    ):
        result = await get_my_access_history(patient_id=patient_id, db=db)

    assert len(result["access_history"]) == 1


@pytest.mark.asyncio
async def test_provider_access_has_registry_provider_hospital_and_purpose():
    provider_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    db = AsyncMock()
    db.execute.side_effect = [
        _result((provider_id, "Dr. Registry Name", hospital_id)),
        _result((hospital_id, "Registry Hospital")),
    ]
    with patch(
        "app.api.v2.patient_record_routes.read_patient_access_history_events",
        new=AsyncMock(return_value=[_provider_access_row(provider_id, hospital_id)]),
    ):
        result = await get_my_access_history(patient_id=str(uuid.uuid4()), db=db)

    event = result["access_history"][0]
    assert event["doctor_name"] == "Dr. Registry Name"
    assert event["hospital_name"] == "Registry Hospital"
    assert event["purpose"] == "treatment"
    assert all(
        isinstance(event[field], str) and event[field].strip()
        for field in ("doctor_name", "hospital_name", "purpose")
    )
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_non_access_and_identity_review_events_are_excluded():
    rows = [
        {"event_type": "PATIENT_RECORD_VIEW_STARTED", "status": "STARTED"},
        {"event_type": "PATIENT_RECORD_VIEW_FAILED", "status": "FAILED"},
        {"event_type": "PATIENT_RECORD_READ_FAILED", "status": "FAILED"},
        {"event_type": "PROVIDER_ACCESS_DENIED", "status": "DENIED"},
        {"event_type": "SESSION_VALIDATION_FAILED", "status": "FAILED"},
        {"event_type": "CONSENT_REQUEST_CREATED", "status": "SUCCESS"},
        {"event_type": "IDENTITY_REVIEW_CASE_CREATED", "status": "SUCCESS"},
        {"event_type": "IDENTITY_REVIEW_CASE_ACCESSED", "status": "SUCCESS"},
        {"event_type": "IDENTITY_REVIEW_CASE_CLAIMED", "status": "SUCCESS"},
        {"event_type": "IDENTITY_REVIEW_SESSION_ROTATED", "status": "SUCCESS"},
        {
            "event_type": "IDENTITY_REVIEW_DISPOSITION_SUBMITTED",
            "status": "SUCCESS",
        },
        {"event_type": "IDENTITY_REVIEW_ESCALATED", "status": "SUCCESS"},
        {"event_type": "IDENTITY_REVIEW_ACCESS_REJECTED", "status": "REJECTED"},
    ]
    db = AsyncMock()
    with patch(
        "app.api.v2.patient_record_routes.read_patient_access_history_events",
        new=AsyncMock(return_value=rows),
    ):
        result = await get_my_access_history(patient_id=str(uuid.uuid4()), db=db)

    assert result["access_history"] == []
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_break_glass_access_remains_clearly_flagged():
    provider_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    row = {
        "event_type": "BREAK_GLASS_EMERGENCY_SUMMARY_ACCESSED",
        "actor_uid": str(provider_id),
        "status": "SUCCESS",
        "metadata": {
            "hospital_id": str(hospital_id),
            "purpose": "EMERGENCY",
            "categories": ["allergies"],
        },
    }
    db = AsyncMock()
    db.execute.side_effect = [
        _result((provider_id, "Dr. Emergency", hospital_id)),
        _result((hospital_id, "Emergency Hospital")),
    ]
    with patch(
        "app.api.v2.patient_record_routes.read_patient_access_history_events",
        new=AsyncMock(return_value=[row]),
    ):
        result = await get_my_access_history(patient_id=str(uuid.uuid4()), db=db)

    event = result["access_history"][0]
    assert event["is_break_glass"] is True
    assert event["flag"] == "BREAK_GLASS_ACCESS"
    assert event["data_categories"] == ["allergies"]


@pytest.mark.asyncio
async def test_legacy_missing_identities_use_explicit_fallback_labels():
    row = {
        "event_type": "PATIENT_RECORD_READ_SUCCESS",
        "actor_uid": "legacy-provider-id",
        "status": "SUCCESS",
        "metadata": {},
    }
    db = AsyncMock()
    with patch(
        "app.api.v2.patient_record_routes.read_patient_access_history_events",
        new=AsyncMock(return_value=[row]),
    ):
        result = await get_my_access_history(patient_id=str(uuid.uuid4()), db=db)

    event = result["access_history"][0]
    assert event["doctor_name"] == "Former or unavailable provider"
    assert event["hospital_name"] == "Unknown facility"
    assert event["purpose"] == "Purpose not recorded"
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_provider_operation_produces_one_transparency_entry():
    provider_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    operation_id = str(uuid.uuid4())
    first = _provider_access_row(provider_id, hospital_id)
    first["metadata"]["audit_transaction_id"] = operation_id
    duplicate = {
        **first,
        "audit_id": str(uuid.uuid4()),
        "event_type": "PATIENT_RECORD_VIEW_COMPLETED",
        "status": "COMPLETED",
    }
    db = AsyncMock()
    db.execute.side_effect = [
        _result((provider_id, "Dr. Registry Name", hospital_id)),
        _result((hospital_id, "Registry Hospital")),
    ]
    with patch(
        "app.api.v2.patient_record_routes.read_patient_access_history_events",
        new=AsyncMock(return_value=[first, duplicate]),
    ):
        result = await get_my_access_history(patient_id=str(uuid.uuid4()), db=db)

    assert len(result["access_history"]) == 1


@pytest.mark.asyncio
async def test_access_history_paginates_filtered_entries_with_an_opaque_cursor():
    patient_id = str(uuid.uuid4())
    rows = [
        _provider_access_row(
            "legacy-provider",
            "",
            audit_id=str(uuid.uuid4()),
            created_at=f"2026-07-2{day}T10:00:00+00:00",
        )
        for day in (6, 5, 4)
    ]
    reader = AsyncMock(side_effect=[rows, [rows[2]]])
    db = AsyncMock()

    with patch(
        "app.api.v2.patient_record_routes.read_patient_access_history_events",
        new=reader,
    ):
        first = await get_my_access_history(limit=2, patient_id=patient_id, db=db)
        second = await get_my_access_history(
            limit=2,
            cursor=first["next_cursor"],
            patient_id=patient_id,
            db=db,
        )

    assert [entry["audit_id"] for entry in first["access_history"]] == [
        rows[0]["audit_id"],
        rows[1]["audit_id"],
    ]
    assert first["next_cursor"]
    assert [entry["audit_id"] for entry in second["access_history"]] == [
        rows[2]["audit_id"]
    ]
    assert second["next_cursor"] is None
    assert reader.await_args_list[0].kwargs["limit"] == 3
    assert reader.await_args_list[0].args[0] is db
    assert (
        reader.await_args_list[1].kwargs["cursor_created_at"] == rows[1]["created_at"]
    )
    assert reader.await_args_list[1].kwargs["cursor_audit_id"] == rows[1]["audit_id"]


@pytest.mark.asyncio
async def test_access_history_rejects_an_invalid_cursor():
    with pytest.raises(HTTPException) as exc:
        await get_my_access_history(
            cursor="not-a-valid-cursor",
            patient_id=str(uuid.uuid4()),
            db=AsyncMock(),
        )

    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_patient_access_history_store_failure_returns_503():
    db = AsyncMock()
    with patch(
        "app.api.v2.patient_record_routes.read_patient_access_history_events",
        new=AsyncMock(side_effect=RuntimeError()),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_my_access_history(patient_id=str(uuid.uuid4()), db=db)
    assert exc.value.status_code == 503
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_patient_access_history_statement_timeout_returns_503_and_rolls_back():
    db = AsyncMock()
    with patch(
        "app.api.v2.patient_record_routes.read_patient_access_history_events",
        new=AsyncMock(side_effect=TimeoutError()),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_my_access_history(patient_id=str(uuid.uuid4()), db=db)

    assert exc.value.status_code == 503
    assert exc.value.detail == {"error_code": "AUDIT_HISTORY_UNAVAILABLE"}
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_patient_access_history_reader_reuses_session_and_sets_statement_timeout():
    row = {"audit_id": str(uuid.uuid4()), "event_type": "PATIENT_RECORD_READ_SUCCESS"}
    db = AsyncMock()
    db.execute.side_effect = [_result(), _mapping_result(row)]

    result = await read_patient_access_history_events(
        db,
        str(uuid.uuid4()),
        limit=20,
    )

    assert result == [row]
    assert db.execute.await_count == 2
    timeout_statement = str(db.execute.await_args_list[0].args[0])
    assert "SET LOCAL statement_timeout = '3000ms'" in timeout_statement
    assert "FROM public.audit_ledger" in str(db.execute.await_args_list[1].args[0])


@pytest.mark.asyncio
async def test_admin_audit_store_failure_returns_503(admin_context):
    with patch(
        "app.api.v2.patient_record_routes.read_audit_events",
        new=AsyncMock(side_effect=RuntimeError()),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_patient_audit_trail(str(uuid.uuid4()), provider=admin_context)
    assert exc.value.status_code == 503
