"""Qualification tests for bootstrap device-enrollment grant refresh."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

from fastapi.testclient import TestClient

from app.core.database import get_db_session
from app.core.dependencies import AuthenticatedPatientSession, get_current_patient_session
from app.main import app
from app.services.patient_auth_service import DEVICE_ENROLLMENT_TTL_SECONDS
from app.services.patient_session_authority import PatientSessionAuthorityUnavailable

client = TestClient(app)
PATIENT_ID = "123e4567-e89b-12d3-a456-426614174001"
SESSION_ID = "session-enrollment-refresh-1234567890"
SUBJECT = "supabase-subject-enrollment-refresh"


async def _patient_dependency():
    yield AuthenticatedPatientSession(
        patient_id=PATIENT_ID,
        patient=SimpleNamespace(patient_uuid=UUID(PATIENT_ID), is_deleted=False),
        session_id=SESSION_ID,
        session_epoch=2,
        supabase_user_id=SUBJECT,
    )


def _db_override():
    db = AsyncMock()
    db.rollback = AsyncMock()
    return db


def test_authenticated_patient_without_device_history_can_refresh_bootstrap_grant():
    db = _db_override()
    app.dependency_overrides[get_current_patient_session] = _patient_dependency
    app.dependency_overrides[get_db_session] = lambda: db
    try:
        with (
            patch(
                "app.api.v2.device_routes.patient_has_device_history",
                new=AsyncMock(return_value=False),
            ) as history,
            patch(
                "app.api.v2.device_routes.issue_device_enrollment_token",
                new=AsyncMock(return_value="fresh-device-enrollment-token"),
            ) as issue,
        ):
            response = client.post("/api/v2/patient/devices/enrollment-token")
    finally:
        app.dependency_overrides.pop(get_current_patient_session, None)
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 201, response.text
    assert response.json() == {
        "device_enrollment_token": "fresh-device-enrollment-token",
        "expires_in_seconds": DEVICE_ENROLLMENT_TTL_SECONDS,
        "device_authority_state": "bootstrap_enrollment",
    }
    history.assert_awaited_once_with(db, patient_id=UUID(PATIENT_ID))
    db.rollback.assert_awaited_once()
    issue.assert_awaited_once_with(PATIENT_ID, SESSION_ID)


def test_device_history_blocks_bootstrap_grant_refresh_and_routes_to_recovery():
    db = _db_override()
    app.dependency_overrides[get_current_patient_session] = _patient_dependency
    app.dependency_overrides[get_db_session] = lambda: db
    try:
        with (
            patch(
                "app.api.v2.device_routes.patient_has_device_history",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.api.v2.device_routes.append_audit_log_or_503",
                new=AsyncMock(return_value=None),
            ) as audit,
            patch(
                "app.api.v2.device_routes.issue_device_enrollment_token",
                new=AsyncMock(),
            ) as issue,
        ):
            response = client.post("/api/v2/patient/devices/enrollment-token")
    finally:
        app.dependency_overrides.pop(get_current_patient_session, None)
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 409
    assert response.json()["detail"] == {"error_code": "DEVICE_RECOVERY_REQUIRED"}
    issue.assert_not_awaited()
    db.rollback.assert_not_awaited()
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["metadata"] == {
        "operation": "bootstrap_enrollment_grant_refresh",
        "reason_code": "DEVICE_RECOVERY_REQUIRED",
    }


def test_bootstrap_grant_refresh_fails_closed_when_session_authority_is_unavailable():
    db = _db_override()
    app.dependency_overrides[get_current_patient_session] = _patient_dependency
    app.dependency_overrides[get_db_session] = lambda: db
    try:
        with (
            patch(
                "app.api.v2.device_routes.patient_has_device_history",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.api.v2.device_routes.issue_device_enrollment_token",
                new=AsyncMock(
                    side_effect=PatientSessionAuthorityUnavailable("redis unavailable")
                ),
            ),
        ):
            response = client.post("/api/v2/patient/devices/enrollment-token")
    finally:
        app.dependency_overrides.pop(get_current_patient_session, None)
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "error_code": "PATIENT_SESSION_AUTHORITY_UNAVAILABLE",
        "retryable": True,
    }
    db.rollback.assert_awaited_once()


def test_bootstrap_grant_refresh_requires_patient_authentication():
    response = client.post("/api/v2/patient/devices/enrollment-token")
    assert response.status_code in {401, 403}
