"""Tests for the dedicated break-glass emergency-summary endpoint (Defect 2)."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api.v2.patient_routes import get_emergency_summary
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.models.patient_records import (
    Allergy,
    DocumentReference,
    LabResult,
    Medication,
    Vitals,
)
from app.services.consent_engine import ConsentCapability
from app.services.emergency_summary_service import build_emergency_summary
from app.security.clinical_categories import ClinicalCategory


def run(coro):
    return asyncio.run(coro)


class FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeRowsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return FakeScalars(self._rows)


class FakeDB:
    def __init__(self, structured: dict[str, list[object]] | None = None):
        self.structured = structured or {}

    async def execute(self, stmt, params=None):
        sql = str(stmt).lower()
        for table_name, rows in self.structured.items():
            if table_name in sql:
                return FakeRowsResult(rows)
        return FakeRowsResult([])


def sample_provider_context() -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid.uuid4(),
            display_name="Dr. Emergency Test",
            medical_registration_number="MCI-ER-1",
            specialty="Emergency Medicine",
            contact_email="er@example.com",
        ),
        hospital=HospitalContext(
            hospital_id=uuid.uuid4(),
            facility_code="ER-HOSP",
            display_name="Emergency Test Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            department="Emergency",
            roles=["emergency_reader"],
            is_primary=True,
            valid_from=None,
            valid_until=None,
        ),
        session_binding="session-abc",
    )


def _break_glass_capability(
    provider: ProviderContext, patient_id: str, scope: list[str]
) -> ConsentCapability:
    now = datetime.now(timezone.utc)
    return ConsentCapability(
        patient_id=patient_id,
        clinician_id=provider.actor_uid,
        purpose="EMERGENCY",
        scope=scope,
        is_break_glass=True,
        reason_code="LIFE_THREATENING_EMERGENCY",
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=15)).isoformat(),
        hospital_id=str(provider.hospital_id),
        session_binding=provider.session_binding,
        reason_code_version="v1",
        category_protocol_version="2026-07-v1",
    )


class TestEmergencySummaryService(unittest.TestCase):
    def test_only_requested_categories_are_built(self) -> None:
        patient_id = uuid.uuid4()
        db = FakeDB(
            structured={
                "patient_allergies": [
                    Allergy(
                        patient_id=patient_id,
                        allergen="Penicillin",
                        severity="Severe",
                        source="manual",
                    )
                ],
                "patient_medications": [
                    Medication(
                        patient_id=patient_id,
                        name="Metformin",
                        strength="500mg",
                        frequency="Twice daily",
                        prescribed_at=datetime.now(timezone.utc),
                        source="manual",
                    )
                ],
            }
        )

        summary = run(
            build_emergency_summary(patient_id, [ClinicalCategory.ALLERGIES], db)
        )

        self.assertIn("allergies", summary.categories)
        self.assertNotIn("active_medications", summary.categories)
        self.assertTrue(summary.categories["allergies"]["available"])
        self.assertEqual(
            summary.categories["allergies"]["items"][0]["allergen"], "Penicillin"
        )

    def test_blood_group_never_returns_unverified_value(self) -> None:
        patient_id = uuid.uuid4()
        db = FakeDB()

        summary = run(
            build_emergency_summary(patient_id, [ClinicalCategory.BLOOD_GROUP], db)
        )

        blood_group = summary.categories["blood_group"]
        self.assertFalse(blood_group["available"])
        self.assertIsNone(blood_group["value"])
        self.assertFalse(blood_group["verified"])

    def test_document_references_omit_storage_ref(self) -> None:
        patient_id = uuid.uuid4()
        db = FakeDB(
            structured={
                "document_references": [
                    DocumentReference(
                        patient_id=patient_id,
                        document_type="discharge_summary",
                        uploaded_at=datetime.now(timezone.utc),
                        storage_ref="s3://secret-bucket/should-not-leak",
                    )
                ]
            }
        )

        summary = run(
            build_emergency_summary(
                patient_id, [ClinicalCategory.DOCUMENT_REFERENCES], db
            )
        )

        item = summary.categories["document_references"]["items"][0]
        self.assertNotIn("storage_ref", item)
        self.assertEqual(item["document_type"], "discharge_summary")


class TestEmergencySummaryRoute(unittest.TestCase):
    def test_missing_token_is_rejected(self) -> None:
        provider = sample_provider_context()
        with self.assertRaises(HTTPException) as ctx:
            run(
                get_emergency_summary(
                    patient_id=uuid.uuid4(),
                    consent_token=None,
                    provider=provider,
                    db=FakeDB(),
                )
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_routine_capability_is_rejected(self) -> None:
        provider = sample_provider_context()
        patient_id = uuid.uuid4()
        capability = ConsentCapability(
            patient_id=str(patient_id),
            clinician_id=provider.actor_uid,
            purpose="EMERGENCY",
            scope=["allergies"],
            is_break_glass=False,  # routine, not break-glass
            reason_code=None,
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            hospital_id=str(provider.hospital_id),
            session_binding=provider.session_binding,
        )

        with patch(
            "app.api.v2.patient_routes.consent_engine.validate",
            AsyncMock(return_value=capability),
        ):
            with self.assertRaises(HTTPException) as ctx:
                run(
                    get_emergency_summary(
                        patient_id=patient_id,
                        consent_token="tok",
                        provider=provider,
                        db=FakeDB(),
                    )
                )
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(
            ctx.exception.detail["error_code"], "BREAK_GLASS_CAPABILITY_REQUIRED"
        )

    def test_invalid_or_expired_capability_is_rejected(self) -> None:
        provider = sample_provider_context()
        with patch(
            "app.api.v2.patient_routes.consent_engine.validate",
            AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as ctx:
                run(
                    get_emergency_summary(
                        patient_id=uuid.uuid4(),
                        consent_token="tok",
                        provider=provider,
                        db=FakeDB(),
                    )
                )
        self.assertEqual(ctx.exception.status_code, 401)

    def test_valid_break_glass_capability_returns_only_granted_categories(self) -> None:
        provider = sample_provider_context()
        patient_id = uuid.uuid4()
        capability = _break_glass_capability(
            provider, str(patient_id), ["allergies", "vitals"]
        )
        db = FakeDB(
            structured={
                "patient_allergies": [
                    Allergy(
                        patient_id=patient_id,
                        allergen="Penicillin",
                        severity="Severe",
                        source="manual",
                    )
                ],
                "patient_vitals": [
                    Vitals(
                        patient_id=patient_id,
                        type="BP",
                        value="130/85",
                        unit="mmHg",
                        recorded_at=datetime.now(timezone.utc),
                        source="manual",
                    )
                ],
                # present in DB but NOT in the granted scope -- must not appear
                "patient_lab_results": [
                    LabResult(
                        patient_id=patient_id,
                        test_name="HbA1c",
                        value="7.2",
                        unit="%",
                        reference_range="4.0-5.6",
                        is_abnormal=True,
                        recorded_at=datetime.now(timezone.utc),
                        source="manual",
                    )
                ],
            }
        )

        with (
            patch(
                "app.api.v2.patient_routes.consent_engine.validate",
                AsyncMock(return_value=capability),
            ),
            patch(
                "app.api.v2.patient_routes.append_audit_log_or_503",
                AsyncMock(return_value=True),
            ) as mock_audit,
        ):
            result = run(
                get_emergency_summary(
                    patient_id=patient_id,
                    consent_token="tok",
                    provider=provider,
                    db=db,
                )
            )

        self.assertIn("allergies", result.categories)
        self.assertIn("vitals", result.categories)
        self.assertNotIn("lab_results", result.categories)
        self.assertTrue(mock_audit.await_count == 1)
        audit_kwargs = mock_audit.await_args.kwargs
        self.assertEqual(
            audit_kwargs["event_type"], "BREAK_GLASS_EMERGENCY_SUMMARY_ACCESSED"
        )
        # Response must never carry the bearer token.
        self.assertNotIn("consent_token", result.model_dump())
        self.assertNotIn("tok", str(result.model_dump()))


if __name__ == "__main__":
    unittest.main()
