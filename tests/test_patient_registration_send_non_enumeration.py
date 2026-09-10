"""Security qualification for patient registration OTP-send non-enumeration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
PHONE = "+918000000001"
NEUTRAL_BODY = {
    "message": "If eligible, an OTP will be sent.",
    "registration_attempt_token": "opaque-registration-attempt",
}


def _allow_limits():
    return patch(
        "app.api.v2.auth_routes._otp_rate_limiter.check",
        new=AsyncMock(return_value=None),
    )


def _provider_error(code: int | None, *, attribute: str = "status") -> RuntimeError:
    error = RuntimeError("provider account diagnostics must remain private")
    if code is not None:
        setattr(error, attribute, code)
    return error


@pytest.mark.parametrize(
    ("attribute", "provider_status"),
    [
        ("status", 400),
        ("status", 401),
        ("status", 403),
        ("status_code", 422),
    ],
)
def test_registration_send_neutralizes_expected_provider_eligibility_outcomes(
    attribute: str, provider_status: int
) -> None:
    """Expected provider identity outcomes must not reveal account existence."""

    auth = MagicMock()
    auth.sign_in_with_otp.side_effect = _provider_error(
        provider_status, attribute=attribute
    )
    issue_attempt = AsyncMock(return_value="opaque-registration-attempt")

    with (
        _allow_limits(),
        patch(
            "app.api.v2.auth_routes.get_supabase_client",
            return_value=SimpleNamespace(auth=auth),
        ),
        patch(
            "app.api.v2.auth_routes.issue_registration_attempt",
            new=issue_attempt,
        ),
    ):
        response = client.post(
            "/api/v2/auth/register/otp/send",
            json={"phone": "91 80000 00001"},
        )

    assert response.status_code == 200
    assert response.json() == NEUTRAL_BODY
    assert "diagnostics" not in response.text.lower()
    auth.sign_in_with_otp.assert_called_once_with(
        {"phone": PHONE, "options": {"should_create_user": True}}
    )
    issue_attempt.assert_awaited_once_with(PHONE)


@pytest.mark.parametrize("provider_status", [404, 429, 500, 503, None])
def test_registration_send_fails_closed_on_non_eligibility_provider_failures(
    provider_status: int | None,
) -> None:
    """Network/service failures remain generic 503s and mint no attempt authority."""

    auth = MagicMock()
    auth.sign_in_with_otp.side_effect = _provider_error(provider_status)
    issue_attempt = AsyncMock(return_value="must-not-be-issued")

    with (
        _allow_limits(),
        patch(
            "app.api.v2.auth_routes.get_supabase_client",
            return_value=SimpleNamespace(auth=auth),
        ),
        patch(
            "app.api.v2.auth_routes.issue_registration_attempt",
            new=issue_attempt,
        ),
    ):
        response = client.post(
            "/api/v2/auth/register/otp/send",
            json={"phone": "8000000001"},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "error_code": "REGISTRATION_SMS_UNAVAILABLE",
        "retryable": True,
    }
    assert "diagnostics" not in response.text.lower()
    issue_attempt.assert_not_awaited()
