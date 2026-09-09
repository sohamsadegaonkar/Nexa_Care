"""Route-level authority tests for Slice 6E patient device recovery."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.core.database import get_db_session
from app.core.dependencies import AuthenticatedPatientSession, get_current_patient_session
from app.main import app
from app.services.patient_device_recovery import PatientRecoveryCapability
from app.services.patient_device_recovery_transactions import PatientRecoveryResult

client = TestClient(app)
PATIENT_ID = "123e4567-e89b-12d3-a456-426614174001"
SESSION_ID = "session-recovery-route-1234567890"
SUBJECT = "supabase-subject-recovery"


def _public_key_b64() -> str:
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(public).decode("ascii")


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


def test_bootstrap_enrollment_is_denied_once_device_history_exists():
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
            ),
            patch(
                "app.api.v2.device_routes.claim_device_enrollment_token",
                new=AsyncMock(),
            ) as claim,
        ):
            response = client.post(
                "/api/v2/patient/devices/enroll",
                json={
                    "device_public_key": _public_key_b64(),
                    "device_label": "new phone",
                    "platform": "android",
                    "device_enrollment_token": "e" * 43,
                },
            )
    finally:
        app.dependency_overrides.pop(get_current_patient_session, None)
        app.dependency_overrides.pop(get_db_session, None)
    assert response.status_code == 409
    assert response.json()["detail"] == {"error_code": "DEVICE_RECOVERY_REQUIRED"}
    claim.assert_not_awaited()


def test_recovery_otp_verify_requires_exact_supabase_subject():
    db = _db_override()
    app.dependency_overrides[get_current_patient_session] = _patient_dependency
    app.dependency_overrides[get_db_session] = lambda: db
    auth = MagicMock()
    auth.verify_otp.return_value = SimpleNamespace(
        user=SimpleNamespace(phone="+918000000001", id="attacker-subject"),
        session=SimpleNamespace(access_token="upstream-token"),
    )
    try:
        with (
            patch(
                "app.api.v2.device_routes._recovery_otp_rate_limiter.check",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.api.v2.device_routes.get_supabase_client",
                return_value=SimpleNamespace(auth=auth),
            ),
            patch(
                "app.api.v2.device_routes.issue_patient_recovery_capability",
                new=AsyncMock(),
            ) as issue_capability,
        ):
            response = client.post(
                "/api/v2/patient/devices/recovery/otp/verify",
                json={"phone": "8000000001", "otp": "123456"},
            )
    finally:
        app.dependency_overrides.pop(get_current_patient_session, None)
        app.dependency_overrides.pop(get_db_session, None)
    assert response.status_code == 403
    assert response.json()["detail"] == {
        "error_code": "PATIENT_RECOVERY_IDENTITY_MISMATCH"
    }
    issue_capability.assert_not_awaited()


def test_recovery_complete_consumes_capability_before_revoking_sessions_and_db_mutation():
    db = _db_override()
    app.dependency_overrides[get_current_patient_session] = _patient_dependency
    app.dependency_overrides[get_db_session] = lambda: db
    order: list[str] = []

    async def consume(**_kwargs):
        order.append("consume")
        return PatientRecoveryCapability(
            token="r" * 43,
            patient_id=PATIENT_ID,
            session_id=SESSION_ID,
            supabase_user_id=SUBJECT,
            issued_at="2026-09-09T00:00:00+00:00",
            expires_at="2026-09-09T00:05:00+00:00",
        )

    async def revoke_all(_patient_id):
        order.append("sessions")
        return 3

    async def recover(_db, **_kwargs):
        order.append("db")
        return PatientRecoveryResult(
            device_id=UUID("123e4567-e89b-12d3-a456-426614174111"),
            key_id=UUID("123e4567-e89b-12d3-a456-426614174222"),
            key_version=1,
            public_key_fingerprint="a" * 64,
            enrolled_at=SimpleNamespace(isoformat=lambda: "ignored"),
            revoked_device_count=2,
            status="active",
        )

    async def issue_session(_patient_id, _subject):
        order.append("session")
        return (
            "fresh-access-token",
            SimpleNamespace(isoformat=lambda: "2026-09-09T00:15:00+00:00"),
            "fresh-session",
        )

    try:
        with (
            patch(
                "app.api.v2.device_routes.consume_patient_recovery_capability",
                new=consume,
            ),
            patch(
                "app.api.v2.device_routes.revoke_all_patient_sessions", new=revoke_all
            ),
            patch(
                "app.api.v2.device_routes.append_audit_log_or_503",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.api.v2.device_routes.recover_patient_device_authority", new=recover
            ),
            patch(
                "app.api.v2.device_routes.issue_patient_access_session",
                new=issue_session,
            ),
        ):
            response = client.post(
                "/api/v2/patient/devices/recovery/complete",
                json={
                    "recovery_token": "r" * 43,
                    "new_device_public_key": _public_key_b64(),
                    "device_label": "recovered phone",
                    "platform": "android",
                },
            )
    finally:
        app.dependency_overrides.pop(get_current_patient_session, None)
        app.dependency_overrides.pop(get_db_session, None)
    assert response.status_code == 200, response.text
    assert order == ["consume", "sessions", "db", "session"]
    assert response.json()["access_token"] == "fresh-access-token"
    assert response.json()["revoked_device_count"] == 2


def test_attacker_bearer_without_fresh_recovery_otp_cannot_complete_recovery():
    db = _db_override()
    app.dependency_overrides[get_current_patient_session] = _patient_dependency
    app.dependency_overrides[get_db_session] = lambda: db
    try:
        with patch(
            "app.api.v2.device_routes.consume_patient_recovery_capability",
            new=AsyncMock(
                side_effect=__import__(
                    "app.services.patient_device_recovery",
                    fromlist=["PatientRecoveryCapabilityError"],
                ).PatientRecoveryCapabilityError("PATIENT_RECOVERY_CAPABILITY_INVALID")
            ),
        ):
            response = client.post(
                "/api/v2/patient/devices/recovery/complete",
                json={
                    "recovery_token": "x" * 43,
                    "new_device_public_key": _public_key_b64(),
                    "device_label": "attacker",
                    "platform": "android",
                },
            )
    finally:
        app.dependency_overrides.pop(get_current_patient_session, None)
        app.dependency_overrides.pop(get_db_session, None)
    assert response.status_code == 401
    assert response.json()["detail"] == {
        "error_code": "PATIENT_RECOVERY_CAPABILITY_INVALID"
    }
