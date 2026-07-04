import asyncio
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBasicCredentials

from app.core.dependencies import get_provider_context, get_scoped_session
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
    """Minimal request stand-in for dependency tests."""

    def __init__(self, user_agent: str = "TestAgent/1.0", client_ip: str = "10.0.0.1"):
        self.headers = {"user-agent": user_agent, "x-forwarded-for": client_ip}
        self.client = None


def _sample_provider_context() -> ProviderContext:
    provider_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    affiliation_id = uuid.uuid4()
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=provider_id,
            display_name="Dr. Test Provider",
            medical_registration_number="MCI-12345",
            specialty="Cardiology",
            contact_email="doctor@example.com",
        ),
        hospital=HospitalContext(
            hospital_id=hospital_id,
            facility_code="HOSP-001",
            display_name="Test General Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=affiliation_id,
            affiliation_type=AffiliationType.PERMANENT,
            department="Cardiology",
            roles=["consultant"],
            is_primary=True,
            valid_from=None,
            valid_until=None,
        ),
    )


class TestGetScopedSession(unittest.TestCase):

    @patch("app.core.dependencies.append_audit_log")
    def test_missing_authorization_header_raises_401(self, mock_audit):
        with self.assertRaises(HTTPException) as cm:
            run(get_scoped_session(authorization=None))
        self.assertEqual(cm.exception.status_code, 401)
        mock_audit.assert_called_once()
        self.assertEqual(mock_audit.call_args.kwargs["status"], "MISSING_TOKEN")

    @patch("app.core.dependencies.validate_session_context")
    @patch("app.core.dependencies.append_audit_log")
    def test_invalid_or_expired_session_raises_401(self, mock_audit, mock_validate):
        mock_validate.return_value = None

        with self.assertRaises(HTTPException) as cm:
            run(get_scoped_session(authorization="Bearer some-token"))
        self.assertEqual(cm.exception.status_code, 401)
        self.assertEqual(mock_audit.call_args.kwargs["status"], "INVALID_OR_EXPIRED")

    @patch("app.core.dependencies.validate_session_context")
    @patch("app.core.dependencies.append_audit_log")
    def test_session_without_masked_internal_id_raises_401(self, mock_audit, mock_validate):
        mock_validate.return_value = {"authenticated": True, "nfc_uid": "NFC-001"}

        with self.assertRaises(HTTPException) as cm:
            run(get_scoped_session(authorization="Bearer some-token"))
        self.assertEqual(cm.exception.status_code, 401)
        self.assertEqual(mock_audit.call_args.kwargs["status"], "UNSCOPED_SESSION")

    @patch("app.core.dependencies.validate_session_context")
    @patch("app.core.dependencies.append_audit_log")
    def test_valid_scoped_session_returns_masked_internal_id(self, mock_audit, mock_validate):
        mock_validate.return_value = {
            "authenticated": True,
            "masked_internal_id": "patient-uuid-123",
            "nfc_uid": "NFC-001",
        }

        result = run(get_scoped_session(authorization="Bearer some-token"))

        self.assertEqual(result, "patient-uuid-123")
        mock_audit.assert_not_called()


class TestGetProviderContext(unittest.TestCase):

    @patch("app.core.dependencies.append_audit_log")
    def test_missing_credentials_raises_401(self, mock_audit):
        db = AsyncMock()
        with self.assertRaises(HTTPException) as cm:
            run(
                get_provider_context(
                    request=MockRequest(),
                    credentials=None,
                    basic_credentials=None,
                    hospital_id=None,
                    db=db,
                )
            )
        self.assertEqual(cm.exception.status_code, 401)
        self.assertEqual(mock_audit.call_args.kwargs["status"], "MISSING_TOKEN")

    @patch("app.core.dependencies.authenticate_provider_session")
    @patch("app.core.dependencies.append_audit_log")
    def test_invalid_bearer_token_raises_401(self, mock_audit, mock_auth):
        mock_auth.return_value = ProviderAuthResult(
            None,
            ProviderAuthFailure.INVALID_CREDENTIALS,
        )
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="bad-token")
        db = AsyncMock()

        with self.assertRaises(HTTPException) as cm:
            run(
                get_provider_context(
                    request=MockRequest(),
                    credentials=creds,
                    basic_credentials=None,
                    hospital_id=None,
                    db=db,
                )
            )
        self.assertEqual(cm.exception.status_code, 401)
        self.assertEqual(mock_audit.call_args.kwargs["status"], "INVALID_CREDENTIALS")

    @patch("app.core.dependencies.authenticate_provider_password")
    @patch("app.core.dependencies.append_audit_log")
    def test_affiliation_required_raises_400(self, mock_audit, mock_auth):
        mock_auth.return_value = ProviderAuthResult(
            None,
            ProviderAuthFailure.AFFILIATION_REQUIRED,
        )
        basic = HTTPBasicCredentials(username="doctor@example.com", password="secret")
        db = AsyncMock()

        with self.assertRaises(HTTPException) as cm:
            run(
                get_provider_context(
                    request=MockRequest(),
                    credentials=None,
                    basic_credentials=basic,
                    hospital_id=None,
                    db=db,
                )
            )
        self.assertEqual(cm.exception.status_code, 400)
        self.assertEqual(mock_audit.call_args.kwargs["status"], "AFFILIATION_REQUIRED")

    @patch("app.core.dependencies.authenticate_provider_password")
    @patch("app.core.dependencies.append_audit_log")
    def test_mfa_enabled_account_gets_401_with_mfa_flow_hint(self, mock_audit, mock_auth):
        """MFA is implemented via /login + /mfa/verify. Routes that use this
        dependency should not accept a half-authenticated password-only
        session; they must return 401 with a hint to complete the MFA flow.
        """
        mock_auth.return_value = ProviderAuthResult(
            None,
            ProviderAuthFailure.MFA_REQUIRED,
        )
        basic = HTTPBasicCredentials(username="mfa-doctor@example.com", password="correct-password")
        db = AsyncMock()

        with self.assertRaises(HTTPException) as cm:
            run(
                get_provider_context(
                    request=MockRequest(),
                    credentials=None,
                    basic_credentials=basic,
                    hospital_id=None,
                    db=db,
                )
            )
        self.assertEqual(cm.exception.status_code, 401)
        self.assertIn("/api/v2/auth/mfa/verify", cm.exception.detail)
        self.assertEqual(mock_audit.call_args.kwargs["status"], "MFA_REQUIRED")

    @patch("app.core.dependencies.authenticate_provider_session")
    @patch("app.core.dependencies.append_audit_log")
    def test_valid_bearer_session_returns_provider_context(self, mock_audit, mock_auth):
        context = _sample_provider_context()
        mock_auth.return_value = ProviderAuthResult(context)
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="valid-token")
        db = AsyncMock()

        result = run(
            get_provider_context(
                request=MockRequest(),
                credentials=creds,
                basic_credentials=None,
                hospital_id=context.hospital.hospital_id,
                db=db,
            )
        )

        self.assertEqual(result, context)
        mock_audit.assert_not_called()

    @patch("app.core.dependencies.authenticate_provider_password")
    @patch("app.core.dependencies.append_audit_log")
    def test_valid_basic_auth_returns_provider_context(self, mock_audit, mock_auth):
        context = _sample_provider_context()
        mock_auth.return_value = ProviderAuthResult(context)
        basic = HTTPBasicCredentials(username="doctor@example.com", password="secret")
        db = AsyncMock()

        result = run(
            get_provider_context(
                request=MockRequest(),
                credentials=None,
                basic_credentials=basic,
                hospital_id=context.hospital.hospital_id,
                db=db,
            )
        )

        self.assertEqual(result, context)
        mock_auth.assert_awaited_once_with(
            db,
            basic.username,
            basic.password,
            context.hospital.hospital_id,
        )
        awaited_result = mock_auth.return_value
        self.assertEqual(awaited_result.context, context)
        mock_audit.assert_not_called()

    @patch("app.core.dependencies.authenticate_provider_session")
    @patch("app.core.dependencies.append_audit_log")
    def test_ip_rotation_warning_is_allowed_and_logged(self, mock_audit, mock_auth):
        context = _sample_provider_context()
        mock_auth.return_value = ProviderAuthResult(
            context, binding_warning="SESSION_IP_ROTATION_DETECTED"
        )
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="valid-token")
        db = AsyncMock()

        with patch("app.core.dependencies.logger") as mock_logger:
            result = run(
                get_provider_context(
                    request=MockRequest(user_agent="TestAgent/1.0", client_ip="10.0.0.2"),
                    credentials=creds,
                    basic_credentials=None,
                    hospital_id=context.hospital.hospital_id,
                    db=db,
                )
            )

        self.assertEqual(result, context)
        mock_logger.warning.assert_called_once()
        self.assertIn("SESSION_IP_ROTATION_DETECTED", str(mock_logger.warning.call_args))


if __name__ == "__main__":
    unittest.main()