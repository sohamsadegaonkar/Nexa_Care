"""Persist authoritative provider email and phone verification challenges.

This forward-only migration adds only provider self-contact assurance state.
It does not alter clinical eligibility, professional/facility verification,
affiliation trust, or capabilities.  Existing providers remain unverified.

Preconditions: upgrade from the single 20260830_delegated_assurance head.
Locking: CREATE TABLE and indexes take ordinary PostgreSQL DDL locks; deploy in
the approved migration release task.  Validation: inspect this table plus
provider_identity.email_verified_at/phone_verified_at after upgrade.  Rollback
is deliberately forbidden; any production correction must be forward-only.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260902_contact_assurance"
down_revision = "20260830_delegated_assurance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_contact_verification_challenge",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "provider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("provider_identity.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(length=8), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("contact_binding_hmac", sa.String(length=64), nullable=False),
        sa.Column("verifier_hmac", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column(
            "failed_attempt_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "channel IN ('EMAIL', 'PHONE')",
            name="ck_provider_contact_challenge_channel",
        ),
        sa.CheckConstraint(
            "max_attempts > 0 AND failed_attempt_count >= 0 "
            "AND failed_attempt_count <= max_attempts",
            name="ck_provider_contact_challenge_attempts",
        ),
        sa.CheckConstraint(
            "(succeeded_at IS NULL) OR (consumed_at IS NOT NULL)",
            name="ck_provider_contact_challenge_success_consumed",
        ),
    )
    op.create_index(
        "ix_provider_contact_challenge_provider_channel_purpose",
        "provider_contact_verification_challenge",
        ["provider_id", "channel", "purpose"],
    )
    op.create_index(
        "ix_provider_contact_challenge_expires_at",
        "provider_contact_verification_challenge",
        ["expires_at"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "20260902_contact_assurance is forward-only; apply a corrective forward migration instead."
    )
