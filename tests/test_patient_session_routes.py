from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.core.dependencies import AuthenticatedPatientSession, get_current_patient_session
from app.main import app
from app.services.patient_session_authority import PatientSessionAuthorityUnavailable

client = TestClient(app)
PATIENT_ID = "123e4567-e89b-12d3-a456-426614174001"
SESSION_ID = "test-current-patient-session"


def _principal() -> AuthenticatedPatientSession:
    return AuthenticatedPatientSession(
        patient_id=PATIENT_ID,
        patient=SimpleNamespace(patient_uuid=PATIENT_ID, is_deleted=False),
        session_id=SESSION_ID,
        session_epoch=3,
        supabase_user_id="supabase-subject",
    )


def _override_current_session() -> None:
    async def _dep():
        yield _principal()

    app.dependency_overrides[get_current_patient_session] = _dep


def _clear_override() -> None:
    app.dependency_overrides.pop(get_current_patient_session, None)


def test_patient_logout_revokes_exact_current_session() -> None:
    _override_current_session()
    revoke = AsyncMock(return_value=True)
    try:
        with (
            patch("app.api.v2.auth_routes.revoke_patient_session", new=revoke),
            patch("app.api.v2.auth_routes.append_audit_log", new=AsyncMock()),
            patch("app.api.v2.auth_routes.current_audit_context", return_value=object()),
        ):
            response = client.post("/api/v2/auth/patient/logout")
    finally:
        _clear_override()

    assert response.status_code == 204
    revoke.assert_awaited_once_with(patient_id=PATIENT_ID, session_id=SESSION_ID)


def test_patient_logout_rejects_already_inactive_session() -> None:
    _override_current_session()
    try:
        with patch(
            "app.api.v2.auth_routes.revoke_patient_session",
            new=AsyncMock(return_value=False),
        ):
            response = client.post("/api/v2/auth/patient/logout")
    finally:
        _clear_override()

    assert response.status_code == 401
    assert response.json()["detail"] == "Patient session is no longer active"


def test_patient_logout_fails_closed_when_session_store_is_unavailable() -> None:
    _override_current_session()
    try:
        with patch(
            "app.api.v2.auth_routes.revoke_patient_session",
            new=AsyncMock(side_effect=PatientSessionAuthorityUnavailable("redis down")),
        ):
            response = client.post("/api/v2/auth/patient/logout")
    finally:
        _clear_override()

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "error_code": "PATIENT_SESSION_AUTHORITY_UNAVAILABLE",
        "retryable": True,
    }


def test_patient_logout_all_advances_patient_session_epoch() -> None:
    _override_current_session()
    revoke_all = AsyncMock(return_value=4)
    audit = AsyncMock()
    try:
        with (
            patch("app.api.v2.auth_routes.revoke_all_patient_sessions", new=revoke_all),
            patch("app.api.v2.auth_routes.append_audit_log", new=audit),
            patch("app.api.v2.auth_routes.current_audit_context", return_value=object()),
        ):
            response = client.post("/api/v2/auth/patient/logout-all")
    finally:
        _clear_override()

    assert response.status_code == 204
    revoke_all.assert_awaited_once_with(PATIENT_ID)
    assert audit.await_args.kwargs["event_type"] == "PATIENT_SESSIONS_REVOKED"
    assert audit.await_args.kwargs["metadata"] == {
        "scope": "all_sessions",
        "session_epoch": 4,
    }


def test_patient_logout_all_fails_closed_when_session_store_is_unavailable() -> None:
    _override_current_session()
    try:
        with patch(
            "app.api.v2.auth_routes.revoke_all_patient_sessions",
            new=AsyncMock(side_effect=PatientSessionAuthorityUnavailable("redis down")),
        ):
            response = client.post("/api/v2/auth/patient/logout-all")
    finally:
        _clear_override()

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "PATIENT_SESSION_AUTHORITY_UNAVAILABLE"
