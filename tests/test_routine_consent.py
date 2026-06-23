"""Tests for Redis-backed routine consent."""

from __future__ import annotations

import asyncio
import json
import unittest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.v2.consent_routes import RoutineConsentGrantResponse
from app.core.dependencies import get_provider_context, require_active_consent
from app.main import app
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.services import consent_service


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


class TestRoutineConsentService(unittest.TestCase):
    @patch("app.services.consent_service.secrets.token_urlsafe")
    @patch("app.services.consent_service.get_async_redis_client")
    def test_grant_stores_json_payload_with_one_hour_ttl(self, mock_get_redis, mock_token) -> None:
        redis = AsyncMock()
        mock_get_redis.return_value = redis
        mock_token.return_value = "secure-token"

        token = run(consent_service.grant_routine_consent("patient-1", "provider-1"))

        self.assertEqual(token, "nexa_cons_secure-token")
        redis.set.assert_awaited_once()
        args, kwargs = redis.set.await_args
        self.assertEqual(args[0], token)
        payload = json.loads(args[1])
        self.assertEqual(payload["patient_id"], "patient-1")
        self.assertEqual(payload["provider_uid"], "provider-1")
        self.assertIn("granted_at", payload)
        self.assertEqual(kwargs["ex"], 3600)

    @patch("app.services.consent_service.get_async_redis_client")
    def test_verify_returns_true_for_matching_live_token(self, mock_get_redis) -> None:
        redis = AsyncMock()
        redis.get.return_value = json.dumps({
            "patient_id": "patient-1",
            "provider_uid": "provider-1",
            "granted_at": "2026-06-23T00:00:00+00:00",
        })
        mock_get_redis.return_value = redis

        result = run(consent_service.verify_routine_consent("token", "patient-1", "provider-1"))

        self.assertTrue(result)
        redis.get.assert_awaited_once_with("token")

    @patch("app.services.consent_service.get_async_redis_client")
    def test_verify_fails_closed_on_mismatch_missing_or_redis_error(self, mock_get_redis) -> None:
        redis = AsyncMock()
        mock_get_redis.return_value = redis

        redis.get.return_value = json.dumps({"patient_id": "patient-1", "provider_uid": "other"})
        self.assertFalse(run(consent_service.verify_routine_consent("token", "patient-1", "provider-1")))

        redis.get.return_value = None
        self.assertFalse(run(consent_service.verify_routine_consent("token", "patient-1", "provider-1")))

        redis.get.side_effect = ConnectionError("redis down")
        self.assertFalse(run(consent_service.verify_routine_consent("token", "patient-1", "provider-1")))


class TestRequireActiveConsent(unittest.TestCase):
    @patch("app.core.dependencies.verify_routine_consent", new_callable=AsyncMock)
    def test_dependency_accepts_matching_token(self, mock_verify) -> None:
        provider = sample_provider_context()
        mock_verify.return_value = True
        request = FakeRequest(
            headers={"X-Consent-Token": "nexa_cons_token"},
            path_params={"patient_id": "patient-1"},
        )

        result = run(require_active_consent(request=request, provider=provider))

        self.assertEqual(result, provider)
        mock_verify.assert_awaited_once_with(
            token="nexa_cons_token",
            patient_id="patient-1",
            provider_uid=provider.actor_uid,
        )

    def test_dependency_rejects_missing_header_or_patient_id(self) -> None:
        provider = sample_provider_context()

        with self.assertRaises(HTTPException) as cm:
            run(require_active_consent(FakeRequest(path_params={"patient_id": "patient-1"}), provider))
        self.assertEqual(cm.exception.status_code, 403)

        with self.assertRaises(HTTPException) as cm:
            run(require_active_consent(FakeRequest(headers={"X-Consent-Token": "token"}), provider))
        self.assertEqual(cm.exception.status_code, 403)

    @patch("app.core.dependencies.verify_routine_consent", new_callable=AsyncMock)
    def test_dependency_rejects_invalid_token(self, mock_verify) -> None:
        mock_verify.return_value = False
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


class TestRoutineConsentRoute(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = sample_provider_context()

        async def override_provider() -> ProviderContext:
            return self.provider

        app.dependency_overrides[get_provider_context] = override_provider
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(get_provider_context, None)

    @patch("app.api.v2.consent_routes.append_audit_log_or_503", new_callable=AsyncMock)
    @patch("app.api.v2.consent_routes.grant_routine_consent", new_callable=AsyncMock)
    def test_grant_route_returns_token_and_audits(self, mock_grant, mock_audit) -> None:
        patient_id = uuid.uuid4()
        mock_grant.return_value = "nexa_cons_test-token"

        response = self.client.post(
            "/api/v2/consent/grant",
            json={"patient_id": str(patient_id)},
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = RoutineConsentGrantResponse.model_validate_json(response.text)
        self.assertEqual(body.consent_token, "nexa_cons_test-token")
        self.assertIsInstance(body.expires_at, datetime)
        mock_grant.assert_awaited_once_with(
            patient_id=str(patient_id),
            provider_uid=self.provider.actor_uid,
        )
        audit_kwargs = mock_audit.await_args.kwargs
        self.assertEqual(audit_kwargs["event_type"], "ROUTINE_CONSENT_GRANTED")
        self.assertEqual(audit_kwargs["target_id"], str(patient_id))
        self.assertEqual(audit_kwargs["metadata"]["provider_uid"], self.provider.actor_uid)

    @patch("app.api.v2.consent_routes.grant_routine_consent", new_callable=AsyncMock)
    def test_grant_route_returns_503_when_redis_unavailable(self, mock_grant) -> None:
        mock_grant.side_effect = consent_service.ConsentServiceUnavailable("redis down")

        response = self.client.post(
            "/api/v2/consent/grant",
            json={"patient_id": str(uuid.uuid4())},
        )

        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
