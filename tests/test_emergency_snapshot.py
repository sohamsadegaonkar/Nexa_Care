"""Tests for the Phase C emergency snapshot retrieval path."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import datetime, timezone
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.api.v2.emergency_routes import read_emergency_card
from app.main import app
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


class TestEmergencyRouteIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_post_read_card_is_retired_without_request_parsing(self) -> None:
        response = self.client.post(
            "/api/v2/emergency/read-card",
            json={"card_uid": "opaque-card-value", "patient_id": str(uuid.uuid4())},
        )
        self.assertEqual(response.status_code, 410)
        self.assertEqual(
            response.json(), {"error_code": "EMERGENCY_DIRECT_CARD_READ_RETIRED"}
        )

    def test_retired_handler_has_no_patient_or_storage_dependencies(self) -> None:
        response = run(read_emergency_card())
        self.assertEqual(response.status_code, 410)
        self.assertEqual(
            response.body,
            b'{"error_code":"EMERGENCY_DIRECT_CARD_READ_RETIRED"}',
        )


if __name__ == "__main__":
    unittest.main()
