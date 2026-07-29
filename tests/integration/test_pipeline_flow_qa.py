"""Integration-level safety contracts for the document pipeline."""

from pathlib import Path

import pytest

from app.api.v2.pipeline_routes import ALLOWED_COMMIT_STATUSES, CommitJobRequest
from app.core.dependencies import get_current_provider
from app.main import app
from app.models.pipeline import ExtractedFieldRecord

ROOT = Path(__file__).resolve().parents[2]
ROUTES = (ROOT / "app/api/v2/pipeline_routes.py").read_text(encoding="utf-8")
ORCHESTRATOR = (ROOT / "app/services/pipeline_orchestrator.py").read_text(
    encoding="utf-8"
)


@pytest.mark.integration
def test_upload_requires_real_multipart_document(
    test_client, admin_headers, admin_context
):
    app.dependency_overrides[get_current_provider] = lambda: admin_context
    try:
        response = test_client.post(
            "/api/v2/pipeline/documents/upload?patient_id=11111111-1111-4111-8111-111111111111",
            json={},
            headers=admin_headers,
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_provider, None)


def test_extracted_records_default_to_human_review():
    assert ExtractedFieldRecord.__table__.c.status.default.arg == "needs_review"


def test_only_human_adjudicated_statuses_can_commit():
    assert ALLOWED_COMMIT_STATUSES == {"approved", "edited"}


def test_commit_payload_retains_field_only_to_reject_it_explicitly():
    assert CommitJobRequest(patient_id=None, fields=[]).fields == []
    assert "CLIENT_SUPPLIED_COMMIT_FIELDS_FORBIDDEN" in ROUTES


def test_pipeline_derives_patient_and_tenant_from_locked_job():
    assert ".with_for_update()" in ROUTES
    assert "server_pid = str(job.patient_id)" in ROUTES
    assert "CROSS_TENANT_JOB_ACCESS" in ROUTES


def test_orchestrator_has_identity_quarantine_and_retry_quarantine():
    assert 'job.status = "identity_mismatch"' in ORCHESTRATOR
    assert '"quarantined"' in ORCHESTRATOR
    assert "if exhausted" in ORCHESTRATOR
    assert "evaluate_and_persist_lane" in ORCHESTRATOR
    assert '"source_only"' in ORCHESTRATOR
