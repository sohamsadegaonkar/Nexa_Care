"""Fast, value-free contracts for provider bootstrap input and route wiring."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v2.auth_routes import (
    ProviderRegistrationRequest,
    provider_registration,
)
from app.services.provider_registration_service import (
    ProviderBootstrapRequest,
    ProviderBootstrapResult,
    ProviderRegistrationError,
    canonical_provider_bootstrap_request_hash,
    normalize_professional_registration_authority_code,
    normalize_professional_registration_number,
)

_HMAC_SECRET = "provider-registration-test-hmac-secret-000000000000000"
_HOSPITAL_ID = uuid.uuid4()


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "display_name": "Bootstrap Test Provider",
        "login_identifier": " Login@Example.Test ",
        "contact_email": " Contact@Example.Test ",
        "contact_phone": "9876543210",
        "password": "provider-bootstrap-password",
        "hospital_id": str(_HOSPITAL_ID),
        "registration_authority_code": " test-council ",
        "registration_number": " reg- 123 / 4 ",
    }
    payload.update(overrides)
    return payload


def _request(**overrides: object) -> ProviderBootstrapRequest:
    return ProviderBootstrapRequest(**_payload(**overrides))


def test_provider_bootstrap_normalizes_identifiers_and_password_safe_hash() -> None:
    first = canonical_provider_bootstrap_request_hash(_request(), hmac_secret=_HMAC_SECRET)
    equivalent = canonical_provider_bootstrap_request_hash(
        _request(
            login_identifier="login@example.test",
            contact_email="contact@example.test",
            contact_phone="+91 98765 43210",
            registration_authority_code="TEST-COUNCIL",
            registration_number="REG123/4",
        ),
        hmac_secret=_HMAC_SECRET,
    )
    changed_password = canonical_provider_bootstrap_request_hash(
        _request(password="different-provider-bootstrap-password"),
        hmac_secret=_HMAC_SECRET,
    )

    assert first == equivalent
    assert first != changed_password
    assert "provider-bootstrap-password" not in first
    assert normalize_professional_registration_authority_code(" council-1 ") == "COUNCIL-1"
    assert normalize_professional_registration_number(" reg- 12 / 3 ") == "REG12/3"


@pytest.mark.parametrize(
    "authority_field",
    [
        "is_active",
        "status",
        "role",
        "roles",
        "clinical_capabilities",
        "email_verified",
        "phone_verified",
        "email_verified_at",
        "phone_verified_at",
        "mfa_enabled",
        "trust_status",
        "professional_verification_status",
        "facility_verification_status",
        "verified_at",
        "reviewer_id",
        "decision_reason_code",
    ],
)
def test_provider_bootstrap_schema_rejects_every_authority_field(
    authority_field: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        ProviderRegistrationRequest.model_validate(_payload(**{authority_field: True}))
    assert exc_info.value.errors()[0]["type"] == "extra_forbidden"


def test_provider_bootstrap_schema_rejects_partial_professional_registration() -> None:
    with pytest.raises(ProviderRegistrationError) as exc_info:
        canonical_provider_bootstrap_request_hash(
            _request(registration_number=None), hmac_secret=_HMAC_SECRET
        )
    assert exc_info.value.code == "PROVIDER_REGISTRATION_INVALID_REQUEST"


def test_provider_registration_route_returns_no_credentials_or_authority() -> None:
    provider_id = str(uuid.uuid4())
    payload = ProviderRegistrationRequest.model_validate(_payload())

    async def invoke():
        with (
            patch(
                "app.api.v2.auth_routes.get_provider_registration_config",
                return_value=SimpleNamespace(idempotency_hmac_secret=_HMAC_SECRET),
            ),
            patch(
                "app.api.v2.auth_routes.bootstrap_provider_account",
                AsyncMock(
                    return_value=ProviderBootstrapResult(
                        provider_id=provider_id, idempotent_replay=False
                    )
                ),
            ) as bootstrap,
        ):
            result = await provider_registration(
                payload, "provider-register-route-01", AsyncMock()
            )
            bootstrap.assert_awaited_once()
            return result

    response = asyncio.run(invoke()).model_dump()
    assert response == {
        "provider_id": uuid.UUID(provider_id),
        "registration_state": "registered",
        "idempotent_replay": False,
    }
    assert "password" not in response
    assert "mfa" not in response
    assert "capabilities" not in response


def test_provider_registration_route_maps_reused_idempotency_key_to_409() -> None:
    payload = ProviderRegistrationRequest.model_validate(_payload())

    async def invoke():
        with (
            patch(
                "app.api.v2.auth_routes.get_provider_registration_config",
                return_value=SimpleNamespace(idempotency_hmac_secret=_HMAC_SECRET),
            ),
            patch(
                "app.api.v2.auth_routes.bootstrap_provider_account",
                AsyncMock(side_effect=ProviderRegistrationError("IDEMPOTENCY_KEY_REUSED")),
            ),
        ):
            await provider_registration(payload, "provider-register-route-02", AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(invoke())
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {"error_code": "IDEMPOTENCY_KEY_REUSED"}
