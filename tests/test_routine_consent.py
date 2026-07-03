"""Tests for Redis-backed routine consent."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.v2.consent_routes import RoutineConsentGrantResponse
from app.core.database import get_db_session
from app.core.dependencies import get_provider_context, require_active_consent
from app.main import app
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.services import consent_engine
from app.services.consent_engine import ConsentEngineUnavailable


def run(coro):
    return asyncio.run(coro)


def sample_provider_context() -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid.uuid4(),
            display_name="Dr. Consent Test",
            medical_registration_number="MCI-CONS-1",
            specialty="General Medicine",
            contact_email="consent@example.com",
        ),
        hospital=HospitalContext(
            hospital_id=uuid.uuid4(),
            facility_code="CONS-HOSP",
            display_name="Consent Test Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            department="OPD",
            roles=["routine_reader"],
            is_primary=True,
            valid_from=None,
            valid_until=None,
        ),
    )


class FakeRequest:
    def __init__(self, headers=None, path_params=None):
        self.headers = headers or {}
        self.path_params = path_params or {}


class TestRequireActiveConsent(unittest.TestCase):
    @patch("app.core.dependencies.validate_consent_capability", new_callable=AsyncMock)
    def test_dependency_accepts_matching_token(self, mock_validate) -> None:
        provider = sample_provider_context()
        mock_validate.return_value = SimpleNamespace(purpose="routine_access")
        request = FakeRequest(
            headers={"X-Consent-Token": "token"},
            path_params={"patient_id": "patient-1"},
        )

        result = run(require_active_consent(request=request, provider=provider))

        self.assertEqual(result, provider)
        mock_validate.assert_awaited_once_with(
            token="token",
            patient_id="patient-1",
            clinician_id=provider.actor_uid,
            purpose="routine_access",
        )

    def test_dependency_rejects_missing_header_or_patient_id(self) -> None:
        provider = sample_provider_context()

        with self.assertRaises(HTTPException) as cm:
            run(require_active_consent(FakeRequest(path_params={"patient_id": "patient-1"}), provider))
        self.assertEqual(cm.exception.status_code, 403)

        with self.assertRaises(HTTPException) as cm:
            run(require_active_consent(FakeRequest(headers={"X-Consent-Token": "token"}), provider))
        self.assertEqual(cm.exception.status_code, 403)

    @patch("app.core.dependencies.validate_consent_capability", new_callable=AsyncMock)
    def test_dependency_rejects_invalid_token(self, mock_validate) -> None:
        mock_validate.return_value = None
        provider = sample_provider_context()

        with self.assertRaises(HTTPException) as cm:
            run(require_active_consent(
                FakeRequest(
                    headers={"X-Consent-Token": "bad"},
                    path_params={"patient_id": "patient-1"},
                ),
                provider,
            ))

        self.assertEqual(cm.exception.status_code, 403)

    @patch("app.core.dependencies.validate_consent_capability", new_callable=AsyncMock)
    def test_dependency_returns_503_when_consent_store_unavailable(self, mock_validate) -> None:
        mock_validate.side_effect = ConsentEngineUnavailable("redis down")
        provider = sample_provider_context()

        with self.assertRaises(HTTPException) as cm:
            run(require_active_consent(
                FakeRequest(
                    headers={"X-Consent-Token": "token"},
                    path_params={"patient_id": "patient-1"},
                ),
                provider,
            ))

        self.assertEqual(cm.exception.status_code, 503)


class TestRoutineConsentRoute(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = sample_provider_context()

        async def override_provider() -> ProviderContext:
            return self.provider

        app.dependency_overrides[get_provider_context] = override_provider
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(get_provider_context, None)

    def setUp_db_override(self) -> None:
        async def override_db():
            yield object()

        app.dependency_overrides[get_db_session] = override_db

    @patch("app.api.v2.consent_routes.consent_engine.issue", new_callable=AsyncMock)
    def test_grant_route_returns_token(self, mock_issue) -> None:
        self.setUp_db_override()
        patient_id = uuid.uuid4()
        mock_issue.return_value = "test-token"

        try:
            response = self.client.post(
                "/api/v2/consent/grant",
                json={"patient_id": str(patient_id), "scope": ["clinical.diagnoses"]},
            )
        finally:
            app.dependency_overrides.pop(get_db_session, None)

        self.assertEqual(response.status_code, 200, response.text)
        body = RoutineConsentGrantResponse.model_validate_json(response.text)
        self.assertEqual(body.consent_token, "test-token")
        self.assertIsInstance(body.expires_at, datetime)
        issue_kwargs = mock_issue.await_args.kwargs
        self.assertEqual(issue_kwargs["patient_id"], str(patient_id))
        self.assertEqual(issue_kwargs["clinician_id"], self.provider.actor_uid)
        self.assertEqual(issue_kwargs["purpose"], "routine_access")
        self.assertEqual(issue_kwargs["scope"], ["clinical.diagnoses"])

    def test_grant_route_rejects_missing_scope(self) -> None:
        response = self.client.post(
            "/api/v2/consent/grant",
            json={"patient_id": str(uuid.uuid4())},
        )

        self.assertEqual(response.status_code, 422)

    @patch("app.api.v2.consent_routes.consent_engine.issue", new_callable=AsyncMock)
    def test_grant_route_returns_503_when_store_unavailable(self, mock_issue) -> None:
        self.setUp_db_override()
        mock_issue.side_effect = consent_engine.ConsentEngineUnavailable("redis down")

        try:
            response = self.client.post(
                "/api/v2/consent/grant",
                json={"patient_id": str(uuid.uuid4()), "scope": ["clinical.diagnoses"]},
            )
        finally:
            app.dependency_overrides.pop(get_db_session, None)

        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()