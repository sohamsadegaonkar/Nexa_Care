"""Durable manual-review state for patient registration recovery.

This model stores authority metadata only. It never stores OTPs, phone numbers,
provider access tokens, patient access tokens, device private material, or
clinical content.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin

REGISTRATION_RECOVERY_REVIEW_CONTRACT_VERSION = "registration-recovery-review/1.0"
REGISTRATION_RECOVERY_REVIEW_POLICY_VERSION = "registration-recovery-review-policy/1.0"
REGISTRATION_RECOVERY_REVIEWER_ROLE = "registration_recovery_reviewer"


class RegistrationRecoveryReviewStatus(StrEnum):
    PENDING = "PENDING"
    IN_REVIEW = "IN_REVIEW"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"
    SECURITY_ESCALATED = "SECURITY_ESCALATED"


class RegistrationRecoveryReviewOutcome(StrEnum):
    RESTORE_MISSING_RECORD_ANCHOR = "RESTORE_MISSING_RECORD_ANCHOR"
    REBIND_MERGED_IDENTITY = "REBIND_MERGED_IDENTITY"
    NO_REPAIR = "NO_REPAIR"
    SECURITY_ESCALATION_REQUIRED = "SECURITY_ESCALATION_REQUIRED"


class RegistrationRecoveryReviewReason(StrEnum):
    MISSING_RECORD_ANCHOR = "MISSING_RECORD_ANCHOR"
    MERGED_IDENTITY_REBIND_REQUIRED = "MERGED_IDENTITY_REBIND_REQUIRED"
    IDENTITY_REVOKED = "IDENTITY_REVOKED"
    PATIENT_DELETED_WITHOUT_MERGE = "PATIENT_DELETED_WITHOUT_MERGE"
    ERASURE_STATE_PRESENT = "ERASURE_STATE_PRESENT"
    MULTIPLE_IDENTITIES = "MULTIPLE_IDENTITIES"
    MERGE_AMBIGUOUS = "MERGE_AMBIGUOUS"
    GRAPH_STATE_CHANGED = "GRAPH_STATE_CHANGED"
    SECURITY_CONCERN = "SECURITY_CONCERN"


class PatientRegistrationRecoveryReviewCase(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "patient_registration_recovery_review_cases"

    case_reference: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_subject_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patients.patient_uuid", ondelete="RESTRICT"),
        nullable=True,
    )
    graph_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    assigned_reviewer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    assigned_reviewer_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    creation_idempotency_key: Mapped[str] = mapped_column(String(192), nullable=False)
    creation_operation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "provider = 'supabase'",
            name="ck_registration_recovery_review_provider",
        ),
        CheckConstraint(
            "status IN ('PENDING','IN_REVIEW','RESOLVED','REJECTED','SECURITY_ESCALATED')",
            name="ck_registration_recovery_review_status",
        ),
        CheckConstraint("version > 0", name="ck_registration_recovery_review_version"),
        CheckConstraint(
            "char_length(provider_subject_hash) = 64 AND char_length(graph_fingerprint) = 64 "
            "AND char_length(creation_operation_hash) = 64",
            name="ck_registration_recovery_review_hash_lengths",
        ),
        CheckConstraint(
            "cardinality(reason_codes) > 0",
            name="ck_registration_recovery_review_reason_nonempty",
        ),
        CheckConstraint(
            "(status = 'PENDING' AND assigned_reviewer_id IS NULL AND assigned_reviewer_role IS NULL "
            "AND claimed_at IS NULL AND resolved_at IS NULL) OR "
            "(status = 'IN_REVIEW' AND assigned_reviewer_id IS NOT NULL "
            "AND assigned_reviewer_role = 'registration_recovery_reviewer' "
            "AND claimed_at IS NOT NULL AND resolved_at IS NULL) OR "
            "(status IN ('RESOLVED','REJECTED','SECURITY_ESCALATED') "
            "AND assigned_reviewer_id IS NOT NULL "
            "AND assigned_reviewer_role = 'registration_recovery_reviewer' "
            "AND claimed_at IS NOT NULL AND resolved_at IS NOT NULL)",
            name="ck_registration_recovery_review_assignment_state",
        ),
        UniqueConstraint(
            "provider",
            "provider_subject_hash",
            "graph_fingerprint",
            name="uq_registration_recovery_review_graph",
        ),
        UniqueConstraint(
            "creation_idempotency_key",
            name="uq_registration_recovery_review_creation_idempotency",
        ),
        Index("ix_registration_recovery_review_status", "status"),
        Index("ix_registration_recovery_review_patient", "patient_id"),
        Index("ix_registration_recovery_review_reviewer", "assigned_reviewer_id", "status"),
    )


class PatientRegistrationRecoveryReviewDisposition(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "patient_registration_recovery_review_dispositions"

    case_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patient_registration_recovery_review_cases.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    reviewer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reviewer_role: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)
    prior_case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(192), nullable=False, unique=True)
    operation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "reviewer_role = 'registration_recovery_reviewer'",
            name="ck_registration_recovery_disposition_role",
        ),
        CheckConstraint(
            "outcome IN ('RESTORE_MISSING_RECORD_ANCHOR','REBIND_MERGED_IDENTITY','NO_REPAIR','SECURITY_ESCALATION_REQUIRED')",
            name="ck_registration_recovery_disposition_outcome",
        ),
        CheckConstraint(
            "cardinality(reason_codes) > 0",
            name="ck_registration_recovery_disposition_reason_nonempty",
        ),
        CheckConstraint(
            "prior_case_version > 0 AND char_length(operation_hash) = 64",
            name="ck_registration_recovery_disposition_operation",
        ),
        Index("ix_registration_recovery_disposition_case", "case_id"),
    )
