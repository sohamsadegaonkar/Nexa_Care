"""Add patient-owned external record import persistence.

Revision ID: 20260916_patient_external_record_import
Revises: 20260916_clinical_access_sessions
Create Date: 2026-09-16 20:20:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260916_patient_external_record_import"
down_revision: Union[str, None] = "20260916_clinical_access_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_document_storage_patient_self_hash",
        "document_storage",
        ["patient_id", "content_hash"],
        unique=True,
        postgresql_where=sa.text("tenant_id IS NULL AND content_hash IS NOT NULL"),
    )

    op.create_table(
        "patient_external_record_imports",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("extractor_provider", sa.String(length=32), nullable=True),
        sa.Column("extractor_version", sa.String(length=64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("final_record_type", sa.String(length=32), nullable=True),
        sa.Column("final_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("timeline_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "category IN ('PRESCRIPTION','LAB_REPORT','IMAGING_REPORT',"
            "'DISCHARGE_SUMMARY','OTHER_MEDICAL_RECORD')",
            name="ck_patient_external_record_import_category",
        ),
        sa.CheckConstraint(
            "status IN ('UPLOADED','PROCESSING','REVIEW_REQUIRED','READY_TO_SAVE',"
            "'COMPLETED','FAILED_RETRYABLE','FAILED_TERMINAL','CANCELLED')",
            name="ck_patient_external_record_import_status",
        ),
        sa.CheckConstraint(
            "(status = 'FAILED_RETRYABLE' AND retryable) OR "
            "(status <> 'FAILED_RETRYABLE')",
            name="ck_patient_external_record_import_retryable_status",
        ),
        sa.CheckConstraint(
            "(status = 'COMPLETED' AND final_record_type IS NOT NULL "
            "AND final_record_id IS NOT NULL AND timeline_event_id IS NOT NULL "
            "AND completed_at IS NOT NULL) OR status <> 'COMPLETED'",
            name="ck_patient_external_record_import_completion_refs",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["patients.patient_uuid"],
            name="fk_patient_external_record_import_patient", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id", "patient_id"],
            ["document_storage.id", "document_storage.patient_id"],
            name="fk_patient_external_record_import_source_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
        sa.UniqueConstraint(
            "source_document_id", "patient_id",
            name="uq_patient_external_record_import_source_owner",
        ),
        sa.UniqueConstraint(
            "id", "patient_id", "source_document_id",
            name="uq_patient_external_record_import_graph",
        ),
    )
    op.create_index(
        "ix_patient_external_record_import_patient_status",
        "patient_external_record_imports",
        ["patient_id", "status", "created_at"],
    )

    op.create_table(
        "patient_external_record_candidates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("import_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_name", sa.String(length=128), nullable=False),
        sa.Column("clinical_fact_key", sa.String(length=64), nullable=True),
        sa.Column("encrypted_raw_value", sa.Text(), nullable=False),
        sa.Column("encrypted_source_text", sa.Text(), nullable=True),
        sa.Column("encrypted_reviewed_value", sa.Text(), nullable=True),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("source_bbox_json", sa.Text(), nullable=True),
        sa.Column("field_confidence", sa.Float(), nullable=True),
        sa.Column("document_confidence", sa.Float(), nullable=True),
        sa.Column("extractor_provider", sa.String(length=32), nullable=False),
        sa.Column("extractor_version", sa.String(length=64), nullable=False),
        sa.Column("evidence_complete", sa.Boolean(), nullable=False),
        sa.Column(
            "review_status",
            sa.String(length=24),
            nullable=False,
            server_default="NEEDS_REVIEW",
        ),
        sa.Column(
            "patient_reviewed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "review_status IN ('NEEDS_REVIEW','ACCEPTED','CORRECTED','REJECTED')",
            name="ck_patient_external_record_candidate_review_status",
        ),
        sa.CheckConstraint(
            "field_confidence IS NULL OR "
            "(field_confidence >= 0.0 AND field_confidence <= 1.0)",
            name="ck_patient_external_record_candidate_field_confidence",
        ),
        sa.CheckConstraint(
            "document_confidence IS NULL OR "
            "(document_confidence >= 0.0 AND document_confidence <= 1.0)",
            name="ck_patient_external_record_candidate_document_confidence",
        ),
        sa.CheckConstraint(
            "(review_status = 'CORRECTED' AND patient_reviewed "
            "AND encrypted_reviewed_value IS NOT NULL AND reviewed_at IS NOT NULL) OR "
            "review_status <> 'CORRECTED'",
            name="ck_patient_external_record_candidate_correction_provenance",
        ),
        sa.ForeignKeyConstraint(
            ["import_id", "patient_id", "source_document_id"],
            [
                "patient_external_record_imports.id",
                "patient_external_record_imports.patient_id",
                "patient_external_record_imports.source_document_id",
            ],
            name="fk_patient_external_record_candidate_import_graph",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evidence_id"),
    )
    op.create_index(
        "ix_patient_external_record_candidate_import_review",
        "patient_external_record_candidates",
        ["import_id", "review_status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_patient_external_record_candidate_import_review",
        table_name="patient_external_record_candidates",
    )
    op.drop_table("patient_external_record_candidates")
    op.drop_index(
        "ix_patient_external_record_import_patient_status",
        table_name="patient_external_record_imports",
    )
    op.drop_table("patient_external_record_imports")
    op.drop_index("uq_document_storage_patient_self_hash", table_name="document_storage")
