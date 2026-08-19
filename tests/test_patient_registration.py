"""Focused security contracts for first-time patient registration."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.v2.auth_routes import router
from app.core.database import get_db_session
from app.core.rate_limiter import OtpRateLimitBackendUnavailable, OtpRateLimitExceeded
from app.main import app
from app.models.patient import Patient
from app.models.patient_auth_identity import PatientAuthIdentity
from app.models.patient_records import PatientRecord
from app.services.patient_registration_attempt_service import (
    RegistrationAttemptClaim,
    RegistrationAttemptError,
    claim_registration_attempt,
    finalize_registration_attempt,
    issue_registration_attempt,
)
from app.services.patient_registration_service import (
    PatientRegistrationAccount,
    PatientRegistrationError,
    finalize_patient_registration,
    registration_audit_idempotency_key,
)

client = TestClient(app)
JWT_SECRET = "patient-registration-test-secret-at-least-32-characters"
PHONE = "+918000000001"
PATIENT_ID = "123e4567-e89b-12d3-a456-426614174001"


def _provider_result(*, phone: str = PHONE, subject: str = "subject-1"):
    return SimpleNamespace(
        user=SimpleNamespace(phone=phone, id=subject),
        session=SimpleNamespace(access_token="provider-session-token"),
    )


def _allow_limits():
    return patch(
        "app.api.v2.auth_routes._otp_rate_limiter.check",
        new=AsyncMock(return_value=None),
    )


class _RegistrationRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key, value, *, nx=False, xx=False, ex=None):
        if nx and key in self.values:
            return False
        if xx and key not in self.values:
            return False
        self.values[key] = value
        self.ttls[key] = int(ex or 300)
        return True

    async def get(self, key):
        return self.values.get(key)

    async def ttl(self, key):
        return self.ttls.get(key, -2)


def _attempt_secret():
    return patch(
        "app.services.patient_registration_attempt_service.get_otp_rate_limit_config",
        return_value=SimpleNamespace(hmac_secret="a" * 32),
    )


def test_registration_routes_are_separate_from_login_routes() -> None:
    paths = app.openapi()["paths"]
    assert "/api/v2/auth/register/otp/send" in paths
    assert "/api/v2/auth/register/otp/verify" in paths
    assert router.routes[0].endpoint.__module__ == "app.api.v2.auth_routes"


def test_registration_send_returns_opaque_server_backed_attempt() -> None:
    auth = MagicMock()
    with (
        _allow_limits(),
        patch(
            "app.api.v2.auth_routes.get_supabase_client",
            return_value=SimpleNamespace(auth=auth),
        ),
        patch(
            "app.api.v2.auth_routes.issue_registration_attempt",
            new=AsyncMock(return_value="opaque-attempt"),
        ) as issue,
    ):
        response = client.post(
            "/api/v2/auth/register/otp/send", json={"phone": "91 80000 00001"}
        )
    assert response.status_code == 200
    assert response.json()["registration_attempt_token"] == "opaque-attempt"
    auth.sign_in_with_otp.assert_called_once_with(
        {"phone": PHONE, "options": {"should_create_user": True}}
    )
    issue.assert_awaited_once_with(PHONE)


@pytest.mark.asyncio
async def test_attempt_state_is_server_side_phone_bound_and_finalized() -> None:
    redis = _RegistrationRedis()
    with (
        _attempt_secret(),
        patch(
            "app.services.patient_registration_attempt_service.get_async_redis_client",
            return_value=redis,
        ),
    ):
        token = await issue_registration_attempt(PHONE)
        assert token not in redis.values
        raw = next(iter(redis.values.values()))
        assert PHONE not in raw
        assert "phone_digest" in raw
        claim = await claim_registration_attempt(token, PHONE)
        await finalize_registration_attempt(token, PHONE, claim, PATIENT_ID)
        replay = await claim_registration_attempt(token, PHONE)
    assert replay.finalized is True
    assert replay.finalized_patient_id == PATIENT_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_phone", ["+919000000001", PHONE])
async def test_attempt_rejects_wrong_phone_scope_and_expiry(wrong_phone: str) -> None:
    redis = _RegistrationRedis()
    with (
        _attempt_secret(),
        patch(
            "app.services.patient_registration_attempt_service.get_async_redis_client",
            return_value=redis,
        ),
    ):
        token = await issue_registration_attempt(PHONE)
        key = next(iter(redis.values))
        if wrong_phone == PHONE:
            state = json.loads(redis.values[key])
            state["scope"] = "wrong_scope"
            redis.values[key] = json.dumps(state)
        with pytest.raises(RegistrationAttemptError) as exc_info:
            await claim_registration_attempt(token, wrong_phone)
    assert exc_info.value.code == "REGISTRATION_ATTEMPT_INVALID"


@pytest.mark.asyncio
async def test_attempt_redis_unavailable_fails_closed() -> None:
    with (
        _attempt_secret(),
        patch(
            "app.services.patient_registration_attempt_service.get_async_redis_client",
            side_effect=RuntimeError("unavailable"),
        ),
    ):
        with pytest.raises(RegistrationAttemptError) as exc_info:
            await issue_registration_attempt(PHONE)
    assert exc_info.value.code == "REGISTRATION_ATTEMPT_UNAVAILABLE"


def test_registration_send_rejects_invalid_phone_and_fails_closed_on_limits() -> None:
    assert (
        client.post(
            "/api/v2/auth/register/otp/send", json={"phone": "not-a-phone"}
        ).status_code
        == 422
    )
    for failure, expected in (
        (OtpRateLimitExceeded("limited"), 429),
        (OtpRateLimitBackendUnavailable("unavailable"), 503),
    ):
        with patch(
            "app.api.v2.auth_routes._otp_rate_limiter.check",
            new=AsyncMock(side_effect=failure),
        ):
            response = client.post(
                "/api/v2/auth/register/otp/send", json={"phone": "8000000001"}
            )
        assert response.status_code == expected


def test_registration_send_fails_closed_when_attempt_storage_is_unavailable() -> None:
    auth = MagicMock()
    with (
        _allow_limits(),
        patch(
            "app.api.v2.auth_routes.get_supabase_client",
            return_value=SimpleNamespace(auth=auth),
        ),
        patch(
            "app.api.v2.auth_routes.issue_registration_attempt",
            new=AsyncMock(
                side_effect=RegistrationAttemptError("REGISTRATION_ATTEMPT_UNAVAILABLE")
            ),
        ),
    ):
        response = client.post(
            "/api/v2/auth/register/otp/send", json={"phone": "8000000001"}
        )
    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "REGISTRATION_ATTEMPT_UNAVAILABLE"


def test_verify_requires_a_valid_attempt_before_provider_otp() -> None:
    with (
        _allow_limits(),
        patch(
            "app.api.v2.auth_routes.claim_registration_attempt",
            new=AsyncMock(
                side_effect=RegistrationAttemptError("REGISTRATION_ATTEMPT_INVALID")
            ),
        ),
        patch("app.api.v2.auth_routes.get_supabase_client") as provider,
    ):
        response = client.post(
            "/api/v2/auth/register/otp/verify",
            json={
                "phone": "8000000001",
                "otp": "123456",
                "registration_attempt_token": "bad",
            },
        )
    assert response.status_code == 401
    assert response.json()["detail"]["error_code"] == "REGISTRATION_ATTEMPT_INVALID"
    provider.assert_not_called()


def test_invalid_provider_otp_releases_only_its_pending_attempt() -> None:
    db = AsyncMock()
    provider_error = RuntimeError("provider diagnostics must not leak")
    provider_error.status = 401
    app.dependency_overrides[get_db_session] = lambda: db
    try:
        with (
            _allow_limits(),
            patch(
                "app.api.v2.auth_routes.claim_registration_attempt",
                new=AsyncMock(
                    return_value=RegistrationAttemptClaim("attempt-a", "claim-a")
                ),
            ),
            patch(
                "app.api.v2.auth_routes.recover_patient_registration_for_attempt",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.api.v2.auth_routes.get_supabase_client",
                return_value=SimpleNamespace(
                    auth=SimpleNamespace(
                        verify_otp=MagicMock(side_effect=provider_error)
                    )
                ),
            ),
            patch(
                "app.api.v2.auth_routes.release_registration_attempt_claim",
                new=AsyncMock(),
            ) as release,
        ):
            response = client.post(
                "/api/v2/auth/register/otp/verify",
                json={
                    "phone": "8000000001",
                    "otp": "123456",
                    "registration_attempt_token": "attempt-token",
                },
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)
    assert response.status_code == 401
    assert response.json()["detail"]["error_code"] == "REGISTRATION_OTP_INVALID"
    assert "diagnostics" not in response.text
    release.assert_awaited_once()


def test_existing_account_new_attempt_is_rejected_not_logged_in() -> None:
    db = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: db
    try:
        with (
            _allow_limits(),
            patch(
                "app.api.v2.auth_routes.claim_registration_attempt",
                new=AsyncMock(
                    return_value=RegistrationAttemptClaim("new-attempt", "claim")
                ),
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
                    side_effect=PatientRegistrationError("ACCOUNT_ALREADY_REGISTERED")
                ),
            ),
            patch("app.api.v2.auth_routes.issue_patient_access_token") as issue_access,
            patch(
                "app.api.v2.auth_routes.release_registration_attempt_claim",
                new=AsyncMock(),
            ),
        ):
            response = client.post(
                "/api/v2/auth/register/otp/verify",
                json={
                    "phone": "8000000001",
                    "otp": "123456",
                    "registration_attempt_token": "new-attempt-token",
                },
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "ACCOUNT_ALREADY_REGISTERED"
    issue_access.assert_not_called()


def test_same_finalized_attempt_recovers_after_device_redis_failure(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PATIENT_JWT_SECRET", JWT_SECRET)
    db = AsyncMock()
    account = PatientRegistrationAccount(PATIENT_ID, True, "subject-1")
    finalized = PatientRegistrationAccount(PATIENT_ID, False, "subject-1")
    enroll = AsyncMock(side_effect=[RuntimeError("redis unavailable"), "enrollment"])
    app.dependency_overrides[get_db_session] = lambda: db
    try:
        with (
            _allow_limits(),
            patch(
                "app.api.v2.auth_routes.claim_registration_attempt",
                new=AsyncMock(
                    side_effect=[
                        RegistrationAttemptClaim("attempt-a", "claim"),
                        RegistrationAttemptClaim("attempt-a", None, PATIENT_ID),
                    ]
                ),
            ),
            patch(
                "app.api.v2.auth_routes.recover_patient_registration_for_attempt",
                new=AsyncMock(side_effect=[None, finalized]),
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
                new=AsyncMock(return_value=account),
            ) as finalize,
            patch(
                "app.api.v2.auth_routes.finalize_registration_attempt", new=AsyncMock()
            ),
            patch("app.api.v2.auth_routes.issue_device_enrollment_token", new=enroll),
        ):
            body = {
                "phone": "8000000001",
                "otp": "123456",
                "registration_attempt_token": "same-attempt",
            }
            first = client.post("/api/v2/auth/register/otp/verify", json=body)
            second = client.post("/api/v2/auth/register/otp/verify", json=body)
    finally:
        app.dependency_overrides.pop(get_db_session, None)
    assert first.status_code == 503
    assert first.json()["detail"]["error_code"] == "REGISTRATION_ENROLLMENT_UNAVAILABLE"
    assert second.status_code == 200
    assert finalize.await_count == 1
    assert enroll.await_count == 2


class _Transaction:
    def __init__(self, db: "_FakeRegistrationDb") -> None:
        self.db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, _tb):
        if exc_type is not None:
            self.db.rolled_back = True
        return False


class _FakeRegistrationDb:
    def __init__(self, scalars) -> None:
        self._scalars = iter(scalars)
        self.added = []
        self.rolled_back = False

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    def begin(self):
        return _Transaction(self)

    def begin_nested(self):
        return _Transaction(self)

    async def scalar(self, _statement, _params=None):
        return next(self._scalars)

    def add(self, row):
        self.added.append(row)

    def add_all(self, rows):
        self.added.extend(rows)

    async def flush(self):
        for row in self.added:
            if isinstance(row, Patient) and row.patient_uuid is None:
                row.patient_uuid = uuid4()


@pytest.mark.asyncio
async def test_new_graph_and_canonical_outbox_share_one_transaction() -> None:
    db = _FakeRegistrationDb([None])
    with patch(
        "app.services.patient_registration_service.enqueue_audit_event", new=AsyncMock()
    ) as enqueue:
        account = await finalize_patient_registration(
            db, provider_subject="subject-new", attempt_id="attempt-new"
        )
    assert [type(row) for row in db.added] == [
        Patient,
        PatientAuthIdentity,
        PatientRecord,
    ]
    assert account.created is True
    kwargs = enqueue.await_args.kwargs
    assert kwargs["event_type"] == "PATIENT_REGISTRATION_SUCCESS"
    assert kwargs["metadata"] == {"provider": "supabase"}
    assert kwargs["idempotency_key"] == registration_audit_idempotency_key(
        "attempt-new"
    )
    assert "attempt-new" not in kwargs["idempotency_key"]
    assert "subject" not in repr(kwargs).lower()


@pytest.mark.asyncio
async def test_outbox_failure_rolls_back_every_account_row() -> None:
    db = _FakeRegistrationDb([None])
    with patch(
        "app.services.patient_registration_service.enqueue_audit_event",
        new=AsyncMock(side_effect=RuntimeError("outbox unavailable")),
    ):
        with pytest.raises(RuntimeError, match="outbox unavailable"):
            await finalize_patient_registration(
                db,
                provider_subject="subject-rollback",
                attempt_id="attempt-rollback",
            )
    assert db.rolled_back is True
    assert [type(row) for row in db.added] == [
        Patient,
        PatientAuthIdentity,
        PatientRecord,
    ]


async def _assert_existing_graph_is_unavailable(identity, patient, record) -> None:
    db = _FakeRegistrationDb([identity, patient, record])
    with pytest.raises(PatientRegistrationError) as exc_info:
        await finalize_patient_registration(
            db, provider_subject="subject-existing", attempt_id="fresh-attempt"
        )
    assert exc_info.value.code == "REGISTRATION_IDENTITY_UNAVAILABLE"


@pytest.mark.asyncio
async def test_revoked_identity_fails_closed_without_reactivation() -> None:
    await _assert_existing_graph_is_unavailable(
        SimpleNamespace(patient_id=uuid4(), revoked_at=object(), provider_subject="s"),
        None,
        None,
    )


@pytest.mark.asyncio
async def test_deleted_or_missing_linked_patient_fails_closed() -> None:
    await _assert_existing_graph_is_unavailable(
        SimpleNamespace(patient_id=uuid4(), revoked_at=None, provider_subject="s"),
        None,
        None,
    )


@pytest.mark.asyncio
async def test_identity_with_missing_patient_record_fails_closed() -> None:
    patient_id = uuid4()
    await _assert_existing_graph_is_unavailable(
        SimpleNamespace(patient_id=patient_id, revoked_at=None, provider_subject="s"),
        SimpleNamespace(patient_uuid=patient_id),
        None,
    )
