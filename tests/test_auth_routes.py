"""Tests for provider-centric auth routes and bearer dependency."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import uuid
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from fastapi import HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials

from app.api.v2.auth_routes import (
    ProviderLoginMfaRequiredResponse,
    ProviderLoginRequest,
    ProviderMfaVerifyRequest,
    _clear_web_auth_cookies,
    _set_web_auth_cookies,
    provider_login,
    provider_mfa_verify,
    provider_refresh,
    provider_web_login,
)
from app.core.dependencies import get_current_provider
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.services.provider_auth_service import (
    ProviderAuthFailure,
    ProviderAuthResult,
    _consume_totp_counter,
    verify_totp_code,
)


def run(coro):
    return asyncio.run(coro)


def set_cookie_headers(response: Response) -> list[str]:
    return [
        value.decode("latin-1")
        for key, value in response.raw_headers
        if key.lower() == b"set-cookie"
    ]


class MockRequest:
    """Minimal request stand-in for route tests."""

    def __init__(self, user_agent: str = "TestAgent/1.0", client_ip: str = "10.0.0.1"):
        self.headers = {"user-agent": user_agent}
        self.client = SimpleNamespace(host=client_ip)
        self.cookies = {}


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
    @patch(
        "app.api.v2.auth_routes.issue_provider_session_token", new_callable=AsyncMock
    )
    @patch(
        "app.api.v2.auth_routes.authenticate_provider_password", new_callable=AsyncMock
    )
    def test_login_returns_bearer_token(
        self, mock_auth, mock_issue_token, mock_audit
    ) -> None:
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
            mfa_verified_at=None,
        )
        self.assertEqual(
            mock_audit.await_args.kwargs["event_type"], "PROVIDER_LOGIN_SUCCEEDED"
        )

    @patch("app.api.v2.auth_routes.append_audit_log", new_callable=AsyncMock)
    @patch(
        "app.api.v2.auth_routes.authenticate_provider_password", new_callable=AsyncMock
    )
    def test_login_rejects_invalid_credentials(self, mock_auth, mock_audit) -> None:
        mock_auth.return_value = ProviderAuthResult(
            None, ProviderAuthFailure.INVALID_CREDENTIALS
        )
        payload = ProviderLoginRequest(
            login_identifier="provider@example.com", password="bad"
        )
        request = MockRequest()

        with self.assertRaises(HTTPException) as cm:
            run(provider_login(payload, request=request, db=AsyncMock()))

        self.assertEqual(cm.exception.status_code, 401)
        self.assertEqual(
            mock_audit.await_args.kwargs["event_type"], "PROVIDER_LOGIN_FAILED"
        )


class TestProviderWebCookies(unittest.TestCase):
    def test_session_and_csrf_cookies_support_cross_site_production(self) -> None:
        response = Response()

        _set_web_auth_cookies(
            response,
            "session-token",
            datetime.now(timezone.utc) + timedelta(hours=1),
        )

        cookies = set_cookie_headers(response)
        self.assertEqual(len(cookies), 2)
        for cookie in cookies:
            self.assertIn("Secure", cookie)
            self.assertIn("SameSite=none", cookie)
        session_cookie = next(
            cookie for cookie in cookies if cookie.startswith("nexa_provider_session=")
        )
        csrf_cookie = next(
            cookie for cookie in cookies if cookie.startswith("nexa_csrf=")
        )
        self.assertIn("HttpOnly", session_cookie)
        self.assertNotIn("HttpOnly", csrf_cookie)

    @patch("app.api.v2.auth_routes.provider_login", new_callable=AsyncMock)
    def test_mfa_pending_and_csrf_cookies_support_cross_site_production(
        self, mock_login
    ) -> None:
        mock_login.return_value = ProviderLoginMfaRequiredResponse(
            detail="MFA verification required",
            mfa_token="mfa-pending-token",
        )
        response = Response()

        result = run(
            provider_web_login(
                ProviderLoginRequest(
                    login_identifier="provider@example.com",
                    password="secret",
                ),
                MockRequest(),
                response,
                AsyncMock(),
            )
        )

        self.assertEqual(result.status, "mfa_required")
        cookies = set_cookie_headers(response)
        self.assertEqual(len(cookies), 2)
        for cookie in cookies:
            self.assertIn("Secure", cookie)
            self.assertIn("SameSite=none", cookie)
        pending_cookie = next(
            cookie for cookie in cookies if cookie.startswith("nexa_mfa_pending=")
        )
        csrf_cookie = next(
            cookie for cookie in cookies if cookie.startswith("nexa_csrf=")
        )
        self.assertIn("HttpOnly", pending_cookie)
        self.assertNotIn("HttpOnly", csrf_cookie)

    def test_cleared_web_cookies_retain_cross_site_security_attributes(self) -> None:
        response = Response()

        _clear_web_auth_cookies(response)

        cookies = set_cookie_headers(response)
        self.assertEqual(len(cookies), 3)
        for cookie in cookies:
            self.assertIn("Secure", cookie)
            self.assertIn("SameSite=none", cookie)


class TestGetCurrentProvider(unittest.TestCase):
    @patch(
        "app.core.dependencies.authenticate_provider_session", new_callable=AsyncMock
    )
    def test_bearer_token_returns_provider_context(self, mock_auth) -> None:
        context = sample_provider_context()
        mock_auth.return_value = ProviderAuthResult(context)
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
        request = MockRequest(user_agent="TestAgent/1.0", client_ip="10.0.0.1")

        result = run(
            get_current_provider(
                request=request,
                credentials=credentials,
                hospital_id=None,
                db=AsyncMock(),
            )
        )

        self.assertEqual(result.provider, context.provider)
        self.assertEqual(result.hospital, context.hospital)
        self.assertEqual(result.affiliation, context.affiliation)
        self.assertIsNotNone(result.session_binding)
        mock_auth.assert_awaited_once()
        _, kwargs = mock_auth.await_args
        self.assertEqual(kwargs["user_agent"], "TestAgent/1.0")
        self.assertEqual(kwargs["client_ip"], "10.0.0.1")

    @patch("app.core.dependencies.append_audit_log", new_callable=AsyncMock)
    def test_missing_bearer_token_rejects(self, mock_audit) -> None:
        with self.assertRaises(HTTPException) as cm:
            run(
                get_current_provider(
                    request=MockRequest(),
                    credentials=None,
                    hospital_id=None,
                    db=AsyncMock(),
                )
            )

        self.assertEqual(cm.exception.status_code, 401)
        self.assertEqual(mock_audit.await_args.kwargs["status"], "MISSING_TOKEN")


class TestProviderRefresh(unittest.TestCase):
    @patch("app.api.v2.auth_routes.append_audit_log", new_callable=AsyncMock)
    @patch(
        "app.api.v2.auth_routes.refresh_provider_session_token", new_callable=AsyncMock
    )
    def test_refresh_rebinds_and_returns_new_token(
        self, mock_refresh, mock_audit
    ) -> None:
        context = sample_provider_context()
        mock_refresh.return_value = "refreshed-token"
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials="old-token"
        )

        result = run(
            provider_refresh(
                request=MockRequest(user_agent="NewAgent/2.0", client_ip="10.0.0.2"),
                credentials=credentials,
                provider=context,
            )
        )

        self.assertEqual(result.access_token, "refreshed-token")
        self.assertEqual(result.token_type, "bearer")
        mock_refresh.assert_awaited_once_with(
            "old-token",
            user_agent="NewAgent/2.0",
            client_ip="10.0.0.2",
        )
        self.assertEqual(
            mock_audit.await_args.kwargs["event_type"], "PROVIDER_SESSION_REFRESH"
        )


class TestMfaFlow(unittest.TestCase):
    @patch("app.api.v2.auth_routes.append_audit_log", new_callable=AsyncMock)
    @patch(
        "app.api.v2.auth_routes.authenticate_provider_password", new_callable=AsyncMock
    )
    def test_login_with_mfa_returns_pending_token(self, mock_auth, mock_audit) -> None:
        mock_auth.return_value = ProviderAuthResult(
            None,
            ProviderAuthFailure.MFA_REQUIRED,
            mfa_pending_token="mfa-pending-token",
        )
        payload = ProviderLoginRequest(
            login_identifier="provider@example.com", password="secret"
        )
        request = MockRequest()

        result = run(provider_login(payload, request=request, db=AsyncMock()))

        self.assertEqual(result.mfa_token, "mfa-pending-token")
        self.assertEqual(
            mock_audit.await_args.kwargs["event_type"], "PROVIDER_MFA_REQUIRED"
        )

    @patch("app.api.v2.auth_routes.append_audit_log", new_callable=AsyncMock)
    @patch(
        "app.api.v2.auth_routes.issue_provider_session_token", new_callable=AsyncMock
    )
    @patch("app.api.v2.auth_routes.complete_mfa_login", new_callable=AsyncMock)
    def test_mfa_verify_issues_bearer_token(
        self, mock_complete, mock_issue, mock_audit
    ) -> None:
        context = sample_provider_context()
        mock_complete.return_value = ProviderAuthResult(context)
        mock_issue.return_value = "final-session-token"
        payload = ProviderMfaVerifyRequest(
            mfa_token="mfa-pending-token", totp_code="123456"
        )
        request = MockRequest(user_agent="TestAgent/1.0", client_ip="10.0.0.1")

        result = run(provider_mfa_verify(payload, request=request, db=AsyncMock()))

        self.assertEqual(result.access_token, "final-session-token")
        mock_issue.assert_awaited_once_with(
            context.provider.provider_id,
            user_agent="TestAgent/1.0",
            client_ip="10.0.0.1",
            mfa_verified_at=mock_issue.await_args.kwargs["mfa_verified_at"],
        )
        self.assertIsNotNone(mock_issue.await_args.kwargs["mfa_verified_at"])
        mock_complete.assert_awaited_once()
        _, kwargs = mock_complete.await_args
        self.assertEqual(kwargs["client_ip"], "10.0.0.1")
        self.assertEqual(
            mock_audit.await_args.kwargs["event_type"], "PROVIDER_LOGIN_SUCCEEDED"
        )


class FakeReplayRedis:
    def __init__(self) -> None:
        self.data = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True


class TestMfaReplayProtection(unittest.TestCase):
    @patch("app.services.provider_auth_service.get_redis_client")
    def test_totp_counter_can_only_be_consumed_once(self, mock_redis) -> None:
        provider_id = uuid.uuid4()
        fake = FakeReplayRedis()
        mock_redis.return_value = fake

        self.assertTrue(run(_consume_totp_counter(provider_id, 12345)))
        self.assertFalse(run(_consume_totp_counter(provider_id, 12345)))
        self.assertTrue(run(_consume_totp_counter(provider_id, 12346)))

    @patch(
        "app.services.provider_auth_service.get_redis_client",
        side_effect=ConnectionError("redis down"),
    )
    def test_totp_replay_store_unavailable_fails_closed(self, _mock_redis) -> None:
        self.assertFalse(run(_consume_totp_counter(uuid.uuid4(), 12345)))

    def test_invalid_totp_code_still_fails(self) -> None:
        self.assertFalse(verify_totp_code("", "000000"))


if __name__ == "__main__":
    unittest.main()
