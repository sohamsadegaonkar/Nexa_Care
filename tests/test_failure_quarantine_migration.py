"""Static contract tests for the Scenario-15 operational lifecycle."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.models.pipeline import ExtractionFailureQuarantineRecord


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "alembic" / "versions" / "20260817_failure_quarantine.py"


def _migration():
    spec = importlib.util.spec_from_file_location(
        "failure_quarantine_migration", MIGRATION_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_is_single_forward_head_without_legacy_backfill():
    migration = _migration()
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert migration.revision == "20260817_failure_quarantine"
    assert migration.down_revision == "20260815_clinical_commit_guard"
    assert "op.execute" not in source
    assert "op.delete" not in source
    assert "20260815_clinical_commit_guard" in source


def test_model_and_migration_agree_on_closed_operational_contract():
    table = ExtractionFailureQuarantineRecord.__table__
    assert table.name == "extraction_failure_quarantines"
    assert list(table.primary_key.columns.keys()) == ["id"]
    assert table.c.job_id.unique is not True
    assert any(
        c.name == "uq_extraction_failure_quarantines_job" for c in table.constraints
    )
    checks = {
        c.name: str(c.sqltext)
        for c in table.constraints
        if c.name and hasattr(c, "sqltext")
    }
    assert "PROVIDER_RETRY_EXHAUSTED" in checks["ck_failure_quarantines_reason_code"]
    assert "PENDING" in checks["ck_failure_quarantines_status"]
    assert "DISPOSED" in checks["ck_failure_quarantines_status"]
    assert (
        "RETAIN_SOURCE_NO_CLINICAL_COMMIT" in checks["ck_failure_quarantines_lifecycle"]
    )
    assert (
        "REJECT_PROCESSING_RETAIN_AUDIT" in checks["ck_failure_quarantines_lifecycle"]
    )
    assert (
        "disposition_idempotency_key"
        in checks["ck_failure_quarantines_idempotency_pair"]
    )
    assert any(
        index.name == "ix_failure_quarantines_processor" for index in table.indexes
    )


def test_new_model_has_no_provider_or_clinical_payload_columns():
    columns = set(ExtractionFailureQuarantineRecord.__table__.columns.keys())
    forbidden_fragments = {
        "raw_ocr",
        "provider_response",
        "clinical_value",
        "source_text",
        "bounding_box",
        "exception",
    }
    assert not any(
        any(fragment in column for fragment in forbidden_fragments)
        for column in columns
    )
