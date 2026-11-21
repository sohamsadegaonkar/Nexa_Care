"""Separate, single-document patient decisions. Existing history grants are untouched."""

import sqlalchemy as sa
from alembic import op

revision = "20261120_document_access"
down_revision = "20260919_medication_catalog"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "original_document_access",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "patient_id",
            sa.Uuid(),
            sa.ForeignKey("patients.patient_uuid", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("document_references.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("hospital_id", sa.Uuid(), nullable=False),
        sa.Column(
            "affiliation_id",
            sa.Uuid(),
            sa.ForeignKey("provider_hospital_affiliation.id"),
            nullable=False,
        ),
        sa.Column(
            "history_id",
            sa.Uuid(),
            sa.ForeignKey("clinical_access_sessions.session_id"),
            nullable=False,
        ),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("nonce", sa.String(64), nullable=False, unique=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("key_id", sa.Uuid()),
        sa.Column("signature", sa.LargeBinary()),
        sa.CheckConstraint(
            "status IN ('pending','approved','denied','opened','revoked')"
        ),
        sa.CheckConstraint("expires_at > issued_at"),
        sa.CheckConstraint(
            "status NOT IN ('approved','denied','opened') OR (key_id IS NOT NULL AND signature IS NOT NULL)"
        ),
    )
    op.create_index(
        "ix_original_access_patient_time",
        "original_document_access",
        ["patient_id", "issued_at"],
    )


def downgrade():
    raise RuntimeError(
        "Forward-only: retain signed patient decisions and audit history"
    )
