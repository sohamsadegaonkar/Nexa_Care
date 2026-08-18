"""Static A1 contract checks for durable asynchronous provider attempts."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.models.pipeline import ExtractionProviderJobRecord


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "alembic" / "versions" / "20260818_async_provider_jobs.py"


def _migration():
    spec = importlib.util.spec_from_file_location(
        "async_provider_jobs_migration", MIGRATION_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_is_the_single_forward_a1_head_without_backfill():
    migration = _migration()
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert migration.revision == "20260818_async_provider_jobs"
    assert migration.down_revision == "20260817_failure_quarantine"
    assert "op.execute" not in source
    assert "op.delete" not in source


def test_model_has_value_free_operational_schema_and_graph_constraints():
    table = ExtractionProviderJobRecord.__table__
    assert table.name == "extraction_provider_jobs"
    assert list(table.primary_key.columns.keys()) == ["id"]
    assert table.c.id.type.python_type is __import__("uuid").UUID
    assert table.c.status.type.length == 40
    assert table.c.provider_job_id.type.length == 256
    assert table.c.client_request_token_digest.nullable is False
    assert table.c.provider_request_fingerprint.nullable is False

    fks = {constraint.name: constraint for constraint in table.foreign_key_constraints}
    assert (
        fks["fk_extraction_provider_jobs_authoritative_job_graph"].ondelete
        == "RESTRICT"
    )
    assert (
        fks["fk_extraction_provider_jobs_authoritative_document_graph"].ondelete
        == "RESTRICT"
    )
    supersession_fk = fks["fk_extraction_provider_jobs_supersedes_same_graph"]
    assert supersession_fk.ondelete == "RESTRICT"
    assert [element.parent.name for element in supersession_fk.elements] == [
        "supersedes_provider_attempt_id",
        "job_id",
        "tenant_id",
        "patient_id",
        "source_document_id",
    ]
    assert [element.target_fullname for element in supersession_fk.elements] == [
        "extraction_provider_jobs.id",
        "extraction_provider_jobs.job_id",
        "extraction_provider_jobs.tenant_id",
        "extraction_provider_jobs.patient_id",
        "extraction_provider_jobs.source_document_id",
    ]
    assert "fk_extraction_provider_jobs_supersedes_attempt" not in fks
    assert {
        element.target_fullname
        for element in fks[
            "fk_extraction_provider_jobs_authoritative_job_graph"
        ].elements
    } == {
        "extraction_jobs.id",
        "extraction_jobs.tenant_id",
        "extraction_jobs.patient_id",
        "extraction_jobs.document_id",
    }
    assert {
        element.target_fullname
        for element in fks[
            "fk_extraction_provider_jobs_authoritative_document_graph"
        ].elements
    } == {
        "document_storage.id",
        "document_storage.tenant_id",
        "document_storage.patient_id",
    }

    constraints = {
        c.name: str(c.sqltext)
        for c in table.constraints
        if c.name and hasattr(c, "sqltext")
    }
    assert (
        "PROVIDER_UNREACHABLE_MANUAL_REVIEW"
        in constraints["ck_extraction_provider_jobs_status"]
    )
    assert (
        "response_complete"
        in constraints["ck_extraction_provider_jobs_complete_safety"]
    )
    assert (
        "expected_page_count = observed_page_count"
        in constraints["ck_extraction_provider_jobs_complete_safety"]
    )
    assert (
        "superseded_at" in constraints["ck_extraction_provider_jobs_superseded_safety"]
    )
    assert (
        "supersession_idempotency_key"
        in constraints["ck_extraction_provider_jobs_replacement_metadata"]
    )
    assert (
        "response_complete = false"
        in constraints["ck_extraction_provider_jobs_manual_review_incomplete"]
    )
    assert (
        "job_attempt_number >= 1"
        in constraints["ck_extraction_provider_jobs_attempt_positive"]
    )
    assert "version >= 1" in constraints["ck_extraction_provider_jobs_version"]

    index_names = {index.name for index in table.indexes}
    assert "uq_extraction_provider_jobs_provider_job_id" in index_names
    assert "uq_extraction_provider_jobs_supersession_key" in index_names
    assert "ix_extraction_provider_jobs_reconciliation_claim" in index_names
    assert "ix_extraction_provider_jobs_job_status" in index_names
    assert "ix_extraction_provider_jobs_tenant_patient_job" in index_names
    assert any(
        constraint.name == "uq_extraction_provider_jobs_adapter_token_digest"
        for constraint in table.constraints
    )
    assert any(
        constraint.name == "uq_extraction_provider_jobs_logical_attempt"
        for constraint in table.constraints
    )
    authoritative_graph = next(
        constraint
        for constraint in table.constraints
        if constraint.name == "uq_extraction_provider_jobs_authoritative_graph"
    )
    assert list(authoritative_graph.columns.keys()) == [
        "id",
        "job_id",
        "tenant_id",
        "patient_id",
        "source_document_id",
    ]


def test_migration_declares_composite_same_graph_supersession_contract():
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    normalized = " ".join(source.split())
    assert "uq_extraction_provider_jobs_authoritative_graph" in source
    assert "fk_extraction_provider_jobs_supersedes_same_graph" in source
    assert "fk_extraction_provider_jobs_supersedes_attempt" not in source
    assert (
        '"supersedes_provider_attempt_id", "job_id", "tenant_id", "patient_id", "source_document_id"'
        in normalized
    )
    assert (
        '"extraction_provider_jobs.id", "extraction_provider_jobs.job_id", '
        '"extraction_provider_jobs.tenant_id", "extraction_provider_jobs.patient_id", '
        '"extraction_provider_jobs.source_document_id"' in normalized
    )


def test_migration_and_model_exclude_provider_payloads_and_page_resume_state():
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    columns = set(ExtractionProviderJobRecord.__table__.columns.keys())
    forbidden = {
        "raw_ocr",
        "provider_response",
        "response_json",
        "partial_result",
        "pagination_token",
        "page_checkpoint",
        "clinical_value",
        "source_text",
        "bounding_box",
    }
    assert not any(
        any(fragment in column for fragment in forbidden) for column in columns
    )
    assert not any(fragment in source for fragment in forbidden)


def test_migration_header_documents_a1_operational_safety_contract():
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    for phrase in (
        "Purpose:",
        "Preconditions:",
        "Existing-data behavior:",
        "Locking risk:",
        "Rollback behavior:",
        "Validation approach:",
        "Forward-fix approach:",
    ):
        assert phrase in source
