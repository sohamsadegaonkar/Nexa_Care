"""Tests for NFC card registry metadata and fail-closed resolution."""

from __future__ import annotations

import asyncio
import unittest
from uuid import UUID, uuid4
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.models.base import Base
from app.models.nfc_card_registry import NFCCardRegistry, NFCCardStatus
from app.services.card_resolution_service import (
    CardResolutionService,
    CardStatusUpdateResult,
)


def run(coro):
    return asyncio.run(coro)


class FakeScalarResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class FakeSession:
    def __init__(self, row=None, execute_error=None, commit_error=None):
        self.row = row
        self.execute_error = execute_error
        self.commit_error = commit_error
        self.committed = False
        self.rolled_back = False
        self.executed_statements = []

    async def execute(self, stmt):
        self.executed_statements.append(stmt)
        if self.execute_error is not None:
            raise self.execute_error
        return FakeScalarResult(self.row)

    async def commit(self):
        if self.commit_error is not None:
            raise self.commit_error
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


def make_card(status=NFCCardStatus.ACTIVE.value, patient_id=None):
    return NFCCardRegistry(
        card_uid="CARD-001",
        patient_id=patient_id or uuid4(),
        status=status,
        issued_by=uuid4(),
    )


class TestNFCCardRegistryModel(unittest.TestCase):
    def test_model_registered_on_base_metadata(self) -> None:
        table_names = {table.name for table in Base.metadata.sorted_tables}
        self.assertIn("nfc_card_registry", table_names)

    def test_required_columns_and_no_patient_pii_columns(self) -> None:
        columns = set(NFCCardRegistry.__table__.columns.keys())
        self.assertIn("card_uid", columns)
        self.assertIn("patient_id", columns)
        self.assertIn("status", columns)
        self.assertIn("issued_at", columns)
        self.assertIn("issued_by", columns)

        forbidden_pii_columns = {
            "name",
            "patient_name",
            "dob",
            "date_of_birth",
            "phone",
            "diagnosis",
            "diagnoses",
        }
        self.assertTrue(columns.isdisjoint(forbidden_pii_columns))

    def test_card_uid_unique_and_indexed(self) -> None:
        table = NFCCardRegistry.__table__
        constraint_names = {constraint.name for constraint in table.constraints if constraint.name}
        index_names = {index.name for index in table.indexes}

        self.assertIn("uq_nfc_card_registry_card_uid", constraint_names)
        self.assertIn("ix_nfc_card_registry_card_uid", index_names)
        self.assertIn("ix_nfc_card_registry_patient_id", index_names)

    def test_status_states_are_supported(self) -> None:
        self.assertEqual(NFCCardStatus.ACTIVE.value, "active")
        self.assertEqual(NFCCardStatus.REPORTED_LOST.value, "reported_lost")
        self.assertEqual(NFCCardStatus.REVOKED.value, "revoked")
        self.assertEqual(NFCCardStatus.REPLACED.value, "replaced")


class TestCardResolutionService(unittest.TestCase):
    def test_resolve_active_card_returns_patient_id(self) -> None:
        patient_id = uuid4()
        service = CardResolutionService(FakeSession(row=make_card(patient_id=patient_id)))

        resolved = run(service.resolve_card("CARD-001"))

        self.assertEqual(resolved, patient_id)
        self.assertIsInstance(resolved, UUID)

    def test_resolve_reported_lost_card_raises_403_without_patient_id(self) -> None:
        patient_id = uuid4()
        service = CardResolutionService(FakeSession(
            row=make_card(status=NFCCardStatus.REPORTED_LOST.value, patient_id=patient_id),
        ))

        with self.assertRaises(HTTPException) as cm:
            run(service.resolve_card("CARD-001"))

        self.assertEqual(cm.exception.status_code, 403)
        self.assertNotEqual(cm.exception.detail, str(patient_id))

    def test_resolve_revoked_card_raises_403(self) -> None:
        service = CardResolutionService(FakeSession(
            row=make_card(status=NFCCardStatus.REVOKED.value),
        ))

        with self.assertRaises(HTTPException) as cm:
            run(service.resolve_card("CARD-001"))

        self.assertEqual(cm.exception.status_code, 403)

    def test_resolve_unknown_card_raises_403(self) -> None:
        service = CardResolutionService(FakeSession(row=None))

        with self.assertRaises(HTTPException) as cm:
            run(service.resolve_card("UNKNOWN-CARD"))

        self.assertEqual(cm.exception.status_code, 403)

    def test_resolve_db_error_raises_503(self) -> None:
        service = CardResolutionService(FakeSession(execute_error=SQLAlchemyError("db down")))

        with self.assertRaises(HTTPException) as cm:
            run(service.resolve_card("CARD-001"))

        self.assertEqual(cm.exception.status_code, 503)

    def test_resolve_empty_card_uid_fails_pydantic_validation(self) -> None:
        service = CardResolutionService(FakeSession(row=None))

        with self.assertRaises(ValidationError):
            run(service.resolve_card(""))

    @patch("app.services.card_resolution_service.append_audit_log_or_503", new_callable=AsyncMock)
    def test_report_lost_card_updates_status_and_audits(self, mock_audit) -> None:
        row = make_card(status=NFCCardStatus.ACTIVE.value)
        actor_id = uuid4()
        session = FakeSession(row=row)
        service = CardResolutionService(session)

        result = run(service.report_lost_card("CARD-001", actor_id))

        self.assertTrue(session.committed)
        self.assertEqual(row.status, NFCCardStatus.REPORTED_LOST.value)
        self.assertEqual(result, CardStatusUpdateResult(
            patient_id=row.patient_id,
            status=NFCCardStatus.REPORTED_LOST,
        ))
        self.assertEqual(mock_audit.await_count, 2)
        first_call, second_call = mock_audit.await_args_list
        self.assertEqual(first_call.kwargs["status"], "STARTED")
        self.assertEqual(second_call.kwargs["status"], "SUCCESS")
        self.assertEqual(second_call.kwargs["target_id"], str(row.patient_id))
        self.assertEqual(second_call.kwargs["actor_uid"], str(actor_id))

    @patch("app.services.card_resolution_service.append_audit_log_or_503", new_callable=AsyncMock)
    def test_report_lost_audit_failure_aborts_before_commit(self, mock_audit) -> None:
        row = make_card(status=NFCCardStatus.ACTIVE.value)
        session = FakeSession(row=row)
        service = CardResolutionService(session)
        mock_audit.side_effect = HTTPException(status_code=503, detail="audit down")

        with self.assertRaises(HTTPException) as cm:
            run(service.report_lost_card("CARD-001", uuid4()))

        self.assertEqual(cm.exception.status_code, 503)
        self.assertFalse(session.committed)
        self.assertEqual(row.status, NFCCardStatus.ACTIVE.value)

    @patch("app.services.card_resolution_service.append_audit_log_or_503", new_callable=AsyncMock)
    def test_report_lost_commit_failure_rolls_back(self, mock_audit) -> None:
        session = FakeSession(row=make_card(), commit_error=SQLAlchemyError("commit failed"))
        service = CardResolutionService(session)

        with self.assertRaises(HTTPException) as cm:
            run(service.report_lost_card("CARD-001", uuid4()))

        self.assertEqual(cm.exception.status_code, 503)
        self.assertTrue(session.rolled_back)
        self.assertEqual(mock_audit.await_count, 1)


if __name__ == "__main__":
    unittest.main()
