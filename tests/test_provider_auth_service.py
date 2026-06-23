"""Tests for provider authentication security behavior."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import datetime, timedelta, timezone
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
)


def run(coro):
    return asyncio.run(coro)


def make_provider_rows() -> tuple[ProviderIdentity, HospitalRegistry, ProviderHospitalAffiliation]:
    provider = ProviderIdentity(
        display_name="Dr. Auth Test",
        medical_registration_number="AUTH-1",
        specialty="Internal Medicine",
        contact_email="auth@example.com",
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


class TestProviderPasswordAuthentication(unittest.TestCase):
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

        result = run(authenticate_provider_password(db, provider.contact_email, "bad", None))

        self.assertIsNone(result.context)
        self.assertEqual(result.failure, ProviderAuthFailure.INVALID_CREDENTIALS)
        self.assertEqual(credential.failed_login_attempts, 3)
        self.assertIsNone(credential.locked_until)
        db.commit.assert_awaited_once()

    @patch("app.services.provider_auth_service.verify_provider_password")
    @patch("app.services.provider_auth_service.load_credential_by_login")
    def test_invalid_password_sets_lockout_at_threshold(
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

        result = run(authenticate_provider_password(db, provider.contact_email, "bad", None))

        self.assertEqual(result.failure, ProviderAuthFailure.INVALID_CREDENTIALS)
        self.assertEqual(credential.failed_login_attempts, 5)
        self.assertIsNotNone(credential.locked_until)
        assert credential.locked_until is not None
        self.assertGreater(credential.locked_until, datetime.now(timezone.utc) + timedelta(minutes=10))
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

        result = run(authenticate_provider_password(db, provider.contact_email, "good", None))

        self.assertIsNotNone(result.context)
        self.assertIsNone(result.failure)
        self.assertEqual(credential.failed_login_attempts, 0)
        self.assertIsNone(credential.locked_until)
        db.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
