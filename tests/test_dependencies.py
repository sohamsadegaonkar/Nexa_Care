import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.core.dependencies import get_scoped_session


def run(coro):
    return asyncio.run(coro)


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
        # patch() auto-detects validate_session_context is async and uses
        # AsyncMock, so return_value is the resolved value, not a coroutine.
        mock_validate.return_value = None

        with self.assertRaises(HTTPException) as cm:
            run(get_scoped_session(authorization="Bearer some-token"))
        self.assertEqual(cm.exception.status_code, 401)
        self.assertEqual(mock_audit.call_args.kwargs["status"], "INVALID_OR_EXPIRED")

    @patch("app.core.dependencies.validate_session_context")
    @patch("app.core.dependencies.append_audit_log")
    def test_session_without_masked_internal_id_raises_401(self, mock_audit, mock_validate):
        # Simulates a pre-fix session that never had an id bound to it --
        # must fail closed, not silently let an unscoped session through.
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
        mock_audit.assert_not_called()  # success path logs nothing here -- the route does


class TestVerifyProviderToken(unittest.TestCase):

    @patch("app.core.dependencies.append_audit_log")
    def test_missing_credentials_raises_401(self, mock_audit):
        from app.core.dependencies import verify_provider_token

        with self.assertRaises(HTTPException) as cm:
            run(verify_provider_token(credentials=None))
        self.assertEqual(cm.exception.status_code, 401)
        self.assertEqual(mock_audit.call_args.kwargs["status"], "MISSING_TOKEN")

    @patch("app.core.dependencies.append_audit_log")
    def test_empty_credentials_string_raises_401(self, mock_audit):
        from app.core.dependencies import verify_provider_token
        from fastapi.security import HTTPAuthorizationCredentials

        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="")
        with self.assertRaises(HTTPException) as cm:
            run(verify_provider_token(credentials=creds))
        self.assertEqual(cm.exception.status_code, 401)
        self.assertEqual(mock_audit.call_args.kwargs["status"], "MISSING_TOKEN")

    @patch("app.core.dependencies.get_clinic_config")
    @patch("app.core.dependencies.append_audit_log")
    def test_wrong_token_raises_401(self, mock_audit, mock_config):
        from app.core.dependencies import verify_provider_token
        from fastapi.security import HTTPAuthorizationCredentials
        from app.core.config import ClinicConfig

        mock_config.return_value = ClinicConfig(api_key="correct-key")
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong-key")

        with self.assertRaises(HTTPException) as cm:
            run(verify_provider_token(credentials=creds))
        self.assertEqual(cm.exception.status_code, 401)
        self.assertEqual(mock_audit.call_args.kwargs["status"], "INVALID_TOKEN")

    @patch("app.core.dependencies.get_clinic_config")
    @patch("app.core.dependencies.append_audit_log")
    def test_correct_token_passes_silently(self, mock_audit, mock_config):
        from app.core.dependencies import verify_provider_token
        from fastapi.security import HTTPAuthorizationCredentials
        from app.core.config import ClinicConfig

        mock_config.return_value = ClinicConfig(api_key="correct-key")
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="correct-key")

        result = run(verify_provider_token(credentials=creds))

        self.assertIsNone(result)
        mock_audit.assert_not_called()

    @patch("app.core.dependencies.get_clinic_config")
    @patch("app.core.dependencies.append_audit_log")
    def test_uses_constant_time_comparison_not_eq(self, mock_audit, mock_config):
        # Regression guard: ensure hmac.compare_digest is actually used,
        # not a plain `==`, by patching it and asserting it was called
        # with exactly the supplied and expected values.
        from app.core.config import ClinicConfig
        import app.core.dependencies as deps_module

        mock_config.return_value = ClinicConfig(api_key="correct-key")
        creds = deps_module.HTTPAuthorizationCredentials(scheme="Bearer", credentials="correct-key")

        with patch("app.core.dependencies.hmac.compare_digest", wraps=__import__("hmac").compare_digest) as mock_cmp:
            run(deps_module.verify_provider_token(credentials=creds))
            mock_cmp.assert_called_once_with("correct-key", "correct-key")


if __name__ == "__main__":
    unittest.main()