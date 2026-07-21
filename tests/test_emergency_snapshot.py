"""Tests for the Phase C emergency snapshot retrieval path."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.api.v2.emergency_routes import NFCReadRequest, read_emergency_card
from app.core.database import get_db_session
from app.core.dependencies import get_provider_context
from app.main import app
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.models.patient_records import Allergy, LabResult, Medication, Vitals
from app.services.emergency_snapshot_service import get_emergency_snapshot


def run(coro):
    return asyncio.run(coro)


class FakeMappings:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class FakeSnapshotResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return FakeMappings(self._row)


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


class FakeSnapshotSession:
    def __init__(self, row=None, execute_error=None, structured=None):
        self.row = row
        self.execute_error = execute_error
        self.structured = structured or {}
        self.executed = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, stmt, params=None):
        self.executed.append((stmt, params))
        if self.execute_error is not None:
            raise self.execute_error
        sql = str(stmt).lower()
        if "nexa_emergency_snapshot" in sql:
            return FakeSnapshotResult(self.row)
        for table_name, rows in self.structured.items():
            if table_name in sql:
                return FakeRowsResult(rows)
        return FakeRowsResult([])

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


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
    )


class TestEmergencySnapshotService(unittest.TestCase):
    def test_existing_snapshot_is_returned_without_writes(self) -> None:
        patient_id = uuid.uuid4()
        session = FakeSnapshotSession(
            row={
                "patient_id": patient_id,
                "allergies": ["penicillin"],
                "blood_group": "O+",
                "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            }
        )

        result = run(get_emergency_snapshot(patient_id, session))

        self.assertEqual(result["patient_id"], patient_id)
        self.assertEqual(result["snapshot_status"], "available")
        self.assertEqual(result["snapshot"]["allergies"], ["penicillin"])
        self.assertEqual(result["snapshot"]["patient_id"], str(patient_id))
        self.assertFalse(session.committed)
        self.assertFalse(session.rolled_back)
        self.assertEqual(len(session.executed), 6)

    def test_structured_records_are_primary_snapshot_source(self) -> None:
        patient_id = uuid.uuid4()
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        session = FakeSnapshotSession(
            row={"allergies": ["legacy-only"]},
            structured={
                "patient_allergies": [
                    Allergy(
                        patient_id=patient_id,
                        allergen="Penicillin",
                        severity="Severe",
                        source="manual",
                        risk_level="HIGH_RISK",
                    )
                ],
                "patient_medications": [
                    Medication(
                        patient_id=patient_id,
                        name="Metformin",
                        strength="500mg",
                        frequency="Twice daily",
                        prescribed_at=now,
                        source="manual",
                        risk_level="MEDIUM_RISK",
                    )
                ],
                "patient_vitals": [
                    Vitals(
                        patient_id=patient_id,
                        type="BP",
                        value="130/85",
                        unit="mmHg",
                        recorded_at=now,
                        source="manual",
                        risk_level="LOW_RISK",
                    )
                ],
                "patient_lab_results": [
                    LabResult(
                        patient_id=patient_id,
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
            },
        )

        result = run(get_emergency_snapshot(patient_id, session))

        self.assertEqual(result["snapshot_status"], "available")
        snapshot = result["snapshot"]
        self.assertEqual(snapshot["source"], "structured_patient_records")
        self.assertEqual(snapshot["allergies"][0]["allergen"], "Penicillin")
        self.assertEqual(snapshot["high_risk_allergies"][0]["allergen"], "Penicillin")
        self.assertEqual(snapshot["active_medications"][0]["name"], "Metformin")
        self.assertEqual(snapshot["latest_vitals"][0]["value"], "130/85")
        self.assertEqual(snapshot["abnormal_labs"][0]["test_name"], "HbA1c")
        self.assertEqual(len(session.executed), 5)

    def test_missing_snapshot_returns_no_known_medical_data(self) -> None:
        patient_id = uuid.uuid4()
        result = run(get_emergency_snapshot(patient_id, FakeSnapshotSession(row=None)))

        self.assertEqual(result["patient_id"], patient_id)
        self.assertEqual(result["snapshot_status"], "no_known_medical_data")
        self.assertEqual(result["message"], "No Known Medical Data")
        self.assertEqual(result["snapshot"], {})

    def test_db_error_raises_503(self) -> None:
        session = FakeSnapshotSession(execute_error=SQLAlchemyError("db unavailable"))

        with self.assertRaises(HTTPException) as cm:
            run(get_emergency_snapshot(uuid.uuid4(), session))

        self.assertEqual(cm.exception.status_code, 503)
        self.assertFalse(session.committed)
        self.assertFalse(session.rolled_back)


class TestEmergencyRoute(unittest.TestCase):
    def test_nfc_request_rejects_extra_fields(self) -> None:
        with self.assertRaises(ValidationError):
            NFCReadRequest.model_validate(
                {"card_uid": "CARD-1", "patient_id": str(uuid.uuid4())}
            )

    @patch("app.api.v2.emergency_routes.get_emergency_snapshot", new_callable=AsyncMock)
    @patch("app.api.v2.emergency_routes.append_audit_log", new_callable=AsyncMock)
    @patch(
        "app.api.v2.emergency_routes.CardResolutionService.resolve_card",
        new_callable=AsyncMock,
    )
    def test_read_card_resolves_audits_then_retrieves(
        self,
        mock_resolve,
        mock_audit,
        mock_snapshot,
    ) -> None:
        patient_id = uuid.uuid4()
        provider = sample_provider_context()
        db_session = FakeSnapshotSession()
        calls: list[str] = []

        async def resolve_side_effect(_card_uid):
            calls.append("resolve")
            return patient_id

        async def audit_side_effect(**_kwargs):
            calls.append("audit")
            return True

        async def snapshot_side_effect(_patient_id, _db_session):
            calls.append("snapshot")
            return {
                "patient_id": patient_id,
                "snapshot_status": "no_known_medical_data",
                "message": "No Known Medical Data",
                "snapshot": {},
                "retrieved_at": datetime.now(timezone.utc),
            }

        mock_resolve.side_effect = resolve_side_effect
        mock_audit.side_effect = audit_side_effect
        mock_snapshot.side_effect = snapshot_side_effect

        response = run(
            read_emergency_card(
                payload=NFCReadRequest(card_uid="CARD-ER-1"),
                provider=provider,
                db_session=db_session,
            )
        )

        self.assertEqual(calls, ["resolve", "audit", "snapshot"])
        self.assertEqual(response.patient_id, patient_id)
        self.assertEqual(response.snapshot_status, "no_known_medical_data")

        audit_kwargs = mock_audit.await_args.kwargs
        self.assertEqual(audit_kwargs["actor_uid"], provider.actor_uid)
        self.assertEqual(audit_kwargs["event_type"], "SNAPSHOT_ACCESSED")
        self.assertEqual(audit_kwargs["target_id"], str(patient_id))
        self.assertEqual(
            audit_kwargs["metadata"]["hospital_id"], str(provider.hospital.hospital_id)
        )
        self.assertEqual(audit_kwargs["metadata"]["patient_id"], str(patient_id))
        self.assertEqual(
            audit_kwargs["metadata"]["access_timestamp"],
            audit_kwargs["event_timestamp"],
        )

    @patch("app.api.v2.emergency_routes.get_emergency_snapshot", new_callable=AsyncMock)
    @patch("app.api.v2.emergency_routes.append_audit_log", new_callable=AsyncMock)
    @patch(
        "app.api.v2.emergency_routes.CardResolutionService.resolve_card",
        new_callable=AsyncMock,
    )
    def test_audit_failure_aborts_before_snapshot_retrieval(
        self,
        mock_resolve,
        mock_audit,
        mock_snapshot,
    ) -> None:
        mock_resolve.return_value = uuid.uuid4()
        mock_audit.return_value = False

        with self.assertRaises(HTTPException) as cm:
            run(
                read_emergency_card(
                    payload=NFCReadRequest(card_uid="CARD-ER-1"),
                    provider=sample_provider_context(),
                    db_session=FakeSnapshotSession(),
                )
            )

        self.assertEqual(cm.exception.status_code, 503)
        mock_snapshot.assert_not_awaited()

    @patch("app.api.v2.emergency_routes.append_audit_log", new_callable=AsyncMock)
    @patch(
        "app.api.v2.emergency_routes.CardResolutionService.resolve_card",
        new_callable=AsyncMock,
    )
    def test_resolution_403_bubbles_without_audit(
        self, mock_resolve, mock_audit
    ) -> None:
        mock_resolve.side_effect = HTTPException(status_code=403, detail="forbidden")

        with self.assertRaises(HTTPException) as cm:
            run(
                read_emergency_card(
                    payload=NFCReadRequest(card_uid="LOST-CARD"),
                    provider=sample_provider_context(),
                    db_session=FakeSnapshotSession(),
                )
            )

        self.assertEqual(cm.exception.status_code, 403)
        mock_audit.assert_not_awaited()


class TestEmergencyRouteIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = sample_provider_context()
        self.patient_id = uuid.uuid4()
        self.db_session = FakeSnapshotSession()

        async def override_provider() -> ProviderContext:
            return self.provider

        async def override_db_session():
            yield self.db_session

        app.dependency_overrides[get_provider_context] = override_provider
        app.dependency_overrides[get_db_session] = override_db_session
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(get_provider_context, None)
        app.dependency_overrides.pop(get_db_session, None)

    @patch("app.api.v2.emergency_routes.get_emergency_snapshot", new_callable=AsyncMock)
    @patch("app.api.v2.emergency_routes.append_audit_log", new_callable=AsyncMock)
    @patch(
        "app.api.v2.emergency_routes.CardResolutionService.resolve_card",
        new_callable=AsyncMock,
    )
    def test_post_read_card_route_returns_snapshot_response(
        self,
        mock_resolve,
        mock_audit,
        mock_snapshot,
    ) -> None:
        mock_resolve.return_value = self.patient_id
        mock_audit.return_value = True
        mock_snapshot.return_value = {
            "patient_id": self.patient_id,
            "snapshot_status": "available",
            "message": None,
            "snapshot": {"allergies": ["penicillin"], "blood_group": "O+"},
            "retrieved_at": datetime.now(timezone.utc),
        }

        response = self.client.post(
            "/api/v2/emergency/read-card",
            json={"card_uid": "CARD-ER-ROUTE"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["patient_id"], str(self.patient_id))
        self.assertEqual(body["snapshot_status"], "available")
        self.assertEqual(body["snapshot"]["blood_group"], "O+")
        self.assertEqual(body["snapshot"]["allergies"], ["penicillin"])
        self.assertNotIn("metadata", body)
        mock_resolve.assert_awaited_once_with("CARD-ER-ROUTE")
        mock_snapshot.assert_awaited_once_with(self.patient_id, self.db_session)

    def test_post_read_card_rejects_extra_body_fields(self) -> None:
        response = self.client.post(
            "/api/v2/emergency/read-card",
            json={"card_uid": "CARD-ER-ROUTE", "patient_id": str(self.patient_id)},
        )

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
