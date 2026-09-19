"""Add append-only prescriber eligibility authority decisions.

Revision ID: 20260919_prescriber_eligibility
Revises: 20260918_treatment_vitals_encounter
Create Date: 2026-09-19 18:20:00.000000

Purpose:
    Introduce the prescribing-specific professional authority layer required
    before any canonical Prescription persistence.  Decisions are independent
    from ProfessionalVerification, immutable, versioned, evidence-bound, and
    reviewer-bound.  Also adds the dedicated GLOBAL trust-management permission
    used by the governed reviewer workflow.

Existing-data behavior:
    No providers receive prescribing eligibility.  The new table starts empty.
    Existing provider trust permissions and clinical capabilities are unchanged.

Rollback position:
    Downgrade removes the prescribing decision table and restores the previous
    trust-management permission constraints.  It does not alter clinical data.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260919_prescriber_eligibility"
down_revision: Union[str, None] = "20260918_treatment_vitals_encounter"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PERMISSION_CONSTRAINT = "ck_provider_trust_permission_grant_permission"
_SCOPE_CONSTRAINT = "ck_provider_trust_permission_grant_scope_binding"
_IMMUTABLE_FUNCTION = "nexa_prescribing_eligibility_decision_immutable"
_IMMUTABLE_TRIGGER = "trg_prescribing_eligibility_decision_immutable"


def _create_permission_constraints() -> None:
    op.create_check_constraint(
        _PERMISSION_CONSTRAINT,
        "provider_trust_permission_grant",
        "permission IN ('PROFESSIONAL_REVIEW', 'PRESCRIBING_ELIGIBILITY_REVIEW', "
        "'FACILITY_REVIEW', 'AFFILIATION_MANAGE', 'TRUST_PERMISSION_MANAGE')",
    )
    op.create_check_constraint(
        _SCOPE_CONSTRAINT,
        "provider_trust_permission_grant",
        "(permission IN ('PROFESSIONAL_REVIEW', "
        "'PRESCRIBING_ELIGIBILITY_REVIEW', 'TRUST_PERMISSION_MANAGE') "
        "AND scope_type = 'GLOBAL' AND facility_id IS NULL) OR "
        "(permission IN ('FACILITY_REVIEW', 'AFFILIATION_MANAGE') "
        "AND scope_type = 'FACILITY' AND facility_id IS NOT NULL)",
    )


def _restore_previous_permission_constraints() -> None:
    op.create_check_constraint(
        _PERMISSION_CONSTRAINT,
        "provider_trust_permission_grant",
        "permission IN ('PROFESSIONAL_REVIEW', 'FACILITY_REVIEW', "
        "'AFFILIATION_MANAGE', 'TRUST_PERMISSION_MANAGE')",
    )
    op.create_check_constraint(
        _SCOPE_CONSTRAINT,
        "provider_trust_permission_grant",
        "(permission IN ('PROFESSIONAL_REVIEW', 'TRUST_PERMISSION_MANAGE') "
        "AND scope_type = 'GLOBAL' AND facility_id IS NULL) OR "
        "(permission IN ('FACILITY_REVIEW', 'AFFILIATION_MANAGE') "
        "AND scope_type = 'FACILITY' AND facility_id IS NOT NULL)",
    )


def upgrade() -> None:
    op.drop_constraint(
        _PERMISSION_CONSTRAINT,
        "provider_trust_permission_grant",
        type_="check",
    )
    op.drop_constraint(
        _SCOPE_CONSTRAINT,
        "provider_trust_permission_grant",
        type_="check",
    )
    _create_permission_constraints()

    op.create_table(
        "prescribing_eligibility_decision",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "provider_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "professional_verification_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "professional_verification_version",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("practitioner_class", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column(
            "registration_authority_code",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "registration_number_normalized",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column("source_reference", sa.String(length=255), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "valid_until",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "reviewer_provider_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "decision_reason_code",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("restriction_code", sa.String(length=64), nullable=True),
        sa.Column(
            "policy_version",
            sa.String(length=64),
            nullable=False,
            server_default="prescriber-eligibility/v1",
        ),
        sa.Column(
            "previous_decision_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["provider_identity.id"],
            name="fk_prescribing_eligibility_provider",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["professional_verification_id"],
            ["professional_verification.id"],
            name="fk_prescribing_eligibility_professional_verification",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_provider_id"],
            ["provider_identity.id"],
            name="fk_prescribing_eligibility_reviewer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["previous_decision_id"],
            ["prescribing_eligibility_decision.id"],
            name="fk_prescribing_eligibility_previous_decision",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "provider_id",
            "version",
            name="uq_prescribing_eligibility_provider_version",
        ),
        sa.UniqueConstraint(
            "previous_decision_id",
            name="uq_prescribing_eligibility_previous_decision",
        ),
        sa.CheckConstraint(
            "version > 0",
            name="ck_prescribing_eligibility_version_positive",
        ),
        sa.CheckConstraint(
            "professional_verification_version > 0",
            name="ck_prescribing_eligibility_prof_version_positive",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'ELIGIBLE', 'RECHECK_DUE', 'RESTRICTED', "
            "'SUSPENDED', 'REVOKED', 'EXPIRED', 'SOURCE_UNAVAILABLE')",
            name="ck_prescribing_eligibility_status",
        ),
        sa.CheckConstraint(
            "practitioner_class IN ('FULL_RMP_MODERN_MEDICINE', "
            "'COMMUNITY_HEALTH_PROVIDER', 'PROVISIONAL_INTERNSHIP', "
            "'TEMPORARY_FOREIGN', 'LIMITED_OR_RESTRICTED', "
            "'UNSUPPORTED_PROFESSION', 'UNKNOWN')",
            name="ck_prescribing_eligibility_practitioner_class",
        ),
        sa.CheckConstraint(
            "source_type IN ('NMR', 'SMR', 'COMPETENT_MEDICAL_COUNCIL', 'HPR')",
            name="ck_prescribing_eligibility_source_type",
        ),
        sa.CheckConstraint(
            "decision_reason_code IN ('PRIMARY_SOURCE_CURRENT_FULL_RMP', "
            "'RECHECK_CONFIRMED_CURRENT', 'RESTRICTED_CLASS', "
            "'PROVISIONAL_REGISTRATION', 'TEMPORARY_REGISTRATION', "
            "'PROFESSIONAL_SUSPENDED', 'PROFESSIONAL_REVOKED', "
            "'PROFESSIONAL_EXPIRED', 'REGISTRATION_INACTIVE', 'HPR_ONLY', "
            "'UNSUPPORTED_PROFESSION', 'SOURCE_UNAVAILABLE', "
            "'AUTHORITY_UNRESOLVED')",
            name="ck_prescribing_eligibility_reason",
        ),
        sa.CheckConstraint(
            "restriction_code IS NULL OR restriction_code IN "
            "('LIMITED_LICENCE', 'PROVISIONAL_ONLY', 'TEMPORARY_SCOPE', "
            "'REGULATORY_RESTRICTION', 'UNSUPPORTED_SCOPE')",
            name="ck_prescribing_eligibility_restriction",
        ),
        sa.CheckConstraint(
            "evidence_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_prescribing_eligibility_evidence_sha256",
        ),
        sa.CheckConstraint(
            "length(trim(source_reference)) > 0",
            name="ck_prescribing_eligibility_source_reference",
        ),
        sa.CheckConstraint(
            "length(trim(registration_authority_code)) > 0 AND "
            "length(trim(registration_number_normalized)) > 0",
            name="ck_prescribing_eligibility_registration_binding",
        ),
        sa.CheckConstraint(
            "valid_until > checked_at",
            name="ck_prescribing_eligibility_validity",
        ),
        sa.CheckConstraint(
            "reviewer_provider_id <> provider_id",
            name="ck_prescribing_eligibility_no_self_review",
        ),
        sa.CheckConstraint(
            "(status <> 'ELIGIBLE') OR "
            "(practitioner_class = 'FULL_RMP_MODERN_MEDICINE' "
            "AND source_type IN ('NMR', 'SMR', 'COMPETENT_MEDICAL_COUNCIL') "
            "AND restriction_code IS NULL)",
            name="ck_prescribing_eligibility_positive_shape",
        ),
    )
    op.create_index(
        "ix_prescribing_eligibility_provider_version",
        "prescribing_eligibility_decision",
        ["provider_id", "version"],
        unique=False,
    )
    op.create_index(
        "ix_prescribing_eligibility_professional_verification_id",
        "prescribing_eligibility_decision",
        ["professional_verification_id"],
        unique=False,
    )
    op.create_index(
        "ix_prescribing_eligibility_valid_until",
        "prescribing_eligibility_decision",
        ["valid_until"],
        unique=False,
    )

    op.execute(
        f"""
        CREATE FUNCTION public.{_IMMUTABLE_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          RAISE EXCEPTION 'PRESCRIBING_ELIGIBILITY_DECISION_IMMUTABLE'
            USING ERRCODE = '55000';
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_IMMUTABLE_TRIGGER}
        BEFORE UPDATE OR DELETE ON public.prescribing_eligibility_decision
        FOR EACH ROW
        EXECUTE FUNCTION public.{_IMMUTABLE_FUNCTION}();
        """
    )


def downgrade() -> None:
    op.execute(
        f"DROP TRIGGER IF EXISTS {_IMMUTABLE_TRIGGER} "
        "ON public.prescribing_eligibility_decision"
    )
    op.execute(
        f"DROP FUNCTION IF EXISTS public.{_IMMUTABLE_FUNCTION}()"
    )
    op.drop_index(
        "ix_prescribing_eligibility_valid_until",
        table_name="prescribing_eligibility_decision",
    )
    op.drop_index(
        "ix_prescribing_eligibility_professional_verification_id",
        table_name="prescribing_eligibility_decision",
    )
    op.drop_index(
        "ix_prescribing_eligibility_provider_version",
        table_name="prescribing_eligibility_decision",
    )
    op.drop_table("prescribing_eligibility_decision")

    op.drop_constraint(
        _PERMISSION_CONSTRAINT,
        "provider_trust_permission_grant",
        type_="check",
    )
    op.drop_constraint(
        _SCOPE_CONSTRAINT,
        "provider_trust_permission_grant",
        type_="check",
    )
    _restore_previous_permission_constraints()
