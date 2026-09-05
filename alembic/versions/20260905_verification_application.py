"""Add server provenance evidence binding and verification review work queue.

Revision ID: 20260905_verification_application
Revises: 20260904_verification_evidence
Create Date: 2026-09-05 02:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260905_verification_application"
down_revision: Union[str, None] = "20260904_verification_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 0. Widen alembic_version.version_num to support revision identifiers up to 64 chars
    op.alter_column(
        "alembic_version",
        "version_num",
        type_=sa.String(64),
        existing_type=sa.String(32),
    )

    # 1. Add unique constraints on provider_trust_verification_evidence for composite FK targets
    op.create_unique_constraint(
        "uq_evidence_professional_binding",
        "provider_trust_verification_evidence",
        ["id", "professional_verification_id"],
    )
    op.create_unique_constraint(
        "uq_evidence_facility_binding",
        "provider_trust_verification_evidence",
        ["id", "facility_verification_id"],
    )

    # 2. Add composite server_provenance_evidence_id column and foreign key to professional_verification
    op.add_column(
        "professional_verification",
        sa.Column(
            "server_provenance_evidence_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_professional_verification_server_provenance_evidence_id",
        "professional_verification",
        ["server_provenance_evidence_id"],
    )
    op.create_foreign_key(
        "fk_professional_verification_server_provenance",
        "professional_verification",
        "provider_trust_verification_evidence",
        ["server_provenance_evidence_id", "id"],
        ["id", "professional_verification_id"],
        ondelete="RESTRICT",
    )

    # 3. Add composite server_provenance_evidence_id column and foreign key to facility_verification
    op.add_column(
        "facility_verification",
        sa.Column(
            "server_provenance_evidence_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_facility_verification_server_provenance_evidence_id",
        "facility_verification",
        ["server_provenance_evidence_id"],
    )
    op.create_foreign_key(
        "fk_facility_verification_server_provenance",
        "facility_verification",
        "provider_trust_verification_evidence",
        ["server_provenance_evidence_id", "id"],
        ["id", "facility_verification_id"],
        ondelete="RESTRICT",
    )

    # 4. Create provider_trust_verification_review_work table
    op.create_table(
        "provider_trust_verification_review_work",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("disposition", sa.String(64), nullable=False),
        sa.Column("reason_code", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="OPEN"),
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
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_actor_id", sa.String(128), nullable=True),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["provider_trust_verification_evidence.id"],
            name="fk_review_work_evidence_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("evidence_id", name="uq_verification_review_work_evidence"),
        sa.CheckConstraint(
            "status IN ('OPEN', 'RESOLVED')",
            name="chk_review_work_status",
        ),
        sa.CheckConstraint(
            "disposition IN ('HUMAN_REVIEW_REQUIRED', 'SYSTEM_FAIL_CLOSED_AND_REVIEW', 'LIFECYCLE_SEMANTIC_GAP')",
            name="chk_review_work_disposition",
        ),
        sa.CheckConstraint(
            "length(trim(reason_code)) > 0",
            name="chk_review_work_reason_code_non_empty",
        ),
        sa.CheckConstraint(
            "(status = 'OPEN' AND resolved_at IS NULL AND resolved_by_actor_id IS NULL) "
            "OR (status = 'RESOLVED' AND resolved_at IS NOT NULL AND resolved_by_actor_id IS NOT NULL)",
            name="chk_review_work_resolution_integrity",
        ),
    )
    op.create_index(
        "ix_provider_trust_verification_review_work_status",
        "provider_trust_verification_review_work",
        ["status"],
    )
    op.create_index(
        "ix_provider_trust_verification_review_work_evidence_id",
        "provider_trust_verification_review_work",
        ["evidence_id"],
    )


def downgrade() -> None:
    # 1. Drop provider_trust_verification_review_work table
    op.drop_index(
        "ix_provider_trust_verification_review_work_evidence_id",
        table_name="provider_trust_verification_review_work",
    )
    op.drop_index(
        "ix_provider_trust_verification_review_work_status",
        table_name="provider_trust_verification_review_work",
    )
    op.drop_table("provider_trust_verification_review_work")

    # 2. Drop facility_verification FK and column
    op.drop_constraint(
        "fk_facility_verification_server_provenance",
        "facility_verification",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_facility_verification_server_provenance_evidence_id",
        table_name="facility_verification",
    )
    op.drop_column("facility_verification", "server_provenance_evidence_id")

    # 3. Drop professional_verification FK and column
    op.drop_constraint(
        "fk_professional_verification_server_provenance",
        "professional_verification",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_professional_verification_server_provenance_evidence_id",
        table_name="professional_verification",
    )
    op.drop_column("professional_verification", "server_provenance_evidence_id")

    # 4. Drop unique constraints on provider_trust_verification_evidence
    op.drop_constraint(
        "uq_evidence_facility_binding",
        "provider_trust_verification_evidence",
        type_="unique",
    )
    op.drop_constraint(
        "uq_evidence_professional_binding",
        "provider_trust_verification_evidence",
        type_="unique",
    )
