"""Tests for FHIR R4 clinical export."""

from __future__ import annotations

import uuid
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.core.dependencies import get_provider_context
from app.main import app
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.services.fhir_converter import generate_fhir_bundle


def sample_provider_context() -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid.uuid4(),
            display_name="Dr. FHIR Test",
            medical_registration_number="MCI-FHIR-1",
            specialty="Internal Medicine",
            contact_email="fhir@example.com",
        ),
        hospital=HospitalContext(
            hospital_id=uuid.uuid4(),
            facility_code="FHIR-HOSP",
            display_name="FHIR Test Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            department="Records",
            roles=["fhir_export"],
            is_primary=True,
            valid_from=None,
            valid_until=None,
        ),
    )


class TestFHIRConverter(unittest.TestCase):
    def test_generate_bundle_maps_conditions_and_medication_requests(self) -> None:
        patient_id = str(uuid.uuid4())
        bundle = generate_fhir_bundle(
            patient_id,
            [
                {
                    "diagnoses": ["Hypertension", "Type 2 Diabetes"],
                    "prescriptions": ["Metformin 500mg"],
                    "lab_results": ["HbA1c 7.2"],
                }
            ],
        )

        self.assertEqual(bundle["resourceType"], "Bundle")
        self.assertEqual(bundle["type"], "collection")
        resources = [entry["resource"] for entry in bundle["entry"]]
        self.assertEqual([resource["resourceType"] for resource in resources].count("Condition"), 2)
        self.assertEqual(
            [resource["resourceType"] for resource in resources].count("MedicationRequest"),
            1,
        )
        for resource in resources:
            self.assertIn("id", resource)
            self.assertEqual(resource["subject"]["reference"], f"Patient/{patient_id}")


class TestFHIRExportRoute(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = sample_provider_context()

        async def override_provider() -> ProviderContext:
            return self.provider

        app.dependency_overrides[get_provider_context] = override_provider
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(get_provider_context, None)

    @patch("app.services.consent_engine.validate", new_callable=AsyncMock)
    def test_export_without_active_consent_token_returns_403(self, mock_verify) -> None:
        response = self.client.get(f"/api/v2/fhir/export/{uuid.uuid4()}")

        self.assertEqual(response.status_code, 403)
        mock_verify.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
