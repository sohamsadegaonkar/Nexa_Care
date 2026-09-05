"""Add durable provider verification work table, leasing, and indexes.

Revision ID: 20260906_verification_scheduler
Revises: 20260905_verification_application
Create Date: 2026-09-06 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260906_verification_scheduler"
down_revision: Union[str, None] = "20260905_verification_application"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "provider_verification_work",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "professional_verification_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "facility_verification_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("lookup_purpose", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("adapter_version", sa.String(64), nullable=False),
        sa.Column("registration_authority_code", sa.String(64), nullable=False),
        sa.Column("registration_number_normalized", sa.String(128), nullable=False),
        sa.Column("expected_resource_version", sa.Integer(), nullable=False),
        sa.Column("scheduler_reason", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default="100",
        ),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            nullable=False,
            server_default="5",
        ),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column(
            "result_evidence_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["professional_verification_id"],
            ["professional_verification.id"],
            name="fk_pvw_professional_verification_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["facility_verification_id"],
            ["facility_verification.id"],
            name="fk_pvw_facility_verification_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["result_evidence_id"],
            ["provider_trust_verification_evidence.id"],
            name="fk_pvw_result_evidence_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "(professional_verification_id IS NOT NULL AND facility_verification_id IS NULL) "
            "OR (professional_verification_id IS NULL AND facility_verification_id IS NOT NULL)",
            name="ck_pvw_resource_target_xor",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'CLAIMED', 'COMPLETED', 'EXHAUSTED', "
            "'CANCELLED_STALE', 'CANCELLED_POLICY', 'FAILED_TERMINAL')",
            name="ck_pvw_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1",
            name="ck_pvw_attempts",
        ),
        sa.CheckConstraint(
            "length(trim(source_id)) > 0",
            name="ck_pvw_source_id_non_empty",
        ),
        sa.CheckConstraint(
            "length(trim(adapter_version)) > 0",
            name="ck_pvw_adapter_version_non_empty",
        ),
        sa.CheckConstraint(
            "expected_resource_version >= 1",
            name="ck_pvw_expected_version_positive",
        ),
    )

    op.create_index(
        "uq_prof_active_verification_work",
        "provider_verification_work",
        [
            "professional_verification_id",
            "lookup_purpose",
            "source_id",
            "expected_resource_version",
        ],
        unique=True,
        postgresql_where=sa.text(
            "professional_verification_id IS NOT NULL AND status IN ('PENDING', 'CLAIMED')"
        ),
    )
    op.create_index(
        "uq_fac_active_verification_work",
        "provider_verification_work",
        [
            "facility_verification_id",
            "lookup_purpose",
            "source_id",
            "expected_resource_version",
        ],
        unique=True,
        postgresql_where=sa.text(
            "facility_verification_id IS NOT NULL AND status IN ('PENDING', 'CLAIMED')"
        ),
    )
    op.create_index(
        "ix_provider_verification_work_status_next_attempt",
        "provider_verification_work",
        ["status", "next_attempt_at", "priority"],
    )
    op.create_index(
        "ix_provider_verification_work_prof_id",
        "provider_verification_work",
        ["professional_verification_id"],
    )
    op.create_index(
        "ix_provider_verification_work_fac_id",
        "provider_verification_work",
        ["facility_verification_id"],
    )
    op.create_index(
        "ix_provider_verification_work_lease_expires",
        "provider_verification_work",
        ["lease_expires_at"],
    )
    op.create_index(
        "ix_provider_verification_work_result_evidence",
        "provider_verification_work",
        ["result_evidence_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_provider_verification_work_result_evidence",
        table_name="provider_verification_work",
    )
    op.drop_index(
        "ix_provider_verification_work_lease_expires",
        table_name="provider_verification_work",
    )
    op.drop_index(
        "ix_provider_verification_work_fac_id",
        table_name="provider_verification_work",
    )
    op.drop_index(
        "ix_provider_verification_work_prof_id",
        table_name="provider_verification_work",
    )
    op.drop_index(
        "ix_provider_verification_work_status_next_attempt",
        table_name="provider_verification_work",
    )
    op.drop_index(
        "uq_fac_active_verification_work",
        table_name="provider_verification_work",
    )
    op.drop_index(
        "uq_prof_active_verification_work",
        table_name="provider_verification_work",
    )
    op.drop_table("provider_verification_work")
