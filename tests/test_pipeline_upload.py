"""Test suite for Workstream 4 Document Upload & Background Extraction Orchestrator.

Verifies:
1. Upload creates staged storage and ExtractionJob with status=queued.
2. Job status endpoint returns counts and extracted fields.
3. Extraction routes safe LOW_RISK fields to auto_approved.
4. Extraction routes risky/uncertain fields to review queue.
5. CRITICAL_RISK fields always route to review (Invariant 4).
6. Hard audit events emitted at every stage of extraction.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.consent_gate import ConsentCapability
from app.main import app
from app.models.ai_models import ExtractedMedicalDocument
from app.models.pipeline import DocumentStorage, ExtractedFieldRecord, ExtractionJob, ReviewQueueItem
from app.services.pipeline_orchestrator import process_extraction_job

client = TestClient(app)


@pytest.fixture(autouse=True)
def auth_override(admin_context):
    from app.core.dependencies import get_current_provider
    app.dependency_overrides[get_current_provider] = lambda: admin_context
    yield
    app.dependency_overrides.pop(get_current_provider, None)


class FakeScalarResult:
    def __init__(self, row):
        self._row = row
    def scalar_one_or_none(self):
        return self._row


def test_upload_creates_job(admin_headers):
    """Test 1: Upload endpoint creates DocumentStorage and ExtractionJob (queued)."""
    mock_cap = ConsentCapability(
        patient_id="pat-101",
        clinician_id="doc-202",
        purpose="ai_document_ingestion",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    mock_db = MagicMock()
    added = []
    mock_db.add = lambda m: added.append(m)
    mock_db.commit = AsyncMock()

    from app.core.database import get_db_session
    app.dependency_overrides[get_db_session] = lambda: mock_db
    try:
        with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap):
            res = client.post(
                "/api/v2/pipeline/documents/upload?patient_id=pat-101&filename=report.pdf",
                headers={**admin_headers, "X-Consent-Token": "valid-tok"},
            )
            assert res.status_code == 202
            data = res.json()
            assert data["status"] == "queued"
            assert "job_id" in data
            jobs = [m for m in added if isinstance(m, ExtractionJob)]
            assert len(jobs) == 1
            assert jobs[0].status == "queued"
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_get_job_status(admin_headers):
    """Test 2: Job status endpoint returns extracted fields and adjudication counts."""
    mock_cap = ConsentCapability(
        patient_id="pat-101",
        clinician_id="doc-202",
        purpose="pipeline_status",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap):
        res = client.get(
            "/api/v2/pipeline/jobs/job-123?patient_id=pat-101",
            headers={**admin_headers, "X-Consent-Token": "valid-tok"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["job_id"] == "job-123"
        assert "auto_approved_count" in data
        assert "needs_review_count" in data
        assert "extracted_fields" in data


@pytest.mark.asyncio
async def test_extraction_routes_auto_approve():
    """Test 3: Extraction orchestrator routes safe LOW_RISK fields to auto_approved."""
    job_uuid = uuid.uuid4()
    doc_uuid = uuid.uuid4()
    now = datetime.now(timezone.utc)
    job = ExtractionJob(id=job_uuid, patient_id=uuid.uuid4(), document_id=doc_uuid, document_type="LAB_REPORT", status="queued", created_at=now)
    doc_storage = DocumentStorage(id=doc_uuid, patient_id=job.patient_id, storage_ref="s3://doc.pdf", content_type="application/pdf", size=1024, uploaded_at=now)

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(side_effect=[FakeScalarResult(job), FakeScalarResult(doc_storage)])
    added = []
    mock_db.add = lambda m: added.append(m)
    mock_db.commit = AsyncMock()

    mock_vlm = ExtractedMedicalDocument(
        patient_name="Aarav Sharma",
        aadhaar_abha_id="1234",
        phone="9876543210",
        diagnoses=[],
        lab_results=["Sodium 4.2 mmol/L"],
        prescriptions=[],
        extraction_confidence=0.98,
    )
    with patch("app.services.pipeline_orchestrator.get_medical_document_extractor") as mock_get_ext, \
         patch("app.services.pipeline_orchestrator.append_audit_log_or_503", new_callable=AsyncMock):
        mock_extractor = MagicMock()
        mock_extractor.extract_data = AsyncMock(return_value=mock_vlm)
        mock_get_ext.return_value = mock_extractor

        res = await process_extraction_job(str(job_uuid), mock_db)
        assert res["auto_approved_count"] >= 1
        ef_records = [m for m in added if isinstance(m, ExtractedFieldRecord)]
        assert any(r.status == "auto_approved" for r in ef_records)


@pytest.mark.asyncio
async def test_extraction_routes_to_review():
    """Test 4: Extraction orchestrator routes risky or low-confidence fields to review queue."""
    job_uuid = uuid.uuid4()
    doc_uuid = uuid.uuid4()
    now = datetime.now(timezone.utc)
    job = ExtractionJob(id=job_uuid, patient_id=uuid.uuid4(), document_id=doc_uuid, document_type="LAB_REPORT", status="queued", created_at=now)
    doc_storage = DocumentStorage(id=doc_uuid, patient_id=job.patient_id, storage_ref="s3://doc.pdf", content_type="application/pdf", size=1024, uploaded_at=now)

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(side_effect=[FakeScalarResult(job), FakeScalarResult(doc_storage)])
    added = []
    mock_db.add = lambda m: added.append(m)
    mock_db.commit = AsyncMock()

    mock_vlm = ExtractedMedicalDocument(
        patient_name="Aarav Sharma",
        aadhaar_abha_id="1234",
        phone="9876543210",
        diagnoses=["Severe Cardiac Arrhythmia"],
        lab_results=["HbA1c 9.8%"],
        prescriptions=[],
        extraction_confidence=0.88,
    )
    with patch("app.services.pipeline_orchestrator.get_medical_document_extractor") as mock_get_ext, \
         patch("app.services.pipeline_orchestrator.append_audit_log_or_503", new_callable=AsyncMock):
        mock_extractor = MagicMock()
        mock_extractor.extract_data = AsyncMock(return_value=mock_vlm)
        mock_get_ext.return_value = mock_extractor

        res = await process_extraction_job(str(job_uuid), mock_db)
        assert res["needs_review_count"] >= 1
        assert any(isinstance(m, ReviewQueueItem) for m in added)


@pytest.mark.asyncio
async def test_critical_field_to_review():
    """Test 5: CRITICAL_RISK field always routes to review queue even with high confidence (Invariant 4)."""
    job_uuid = uuid.uuid4()
    doc_uuid = uuid.uuid4()
    now = datetime.now(timezone.utc)
    job = ExtractionJob(id=job_uuid, patient_id=uuid.uuid4(), document_id=doc_uuid, document_type="LAB_REPORT", status="queued", created_at=now)
    doc_storage = DocumentStorage(id=doc_uuid, patient_id=job.patient_id, storage_ref="s3://doc.pdf", content_type="application/pdf", size=1024, uploaded_at=now)

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(side_effect=[FakeScalarResult(job), FakeScalarResult(doc_storage)])
    added = []
    mock_db.add = lambda m: added.append(m)
    mock_db.commit = AsyncMock()

    # Even if confidence is 0.99, CRITICAL_RISK or HIGH_RISK must route to review
    mock_vlm = ExtractedMedicalDocument(
        patient_name="Aarav Sharma",
        aadhaar_abha_id="1234",
        phone="9876543210",
        diagnoses=[],
        lab_results=["HbA1c 11.2% Critical"],
        prescriptions=[],
        extraction_confidence=0.99,
    )
    with patch("app.services.pipeline_orchestrator.get_medical_document_extractor") as mock_get_ext, \
         patch("app.services.pipeline_orchestrator.append_audit_log_or_503", new_callable=AsyncMock):
        mock_extractor = MagicMock()
        mock_extractor.extract_data = AsyncMock(return_value=mock_vlm)
        mock_get_ext.return_value = mock_extractor

        res = await process_extraction_job(str(job_uuid), mock_db)
        assert res["needs_review_count"] >= 1
        ef_records = [m for m in added if isinstance(m, ExtractedFieldRecord)]
        assert all(r.status == "needs_review" for r in ef_records)


@pytest.mark.asyncio
async def test_audit_on_each_stage():
    """Test 6: Orchestrator audits EXTRACTION_JOB_STARTED, field review/approval, and EXTRACTION_JOB_SCORED."""
    job_uuid = uuid.uuid4()
    doc_uuid = uuid.uuid4()
    now = datetime.now(timezone.utc)
    job = ExtractionJob(id=job_uuid, patient_id=uuid.uuid4(), document_id=doc_uuid, document_type="LAB_REPORT", status="queued", created_at=now)
    doc_storage = DocumentStorage(id=doc_uuid, patient_id=job.patient_id, storage_ref="s3://doc.pdf", content_type="application/pdf", size=1024, uploaded_at=now)

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(side_effect=[FakeScalarResult(job), FakeScalarResult(doc_storage)])
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    mock_vlm = ExtractedMedicalDocument(
        patient_name="Aarav Sharma",
        aadhaar_abha_id="1234",
        phone="9876543210",
        diagnoses=[],
        lab_results=["Sodium 4.2 mmol/L"],
        prescriptions=[],
        extraction_confidence=0.98,
    )
    with patch("app.services.pipeline_orchestrator.get_medical_document_extractor") as mock_get_ext, \
         patch("app.services.pipeline_orchestrator.append_audit_log_or_503", new_callable=AsyncMock) as mock_audit:
        mock_extractor = MagicMock()
        mock_extractor.extract_data = AsyncMock(return_value=mock_vlm)
        mock_get_ext.return_value = mock_extractor

        await process_extraction_job(str(job_uuid), mock_db)
        event_types = [call.kwargs.get("event_type") for call in mock_audit.call_args_list]
        assert "EXTRACTION_JOB_STARTED" in event_types
        assert "EXTRACTION_JOB_SCORED" in event_types
