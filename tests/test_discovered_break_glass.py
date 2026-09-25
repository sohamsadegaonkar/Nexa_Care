from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from app.api.v2.consent_routes import (
    BreakGlassConsentIssueResponse,
    DiscoveredBreakGlassConsentIssueRequest,
    _prepare_break_glass_request,
    issue_discovered_break_glass_consent_route,
)
from app.security.clinical_categories import UnsupportedClinicalCategoryError
from app.services.break_glass_policy import BreakGlassReasonCode
from app.services.patient_discovery_service import (
    DiscoveryHandleInvalid,
    DiscoveryUnavailable,
)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v2/consent/break-glass/discovered/issue",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def _provider() -> MagicMock:
    provider = MagicMock()
    provider.actor_uid = str(uuid.uuid4())
    provider.hospital_id = uuid.uuid4()
    provider.session_binding = "bound-session-hash"
    provider.provider.provider_id = uuid.UUID(provider.actor_uid)
    provider.hospital.hospital_id = provider.hospital_id
    return provider


def _payload(**overrides) -> DiscoveredBreakGlassConsentIssueRequest:
    values = {
        "discovery_handle": "h" * 43,
        "reason_code": next(iter(BreakGlassReasonCode)),
        "justification": "Immediate emergency treatment required.",
        "requested_scope": None,
    }
    values.update(overrides)
    return DiscoveredBreakGlassConsentIssueRequest(**values)


@pytest.mark.parametrize(
    "field,value",
    [
        ("patient_id", str(uuid.uuid4())),
        ("provider_id", str(uuid.uuid4())),
        ("hospital_id", str(uuid.uuid4())),
        ("client_id", "client-selected-authority"),
    ],
)
def test_discovered_payload_rejects_client_authority_overrides(field, value) -> None:
    values = {
        "discovery_handle": "h" * 43,
        "reason_code": next(iter(BreakGlassReasonCode)),
        "justification": "Immediate emergency treatment required.",
        field: value,
    }
    with pytest.raises(ValidationError):
        DiscoveredBreakGlassConsentIssueRequest(**values)


def test_discovered_payload_rejects_malformed_handle() -> None:
    with pytest.raises(ValidationError):
        _payload(discovery_handle="too-short")


@pytest.mark.asyncio
async def test_scope_widening_fails_closed_before_session_or_discovery() -> None:
    provider = _provider()
    with pytest.raises(UnsupportedClinicalCategoryError):
        await _prepare_break_glass_request(
            request=_request(),
            provider=provider,
            reason_code=BreakGlassReasonCode.UNCONSCIOUS_PATIENT,
            justification_raw="Immediate emergency treatment required.",
            requested_scope=["clinical.not-a-canonical-category"],
        )


@pytest.mark.asyncio
async def test_rate_limit_denial_does_not_consume_discovery_handle() -> None:
    provider = _provider()
    service = SimpleNamespace(consume_handle=AsyncMock())

    with (
        patch(
            "app.api.v2.consent_routes._break_glass_limiter",
            new=AsyncMock(
                side_effect=HTTPException(
                    status_code=429,
                    detail={"error_code": "RATE_LIMIT_EXCEEDED"},
                )
            ),
        ),
        patch(
            "app.api.v2.consent_routes.PatientDiscoveryService",
            return_value=service,
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await issue_discovered_break_glass_consent_route(
                request=_request(),
                payload=_payload(),
                background_tasks=BackgroundTasks(),
                db=MagicMock(),
                provider=provider,
            )

    assert exc_info.value.status_code == 429
    service.consume_handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_discovery_handle_issues_authority_for_server_resolved_patient() -> None:
    provider = _provider()
    patient_id = uuid.uuid4()
    service = SimpleNamespace(
        consume_handle=AsyncMock(
            return_value=SimpleNamespace(patient_uuid=patient_id)
        )
    )
    issued = BreakGlassConsentIssueResponse(
        consent_token="nexa:consent:test-token",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        approved_scope=["clinical.allergies"],
        policy_version="break-glass-test-v1",
        authorization_ref="deadbeefdeadbeef",
    )

    with (
        patch(
            "app.api.v2.consent_routes._break_glass_limiter",
            new=AsyncMock(),
        ),
        patch(
            "app.api.v2.consent_routes._prepare_break_glass_request",
            new=AsyncMock(
                return_value=(
                    "Immediate emergency treatment required.",
                    ["clinical.allergies"],
                    datetime.now(timezone.utc),
                    2,
                )
            ),
        ),
        patch(
            "app.api.v2.consent_routes.PatientDiscoveryService",
            return_value=service,
        ),
        patch(
            "app.api.v2.consent_routes._issue_break_glass_for_patient",
            new=AsyncMock(return_value=issued),
        ) as issue,
    ):
        response = await issue_discovered_break_glass_consent_route(
            request=_request(),
            payload=_payload(),
            background_tasks=BackgroundTasks(),
            db=MagicMock(),
            provider=provider,
        )

    assert response.patient_id == str(patient_id)
    assert response.consent_token == issued.consent_token
    service.consume_handle.assert_awaited_once_with(
        raw_handle="h" * 43,
        provider_id=provider.actor_uid,
        hospital_id=str(provider.hospital_id),
        session_binding=provider.session_binding,
    )
    assert issue.await_args.kwargs["patient_id"] == str(patient_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        DiscoveryHandleInvalid(),
        DiscoveryUnavailable(),
    ],
)
async def test_invalid_or_unavailable_discovery_handle_fails_closed(failure) -> None:
    provider = _provider()
    service = SimpleNamespace(consume_handle=AsyncMock(side_effect=failure))

    with (
        patch("app.api.v2.consent_routes._break_glass_limiter", new=AsyncMock()),
        patch(
            "app.api.v2.consent_routes._prepare_break_glass_request",
            new=AsyncMock(
                return_value=(
                    "Emergency",
                    ["clinical.allergies"],
                    datetime.now(timezone.utc),
                    1,
                )
            ),
        ),
        patch(
            "app.api.v2.consent_routes.PatientDiscoveryService",
            return_value=service,
        ),
        patch(
            "app.api.v2.consent_routes._issue_break_glass_for_patient",
            new=AsyncMock(),
        ) as issue,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await issue_discovered_break_glass_consent_route(
                request=_request(),
                payload=_payload(),
                background_tasks=BackgroundTasks(),
                db=MagicMock(),
                provider=provider,
            )

    assert exc_info.value.status_code == (
        403 if isinstance(failure, DiscoveryHandleInvalid) else 503
    )
    issue.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_mfa_is_rejected_before_discovery_handle_consumption() -> None:
    provider = _provider()
    service = SimpleNamespace(consume_handle=AsyncMock())

    with (
        patch("app.api.v2.consent_routes._break_glass_limiter", new=AsyncMock()),
        patch(
            "app.api.v2.consent_routes._prepare_break_glass_request",
            new=AsyncMock(
                side_effect=HTTPException(
                    status_code=428,
                    detail={"error_code": "BREAK_GLASS_STEP_UP_MFA_REQUIRED"},
                )
            ),
        ),
        patch(
            "app.api.v2.consent_routes.PatientDiscoveryService",
            return_value=service,
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await issue_discovered_break_glass_consent_route(
                request=_request(),
                payload=_payload(),
                background_tasks=BackgroundTasks(),
                db=MagicMock(),
                provider=provider,
            )

    assert exc_info.value.status_code == 428
    service.consume_handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_mfa_gets_correct_precondition_response() -> None:
    provider = _provider()
    now = datetime.now(timezone.utc)
    stale = now - timedelta(hours=1)

    with (
        patch(
            "app.api.v2.consent_routes.provider_session_token",
            return_value="session-token",
        ),
        patch(
            "app.api.v2.consent_routes.provider_session_binding",
            return_value=provider.session_binding,
        ),
        patch(
            "app.api.v2.consent_routes.resolve_provider_session_context",
            new=AsyncMock(
                return_value={
                    "provider_id": str(provider.provider.provider_id),
                    "mfa_verified_at": stale.isoformat(),
                }
            ),
        ),
        patch(
            "app.api.v2.consent_routes.get_break_glass_mfa_max_age_seconds",
            return_value=300,
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await _prepare_break_glass_request(
                request=_request(),
                provider=provider,
                reason_code=next(iter(BreakGlassReasonCode)),
                justification_raw="Immediate emergency treatment required.",
                requested_scope=None,
            )

    assert exc_info.value.status_code == 428
    assert exc_info.value.detail == {
        "error_code": "BREAK_GLASS_STEP_UP_MFA_REQUIRED"
    }


@pytest.mark.asyncio
async def test_audit_or_issuance_failure_returns_no_usable_capability() -> None:
    provider = _provider()
    patient_id = uuid.uuid4()
    service = SimpleNamespace(
        consume_handle=AsyncMock(
            return_value=SimpleNamespace(patient_uuid=patient_id)
        )
    )

    with (
        patch("app.api.v2.consent_routes._break_glass_limiter", new=AsyncMock()),
        patch(
            "app.api.v2.consent_routes._prepare_break_glass_request",
            new=AsyncMock(
                return_value=(
                    "Emergency",
                    ["clinical.allergies"],
                    datetime.now(timezone.utc),
                    1,
                )
            ),
        ),
        patch(
            "app.api.v2.consent_routes.PatientDiscoveryService",
            return_value=service,
        ),
        patch(
            "app.api.v2.consent_routes._issue_break_glass_for_patient",
            new=AsyncMock(
                side_effect=HTTPException(
                    status_code=503,
                    detail={"error_code": "AUDIT_UNAVAILABLE"},
                )
            ),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await issue_discovered_break_glass_consent_route(
                request=_request(),
                payload=_payload(),
                background_tasks=BackgroundTasks(),
                db=MagicMock(),
                provider=provider,
            )

    assert exc_info.value.status_code == 503
