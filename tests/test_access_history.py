"""Access-history results remain authoritative and fail closed."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.v2.patient_record_routes import (
    get_my_access_history,
    get_patient_audit_trail,
)


@pytest.mark.asyncio
async def test_missing_audit_attributes_remain_null():
    row = {"event_type": "PATIENT_RECORD_READ_SUCCESS", "actor_uid": "provider-id"}
    with patch(
        "app.api.v2.patient_record_routes.read_audit_events",
        new=AsyncMock(return_value=[row]),
    ):
        result = await get_my_access_history(patient_id=str(uuid.uuid4()))
    event = result["access_history"][0]
    assert event["audit_id"] is None
    assert event["hospital_name"] is None
    assert event["accessed_at"] is None
    assert event["accessed_by"] == "provider-id"


@pytest.mark.asyncio
async def test_break_glass_is_derived_only_from_audit_evidence():
    row = {
        "event_type": "BREAK_GLASS_ACCESS",
        "actor_uid": "provider-id",
        "metadata": {"purpose": "EMERGENCY", "scope": ["clinical"]},
    }
    with patch(
        "app.api.v2.patient_record_routes.read_audit_events",
        new=AsyncMock(return_value=[row]),
    ):
        result = await get_my_access_history(patient_id=str(uuid.uuid4()))
    assert result["access_history"][0]["is_break_glass"] is True


@pytest.mark.asyncio
async def test_patient_access_history_store_failure_returns_503():
    with patch(
        "app.api.v2.patient_record_routes.read_audit_events",
        new=AsyncMock(side_effect=RuntimeError()),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_my_access_history(patient_id=str(uuid.uuid4()))
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_admin_audit_store_failure_returns_503(admin_context):
    with patch(
        "app.api.v2.patient_record_routes.read_audit_events",
        new=AsyncMock(side_effect=RuntimeError()),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_patient_audit_trail(str(uuid.uuid4()), provider=admin_context)
    assert exc.value.status_code == 503
