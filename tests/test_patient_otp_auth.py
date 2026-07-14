from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db_session
from app.core.rate_limiter import OtpRateLimitBackendUnavailable, OtpRateLimitExceeded
from app.main import app
from app.services.patient_auth_service import (
    claim_device_enrollment_token,
    decode_patient_access_token,
    finalize_device_enrollment_token,
    issue_device_enrollment_token,
    issue_patient_access_token,
    normalize_indian_phone,
)

client = TestClient(app)
JWT_SECRET = "patient-test-secret-that-is-at-least-32-characters"


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def setex(self, key: str, _ttl: int, value: str) -> bool:
        self.values[key] = value
        return True

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            count += int(self.values.pop(key, None) is not None)
        return count


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("8975073895", "+918975073895"),
        ("91 89750 73895", "+918975073895"),
        ("+91-89750-73895", "+918975073895"),
    ],
)
def test_indian_phone_normalization(raw: str, expected: str) -> None:
    assert normalize_indian_phone(raw) == expected


@pytest.mark.parametrize("raw", ["123", "+14155552671", "5975073895", "not-a-phone"])
def test_invalid_phone_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_indian_phone(raw)


def test_patient_jwt_claims_and_tamper_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATIENT_JWT_SECRET", JWT_SECRET)
    token, _ = issue_patient_access_token("patient-1", "supabase-user-1")
    claims = decode_patient_access_token(token)
    assert claims is not None
    assert claims["sub"] == "patient-1"
    assert claims["actor_type"] == "patient"
    assert claims["auth_method"] == "phone_otp"
    assert decode_patient_access_token(token + "tampered") is None


def test_expired_patient_jwt_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATIENT_JWT_SECRET", JWT_SECRET)
    expired = jwt.encode(
        {"sub": "p", "patient_id": "p", "actor_type": "patient", "auth_method": "phone_otp", "exp": 1},
        JWT_SECRET,
        algorithm="HS256",
    )
    assert decode_patient_access_token(expired) is None


@pytest.mark.asyncio
async def test_enrollment_token_scope_binding_expiry_and_replay() -> None:
    redis = FakeRedis()
    with patch("app.services.patient_auth_service.get_redis_client", return_value=redis):
        token = await issue_device_enrollment_token("patient-1", "auth-session-1")
        stored = next(value for key, value in redis.values.items() if "claim" not in key)
        assert json.loads(stored)["scope"] == "device_enrollment"
        assert await claim_device_enrollment_token(token, "patient-2") is None

        claim = await claim_device_enrollment_token(token, "patient-1")
        assert claim is not None
        assert await finalize_device_enrollment_token(token, claim)
        assert await claim_device_enrollment_token(token, "patient-1") is None

        expired = await issue_device_enrollment_token("patient-1", "auth-session-2")
        redis.values.clear()
        assert await claim_device_enrollment_token(expired, "patient-1") is None


def _allow_rate_limits():
    return patch("app.api.v2.auth_routes._otp_rate_limiter.check", new=AsyncMock(return_value=None))


def test_otp_send_is_generic_and_disables_user_creation() -> None:
    auth = MagicMock()
    supabase = SimpleNamespace(auth=auth)
    check = AsyncMock(return_value=None)
    with patch("app.api.v2.auth_routes._otp_rate_limiter.check", new=check), patch(
        "app.api.v2.auth_routes.get_supabase_client", return_value=supabase
    ):
        response = client.post("/api/v2/auth/otp/send", json={"phone": "8975073895"})
    assert response.status_code == 200
    assert response.json() == {"message": "If this phone is registered, an OTP will be sent."}
    auth.sign_in_with_otp.assert_called_once_with(
        {"phone": "+918975073895", "options": {"should_create_user": False}}
    )
    assert check.await_args.kwargs["action"] == "send"
    assert check.await_args.kwargs["normalized_phone"] == "+918975073895"


def test_otp_send_rate_limit() -> None:
    with patch(
        "app.api.v2.auth_routes._otp_rate_limiter.check",
        new=AsyncMock(side_effect=OtpRateLimitExceeded("limited")),
    ):
        response = client.post("/api/v2/auth/otp/send", json={"phone": "8975073895"})
    assert response.status_code == 429


def test_otp_redis_failure_is_fail_closed_at_route() -> None:
    with patch(
        "app.api.v2.auth_routes._otp_rate_limiter.check",
        new=AsyncMock(side_effect=OtpRateLimitBackendUnavailable("down")),
    ):
        response = client.post("/api/v2/auth/otp/send", json={"phone": "8975073895"})
    assert response.status_code == 503


def test_otp_send_unknown_phone_remains_generic() -> None:
    error = RuntimeError("user missing")
    error.status = 400
    auth = MagicMock()
    auth.sign_in_with_otp.side_effect = error
    with _allow_rate_limits(), patch("app.api.v2.auth_routes.get_supabase_client", return_value=SimpleNamespace(auth=auth)):
        response = client.post("/api/v2/auth/otp/send", json={"phone": "8975073895"})
    assert response.status_code == 200
    assert "registered" in response.json()["message"]


def test_successful_otp_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATIENT_JWT_SECRET", JWT_SECRET)
    patient = SimpleNamespace(patient_uuid=UUID("123e4567-e89b-12d3-a456-426614174001"))
    identity = SimpleNamespace(patient_id=patient.patient_uuid, revoked_at=None)
    db = AsyncMock()
    db.scalar.side_effect = [identity, patient]
    app.dependency_overrides[get_db_session] = lambda: db
    auth = MagicMock()
    auth.verify_otp.return_value = SimpleNamespace(
        user=SimpleNamespace(phone="+918975073895", id="supabase-user-1"),
        session=SimpleNamespace(access_token="supabase-session-token"),
    )
    try:
        with _allow_rate_limits(), \
             patch("app.api.v2.auth_routes.get_supabase_client", return_value=SimpleNamespace(auth=auth)), \
             patch("app.api.v2.auth_routes.issue_device_enrollment_token", new=AsyncMock(return_value="enroll-token")):
            response = client.post(
                "/api/v2/auth/otp/verify",
                json={"phone": "8975073895", "otp": "123456"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["patient_id"] == str(patient.patient_uuid)
    assert body["device_enrollment_token"] == "enroll-token"
    assert decode_patient_access_token(body["access_token"])["patient_id"] == body["patient_id"]
    compiled_queries = " ".join(str(call.args[0]) for call in db.scalar.call_args_list)
    assert "patient_auth_identities.provider_subject" in compiled_queries
    assert "WHERE patients.phone" not in compiled_queries


@pytest.mark.parametrize("status_code", [400, 401])
def test_invalid_or_expired_otp(status_code: int) -> None:
    error = RuntimeError("provider details must stay private")
    error.status = status_code
    auth = MagicMock()
    auth.verify_otp.side_effect = error
    with _allow_rate_limits(), patch("app.api.v2.auth_routes.get_supabase_client", return_value=SimpleNamespace(auth=auth)):
        response = client.post(
            "/api/v2/auth/otp/verify",
            json={"phone": "8975073895", "otp": "123456"},
        )
    assert response.status_code == 401
    assert "provider details" not in response.text


def test_matching_phone_without_identity_mapping_is_rejected() -> None:
    db = AsyncMock()
    db.scalar.return_value = None
    app.dependency_overrides[get_db_session] = lambda: db
    auth = MagicMock()
    auth.verify_otp.return_value = SimpleNamespace(
        user=SimpleNamespace(phone="+918975073895", id="supabase-user-1"),
        session=SimpleNamespace(access_token="supabase-session-token"),
    )
    try:
        with _allow_rate_limits(), patch("app.api.v2.auth_routes.get_supabase_client", return_value=SimpleNamespace(auth=auth)):
            response = client.post(
                "/api/v2/auth/otp/verify",
                json={"phone": "8975073895", "otp": "123456"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)
    assert response.status_code == 403


def test_revoked_identity_mapping_is_rejected() -> None:
    db = AsyncMock()
    # The query requires revoked_at IS NULL, so a revoked row resolves as absent.
    db.scalar.return_value = None
    app.dependency_overrides[get_db_session] = lambda: db
    auth = MagicMock()
    auth.verify_otp.return_value = SimpleNamespace(
        user=SimpleNamespace(phone="+918975073895", id="revoked-subject"),
        session=SimpleNamespace(access_token="supabase-session-token"),
    )
    try:
        with _allow_rate_limits(), patch("app.api.v2.auth_routes.get_supabase_client", return_value=SimpleNamespace(auth=auth)):
            response = client.post(
                "/api/v2/auth/otp/verify",
                json={"phone": "8975073895", "otp": "123456"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)
    assert response.status_code == 403
    assert "revoked_at IS NULL" in str(db.scalar.call_args.args[0])


def test_deleted_or_missing_mapped_patient_is_rejected() -> None:
    patient_id = UUID("123e4567-e89b-12d3-a456-426614174001")
    db = AsyncMock()
    db.scalar.side_effect = [SimpleNamespace(patient_id=patient_id, revoked_at=None), None]
    app.dependency_overrides[get_db_session] = lambda: db
    auth = MagicMock()
    auth.verify_otp.return_value = SimpleNamespace(
        user=SimpleNamespace(phone="+918975073895", id="supabase-user-1"),
        session=SimpleNamespace(access_token="supabase-session-token"),
    )
    try:
        with _allow_rate_limits(), patch("app.api.v2.auth_routes.get_supabase_client", return_value=SimpleNamespace(auth=auth)):
            response = client.post(
                "/api/v2/auth/otp/verify",
                json={"phone": "8975073895", "otp": "123456"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)
    assert response.status_code == 403
    assert "patients.is_deleted IS false" in str(db.scalar.call_args_list[1].args[0])


def test_otp_paths_are_in_openapi_and_provider_mfa_remains_registered() -> None:
    paths = app.openapi()["paths"]
    assert "/api/v2/auth/otp/send" in paths
    assert "/api/v2/auth/otp/verify" in paths
    assert "/api/v2/auth/login" in paths
    assert "/api/v2/auth/mfa/verify" in paths
