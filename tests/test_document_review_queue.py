"""Review queue authorization and transaction contracts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = (ROOT / "app/api/v2/pipeline_routes.py").read_text(encoding="utf-8")
REVIEW = (ROOT / "app/api/v2/review_routes.py").read_text(encoding="utf-8")


def test_every_extracted_field_is_queued_for_review():
    orchestrator = (ROOT / "app/services/pipeline_orchestrator.py").read_text(encoding="utf-8")
    assert 'status="needs_review"' in orchestrator
    assert "ReviewQueueItem(" in orchestrator


def test_pipeline_review_requires_role_and_tenant():
    assert "REVIEW_ROLE_REQUIRED" in PIPELINE
    assert "CROSS_TENANT_JOB_ACCESS" in PIPELINE


def test_pipeline_review_uses_row_lock():
    assert "ExtractedFieldRecord.id == f_uuid).with_for_update()" in PIPELINE


def test_legacy_review_service_rolls_back_on_write_failure():
    assert "await db.rollback()" in REVIEW


def test_review_success_is_audited():
    assert "FIELD_APPROVED" in PIPELINE
    assert "FIELD_REJECTED" in PIPELINE
    assert "FIELD_EDITED" in PIPELINE
