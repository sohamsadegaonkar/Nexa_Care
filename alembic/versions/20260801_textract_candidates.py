"""Add encrypted, authorization-bound Textract candidate staging.

Revision ID: 20260801_textract_candidates
Revises: 20260731_adjudication_harden

Purpose: retain provider candidates only for authorized result display and
clinician source adjudication. Candidate values and source text are encrypted
with the patient's DEK; routing and audit projections remain PHI-free.
Lifecycle: rows cascade with the owning extraction job/source document and do
not extend the approved source-document retention period.
Preconditions: the Milestone 4.1 adjudication head is applied.
Existing-data behavior: no existing rows are rewritten or backfilled.
Locking risk: ordinary PostgreSQL DDL locks while creating one empty table.
Rollback: drops only the new staging table and its indexes.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260801_textract_candidates"
down_revision = "20260731_adjudication_harden"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_candidates",
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("authorization_provider_id", sa.String(length=64), nullable=False),
        sa.Column("field_name", sa.String(length=128), nullable=False),
        sa.Column("encrypted_raw_value", sa.Text(), nullable=False),
        sa.Column("encrypted_source_text", sa.Text(), nullable=True),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("source_bbox", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("field_confidence", sa.Float(), nullable=True),
        sa.Column("document_confidence", sa.Float(), nullable=True),
        sa.Column("provider_name", sa.String(length=32), nullable=False),
        sa.Column("provider_version", sa.String(length=64), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_complete", sa.Boolean(), nullable=False),
        sa.Column("lane", sa.String(length=24), nullable=False),
        sa.Column(
            "reason_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "lane IN ('SOURCE_ONLY', 'QUARANTINE')",
            name="ck_extraction_candidates_safe_lane",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["extraction_jobs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["document_storage.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evidence_id"),
    )
    op.create_index(
        "ix_extraction_candidates_authorization_binding",
        "extraction_candidates",
        ["tenant_id", "patient_id", "authorization_provider_id", "job_id"],
        unique=False,
    )
    op.create_index(
        "ix_extraction_candidates_document",
        "extraction_candidates",
        ["source_document_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_extraction_candidates_document",
        table_name="extraction_candidates",
    )
    op.drop_index(
        "ix_extraction_candidates_authorization_binding",
        table_name="extraction_candidates",
    )
    op.drop_table("extraction_candidates")
