"""Add durable document-processing authorization bindings.

Revision ID: 20260727_doc_process_bind
Revises: 20260721_policy_audit_types
"""

from alembic import op
import sqlalchemy as sa

revision = "20260727_doc_process_bind"
down_revision = "20260721_policy_audit_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "extracted_fields",
        "source_page",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.add_column(
        "extraction_jobs",
        sa.Column("authorization_provider_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "extraction_jobs",
        sa.Column("consent_request_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_extraction_jobs_authorization_provider_id",
        "extraction_jobs",
        ["authorization_provider_id"],
    )
    op.create_index(
        "ix_extraction_jobs_consent_request_id",
        "extraction_jobs",
        ["consent_request_id"],
    )
    op.create_index(
        "ix_extraction_jobs_authorization_binding",
        "extraction_jobs",
        [
            "patient_id",
            "tenant_id",
            "authorization_provider_id",
            "consent_request_id",
        ],
    )


def downgrade() -> None:
    op.execute("UPDATE extracted_fields SET source_page = 1 WHERE source_page IS NULL")
    op.alter_column(
        "extracted_fields",
        "source_page",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.drop_index(
        "ix_extraction_jobs_authorization_binding", table_name="extraction_jobs"
    )
    op.drop_index("ix_extraction_jobs_consent_request_id", table_name="extraction_jobs")
    op.drop_index(
        "ix_extraction_jobs_authorization_provider_id",
        table_name="extraction_jobs",
    )
    op.drop_column("extraction_jobs", "consent_request_id")
    op.drop_column("extraction_jobs", "authorization_provider_id")
