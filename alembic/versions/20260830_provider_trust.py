"""Add fail-closed provider, facility, and affiliation trust primitives.

Purpose: introduce independently reviewable professional/facility trust and an
explicit affiliation lifecycle without changing existing route authorization.
Preconditions: database is at 20260827_patient_public_id.
Existing-data behavior: every legacy provider receives NOT_SUBMITTED trust,
every legacy facility receives DRAFT trust, and every affiliation becomes
PENDING_ACTIVATION. Legacy registration numbers and is_active remain intact
but cannot establish clinical trust.
Locking risk: short metadata locks plus bounded INSERT...SELECT backfills over
provider, facility, and affiliation rows.
Rollback position: forward-only after application; trust-state history must not
be erased by downgrade.
Validation query: verify one trust row per provider/facility and that no
legacy affiliation is ACTIVE after upgrade.
Forward-fix strategy: add a corrective forward revision; do not rewrite this
revision after it has been applied.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260830_provider_trust"
down_revision = "20260827_patient_public_id"
branch_labels = None
depends_on = None


_PROFESSIONAL_STATUSES = "'NOT_SUBMITTED','PENDING_REVIEW','VERIFIED','RECHECK_DUE','VERIFICATION_STALE','SUSPENDED','REJECTED','REVOKED','EXPIRED'"
_FACILITY_STATUSES = "'DRAFT','PENDING_VERIFICATION','VERIFIED','RECHECK_REQUIRED','SUSPENDED','REJECTED','CLOSED'"
_AFFILIATION_STATUSES = (
    "'PENDING_ACTIVATION','ACTIVE','SUSPENDED','REVOKED','EXPIRED','LEFT'"
)


def upgrade() -> None:
    op.add_column(
        "provider_identity",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "provider_identity",
        sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "hospital_registry", sa.Column("facility_type", sa.String(64), nullable=True)
    )
    op.add_column(
        "provider_hospital_affiliation",
        sa.Column(
            "trust_status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'PENDING_ACTIVATION'"),
        ),
    )
    op.create_check_constraint(
        "ck_provider_hospital_affiliation_trust_status",
        "provider_hospital_affiliation",
        f"trust_status IN ({_AFFILIATION_STATUSES})",
    )

    op.create_table(
        "professional_verification",
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
            unique=True,
        ),
        sa.Column("registration_authority_code", sa.String(64), nullable=True),
        sa.Column("registration_number_normalized", sa.String(128), nullable=True),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'NOT_SUBMITTED'"),
        ),
        sa.Column("verification_method", sa.String(64), nullable=True),
        sa.Column("verification_source", sa.String(128), nullable=True),
        sa.Column("verification_reference", sa.String(128), nullable=True),
        sa.Column("identity_binding_method", sa.String(64), nullable=True),
        sa.Column("identity_binding_status", sa.String(32), nullable=True),
        sa.Column("registration_valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "registration_valid_until", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grace_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recheck_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recheck_failure_reason", sa.String(64), nullable=True),
        sa.Column(
            "previous_verification_valid",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "authoritative_adverse_signal_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("reviewer_id", sa.String(128), nullable=True),
        sa.Column("decision_reason_code", sa.String(64), nullable=True),
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
        sa.UniqueConstraint(
            "registration_authority_code",
            "registration_number_normalized",
            name="uq_professional_verification_authority_registration",
        ),
        sa.CheckConstraint(
            f"status IN ({_PROFESSIONAL_STATUSES})",
            name="ck_professional_verification_status",
        ),
    )
    op.create_index(
        "ix_professional_verification_status", "professional_verification", ["status"]
    )
    op.create_index(
        "ix_professional_verification_provider_id",
        "professional_verification",
        ["provider_id"],
    )

    op.create_table(
        "facility_verification",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "facility_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hospital_registry.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default=sa.text("'DRAFT'")
        ),
        sa.Column("verification_method", sa.String(64), nullable=True),
        sa.Column("verification_source", sa.String(128), nullable=True),
        sa.Column("verification_reference", sa.String(128), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewer_id", sa.String(128), nullable=True),
        sa.Column("decision_reason_code", sa.String(64), nullable=True),
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
            f"status IN ({_FACILITY_STATUSES})",
            name="ck_facility_verification_status",
        ),
    )
    op.create_index(
        "ix_facility_verification_status", "facility_verification", ["status"]
    )
    op.create_index(
        "ix_facility_verification_facility_id", "facility_verification", ["facility_id"]
    )

    # Fail closed: preserving legacy rows does not activate clinical trust.
    op.execute(
        """
        INSERT INTO professional_verification (id, provider_id, status, previous_verification_valid, created_at, updated_at)
        SELECT gen_random_uuid(), id, 'NOT_SUBMITTED', FALSE, now(), now()
        FROM provider_identity
        WHERE NOT EXISTS (
            SELECT 1 FROM professional_verification pv WHERE pv.provider_id = provider_identity.id
        )
        """
    )
    op.execute(
        """
        INSERT INTO facility_verification (id, facility_id, status, created_at, updated_at)
        SELECT gen_random_uuid(), id, 'DRAFT', now(), now()
        FROM hospital_registry
        WHERE NOT EXISTS (
            SELECT 1 FROM facility_verification fv WHERE fv.facility_id = hospital_registry.id
        )
        """
    )
    op.alter_column(
        "provider_hospital_affiliation", "trust_status", server_default=None
    )
    op.alter_column("professional_verification", "status", server_default=None)
    op.alter_column(
        "professional_verification", "previous_verification_valid", server_default=None
    )
    op.alter_column("facility_verification", "status", server_default=None)


def downgrade() -> None:
    raise RuntimeError(
        "20260830_provider_trust is forward-only; apply a corrective forward migration instead."
    )
