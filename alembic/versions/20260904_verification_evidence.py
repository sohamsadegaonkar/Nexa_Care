"""Add verification evidence history and facility trust schema foundations.

Purpose:
Introduce the immutable append-only verification evidence table
(``provider_trust_verification_evidence``) to record external registry lookup
and manual reviewer observations independently from current lifecycle state.
Extend ``facility_verification`` with external registration, validity, and
recheck/failure tracking columns matching the professional trust schema.

Preconditions:
Database is at sole head ``20260903_trust_authorization``.

Existing-data behavior:
Existing ``facility_verification`` rows receive ``previous_verification_valid = FALSE``
and ``NULL`` for all new nullable fields.  Zero evidence rows are created or backfilled,
preserving true provenance history.  Existing ``professional_verification`` rows survive
intact with one effective row per provider.

Locking risk:
Standard PostgreSQL DDL metadata locks for adding nullable columns, check constraints,
table creation, indexes, and trigger definition.

Rollback position:
Deterministic downgrade drops trigger, function, table, indexes, and added columns
for qualification in disposable environments.

Validation query:
SELECT count(*) FROM provider_trust_verification_evidence; must be 0 immediately
after upgrade.

Forward-fix strategy:
Forward revisions must build on this schema without modifying applied migrations.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260904_verification_evidence"
down_revision = "20260903_trust_authorization"
branch_labels = None
depends_on = None

_RECHECK_FAILURE_REASONS = "'SOURCE_UNAVAILABLE', 'SOURCE_RESPONSE_INVALID', 'SOURCE_NOT_FOUND', 'REVIEW_REQUIRED'"
_EVIDENCE_ORIGINS = "'MANUAL_REVIEWER_ATTESTATION', 'SERVER_REGISTRY_OBSERVATION'"
_LOOKUP_PURPOSES = (
    "'INITIAL_VERIFICATION', 'RECHECK', 'ADVERSE_SIGNAL_CHECK', 'MANUAL_REVIEW'"
)
_OUTCOMES = (
    "'CONFIRMED_ACTIVE', 'CONFIRMED_INACTIVE', 'NOT_FOUND', "
    "'IDENTITY_MISMATCH', 'AMBIGUOUS', 'SOURCE_UNAVAILABLE', "
    "'SOURCE_RESPONSE_INVALID', 'SOURCE_AUTHENTICATION_FAILURE', "
    "'SOURCE_INTEGRITY_FAILURE', 'REVIEW_REQUIRED'"
)
_IDENTITY_BINDING_RESULTS = "'NOT_EVALUATED', 'MATCHED', 'MISMATCHED', 'AMBIGUOUS'"


def upgrade() -> None:
    # 1. Extend facility_verification with authoritative external registration and recheck fields
    op.add_column(
        "facility_verification",
        sa.Column("registration_authority_code", sa.String(64), nullable=True),
    )
    op.add_column(
        "facility_verification",
        sa.Column("registration_number_normalized", sa.String(128), nullable=True),
    )
    op.add_column(
        "facility_verification",
        sa.Column("registration_valid_from", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "facility_verification",
        sa.Column(
            "registration_valid_until", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "facility_verification",
        sa.Column("grace_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "facility_verification",
        sa.Column("recheck_attempted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "facility_verification",
        sa.Column("recheck_failure_reason", sa.String(64), nullable=True),
    )
    op.add_column(
        "facility_verification",
        sa.Column(
            "previous_verification_valid",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "facility_verification",
        sa.Column(
            "authoritative_adverse_signal_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.create_check_constraint(
        "ck_facility_verification_validity",
        "facility_verification",
        "registration_valid_until IS NULL OR registration_valid_from IS NULL "
        "OR registration_valid_until >= registration_valid_from",
    )
    op.create_check_constraint(
        "ck_facility_verification_recheck_failure_reason",
        "facility_verification",
        f"recheck_failure_reason IS NULL OR recheck_failure_reason IN ({_RECHECK_FAILURE_REASONS})",
    )
    op.create_index(
        "ix_facility_verification_registration",
        "facility_verification",
        ["registration_authority_code", "registration_number_normalized"],
    )

    # 2. Create append-only provider_trust_verification_evidence table
    op.create_table(
        "provider_trust_verification_evidence",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "professional_verification_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("professional_verification.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "facility_verification_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("facility_verification.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("origin", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("adapter_version", sa.String(64), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lookup_purpose", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("source_record_reference", sa.String(255), nullable=True),
        sa.Column("observed_valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "identity_binding_result",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'NOT_EVALUATED'"),
        ),
        sa.Column("binding_method", sa.String(64), nullable=True),
        sa.Column("response_digest", sa.String(64), nullable=True),
        sa.Column("external_transaction_id", sa.String(128), nullable=True),
        sa.Column("observed_resource_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "(professional_verification_id IS NOT NULL AND facility_verification_id IS NULL) "
            "OR (professional_verification_id IS NULL AND facility_verification_id IS NOT NULL)",
            name="ck_provider_trust_verification_evidence_resource_target",
        ),
        sa.CheckConstraint(
            f"origin IN ({_EVIDENCE_ORIGINS})",
            name="ck_provider_trust_verification_evidence_origin",
        ),
        sa.CheckConstraint(
            f"lookup_purpose IN ({_LOOKUP_PURPOSES})",
            name="ck_provider_trust_verification_evidence_lookup_purpose",
        ),
        sa.CheckConstraint(
            f"outcome IN ({_OUTCOMES})",
            name="ck_provider_trust_verification_evidence_outcome",
        ),
        sa.CheckConstraint(
            f"identity_binding_result IN ({_IDENTITY_BINDING_RESULTS})",
            name="ck_provider_trust_verification_evidence_identity_binding_result",
        ),
        sa.CheckConstraint(
            "observed_resource_version >= 1",
            name="ck_ptve_observed_resource_version",
        ),
        sa.CheckConstraint(
            "(origin = 'SERVER_REGISTRY_OBSERVATION' AND adapter_version IS NOT NULL AND length(trim(adapter_version)) > 0) "
            "OR (origin = 'MANUAL_REVIEWER_ATTESTATION')",
            name="ck_provider_trust_verification_evidence_adapter_version_origin",
        ),
        sa.CheckConstraint(
            "observed_valid_until IS NULL OR observed_valid_from IS NULL OR observed_valid_until >= observed_valid_from",
            name="ck_provider_trust_verification_evidence_validity_interval",
        ),
        sa.CheckConstraint(
            "response_digest IS NULL OR response_digest ~ '^[0-9a-f]{64}$'",
            name="ck_provider_trust_verification_evidence_response_digest",
        ),
        sa.CheckConstraint(
            "length(trim(source_id)) > 0",
            name="ck_provider_trust_verification_evidence_source_id_non_empty",
        ),
    )

    op.create_index(
        "ix_provider_trust_verification_evidence_prof_id",
        "provider_trust_verification_evidence",
        ["professional_verification_id"],
    )
    op.create_index(
        "ix_provider_trust_verification_evidence_fac_id",
        "provider_trust_verification_evidence",
        ["facility_verification_id"],
    )
    op.create_index(
        "ix_provider_trust_verification_evidence_source_id",
        "provider_trust_verification_evidence",
        ["source_id"],
    )
    op.create_index(
        "ix_provider_trust_verification_evidence_observed_at",
        "provider_trust_verification_evidence",
        ["observed_at"],
    )
    op.create_index(
        "ix_provider_trust_verification_evidence_outcome",
        "provider_trust_verification_evidence",
        ["outcome"],
    )

    # 3. Install immutable trigger and function
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.nexa_provider_verification_evidence_immutable()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'PROVIDER_TRUST_VERIFICATION_EVIDENCE_IMMUTABLE';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_provider_trust_verification_evidence_immutable
        BEFORE UPDATE OR DELETE ON public.provider_trust_verification_evidence
        FOR EACH ROW
        EXECUTE FUNCTION public.nexa_provider_verification_evidence_immutable();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_provider_trust_verification_evidence_immutable "
        "ON public.provider_trust_verification_evidence"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.nexa_provider_verification_evidence_immutable()"
    )
    op.drop_index(
        "ix_provider_trust_verification_evidence_outcome",
        table_name="provider_trust_verification_evidence",
    )
    op.drop_index(
        "ix_provider_trust_verification_evidence_observed_at",
        table_name="provider_trust_verification_evidence",
    )
    op.drop_index(
        "ix_provider_trust_verification_evidence_source_id",
        table_name="provider_trust_verification_evidence",
    )
    op.drop_index(
        "ix_provider_trust_verification_evidence_fac_id",
        table_name="provider_trust_verification_evidence",
    )
    op.drop_index(
        "ix_provider_trust_verification_evidence_prof_id",
        table_name="provider_trust_verification_evidence",
    )
    op.drop_table("provider_trust_verification_evidence")

    op.drop_index(
        "ix_facility_verification_registration",
        table_name="facility_verification",
    )
    op.drop_constraint(
        "ck_facility_verification_recheck_failure_reason",
        "facility_verification",
        type_="check",
    )
    op.drop_constraint(
        "ck_facility_verification_validity",
        "facility_verification",
        type_="check",
    )
    op.drop_column("facility_verification", "authoritative_adverse_signal_at")
    op.drop_column("facility_verification", "previous_verification_valid")
    op.drop_column("facility_verification", "recheck_failure_reason")
    op.drop_column("facility_verification", "recheck_attempted_at")
    op.drop_column("facility_verification", "grace_expires_at")
    op.drop_column("facility_verification", "registration_valid_until")
    op.drop_column("facility_verification", "registration_valid_from")
    op.drop_column("facility_verification", "registration_number_normalized")
    op.drop_column("facility_verification", "registration_authority_code")
