"""Tests for provider-centric auth routes and bearer dependency."""

from __future__ import annotations

import asyncio
import uuid
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api.v2.auth_routes import ProviderLoginRequest, provider_login
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

        result = run(provider_login(payload, db=AsyncMock()))

        self.assertEqual(result.access_token, "session-token")
        self.assertEqual(result.token_type, "bearer")
        self.assertEqual(result.provider_uid, context.actor_uid)
        self.assertEqual(result.hospital_id, context.hospital.hospital_id)
        mock_issue_token.assert_awaited_once_with(context.provider.provider_id)
        self.assertEqual(mock_audit.await_args.kwargs["event_type"], "PROVIDER_LOGIN_SUCCEEDED")

    @patch("app.api.v2.auth_routes.append_audit_log", new_callable=AsyncMock)
    @patch("app.api.v2.auth_routes.authenticate_provider_password", new_callable=AsyncMock)
    def test_login_rejects_invalid_credentials(self, mock_auth, mock_audit) -> None:
        mock_auth.return_value = ProviderAuthResult(None, ProviderAuthFailure.INVALID_CREDENTIALS)
        payload = ProviderLoginRequest(login_identifier="provider@example.com", password="bad")

        with self.assertRaises(HTTPException) as cm:
            run(provider_login(payload, db=AsyncMock()))

        self.assertEqual(cm.exception.status_code, 401)
        self.assertEqual(mock_audit.await_args.kwargs["event_type"], "PROVIDER_LOGIN_FAILED")


class TestGetCurrentProvider(unittest.TestCase):
    @patch("app.core.dependencies.authenticate_provider_session", new_callable=AsyncMock)
    def test_bearer_token_returns_provider_context(self, mock_auth) -> None:
        context = sample_provider_context()
        mock_auth.return_value = ProviderAuthResult(context)
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")

        result = run(get_current_provider(credentials=credentials, hospital_id=None, db=AsyncMock()))

        self.assertEqual(result, context)
        mock_auth.assert_awaited_once()

    @patch("app.core.dependencies.append_audit_log", new_callable=AsyncMock)
    def test_missing_bearer_token_rejects(self, mock_audit) -> None:
        with self.assertRaises(HTTPException) as cm:
            run(get_current_provider(credentials=None, hospital_id=None, db=AsyncMock()))

        self.assertEqual(cm.exception.status_code, 401)
        self.assertEqual(mock_audit.await_args.kwargs["status"], "MISSING_TOKEN")


if __name__ == "__main__":
    unittest.main()
