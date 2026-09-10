"""Route-level qualification for patient registration invalid-OTP budgeting."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db_session
from app.main import app
from app.services.patient_registration_attempt_service import (
    RegistrationAttemptClaim,
    RegistrationAttemptError,
)
from app.services.patient_registration_service import PatientRegistrationError

client = TestClient(app)
PHONE = "+918000000001"
TOKEN = "opaque-registration-attempt-token"
CLAIM = RegistrationAttemptClaim("attempt-a", "claim-a")


def _allow_limits():
    return patch(
        "app.api.v2.auth_routes._otp_rate_limiter.check",
        new=AsyncMock(return_value=None),
    )


def _provider_error(status_code: int) -> RuntimeError:
    error = RuntimeError("provider diagnostics must remain private")
    error.status = status_code
    return error


def _body() -> dict[str, str]:
    return {
        "phone": "8000000001",
        "otp": "123456",
        "registration_attempt_token": TOKEN,
    }


@pytest.mark.parametrize("provider_status", [400, 401, 403])
def test_confirmed_invalid_provider_otp_charges_exact_claim_only(
    provider_status: int,
) -> None:
    db = AsyncMock()
    record_invalid = AsyncMock()
    release = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: db
    try:
        with (
            _allow_limits(),
            patch(
                "app.api.v2.auth_routes.claim_registration_attempt",
                new=AsyncMock(return_value=CLAIM),
            ),
            patch(
                "app.api.v2.auth_routes.recover_patient_registration_for_attempt",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.api.v2.auth_routes.get_supabase_client",
                return_value=SimpleNamespace(
                    auth=SimpleNamespace(
                        verify_otp=MagicMock(side_effect=_provider_error(provider_status))
                    )
                ),
            ),
            patch(
                "app.api.v2.auth_routes.record_registration_attempt_invalid_otp",
                new=record_invalid,
            ),
            patch(
                "app.api.v2.auth_routes.release_registration_attempt_claim",
                new=release,
            ),
        ):
            response = client.post("/api/v2/auth/register/otp/verify", json=_body())
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 401
    assert response.json()["detail"] == {"error_code": "REGISTRATION_OTP_INVALID"}
    assert "diagnostics" not in response.text.lower()
    record_invalid.assert_awaited_once_with(TOKEN, PHONE, CLAIM)
    release.assert_not_awaited()


def test_provider_service_failure_releases_claim_without_charging_budget() -> None:
    db = AsyncMock()
    record_invalid = AsyncMock()
    release = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: db
    try:
        with (
            _allow_limits(),
            patch(
                "app.api.v2.auth_routes.claim_registration_attempt",
                new=AsyncMock(return_value=CLAIM),
            ),
            patch(
                "app.api.v2.auth_routes.recover_patient_registration_for_attempt",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.api.v2.auth_routes.get_supabase_client",
                return_value=SimpleNamespace(
                    auth=SimpleNamespace(verify_otp=MagicMock(side_effect=_provider_error(503)))
                ),
            ),
            patch(
                "app.api.v2.auth_routes.record_registration_attempt_invalid_otp",
                new=record_invalid,
            ),
            patch(
                "app.api.v2.auth_routes.release_registration_attempt_claim",
                new=release,
            ),
        ):
            response = client.post("/api/v2/auth/register/otp/verify", json=_body())
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "error_code": "REGISTRATION_SMS_UNAVAILABLE",
        "retryable": True,
    }
    record_invalid.assert_not_awaited()
    release.assert_awaited_once_with(TOKEN, PHONE, CLAIM)


def test_registration_recovery_failure_releases_without_provider_or_budget_charge() -> None:
    db = AsyncMock()
    record_invalid = AsyncMock()
    release = AsyncMock()
    provider = MagicMock()
    app.dependency_overrides[get_db_session] = lambda: db
    try:
        with (
            _allow_limits(),
            patch(
                "app.api.v2.auth_routes.claim_registration_attempt",
                new=AsyncMock(return_value=CLAIM),
            ),
            patch(
                "app.api.v2.auth_routes.recover_patient_registration_for_attempt",
                new=AsyncMock(
                    side_effect=PatientRegistrationError("REGISTRATION_IDENTITY_UNAVAILABLE")
                ),
            ),
            patch("app.api.v2.auth_routes.get_supabase_client", return_value=provider),
            patch(
                "app.api.v2.auth_routes.record_registration_attempt_invalid_otp",
                new=record_invalid,
            ),
            patch(
                "app.api.v2.auth_routes.release_registration_attempt_claim",
                new=release,
            ),
        ):
            response = client.post("/api/v2/auth/register/otp/verify", json=_body())
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "error_code": "REGISTRATION_IDENTITY_UNAVAILABLE"
    }
    provider.auth.verify_otp.assert_not_called()
    record_invalid.assert_not_awaited()
    release.assert_awaited_once_with(TOKEN, PHONE, CLAIM)


def test_invalid_otp_counter_storage_failure_fails_closed_without_free_release() -> None:
    db = AsyncMock()
    release = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: db
    try:
        with (
            _allow_limits(),
            patch(
                "app.api.v2.auth_routes.claim_registration_attempt",
                new=AsyncMock(return_value=CLAIM),
            ),
            patch(
                "app.api.v2.auth_routes.recover_patient_registration_for_attempt",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.api.v2.auth_routes.get_supabase_client",
                return_value=SimpleNamespace(
                    auth=SimpleNamespace(verify_otp=MagicMock(side_effect=_provider_error(401)))
                ),
            ),
            patch(
                "app.api.v2.auth_routes.record_registration_attempt_invalid_otp",
                new=AsyncMock(
                    side_effect=RegistrationAttemptError(
                        "REGISTRATION_ATTEMPT_UNAVAILABLE"
                    )
                ),
            ),
            patch(
                "app.api.v2.auth_routes.release_registration_attempt_claim",
                new=release,
            ),
        ):
            response = client.post("/api/v2/auth/register/otp/verify", json=_body())
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "error_code": "REGISTRATION_ATTEMPT_UNAVAILABLE",
        "retryable": True,
    }
    release.assert_not_awaited()
