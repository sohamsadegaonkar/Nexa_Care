"""Route qualification for Slice 10A exact PHONE and QR discovery."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db_session
from app.core.dependencies import require_clinical_capability
from app.main import app
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.security.provider_capabilities import ClinicalCapability
from app.services.patient_discovery_service import DiscoveryHandle
from app.services.patient_search_identifier_service import (
    PatientSearchIdentifierNoMatch,
    PatientSearchIdentifierUnavailable,
)

TOKEN = "provider-session-token"
PUBLIC_ID = "NC-" + "AB" * 12
PHONE = "+918000000001"


@pytest.fixture
def provider() -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid4(),
            display_name="Test clinician",
            contact_email="test@example.invalid",
        ),
        hospital=HospitalContext(
            hospital_id=uuid4(), facility_code="TEST", display_name="Test Hospital"
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            is_primary=True,
            roles=["clinician"],
        ),
        session_binding=hashlib.sha256(TOKEN.encode("utf-8")).hexdigest(),
    )


@pytest.fixture
def client(provider: ProviderContext):
    discover = require_clinical_capability(ClinicalCapability.PATIENT_DISCOVER)
    app.dependency_overrides[discover] = lambda: provider
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _common_success():
    handle = DiscoveryHandle("opaque-discovery-handle", datetime.now(timezone.utc))
    return (
        patch(
            "app.api.v2.patient_discovery_routes.get_async_redis_client",
            return_value=MagicMock(),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.enforce_patient_discovery_budget",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.api.v2.patient_discovery_routes._audit",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.PatientDiscoveryService.issue_handle",
            new=AsyncMock(return_value=handle),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.PatientDiscoveryService.activate_handle",
            new=AsyncMock(return_value=True),
        ),
        handle,
    )


def test_phone_discovery_requires_recent_mfa_and_returns_only_opaque_handle(
    client: TestClient, provider: ProviderContext
) -> None:
    patient = SimpleNamespace(patient_uuid=uuid4())
    redis_patch, budget_patch, audit_patch, issue_patch, activate_patch, handle = (
        _common_success()
    )
    with (
        redis_patch,
        budget_patch as budget,
        audit_patch,
        issue_patch,
        activate_patch,
        patch(
            "app.api.v2.patient_discovery_routes.require_recent_mfa_for_phone_discovery",
            new=AsyncMock(return_value=None),
        ) as mfa,
        patch(
            "app.api.v2.patient_discovery_routes.resolve_verified_phone_patient",
            new=AsyncMock(return_value=(patient, False)),
        ) as resolve,
    ):
        response = client.post(
            "/api/v2/patient-discovery",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"identifier_type": "PHONE", "value": PHONE},
        )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "discovery_handle": handle.value,
        "expires_at": handle.expires_at.isoformat(),
    }
    assert PHONE not in response.text
    assert str(patient.patient_uuid) not in response.text
    budget.assert_awaited_once_with(
        ANY,
        provider_id=provider.actor_uid,
        hospital_id=str(provider.hospital_id),
        identifier_type="PHONE",
    )
    mfa.assert_awaited_once()
    resolve.assert_awaited_once()
    assert resolve.await_args.kwargs["phone"] == PHONE


def test_qr_is_only_transport_for_public_id_and_never_discloses_patient(
    client: TestClient,
) -> None:
    patient = SimpleNamespace(patient_uuid=uuid4())
    redis_patch, budget_patch, audit_patch, issue_patch, activate_patch, handle = (
        _common_success()
    )
    with (
        redis_patch,
        budget_patch,
        audit_patch,
        issue_patch,
        activate_patch,
        patch(
            "app.api.v2.patient_discovery_routes.PatientDiscoveryService.resolve_public_id",
            new=AsyncMock(return_value=(patient, False)),
        ) as resolve,
    ):
        response = client.post(
            "/api/v2/patient-discovery",
            json={
                "identifier_type": "QR_PUBLIC_ID",
                "value": f"nexa://patient-discovery/v1/{PUBLIC_ID}",
            },
        )

    assert response.status_code == 200, response.text
    resolve.assert_awaited_once_with(PUBLIC_ID)
    assert response.json()["discovery_handle"] == handle.value
    assert str(patient.patient_uuid) not in response.text


@pytest.mark.parametrize("identifier_type", ["NAME", "MRN", "EXTERNAL_ID", "PATIENT_UUID"])
def test_unqualified_directory_identifiers_are_rejected_before_resolution(
    client: TestClient, identifier_type: str
) -> None:
    with patch(
        "app.api.v2.patient_discovery_routes.resolve_verified_phone_patient",
        new=AsyncMock(),
    ) as resolve:
        response = client.post(
            "/api/v2/patient-discovery",
            json={"identifier_type": identifier_type, "value": "anything"},
        )
    assert response.status_code == 422
    resolve.assert_not_awaited()


def test_phone_no_match_collapses_to_generic_discovery_no_match(client: TestClient) -> None:
    with (
        patch(
            "app.api.v2.patient_discovery_routes.get_async_redis_client",
            return_value=MagicMock(),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.enforce_patient_discovery_budget",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.require_recent_mfa_for_phone_discovery",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.api.v2.patient_discovery_routes._audit",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.resolve_verified_phone_patient",
            new=AsyncMock(side_effect=PatientSearchIdentifierNoMatch()),
        ),
    ):
        response = client.post(
            "/api/v2/patient-discovery",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"identifier_type": "PHONE", "value": PHONE},
        )
    assert response.status_code == 404
    assert response.json()["detail"] == {"error_code": "DISCOVERY_NO_MATCH"}
    assert PHONE not in response.text


def test_phone_integrity_failure_is_generic_unavailable(client: TestClient) -> None:
    with (
        patch(
            "app.api.v2.patient_discovery_routes.get_async_redis_client",
            return_value=MagicMock(),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.enforce_patient_discovery_budget",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.require_recent_mfa_for_phone_discovery",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.api.v2.patient_discovery_routes._audit",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.resolve_verified_phone_patient",
            new=AsyncMock(side_effect=PatientSearchIdentifierUnavailable()),
        ),
    ):
        response = client.post(
            "/api/v2/patient-discovery",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"identifier_type": "PHONE", "value": PHONE},
        )
    assert response.status_code == 503
    assert response.json()["detail"] == {"error_code": "DISCOVERY_UNAVAILABLE"}
    assert PHONE not in response.text


def test_extra_client_patient_mapping_is_forbidden(client: TestClient) -> None:
    response = client.post(
        "/api/v2/patient-discovery",
        json={
            "identifier_type": "NEXA_PUBLIC_ID",
            "value": PUBLIC_ID,
            "patient_id": str(uuid4()),
        },
    )
    assert response.status_code == 422
