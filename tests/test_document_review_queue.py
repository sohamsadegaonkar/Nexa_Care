"""Tests for AI document review queue and HITL approval routes."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.ai.pipeline import process_medical_document_background
from app.api.v2.review_routes import approve_review, list_pending_reviews, reject_review
from app.models.ai_models import ExtractedMedicalDocument
from app.models.document_review import DocumentReviewQueue
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)


def run(coro):
    return asyncio.run(coro)


def sample_provider_context(provider_id: uuid.UUID | None = None) -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=provider_id or uuid.uuid4(),
            display_name="Dr. Review Test",
            medical_registration_number="MCI-REV-1",
            specialty="General Medicine",
            contact_email="review@example.com",
        ),
        hospital=HospitalContext(
            hospital_id=uuid.uuid4(),
            facility_code="REV-HOSP",
            display_name="Review Test Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            department="Records",
            roles=["reviewer"],
            is_primary=True,
            valid_from=None,
            valid_until=None,
        ),
    )


def extracted_doc(confidence: float = 0.88) -> ExtractedMedicalDocument:
    return ExtractedMedicalDocument(
        patient_name="Jane Example",
        aadhaar_abha_id="1234-5678-9012",
        phone="9876543210",
        diagnoses=["asthma"],
        lab_results=["CBC normal"],
        prescriptions=["Salbutamol"],
        extraction_confidence=confidence,
    )


class FakeScalarResult:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows if rows is not None else ([] if row is None else [row])

    def scalar_one_or_none(self):
        return self.row

    def scalars(self):
        return self

    def all(self):
        return self.rows


class FakeReviewDB:
    def __init__(self, row=None, rows=None, execute_error=None, execute_error_after_count=None, events=None):
        self.row = row
        self.rows = rows
        self.execute_error = execute_error
        self.execute_error_after_count = execute_error_after_count
        self.added = []
        self.executions = []
        self.committed = False
        self.commit_count = 0
        self.rolled_back = False
        self.refreshed = []
        # Shared list also appended to by the audit mocks below, so tests
        # can assert real ordering (audit-before-write), not just that
        # both eventually happened.
        self.events = events if events is not None else []

    def add(self, obj):
        self.added.append(obj)
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()

    async def execute(self, stmt, params=None):
        self.executions.append((stmt, params or {}))
        self.events.append("DB_EXECUTE")
        if self.execute_error is not None:
            if self.execute_error_after_count is None or len(self.executions) > self.execute_error_after_count:
                raise self.execute_error
        return FakeScalarResult(self.row, self.rows)

    async def commit(self):
        self.committed = True
        self.commit_count += 1
        self.events.append("DB_COMMIT")

    async def rollback(self):
        self.rolled_back = True
        self.events.append("DB_ROLLBACK")

    async def refresh(self, obj):
        self.refreshed.append(obj)
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()


def make_audit_or_503_mock(events):
    """Mock for append_audit_log_or_503 that records which event_type was
    audited, in order, into the shared `events` list -- used to prove
    audit calls happen before/after DB writes, not just that they happen.
    """
    async def _mock(*, actor_uid, event_type, target_id, status, metadata=None, event_timestamp=None):
        events.append(f"AUDIT_OR_503:{event_type}")
    return AsyncMock(side_effect=_mock)


def make_audit_log_mock(events, success=True):
    """Mock for append_audit_log (used by _audit_best_effort on the
    failure path) -- same event recording, returns a bool like the real
    function instead of raising.
    """
    async def _mock(*, actor_uid, event_type, target_id, status, metadata=None, event_timestamp=None):
        events.append(f"AUDIT_LOG:{event_type}")
        return success
    return AsyncMock(side_effect=_mock)


def make_review(provider_uid: str, status: str = "PENDING") -> DocumentReviewQueue:
    review = DocumentReviewQueue(
        provider_uid=provider_uid,
        status=status,
        confidence_score=0.88,
        extracted_data=extracted_doc(0.88).model_dump(),
    )
    review.id = uuid.uuid4()
    review.created_at = datetime.now(timezone.utc)
    review.updated_at = datetime.now(timezone.utc)
    return review


class TestPipelineReviewQueue(unittest.TestCase):
    def _temp_file(self) -> str:
        fd, path = tempfile.mkstemp()
        os.write(fd, b"fake medical document")
        os.close(fd)
        return path

    @patch("app.ai.pipeline.append_audit_log", new_callable=AsyncMock)
    @patch("app.ai.pipeline.get_medical_document_extractor")
    def test_medium_confidence_creates_pending_review_record(self, mock_get_extractor, mock_audit):
        path = self._temp_file()
        db = FakeReviewDB()
        extractor = Mock()
        extractor.extract_data = AsyncMock(return_value=extracted_doc(0.88))
        mock_get_extractor.return_value = extractor
        mock_audit.return_value = True

        run(process_medical_document_background(path, "provider-123", db))

        self.assertFalse(os.path.exists(path))
        self.assertEqual(len(db.added), 1)
        review = db.added[0]
        self.assertIsInstance(review, DocumentReviewQueue)
        self.assertEqual(review.provider_uid, "provider-123")
        self.assertEqual(review.status, "PENDING")
        self.assertEqual(review.confidence_score, 0.88)
        self.assertEqual(review.extracted_data["diagnoses"], ["asthma"])
        self.assertEqual(review.extracted_data["prescriptions"], ["Salbutamol"])
        self.assertNotIn("patient_name", review.extracted_data)
        self.assertNotIn("phone", review.extracted_data)
        self.assertNotIn("phone_number", review.extracted_data)
        self.assertNotIn("aadhaar_abha_id", review.extracted_data)
        self.assertNotIn("aadhaar_id", review.extracted_data)
        self.assertTrue(db.committed)
        self.assertEqual(mock_audit.await_args.kwargs["event_type"], "DOCUMENT_NEEDS_REVIEW")


class TestReviewRoutes(unittest.TestCase):
    def test_list_pending_reviews_filters_to_provider(self):
        provider = sample_provider_context()
        owned = make_review(provider.actor_uid)
        db = FakeReviewDB(rows=[owned])

        result = run(list_pending_reviews(provider=provider, db=db))

        self.assertEqual(len(result.reviews), 1)
        self.assertEqual(result.reviews[0].id, owned.id)

    @patch("app.api.v2.review_routes.append_audit_log_or_503", new_callable=AsyncMock)
    @patch("app.api.v2.review_routes.split_pii_and_clinical_fields")
    def test_approve_owned_pending_review_writes_shards_and_marks_approved(self, mock_split, mock_audit):
        provider = sample_provider_context()
        review = make_review(provider.actor_uid)
        db = FakeReviewDB(row=review)
        mock_split.return_value = (
            {"patient_name": "Jane Example", "phone": "9876543210", "aadhaar_abha_id": "1234"},
            {"diagnoses": ["asthma"], "lab_results": [], "prescriptions": []},
            {},
        )

        result = run(approve_review(review.id, extracted_doc(0.91), provider, db))

        self.assertEqual(result.status, "APPROVED")
        self.assertEqual(review.status, "APPROVED")
        self.assertEqual(len(db.executions), 3)  # select + two shard inserts
        self.assertEqual(db.commit_count, 1)  # shard inserts + status update commit atomically
        self.assertEqual(mock_audit.await_args.kwargs["event_type"], "DOCUMENT_REVIEW_APPROVED")

    @patch("app.api.v2.review_routes.append_audit_log_or_503", new_callable=AsyncMock)
    def test_reject_owned_pending_review_marks_rejected(self, mock_audit):
        provider = sample_provider_context()
        review = make_review(provider.actor_uid)
        db = FakeReviewDB(row=review)

        result = run(reject_review(review.id, provider, db))

        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(review.status, "REJECTED")
        self.assertTrue(db.committed)
        self.assertEqual(mock_audit.await_args.kwargs["event_type"], "DOCUMENT_REVIEW_REJECTED")

    def test_another_provider_cannot_approve_pending_review(self):
        provider = sample_provider_context()
        db = FakeReviewDB(row=None)

        with self.assertRaises(HTTPException) as cm:
            run(approve_review(uuid.uuid4(), extracted_doc(0.91), provider, db))

        self.assertEqual(cm.exception.status_code, 404)
        self.assertFalse(db.committed)

    def test_non_pending_review_cannot_be_rejected(self):
        provider = sample_provider_context()
        db = FakeReviewDB(row=None)

        with self.assertRaises(HTTPException) as cm:
            run(reject_review(uuid.uuid4(), provider, db))

        self.assertEqual(cm.exception.status_code, 404)

    @patch("app.api.v2.review_routes.split_pii_and_clinical_fields")
    def test_approve_rolls_back_on_db_error(self, mock_split):
        provider = sample_provider_context()
        review = make_review(provider.actor_uid)
        db = FakeReviewDB(row=review, execute_error=SQLAlchemyError("db down"), execute_error_after_count=1)
        mock_split.return_value = ({}, {}, {})

        with self.assertRaises(SQLAlchemyError):
            run(approve_review(review.id, extracted_doc(0.91), provider, db))

        self.assertTrue(db.rolled_back)


if __name__ == "__main__":
    unittest.main()