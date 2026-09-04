"""Unit tests for affiliation-independent provider MFA step-up authentication."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v2.mfa_action_routes import VerifyActionRequest, verify_action_mfa
from app.core.dependencies import (
    ProviderStepUpPrincipal,
    get_provider_step_up_principal,
)
from app.core.security import hash_client_ip, hash_user_agent
from app.security.audit_context import AuditDomain


# ---------------------------------------------------------------------------
# Schema & Model Tests
# ---------------------------------------------------------------------------


def test_verify_action_request_schema_forbids_extra_fields():
    """VerifyActionRequest strictly forbids client injection of unapproved fields."""
    # Valid
    req = VerifyActionRequest(code="123456")
    assert req.code == "123456"

    # Extra fields rejected
    for extra_field in (
        "provider_id",
        "session_token",
        "mfa_verified_at",
        "role",
        "permission",
        "facility_id",
        "trust_grant",
    ):
        with pytest.raises(ValidationError):
            VerifyActionRequest.model_validate(
                {"code": "123456", extra_field: "injected"}
            )


def test_provider_step_up_principal_redacts_session_token():
    """ProviderStepUpPrincipal repr and str must not expose the raw session token."""
    pid = uuid4()
    raw_token = "secret-provider-session-token-xyz"
    principal = ProviderStepUpPrincipal(
        provider_id=pid,
        session_authenticated=True,
        session_token=raw_token,
        transport="bearer",
    )
    assert raw_token not in repr(principal)
    assert raw_token not in str(principal)
    assert str(pid) in repr(principal)
    assert principal.transport == "bearer"
    # Ensure no clinical fields exist on the principal
    assert not hasattr(principal, "hospital_id")
    assert not hasattr(principal, "roles")
    assert not hasattr(principal, "capabilities")


# ---------------------------------------------------------------------------
# Dependency: get_provider_step_up_principal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step_up_principal_missing_session_rejected():
    """Missing session credentials raise 401."""
    req = MagicMock()
    req.cookies = {}
    with pytest.raises(HTTPException) as exc:
        await get_provider_step_up_principal(request=req, credentials=None)
    assert exc.value.status_code == 401
    assert exc.value.detail == {"error_code": "PROVIDER_SESSION_REQUIRED"}


@pytest.mark.asyncio
async def test_step_up_principal_basic_auth_rejected():
    """Basic authentication has no path through step-up dependency."""
    req = MagicMock()
    req.cookies = {}
    creds = MagicMock(scheme="basic", credentials="user:pass")
    with pytest.raises(HTTPException) as exc:
        await get_provider_step_up_principal(request=req, credentials=creds)
    assert exc.value.status_code == 401
    assert exc.value.detail == {"error_code": "PROVIDER_SESSION_REQUIRED"}


@pytest.mark.asyncio
async def test_step_up_principal_ambiguous_dual_tokens_rejected():
    """Conflicting bearer and cookie sessions fail closed."""
    req = MagicMock()
    req.cookies = {"nexa_provider_session": "cookie-token-1"}
    creds = MagicMock(scheme="bearer", credentials="bearer-token-2")
    with pytest.raises(HTTPException) as exc:
        await get_provider_step_up_principal(request=req, credentials=creds)
    assert exc.value.status_code == 401
    assert exc.value.detail == {"error_code": "AMBIGUOUS_SESSION_TRANSPORT"}


@pytest.mark.asyncio
async def test_step_up_principal_matching_dual_tokens_accepted():
    """Identical bearer and cookie sessions resolve successfully."""
    now = datetime.now(timezone.utc)
    pid = uuid4()
    ua = "TestUA/1.0"
    session_data = {
        "provider_id": str(pid),
        "authenticated": True,
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "ua_hash": hash_user_agent(ua),
        "ip_hash": hash_client_ip("127.0.0.1"),
    }

    req = MagicMock()
    req.cookies = {"nexa_provider_session": "same-token"}
    req.headers = {"user-agent": ua}
    req.client.host = "127.0.0.1"
    creds = MagicMock(scheme="bearer", credentials="same-token")

    with patch(
        "app.core.dependencies.resolve_provider_session_context",
        AsyncMock(return_value=session_data),
    ):
        principal = await get_provider_step_up_principal(request=req, credentials=creds)
        assert principal.provider_id == pid
        assert principal.session_token == "same-token"


@pytest.mark.asyncio
async def test_step_up_principal_expired_session_rejected():
    """Expired session raises 401."""
    now = datetime.now(timezone.utc)
    pid = uuid4()
    ua = "TestUA/1.0"
    session_data = {
        "provider_id": str(pid),
        "authenticated": True,
        "expires_at": (now - timedelta(seconds=1)).isoformat(),  # expired!
        "ua_hash": hash_user_agent(ua),
    }

    req = MagicMock()
    req.cookies = {}
    req.headers = {"user-agent": ua}
    creds = MagicMock(scheme="bearer", credentials="test-token")

    with patch(
        "app.core.dependencies.resolve_provider_session_context",
        AsyncMock(return_value=session_data),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_provider_step_up_principal(request=req, credentials=creds)
        assert exc.value.status_code == 401
        assert exc.value.detail == {"error_code": "PROVIDER_SESSION_REQUIRED"}


@pytest.mark.asyncio
async def test_step_up_principal_accepts_missing_or_stale_mfa():
    """Step-up dependency does NOT require valid mfa_verified_at, enabling step-up recovery."""
    now = datetime.now(timezone.utc)
    pid = uuid4()
    ua = "TestUA/1.0"

    # Case A: Missing mfa_verified_at
    session_no_mfa = {
        "provider_id": str(pid),
        "authenticated": True,
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "ua_hash": hash_user_agent(ua),
    }

    req = MagicMock()
    req.cookies = {}
    req.headers = {"user-agent": ua}
    creds = MagicMock(scheme="bearer", credentials="test-token")

    with patch(
        "app.core.dependencies.resolve_provider_session_context",
        AsyncMock(return_value=session_no_mfa),
    ):
        p1 = await get_provider_step_up_principal(request=req, credentials=creds)
        assert p1.provider_id == pid

    # Case B: Stale mfa_verified_at (e.g. 2 hours ago)
    session_stale_mfa = {
        "provider_id": str(pid),
        "authenticated": True,
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "mfa_verified_at": (now - timedelta(hours=2)).isoformat(),
        "ua_hash": hash_user_agent(ua),
    }

    with patch(
        "app.core.dependencies.resolve_provider_session_context",
        AsyncMock(return_value=session_stale_mfa),
    ):
        p2 = await get_provider_step_up_principal(request=req, credentials=creds)
        assert p2.provider_id == pid


@pytest.mark.asyncio
async def test_step_up_principal_future_dated_or_naive_mfa_fails_closed():
    """Future-dated or naive mfa_verified_at is malformed session state and fails closed."""
    now = datetime.now(timezone.utc)
    pid = uuid4()
    ua = "TestUA/1.0"

    # Case A: Future-dated
    session_future_mfa = {
        "provider_id": str(pid),
        "authenticated": True,
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "mfa_verified_at": (now + timedelta(days=1)).isoformat(),
        "ua_hash": hash_user_agent(ua),
    }

    req = MagicMock()
    req.cookies = {}
    req.headers = {"user-agent": ua}
    creds = MagicMock(scheme="bearer", credentials="test-token")

    with patch(
        "app.core.dependencies.resolve_provider_session_context",
        AsyncMock(return_value=session_future_mfa),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_provider_step_up_principal(request=req, credentials=creds)
        assert exc.value.status_code == 401

    # Case B: Naive timestamp
    session_naive_mfa = {
        "provider_id": str(pid),
        "authenticated": True,
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "mfa_verified_at": "2026-09-04T12:00:00",  # naive!
        "ua_hash": hash_user_agent(ua),
    }

    with patch(
        "app.core.dependencies.resolve_provider_session_context",
        AsyncMock(return_value=session_naive_mfa),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_provider_step_up_principal(request=req, credentials=creds)
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_step_up_principal_user_agent_mismatch_rejected():
    """User-Agent mismatch raises 401."""
    now = datetime.now(timezone.utc)
    pid = uuid4()
    session_data = {
        "provider_id": str(pid),
        "authenticated": True,
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "ua_hash": hash_user_agent("BoundUA/1.0"),
    }

    req = MagicMock()
    req.cookies = {}
    req.headers = {"user-agent": "DifferentUA/2.0"}
    creds = MagicMock(scheme="bearer", credentials="test-token")

    with patch(
        "app.core.dependencies.resolve_provider_session_context",
        AsyncMock(return_value=session_data),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_provider_step_up_principal(request=req, credentials=creds)
        assert exc.value.status_code == 401
        assert exc.value.detail == {"error_code": "PROVIDER_SESSION_REQUIRED"}


@pytest.mark.asyncio
async def test_step_up_principal_ip_rotation_allowed_with_warning(caplog):
    """IP rotation retains existing soft behavior (warning logged, caller admitted)."""
    now = datetime.now(timezone.utc)
    pid = uuid4()
    ua = "TestUA/1.0"
    session_data = {
        "provider_id": str(pid),
        "authenticated": True,
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "ua_hash": hash_user_agent(ua),
        "ip_hash": hash_client_ip("192.168.1.1"),
    }

    req = MagicMock()
    req.cookies = {}
    req.headers = {"user-agent": ua}
    req.client.host = "10.0.0.1"  # Rotated IP!
    creds = MagicMock(scheme="bearer", credentials="test-token")

    with patch(
        "app.core.dependencies.resolve_provider_session_context",
        AsyncMock(return_value=session_data),
    ):
        principal = await get_provider_step_up_principal(request=req, credentials=creds)
        assert principal.provider_id == pid
        assert any(
            "SESSION_IP_ROTATION_DETECTED" in record.message
            for record in caplog.records
        )


# ---------------------------------------------------------------------------
# Route: verify_action_mfa
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_action_invalid_code_format():
    """Non-6-digit or non-numeric codes fail with 401."""
    principal = ProviderStepUpPrincipal(
        provider_id=uuid4(),
        session_authenticated=True,
        session_token="tok",
    )
    for bad_code in ("12345", "1234567", "abcdef", "12 456"):
        with pytest.raises(HTTPException) as exc:
            await verify_action_mfa(
                payload=VerifyActionRequest(code=bad_code),
                principal=principal,
                db=MagicMock(),
            )
        assert exc.value.status_code == 401
        assert exc.value.detail == {"error_code": "INVALID_MFA_CODE"}


def _mock_db_with_row(row):
    mock_db = AsyncMock()
    exec_res = MagicMock()
    exec_res.first.return_value = row
    mock_db.execute.return_value = exec_res
    return mock_db


@pytest.mark.asyncio
async def test_verify_action_provider_inactive_or_missing():
    """Inactive provider or missing account raises 401."""
    principal = ProviderStepUpPrincipal(
        provider_id=uuid4(),
        session_authenticated=True,
        session_token="tok",
    )

    # Case A: Missing provider
    mock_db = _mock_db_with_row(None)
    with pytest.raises(HTTPException) as exc:
        await verify_action_mfa(
            payload=VerifyActionRequest(code="123456"),
            principal=principal,
            db=mock_db,
        )
    assert exc.value.status_code == 401
    assert exc.value.detail == {"error_code": "PROVIDER_ACCOUNT_INACTIVE"}

    # Case B: Inactive provider status
    mock_identity = MagicMock(is_active=False, status="suspended")
    mock_cred = MagicMock(is_active=True, mfa_enabled=True)
    mock_db2 = _mock_db_with_row((mock_identity, mock_cred))
    with pytest.raises(HTTPException) as exc:
        await verify_action_mfa(
            payload=VerifyActionRequest(code="123456"),
            principal=principal,
            db=mock_db2,
        )
    assert exc.value.status_code == 401
    assert exc.value.detail == {"error_code": "PROVIDER_ACCOUNT_INACTIVE"}


@pytest.mark.asyncio
async def test_verify_action_credential_inactive():
    """Inactive credential raises 401."""
    principal = ProviderStepUpPrincipal(
        provider_id=uuid4(),
        session_authenticated=True,
        session_token="tok",
    )
    mock_identity = MagicMock(is_active=True, status="active")
    mock_cred = MagicMock(is_active=False, mfa_enabled=True)
    mock_db = _mock_db_with_row((mock_identity, mock_cred))

    with pytest.raises(HTTPException) as exc:
        await verify_action_mfa(
            payload=VerifyActionRequest(code="123456"),
            principal=principal,
            db=mock_db,
        )
    assert exc.value.status_code == 401
    assert exc.value.detail == {"error_code": "PROVIDER_CREDENTIAL_INACTIVE"}


@pytest.mark.asyncio
async def test_verify_action_mfa_not_enabled():
    """MFA not enabled on credential raises 400."""
    principal = ProviderStepUpPrincipal(
        provider_id=uuid4(),
        session_authenticated=True,
        session_token="tok",
    )
    mock_identity = MagicMock(is_active=True, status="active")
    mock_cred = MagicMock(is_active=True, mfa_enabled=False, mfa_secret_encrypted="enc")
    mock_db = _mock_db_with_row((mock_identity, mock_cred))

    with pytest.raises(HTTPException) as exc:
        await verify_action_mfa(
            payload=VerifyActionRequest(code="123456"),
            principal=principal,
            db=mock_db,
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == {"error_code": "MFA_NOT_CONFIGURED"}


@pytest.mark.asyncio
async def test_verify_action_mfa_secret_absent():
    """MFA enabled but secret is None/absent raises 400 MFA_NOT_CONFIGURED."""
    principal = ProviderStepUpPrincipal(
        provider_id=uuid4(),
        session_authenticated=True,
        session_token="tok",
    )
    mock_identity = MagicMock(is_active=True, status="active")
    mock_cred = MagicMock(is_active=True, mfa_enabled=True, mfa_secret_encrypted=None)
    mock_db = _mock_db_with_row((mock_identity, mock_cred))

    with pytest.raises(HTTPException) as exc:
        await verify_action_mfa(
            payload=VerifyActionRequest(code="123456"),
            principal=principal,
            db=mock_db,
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == {"error_code": "MFA_NOT_CONFIGURED"}


@pytest.mark.asyncio
async def test_verify_action_corrupt_secret_fails_safely():
    """Corrupt encrypted secret raises 400 safely without unhandled exceptions."""
    principal = ProviderStepUpPrincipal(
        provider_id=uuid4(),
        session_authenticated=True,
        session_token="tok",
    )
    mock_identity = MagicMock(is_active=True, status="active")
    mock_cred = MagicMock(
        is_active=True, mfa_enabled=True, mfa_secret_encrypted="corrupted-enc"
    )
    mock_db = _mock_db_with_row((mock_identity, mock_cred))

    with patch("app.api.v2.mfa_action_routes.decrypt_mfa_secret", return_value=None):
        with pytest.raises(HTTPException) as exc:
            await verify_action_mfa(
                payload=VerifyActionRequest(code="123456"),
                principal=principal,
                db=mock_db,
            )
        assert exc.value.status_code == 400
        assert exc.value.detail == {"error_code": "MFA_SECRET_INVALID"}


@pytest.mark.asyncio
async def test_verify_action_wrong_code_or_replayed_code_denied():
    """Invalid or replayed TOTP code fails with 401."""
    principal = ProviderStepUpPrincipal(
        provider_id=uuid4(),
        session_authenticated=True,
        session_token="tok",
    )
    mock_identity = MagicMock(is_active=True, status="active")
    mock_cred = MagicMock(is_active=True, mfa_enabled=True, mfa_secret_encrypted="enc")
    mock_db = _mock_db_with_row((mock_identity, mock_cred))

    with patch(
        "app.api.v2.mfa_action_routes.decrypt_mfa_secret",
        return_value="JBSWY3DPEHPK3PXP",
    ):
        with patch(
            "app.api.v2.mfa_action_routes.verify_totp_code_once",
            AsyncMock(return_value=False),
        ):
            with pytest.raises(HTTPException) as exc:
                await verify_action_mfa(
                    payload=VerifyActionRequest(code="999999"),
                    principal=principal,
                    db=mock_db,
                )
            assert exc.value.status_code == 401
            assert exc.value.detail == {"error_code": "INVALID_MFA_CODE"}


@pytest.mark.asyncio
async def test_verify_action_audit_failure_blocks_session_refresh():
    """If audit staging/persistence fails, session MFA is NOT refreshed and 503 is returned."""
    pid = uuid4()
    principal = ProviderStepUpPrincipal(
        provider_id=pid,
        session_authenticated=True,
        session_token="tok",
    )
    mock_identity = MagicMock(is_active=True, status="active", provider_uid="PRV-UID")
    mock_cred = MagicMock(is_active=True, mfa_enabled=True, mfa_secret_encrypted="enc")
    mock_db = _mock_db_with_row((mock_identity, mock_cred))

    now = datetime.now(timezone.utc)
    live_session = {
        "provider_id": str(pid),
        "authenticated": True,
        "expires_at": (now + timedelta(hours=1)).isoformat(),
    }

    mock_refresh = AsyncMock()

    with patch(
        "app.api.v2.mfa_action_routes.decrypt_mfa_secret",
        return_value="JBSWY3DPEHPK3PXP",
    ):
        with patch(
            "app.api.v2.mfa_action_routes.verify_totp_code_once",
            AsyncMock(return_value=True),
        ):
            with patch(
                "app.api.v2.mfa_action_routes.resolve_provider_session_context",
                AsyncMock(return_value=live_session),
            ):
                with patch(
                    "app.api.v2.mfa_action_routes.append_audit_log_or_503",
                    AsyncMock(
                        side_effect=HTTPException(
                            status_code=503, detail="Audit failure"
                        )
                    ),
                ):
                    with patch(
                        "app.api.v2.mfa_action_routes.mark_provider_session_mfa_verified",
                        mock_refresh,
                    ):
                        with pytest.raises(HTTPException) as exc:
                            await verify_action_mfa(
                                payload=VerifyActionRequest(code="123456"),
                                principal=principal,
                                db=mock_db,
                            )
                        assert exc.value.status_code == 503
                        # CRITICAL: session refresh must NOT have been called!
                        mock_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_verify_action_final_refresh_failure_fails_closed():
    """If mark_provider_session_mfa_verified returns False, 401 PROVIDER_SESSION_INVALID is returned."""
    pid = uuid4()
    principal = ProviderStepUpPrincipal(
        provider_id=pid,
        session_authenticated=True,
        session_token="valid-tok",
    )
    mock_identity = MagicMock(is_active=True, status="active", provider_uid="PRV-UID")
    mock_cred = MagicMock(is_active=True, mfa_enabled=True, mfa_secret_encrypted="enc")
    mock_db = _mock_db_with_row((mock_identity, mock_cred))

    now = datetime.now(timezone.utc)
    live_session = {
        "provider_id": str(pid),
        "authenticated": True,
        "expires_at": (now + timedelta(hours=1)).isoformat(),
    }

    mock_audit = AsyncMock()
    mock_refresh = AsyncMock(return_value=False)

    with patch(
        "app.api.v2.mfa_action_routes.decrypt_mfa_secret",
        return_value="JBSWY3DPEHPK3PXP",
    ):
        with patch(
            "app.api.v2.mfa_action_routes.verify_totp_code_once",
            AsyncMock(return_value=True),
        ):
            with patch(
                "app.api.v2.mfa_action_routes.resolve_provider_session_context",
                AsyncMock(return_value=live_session),
            ):
                with patch(
                    "app.api.v2.mfa_action_routes.append_audit_log_or_503",
                    mock_audit,
                ):
                    with patch(
                        "app.api.v2.mfa_action_routes.mark_provider_session_mfa_verified",
                        mock_refresh,
                    ):
                        with pytest.raises(HTTPException) as exc:
                            await verify_action_mfa(
                                payload=VerifyActionRequest(code="123456"),
                                principal=principal,
                                db=mock_db,
                            )
                        assert exc.value.status_code == 401
                        assert exc.value.detail == {
                            "error_code": "PROVIDER_SESSION_INVALID"
                        }
                        mock_audit.assert_called_once()
                        mock_refresh.assert_called_once_with("valid-tok", pid)


@pytest.mark.asyncio
async def test_verify_action_success_path():
    """Successful TOTP verification audits then refreshes the session."""
    pid = uuid4()
    principal = ProviderStepUpPrincipal(
        provider_id=pid,
        session_authenticated=True,
        session_token="valid-tok",
    )
    mock_identity = MagicMock(is_active=True, status="active", provider_uid="PRV-UID")
    mock_cred = MagicMock(is_active=True, mfa_enabled=True, mfa_secret_encrypted="enc")
    mock_db = _mock_db_with_row((mock_identity, mock_cred))

    now = datetime.now(timezone.utc)
    live_session = {
        "provider_id": str(pid),
        "authenticated": True,
        "expires_at": (now + timedelta(hours=1)).isoformat(),
    }

    mock_audit = AsyncMock()
    mock_refresh = AsyncMock(return_value=True)

    with patch(
        "app.api.v2.mfa_action_routes.decrypt_mfa_secret",
        return_value="JBSWY3DPEHPK3PXP",
    ):
        with patch(
            "app.api.v2.mfa_action_routes.verify_totp_code_once",
            AsyncMock(return_value=True),
        ):
            with patch(
                "app.api.v2.mfa_action_routes.resolve_provider_session_context",
                AsyncMock(return_value=live_session),
            ):
                with patch(
                    "app.api.v2.mfa_action_routes.append_audit_log_or_503",
                    mock_audit,
                ):
                    with patch(
                        "app.api.v2.mfa_action_routes.mark_provider_session_mfa_verified",
                        mock_refresh,
                    ):
                        res = await verify_action_mfa(
                            payload=VerifyActionRequest(code="123456"),
                            principal=principal,
                            db=mock_db,
                        )
                        assert res == {"verified": True}
                        mock_audit.assert_called_once()
                        call_kwargs = mock_audit.call_args[1]
                        assert (
                            call_kwargs["event_type"] == "PROVIDER_STEP_UP_MFA_VERIFIED"
                        )
                        assert call_kwargs["target_id"] == str(pid)
                        assert call_kwargs["audit_context"].domain == AuditDomain.AUTH
                        mock_refresh.assert_called_once_with("valid-tok", pid)
