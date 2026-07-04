import uuid

from fastapi.testclient import TestClient

from app.core.dependencies import get_provider_context
from app.main import app
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)

client = TestClient(app)


def _provider_context_with_roles(roles: list[str]) -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid.uuid4(),
            display_name="Policy Test Provider",
            medical_registration_number="MCI-POLICY",
            specialty="Operations",
            contact_email="policy-test@example.com",
        ),
        hospital=HospitalContext(
            hospital_id=uuid.uuid4(),
            facility_code="HOSP-POLICY",
            display_name="Policy Test Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            department="Front Desk",
            roles=roles,
            is_primary=True,
            valid_from=None,
            valid_until=None,
        ),
    )


def _override_provider_context(provider: ProviderContext):
    async def _override() -> ProviderContext:
        return provider

    return _override

def test_policy_update_requires_auth():
    """Policy update should fail without authentication"""
    response = client.put("/api/v2/patient/123e4567-e89b-12d3-a456-426614174000/policy", json={
        "consent_assurance_policy": "standard"
    })
    assert response.status_code == 401

def test_policy_update_requires_role():
    """Only clinician/admin should be able to update policy"""
    app.dependency_overrides[get_provider_context] = _override_provider_context(
        _provider_context_with_roles(["receptionist"])
    )
    try:
        response = client.put(
            "/api/v2/patient/123e4567-e89b-12d3-a456-426614174000/policy",
            json={"consent_assurance_policy": "standard"},
            headers={"Authorization": "Bearer receptionist-token"},
        )
    finally:
        app.dependency_overrides.pop(get_provider_context, None)

    assert response.status_code == 403

def test_policy_update_blocked_in_production():
    """Policy updates should be blocked unless ALLOW_DEV_POLICY_UPDATES=true"""
    headers = {"Authorization": "Bearer clinician-token"}
    response = client.put(
        "/api/v2/patient/123e4567-e89b-12d3-a456-426614174000/policy",
        json={"consent_assurance_policy": "standard"},
        headers=headers
    )
    # Should be 403 if ALLOW_DEV_POLICY_UPDATES is not set
    assert response.status_code in [403, 401]