"""secure patient-bound document pipeline metadata

Revision ID: 20260717_secure_document_pipeline
Revises: 20260717_provider_pwd_canonical
Create Date: 2026-07-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260717_secure_document_pipeline"
down_revision = "20260717_provider_pwd_canonical"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("patient_dek_store", sa.Column("wrapping_backend", sa.String(32), nullable=False, server_default="local-aes-gcm"))
    op.add_column("consent_grant_log", sa.Column("hospital_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_consent_grant_log_hospital_id", "consent_grant_log", ["hospital_id"])
    op.create_foreign_key("fk_consent_grant_log_hospital", "consent_grant_log", "hospital_registry", ["hospital_id"], ["id"])
    op.add_column("document_storage", sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("document_storage", sa.Column("uploader_id", sa.String(64), nullable=True))
    op.add_column("document_storage", sa.Column("content_hash", sa.String(64), nullable=True))
    op.add_column("document_storage", sa.Column("original_filename", sa.String(255), nullable=True))
    op.add_column("document_storage", sa.Column("upload_purpose", sa.String(64), nullable=True))
    op.add_column("document_storage", sa.Column("consent_session_id", sa.String(64), nullable=True))
    op.add_column("document_storage", sa.Column("source_system", sa.String(64), nullable=True))
    op.create_index("ix_document_storage_tenant_id", "document_storage", ["tenant_id"])
    op.create_index("ix_document_storage_hash", "document_storage", ["content_hash"])
    op.create_unique_constraint("uq_document_tenant_patient_hash", "document_storage", ["tenant_id", "patient_id", "content_hash"])
    op.create_foreign_key("fk_document_storage_tenant", "document_storage", "hospital_registry", ["tenant_id"], ["id"])

    op.add_column("extraction_jobs", sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("extraction_jobs", sa.Column("uploader_id", sa.String(64), nullable=True))
    op.add_column("extraction_jobs", sa.Column("request_id", sa.String(64), nullable=True))
    op.add_column("extraction_jobs", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("extraction_jobs", sa.Column("error_code", sa.String(64), nullable=True))
    op.add_column("extraction_jobs", sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("extraction_jobs", sa.Column("extractor_provider", sa.String(32), nullable=True))
    op.add_column("extraction_jobs", sa.Column("extractor_version", sa.String(64), nullable=True))
    op.add_column("extraction_jobs", sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("extraction_jobs", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.create_index("ix_extraction_jobs_tenant_id", "extraction_jobs", ["tenant_id"])
    op.create_unique_constraint("uq_extraction_jobs_request_id", "extraction_jobs", ["request_id"])
    op.create_foreign_key("fk_extraction_jobs_tenant", "extraction_jobs", "hospital_registry", ["tenant_id"], ["id"])

    op.add_column("extracted_fields", sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("extracted_fields", sa.Column("units", sa.String(64), nullable=True))
    op.add_column("extracted_fields", sa.Column("review_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("extracted_fields", sa.Column("extractor_provider", sa.String(32), nullable=True))
    op.add_column("extracted_fields", sa.Column("extractor_version", sa.String(64), nullable=True))
    op.create_index("ix_extracted_fields_patient_id", "extracted_fields", ["patient_id"])


def downgrade() -> None:
    op.drop_index("ix_extracted_fields_patient_id", table_name="extracted_fields")
    for column in ("extractor_version", "extractor_provider", "review_version", "units", "patient_id"):
        op.drop_column("extracted_fields", column)
    op.drop_constraint("fk_extraction_jobs_tenant", "extraction_jobs", type_="foreignkey")
    op.drop_constraint("uq_extraction_jobs_request_id", "extraction_jobs", type_="unique")
    op.drop_index("ix_extraction_jobs_tenant_id", table_name="extraction_jobs")
    for column in ("processing_started_at", "version", "extractor_version", "extractor_provider", "retryable", "error_code", "attempt_count", "request_id", "uploader_id", "tenant_id"):
        op.drop_column("extraction_jobs", column)
    op.drop_constraint("fk_document_storage_tenant", "document_storage", type_="foreignkey")
    op.drop_constraint("uq_document_tenant_patient_hash", "document_storage", type_="unique")
    op.drop_index("ix_document_storage_hash", table_name="document_storage")
    op.drop_index("ix_document_storage_tenant_id", table_name="document_storage")
    for column in ("source_system", "consent_session_id", "upload_purpose", "original_filename", "content_hash", "uploader_id", "tenant_id"):
        op.drop_column("document_storage", column)
    op.drop_column("patient_dek_store", "wrapping_backend")
    op.drop_constraint("fk_consent_grant_log_hospital", "consent_grant_log", type_="foreignkey")
    op.drop_index("ix_consent_grant_log_hospital_id", table_name="consent_grant_log")
    op.drop_column("consent_grant_log", "hospital_id")
