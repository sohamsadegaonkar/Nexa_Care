"""Tests for provider-centric auth routes and bearer dependency."""

from __future__ import annotations

import asyncio
import uuid
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api.v2.auth_routes import (
    ProviderLoginRequest,
    ProviderMfaVerifyRequest,
    provider_login,
    provider_mfa_verify,
    provider_refresh,
)
from app.core.dependencies import get_current_provider
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.services.provider_auth_service import ProviderAuthFailure, ProviderAuthResult


def run(coro):
    return asyncio.run(coro)


class MockRequest:
    """Minimal request stand-in for route tests."""

    def __init__(self, user_agent: str = "TestAgent/1.0", client_ip: str = "10.0.0.1"):
        self.headers = {"user-agent": user_agent, "x-forwarded-for": client_ip}
        self.client = None


def sample_provider_context() -> ProviderContext:
    provider_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=provider_id,
            display_name="Provider",
            medical_registration_number=None,
            specialty=None,
            contact_email="provider@example.com",
        ),
        hospital=HospitalContext(
            hospital_id=hospital_id,
            facility_code="HOSP",
            display_name="Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            department=None,
            roles=["doctor"],
            is_primary=True,
            valid_from=None,
            valid_until=None,
        ),
    )


class TestProviderLoginRoute(unittest.TestCase):
    @patch("app.api.v2.auth_routes.append_audit_log", new_callable=AsyncMock)
    @patch("app.api.v2.auth_routes.issue_provider_session_token", new_callable=AsyncMock)
    @patch("app.api.v2.auth_routes.authenticate_provider_password", new_callable=AsyncMock)
    def test_login_returns_bearer_token(self, mock_auth, mock_issue_token, mock_audit) -> None:
        context = sample_provider_context()
        mock_auth.return_value = ProviderAuthResult(context)
        mock_issue_token.return_value = "session-token"
        payload = ProviderLoginRequest(
            login_identifier="provider@example.com",
            password="secret",
            hospital_id=context.hospital.hospital_id,
        )
        request = MockRequest(user_agent="TestAgent/1.0", client_ip="10.0.0.1")

        result = run(provider_login(payload, request=request, db=AsyncMock()))

        self.assertEqual(result.access_token, "session-token")
        self.assertEqual(result.token_type, "bearer")
        self.assertEqual(result.provider_uid, context.actor_uid)
        self.assertEqual(result.hospital_id, context.hospital.hospital_id)
        mock_issue_token.assert_awaited_once_with(
            context.provider.provider_id,
            user_agent="TestAgent/1.0",
            client_ip="10.0.0.1",
        )
        self.assertEqual(mock_audit.await_args.kwargs["event_type"], "PROVIDER_LOGIN_SUCCEEDED")

    @patch("app.api.v2.auth_routes.append_audit_log", new_callable=AsyncMock)
    @patch("app.api.v2.auth_routes.authenticate_provider_password", new_callable=AsyncMock)
    def test_login_rejects_invalid_credentials(self, mock_auth, mock_audit) -> None:
        mock_auth.return_value = ProviderAuthResult(None, ProviderAuthFailure.INVALID_CREDENTIALS)
        payload = ProviderLoginRequest(login_identifier="provider@example.com", password="bad")
        request = MockRequest()

        with self.assertRaises(HTTPException) as cm:
            run(provider_login(payload, request=request, db=AsyncMock()))

        self.assertEqual(cm.exception.status_code, 401)
        self.assertEqual(mock_audit.await_args.kwargs["event_type"], "PROVIDER_LOGIN_FAILED")


class TestGetCurrentProvider(unittest.TestCase):
    @patch("app.core.dependencies.authenticate_provider_session", new_callable=AsyncMock)
    def test_bearer_token_returns_provider_context(self, mock_auth) -> None:
        context = sample_provider_context()
        mock_auth.return_value = ProviderAuthResult(context)
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
        request = MockRequest(user_agent="TestAgent/1.0", client_ip="10.0.0.1")

        result = run(get_current_provider(
            request=request,
            credentials=credentials,
            hospital_id=None,
            db=AsyncMock(),
        ))

        self.assertEqual(result, context)
        mock_auth.assert_awaited_once()
        _, kwargs = mock_auth.await_args
        self.assertEqual(kwargs["user_agent"], "TestAgent/1.0")
        self.assertEqual(kwargs["client_ip"], "10.0.0.1")

    @patch("app.core.dependencies.append_audit_log", new_callable=AsyncMock)
    def test_missing_bearer_token_rejects(self, mock_audit) -> None:
        with self.assertRaises(HTTPException) as cm:
            run(get_current_provider(
                request=MockRequest(),
                credentials=None,
                hospital_id=None,
                db=AsyncMock(),
            ))

        self.assertEqual(cm.exception.status_code, 401)
        self.assertEqual(mock_audit.await_args.kwargs["status"], "MISSING_TOKEN")


class TestProviderRefresh(unittest.TestCase):
    @patch("app.api.v2.auth_routes.append_audit_log", new_callable=AsyncMock)
    @patch("app.api.v2.auth_routes.refresh_provider_session_token", new_callable=AsyncMock)
    def test_refresh_rebinds_and_returns_new_token(self, mock_refresh, mock_audit) -> None:
        context = sample_provider_context()
        mock_refresh.return_value = "refreshed-token"
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="old-token")

        result = run(provider_refresh(
            request=MockRequest(user_agent="NewAgent/2.0", client_ip="10.0.0.2"),
            credentials=credentials,
            provider=context,
        ))

        self.assertEqual(result.access_token, "refreshed-token")
        self.assertEqual(result.token_type, "bearer")
        mock_refresh.assert_awaited_once_with(
            "old-token",
            user_agent="NewAgent/2.0",
            client_ip="10.0.0.2",
        )
        self.assertEqual(mock_audit.await_args.kwargs["event_type"], "PROVIDER_SESSION_REFRESH")


class TestMfaFlow(unittest.TestCase):
    @patch("app.api.v2.auth_routes.append_audit_log", new_callable=AsyncMock)
    @patch("app.api.v2.auth_routes.authenticate_provider_password", new_callable=AsyncMock)
    def test_login_with_mfa_returns_pending_token(self, mock_auth, mock_audit) -> None:
        mock_auth.return_value = ProviderAuthResult(
            None, ProviderAuthFailure.MFA_REQUIRED, mfa_pending_token="mfa-pending-token"
        )
        payload = ProviderLoginRequest(login_identifier="provider@example.com", password="secret")
        request = MockRequest()

        result = run(provider_login(payload, request=request, db=AsyncMock()))

        self.assertEqual(result.mfa_token, "mfa-pending-token")
        self.assertEqual(mock_audit.await_args.kwargs["event_type"], "PROVIDER_MFA_REQUIRED")

    @patch("app.api.v2.auth_routes.append_audit_log", new_callable=AsyncMock)
    @patch("app.api.v2.auth_routes.issue_provider_session_token", new_callable=AsyncMock)
    @patch("app.api.v2.auth_routes.complete_mfa_login", new_callable=AsyncMock)
    def test_mfa_verify_issues_bearer_token(self, mock_complete, mock_issue, mock_audit) -> None:
        context = sample_provider_context()
        mock_complete.return_value = ProviderAuthResult(context)
        mock_issue.return_value = "final-session-token"
        payload = ProviderMfaVerifyRequest(mfa_token="mfa-pending-token", totp_code="123456")
        request = MockRequest(user_agent="TestAgent/1.0", client_ip="10.0.0.1")

        result = run(provider_mfa_verify(payload, request=request, db=AsyncMock()))

        self.assertEqual(result.access_token, "final-session-token")
        mock_issue.assert_awaited_once_with(
            context.provider.provider_id,
            user_agent="TestAgent/1.0",
            client_ip="10.0.0.1",
        )
        mock_complete.assert_awaited_once()
        _, kwargs = mock_complete.await_args
        self.assertEqual(kwargs["client_ip"], "10.0.0.1")
        self.assertEqual(mock_audit.await_args.kwargs["event_type"], "PROVIDER_LOGIN_SUCCEEDED")


if __name__ == "__main__":
    unittest.main()
