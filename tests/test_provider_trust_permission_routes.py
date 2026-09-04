"""Unit tests for Phase 4E Provider Trust Permission Administration HTTP surface."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.api.v2.provider_trust_permission_routes import (
    ClientRevocationReasonCode,
    GrantPermissionRequest,
    ProviderTrustPermissionResponse,
    ProviderTrustPermissionRouteError,
    RevokePermissionRequest,
    _map_application_error,
    grant_permission,
    revoke_permission,
    router,
)
from app.core.dependencies import ProviderTrustRoutePrincipal
from app.main import app
from app.security.trust_management_permissions import (
    TrustManagementPermission,
)
from app.services.clinical_eligibility import ClinicalAuthenticationMethod
from app.services.provider_trust_authorization import TrustManagementAuthentication
from app.services.provider_trust_permission_application import (
    ProviderTrustPermissionApplicationError,
    ProviderTrustPermissionApplicationResult,
)


def _make_principal(actor_id=None, fresh=True):
    pid = actor_id or uuid4()
    now = datetime.now(timezone.utc)
    mfa_time = now if fresh else None
    return ProviderTrustRoutePrincipal(
        actor_provider_id=pid,
        authentication=TrustManagementAuthentication(
            provider_id=pid,
            method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
            session_authenticated=True,
            mfa_verified_at=mfa_time,
        ),
    )


def test_route_inventory_and_surface_audit():
    """Exactly two permission routes exist under /api/v2/provider-trust/permissions."""
    routes = [
        r
        for r in router.routes
        if getattr(r, "path", "").startswith("/api/v2/provider-trust/permissions")
    ]
    assert len(routes) == 2
    paths = {r.path for r in routes}
    assert "/api/v2/provider-trust/permissions/grant" in paths
    assert "/api/v2/provider-trust/permissions/{grant_id}/revoke" in paths
    for r in routes:
        assert r.methods == {"POST"}

    # Overall application provider-trust surface must be exactly 26 routes (all POST)
    all_pt_routes = [
        r
        for r in app.routes
        if getattr(r, "path", "").startswith("/api/v2/provider-trust")
    ]
    assert len(all_pt_routes) == 26
    for r in all_pt_routes:
        assert r.methods == {"POST"}


def test_grant_request_schema_strictness():
    """Grant request forbids extra fields and rejects malformed inputs."""
    target_id = uuid4()

    # Valid base request
    valid = GrantPermissionRequest(
        target_provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
    )
    assert valid.target_provider_id == target_id

    # Extra fields forbidden
    with pytest.raises(Exception):
        GrantPermissionRequest(
            target_provider_id=target_id,
            permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
            actor_id=uuid4(),  # forbidden!
        )

    with pytest.raises(Exception):
        GrantPermissionRequest(
            target_provider_id=target_id,
            permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
            scope_type="GLOBAL",  # forbidden!
        )


def test_revoke_request_schema_strictness():
    """Revoke request permits only valid client reasons and forbids EXPIRED_SUPERSEDED."""
    # Valid client reasons
    for reason in ClientRevocationReasonCode:
        req = RevokePermissionRequest(revocation_reason_code=reason)
        assert req.revocation_reason_code == reason

    # Client cannot choose EXPIRED_SUPERSEDED (server-owned only)
    with pytest.raises(Exception):
        RevokePermissionRequest(
            revocation_reason_code="EXPIRED_SUPERSEDED"  # type: ignore
        )

    # Extra fields forbidden
    with pytest.raises(Exception):
        RevokePermissionRequest(
            revocation_reason_code=ClientRevocationReasonCode.ACCESS_REMOVED,
            actor_id=uuid4(),  # forbidden!
        )


def test_response_model_structure():
    """Response model strictly enforces structural fields and forbids extra attributes."""
    gid = uuid4()
    tid = uuid4()
    resp = ProviderTrustPermissionResponse(
        command="GRANT",
        grant_id=gid,
        target_provider_id=tid,
        permission="PROFESSIONAL_REVIEW",
        scope_type="GLOBAL",
        facility_id=None,
        superseded_grant_id=None,
        idempotent_replay=False,
    )
    assert resp.grant_id == gid
    assert resp.idempotent_replay is False

    with pytest.raises(Exception):
        ProviderTrustPermissionResponse(
            command="GRANT",
            grant_id=gid,
            target_provider_id=tid,
            permission="PROFESSIONAL_REVIEW",
            scope_type="GLOBAL",
            session_state="active",  # forbidden!
        )


@pytest.mark.asyncio
async def test_grant_permission_naive_datetime_rejected():
    """Naive datetime in valid_from or valid_until is rejected with 400 INVALID_DATETIME_TIMEZONE."""
    principal = _make_principal()
    mock_db = MagicMock()
    mock_db.in_transaction.return_value = False

    payload = GrantPermissionRequest(
        target_provider_id=uuid4(),
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        valid_from=datetime(2026, 9, 4, 12, 0, 0),  # naive!
    )

    with pytest.raises(ProviderTrustPermissionRouteError) as exc:
        await grant_permission(
            payload=payload,
            idempotency_key="key-12345",
            principal=principal,
            db=mock_db,
        )
    assert exc.value.status_code == 400
    assert exc.value.error_code == "INVALID_DATETIME_TIMEZONE"


@pytest.mark.asyncio
async def test_grant_permission_db_session_boundary():
    """db.in_transaction() must be False when entering application service; no route-level tx commands."""
    principal = _make_principal()
    mock_db = MagicMock()
    mock_db.in_transaction.return_value = False
    mock_db.begin = MagicMock()
    mock_db.commit = MagicMock()

    res = ProviderTrustPermissionApplicationResult(
        command="GRANT",
        grant_id=uuid4(),
        target_provider_id=uuid4(),
        permission="PROFESSIONAL_REVIEW",
        scope_type="GLOBAL",
        facility_id=None,
        superseded_grant_id=None,
        event_types=["PROVIDER_TRUST_PERMISSION_GRANTED"],
        idempotent_replay=False,
    )

    with patch(
        "app.api.v2.provider_trust_permission_routes.ProviderTrustPermissionApplicationService"
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc.apply_grant = AsyncMock(return_value=res)
        mock_svc_cls.return_value = mock_svc

        resp = await grant_permission(
            payload=GrantPermissionRequest(
                target_provider_id=res.target_provider_id,
                permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
            ),
            idempotency_key="key-valid-123",
            principal=principal,
            db=mock_db,
        )

        assert resp.grant_id == res.grant_id
        # db must not have had begin or commit called by the route
        mock_db.begin.assert_not_called()
        mock_db.commit.assert_not_called()
        # Verify actor passed is strictly from principal
        call_kwargs = mock_svc.apply_grant.call_args.kwargs
        assert call_kwargs["actor_id"] == principal.actor_provider_id
        assert call_kwargs["authentication"] == principal.authentication


@pytest.mark.asyncio
async def test_revoke_permission_success_mapping():
    """Revoke permission delegates correctly and maps result to 200 response."""
    principal = _make_principal()
    mock_db = MagicMock()
    mock_db.in_transaction.return_value = False

    res = ProviderTrustPermissionApplicationResult(
        command="REVOKE",
        grant_id=uuid4(),
        target_provider_id=uuid4(),
        permission="FACILITY_REVIEW",
        scope_type="FACILITY",
        facility_id=uuid4(),
        superseded_grant_id=None,
        event_types=["PROVIDER_TRUST_PERMISSION_REVOKED"],
        idempotent_replay=True,
    )

    with patch(
        "app.api.v2.provider_trust_permission_routes.ProviderTrustPermissionApplicationService"
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc.apply_revoke = AsyncMock(return_value=res)
        mock_svc_cls.return_value = mock_svc

        resp = await revoke_permission(
            grant_id=res.grant_id,
            payload=RevokePermissionRequest(
                revocation_reason_code=ClientRevocationReasonCode.ROLE_CHANGED,
                governance_reference="GOV-REF-100",
            ),
            idempotency_key="key-revoke-456",
            principal=principal,
            db=mock_db,
        )

        assert resp.command == "REVOKE"
        assert resp.grant_id == res.grant_id
        assert resp.idempotent_replay is True
        call_kwargs = mock_svc.apply_revoke.call_args.kwargs
        assert call_kwargs["actor_id"] == principal.actor_provider_id
        assert call_kwargs["grant_id"] == res.grant_id


def test_application_and_policy_error_mappings():
    """Verify all required Phase-4C application and policy error mappings."""
    # MFA step-up -> 428
    err = _map_application_error(
        ProviderTrustPermissionApplicationError("MFA_STEP_UP_REQUIRED")
    )
    assert err.status_code == 428
    assert err.error_code == "MFA_STEP_UP_REQUIRED"

    # Authorization denied -> 403
    err = _map_application_error(
        ProviderTrustPermissionApplicationError("AUTHORIZATION_DENIED")
    )
    assert err.status_code == 403
    assert err.error_code == "AUTHORIZATION_DENIED"

    # Resource not found -> 404
    err = _map_application_error(
        ProviderTrustPermissionApplicationError("RESOURCE_NOT_FOUND")
    )
    assert err.status_code == 404
    assert err.error_code == "RESOURCE_NOT_FOUND"

    # Idempotency conflicts -> 409
    err = _map_application_error(
        ProviderTrustPermissionApplicationError("IDEMPOTENCY_KEY_REUSED")
    )
    assert err.status_code == 409
    assert err.error_code == "IDEMPOTENCY_KEY_REUSED"

    err = _map_application_error(
        ProviderTrustPermissionApplicationError("IDEMPOTENCY_IN_PROGRESS")
    )
    assert err.status_code == 409
    assert err.error_code == "IDEMPOTENCY_IN_PROGRESS"

    # Policy codes
    # Root permission offline -> 403
    err = _map_application_error(
        ProviderTrustPermissionApplicationError(
            "TRUST_PERMISSION_POLICY_DENIED", policy_code="ROOT_PERMISSION_OFFLINE_ONLY"
        )
    )
    assert err.status_code == 403
    assert err.error_code == "ROOT_PERMISSION_OFFLINE_ONLY"

    # Self-grant prohibited -> 403
    err = _map_application_error(
        ProviderTrustPermissionApplicationError(
            "TRUST_PERMISSION_POLICY_DENIED", policy_code="SELF_GRANT_PROHIBITED"
        )
    )
    assert err.status_code == 403
    assert err.error_code == "SELF_GRANT_PROHIBITED"

    # Active grant exists -> 409
    err = _map_application_error(
        ProviderTrustPermissionApplicationError(
            "TRUST_PERMISSION_POLICY_DENIED", policy_code="ACTIVE_GRANT_EXISTS"
        )
    )
    assert err.status_code == 409
    assert err.error_code == "ACTIVE_GRANT_EXISTS"

    # Grant already revoked -> 409
    err = _map_application_error(
        ProviderTrustPermissionApplicationError(
            "TRUST_PERMISSION_POLICY_DENIED", policy_code="GRANT_ALREADY_REVOKED"
        )
    )
    assert err.status_code == 409
    assert err.error_code == "GRANT_ALREADY_REVOKED"

    # Target-state collapse -> 404 TARGET_PROVIDER_UNAVAILABLE
    for target_code in (
        "TARGET_PROVIDER_NOT_FOUND",
        "TARGET_PROVIDER_INACTIVE",
        "TARGET_CREDENTIAL_INACTIVE",
    ):
        err = _map_application_error(
            ProviderTrustPermissionApplicationError(
                "TRUST_PERMISSION_POLICY_DENIED", policy_code=target_code
            )
        )
        assert err.status_code == 404
        assert err.error_code == "TARGET_PROVIDER_UNAVAILABLE"

    # Malformed authoritative state -> 503 TRANSACTION_INTEGRITY_FAILURE
    err = _map_application_error(
        ProviderTrustPermissionApplicationError(
            "TRUST_PERMISSION_POLICY_DENIED", policy_code="GRANT_STATE_INVALID"
        )
    )
    assert err.status_code == 503
    assert err.error_code == "TRANSACTION_INTEGRITY_FAILURE"

    # 400 Bad request policy errors
    for b_code in (
        "GLOBAL_PERMISSION_FACILITY_PROHIBITED",
        "FACILITY_PERMISSION_FACILITY_REQUIRED",
        "INVALID_VALIDITY_INTERVAL",
        "INVALID_DATETIME_TIMEZONE",
        "INVALID_GOVERNANCE_REFERENCE",
        "INVALID_PERMISSION",
        "INVALID_REVOCATION_REASON",
    ):
        err = _map_application_error(
            ProviderTrustPermissionApplicationError(
                "TRUST_PERMISSION_POLICY_DENIED", policy_code=b_code
            )
        )
        assert err.status_code == 400
        assert err.error_code == b_code

    # Unknown policy or application error -> 503
    err = _map_application_error(
        ProviderTrustPermissionApplicationError("UNEXPECTED_SYSTEM_FAILURE")
    )
    assert err.status_code == 503
    assert err.error_code == "TRANSACTION_INTEGRITY_FAILURE"
