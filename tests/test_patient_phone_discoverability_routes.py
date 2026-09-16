"""Patient-self qualification for opt-in phone discoverability."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db_session
from app.core.dependencies import AuthenticatedPatientSession, get_current_patient_session
from app.core.rate_limiter import OtpRateLimitBackendUnavailable
from app.main import app
from app.services.patient_phone_discoverability_service import (
    PatientPhoneDiscoverabilityError,
    PhoneDiscoverabilityState,
)

PATIENT_ID = "123e4567-e89b-12d3-a456-426614174001"
SUBJECT = "supabase-subject-1"
PHONE = "+918000000001"


@pytest.fixture
def auth() -> AuthenticatedPatientSession:
    return AuthenticatedPatientSession(
        patient_id=PATIENT_ID,
        patient=SimpleNamespace(patient_uuid=UUID(PATIENT_ID), is_deleted=False),
        session_id="patient-session-1234567890",
        session_epoch=1,
        supabase_user_id=SUBJECT,
    )


@pytest.fixture
def client(auth: AuthenticatedPatientSession):
    db = AsyncMock()
    app.dependency_overrides[get_current_patient_session] = lambda: auth
    app.dependency_overrides[get_db_session] = lambda: db
    try:
        yield TestClient(app), db
    finally:
        app.dependency_overrides.clear()


def _provider_result(*, phone: str = PHONE, subject: str = SUBJECT):
    return SimpleNamespace(
        user=SimpleNamespace(phone=phone, id=subject),
        session=SimpleNamespace(access_token="provider-access-token"),
    )


def test_status_discloses_only_boolean(client) -> None:
    http, _db = client
    with patch(
        "app.api.v2.patient_self_routes.get_phone_discoverability_state",
        new=AsyncMock(return_value=PhoneDiscoverabilityState(enabled=True)),
    ):
        response = http.get("/api/v2/patient/me/discoverability/phone")
    assert response.status_code == 200
    assert response.json() == {"enabled": True}
    assert PHONE not in response.text
    assert SUBJECT not in response.text
    assert PATIENT_ID not in response.text


def test_enable_requires_fresh_same_subject_phone_proof_and_commits(client) -> None:
    http, db = client
    auth_client = MagicMock()
    auth_client.verify_otp.return_value = _provider_result()
    with (
        patch(
            "app.api.v2.patient_self_routes._phone_discoverability_otp_limiter.check",
            new=AsyncMock(return_value=None),
        ) as limiter,
        patch(
            "app.api.v2.patient_self_routes.get_supabase_client",
            return_value=SimpleNamespace(auth=auth_client),
        ),
        patch(
            "app.api.v2.patient_self_routes.enable_phone_discoverability",
            new=AsyncMock(return_value=PhoneDiscoverabilityState(enabled=True)),
        ) as enable,
    ):
        response = http.post(
            "/api/v2/patient/me/discoverability/phone/enable",
            json={"phone": "8000000001", "otp": "123456"},
        )
    assert response.status_code == 200, response.text
    assert response.json() == {"enabled": True}
    assert PHONE not in response.text
    limiter.assert_awaited_once()
    auth_client.verify_otp.assert_called_once_with(
        {"phone": PHONE, "token": "123456", "type": "sms"}
    )
    enable.assert_awaited_once_with(
        db,
        patient_id=UUID(PATIENT_ID),
        provider_subject=SUBJECT,
        verified_phone=PHONE,
    )
    db.commit.assert_awaited_once()


@pytest.mark.parametrize(
    "result",
    [
        _provider_result(subject="different-subject"),
        _provider_result(phone="+919000000001"),
        SimpleNamespace(user=None, session=None),
    ],
)
def test_enable_rejects_provider_subject_or_phone_mismatch_without_mutation(
    client, result
) -> None:
    http, _db = client
    auth_client = MagicMock()
    auth_client.verify_otp.return_value = result
    with (
        patch(
            "app.api.v2.patient_self_routes._phone_discoverability_otp_limiter.check",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.api.v2.patient_self_routes.get_supabase_client",
            return_value=SimpleNamespace(auth=auth_client),
        ),
        patch(
            "app.api.v2.patient_self_routes.enable_phone_discoverability",
            new=AsyncMock(),
        ) as enable,
    ):
        response = http.post(
            "/api/v2/patient/me/discoverability/phone/enable",
            json={"phone": PHONE, "otp": "123456"},
        )
    assert response.status_code == 401
    assert response.json()["detail"]["error_code"] == (
        "PHONE_DISCOVERABILITY_VERIFICATION_FAILED"
    )
    assert PHONE not in response.text
    enable.assert_not_awaited()


def test_security_control_failure_denies_before_provider_verification(client) -> None:
    http, _db = client
    auth_client = MagicMock()
    with (
        patch(
            "app.api.v2.patient_self_routes._phone_discoverability_otp_limiter.check",
            new=AsyncMock(
                side_effect=OtpRateLimitBackendUnavailable("redis diagnostics")
            ),
        ),
        patch(
            "app.api.v2.patient_self_routes.get_supabase_client",
            return_value=SimpleNamespace(auth=auth_client),
        ),
    ):
        response = http.post(
            "/api/v2/patient/me/discoverability/phone/enable",
            json={"phone": PHONE, "otp": "123456"},
        )
    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == (
        "PHONE_DISCOVERABILITY_SECURITY_CONTROL_UNAVAILABLE"
    )
    assert "diagnostics" not in response.text
    auth_client.verify_otp.assert_not_called()


def test_conflict_persists_quarantine_but_returns_no_cross_account_detail(client) -> None:
    http, db = client
    auth_client = MagicMock()
    auth_client.verify_otp.return_value = _provider_result()
    with (
        patch(
            "app.api.v2.patient_self_routes._phone_discoverability_otp_limiter.check",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.api.v2.patient_self_routes.get_supabase_client",
            return_value=SimpleNamespace(auth=auth_client),
        ),
        patch(
            "app.api.v2.patient_self_routes.enable_phone_discoverability",
            new=AsyncMock(
                side_effect=PatientPhoneDiscoverabilityError(
                    "PHONE_DISCOVERABILITY_CONFLICT"
                )
            ),
        ),
    ):
        response = http.post(
            "/api/v2/patient/me/discoverability/phone/enable",
            json={"phone": PHONE, "otp": "123456"},
        )
    assert response.status_code == 409
    assert PHONE not in response.text
    assert PATIENT_ID not in response.text
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()


def test_disable_is_idempotent_server_authority_and_commits(client) -> None:
    http, db = client
    with patch(
        "app.api.v2.patient_self_routes.disable_phone_discoverability",
        new=AsyncMock(return_value=PhoneDiscoverabilityState(enabled=False)),
    ) as disable:
        response = http.delete("/api/v2/patient/me/discoverability/phone")
    assert response.status_code == 200
    assert response.json() == {"enabled": False}
    disable.assert_awaited_once_with(
        db,
        patient_id=UUID(PATIENT_ID),
        provider_subject=SUBJECT,
    )
    db.commit.assert_awaited_once()
