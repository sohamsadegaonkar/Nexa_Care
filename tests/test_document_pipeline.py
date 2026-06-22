"""Tests for the Path 2 document ingestion and AI pipeline scaffold."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.ai.pipeline import process_medical_document_background
from app.api.v2.document_routes import DocumentUploadAcceptedResponse
from app.core.dependencies import get_provider_context
from app.main import app
from app.models.ai_models import ExtractedMedicalDocument
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


class FakeDBSession:
    def __init__(self) -> None:
        self.executed = False
        self.committed = False
        self.rolled_back = False

    async def execute(self, *_args, **_kwargs):
        self.executed = True

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


class TestDocumentPipeline(unittest.TestCase):
    @patch("app.ai.pipeline.split_pii_and_clinical_fields")
    @patch("app.ai.pipeline.asyncio.to_thread", new_callable=AsyncMock)
    def test_pipeline_runs_extraction_in_thread_splits_and_deletes_temp_file(
        self,
        mock_to_thread,
        mock_split,
    ) -> None:
        fd, path = tempfile.mkstemp()
        os.write(fd, b"fake medical document")
        os.close(fd)
        db = FakeDBSession()
        mock_to_thread.return_value = ExtractedMedicalDocument(
            patient_name="Jane Example",
            aadhaar_abha_id="1234-5678-9012",
            phone="9876543210",
            diagnoses=["asthma"],
            lab_results=[],
            prescriptions=[],
            unknown_model_key="treat as vault",
        )
        mock_split.return_value = (
            {"patient_name": "Jane Example"},
            {"diagnoses": ["asthma"]},
            {"unknown_model_key": "treat as vault"},
        )

        run(process_medical_document_background(path, "provider-123", db))

        self.assertFalse(os.path.exists(path))
        mock_to_thread.assert_awaited_once()
        mock_split.assert_called_once_with(mock_to_thread.return_value.model_dump())
        self.assertFalse(db.executed)
        self.assertFalse(db.committed)
        self.assertFalse(db.rolled_back)

    @patch("app.ai.pipeline.asyncio.to_thread", new_callable=AsyncMock)
    def test_pipeline_deletes_temp_file_even_when_extraction_fails(self, mock_to_thread) -> None:
        fd, path = tempfile.mkstemp()
        os.write(fd, b"fake medical document")
        os.close(fd)
        mock_to_thread.side_effect = RuntimeError("model unavailable")

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
