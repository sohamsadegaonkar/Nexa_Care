"""Medication classification catalog infrastructure.

Revision ID: 20260919_medication_catalog
Revises: 20260919_prescriber_eligibility

Creates only global medication-catalog governance authority. No Prescription or
PrescriptionItem persistence and no medication data backfill are introduced.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260919_medication_catalog"
down_revision: Union[str, Sequence[str], None] = "20260919_prescriber_eligibility"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PERMISSION_CONSTRAINT = "ck_provider_trust_permission_grant_permission"
_SCOPE_CONSTRAINT = "ck_provider_trust_permission_grant_scope_binding"

_RELEASE_GUARD_FN = "nexa_medication_catalog_release_guard"
_RELEASE_GUARD_TRIGGER = "trg_medication_catalog_release_guard"
_CHILD_GUARD_FN = "nexa_medication_catalog_child_guard"
_ENTRY_GUARD_TRIGGER = "trg_medication_catalog_entry_guard"
_EVIDENCE_GUARD_TRIGGER = "trg_medication_catalog_evidence_guard"
_EMERGENCY_GUARD_FN = "nexa_medication_catalog_emergency_immutable"
_EMERGENCY_GUARD_TRIGGER = "trg_medication_catalog_emergency_immutable"


def _create_permission_constraints() -> None:
    op.create_check_constraint(
        _PERMISSION_CONSTRAINT,
        "provider_trust_permission_grant",
        "permission IN ('PROFESSIONAL_REVIEW', "
        "'PRESCRIBING_ELIGIBILITY_REVIEW', "
        "'MEDICATION_CATALOG_RELEASE_REVIEW', "
        "'FACILITY_REVIEW', 'AFFILIATION_MANAGE', "
        "'TRUST_PERMISSION_MANAGE')",
    )
    op.create_check_constraint(
        _SCOPE_CONSTRAINT,
        "provider_trust_permission_grant",
        "(permission IN ('PROFESSIONAL_REVIEW', "
        "'PRESCRIBING_ELIGIBILITY_REVIEW', "
        "'MEDICATION_CATALOG_RELEASE_REVIEW', "
        "'TRUST_PERMISSION_MANAGE') "
        "AND scope_type = 'GLOBAL' AND facility_id IS NULL) OR "
        "(permission IN ('FACILITY_REVIEW', 'AFFILIATION_MANAGE') "
        "AND scope_type = 'FACILITY' AND facility_id IS NOT NULL)",
    )


def _restore_permission_constraints() -> None:
    op.create_check_constraint(
        _PERMISSION_CONSTRAINT,
        "provider_trust_permission_grant",
        "permission IN ('PROFESSIONAL_REVIEW', "
        "'PRESCRIBING_ELIGIBILITY_REVIEW', 'FACILITY_REVIEW', "
        "'AFFILIATION_MANAGE', 'TRUST_PERMISSION_MANAGE')",
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
        "medication_catalog_release",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("source_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column(
            "source_terminology_version",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column("integrity_digest", sa.String(length=64), nullable=True),
        sa.Column("canonical_manifest", sa.Text(), nullable=True),
        sa.Column("artifact_signature", sa.Text(), nullable=True),
        sa.Column("artifact_key_id", sa.String(length=255), nullable=True),
        sa.Column("signature_algorithm", sa.String(length=32), nullable=True),
        sa.Column(
            "prepared_by",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "qualified_by",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "activated_by",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("qualified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "previous_release_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["prepared_by"],
            ["provider_identity.id"],
            name="fk_medication_catalog_release_preparer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["qualified_by"],
            ["provider_identity.id"],
            name="fk_medication_catalog_release_qualifier",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["activated_by"],
            ["provider_identity.id"],
            name="fk_medication_catalog_release_activator",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["previous_release_id"],
            ["medication_catalog_release.id"],
            name="fk_medication_catalog_release_previous",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "version",
            name="uq_medication_catalog_release_version",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','QUALIFIED','ACTIVE','SUPERSEDED','REVOKED')",
            name="ck_medication_catalog_release_status",
        ),
        sa.CheckConstraint(
            "signature_algorithm IS NULL OR "
            "signature_algorithm = 'ECDSA_SHA_256'",
            name="ck_medication_catalog_release_signature_algorithm",
        ),
        sa.CheckConstraint(
            "integrity_digest IS NULL OR "
            "integrity_digest ~ '^[0-9a-f]{64}$'",
            name="ck_medication_catalog_release_integrity_digest",
        ),
        sa.CheckConstraint(
            "qualified_by IS NULL OR prepared_by <> qualified_by",
            name="ck_medication_catalog_release_preparer_not_qualifier",
        ),
        sa.CheckConstraint(
            "activated_by IS NULL OR prepared_by <> activated_by",
            name="ck_medication_catalog_release_preparer_not_activator",
        ),
        sa.CheckConstraint(
            "previous_release_id IS NULL OR previous_release_id <> id",
            name="ck_medication_catalog_release_previous_not_self",
        ),
        sa.CheckConstraint(
            "(status = 'DRAFT') OR "
            "(qualified_by IS NOT NULL AND qualified_at IS NOT NULL "
            "AND integrity_digest IS NOT NULL "
            "AND canonical_manifest IS NOT NULL "
            "AND artifact_signature IS NOT NULL "
            "AND artifact_key_id IS NOT NULL "
            "AND signature_algorithm = 'ECDSA_SHA_256')",
            name="ck_medication_catalog_release_published_shape",
        ),
        sa.CheckConstraint(
            "(status <> 'ACTIVE') OR "
            "(activated_by IS NOT NULL AND activated_at IS NOT NULL "
            "AND superseded_at IS NULL AND revoked_at IS NULL)",
            name="ck_medication_catalog_release_active_shape",
        ),
        sa.CheckConstraint(
            "(status <> 'SUPERSEDED') OR "
            "(superseded_at IS NOT NULL AND revoked_at IS NULL)",
            name="ck_medication_catalog_release_superseded_shape",
        ),
        sa.CheckConstraint(
            "(status <> 'REVOKED') OR revoked_at IS NOT NULL",
            name="ck_medication_catalog_release_revoked_shape",
        ),
    )
    op.create_index(
        "uq_medication_catalog_release_single_active",
        "medication_catalog_release",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "ix_medication_catalog_release_source_cutoff",
        "medication_catalog_release",
        ["source_cutoff_at"],
        unique=False,
    )

    op.create_table(
        "medication_catalog_entry",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "release_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("medication_code", sa.String(length=64), nullable=False),
        sa.Column("code_system", sa.String(length=64), nullable=False),
        sa.Column("code_system_version", sa.String(length=128), nullable=False),
        sa.Column(
            "canonical_generic_name",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column("medication_display", sa.String(length=255), nullable=False),
        sa.Column("ingredient_identity", sa.String(length=512), nullable=False),
        sa.Column("dose_form", sa.String(length=128), nullable=True),
        sa.Column(
            "identity_strength_descriptor",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column(
            "identity_granularity_sufficient",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("terminology_status", sa.String(length=16), nullable=False),
        sa.Column(
            "drug_schedule_class",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("ndps_class", sa.String(length=32), nullable=False),
        sa.Column("telemedicine_class", sa.String(length=32), nullable=False),
        sa.Column(
            "special_recordkeeping_class",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "nexa_high_risk_class",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "regulatory_product_status",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "classification_rationale_code",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "v1_universal_allowed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "entry_integrity_digest",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "first_reviewer_provider_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "second_reviewer_provider_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("first_review_digest", sa.String(length=64), nullable=True),
        sa.Column("second_review_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "first_reviewed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "second_reviewed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["release_id"],
            ["medication_catalog_release.id"],
            name="fk_medication_catalog_entry_release",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["first_reviewer_provider_id"],
            ["provider_identity.id"],
            name="fk_medication_catalog_entry_first_reviewer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["second_reviewer_provider_id"],
            ["provider_identity.id"],
            name="fk_medication_catalog_entry_second_reviewer",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "release_id",
            "medication_code",
            name="uq_medication_catalog_entry_release_code",
        ),
        sa.CheckConstraint(
            "terminology_status IN ('ACTIVE','INACTIVE','UNKNOWN')",
            name="ck_medication_catalog_entry_terminology_status",
        ),
        sa.CheckConstraint(
            "drug_schedule_class IN "
            "('NONE_CONFIRMED','G','H','H1','X',"
            "'MULTIPLE_RESTRICTED','UNKNOWN')",
            name="ck_medication_catalog_entry_schedule",
        ),
        sa.CheckConstraint(
            "ndps_class IN "
            "('NOT_CONTROLLED_CONFIRMED','CONTROLLED','UNKNOWN')",
            name="ck_medication_catalog_entry_ndps",
        ),
        sa.CheckConstraint(
            "telemedicine_class IN "
            "('LIST_O_ANY_MODE','RESTRICTED_MODE','PROHIBITED','UNKNOWN')",
            name="ck_medication_catalog_entry_telemedicine",
        ),
        sa.CheckConstraint(
            "special_recordkeeping_class IN "
            "('NONE_CONFIRMED','REQUIRED','UNKNOWN')",
            name="ck_medication_catalog_entry_recordkeeping",
        ),
        sa.CheckConstraint(
            "nexa_high_risk_class IN "
            "('NONE_CONFIRMED','SPECIALIST_RESTRICTED',"
            "'ONCOLOGY_HIGH_RISK','OTHER_HIGH_RISK','UNKNOWN')",
            name="ck_medication_catalog_entry_high_risk",
        ),
        sa.CheckConstraint(
            "regulatory_product_status IN "
            "('CURRENT','INACTIVE','PROHIBITED','UNKNOWN')",
            name="ck_medication_catalog_entry_regulatory_status",
        ),
        sa.CheckConstraint(
            "entry_integrity_digest IS NULL OR "
            "entry_integrity_digest ~ '^[0-9a-f]{64}$'",
            name="ck_medication_catalog_entry_digest",
        ),
        sa.CheckConstraint(
            "first_review_digest IS NULL OR "
            "first_review_digest ~ '^[0-9a-f]{64}$'",
            name="ck_medication_catalog_entry_first_review_digest",
        ),
        sa.CheckConstraint(
            "second_review_digest IS NULL OR "
            "second_review_digest ~ '^[0-9a-f]{64}$'",
            name="ck_medication_catalog_entry_second_review_digest",
        ),
        sa.CheckConstraint(
            "first_reviewer_provider_id IS NULL OR "
            "second_reviewer_provider_id IS NULL OR "
            "first_reviewer_provider_id <> second_reviewer_provider_id",
            name="ck_medication_catalog_entry_distinct_reviewers",
        ),
        sa.CheckConstraint(
            "(first_reviewer_provider_id IS NULL) = "
            "(first_reviewed_at IS NULL) AND "
            "(first_reviewer_provider_id IS NULL) = "
            "(first_review_digest IS NULL)",
            name="ck_medication_catalog_entry_first_review_shape",
        ),
        sa.CheckConstraint(
            "(second_reviewer_provider_id IS NULL) = "
            "(second_reviewed_at IS NULL) AND "
            "(second_reviewer_provider_id IS NULL) = "
            "(second_review_digest IS NULL)",
            name="ck_medication_catalog_entry_second_review_shape",
        ),
        sa.CheckConstraint(
            "length(trim(medication_code)) BETWEEN 1 AND 64",
            name="ck_medication_catalog_entry_code_nonempty",
        ),
        sa.CheckConstraint(
            "length(trim(classification_rationale_code)) > 0",
            name="ck_medication_catalog_entry_rationale_nonempty",
        ),
    )
    op.create_index(
        "ix_medication_catalog_entry_code",
        "medication_catalog_entry",
        ["medication_code"],
        unique=False,
    )
    op.create_index(
        "ix_medication_catalog_entry_release_allowed",
        "medication_catalog_entry",
        ["release_id", "v1_universal_allowed"],
        unique=False,
    )

    op.create_table(
        "medication_catalog_evidence",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "release_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "entry_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("finding_dimension", sa.String(length=32), nullable=False),
        sa.Column("source_authority", sa.String(length=32), nullable=False),
        sa.Column(
            "source_document_version",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column("source_reference", sa.String(length=255), nullable=False),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finding_value", sa.String(length=128), nullable=False),
        sa.Column("rationale_code", sa.String(length=64), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "prepared_by",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["release_id"],
            ["medication_catalog_release.id"],
            name="fk_medication_catalog_evidence_release",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["medication_catalog_entry.id"],
            name="fk_medication_catalog_evidence_entry",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["prepared_by"],
            ["provider_identity.id"],
            name="fk_medication_catalog_evidence_preparer",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "release_id",
            "entry_id",
            "finding_dimension",
            "evidence_sha256",
            name="uq_medication_catalog_evidence_finding",
        ),
        sa.CheckConstraint(
            "finding_dimension IN "
            "('IDENTITY','DRUG_SCHEDULE','NDPS','TELEMEDICINE',"
            "'SPECIAL_RECORDKEEPING','HIGH_RISK',"
            "'REGULATORY_PRODUCT_STATUS')",
            name="ck_medication_catalog_evidence_dimension",
        ),
        sa.CheckConstraint(
            "source_authority IN "
            "('CDSCO','INDIA_CODE','MOHFW','NMC','NRCES',"
            "'SNOMED_IDENTITY_ONLY')",
            name="ck_medication_catalog_evidence_authority",
        ),
        sa.CheckConstraint(
            "evidence_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_medication_catalog_evidence_sha256",
        ),
        sa.CheckConstraint(
            "length(trim(source_reference)) > 0 "
            "AND length(trim(finding_value)) > 0 "
            "AND length(trim(rationale_code)) > 0",
            name="ck_medication_catalog_evidence_nonempty",
        ),
    )
    op.create_index(
        "ix_medication_catalog_evidence_entry",
        "medication_catalog_evidence",
        ["release_id", "entry_id"],
        unique=False,
    )

    op.create_table(
        "medication_catalog_emergency_deny",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("medication_code", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=8), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column(
            "evidence_reference",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "actor_provider_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "predecessor_event_id",
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
            ["actor_provider_id"],
            ["provider_identity.id"],
            name="fk_medication_catalog_emergency_actor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["predecessor_event_id"],
            ["medication_catalog_emergency_deny.id"],
            name="fk_medication_catalog_emergency_predecessor",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "medication_code",
            "version",
            name="uq_medication_catalog_emergency_code_version",
        ),
        sa.UniqueConstraint(
            "predecessor_event_id",
            name="uq_medication_catalog_emergency_predecessor",
        ),
        sa.CheckConstraint(
            "version > 0",
            name="ck_medication_catalog_emergency_version_positive",
        ),
        sa.CheckConstraint(
            "action IN ('DENY','CLEAR')",
            name="ck_medication_catalog_emergency_action",
        ),
        sa.CheckConstraint(
            "reason_code IN "
            "('REGULATORY_PROHIBITION','REGULATORY_RECLASSIFICATION',"
            "'SOURCE_INTEGRITY_FAILURE','CATALOG_CLASSIFICATION_ERROR',"
            "'PATIENT_SAFETY_HOLD')",
            name="ck_medication_catalog_emergency_reason",
        ),
        sa.CheckConstraint(
            "evidence_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_medication_catalog_emergency_evidence_sha256",
        ),
        sa.CheckConstraint(
            "predecessor_event_id IS NULL OR predecessor_event_id <> id",
            name="ck_medication_catalog_emergency_predecessor_not_self",
        ),
    )
    op.create_index(
        "ix_medication_catalog_emergency_code_effective",
        "medication_catalog_emergency_deny",
        ["medication_code", "effective_at", "version"],
        unique=False,
    )

    op.execute(
        f"""
        CREATE FUNCTION public.{_RELEASE_GUARD_FN}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.status <> 'DRAFT' THEN
              RAISE EXCEPTION 'MEDICATION_CATALOG_RELEASE_IMMUTABLE'
                USING ERRCODE = '55000';
            END IF;
            RETURN OLD;
          END IF;

          IF OLD.status = 'DRAFT' THEN
            IF NEW.status NOT IN ('DRAFT', 'QUALIFIED') THEN
              RAISE EXCEPTION 'MEDICATION_CATALOG_RELEASE_TRANSITION_INVALID'
                USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
          END IF;

          IF OLD.status = 'QUALIFIED' THEN
            IF NEW.status NOT IN ('ACTIVE', 'REVOKED') THEN
              RAISE EXCEPTION 'MEDICATION_CATALOG_RELEASE_TRANSITION_INVALID'
                USING ERRCODE = '55000';
            END IF;
            IF (
              to_jsonb(NEW) - ARRAY[
                'status','activated_by','activated_at',
                'superseded_at','revoked_at','previous_release_id'
              ]::text[]
            ) IS DISTINCT FROM (
              to_jsonb(OLD) - ARRAY[
                'status','activated_by','activated_at',
                'superseded_at','revoked_at','previous_release_id'
              ]::text[]
            ) THEN
              RAISE EXCEPTION 'MEDICATION_CATALOG_RELEASE_IMMUTABLE'
                USING ERRCODE = '55000';
            END IF;
          ELSIF OLD.status = 'ACTIVE' THEN
            IF NEW.status NOT IN ('SUPERSEDED', 'REVOKED') THEN
              RAISE EXCEPTION 'MEDICATION_CATALOG_RELEASE_TRANSITION_INVALID'
                USING ERRCODE = '55000';
            END IF;
            IF (
              to_jsonb(NEW) - ARRAY[
                'status','superseded_at','revoked_at'
              ]::text[]
            ) IS DISTINCT FROM (
              to_jsonb(OLD) - ARRAY[
                'status','superseded_at','revoked_at'
              ]::text[]
            ) THEN
              RAISE EXCEPTION 'MEDICATION_CATALOG_RELEASE_IMMUTABLE'
                USING ERRCODE = '55000';
            END IF;
          ELSE
            RAISE EXCEPTION 'MEDICATION_CATALOG_RELEASE_IMMUTABLE'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_RELEASE_GUARD_TRIGGER}
        BEFORE UPDATE OR DELETE ON public.medication_catalog_release
        FOR EACH ROW EXECUTE FUNCTION public.{_RELEASE_GUARD_FN}();
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION public.{_CHILD_GUARD_FN}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE parent_status text;
        DECLARE target_release_id uuid;
        BEGIN
          target_release_id := CASE
            WHEN TG_OP = 'DELETE' THEN OLD.release_id
            ELSE NEW.release_id
          END;

          SELECT status INTO parent_status
          FROM public.medication_catalog_release
          WHERE id = target_release_id
          FOR SHARE;

          IF parent_status IS DISTINCT FROM 'DRAFT' THEN
            RAISE EXCEPTION 'MEDICATION_CATALOG_PUBLISHED_CONTENT_IMMUTABLE'
              USING ERRCODE = '55000';
          END IF;
          RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_ENTRY_GUARD_TRIGGER}
        BEFORE INSERT OR UPDATE OR DELETE ON public.medication_catalog_entry
        FOR EACH ROW EXECUTE FUNCTION public.{_CHILD_GUARD_FN}();
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_EVIDENCE_GUARD_TRIGGER}
        BEFORE INSERT OR UPDATE OR DELETE ON public.medication_catalog_evidence
        FOR EACH ROW EXECUTE FUNCTION public.{_CHILD_GUARD_FN}();
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION public.{_EMERGENCY_GUARD_FN}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          RAISE EXCEPTION 'MEDICATION_CATALOG_EMERGENCY_HISTORY_IMMUTABLE'
            USING ERRCODE = '55000';
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_EMERGENCY_GUARD_TRIGGER}
        BEFORE UPDATE OR DELETE ON public.medication_catalog_emergency_deny
        FOR EACH ROW EXECUTE FUNCTION public.{_EMERGENCY_GUARD_FN}();
        """
    )


def downgrade() -> None:
    op.execute(
        f"DROP TRIGGER IF EXISTS {_EMERGENCY_GUARD_TRIGGER} "
        "ON public.medication_catalog_emergency_deny"
    )
    op.execute(
        f"DROP FUNCTION IF EXISTS public.{_EMERGENCY_GUARD_FN}()"
    )
    op.execute(
        f"DROP TRIGGER IF EXISTS {_EVIDENCE_GUARD_TRIGGER} "
        "ON public.medication_catalog_evidence"
    )
    op.execute(
        f"DROP TRIGGER IF EXISTS {_ENTRY_GUARD_TRIGGER} "
        "ON public.medication_catalog_entry"
    )
    op.execute(f"DROP FUNCTION IF EXISTS public.{_CHILD_GUARD_FN}()")
    op.execute(
        f"DROP TRIGGER IF EXISTS {_RELEASE_GUARD_TRIGGER} "
        "ON public.medication_catalog_release"
    )
    op.execute(f"DROP FUNCTION IF EXISTS public.{_RELEASE_GUARD_FN}()")

    op.drop_index(
        "ix_medication_catalog_emergency_code_effective",
        table_name="medication_catalog_emergency_deny",
    )
    op.drop_table("medication_catalog_emergency_deny")
    op.drop_index(
        "ix_medication_catalog_evidence_entry",
        table_name="medication_catalog_evidence",
    )
    op.drop_table("medication_catalog_evidence")
    op.drop_index(
        "ix_medication_catalog_entry_release_allowed",
        table_name="medication_catalog_entry",
    )
    op.drop_index(
        "ix_medication_catalog_entry_code",
        table_name="medication_catalog_entry",
    )
    op.drop_table("medication_catalog_entry")
    op.drop_index(
        "ix_medication_catalog_release_source_cutoff",
        table_name="medication_catalog_release",
    )
    op.drop_index(
        "uq_medication_catalog_release_single_active",
        table_name="medication_catalog_release",
    )
    op.drop_table("medication_catalog_release")

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
    _restore_permission_constraints()
