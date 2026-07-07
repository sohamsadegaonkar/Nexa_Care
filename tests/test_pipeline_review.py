"""Test suite for Workstream 4, 5, 8 Field Adjudication & Job Commit layer (`app/api/v2/pipeline_routes.py`).

Verifies:
1. Approve action sets field status to approved and emits FIELD_APPROVED.
2. Reject action sets status to rejected, stores reason, and emits FIELD_REJECTED.
3. Edit action stores corrected_value, sets status to edited, logs to field_corrections table, and emits FIELD_EDITED.
4. Committing job with all fields resolved pushes approved observations via WS3 ingestion.
5. Committing job with unresolved fields (status=needs_review) raises 409 Conflict.
6. Rejected fields are omitted during job commit.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.consent_gate import ConsentCapability
from app.main import app
from app.models.pipeline import ExtractedFieldRecord, FieldCorrection

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


def test_approve_field(admin_headers):
    """Test 1: POST /api/v2/pipeline/fields/{field_id}/approve marks field approved and audits FIELD_APPROVED."""
    f_id = str(uuid.uuid4())
    mock_cap = ConsentCapability(
        patient_id="pat-101",
        clinician_id="doc-202",
        purpose="field_adjudication",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    field_rec = ExtractedFieldRecord(
        id=uuid.UUID(f_id),
        job_id=uuid.uuid4(),
        field_name="bp",
        raw_value="135/88",
        confidence=0.91,
        risk_level="MEDIUM_RISK",
        status="needs_review",
    )
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(side_effect=[FakeScalarResult(field_rec), FakeScalarResult(None)])
    mock_db.commit = AsyncMock()

    from app.core.database import get_db_session
    app.dependency_overrides[get_db_session] = lambda: mock_db
    try:
        with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap), \
             patch("app.api.v2.pipeline_routes.append_audit_log_or_503", new_callable=AsyncMock) as mock_audit:
            res = client.post(
                f"/api/v2/pipeline/fields/{f_id}/approve?patient_id=pat-101",
                headers={**admin_headers, "X-Consent-Token": "valid-tok"},
            )
            assert res.status_code == 200
            assert res.json()["new_status"] == "approved"
            assert field_rec.status == "approved"
            mock_audit.assert_called_once()
            assert mock_audit.call_args.kwargs["event_type"] == "FIELD_APPROVED"
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_reject_field(admin_headers):
    """Test 2: POST /api/v2/pipeline/fields/{field_id}/reject marks field rejected and audits FIELD_REJECTED."""
    f_id = str(uuid.uuid4())
    mock_cap = ConsentCapability(
        patient_id="pat-101",
        clinician_id="doc-202",
        purpose="field_adjudication",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    field_rec = ExtractedFieldRecord(
        id=uuid.UUID(f_id),
        job_id=uuid.uuid4(),
        field_name="sugar",
        raw_value="invalid_str",
        confidence=0.60,
        risk_level="HIGH_RISK",
        status="needs_review",
    )
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(side_effect=[FakeScalarResult(field_rec), FakeScalarResult(None)])
    mock_db.commit = AsyncMock()

    from app.core.database import get_db_session
    app.dependency_overrides[get_db_session] = lambda: mock_db
    try:
        with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap), \
             patch("app.api.v2.pipeline_routes.append_audit_log_or_503", new_callable=AsyncMock) as mock_audit:
            res = client.post(
                f"/api/v2/pipeline/fields/{f_id}/reject?patient_id=pat-101",
                headers={**admin_headers, "X-Consent-Token": "valid-tok"},
                json={"reason": "OCR Artifact"},
            )
            assert res.status_code == 200
            assert res.json()["new_status"] == "rejected"
            assert field_rec.status == "rejected"
            assert mock_audit.call_args.kwargs["event_type"] == "FIELD_REJECTED"
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_edit_field_and_correction_logged(admin_headers):
    """Test 3 & 6: POST /api/v2/pipeline/fields/{field_id}/edit sets status=edited and writes to field_corrections table."""
    f_id = str(uuid.uuid4())
    mock_cap = ConsentCapability(
        patient_id="pat-101",
        clinician_id="doc-202",
        purpose="field_adjudication",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    field_rec = ExtractedFieldRecord(
        id=uuid.UUID(f_id),
        job_id=uuid.uuid4(),
        field_name="bp",
        raw_value="138/88",
        confidence=0.92,
        risk_level="MEDIUM_RISK",
        status="needs_review",
    )
    mock_db = MagicMock()
    added = []
    mock_db.add = lambda m: added.append(m)
    mock_db.execute = AsyncMock(side_effect=[FakeScalarResult(field_rec), FakeScalarResult(None)])
    mock_db.commit = AsyncMock()

    from app.core.database import get_db_session
    app.dependency_overrides[get_db_session] = lambda: mock_db
    try:
        with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap), \
             patch("app.api.v2.pipeline_routes.append_audit_log_or_503", new_callable=AsyncMock) as mock_audit:
            res = client.post(
                f"/api/v2/pipeline/fields/{f_id}/edit?patient_id=pat-101",
                headers={**admin_headers, "X-Consent-Token": "valid-tok"},
                json={"corrected_value": "130/85"},
            )
            assert res.status_code == 200
            data = res.json()
            assert data["new_status"] == "edited"
            assert data["final_value"] == "130/85"
            assert field_rec.status == "edited"
            assert field_rec.corrected_value == "130/85"

            # Check field_corrections table insertion
            corrections = [m for m in added if isinstance(m, FieldCorrection)]
            assert len(corrections) == 1
            assert corrections[0].original_value == "138/88"
            assert corrections[0].corrected_value == "130/85"
            assert mock_audit.call_args.kwargs["event_type"] == "FIELD_EDITED"
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_commit_with_all_resolved(admin_headers):
    """Test 4: Job commit with all resolved fields ingests approved/edited items and ignores rejected items."""
    mock_cap = ConsentCapability(
        patient_id="pat-101",
        clinician_id="doc-202",
        purpose="pipeline_commit",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    mock_db = MagicMock()
    # Simulate DB query finding NO unresolved fields (empty list)
    mock_db.execute = AsyncMock(return_value=FakeScalarResult(None))
    mock_db.execute.side_effect = [
        MagicMock(scalars=lambda: MagicMock(all=lambda: [])), # unres query
        FakeScalarResult(None), # job query
    ]
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    from app.core.database import get_db_session
    app.dependency_overrides[get_db_session] = lambda: mock_db
    try:
        with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap), \
             patch("app.api.v2.pipeline_routes.ingest_extracted_fields", new_callable=AsyncMock) as mock_ingest, \
             patch("app.api.v2.pipeline_routes.append_audit_log_or_503", new_callable=AsyncMock) as mock_audit:
            payload = {
                "patient_id": "pat-101",
                "encounter_summary": "Resolved labs committed",
                "fields": [
                    {"field_id": "f-1", "field_name": "bp", "value": "120/80", "confidence": 0.98, "risk_level": "LOW_RISK", "status": "approved"},
                    {"field_id": "f-2", "field_name": "sugar", "value": "999", "confidence": 0.40, "risk_level": "CRITICAL_RISK", "status": "rejected"},
                ],
            }
            res = client.post(
                "/api/v2/pipeline/jobs/job-res-1/commit",
                headers={**admin_headers, "X-Consent-Token": "valid-tok"},
                json=payload,
            )
            assert res.status_code == 201
            data = res.json()
            assert data["status"] == "committed"
            # Verify only approved item was passed to ingest_extracted_fields (rejected item filtered out)
            passed_fields = mock_ingest.call_args[1]["approved_fields"]
            assert len(passed_fields) == 1
            assert passed_fields[0].field_id == "f-1"
            assert mock_audit.call_args.kwargs["event_type"] == "JOB_COMMITTED"
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_commit_with_unresolved_returns_409(admin_headers):
    """Test 5: Job commit with unresolved fields (needs_review) raises 409 Conflict."""
    mock_cap = ConsentCapability(
        patient_id="pat-101",
        clinician_id="doc-202",
        purpose="pipeline_commit",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    unres_rec = ExtractedFieldRecord(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        field_name="bp",
        raw_value="140/90",
        confidence=0.88,
        risk_level="MEDIUM_RISK",
        status="needs_review",
    )
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [unres_rec])))

    from app.core.database import get_db_session
    app.dependency_overrides[get_db_session] = lambda: mock_db
    try:
        with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap):
            payload = {
                "patient_id": "pat-101",
                "fields": [
                    {"field_id": "f-1", "field_name": "bp", "value": "140/90", "confidence": 0.88, "risk_level": "MEDIUM_RISK", "status": "needs_review"},
                ],
            }
            res = client.post(
                "/api/v2/pipeline/jobs/job-unres-1/commit",
                headers={**admin_headers, "X-Consent-Token": "valid-tok"},
                json=payload,
            )
            assert res.status_code == 409
            assert "Review incomplete" in res.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_rejected_field_not_ingested(admin_headers):
    """Test 6: Rejected field is strictly omitted from clinical sub-models and patient timeline upon commit."""
    mock_cap = ConsentCapability(
        patient_id="pat-101",
        clinician_id="doc-202",
        purpose="pipeline_commit",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    rej_rec = ExtractedFieldRecord(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        field_name="sugar",
        raw_value="999 mg/dL",
        confidence=0.40,
        risk_level="CRITICAL_RISK",
        status="rejected",
    )
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=FakeScalarResult(None))
    mock_db.execute.side_effect = [
        MagicMock(scalars=lambda: MagicMock(all=lambda: [])),  # unres query returns 0 needs_review
        MagicMock(scalars=lambda: MagicMock(all=lambda: [rej_rec])),  # approved/edited query
        FakeScalarResult(None),  # job query
    ]
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    from app.core.database import get_db_session
    app.dependency_overrides[get_db_session] = lambda: mock_db
    try:
        with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap), \
             patch("app.api.v2.pipeline_routes.ingest_extracted_fields", new_callable=AsyncMock) as mock_ingest:
            payload = {
                "patient_id": "pat-101",
                "fields": [
                    {"field_id": "f-rej", "field_name": "sugar", "value": "999 mg/dL", "confidence": 0.40, "risk_level": "CRITICAL_RISK", "status": "rejected"},
                ],
            }
            res = client.post(
                "/api/v2/pipeline/jobs/job-rej-1/commit",
                headers={**admin_headers, "X-Consent-Token": "valid-tok"},
                json=payload,
            )
            assert res.status_code == 201
            # Verify ingest_extracted_fields received 0 approved fields (rejected field filtered out)
            if mock_ingest.called:
                passed_fields = mock_ingest.call_args[1]["approved_fields"]
                assert len(passed_fields) == 0
                assert all(f.status != "rejected" for f in passed_fields)
    finally:
        app.dependency_overrides.pop(get_db_session, None)
