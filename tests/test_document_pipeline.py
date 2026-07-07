"""Tests for the remote Document AI ingestion and confidence gate."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.ai.pipeline import process_medical_document_background
from app.api.v2.document_routes import DocumentUploadAcceptedResponse
from app.core.dependencies import get_provider_context
from app.main import app
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


def sample_provider_context() -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid.uuid4(),
            display_name="Dr. Document Test",
            medical_registration_number="MCI-DOC-1",
            specialty="Internal Medicine",
            contact_email="documents@example.com",
        ),
        hospital=HospitalContext(
            hospital_id=uuid.uuid4(),
            facility_code="DOC-HOSP",
            display_name="Document Test Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            department="Records",
            roles=["document_ingest"],
            is_primary=True,
            valid_from=None,
            valid_until=None,
        ),
    )


def extracted_doc(confidence: float) -> ExtractedMedicalDocument:
    return ExtractedMedicalDocument(
        patient_name="Jane Example",
        aadhaar_abha_id="1234-5678-9012",
        phone="9876543210",
        diagnoses=["asthma"],
        lab_results=["CBC normal"],
        prescriptions=["Salbutamol"],
        extraction_confidence=confidence,
        unknown_model_key="treat as vault",
    )


class FakeEncryptedField:
    def __init__(self, field_name: str, plaintext: str) -> None:
        self.field_name = field_name
        self.plaintext = plaintext

    def serialize(self) -> str:
        return f"encrypted:{self.field_name}:{self.plaintext}"


class FakeKMSProvider:
    async def generate_dek(self, patient_id: str, db) -> Mock:
        await db.commit()
        return Mock(dek_version=1)

    async def encrypt_field(self, patient_id: str, field_name: str, plaintext: str, db) -> FakeEncryptedField:
        return FakeEncryptedField(field_name, plaintext)


class FakeDBSession:
    def __init__(self) -> None:
        self.executions: list[tuple[object, dict]] = []
        self.added: list[object] = []
        self.refreshed: list[object] = []
        self.committed = False
        self.rolled_back = False

    def add(self, obj):
        self.added.append(obj)
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()

    async def execute(self, stmt, params=None):
        self.executions.append((stmt, params or {}))

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def refresh(self, obj):
        self.refreshed.append(obj)
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()


class TestDocumentPipeline(unittest.TestCase):
    def _temp_file(self) -> str:
        fd, path = tempfile.mkstemp()
        os.write(fd, b"fake medical document")
        os.close(fd)
        return path

    @patch("app.ai.pipeline.append_audit_log", new_callable=AsyncMock)
    @patch("app.ai.pipeline.split_pii_and_clinical_fields")
    @patch("app.ai.pipeline.get_medical_document_extractor")
    def test_high_confidence_auto_processes_and_persists_shards(
        self,
        mock_get_extractor,
        mock_split,
        mock_audit,
    ) -> None:
        path = self._temp_file()
        db = FakeDBSession()
        extractor = Mock()
        extractor.extract_data = AsyncMock(return_value=extracted_doc(0.96))
        mock_get_extractor.return_value = extractor
        mock_audit.return_value = True
        mock_split.return_value = (
            {
                "patient_name": "Jane Example",
                "aadhaar_abha_id": "1234-5678-9012",
                "phone": "9876543210",
            },
            {
                "diagnoses": ["asthma"],
                "lab_results": ["CBC normal"],
                "prescriptions": ["Salbutamol"],
            },
            {"unknown_model_key": "treat as vault"},
        )

        with (
            patch("app.ai.pipeline.get_encryption_provider", return_value=FakeKMSProvider()),
            patch("app.ai.pipeline.append_audit_log_or_503", new_callable=AsyncMock),
        ):
            run(process_medical_document_background(path, "provider-123", db))

        self.assertFalse(os.path.exists(path))
        extractor.extract_data.assert_awaited_once_with(path)
        split_payload = mock_split.call_args.args[0]
        self.assertNotIn("extraction_confidence", split_payload)
        self.assertEqual(len(db.executions), 2)
        self.assertTrue(db.committed)
        self.assertFalse(db.rolled_back)
        audit_events = [call.kwargs["event_type"] for call in mock_audit.await_args_list]
        self.assertEqual(audit_events, ["DOCUMENT_AUTO_PROCESS_STARTED", "DOCUMENT_AUTO_PROCESSED"])

    @patch("app.ai.pipeline.append_audit_log", new_callable=AsyncMock)
    @patch("app.ai.pipeline.get_medical_document_extractor")
    def test_medium_confidence_creates_pending_review_without_shard_writes(
        self,
        mock_get_extractor,
        mock_audit,
    ) -> None:
        path = self._temp_file()
        db = FakeDBSession()
        extractor = Mock()
        extractor.extract_data = AsyncMock(return_value=extracted_doc(0.90))
        mock_get_extractor.return_value = extractor
        mock_audit.return_value = True

        run(process_medical_document_background(path, "provider-123", db))

        self.assertFalse(os.path.exists(path))
        self.assertEqual(db.executions, [])
        self.assertEqual(len(db.added), 1)
        review = db.added[0]
        self.assertIsInstance(review, DocumentReviewQueue)
        self.assertEqual(review.provider_uid, "provider-123")
        self.assertEqual(review.status, "PENDING")
        self.assertEqual(review.confidence_score, 0.90)
        self.assertEqual(review.extracted_data["diagnoses"], ["asthma"])
        self.assertEqual(review.extracted_data["prescriptions"], ["Salbutamol"])
        self.assertNotIn("patient_name", review.extracted_data)
        self.assertNotIn("phone", review.extracted_data)
        self.assertNotIn("phone_number", review.extracted_data)
        self.assertNotIn("aadhaar_abha_id", review.extracted_data)
        self.assertNotIn("aadhaar_id", review.extracted_data)
        self.assertTrue(db.committed)
        self.assertEqual(mock_audit.await_args.kwargs["event_type"], "DOCUMENT_NEEDS_REVIEW")

    @patch("app.ai.pipeline.append_audit_log", new_callable=AsyncMock)
    @patch("app.ai.pipeline.get_medical_document_extractor")
    def test_low_confidence_rejects_without_db_writes(self, mock_get_extractor, mock_audit) -> None:
        path = self._temp_file()
        db = FakeDBSession()
        extractor = Mock()
        extractor.extract_data = AsyncMock(return_value=extracted_doc(0.72))
        mock_get_extractor.return_value = extractor
        mock_audit.return_value = True

        run(process_medical_document_background(path, "provider-123", db))

        self.assertFalse(os.path.exists(path))
        self.assertEqual(db.executions, [])
        self.assertFalse(db.committed)
        self.assertEqual(
            mock_audit.await_args.kwargs["event_type"],
            "DOCUMENT_REJECTED_LOW_CONFIDENCE",
        )

    @patch("app.ai.pipeline.append_audit_log", new_callable=AsyncMock)
    @patch("app.ai.pipeline.get_medical_document_extractor")
    def test_auto_process_audit_failure_aborts_before_db_write(
        self,
        mock_get_extractor,
        mock_audit,
    ) -> None:
        path = self._temp_file()
        db = FakeDBSession()
        extractor = Mock()
        extractor.extract_data = AsyncMock(return_value=extracted_doc(0.96))
        mock_get_extractor.return_value = extractor
        mock_audit.return_value = False

        with self.assertRaises(RuntimeError):
            run(process_medical_document_background(path, "provider-123", db))

        self.assertFalse(os.path.exists(path))
        self.assertEqual(db.executions, [])
        self.assertFalse(db.committed)

    @patch("app.ai.pipeline.get_medical_document_extractor")
    def test_pipeline_deletes_temp_file_even_when_extraction_fails(self, mock_get_extractor) -> None:
        path = self._temp_file()
        extractor = Mock()
        extractor.extract_data = AsyncMock(side_effect=RuntimeError("remote unavailable"))
        mock_get_extractor.return_value = extractor

        with self.assertRaises(RuntimeError):
            run(process_medical_document_background(path, "provider-123", FakeDBSession()))

        self.assertFalse(os.path.exists(path))


class TestDocumentUploadRoute(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = sample_provider_context()

        async def override_provider() -> ProviderContext:
            return self.provider

        app.dependency_overrides[get_provider_context] = override_provider
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(get_provider_context, None)

    @patch("app.api.v2.document_routes.run_medical_document_pipeline_job", new_callable=AsyncMock)
    @patch("app.api.v2.document_routes.append_audit_log_or_503", new_callable=AsyncMock)
    def test_upload_returns_202_and_schedules_background_job(self, mock_audit, mock_job) -> None:
        staged_paths: list[str] = []

        async def job_side_effect(file_path: str, _provider_uid: str) -> None:
            staged_paths.append(file_path)
            self.assertTrue(os.path.exists(file_path))
            os.remove(file_path)

        mock_job.side_effect = job_side_effect

        response = self.client.post(
            "/api/v2/documents/upload",
            files={"file": ("report.pdf", b"medical document bytes", "application/pdf")},
        )

        self.assertEqual(response.status_code, 202, response.text)
        payload = DocumentUploadAcceptedResponse.model_validate_json(response.text)
        self.assertEqual(payload.status, "accepted")
        self.assertIsInstance(payload.queued_at, datetime)
        self.assertEqual(len(staged_paths), 1)
        self.assertFalse(os.path.exists(staged_paths[0]))

        mock_job.assert_awaited_once()
        job_args = mock_job.await_args.args
        self.assertEqual(job_args[1], self.provider.actor_uid)

        mock_audit.assert_awaited_once()
        audit_kwargs = mock_audit.await_args.kwargs
        self.assertEqual(audit_kwargs["event_type"], "DOCUMENT_UPLOAD_RECEIVED")
        self.assertEqual(audit_kwargs["actor_uid"], self.provider.actor_uid)
        self.assertEqual(audit_kwargs["target_id"], str(payload.job_id))
        self.assertEqual(audit_kwargs["metadata"]["hospital_id"], str(self.provider.hospital.hospital_id))
        self.assertNotIn("medical document bytes", str(audit_kwargs))
        self.assertNotIn("report.pdf", str(audit_kwargs))

    @patch("app.api.v2.document_routes.os.remove")
    @patch("app.api.v2.document_routes.append_audit_log_or_503", new_callable=AsyncMock)
    def test_upload_audit_failure_removes_temp_file(self, mock_audit, mock_remove) -> None:
        mock_audit.side_effect = HTTPException(status_code=503, detail="audit down")
        mock_remove.return_value = None

        response = self.client.post(
            "/api/v2/documents/upload",
            files={"file": ("report.pdf", b"medical document bytes", "application/pdf")},
        )

        self.assertEqual(response.status_code, 503)
        mock_remove.assert_called_once()


if __name__ == "__main__":
    unittest.main()
