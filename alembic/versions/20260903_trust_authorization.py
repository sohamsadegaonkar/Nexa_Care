"""Add explicit organizational trust-management permission grants.

Purpose: persist server-owned organizational trust permissions independently
from clinical capabilities, affiliation roles, and provider identity roles.
Preconditions: database is at the sole 20260903_trust_lifecycle head.
Existing-data behavior: this migration inserts no grants; every existing
provider retains exactly their prior authority.
Expired but non-revoked grants remain historical rows and retain their partial
unique-index slot; a future re-grant flow must explicitly revoke or supersede
them rather than assuming temporal expiry permits an implicit replacement.
Locking risk: table, indexes, and constraints take ordinary PostgreSQL DDL
locks; run only through the dedicated release migration task.
Rollback position: forward-only; retain grant history and use a corrective
forward migration for any production repair.
Validation query: SELECT count(*) FROM provider_trust_permission_grant must
be zero immediately after upgrade of an existing database.
Forward-fix strategy: never rewrite this migration after application.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260903_trust_authorization"
down_revision = "20260903_trust_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_trust_permission_grant",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "provider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("provider_identity.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("permission", sa.String(64), nullable=False),
        sa.Column("scope_type", sa.String(16), nullable=False),
        sa.Column(
            "facility_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hospital_registry.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("granted_by_actor_id", sa.String(128), nullable=False),
        sa.Column("governance_reference", sa.String(128), nullable=True),
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
        sa.CheckConstraint(
            "permission IN ('PROFESSIONAL_REVIEW', 'FACILITY_REVIEW', 'AFFILIATION_MANAGE', 'TRUST_PERMISSION_MANAGE')",
            name="ck_provider_trust_permission_grant_permission",
        ),
        sa.CheckConstraint(
            "scope_type IN ('GLOBAL', 'FACILITY')",
            name="ck_provider_trust_permission_grant_scope_type",
        ),
        sa.CheckConstraint(
            "(permission IN ('PROFESSIONAL_REVIEW', 'TRUST_PERMISSION_MANAGE') AND scope_type = 'GLOBAL' AND facility_id IS NULL) OR (permission IN ('FACILITY_REVIEW', 'AFFILIATION_MANAGE') AND scope_type = 'FACILITY' AND facility_id IS NOT NULL)",
            name="ck_provider_trust_permission_grant_scope_binding",
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until > valid_from",
            name="ck_provider_trust_permission_grant_validity",
        ),
    )
    op.create_index(
        "ix_provider_trust_permission_grant_provider_id",
        "provider_trust_permission_grant",
        ["provider_id"],
    )
    op.create_index(
        "ix_provider_trust_permission_grant_facility_id",
        "provider_trust_permission_grant",
        ["facility_id"],
    )
    op.create_index(
        "uq_provider_trust_permission_grant_global_active",
        "provider_trust_permission_grant",
        ["provider_id", "permission"],
        unique=True,
        postgresql_where=sa.text("scope_type = 'GLOBAL' AND revoked_at IS NULL"),
    )
    op.create_index(
        "uq_provider_trust_permission_grant_facility_active",
        "provider_trust_permission_grant",
        ["provider_id", "permission", "facility_id"],
        unique=True,
        postgresql_where=sa.text("scope_type = 'FACILITY' AND revoked_at IS NULL"),
    )


def downgrade() -> None:
    raise RuntimeError(
        "20260903_trust_authorization is forward-only; apply a corrective forward migration instead."
    )
