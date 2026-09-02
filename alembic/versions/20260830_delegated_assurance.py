"""Persist non-secret initiation assurance for delegated extraction jobs.

Legacy extraction jobs remain readable but cannot satisfy the new delegated
clinical-work gate because their assurance columns are null. New jobs require
the complete non-secret provenance tuple. This is forward-only: do not erase
trust/audit provenance in a downgrade.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260830_delegated_assurance"
down_revision = "20260830_provider_trust"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extraction_jobs",
        sa.Column(
            "authorization_initiated_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "extraction_jobs",
        sa.Column("authorization_authentication_method", sa.String(32), nullable=True),
    )
    op.add_column(
        "extraction_jobs",
        sa.Column(
            "authorization_mfa_verified_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "extraction_jobs",
        sa.Column(
            "authorization_assurance_policy_version", sa.String(64), nullable=True
        ),
    )
    op.create_check_constraint(
        "ck_extraction_jobs_delegated_assurance_complete",
        "extraction_jobs",
        "(authorization_initiated_at IS NULL "
        "AND authorization_authentication_method IS NULL "
        "AND authorization_mfa_verified_at IS NULL "
        "AND authorization_assurance_policy_version IS NULL) "
        "OR (authorization_initiated_at IS NOT NULL "
        "AND authorization_authentication_method IS NOT NULL "
        "AND authorization_mfa_verified_at IS NOT NULL "
        "AND authorization_assurance_policy_version IS NOT NULL)",
    )


def downgrade() -> None:
    raise RuntimeError(
        "20260830_delegated_assurance is forward-only; apply a corrective forward migration instead."
    )
