"""Adversarial contracts for the closed local synthetic-patient login path."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi import FastAPI

from app.api.v2.auth_routes import (
    LocalDemoPatientLoginRequest,
    _is_local_demo_patient_transport,
    include_local_demo_patient_auth_router,
    local_demo_patient_login,
)
from app.services.local_demo_patient_auth import (
    LOCAL_DEMO_PATIENT_AUTH_METHOD,
    LOCAL_DEMO_PATIENT_AUTH_PROVIDER,
    LOCAL_DEMO_PATIENT_LOGIN_ALLOWED_CLIENT_CIDRS_FLAG,
    LOCAL_DEMO_PATIENT_LOGIN_ALLOWED_HOSTS_FLAG,
)
from app.services.patient_auth_service import (
    decode_patient_access_token,
    issue_patient_access_token,
)


JWT_SECRET = "local-demo-patient-test-secret-that-is-at-least-32-chars"
SESSION_ID = "local-demo-session-0123456789abcdef"


class LocalRequest:
    """The small Request surface used by the transport gate."""

    def __init__(
        self,
        *,
        scheme: str = "http",
        hostname: str = "127.0.0.1",
        client_host: str = "127.0.0.1",
    ) -> None:
        self.url = SimpleNamespace(scheme=scheme, hostname=hostname)
        self.client = SimpleNamespace(host=client_host)


def enable_local_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("NEXA_DEMO_PATIENT_LOGIN_ENABLED", "true")
    monkeypatch.setenv("PATIENT_JWT_SECRET", JWT_SECRET)


def _demo_login_route_paths(application: FastAPI) -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in application.routes
        if (methods := getattr(route, "methods", None))
        for method in methods
        if method != "HEAD"
    }


@pytest.mark.parametrize(
    "environment", ["local", "test", "alpha", "staging", "preview", "pilot", "production"]
)
def test_local_demo_route_registration_is_absent_outside_development(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    """The synthetic login route never enters non-development inventories."""

    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("ENV", environment)
    monkeypatch.setenv("NEXA_DEMO_PATIENT_LOGIN_ENABLED", "true")
    ordinary_application = FastAPI()
    include_local_demo_patient_auth_router(ordinary_application)
    assert ("POST", "/api/v2/auth/demo/patient-login") not in _demo_login_route_paths(
        ordinary_application
    )
    assert "/api/v2/auth/demo/patient-login" not in ordinary_application.openapi()[
        "paths"
    ]


def test_local_demo_route_registration_requires_the_explicit_development_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route appears only with both explicit local-demo configuration gates."""

    enable_local_demo(monkeypatch)
    demo_application = FastAPI()
    include_local_demo_patient_auth_router(demo_application)
    registered = {
        route
        for route in _demo_login_route_paths(demo_application)
        if route[1] == "/api/v2/auth/demo/patient-login"
    }
    assert registered == {("POST", "/api/v2/auth/demo/patient-login")}
    assert "/api/v2/auth/demo/patient-login" in demo_application.openapi()["paths"]


def test_local_demo_transport_is_closed_without_an_explicit_lan_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _is_local_demo_patient_transport(LocalRequest())
    assert _is_local_demo_patient_transport(
        LocalRequest(hostname="10.0.2.2", client_host="10.0.2.16")
    )
    assert not _is_local_demo_patient_transport(
        LocalRequest(hostname="10.0.2.2", client_host="10.0.3.2")
    )
    assert not _is_local_demo_patient_transport(
        LocalRequest(hostname="192.168.1.20", client_host="192.168.1.20")
    )
    assert not _is_local_demo_patient_transport(
        LocalRequest(hostname="127.0.0.1", client_host="192.168.1.20")
    )
    assert not _is_local_demo_patient_transport(LocalRequest(scheme="https"))

    monkeypatch.setenv(LOCAL_DEMO_PATIENT_LOGIN_ALLOWED_HOSTS_FLAG, "192.168.1.20")
    monkeypatch.setenv(
        LOCAL_DEMO_PATIENT_LOGIN_ALLOWED_CLIENT_CIDRS_FLAG, "192.168.1.0/24"
    )
    assert _is_local_demo_patient_transport(
        LocalRequest(hostname="192.168.1.20", client_host="192.168.1.42")
    )
    assert not _is_local_demo_patient_transport(
        LocalRequest(hostname="192.168.1.21", client_host="192.168.1.42")
    )
    assert not _is_local_demo_patient_transport(
        LocalRequest(hostname="192.168.1.20", client_host="192.168.2.42")
    )
    monkeypatch.setenv(LOCAL_DEMO_PATIENT_LOGIN_ALLOWED_CLIENT_CIDRS_FLAG, "bad")
    assert not _is_local_demo_patient_transport(
        LocalRequest(hostname="192.168.1.20", client_host="192.168.1.42")
    )


@pytest.mark.asyncio
async def test_local_demo_login_issues_normal_session_and_device_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_local_demo(monkeypatch)
    patient_id = UUID("123e4567-e89b-12d3-a456-426614174001")
    identity = SimpleNamespace(patient_id=patient_id)
    patient = SimpleNamespace(patient_uuid=patient_id)
    db = AsyncMock()
    db.scalar.side_effect = [identity, patient]
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
    issue_session = AsyncMock(return_value=("patient-token", expires_at, SESSION_ID))
    issue_enrollment = AsyncMock(
        return_value=("enrollment-token", "bootstrap_enrollment")
    )

    with (
        patch(
            "app.api.v2.auth_routes.issue_patient_access_session",
            new=issue_session,
        ),
        patch(
            "app.api.v2.auth_routes._patient_device_login_authority",
            new=issue_enrollment,
        ),
    ):
        response = await local_demo_patient_login(
            LocalDemoPatientLoginRequest(demo_patient="aarav"),
            LocalRequest(),
            db,
        )

    assert response.patient_id == str(patient_id)
    assert response.access_token == "patient-token"
    assert response.device_enrollment_token == "enrollment-token"
    issue_session.assert_awaited_once_with(
        str(patient_id),
        "nexa-local-demo:patient:aarav",
        identity_provider=LOCAL_DEMO_PATIENT_AUTH_PROVIDER,
        auth_method=LOCAL_DEMO_PATIENT_AUTH_METHOD,
    )
    issue_enrollment.assert_awaited_once_with(
        db, patient_id=str(patient_id), session_id=SESSION_ID
    )


@pytest.mark.asyncio
async def test_local_demo_login_fails_closed_outside_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_local_demo(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "pilot")
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await local_demo_patient_login(
            LocalDemoPatientLoginRequest(demo_patient="aarav"),
            LocalRequest(),
            db,
        )

    assert exc_info.value.status_code == 404
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_demo_login_never_falls_back_when_seed_identity_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_local_demo(monkeypatch)
    db = AsyncMock()
    db.scalar.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        await local_demo_patient_login(
            LocalDemoPatientLoginRequest(demo_patient="priya"),
            LocalRequest(),
            db,
        )

    assert exc_info.value.status_code == 404
    db.scalar.assert_awaited_once()


def test_local_demo_token_is_rejected_when_its_explicit_gate_is_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_local_demo(monkeypatch)
    token, _ = issue_patient_access_token(
        "123e4567-e89b-12d3-a456-426614174001",
        "nexa-local-demo:patient:aarav",
        session_id=SESSION_ID,
        session_epoch=0,
        identity_provider=LOCAL_DEMO_PATIENT_AUTH_PROVIDER,
        auth_method=LOCAL_DEMO_PATIENT_AUTH_METHOD,
    )
    assert decode_patient_access_token(token) is not None

    monkeypatch.setenv("NEXA_DEMO_PATIENT_LOGIN_ENABLED", "false")
    assert decode_patient_access_token(token) is None

    monkeypatch.setenv("NEXA_DEMO_PATIENT_LOGIN_ENABLED", "true")
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert decode_patient_access_token(token) is None
