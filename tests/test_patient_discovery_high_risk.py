"""Pure adversarial qualification for Slice 10A high-risk discovery inputs."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from app.services.patient_discovery_high_risk_gate import (
    PatientDiscoveryHighRiskGateError,
    require_recent_mfa_for_phone_discovery,
)
from app.services.patient_discovery_input import normalize_qr_public_id


PUBLIC_ID = "NC-" + "A1" * 12


def _request(token: str | None = "provider-session-token") -> Request:
    headers = []
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode("utf-8")))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/api/v2/patient-discovery",
            "raw_path": b"/api/v2/patient-discovery",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 443),
            "server": ("testserver", 443),
        }
    )


def _provider(token: str = "provider-session-token"):
    return SimpleNamespace(
        actor_uid="provider-1",
        session_binding=hashlib.sha256(token.encode("utf-8")).hexdigest(),
    )


def test_qr_accepts_only_versioned_public_id_payload() -> None:
    assert normalize_qr_public_id(f"nexa://patient-discovery/v1/{PUBLIC_ID}") == PUBLIC_ID
    assert (
        normalize_qr_public_id(
            f"  nexa://patient-discovery/v1/{PUBLIC_ID.lower()}  "
        )
        == PUBLIC_ID
    )


@pytest.mark.parametrize(
    "value",
    [
        PUBLIC_ID,
        "123e4567-e89b-12d3-a456-426614174000",
        f"https://nexa.example/patient/{PUBLIC_ID}",
        f"nexa://patient-discovery/v2/{PUBLIC_ID}",
        f"nexa://patient-discovery/v1/{PUBLIC_ID}?token=secret",
        f"nexa://patient-discovery/v1/{PUBLIC_ID}#fragment",
        f"nexa://patient-discovery/v1/{PUBLIC_ID}%3Ftoken%3Dsecret",
        "nexa://patient-discovery/v1/not-a-patient-id",
    ],
)
def test_qr_rejects_raw_ids_urls_queries_fragments_and_encoded_payloads(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_qr_public_id(value)


@pytest.mark.asyncio
async def test_phone_discovery_accepts_exact_live_session_with_recent_mfa() -> None:
    token = "provider-session-token"
    now = datetime.now(timezone.utc)
    session = {
        "provider_id": "provider-1",
        "mfa_verified_at": (now - timedelta(minutes=3)).isoformat(),
    }
    with patch(
        "app.services.patient_discovery_high_risk_gate.resolve_provider_session_context",
        new=AsyncMock(return_value=session),
    ):
        await require_recent_mfa_for_phone_discovery(
            _request(token), provider=_provider(token), now=now
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("session", "expected"),
    [
        ({"provider_id": "provider-1"}, "DISCOVERY_RECENT_MFA_REQUIRED"),
        (
            {
                "provider_id": "provider-1",
                "mfa_verified_at": (
                    datetime.now(timezone.utc) - timedelta(minutes=16)
                ).isoformat(),
            },
            "DISCOVERY_RECENT_MFA_REQUIRED",
        ),
        (
            {
                "provider_id": "provider-1",
                "mfa_verified_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=5)
                ).isoformat(),
            },
            "DISCOVERY_RECENT_MFA_REQUIRED",
        ),
        (
            {
                "provider_id": "other-provider",
                "mfa_verified_at": datetime.now(timezone.utc).isoformat(),
            },
            "DISCOVERY_SESSION_BINDING_MISMATCH",
        ),
        (None, "DISCOVERY_SESSION_REQUIRED"),
    ],
)
async def test_phone_discovery_fails_closed_on_stale_or_mismatched_authority(
    session, expected: str
) -> None:
    token = "provider-session-token"
    with patch(
        "app.services.patient_discovery_high_risk_gate.resolve_provider_session_context",
        new=AsyncMock(return_value=session),
    ):
        with pytest.raises(PatientDiscoveryHighRiskGateError) as exc_info:
            await require_recent_mfa_for_phone_discovery(
                _request(token), provider=_provider(token)
            )
    assert exc_info.value.code == expected


@pytest.mark.asyncio
async def test_phone_discovery_rejects_missing_or_wrong_session_binding() -> None:
    with pytest.raises(PatientDiscoveryHighRiskGateError) as missing:
        await require_recent_mfa_for_phone_discovery(
            _request(None), provider=_provider()
        )
    assert missing.value.code == "DISCOVERY_SESSION_REQUIRED"

    token = "provider-session-token"
    provider = SimpleNamespace(actor_uid="provider-1", session_binding="wrong")
    with pytest.raises(PatientDiscoveryHighRiskGateError) as mismatch:
        await require_recent_mfa_for_phone_discovery(
            _request(token), provider=provider
        )
    assert mismatch.value.code == "DISCOVERY_SESSION_BINDING_MISMATCH"
