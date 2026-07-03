"""Tests for app/services/auth_service.py: validate_session_context (the
legacy v1 session lookup used by GET /api/v1/record/{id} and
POST /request-consent) and session_authorizes_patient (a pure scope check,
currently unwired -- see its docstring in auth_service.py).

REGRESSION NOTE (2026-07-03): this file's entire original content was
found silently replaced with a near-duplicate of test_auth_routes.py
(provider login / MFA tests that already live there), which deleted all
coverage of validate_session_context with no test failure to signal it --
py_compile and even a full pytest run stay green either way, since the
duplicated tests are valid tests, just of the wrong module. Restored here.
"""

import asyncio
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.services.auth_service import session_authorizes_patient, validate_session_context


def run(coro):
    return asyncio.run(coro)


async def _resolved(value):
    return value


class TestValidateSessionContext(unittest.TestCase):

    def test_empty_token_returns_none(self):
        self.assertIsNone(run(validate_session_context("")))
        self.assertIsNone(run(validate_session_context(None)))

    def test_bearer_only_with_no_token_returns_none(self):
        self.assertIsNone(run(validate_session_context("Bearer ")))
        self.assertIsNone(run(validate_session_context("Bearer    ")))

    @patch("app.services.auth_service.get_redis_client")
    def test_strips_bearer_prefix_before_lookup(self, mock_get_client):
        fake_client = MagicMock()
        fake_client.get.return_value = '{"authenticated": true}'
        mock_get_client.return_value = fake_client

        result = run(validate_session_context("Bearer abc123"))

        fake_client.get.assert_called_once_with("abc123")
        self.assertEqual(result, {"authenticated": True})

    @patch("app.services.auth_service.get_redis_client")
    def test_valid_json_string_session_returns_dict(self, mock_get_client):
        fake_client = MagicMock()
        fake_client.get.return_value = '{"nfc_uid": "abc", "authenticated": true}'
        mock_get_client.return_value = fake_client

        result = run(validate_session_context("abc123"))

        self.assertEqual(result, {"nfc_uid": "abc", "authenticated": True})

    @patch("app.services.auth_service.get_redis_client")
    def test_valid_json_bytes_session_is_decoded(self, mock_get_client):
        fake_client = MagicMock()
        fake_client.get.return_value = b'{"authenticated": true}'
        mock_get_client.return_value = fake_client

        result = run(validate_session_context("abc123"))

        self.assertEqual(result, {"authenticated": True})

    @patch("app.services.auth_service.get_redis_client")
    def test_missing_session_returns_none(self, mock_get_client):
        fake_client = MagicMock()
        fake_client.get.return_value = None
        mock_get_client.return_value = fake_client

        self.assertIsNone(run(validate_session_context("expired-token")))

    @patch("app.services.auth_service.get_redis_client")
    def test_malformed_json_returns_none_not_an_exception(self, mock_get_client):
        fake_client = MagicMock()
        fake_client.get.return_value = "{not valid json"
        mock_get_client.return_value = fake_client

        self.assertIsNone(run(validate_session_context("abc123")))

    @patch("app.services.auth_service.get_redis_client")
    def test_awaitable_redis_client_is_awaited(self, mock_get_client):
        # Some redis clients (async ones) return a coroutine from .get();
        # validate_session_context must detect and await it.
        fake_client = MagicMock()
        fake_client.get.return_value = _resolved('{"authenticated": true}')
        mock_get_client.return_value = fake_client

        result = run(validate_session_context("abc123"))

        self.assertEqual(result, {"authenticated": True})

    @patch("app.services.auth_service.get_redis_client")
    def test_redis_connection_error_returns_none_not_raise(self, mock_get_client):
        mock_get_client.side_effect = ConnectionError("redis unreachable")

        # Must not propagate -- auth failures should fail closed, not 500.
        self.assertIsNone(run(validate_session_context("abc123")))


class TestSessionAuthorizesPatient(unittest.TestCase):
    """Companion coverage to tests/test_handshake_scoping.py, kept here
    too since this module (auth_service.py) is where the function lives.
    Not a duplicate suite -- test_handshake_scoping.py is the canonical
    scope-matching test; these two just confirm the None/empty-session
    guard clauses that live right at the top of the function.
    """

    def test_none_session_context_returns_false(self):
        self.assertFalse(session_authorizes_patient(None, str(uuid4())))

    def test_empty_dict_session_context_returns_false(self):
        self.assertFalse(session_authorizes_patient({}, str(uuid4())))


if __name__ == "__main__":
    unittest.main()