"""Add AI ingestion pipeline tables (document_storage, extraction_jobs, extracted_fields, review_queue_items).

Revision ID: 20260707_pipeline
Revises: 20260707_records
Create Date: 2026-07-07 17:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260707_pipeline"
down_revision = "20260707_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_storage",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("storage_ref", sa.String(length=256), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_storage")),
    )
    op.create_index(op.f("ix_document_storage_patient_id"), "document_storage", ["patient_id"], unique=False)

    op.create_table(
        "extraction_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["document_storage.id"], name=op.f("fk_extraction_jobs_document_id_document_storage"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extraction_jobs")),
    )
    op.create_index(op.f("ix_extraction_jobs_patient_id"), "extraction_jobs", ["patient_id"], unique=False)
    op.create_index(op.f("ix_extraction_jobs_document_id"), "extraction_jobs", ["document_id"], unique=False)

    op.create_table(
        "extracted_fields",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_name", sa.String(length=128), nullable=False),
        sa.Column("raw_value", sa.String(length=512), nullable=False),
        sa.Column("normalized_value", sa.String(length=512), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("validation_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_page", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source_bbox", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="auto_approved"),
        sa.Column("corrected_value", sa.String(length=512), nullable=True),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("committed_by", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["extraction_jobs.id"], name=op.f("fk_extracted_fields_job_id_extraction_jobs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extracted_fields")),
    )
    op.create_index(op.f("ix_extracted_fields_job_id"), "extracted_fields", ["job_id"], unique=False)
    op.create_index("ix_extracted_fields_job_status", "extracted_fields", ["job_id", "status"], unique=False)

    op.create_table(
        "pipeline_commits",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_by", sa.String(length=64), nullable=True),
        sa.Column("ingested_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("job_id", name=op.f("pk_pipeline_commits")),
    )
    op.create_index(op.f("ix_pipeline_commits_job_id"), "pipeline_commits", ["job_id"], unique=True)
    op.create_index(op.f("ix_pipeline_commits_patient_id"), "pipeline_commits", ["patient_id"], unique=False)

    op.create_table(
        "review_queue_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("adjudicated_by", sa.String(length=64), nullable=True),
        sa.Column("adjudicated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(["field_id"], ["extracted_fields.id"], name=op.f("fk_review_queue_items_field_id_extracted_fields"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_queue_items")),
    )
    op.create_index(op.f("ix_review_queue_items_job_id"), "review_queue_items", ["job_id"], unique=False)
    op.create_index(op.f("ix_review_queue_items_field_id"), "review_queue_items", ["field_id"], unique=False)
    op.create_index(op.f("ix_review_queue_items_patient_id"), "review_queue_items", ["patient_id"], unique=False)
    op.create_index("ix_review_queue_items_patient_status", "review_queue_items", ["patient_id", "status"], unique=False)


def downgrade() -> None:
    op.drop_table("review_queue_items")
    op.drop_table("pipeline_commits")
    op.drop_table("extracted_fields")
    op.drop_table("extraction_jobs")
    op.drop_table("document_storage")
