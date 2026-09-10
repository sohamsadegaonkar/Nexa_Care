"""Focused route contracts for explicit patient registration recovery."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.core.database import get_db_session
from app.main import app
from app.services.patient_registration_attempt_service import RegistrationAttemptClaim
from app.services.patient_registration_service import (
    REGISTRATION_RECOVERY_REQUIRED,
    PatientRegistrationError,
)


client = TestClient(app)
PHONE = "+918000000001"
PATIENT_ID = "123e4567-e89b-12d3-a456-426614174001"


def _allow_limits():
    return patch(
        "app.api.v2.auth_routes._otp_rate_limiter.check",
        new=AsyncMock(return_value=None),
    )


def _provider_result():
    return SimpleNamespace(
        user=SimpleNamespace(phone=PHONE, id="subject-recovery"),
        session=SimpleNamespace(access_token="provider-session-token"),
    )


def _request_body() -> dict[str, str]:
    return {
        "phone": "8000000001",
        "otp": "123456",
        "registration_attempt_token": "registration-attempt-token",
    }


def test_finalized_attempt_recovery_conflict_never_issues_authority() -> None:
    db = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: db
    try:
        with (
            _allow_limits(),
            patch(
                "app.api.v2.auth_routes.claim_registration_attempt",
                new=AsyncMock(
                    return_value=RegistrationAttemptClaim(
                        "attempt-finalized", None, PATIENT_ID
                    )
                ),
            ),
            patch(
                "app.api.v2.auth_routes.recover_patient_registration_for_attempt",
                new=AsyncMock(
                    side_effect=PatientRegistrationError(
                        REGISTRATION_RECOVERY_REQUIRED
                    )
                ),
            ),
            patch("app.api.v2.auth_routes.get_supabase_client") as provider,
            patch(
                "app.api.v2.auth_routes.issue_patient_access_session",
                new=AsyncMock(),
            ) as issue_access,
            patch(
                "app.api.v2.auth_routes.issue_device_enrollment_token",
                new=AsyncMock(),
            ) as issue_enrollment,
        ):
            response = client.post(
                "/api/v2/auth/register/otp/verify", json=_request_body()
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "error_code": REGISTRATION_RECOVERY_REQUIRED
    }
    provider.assert_not_called()
    issue_access.assert_not_awaited()
    issue_enrollment.assert_not_awaited()


def test_pending_attempt_durable_recovery_conflict_releases_claim_and_stops() -> None:
    db = AsyncMock()
    claim = RegistrationAttemptClaim("attempt-pending", "claim-pending")
    app.dependency_overrides[get_db_session] = lambda: db
    try:
        with (
            _allow_limits(),
            patch(
                "app.api.v2.auth_routes.claim_registration_attempt",
                new=AsyncMock(return_value=claim),
            ),
            patch(
                "app.api.v2.auth_routes.recover_patient_registration_for_attempt",
                new=AsyncMock(
                    side_effect=PatientRegistrationError(
                        REGISTRATION_RECOVERY_REQUIRED
                    )
                ),
            ),
            patch(
                "app.api.v2.auth_routes.release_registration_attempt_claim",
                new=AsyncMock(),
            ) as release,
            patch("app.api.v2.auth_routes.get_supabase_client") as provider,
            patch(
                "app.api.v2.auth_routes.issue_patient_access_session",
                new=AsyncMock(),
            ) as issue_access,
        ):
            response = client.post(
                "/api/v2/auth/register/otp/verify", json=_request_body()
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "error_code": REGISTRATION_RECOVERY_REQUIRED
    }
    release.assert_awaited_once_with(
        "registration-attempt-token", PHONE, claim
    )
    provider.assert_not_called()
    issue_access.assert_not_awaited()


def test_post_otp_historical_graph_conflict_releases_claim_and_never_logs_in() -> None:
    db = AsyncMock()
    claim = RegistrationAttemptClaim("attempt-post-otp", "claim-post-otp")
    app.dependency_overrides[get_db_session] = lambda: db
    try:
        with (
            _allow_limits(),
            patch(
                "app.api.v2.auth_routes.claim_registration_attempt",
                new=AsyncMock(return_value=claim),
            ),
            patch(
                "app.api.v2.auth_routes.recover_patient_registration_for_attempt",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.api.v2.auth_routes.get_supabase_client",
                return_value=SimpleNamespace(
                    auth=SimpleNamespace(
                        verify_otp=MagicMock(return_value=_provider_result())
                    )
                ),
            ),
            patch(
                "app.api.v2.auth_routes.finalize_patient_registration",
                new=AsyncMock(
                    side_effect=PatientRegistrationError(
                        REGISTRATION_RECOVERY_REQUIRED
                    )
                ),
            ) as finalize,
            patch(
                "app.api.v2.auth_routes.release_registration_attempt_claim",
                new=AsyncMock(),
            ) as release,
            patch(
                "app.api.v2.auth_routes.issue_patient_access_session",
                new=AsyncMock(),
            ) as issue_access,
            patch(
                "app.api.v2.auth_routes.issue_device_enrollment_token",
                new=AsyncMock(),
            ) as issue_enrollment,
        ):
            response = client.post(
                "/api/v2/auth/register/otp/verify", json=_request_body()
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "error_code": REGISTRATION_RECOVERY_REQUIRED
    }
    db.rollback.assert_awaited_once()
    finalize.assert_awaited_once_with(
        db,
        provider_subject="subject-recovery",
        attempt_id="attempt-post-otp",
    )
    release.assert_awaited_once_with(
        "registration-attempt-token", PHONE, claim
    )
    issue_access.assert_not_awaited()
    issue_enrollment.assert_not_awaited()
