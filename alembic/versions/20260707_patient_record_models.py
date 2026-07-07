"""add_patient_record_models

Revision ID: 20260707_records
Revises: 20260707_device_keys
Create Date: 2026-07-07 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260707_records"
down_revision: Union[str, None] = "20260707_device_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. patient_records
    op.create_table(
        "patient_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("patient_id"),
    )
    op.create_index(op.f("ix_patient_records_patient_id"), "patient_records", ["patient_id"], unique=True)

    # 2. patient_vitals
    op.create_table(
        "patient_vitals",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("value", sa.String(length=64), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=20), server_default="manual", nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("risk_level", sa.String(length=20), server_default="LOW_RISK", nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("source != 'ai_extracted' OR (confidence IS NOT NULL AND risk_level IS NOT NULL AND source_document_id IS NOT NULL)", name="ck_patient_vitals_provenance_complete"),
    )
    op.create_index(op.f("ix_patient_vitals_patient_id"), "patient_vitals", ["patient_id"], unique=False)
    op.create_index("ix_patient_vitals_patient_recorded", "patient_vitals", ["patient_id", "recorded_at"], unique=False)

    # 3. patient_medications
    op.create_table(
        "patient_medications",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("strength", sa.String(length=64), nullable=False),
        sa.Column("frequency", sa.String(length=64), nullable=False),
        sa.Column("prescribed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=20), server_default="manual", nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("risk_level", sa.String(length=20), server_default="MEDIUM_RISK", nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("source != 'ai_extracted' OR (confidence IS NOT NULL AND risk_level IS NOT NULL AND source_document_id IS NOT NULL)", name="ck_patient_medications_provenance_complete"),
    )
    op.create_index(op.f("ix_patient_medications_patient_id"), "patient_medications", ["patient_id"], unique=False)

    # 4. patient_lab_results
    op.create_table(
        "patient_lab_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("test_name", sa.String(length=128), nullable=False),
        sa.Column("value", sa.String(length=64), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("reference_range", sa.String(length=64), nullable=False),
        sa.Column("is_abnormal", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=20), server_default="manual", nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("risk_level", sa.String(length=20), server_default="MEDIUM_RISK", nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("source != 'ai_extracted' OR (confidence IS NOT NULL AND risk_level IS NOT NULL AND source_document_id IS NOT NULL)", name="ck_patient_lab_results_provenance_complete"),
    )
    op.create_index(op.f("ix_patient_lab_results_patient_id"), "patient_lab_results", ["patient_id"], unique=False)
    op.create_index("ix_patient_lab_results_patient_recorded", "patient_lab_results", ["patient_id", "recorded_at"], unique=False)

    # 5. patient_allergies
    op.create_table(
        "patient_allergies",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("allergen", sa.String(length=128), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=20), server_default="manual", nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("risk_level", sa.String(length=20), server_default="HIGH_RISK", nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("source != 'ai_extracted' OR (confidence IS NOT NULL AND risk_level IS NOT NULL AND source_document_id IS NOT NULL)", name="ck_patient_allergies_provenance_complete"),
    )
    op.create_index(op.f("ix_patient_allergies_patient_id"), "patient_allergies", ["patient_id"], unique=False)

    # 6. document_references
    op.create_table(
        "document_references",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_type", sa.String(length=64), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("storage_ref", sa.String(length=256), nullable=False),
        sa.Column("extraction_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_document_references_patient_id"), "document_references", ["patient_id"], unique=False)
    op.create_index(op.f("ix_document_references_extraction_job_id"), "document_references", ["extraction_job_id"], unique=False)

    # 7. timeline_events
    op.create_table(
        "timeline_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("event_ref_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=20), server_default="manual", nullable=False),
        sa.Column("summary", sa.String(length=512), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_timeline_events_patient_id"), "timeline_events", ["patient_id"], unique=False)
    op.create_index("ix_timeline_events_patient_occurred", "timeline_events", ["patient_id", "occurred_at"], unique=False)


def downgrade() -> None:
    op.drop_table("timeline_events")
    op.drop_table("document_references")
    op.drop_table("patient_allergies")
    op.drop_table("patient_lab_results")
    op.drop_table("patient_medications")
    op.drop_table("patient_vitals")
    op.drop_table("patient_records")
