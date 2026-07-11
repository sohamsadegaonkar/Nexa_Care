"""Test suite for Workstream 3 & 4 Extracted-Data Ingestion Path (`app/services/record_ingestion.py`).

Verifies:
1. Ingestion of vitals observations (BP, sugar, heart rate).
2. Ingestion of active medications.
3. Ingestion of allergies (strictly enforcing HIGH_RISK).
4. Provenance rejection of fields lacking numeric confidence or risk metadata.
5. Idempotent double-commit prevention per job_id.
6. Creation of chronological TimelineEvent entities for every ingested field.
7. End-to-end integration via pipeline commit route POST /api/v2/pipeline/jobs/{job_id}/commit.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.models.extracted_field import ExtractedField
from app.models.patient_records import Allergy, TimelineEvent
from app.services.record_ingestion import ingest_extracted_fields
from fastapi import HTTPException

client = TestClient(app)


@pytest.fixture(autouse=True)
def auth_override(request, admin_context):
    from app.core.dependencies import get_current_provider
    app.dependency_overrides[get_current_provider] = lambda: admin_context
    yield
    app.dependency_overrides.pop(get_current_provider, None)


class FakeScalarResult:
    def __init__(self, row):
        self._row = row
    def scalar_one_or_none(self):
        return self._row


class FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows
    def scalars(self):
        return MagicMock(all=lambda: self._rows)


@pytest.mark.asyncio
async def test_ingest_vitals():
    """Test 1: Route 'bp' extracted field into Vitals model with provenance."""
    field = ExtractedField(
        field_id="f-1",
        job_id="job-101",
        field_name="bp",
        raw_value="120/80",
        confidence=0.98,
        risk_level="LOW_RISK",
        source_document_id="doc-101",
    )
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=FakeScalarResult(None))
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    with patch("app.services.record_ingestion.append_audit_log_or_503", new_callable=AsyncMock) as mock_audit:
        res = await ingest_extracted_fields("pat-101", "job-101", [field], mock_db)
        assert res.ingested_count == 1
        assert res.vitals_created == 1
        assert res.timeline_events_created == 1
        assert mock_db.add.call_count == 3  # 1 Vitals + 1 TimelineEvent + 1 PipelineCommit
        mock_audit.assert_called_once()
        assert mock_audit.call_args.kwargs["event_type"] == "EXTRACTED_DATA_INGESTED"


@pytest.mark.asyncio
async def test_ingest_medication():
    """Test 2: Route 'medication' extracted field into Medication model."""
    field = ExtractedField(
        field_id="f-2",
        job_id="job-102",
        field_name="medication",
        raw_value="Lisinopril",
        normalized_value="10mg",
        confidence=0.96,
        risk_level="MEDIUM_RISK",
        source_document_id="doc-102",
    )
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=FakeScalarResult(None))
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    with patch("app.services.record_ingestion.append_audit_log_or_503", new_callable=AsyncMock):
        res = await ingest_extracted_fields("pat-101", "job-102", [field], mock_db)
        assert res.ingested_count == 1
        assert res.medications_created == 1


@pytest.mark.asyncio
async def test_ingest_allergy_high_risk():
    """Test 3: Route 'allergy' extracted field into Allergy model and strictly enforce HIGH_RISK."""
    field = ExtractedField(
        field_id="f-3",
        job_id="job-103",
        field_name="allergy",
        raw_value="Penicillin",
        confidence=0.99,
        risk_level="LOW_RISK",  # Passing LOW_RISK
        source_document_id="doc-103",
    )
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=FakeScalarResult(None))
    added_models = []
    mock_db.add = lambda m: added_models.append(m)
    mock_db.commit = AsyncMock()

    with patch("app.services.record_ingestion.append_audit_log_or_503", new_callable=AsyncMock):
        res = await ingest_extracted_fields("pat-101", "job-103", [field], mock_db)
        assert res.allergies_created == 1
        allergy_model = [m for m in added_models if isinstance(m, Allergy)][0]
        assert allergy_model.risk_level == "HIGH_RISK"  # Enforces HIGH_RISK


@pytest.mark.asyncio
async def test_reject_field_without_metadata():
    """Test 4: Reject ExtractedField lacking numeric confidence or risk_level metadata."""
    field = ExtractedField(
        field_id="f-4",
        job_id="job-104",
        field_name="bp",
        raw_value="120/80",
        confidence=None,
        risk_level="LOW_RISK",
    )
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=FakeScalarResult(None))

    with pytest.raises(HTTPException) as exc_info:
        await ingest_extracted_fields("pat-101", "job-104", [field], mock_db)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_idempotent_double_commit():
    """Test 5: Committing the same job_id twice returns 0 ingested and creates no duplicates."""
    field = ExtractedField(
        field_id="f-5",
        job_id="job-idemp-1",
        field_name="sugar",
        raw_value="105 mg/dL",
        confidence=0.97,
        risk_level="LOW_RISK",
        source_document_id="doc-105",
    )
    mock_db = MagicMock()
    # Simulate first run returning None (not ingested yet)
    mock_db.execute = AsyncMock(return_value=FakeScalarResult(None))
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    with patch("app.services.record_ingestion.append_audit_log_or_503", new_callable=AsyncMock):
        res1 = await ingest_extracted_fields("pat-101", "job-idemp-1", [field], mock_db)
        assert res1.ingested_count == 1

        # Simulate second run finding existing TimelineEvent marker
        existing_te = MagicMock(spec=TimelineEvent)
        mock_db.execute = AsyncMock(return_value=FakeScalarResult(existing_te))
        mock_db.add.reset_mock()

        res2 = await ingest_extracted_fields("pat-101", "job-idemp-1", [field], mock_db)
        assert res2.ingested_count == 0
        assert res2.vitals_created == 0
        mock_db.add.assert_not_called()


@pytest.mark.asyncio
async def test_timeline_event_created():
    """Test 6: Verify TimelineEvent created for ingested extracted data."""
    field = ExtractedField(
        field_id="f-6",
        job_id="job-106",
        field_name="hba1c",
        raw_value="6.5 %",
        confidence=0.95,
        risk_level="MEDIUM_RISK",
        source_document_id="doc-106",
    )
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=FakeScalarResult(None))
    added_models = []
    mock_db.add = lambda m: added_models.append(m)
    mock_db.commit = AsyncMock()

    with patch("app.services.record_ingestion.append_audit_log_or_503", new_callable=AsyncMock):
        await ingest_extracted_fields("pat-101", "job-106", [field], mock_db)
        te_models = [m for m in added_models if isinstance(m, TimelineEvent)]
        assert len(te_models) == 1
        assert te_models[0].event_type == "EXTRACTED_DATA_INGESTED"
        assert te_models[0].source == "ai_extracted"


def test_commit_endpoint_integration(admin_headers, mock_db):
    """Test 7: POST /api/v2/pipeline/jobs/{job_id}/commit triggers ingestion path.

    ALPHA: Commit handler now loads the job first and validates patient_id
    server-side.  The mock DB must return a job with patient_id matching
    the payload.
    """
    from app.core.consent_gate import ConsentCapability
    from app.api.v2.pipeline_routes import _parse_uuid

    mock_cap = ConsentCapability(
        patient_id="pat-101",
        clinician_id="doc-202",
        purpose="pipeline_commit",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )

    # Mock job with patient_id matching the payload
    mock_job = MagicMock()
    mock_job.patient_id = _parse_uuid("pat-101")
    mock_job.status = "review_required"

    # DB query order: (1) load job → consent → (2) check unresolved fields
    mock_db.execute.side_effect = [
        MagicMock(scalar_one_or_none=MagicMock(return_value=mock_job)),
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
    ]

    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap), \
         patch("app.api.v2.pipeline_routes.ingest_extracted_fields", new_callable=AsyncMock) as mock_ingest:
        payload = {
            "patient_id": "pat-101",
            "encounter_summary": "Extracted labs committed via pipeline",
            "fields": [
                {
                    "field_id": "f-100",
                    "field_name": "bp",
                    "raw_value": "120/80",
                    "confidence": 0.98,
                    "risk_level": "LOW_RISK",
                }
            ],
        }
        res = client.post(
            "/api/v2/pipeline/jobs/job-int-1/commit",
            headers={**admin_headers, "X-Consent-Token": "valid-tok"},
            json=payload,
        )
        assert res.status_code == 201
        data = res.json()
        assert data["job_id"] == "job-int-1"
        assert data["committed_fields_count"] == 1
        mock_ingest.assert_called_once()


@pytest.mark.asyncio
async def test_reject_needs_review_field_on_commit():
    """Test 8: Reject ExtractedField with status='needs_review' from entering clinical record."""
    field = ExtractedField(
        field_id="f-8",
        job_id="job-108",
        field_name="bp",
        raw_value="140/90",
        confidence=0.92,
        risk_level="MEDIUM_RISK",
        status="needs_review",
    )
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=FakeScalarResult(None))

    with pytest.raises(HTTPException) as exc_info:
        await ingest_extracted_fields("pat-101", "job-108", [field], mock_db)
    assert exc_info.value.status_code == 400
    assert "unreviewed or rejected status 'needs_review'" in exc_info.value.detail


@pytest.mark.asyncio
async def test_reject_rejected_field_on_commit():
    """Test 9: Reject ExtractedField with status='rejected' from entering clinical record."""
    field = ExtractedField(
        field_id="f-9",
        job_id="job-109",
        field_name="sugar",
        raw_value="300 mg/dL",
        confidence=0.99,
        risk_level="CRITICAL_RISK",
        status="rejected",
    )
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=FakeScalarResult(None))

    with pytest.raises(HTTPException) as exc_info:
        await ingest_extracted_fields("pat-101", "job-109", [field], mock_db)
    assert exc_info.value.status_code == 400
    assert "unreviewed or rejected status 'rejected'" in exc_info.value.detail
