"""Tests for FHIR R4 clinical export."""

from __future__ import annotations

import uuid
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.core.database import get_db_session
from app.core.dependencies import (
    get_provider_context,
    require_active_consent,
    require_clinical_capability,
)
from app.main import app
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.models.patient_records import Allergy, LabResult, Medication, Vitals
from app.security.provider_capabilities import ClinicalCapability
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
        self.assertEqual(
            [resource["resourceType"] for resource in resources].count("Condition"), 2
        )
        self.assertEqual(
            [resource["resourceType"] for resource in resources].count(
                "MedicationRequest"
            ),
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
        app.dependency_overrides[
            require_clinical_capability(ClinicalCapability.RECORD_READ)
        ] = override_provider
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(get_provider_context, None)
        app.dependency_overrides.pop(
            require_clinical_capability(ClinicalCapability.RECORD_READ), None
        )

    @patch("app.services.consent_engine.validate", new_callable=AsyncMock)
    def test_export_without_active_consent_token_returns_403(self, mock_verify) -> None:
        response = self.client.get(f"/api/v2/fhir/export/{uuid.uuid4()}")

        self.assertEqual(response.status_code, 403)
        mock_verify.assert_not_awaited()


class FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeFHIRResult:
    def __init__(self, rows=None, legacy_rows=None):
        self._rows = rows or []
        self._legacy_rows = legacy_rows or []

    def scalars(self):
        return FakeScalars(self._rows)

    def fetchall(self):
        return self._legacy_rows


class FakeFHIRDB:
    def __init__(self, structured):
        self.structured = structured
        self.executed = []

    async def execute(self, stmt, params=None):
        self.executed.append(str(stmt).lower())
        sql = self.executed[-1]
        for table_name, rows in self.structured.items():
            if table_name in sql:
                return FakeFHIRResult(rows=rows)
        return FakeFHIRResult(rows=[], legacy_rows=[])


class TestFHIRStructuredExportRoute(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = sample_provider_context()
        self.patient_id = uuid.uuid4()
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.db = FakeFHIRDB(
            {
                "patient_vitals": [
                    Vitals(
                        patient_id=self.patient_id,
                        type="BP",
                        value="130/85",
                        unit="mmHg",
                        recorded_at=now,
                        source="manual",
                        risk_level="LOW_RISK",
                    )
                ],
                "patient_medications": [
                    Medication(
                        patient_id=self.patient_id,
                        name="Metformin",
                        strength="500mg",
                        frequency="Twice daily",
                        prescribed_at=now,
                        source="manual",
                        risk_level="MEDIUM_RISK",
                    )
                ],
                "patient_lab_results": [
                    LabResult(
                        patient_id=self.patient_id,
                        test_name="HbA1c",
                        value="7.2",
                        unit="%",
                        reference_range="4.0-5.6",
                        is_abnormal=True,
                        recorded_at=now,
                        source="manual",
                        risk_level="HIGH_RISK",
                    )
                ],
                "patient_allergies": [
                    Allergy(
                        patient_id=self.patient_id,
                        allergen="Penicillin",
                        severity="Severe",
                        source="manual",
                        risk_level="HIGH_RISK",
                    )
                ],
            }
        )

        async def override_provider() -> ProviderContext:
            return self.provider

        async def override_db_session():
            yield self.db

        app.dependency_overrides[require_active_consent] = override_provider
        app.dependency_overrides[
            require_clinical_capability(ClinicalCapability.RECORD_READ)
        ] = override_provider
        app.dependency_overrides[get_db_session] = override_db_session
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(require_active_consent, None)
        app.dependency_overrides.pop(
            require_clinical_capability(ClinicalCapability.RECORD_READ), None
        )
        app.dependency_overrides.pop(get_db_session, None)

    @patch("app.api.v2.fhir_routes.append_audit_log_or_503", new_callable=AsyncMock)
    def test_export_uses_structured_records_without_legacy_clinical(
        self, mock_audit
    ) -> None:
        response = self.client.get(f"/api/v2/fhir/export/{self.patient_id}")

        self.assertEqual(response.status_code, 200, response.text)
        resources = [entry["resource"] for entry in response.json()["entry"]]
        resource_types = {resource["resourceType"] for resource in resources}
        self.assertIn("MedicationRequest", resource_types)
        self.assertIn("Observation", resource_types)
        self.assertIn("AllergyIntolerance", resource_types)
        self.assertFalse(any("nexa_clinical" in stmt for stmt in self.db.executed))
        mock_audit.assert_awaited_once()
        self.assertEqual(
            mock_audit.await_args.kwargs["metadata"]["source"],
            "structured_patient_records",
        )

    @patch("app.api.v2.fhir_routes.append_audit_log_or_503", new_callable=AsyncMock)
    def test_empty_bundle_only_when_structured_and_legacy_records_empty(
        self, _mock_audit
    ) -> None:
        self.db.structured = {}
        response = self.client.get(f"/api/v2/fhir/export/{self.patient_id}")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["entry"], [])
        self.assertTrue(any("nexa_clinical" in stmt for stmt in self.db.executed))


if __name__ == "__main__":
    unittest.main()
