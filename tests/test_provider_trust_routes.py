"""Adversarial contracts for the narrow Phase-3F trust HTTP adapter."""

from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.v2.provider_trust_routes import (
    ProviderTrustRouteError,
    AffiliationActivationRequest,
    DecisionRequest,
    FacilityEvidenceRequest,
    ProfessionalEvidenceRequest,
    ProfessionalSubmissionRequest,
    ProviderTrustTransitionResponse,
    _VersionRequest,
    provider_trust_route_error_response,
    router,
)
from app.core.database import get_db_session
from app.core.dependencies import get_provider_trust_route_principal
from app.core.security import hash_client_ip, hash_user_agent
from app.main import CookieCsrfMiddleware
from app.main import app as main_app
from app.services.provider_trust_lifecycle_application import (
    ProviderTrustLifecycleResult,
)


def _run(awaitable):
    return asyncio.run(awaitable)


class _Request:
    def __init__(
        self,
        *,
        user_agent: str = "ProviderTrustTest/1.0",
        client_ip: str = "192.0.2.10",
        cookie: str | None = None,
    ) -> None:
        self.headers = {"user-agent": user_agent}
        self.client = SimpleNamespace(host=client_ip)
        self.cookies = {"nexa_provider_session": cookie} if cookie else {}


def _session(
    *,
    provider_id=None,
    user_agent: str = "ProviderTrustTest/1.0",
    client_ip: str = "192.0.2.10",
) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    return {
        "authenticated": True,
        "provider_id": str(provider_id or uuid4()),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "mfa_verified_at": now.isoformat(),
        "ua_hash": hash_user_agent(user_agent),
        "ip_hash": hash_client_ip(client_ip),
    }


def _principal_with(session: dict[str, object], request: _Request | None = None):
    request = request or _Request()
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="opaque")
    with patch(
        "app.core.dependencies.resolve_provider_session_context",
        AsyncMock(return_value=session),
    ):
        return _run(get_provider_trust_route_principal(request, credentials))


def test_session_principal_derives_actor_only_from_server_session() -> None:
    provider_id = uuid4()
    principal = _principal_with(_session(provider_id=provider_id))

    assert principal.actor_provider_id == provider_id
    assert principal.authentication.provider_id == provider_id
    assert principal.authentication.session_authenticated is True


@pytest.mark.parametrize(
    "test_request,credentials,session,status_code",
    [
        (_Request(), None, _session(), 401),
        (
            _Request(),
            HTTPAuthorizationCredentials(scheme="Basic", credentials="x"),
            _session(),
            401,
        ),
        (
            _Request(),
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="bad"),
            None,
            401,
        ),
        (
            _Request(),
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="opaque"),
            {**_session(), "authenticated": False},
            401,
        ),
        (
            _Request(),
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="opaque"),
            {
                key: value
                for key, value in _session().items()
                if key != "mfa_verified_at"
            },
            428,
        ),
        (
            _Request(user_agent="other"),
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="opaque"),
            _session(),
            401,
        ),
        (
            _Request(),
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="opaque"),
            {key: value for key, value in _session().items() if key != "ua_hash"},
            401,
        ),
        (
            _Request(),
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="opaque"),
            {
                **_session(),
                "mfa_verified_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=1)
                ).isoformat(),
            },
            428,
        ),
    ],
)
def test_session_principal_rejects_invalid_transports_and_assurance(
    test_request, credentials, session, status_code
) -> None:
    with patch(
        "app.core.dependencies.resolve_provider_session_context",
        AsyncMock(return_value=session),
    ) as resolve:
        with pytest.raises(HTTPException) as failure:
            _run(get_provider_trust_route_principal(test_request, credentials))

    assert failure.value.status_code == status_code
    assert failure.value.detail["error_code"] in {
        "PROVIDER_SESSION_REQUIRED",
        "MFA_SESSION_ASSURANCE_REQUIRED",
    }
    if credentials is None or credentials.scheme == "Basic":
        resolve.assert_not_awaited()


def test_session_principal_accepts_cookie_and_tolerates_ip_rotation() -> None:
    session = _session(client_ip="192.0.2.10")
    request = _Request(client_ip="192.0.2.99", cookie="opaque-cookie")
    with patch(
        "app.core.dependencies.resolve_provider_session_context",
        AsyncMock(return_value=session),
    ) as resolve:
        principal = _run(get_provider_trust_route_principal(request, None))

    assert principal.actor_provider_id == UUID(session["provider_id"])
    resolve.assert_awaited_once_with("opaque-cookie")


def test_route_inventory_is_command_specific_and_complete() -> None:
    paths = {route.path for route in router.routes}
    expected = {
        "/api/v2/provider-trust/professional/me/submit",
        *{
            f"/api/v2/provider-trust/professional/{{provider_id}}/{command}"
            for command in (
                "verify",
                "reject",
                "suspend",
                "restore",
                "mark-recheck-due",
                "complete-recheck",
                "mark-stale",
                "revoke",
                "expire",
            )
        },
        *{
            f"/api/v2/provider-trust/facilities/{{facility_id}}/{command}"
            for command in (
                "submit",
                "verify",
                "reject",
                "suspend",
                "restore",
                "mark-recheck-required",
                "complete-recheck",
                "close",
            )
        },
        *{
            f"/api/v2/provider-trust/affiliations/{{affiliation_id}}/{command}"
            for command in (
                "activate",
                "suspend",
                "restore",
                "revoke",
                "expire",
                "leave",
            )
        },
    }

    assert paths == expected
    assert all(route.methods == {"POST"} for route in router.routes)
    assert all("transition" not in path and "status" not in path for path in paths)
    for route in router.routes:
        idempotency = inspect.signature(route.endpoint).parameters["idempotency_key"]
        assert idempotency.default.is_required()


def test_all_trust_mutation_models_forbid_unknown_fields() -> None:
    for model in (
        _VersionRequest,
        ProfessionalSubmissionRequest,
        ProfessionalEvidenceRequest,
        DecisionRequest,
        FacilityEvidenceRequest,
        AffiliationActivationRequest,
        ProviderTrustTransitionResponse,
    ):
        assert model.model_config["extra"] == "forbid"


def test_provider_trust_router_is_registered_in_fastapi_composition() -> None:
    registered_paths = {route.path for route in main_app.routes}

    assert "/api/v2/provider-trust/professional/me/submit" in registered_paths


@pytest.mark.parametrize(
    "field",
    (
        "SOURCE_UNAVAILABLE",
        "recheck_failure_reason",
        "grace_expires_at",
        "previous_verification_valid",
        "authoritative_adverse_signal_at",
        "recheck_attempted_at",
        "reviewer_id",
        "status",
        "command",
        "event_type",
    ),
)
def test_mark_recheck_due_schema_rejects_client_grace_and_control_injection(
    field: str,
) -> None:
    with pytest.raises(ValidationError):
        _VersionRequest.model_validate({"expected_version": 1, field: "injected"})


def test_routes_cannot_open_a_mutation_or_audit_boundary() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "app/api/v2/provider_trust_routes.py"
    ).read_text(encoding="utf-8")

    assert "get_provider_context" not in source
    assert "get_current_provider" not in source
    assert "db.commit(" not in source
    assert "enqueue_audit_event" not in source
    assert ".status =" not in source
    assert ".trust_status =" not in source


def test_phase3e_error_handler_exposes_only_the_stable_error_code() -> None:
    app = FastAPI()
    app.add_exception_handler(
        ProviderTrustRouteError, provider_trust_route_error_response
    )

    @app.get("/provider-trust-error")
    async def fail():
        raise ProviderTrustRouteError(409, "LIFECYCLE_VERSION_CONFLICT")

    response = TestClient(app).get("/provider-trust-error")

    assert response.status_code == 409
    assert response.json() == {"error_code": "LIFECYCLE_VERSION_CONFLICT"}


def test_cookie_csrf_stays_enforced_and_bearer_has_no_cookie_requirement() -> None:
    app = FastAPI()
    app.add_middleware(CookieCsrfMiddleware)
    app.include_router(router)
    resource_id, actor_id = uuid4(), uuid4()

    class _LookupDb:
        @asynccontextmanager
        async def begin(self):
            yield self

        async def scalar(self, _statement):
            return resource_id

    principal = _principal_with(_session(provider_id=actor_id))

    async def principal_dependency():
        return principal

    async def db_dependency():
        yield _LookupDb()

    app.dependency_overrides[get_provider_trust_route_principal] = principal_dependency
    app.dependency_overrides[get_db_session] = db_dependency

    client = TestClient(app)
    cookie = {"nexa_provider_session": "opaque", "nexa_csrf": "csrf-value"}
    headers = {"Idempotency-Key": "provider-trust-csrf-0001"}
    payload = {
        "expected_version": 1,
        "registration_authority_code": "AUTH",
        "registration_number": "CSRF-REG-001",
    }
    result = ProviderTrustLifecycleResult(
        resource_id=resource_id,
        lifecycle_type="professional",
        old_state="NOT_SUBMITTED",
        new_state="PENDING_REVIEW",
        version=2,
        event_type="PROVIDER_PROFESSIONAL_VERIFICATION_SUBMITTED",
        idempotent_replay=False,
    )

    with patch(
        "app.api.v2.provider_trust_routes."
        "ProviderTrustLifecycleApplicationService.apply_professional",
        AsyncMock(return_value=result),
    ):
        assert (
            client.post(
                "/api/v2/provider-trust/professional/me/submit",
                cookies=cookie,
                headers=headers,
                json=payload,
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v2/provider-trust/professional/me/submit",
                cookies=cookie,
                headers={
                    **headers,
                    "origin": "http://testserver",
                    "x-csrf-token": "wrong",
                },
                json=payload,
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v2/provider-trust/professional/me/submit",
                cookies=cookie,
                headers={
                    **headers,
                    "origin": "http://testserver",
                    "x-csrf-token": "csrf-value",
                },
                json=payload,
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/v2/provider-trust/professional/me/submit",
                headers={**headers, "authorization": "Bearer opaque"},
                json=payload,
            ).status_code
            == 200
        )
