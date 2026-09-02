import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.main import app
from app.core.dependencies import get_db_session, get_provider_context, require_role
from app.models.provider_context import (
    ProviderContext,
    ProviderIdentityContext,
    HospitalContext,
    AffiliationContext,
)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_provider():
    # Use real Pydantic models for the nested structures if possible, or deep MagicMocks
    provider_id = MagicMock()
    hosp_id = MagicMock()

    mock_id_ctx = MagicMock(spec=ProviderIdentityContext)
    mock_id_ctx.provider_id = provider_id

    mock_hosp_ctx = MagicMock(spec=HospitalContext)
    mock_hosp_ctx.hospital_id = hosp_id

    mock_affil_ctx = MagicMock(spec=AffiliationContext)
    mock_affil_ctx.roles = ["clinician"]

    provider = MagicMock(spec=ProviderContext)
    provider.provider = mock_id_ctx
    provider.hospital = mock_hosp_ctx
    provider.affiliation = mock_affil_ctx
    provider.actor_uid = "doctor-123"
    provider.hospital_id = hosp_id

    return provider


@pytest.mark.asyncio
async def test_revoke_break_glass_happy_path(client, mock_provider):
    token = "nexa:consent:emergency-token"

    mock_db = AsyncMock(spec=AsyncSession)
    mock_grant = MagicMock()
    mock_grant.is_break_glass = True

    async def mock_execute(*args, **kwargs):
        result = MagicMock()
        result.scalar_one_or_none.return_value = mock_grant
        return result

    mock_db.execute.side_effect = mock_execute

    app.dependency_overrides[get_db_session] = lambda: mock_db
    app.dependency_overrides[get_provider_context] = lambda: mock_provider
    app.dependency_overrides[require_role("clinician")] = lambda: mock_provider

    with (
        patch(
            "app.api.v2.consent_routes.append_audit_log_or_503", new_callable=AsyncMock
        ) as mock_audit,
        patch(
            "app.api.v2.consent_routes.consent_engine.revoke", new_callable=AsyncMock
        ) as mock_revoke,
    ):
        response = client.post(
            "/api/v2/consent/break-glass/revoke",
            json={"consent_token": token, "revocation_reason": "emergency over"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "revoked"
        assert mock_audit.await_count == 2
        assert mock_revoke.called

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_revoke_non_break_glass_token_returns_400(client, mock_provider):
    token = "nexa:consent:routine-token"

    mock_db = AsyncMock(spec=AsyncSession)
    mock_grant = MagicMock()
    mock_grant.is_break_glass = False

    async def mock_execute(*args, **kwargs):
        result = MagicMock()
        result.scalar_one_or_none.return_value = mock_grant
        return result

    mock_db.execute.side_effect = mock_execute

    app.dependency_overrides[get_db_session] = lambda: mock_db
    app.dependency_overrides[get_provider_context] = lambda: mock_provider
    app.dependency_overrides[require_role("clinician")] = lambda: mock_provider

    with patch(
        "app.api.v2.consent_routes.append_audit_log_or_503", new_callable=AsyncMock
    ):
        response = client.post(
            "/api/v2/consent/break-glass/revoke",
            json={"consent_token": token, "revocation_reason": "wrong path"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Token is not a break-glass grant"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_revoke_missing_auth_returns_401(client):
    response = client.post(
        "/api/v2/consent/break-glass/revoke",
        json={"consent_token": "token", "revocation_reason": "no auth"},
    )
    assert response.status_code in [401, 403]


@pytest.mark.asyncio
async def test_revoke_audit_failure_returns_503(client, mock_provider):
    mock_db = AsyncMock(spec=AsyncSession)

    app.dependency_overrides[get_db_session] = lambda: mock_db
    app.dependency_overrides[get_provider_context] = lambda: mock_provider
    app.dependency_overrides[require_role("clinician")] = lambda: mock_provider

    with patch(
        "app.api.v2.consent_routes.append_audit_log_or_503", new_callable=AsyncMock
    ) as mock_audit:
        mock_audit.side_effect = HTTPException(status_code=503, detail="Audit failed")

        response = client.post(
            "/api/v2/consent/break-glass/revoke",
            json={"consent_token": "token", "revocation_reason": "audit fail"},
        )

        assert response.status_code == 503

    app.dependency_overrides.clear()
