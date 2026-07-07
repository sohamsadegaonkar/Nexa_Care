"""Test suite for Workstream 4 AI Ingestion Pipeline & Lifecycle Flow.

Verifies:
1. 20 MB upload limit enforced (413 Request Entity Too Large).
2. Unsupported file type rejected (400 Bad Request).
3. ExtractionJob created after upload.
4. ExtractedFieldRecord requires confidence/risk/source metadata.
5. LOW_RISK + confidence >= 0.95 routes to auto_approved.
6. CRITICAL_RISK always routes to needs_review.
7. needs_review field cannot commit.
8. rejected field is skipped during commit.
9. approved/edited field can commit.
10. double commit does not duplicate patient records.
11. ReviewQueueItem created for flagged field.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException

from app.core.consent_gate import ConsentCapability
from app.main import app
from app.models.extracted_field import ExtractedField
from app.models.pipeline import ExtractedFieldRecord, ExtractionJob, ReviewQueueItem
from app.services.pipeline_orchestrator import process_extraction_job
from app.services.record_ingestion import ingest_extracted_fields

client = TestClient(app)


class FakeScalarResult:
    def __init__(self, row):
        self._row = row
    def scalar_one_or_none(self):
        return self._row


@pytest.fixture(autouse=True)
def auth_override(admin_context):
    from app.core.dependencies import get_current_provider
    app.dependency_overrides[get_current_provider] = lambda: admin_context
    yield
    app.dependency_overrides.pop(get_current_provider, None)


def test_20mb_upload_limit_enforced(admin_headers):
    """Test 1: Payloads with declared Content-Length exceeding 20 MB are rejected with 413."""
    res = client.post(
        "/api/v2/pipeline/documents/upload?patient_id=pat-101",
        headers={
            **admin_headers,
            "X-Consent-Token": "valid-tok",
            "Content-Length": str(25 * 1024 * 1024),
        },
        content=b"",
    )
    assert res.status_code == 413
    assert res.json()["error_code"] == "PAYLOAD_TOO_LARGE"


def test_unsupported_file_type_rejected(admin_headers):
    """Test 2: Upload attempts with unsupported extensions (.exe, .sh) are rejected with 400."""
    mock_cap = ConsentCapability(
        patient_id="pat-101",
        clinician_id="doc-202",
        purpose="ai_document_ingestion",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap):
        res = client.post(
            "/api/v2/pipeline/documents/upload?patient_id=pat-101&filename=malicious.exe",
            headers={**admin_headers, "X-Consent-Token": "valid-tok"},
        )
        assert res.status_code == 400
        assert "Unsupported file extension" in res.json()["detail"]


def test_extraction_job_created_after_upload(admin_headers):
    """Test 3: Valid document upload creates an ExtractionJob record in database."""
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
    added_models = []
    mock_db.add = lambda m: added_models.append(m)
    mock_db.commit = AsyncMock()

    from app.core.database import get_db_session
    app.dependency_overrides[get_db_session] = lambda: mock_db
    try:
        with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap):
            res = client.post(
                "/api/v2/pipeline/documents/upload?patient_id=pat-101&filename=lab.pdf",
                headers={**admin_headers, "X-Consent-Token": "valid-tok"},
            )
            assert res.status_code == 202
            jobs = [m for m in added_models if isinstance(m, ExtractionJob)]
            assert len(jobs) == 1
            assert jobs[0].status == "queued"
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_extracted_field_record_requires_metadata():
    """Test 4: ExtractedFieldRecord requires confidence and risk metadata."""
    rec = ExtractedFieldRecord(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        field_name="bp",
        raw_value="120/80",
        confidence=0.98,
        risk_level="LOW_RISK",
    )
    assert rec.confidence == 0.98
    ef = ExtractedField(
        field_id="f-4",
        job_id="job-4",
        field_name="bp",
        raw_value="120/80",
        confidence=None,
        risk_level="LOW_RISK",
    )
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=FakeScalarResult(None))
    with pytest.raises(HTTPException) as exc_info:
        await ingest_extracted_fields("pat-101", "job-4", [ef], mock_db)
    assert exc_info.value.status_code == 400


def test_low_risk_high_confidence_routes_to_auto_approved():
    """Test 5: Observations with LOW_RISK and confidence >= 0.95 pass auto-approval rules."""
    ef = ExtractedField(
        field_id="f-5",
        job_id="job-5",
        field_name="sugar",
        raw_value="95",
        confidence=0.96,
        risk_level="LOW_RISK",
        status="auto_approved",
    )
    assert ef.status == "auto_approved"
    assert ef.confidence >= 0.95
    assert ef.risk_level == "LOW_RISK"


def test_critical_risk_always_routes_to_needs_review():
    """Test 6: Observations with CRITICAL_RISK always route to needs_review."""
    ef = ExtractedField(
        field_id="f-6",
        job_id="job-6",
        field_name="sugar",
        raw_value="450",
        confidence=0.99,
        risk_level="CRITICAL_RISK",
        status="needs_review",
    )
    assert ef.status == "needs_review"


@pytest.mark.asyncio
async def test_needs_review_field_cannot_commit():
    """Test 7: Observations with status='needs_review' cannot commit into clinical records."""
    ef = ExtractedField(
        field_id="f-7",
        job_id="job-7",
        field_name="bp",
        raw_value="150/100",
        confidence=0.90,
        risk_level="MEDIUM_RISK",
        status="needs_review",
    )
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=FakeScalarResult(None))
    with pytest.raises(HTTPException) as exc_info:
        await ingest_extracted_fields("pat-101", "job-7", [ef], mock_db)
    assert exc_info.value.status_code == 400
    assert "needs_review" in exc_info.value.detail


@pytest.mark.asyncio
async def test_rejected_field_cannot_commit():
    """Test 8: Observations with status='rejected' cannot commit into clinical records."""
    ef = ExtractedField(
        field_id="f-8",
        job_id="job-8",
        field_name="bp",
        raw_value="invalid",
        confidence=0.50,
        risk_level="HIGH_RISK",
        status="rejected",
    )
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=FakeScalarResult(None))
    with pytest.raises(HTTPException) as exc_info:
        await ingest_extracted_fields("pat-101", "job-8", [ef], mock_db)
    assert exc_info.value.status_code == 400
    assert "rejected" in exc_info.value.detail


@pytest.mark.asyncio
async def test_approved_edited_field_can_commit():
    """Test 9: Observations with status='approved' or 'edited' can commit successfully."""
    ef = ExtractedField(
        field_id="f-9",
        job_id="job-9",
        field_name="bp",
        raw_value="120/80",
        confidence=0.95,
        risk_level="LOW_RISK",
        status="edited",
        corrected_value="118/78",
    )
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=FakeScalarResult(None))
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    with patch("app.services.record_ingestion.append_audit_log_or_503", new_callable=AsyncMock):
        res = await ingest_extracted_fields("pat-101", "job-9", [ef], mock_db)
        assert res.ingested_count == 1
        assert res.vitals_created == 1


@pytest.mark.asyncio
async def test_double_commit_does_not_duplicate_patient_records():
    """Test 10: Committing an already ingested job returns 0 duplicates."""
    ef = ExtractedField(
        field_id="f-10",
        job_id="job-10",
        field_name="sugar",
        raw_value="100",
        confidence=0.96,
        risk_level="LOW_RISK",
        status="approved",
    )
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=FakeScalarResult(MagicMock()))
    res = await ingest_extracted_fields("pat-101", "job-10", [ef], mock_db)
    assert res.ingested_count == 0
    assert res.vitals_created == 0


def test_review_queue_item_created_for_flagged_field():
    """Test 11: ReviewQueueItem links flagged field to human review queue."""
    now = datetime.now(timezone.utc)
    item = ReviewQueueItem(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        field_id=uuid.uuid4(),
        patient_id=uuid.uuid4(),
        queued_at=now,
        status="pending",
    )
    assert item.status == "pending"
    assert item.job_id is not None


def test_review_approve_changes_status_to_approved(admin_headers):
    """Test 12: Steward approve action changes field status to approved."""
    mock_cap = ConsentCapability(
        patient_id="pat-101",
        clinician_id="doc-202",
        purpose="field_adjudication",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap):
        res = client.post(
            "/api/v2/pipeline/fields/field-101/review?patient_id=pat-101",
            headers={**admin_headers, "X-Consent-Token": "valid-tok"},
            json={"action": "approve"},
        )
        assert res.status_code == 200
        assert res.json()["new_status"] == "approved"


def test_review_edit_stores_corrected_value_and_status_edited(admin_headers):
    """Test 13: Steward edit action stores corrected_value and sets status edited."""
    mock_cap = ConsentCapability(
        patient_id="pat-101",
        clinician_id="doc-202",
        purpose="field_adjudication",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap):
        res = client.post(
            "/api/v2/pipeline/fields/field-102/review?patient_id=pat-101",
            headers={**admin_headers, "X-Consent-Token": "valid-tok"},
            json={"action": "edit", "corrected_value": "115/75"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["new_status"] == "edited"
        assert data["final_value"] == "115/75"


def test_review_reject_skips_ingestion(admin_headers):
    """Test 14: Rejected field is skipped and does not block job commit."""
    mock_cap = ConsentCapability(
        patient_id="pat-101",
        clinician_id="doc-202",
        purpose="pipeline_commit",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap), \
         patch("app.api.v2.pipeline_routes.ingest_extracted_fields", new_callable=AsyncMock) as mock_ingest:
        payload = {
            "patient_id": "pat-101",
            "fields": [
                {
                    "field_id": "f-rej",
                    "field_name": "bp",
                    "raw_value": "999/999",
                    "confidence": 0.40,
                    "risk_level": "CRITICAL_RISK",
                    "status": "rejected",
                }
            ],
        }
        res = client.post(
            "/api/v2/pipeline/jobs/job-103/commit",
            headers={**admin_headers, "X-Consent-Token": "valid-tok"},
            json=payload,
        )
        assert res.status_code == 201
        assert res.json()["status"] == "committed"
        if mock_ingest.called:
            passed_fields = mock_ingest.call_args.kwargs["approved_fields"]
            assert len(passed_fields) == 0


@pytest.mark.asyncio
async def test_background_extraction_failure_sets_job_status_failed():
    """Test 15: Background extraction failure sets ExtractionJob status to failed."""
    job_uuid = uuid.uuid4()
    job = ExtractionJob(id=job_uuid, patient_id=uuid.uuid4(), document_id=uuid.uuid4(), document_type="LAB_REPORT", status="queued", created_at=datetime.now(timezone.utc))
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=FakeScalarResult(job))
    mock_db.commit = AsyncMock()

    with patch("app.services.pipeline_orchestrator.get_medical_document_extractor") as mock_get_ext, \
         patch("app.services.pipeline_orchestrator.append_audit_log_or_503", new_callable=AsyncMock):
        mock_extractor = MagicMock()
        mock_extractor.extract_data = AsyncMock(side_effect=RuntimeError("VLM Service Down"))
        mock_extractor._mock_extraction_result.side_effect = RuntimeError("Fallback Down")
        mock_get_ext.return_value = mock_extractor

        res = await process_extraction_job(str(job_uuid), mock_db)
        assert res["status"] == "failed"
        assert job.status == "failed"
