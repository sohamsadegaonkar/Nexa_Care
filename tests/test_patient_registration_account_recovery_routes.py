"""Focused route contracts for patient-facing registration-account recovery."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v2.registration_recovery_routes import router
from app.core.database import get_db_session
from app.services.patient_registration_recovery_authority import (
    REGISTRATION_RECOVERY_OPERATION,
    RegistrationRecoveryAttemptClaim,
    RegistrationRecoveryCapability,
    RegistrationRecoveryCapabilityError,
)
from app.services.patient_registration_recovery_service import (
    REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED,
    REGISTRATION_RECOVERY_NOT_REQUIRED,
    REGISTRATION_RECOVERY_STATE_CHANGED,
    PatientRegistrationRecoveryError,
    REPAIR_RESTORE_RECORD,
    RegistrationRecoveryInspection,
    RegistrationRecoveryResult,
)
from app.services.patient_session_authority import PatientSessionAuthorityUnavailable


PHONE = "+918000000001"
PATIENT_ID = "123e4567-e89b-12d3-a456-426614174001"
ATTEMPT = RegistrationRecoveryAttemptClaim("attempt-recovery", "claim-recovery")


def _client(db: AsyncMock | None = None) -> tuple[TestClient, FastAPI]:
    app = FastAPI()
    app.include_router(router)
    if db is not None:
        app.dependency_overrides[get_db_session] = lambda: db
    return TestClient(app), app


def _allow_limits():
    return patch(
        "app.api.v2.registration_recovery_routes._otp_limiter.check",
        new=AsyncMock(return_value=None),
    )


def _provider_result(subject: str = "subject-recovery"):
    return SimpleNamespace(
        user=SimpleNamespace(phone=PHONE, id=subject),
        session=SimpleNamespace(access_token="provider-token"),
    )


def _repairable() -> RegistrationRecoveryInspection:
    return RegistrationRecoveryInspection(
        disposition="repairable",
        provider_subject="subject-recovery",
        patient_id=PATIENT_ID,
        target_patient_id=PATIENT_ID,
        graph_fingerprint="graph-fingerprint",
        repair_kind=REPAIR_RESTORE_RECORD,
        reason_code="PATIENT_RECORD_ANCHOR_MISSING",
    )


def _capability() -> RegistrationRecoveryCapability:
    return RegistrationRecoveryCapability(
        token="repair-capability-token",
        patient_id=PATIENT_ID,
        provider_subject="subject-recovery",
        repair_kind=REPAIR_RESTORE_RECORD,
        graph_fingerprint="graph-fingerprint",
        issued_at="2026-09-10T00:00:00+00:00",
        expires_at="2026-09-10T00:05:00+00:00",
    )


def _verify_body() -> dict[str, str]:
    return {
        "phone": "8000000001",
        "otp": "123456",
        "registration_recovery_attempt_token": "a" * 40,
    }


def test_recovery_send_is_neutral_for_expected_provider_rejection() -> None:
    client, _ = _client()
    provider_error = RuntimeError("private upstream diagnostic")
    provider_error.status = 422
    with (
        _allow_limits(),
        patch(
            "app.api.v2.registration_recovery_routes.get_supabase_client",
            return_value=SimpleNamespace(
                auth=SimpleNamespace(sign_in_with_otp=MagicMock(side_effect=provider_error))
            ),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.issue_registration_recovery_attempt",
            new=AsyncMock(return_value="opaque-recovery-attempt-token"),
        ),
    ):
        response = client.post(
            "/api/v2/auth/registration-recovery/otp/send",
            json={"phone": "8000000001"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "message": "If this identity is eligible for account recovery, an OTP will be sent.",
        "registration_recovery_attempt_token": "opaque-recovery-attempt-token",
    }
    assert "diagnostic" not in response.text.lower()


def test_invalid_recovery_otp_charges_budget_and_never_releases_claim() -> None:
    db = AsyncMock()
    client, _ = _client(db)
    provider_error = RuntimeError("invalid OTP private detail")
    provider_error.status = 401
    record_invalid = AsyncMock()
    release = AsyncMock()
    with (
        _allow_limits(),
        patch(
            "app.api.v2.registration_recovery_routes.claim_registration_recovery_attempt",
            new=AsyncMock(return_value=ATTEMPT),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.get_supabase_client",
            return_value=SimpleNamespace(
                auth=SimpleNamespace(verify_otp=MagicMock(side_effect=provider_error))
            ),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.record_registration_recovery_invalid_otp",
            new=record_invalid,
        ),
        patch(
            "app.api.v2.registration_recovery_routes.release_registration_recovery_claim",
            new=release,
        ),
    ):
        response = client.post(
            "/api/v2/auth/registration-recovery/otp/verify", json=_verify_body()
        )

    assert response.status_code == 401
    assert response.json()["detail"] == {"error_code": "REGISTRATION_RECOVERY_OTP_INVALID"}
    record_invalid.assert_awaited_once_with("a" * 40, PHONE, ATTEMPT)
    release.assert_not_awaited()


def test_repairable_verified_identity_exchanges_attempt_for_exact_repair_capability() -> None:
    db = AsyncMock()
    client, _ = _client(db)
    audit_required = AsyncMock()
    consume_attempt = AsyncMock()
    issue_access = AsyncMock()
    exchange = AsyncMock(return_value=_capability())
    with (
        _allow_limits(),
        patch(
            "app.api.v2.registration_recovery_routes.claim_registration_recovery_attempt",
            new=AsyncMock(return_value=ATTEMPT),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.get_supabase_client",
            return_value=SimpleNamespace(
                auth=SimpleNamespace(verify_otp=MagicMock(return_value=_provider_result()))
            ),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.inspect_patient_registration_recovery",
            new=AsyncMock(return_value=_repairable()),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.audit_registration_recovery_required",
            new=audit_required,
        ),
        patch(
            "app.api.v2.registration_recovery_routes.consume_registration_recovery_attempt",
            new=consume_attempt,
        ),
        patch(
            "app.api.v2.registration_recovery_routes.exchange_registration_recovery_attempt_for_capability",
            new=exchange,
        ),
        patch(
            "app.api.v2.registration_recovery_routes.issue_patient_access_session",
            new=issue_access,
        ),
    ):
        response = client.post(
            "/api/v2/auth/registration-recovery/otp/verify", json=_verify_body()
        )

    assert response.status_code == 201
    body = response.json()
    assert body["registration_recovery_token"] == "repair-capability-token"
    assert body["operation"] == REGISTRATION_RECOVERY_OPERATION
    assert body["repair_kind"] == REPAIR_RESTORE_RECORD
    audit_required.assert_awaited_once()
    db.commit.assert_awaited_once()
    consume_attempt.assert_not_awaited()
    exchange.assert_awaited_once_with(
        attempt_token="a" * 40,
        phone=PHONE,
        claim=ATTEMPT,
        patient_id=PATIENT_ID,
        provider_subject="subject-recovery",
        repair_kind=REPAIR_RESTORE_RECORD,
        graph_fingerprint="graph-fingerprint",
    )
    issue_access.assert_not_awaited()


def test_manual_review_state_returns_support_reference_and_no_repair_authority() -> None:
    db = AsyncMock()
    client, _ = _client(db)
    inspection = RegistrationRecoveryInspection(
        disposition="manual_review",
        provider_subject="subject-recovery",
        patient_id=PATIENT_ID,
        target_patient_id=PATIENT_ID,
        graph_fingerprint="manual-graph",
        reason_code="IDENTITY_REVOKED",
    )
    exchange = AsyncMock()
    issue_access = AsyncMock()
    consume_attempt = AsyncMock()
    with (
        _allow_limits(),
        patch(
            "app.api.v2.registration_recovery_routes.claim_registration_recovery_attempt",
            new=AsyncMock(return_value=ATTEMPT),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.get_supabase_client",
            return_value=SimpleNamespace(
                auth=SimpleNamespace(verify_otp=MagicMock(return_value=_provider_result()))
            ),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.inspect_patient_registration_recovery",
            new=AsyncMock(return_value=inspection),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.audit_registration_recovery_required",
            new=AsyncMock(),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.consume_registration_recovery_attempt",
            new=consume_attempt,
        ),
        patch(
            "app.api.v2.registration_recovery_routes.exchange_registration_recovery_attempt_for_capability",
            new=exchange,
        ),
        patch(
            "app.api.v2.registration_recovery_routes.issue_patient_access_session",
            new=issue_access,
        ),
    ):
        response = client.post(
            "/api/v2/auth/registration-recovery/otp/verify", json=_verify_body()
        )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error_code"] == REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED
    assert detail["recovery_reference"].startswith("RR-")
    assert "case_reference" not in detail
    consume_attempt.assert_awaited_once_with("a" * 40, PHONE, ATTEMPT)
    exchange.assert_not_awaited()
    issue_access.assert_not_awaited()


def test_not_required_consumes_attempt_and_issues_no_repair_or_login_authority() -> None:
    db = AsyncMock()
    client, _ = _client(db)
    inspection = RegistrationRecoveryInspection(
        disposition="not_required",
        provider_subject="subject-recovery",
        patient_id=PATIENT_ID,
        target_patient_id=PATIENT_ID,
        graph_fingerprint="complete-graph",
    )
    consume_attempt = AsyncMock()
    exchange = AsyncMock()
    issue_access = AsyncMock()
    with (
        _allow_limits(),
        patch(
            "app.api.v2.registration_recovery_routes.claim_registration_recovery_attempt",
            new=AsyncMock(return_value=ATTEMPT),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.get_supabase_client",
            return_value=SimpleNamespace(
                auth=SimpleNamespace(verify_otp=MagicMock(return_value=_provider_result()))
            ),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.inspect_patient_registration_recovery",
            new=AsyncMock(return_value=inspection),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.consume_registration_recovery_attempt",
            new=consume_attempt,
        ),
        patch(
            "app.api.v2.registration_recovery_routes.exchange_registration_recovery_attempt_for_capability",
            new=exchange,
        ),
        patch(
            "app.api.v2.registration_recovery_routes.issue_patient_access_session",
            new=issue_access,
        ),
    ):
        response = client.post(
            "/api/v2/auth/registration-recovery/otp/verify", json=_verify_body()
        )

    assert response.status_code == 409
    assert response.json()["detail"] == {"error_code": REGISTRATION_RECOVERY_NOT_REQUIRED}
    consume_attempt.assert_awaited_once_with("a" * 40, PHONE, ATTEMPT)
    exchange.assert_not_awaited()
    issue_access.assert_not_awaited()


def test_complete_repair_with_device_history_never_mints_device_authority() -> None:
    db = AsyncMock()
    client, _ = _client(db)
    issue_device = AsyncMock()
    with (
        patch(
            "app.api.v2.registration_recovery_routes.consume_registration_recovery_capability",
            new=AsyncMock(return_value=_capability()),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.repair_patient_registration_account",
            new=AsyncMock(
                return_value=RegistrationRecoveryResult(
                    patient_id=PATIENT_ID,
                    provider_subject="subject-recovery",
                    repair_kind=REPAIR_RESTORE_RECORD,
                )
            ),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.issue_patient_access_session",
            new=AsyncMock(
                return_value=(
                    "patient-access-token",
                    SimpleNamespace(isoformat=lambda: "2026-09-10T00:15:00+00:00"),
                    "session-id",
                )
            ),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.patient_has_device_history",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.issue_device_enrollment_token",
            new=issue_device,
        ),
    ):
        response = client.post(
            "/api/v2/auth/registration-recovery/complete",
            json={"registration_recovery_token": "x" * 40},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] == "patient-access-token"
    assert body["device_authority_state"] == "existing_device_required"
    assert body["device_enrollment_token"] is None
    issue_device.assert_not_awaited()


def test_complete_repair_without_device_history_mints_exact_session_bootstrap_grant() -> None:
    db = AsyncMock()
    client, _ = _client(db)
    issue_device = AsyncMock(return_value="bootstrap-grant")
    with (
        patch(
            "app.api.v2.registration_recovery_routes.consume_registration_recovery_capability",
            new=AsyncMock(return_value=_capability()),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.repair_patient_registration_account",
            new=AsyncMock(
                return_value=RegistrationRecoveryResult(
                    patient_id=PATIENT_ID,
                    provider_subject="subject-recovery",
                    repair_kind=REPAIR_RESTORE_RECORD,
                )
            ),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.issue_patient_access_session",
            new=AsyncMock(
                return_value=(
                    "patient-access-token",
                    SimpleNamespace(isoformat=lambda: "2026-09-10T00:15:00+00:00"),
                    "session-id",
                )
            ),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.patient_has_device_history",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.issue_device_enrollment_token",
            new=issue_device,
        ),
    ):
        response = client.post(
            "/api/v2/auth/registration-recovery/complete",
            json={"registration_recovery_token": "x" * 40},
        )

    assert response.status_code == 200
    assert response.json()["device_authority_state"] == "bootstrap_enrollment"
    assert response.json()["device_enrollment_token"] == "bootstrap-grant"
    issue_device.assert_awaited_once_with(PATIENT_ID, "session-id")


def test_repair_state_change_burns_capability_and_requires_restart() -> None:
    db = AsyncMock()
    client, _ = _client(db)
    with (
        patch(
            "app.api.v2.registration_recovery_routes.consume_registration_recovery_capability",
            new=AsyncMock(return_value=_capability()),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.repair_patient_registration_account",
            new=AsyncMock(
                side_effect=PatientRegistrationRecoveryError(
                    REGISTRATION_RECOVERY_STATE_CHANGED
                )
            ),
        ),
    ):
        response = client.post(
            "/api/v2/auth/registration-recovery/complete",
            json={"registration_recovery_token": "x" * 40},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "error_code": REGISTRATION_RECOVERY_STATE_CHANGED
    }


def test_session_failure_after_committed_repair_reports_sign_in_fallback() -> None:
    db = AsyncMock()
    client, _ = _client(db)
    with (
        patch(
            "app.api.v2.registration_recovery_routes.consume_registration_recovery_capability",
            new=AsyncMock(return_value=_capability()),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.repair_patient_registration_account",
            new=AsyncMock(
                return_value=RegistrationRecoveryResult(
                    patient_id=PATIENT_ID,
                    provider_subject="subject-recovery",
                    repair_kind=REPAIR_RESTORE_RECORD,
                )
            ),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.issue_patient_access_session",
            new=AsyncMock(
                side_effect=PatientSessionAuthorityUnavailable("redis unavailable")
            ),
        ),
    ):
        response = client.post(
            "/api/v2/auth/registration-recovery/complete",
            json={"registration_recovery_token": "x" * 40},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "error_code": "PATIENT_SESSION_AUTHORITY_UNAVAILABLE",
        "retryable": False,
        "account_repaired": True,
    }


def test_replayed_repair_capability_is_rejected_before_database_mutation() -> None:
    db = AsyncMock()
    client, _ = _client(db)
    repair = AsyncMock()
    with (
        patch(
            "app.api.v2.registration_recovery_routes.consume_registration_recovery_capability",
            new=AsyncMock(
                side_effect=RegistrationRecoveryCapabilityError(
                    "REGISTRATION_RECOVERY_CAPABILITY_INVALID"
                )
            ),
        ),
        patch(
            "app.api.v2.registration_recovery_routes.repair_patient_registration_account",
            new=repair,
        ),
    ):
        response = client.post(
            "/api/v2/auth/registration-recovery/complete",
            json={"registration_recovery_token": "x" * 40},
        )

    assert response.status_code == 401
    repair.assert_not_awaited()
