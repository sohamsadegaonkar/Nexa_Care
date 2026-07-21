"""Tests for provider authentication security behavior."""

from __future__ import annotations

import asyncio
import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

from app.models.provider import (
    AffiliationType,
    HospitalRegistry,
    ProviderCredential,
    ProviderHospitalAffiliation,
    ProviderIdentity,
)
from app.services.provider_auth_service import (
    ProviderAuthFailure,
    authenticate_provider_password,
    authenticate_provider_session,
    complete_mfa_login,
    hash_client_ip,
    hash_user_agent,
    hash_provider_password,
    normalize_provider_login_identifier,
    revoke_provider_auth_sessions,
    refresh_provider_session_token,
    resolve_provider_session_context,
)


def run(coro):
    return asyncio.run(coro)


def make_provider_rows() -> (
    tuple[ProviderIdentity, HospitalRegistry, ProviderHospitalAffiliation]
):
    provider = ProviderIdentity(
        display_name="Dr. Auth Test",
        medical_registration_number="AUTH-1",
        specialty="Internal Medicine",
        contact_email="auth@example.com",
        status="active",
        is_active=True,
    )
    provider.id = uuid.uuid4()

    hospital = HospitalRegistry(
        facility_code="AUTH-HOSP",
        legal_name="Auth Test Hospital",
        display_name="Auth Test Hospital",
        country_code="IN",
        is_active=True,
    )
    hospital.id = uuid.uuid4()

    affiliation = ProviderHospitalAffiliation(
        provider_id=provider.id,
        hospital_id=hospital.id,
        affiliation_type=AffiliationType.PERMANENT.value,
        department="Security",
        roles=["provider"],
        is_primary=True,
        is_active=True,
    )
    affiliation.id = uuid.uuid4()
    affiliation.hospital = hospital
    provider.affiliations = [affiliation]
    return provider, hospital, affiliation


def make_credential(provider: ProviderIdentity) -> ProviderCredential:
    credential = ProviderCredential(
        provider_id=provider.id,
        login_identifier=provider.contact_email,
        password_hash="hash",
        mfa_enabled=False,
        failed_login_attempts=0,
        locked_until=None,
        is_active=True,
    )
    credential.id = uuid.uuid4()
    credential.provider = provider
    return credential


class FakeRedis:
    """In-memory synchronous Redis stand-in for provider auth tests."""

    def __init__(self):
        self._store: dict[str, Any] = {}
        self._pipeline_key: str | None = None

    def get(self, key: str) -> Any:
        return self._store.get(key)

    def setex(self, key: str, time: int, value: str) -> bool:
        self._store[key] = value
        return True

    def delete(self, key: str) -> int:
        self._store.pop(key, None)
        return 1

    def pipeline(self):
        return self

    def incr(self, key: str) -> int:
        self._pipeline_key = key
        self._store[key] = int(self._store.get(key, 0)) + 1
        return self._store[key]

    def expire(self, key: str, time: int) -> bool:
        return True

    def execute(self) -> list[Any]:
        # Return the post-incr count and the expire result.
        return [self._store.get(self._pipeline_key, 0), True]

    def scan(self, cursor=0, match=None, count=None):
        prefix = str(match or "").removesuffix("*")
        return 0, [key for key in self._store if key.startswith(prefix)]


class TestProviderPasswordAuthentication(unittest.TestCase):
    def test_password_hash_round_trip(self) -> None:
        password_hash = hash_provider_password("Strong-Test-Password-42!")
        from app.services.provider_auth_service import verify_provider_password

        self.assertTrue(
            verify_provider_password("Strong-Test-Password-42!", password_hash)
        )
        self.assertFalse(verify_provider_password("wrong", password_hash))

    def test_login_identifier_normalization(self) -> None:
        self.assertEqual(
            normalize_provider_login_identifier("  Doctor@Example.COM  "),
            "doctor@example.com",
        )

    @patch("app.services.provider_auth_service.verify_provider_password")
    @patch("app.services.provider_auth_service.load_credential_by_login")
    def test_invalid_password_increments_failed_attempts_and_commits(
        self,
        mock_load_credential,
        mock_verify,
    ) -> None:
        provider, _, _ = make_provider_rows()
        credential = make_credential(provider)
        credential.failed_login_attempts = 2
        db = AsyncMock()
        mock_load_credential.return_value = credential
        mock_verify.return_value = False

        result = run(
            authenticate_provider_password(db, provider.contact_email, "bad", None)
        )

        self.assertIsNone(result.context)
        self.assertEqual(result.failure, ProviderAuthFailure.INVALID_CREDENTIALS)
        self.assertEqual(credential.failed_login_attempts, 3)
        self.assertIsNone(credential.locked_until)
        db.commit.assert_awaited_once()

    @patch("app.services.provider_auth_service.verify_provider_password")
    @patch("app.services.provider_auth_service.load_credential_by_login")
    def test_invalid_password_does_not_create_global_account_lockout(
        self,
        mock_load_credential,
        mock_verify,
    ) -> None:
        provider, _, _ = make_provider_rows()
        credential = make_credential(provider)
        credential.failed_login_attempts = 4
        db = AsyncMock()
        mock_load_credential.return_value = credential
        mock_verify.return_value = False

        result = run(
            authenticate_provider_password(db, provider.contact_email, "bad", None)
        )

        self.assertEqual(result.failure, ProviderAuthFailure.INVALID_CREDENTIALS)
        self.assertEqual(credential.failed_login_attempts, 5)
        # Abuse controls are keyed by source IP and a privacy-preserving target
        # digest in Redis; one attacker must not globally lock the account.
        self.assertIsNone(credential.locked_until)
        db.commit.assert_awaited_once()

    @patch("app.services.provider_auth_service.verify_provider_password")
    @patch("app.services.provider_auth_service.load_provider_with_affiliations")
    @patch("app.services.provider_auth_service.load_credential_by_login")
    def test_successful_password_login_resets_failed_attempts_and_lockout(
        self,
        mock_load_credential,
        mock_load_provider,
        mock_verify,
    ) -> None:
        provider, _, _ = make_provider_rows()
        credential = make_credential(provider)
        credential.failed_login_attempts = 3
        credential.locked_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        db = AsyncMock()
        mock_load_credential.return_value = credential
        mock_load_provider.return_value = provider
        mock_verify.return_value = True

        result = run(
            authenticate_provider_password(db, provider.contact_email, "good", None)
        )

        self.assertIsNotNone(result.context)
        self.assertIsNone(result.failure)
        self.assertEqual(credential.failed_login_attempts, 0)
        self.assertIsNone(credential.locked_until)
        db.commit.assert_awaited_once()

    @patch("app.services.provider_auth_service.verify_provider_password")
    @patch("app.services.provider_auth_service.load_credential_by_login")
    def test_canonical_password_hash_is_authoritative(
        self, mock_load, mock_verify
    ) -> None:
        provider, _, _ = make_provider_rows()
        credential = make_credential(provider)
        credential.password_hash = "canonical-hash"
        credential.hashed_password = "legacy-hash"
        mock_load.return_value = credential
        mock_verify.return_value = False

        result = run(
            authenticate_provider_password(
                AsyncMock(), provider.contact_email, "bad", None
            )
        )

        self.assertEqual(result.failure, ProviderAuthFailure.INVALID_CREDENTIALS)
        mock_verify.assert_called_once_with("bad", "canonical-hash")

    @patch("app.services.provider_auth_service.load_credential_by_login")
    def test_disabled_provider_status_is_rejected(self, mock_load) -> None:
        provider, _, _ = make_provider_rows()
        provider.status = "suspended"
        mock_load.return_value = make_credential(provider)

        result = run(
            authenticate_provider_password(
                AsyncMock(), provider.contact_email, "good", None
            )
        )

        self.assertEqual(result.failure, ProviderAuthFailure.PROVIDER_INACTIVE)

    @patch("app.services.provider_auth_service.verify_provider_password")
    @patch("app.services.provider_auth_service.load_credential_by_login")
    def test_unknown_provider_runs_dummy_verification(
        self, mock_load, mock_verify
    ) -> None:
        mock_load.return_value = None
        result = run(
            authenticate_provider_password(
                AsyncMock(), "missing@example.com", "guess", None
            )
        )

        self.assertEqual(result.failure, ProviderAuthFailure.INVALID_CREDENTIALS)
        mock_verify.assert_called_once()

    @patch("app.services.provider_auth_service.load_credential_by_login")
    def test_active_lockout_is_rejected(self, mock_load) -> None:
        provider, _, _ = make_provider_rows()
        credential = make_credential(provider)
        credential.locked_until = datetime.now(timezone.utc) + timedelta(minutes=5)
        mock_load.return_value = credential

        result = run(
            authenticate_provider_password(
                AsyncMock(), provider.contact_email, "good", None
            )
        )

        self.assertEqual(result.failure, ProviderAuthFailure.ACCOUNT_LOCKED)


class TestProviderSessionRevocation(unittest.TestCase):
    @patch("app.services.provider_auth_service.get_redis_client")
    def test_revokes_bearer_and_pending_mfa_for_only_target_provider(
        self, mock_redis
    ) -> None:
        target = uuid.uuid4()
        other = uuid.uuid4()
        fake = FakeRedis()
        fake._store = {
            "provider_session:target": json.dumps({"provider_id": str(target)}),
            "provider_session:other": json.dumps({"provider_id": str(other)}),
            "mfa_pending:target": json.dumps({"provider_id": str(target)}),
        }
        mock_redis.return_value = fake

        revoked = run(revoke_provider_auth_sessions(target))

        self.assertEqual(revoked, 2)
        self.assertNotIn("provider_session:target", fake._store)
        self.assertNotIn("mfa_pending:target", fake._store)
        self.assertIn("provider_session:other", fake._store)


class TestProviderSessionBinding(unittest.TestCase):
    @patch("app.services.provider_auth_service.get_redis_client")
    @patch("app.services.provider_auth_service.load_provider_with_affiliations")
    def test_ua_mismatch_returns_session_binding_mismatch(self, mock_load, mock_redis):
        provider, _, _ = make_provider_rows()
        credential = make_credential(provider)
        credential.mfa_enabled = False
        provider.credential = credential
        mock_load.return_value = provider
        fake_redis = FakeRedis()
        fake_redis._store["provider_session:token"] = json.dumps(
            {
                "authenticated": True,
                "provider_id": str(provider.id),
                "ua_hash": hash_user_agent("OriginalAgent/1.0"),
                "ip_hash": hash_client_ip("10.0.0.1"),
            }
        )
        mock_redis.return_value = fake_redis

        result = run(
            authenticate_provider_session(
                AsyncMock(),
                "token",
                None,
                user_agent="DifferentAgent/2.0",
                client_ip="10.0.0.1",
            )
        )

        self.assertIsNone(result.context)
        self.assertEqual(result.failure, ProviderAuthFailure.SESSION_BINDING_MISMATCH)

    @patch("app.services.provider_auth_service.get_redis_client")
    @patch("app.services.provider_auth_service.load_provider_with_affiliations")
    def test_ip_mismatch_returns_warning_not_failure(self, mock_load, mock_redis):
        provider, _, _ = make_provider_rows()
        credential = make_credential(provider)
        credential.mfa_enabled = False
        provider.credential = credential
        mock_load.return_value = provider
        fake_redis = FakeRedis()
        fake_redis._store["provider_session:token"] = json.dumps(
            {
                "authenticated": True,
                "provider_id": str(provider.id),
                "ua_hash": hash_user_agent("SameAgent/1.0"),
                "ip_hash": hash_client_ip("10.0.0.1"),
            }
        )
        mock_redis.return_value = fake_redis

        result = run(
            authenticate_provider_session(
                AsyncMock(),
                "token",
                None,
                user_agent="SameAgent/1.0",
                client_ip="10.0.0.2",
            )
        )

        self.assertIsNotNone(result.context)
        self.assertEqual(result.binding_warning, "SESSION_IP_ROTATION_DETECTED")


class TestProviderSessionRefresh(unittest.TestCase):
    @patch("app.services.provider_auth_service.get_redis_client")
    def test_refresh_rebinds_new_token_to_current_context(self, mock_redis):
        provider_id = uuid.uuid4()
        fake_redis = FakeRedis()
        fake_redis._store["provider_session:old-token"] = json.dumps(
            {
                "authenticated": True,
                "provider_id": str(provider_id),
                "ua_hash": hash_user_agent("OldAgent/1.0"),
                "ip_hash": hash_client_ip("10.0.0.1"),
            }
        )
        mock_redis.return_value = fake_redis

        new_token = run(
            refresh_provider_session_token(
                "old-token",
                user_agent="NewAgent/2.0",
                client_ip="10.0.0.2",
            )
        )

        self.assertIsNotNone(new_token)
        self.assertNotEqual(new_token, "old-token")
        # Old token must be burned.
        self.assertIsNone(fake_redis._store.get("provider_session:old-token"))
        context = run(resolve_provider_session_context(new_token))
        self.assertIsNotNone(context)
        self.assertEqual(context["ua_hash"], hash_user_agent("NewAgent/2.0"))
        self.assertEqual(context["ip_hash"], hash_client_ip("10.0.0.2"))


class TestMfaCompositeLockout(unittest.TestCase):
    @patch(
        "app.services.provider_auth_service.delete_mfa_pending_token",
        new_callable=AsyncMock,
    )
    @patch(
        "app.services.provider_auth_service.resolve_mfa_pending_token",
        new_callable=AsyncMock,
    )
    def test_missing_pending_token_is_reported_as_expired(
        self, mock_resolve, mock_delete
    ):
        mock_resolve.return_value = None

        result = run(complete_mfa_login(AsyncMock(), "expired-token", "000000", None))

        self.assertEqual(result.failure, ProviderAuthFailure.MFA_SESSION_EXPIRED)
        mock_delete.assert_awaited_once_with("expired-token")

    @patch("app.services.provider_auth_service.get_redis_client")
    @patch("app.services.provider_auth_service.load_provider_with_affiliations")
    @patch("app.services.provider_auth_service.decrypt_mfa_secret")
    @patch("app.services.provider_auth_service.verify_totp_code")
    @patch(
        "app.services.provider_auth_service.resolve_mfa_pending_token",
        new_callable=AsyncMock,
    )
    def test_failed_mfa_tracks_composite_key_and_rate_limits(
        self,
        mock_resolve,
        mock_verify_totp,
        mock_decrypt,
        mock_load,
        mock_redis,
    ):
        from app.services.provider_auth_service import _MAX_FAILED_MFA_ATTEMPTS

        provider, hospital, affiliation = make_provider_rows()
        credential = make_credential(provider)
        credential.mfa_enabled = True
        credential.mfa_secret_encrypted = "encrypted-secret"
        provider.credential = credential
        mock_load.return_value = provider
        mock_decrypt.return_value = "secret"
        mock_verify_totp.return_value = False
        mock_resolve.return_value = provider.id
        fake_redis = FakeRedis()
        mock_redis.return_value = fake_redis
        db = AsyncMock()

        client_ip = "10.0.0.1"
        ip_hash = hash_client_ip(client_ip)
        for attempt in range(1, _MAX_FAILED_MFA_ATTEMPTS + 1):
            result = run(
                complete_mfa_login(db, "mfa-token", "000000", None, client_ip=client_ip)
            )
            if attempt < _MAX_FAILED_MFA_ATTEMPTS:
                self.assertEqual(result.failure, ProviderAuthFailure.MFA_INVALID_CODE)
            else:
                self.assertEqual(result.failure, ProviderAuthFailure.MFA_RATE_LIMITED)

        key = f"mfa_fails:{provider.id}:{ip_hash}"
        self.assertEqual(fake_redis._store.get(key), _MAX_FAILED_MFA_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
