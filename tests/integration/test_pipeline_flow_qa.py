"""Integration-level safety contracts for the document pipeline."""

from pathlib import Path

import pytest

from app.ai.identity_decision import IdentityDecisionState
from app.api.v2.pipeline_routes import ALLOWED_COMMIT_STATUSES, CommitJobRequest
from app.core.dependencies import get_current_provider
from app.main import app
from app.models.extraction_decision import RUNTIME_AUTO_COMMIT_ENABLED
from app.models.pipeline import ExtractedFieldRecord
from app.services.pipeline_orchestrator import (
    _IDENTITY_ERROR_CODES,
    _IDENTITY_REASON_CODES,
)

ROOT = Path(__file__).resolve().parents[2]
ROUTES = (ROOT / "app/api/v2/pipeline_routes.py").read_text(encoding="utf-8")
ORCHESTRATOR = (ROOT / "app/services/pipeline_orchestrator.py").read_text(
    encoding="utf-8"
)
ROUTING = (ROOT / "app/services/extraction_routing.py").read_text(encoding="utf-8")


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
    assert 'job.status = "identity_mismatch"' not in ORCHESTRATOR
    assert "identity_quarantine" in ORCHESTRATOR
    assert "IdentityDecisionState.IDENTITY_DISCREPANCY" in ORCHESTRATOR
    assert "IdentityDecisionState.IDENTITY_CONFLICTING" in ORCHESTRATOR
    assert "IdentityDecisionState.IDENTITY_INSUFFICIENT" in ORCHESTRATOR
    assert _IDENTITY_ERROR_CODES == {
        IdentityDecisionState.IDENTITY_DISCREPANCY: "EXTRACTED_IDENTITY_MISMATCH",
        IdentityDecisionState.IDENTITY_CONFLICTING: "EXTRACTED_IDENTITY_MISMATCH",
        IdentityDecisionState.IDENTITY_INSUFFICIENT: "EXTRACTED_IDENTITY_UNAVAILABLE",
    }
    assert _IDENTITY_REASON_CODES == {
        IdentityDecisionState.IDENTITY_DISCREPANCY: "IDENTITY_MISMATCH",
        IdentityDecisionState.IDENTITY_CONFLICTING: "IDENTITY_MISMATCH",
        IdentityDecisionState.IDENTITY_INSUFFICIENT: "IDENTITY_UNAVAILABLE",
    }
    assert "job.error_code = (" in ORCHESTRATOR
    assert "identity_error_code" in ORCHESTRATOR
    assert '"reason_code": identity_reason_code' in ORCHESTRATOR
    assert '"quarantined"' in ORCHESTRATOR
    assert '"idempotent": True' in ORCHESTRATOR
    assert '"extraction_failed_retryable"' in ORCHESTRATOR
    assert "if exhausted" in ORCHESTRATOR
    assert "evaluate_and_persist_lane" in ORCHESTRATOR
    assert '"source_only"' in ORCHESTRATOR
    assert "quarantine_review_deadline=(" in ORCHESTRATOR
    assert "if identity_quarantine" in ORCHESTRATOR
    assert "SOURCE_ONLY_DEADLINE_FORBIDDEN" in ROUTING
    assert "QUARANTINED_JOB_NOT_COMMITTABLE" in ROUTES
    assert 'if job.status == "quarantined"' in ROUTES
    assert "job.patient_id =" not in ORCHESTRATOR
    assert RUNTIME_AUTO_COMMIT_ENABLED is False
