"""Closed contracts and dedicated persistence for identity quarantine review."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.security.identity_review_policy import IDENTITY_REVIEW_POLICY_VERSION

IDENTITY_REVIEW_CONTRACT_VERSION = "identity-review/1.0"
IDENTITY_REVIEW_IDEMPOTENCY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,191}$"


class IdentityReviewCaseStatus(StrEnum):
    PENDING = "PENDING"
    IN_REVIEW = "IN_REVIEW"
    RESOLVED_NO_RELEASE = "RESOLVED_NO_RELEASE"
    ESCALATED = "ESCALATED"


class IdentityReviewOutcome(StrEnum):
    REJECTED_FOR_BOUND_PATIENT = "REJECTED_FOR_BOUND_PATIENT"
    VERIFIED_IDENTITY_REQUIRED = "VERIFIED_IDENTITY_REQUIRED"
    SECURITY_ESCALATION_REQUIRED = "SECURITY_ESCALATION_REQUIRED"
    INSUFFICIENT_IDENTITY_EVIDENCE = "INSUFFICIENT_IDENTITY_EVIDENCE"


class IdentityReviewReasonCode(StrEnum):
    DOCUMENT_IDENTITY_MISMATCH = "DOCUMENT_IDENTITY_MISMATCH"
    CANONICAL_IDENTITY_UNAVAILABLE = "CANONICAL_IDENTITY_UNAVAILABLE"
    VERIFIED_IDENTIFIER_REQUIRED = "VERIFIED_IDENTIFIER_REQUIRED"
    POSSIBLE_CROSS_PATIENT_DOCUMENT = "POSSIBLE_CROSS_PATIENT_DOCUMENT"
    POSSIBLE_PRIVACY_INCIDENT = "POSSIBLE_PRIVACY_INCIDENT"
    IDENTITY_REVIEW_INCONCLUSIVE = "IDENTITY_REVIEW_INCONCLUSIVE"
    DOCUMENT_REJECTED_FOR_BOUND_PATIENT = "DOCUMENT_REJECTED_FOR_BOUND_PATIENT"


class IdentityReviewMutationOperation(StrEnum):
    CLAIM = "CLAIM"
    RECOVER_SESSION = "RECOVER_SESSION"
    SUBMIT_DISPOSITION = "SUBMIT_DISPOSITION"


REASONS_BY_OUTCOME: dict[IdentityReviewOutcome, frozenset[IdentityReviewReasonCode]] = {
    IdentityReviewOutcome.REJECTED_FOR_BOUND_PATIENT: frozenset(
        {
            IdentityReviewReasonCode.DOCUMENT_IDENTITY_MISMATCH,
            IdentityReviewReasonCode.POSSIBLE_CROSS_PATIENT_DOCUMENT,
            IdentityReviewReasonCode.DOCUMENT_REJECTED_FOR_BOUND_PATIENT,
        }
    ),
    IdentityReviewOutcome.VERIFIED_IDENTITY_REQUIRED: frozenset(
        {
            IdentityReviewReasonCode.CANONICAL_IDENTITY_UNAVAILABLE,
            IdentityReviewReasonCode.VERIFIED_IDENTIFIER_REQUIRED,
            IdentityReviewReasonCode.IDENTITY_REVIEW_INCONCLUSIVE,
        }
    ),
    IdentityReviewOutcome.SECURITY_ESCALATION_REQUIRED: frozenset(
        {
            IdentityReviewReasonCode.POSSIBLE_CROSS_PATIENT_DOCUMENT,
            IdentityReviewReasonCode.POSSIBLE_PRIVACY_INCIDENT,
        }
    ),
    IdentityReviewOutcome.INSUFFICIENT_IDENTITY_EVIDENCE: frozenset(
        {
            IdentityReviewReasonCode.CANONICAL_IDENTITY_UNAVAILABLE,
            IdentityReviewReasonCode.VERIFIED_IDENTIFIER_REQUIRED,
            IdentityReviewReasonCode.IDENTITY_REVIEW_INCONCLUSIVE,
        }
    ),
}


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CreateIdentityReviewCaseRequest(_FrozenStrictModel):
    idempotency_key: str = Field(
        min_length=8, max_length=192, pattern=IDENTITY_REVIEW_IDEMPOTENCY_PATTERN
    )


class ClaimIdentityReviewCaseRequest(_FrozenStrictModel):
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(
        min_length=8, max_length=192, pattern=IDENTITY_REVIEW_IDEMPOTENCY_PATTERN
    )


class RecoverIdentityReviewSessionRequest(ClaimIdentityReviewCaseRequest):
    pass


class SubmitIdentityReviewDispositionRequest(_FrozenStrictModel):
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(
        min_length=8, max_length=192, pattern=IDENTITY_REVIEW_IDEMPOTENCY_PATTERN
    )
    outcome: IdentityReviewOutcome
    reason_codes: tuple[IdentityReviewReasonCode, ...] = Field(
        min_length=1, max_length=3
    )

    @model_validator(mode="after")
    def validate_reason_codes(self) -> "SubmitIdentityReviewDispositionRequest":
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("duplicate identity-review reason code")
        allowed = REASONS_BY_OUTCOME[self.outcome]
        if any(reason not in allowed for reason in self.reason_codes):
            raise ValueError("reason code is invalid for identity-review outcome")
        return self

    def canonical_operation_hash(self, *, case_id: str, actor_id: str) -> str:
        payload = {
            "actor_id": actor_id,
            "case_id": case_id,
            "expected_version": self.expected_version,
            "outcome": self.outcome.value,
            "reason_codes": sorted(reason.value for reason in self.reason_codes),
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


class IdentityReviewCaseRecord(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "identity_review_cases"

    job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extraction_jobs.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_storage.id", ondelete="RESTRICT"),
        nullable=False,
    )
    original_uploader_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    original_authorization_provider_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    source_consent_request_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    identity_reason_codes: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False
    )
    assigned_reviewer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    assigned_reviewer_role: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    review_session_binding: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    creation_idempotency_key: Mapped[str] = mapped_column(String(192), nullable=False)
    creation_operation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'IN_REVIEW', 'RESOLVED_NO_RELEASE', 'ESCALATED')",
            name="ck_identity_review_cases_status",
        ),
        CheckConstraint(
            "version > 0", name="ck_identity_review_cases_version_positive"
        ),
        CheckConstraint(
            "char_length(creation_operation_hash) = 64",
            name="ck_identity_review_cases_operation_hash_length",
        ),
        CheckConstraint(
            "review_session_binding IS NULL OR char_length(review_session_binding) = 64",
            name="ck_identity_review_cases_session_binding_length",
        ),
        CheckConstraint(
            "identity_reason_codes <@ ARRAY["
            "'DOCUMENT_IDENTITY_MISMATCH', 'CANONICAL_IDENTITY_UNAVAILABLE']::varchar[] "
            "AND cardinality(identity_reason_codes) > 0",
            name="ck_identity_review_cases_reason_codes",
        ),
        CheckConstraint(
            "(status = 'PENDING' AND assigned_reviewer_id IS NULL "
            "AND assigned_reviewer_role IS NULL AND review_session_binding IS NULL "
            "AND claimed_at IS NULL AND resolved_at IS NULL) OR "
            "(status = 'IN_REVIEW' AND assigned_reviewer_id IS NOT NULL "
            "AND assigned_reviewer_role = 'identity_reviewer' "
            "AND review_session_binding IS NOT NULL AND claimed_at IS NOT NULL "
            "AND resolved_at IS NULL) OR "
            "(status IN ('RESOLVED_NO_RELEASE', 'ESCALATED') "
            "AND assigned_reviewer_id IS NOT NULL "
            "AND assigned_reviewer_role = 'identity_reviewer' "
            "AND review_session_binding IS NOT NULL AND claimed_at IS NOT NULL "
            "AND resolved_at IS NOT NULL)",
            name="ck_identity_review_cases_assignment_state",
        ),
        CheckConstraint(
            "assigned_reviewer_id IS NULL OR "
            "(assigned_reviewer_id IS DISTINCT FROM original_uploader_id "
            "AND assigned_reviewer_id IS DISTINCT FROM original_authorization_provider_id)",
            name="ck_identity_review_cases_reviewer_separation",
        ),
        UniqueConstraint(
            "tenant_id",
            "creation_idempotency_key",
            name="uq_identity_review_cases_tenant_idempotency",
        ),
        Index("ix_identity_review_cases_tenant_status", "tenant_id", "status"),
        Index(
            "ix_identity_review_cases_reviewer_status",
            "assigned_reviewer_id",
            "status",
        ),
        Index("ix_identity_review_cases_patient", "patient_id"),
        Index("ix_identity_review_cases_document", "source_document_id"),
    )


class IdentityReviewCaseRouteRecord(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "identity_review_case_routes"

    case_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity_review_cases.id", ondelete="RESTRICT"),
        nullable=False,
    )
    routing_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extraction_routing.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    decision_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extraction_decisions.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extraction_jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_storage.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "case_id", "routing_id", "decision_id", name="uq_identity_review_case_route"
        ),
        Index("ix_identity_review_case_routes_case", "case_id"),
        Index("ix_identity_review_case_routes_job", "job_id"),
    )


class IdentityReviewDispositionRecord(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "identity_review_dispositions"

    case_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity_review_cases.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    reviewer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewer_role: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(48), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)
    prior_case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(192), nullable=False)
    operation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "reviewer_role = 'identity_reviewer'",
            name="ck_identity_review_dispositions_reviewer_role",
        ),
        CheckConstraint(
            "outcome IN ('REJECTED_FOR_BOUND_PATIENT', 'VERIFIED_IDENTITY_REQUIRED', "
            "'SECURITY_ESCALATION_REQUIRED', 'INSUFFICIENT_IDENTITY_EVIDENCE')",
            name="ck_identity_review_dispositions_outcome",
        ),
        CheckConstraint(
            "reason_codes <@ ARRAY["
            "'DOCUMENT_IDENTITY_MISMATCH', 'CANONICAL_IDENTITY_UNAVAILABLE', "
            "'VERIFIED_IDENTIFIER_REQUIRED', 'POSSIBLE_CROSS_PATIENT_DOCUMENT', "
            "'POSSIBLE_PRIVACY_INCIDENT', 'IDENTITY_REVIEW_INCONCLUSIVE', "
            "'DOCUMENT_REJECTED_FOR_BOUND_PATIENT']::varchar[] "
            "AND cardinality(reason_codes) > 0",
            name="ck_identity_review_dispositions_reason_codes",
        ),
        CheckConstraint(
            "((outcome = 'REJECTED_FOR_BOUND_PATIENT' AND reason_codes <@ ARRAY["
            "'DOCUMENT_IDENTITY_MISMATCH', 'POSSIBLE_CROSS_PATIENT_DOCUMENT', "
            "'DOCUMENT_REJECTED_FOR_BOUND_PATIENT']::varchar[]) OR "
            "(outcome IN ('VERIFIED_IDENTITY_REQUIRED', 'INSUFFICIENT_IDENTITY_EVIDENCE') "
            "AND reason_codes <@ ARRAY['CANONICAL_IDENTITY_UNAVAILABLE', "
            "'VERIFIED_IDENTIFIER_REQUIRED', 'IDENTITY_REVIEW_INCONCLUSIVE']::varchar[]) OR "
            "(outcome = 'SECURITY_ESCALATION_REQUIRED' AND reason_codes <@ ARRAY["
            "'POSSIBLE_CROSS_PATIENT_DOCUMENT', 'POSSIBLE_PRIVACY_INCIDENT']::varchar[]))",
            name="ck_identity_review_dispositions_outcome_reasons",
        ),
        CheckConstraint(
            "prior_case_version > 0",
            name="ck_identity_review_dispositions_prior_version_positive",
        ),
        CheckConstraint(
            "char_length(operation_hash) = 64",
            name="ck_identity_review_dispositions_operation_hash_length",
        ),
        UniqueConstraint(
            "case_id",
            "prior_case_version",
            name="uq_identity_review_dispositions_case_version",
        ),
        UniqueConstraint(
            "case_id",
            "idempotency_key",
            name="uq_identity_review_dispositions_idempotency",
        ),
        Index("ix_identity_review_dispositions_reviewer", "reviewer_id"),
    )


class IdentityReviewOperationRecord(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "identity_review_operations"

    case_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity_review_cases.id", ondelete="RESTRICT"),
        nullable=False,
    )
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(192), nullable=False)
    operation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prior_version: Mapped[int] = mapped_column(Integer, nullable=False)
    result_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "operation IN ('CLAIM', 'RECOVER_SESSION', 'SUBMIT_DISPOSITION')",
            name="ck_identity_review_operations_type",
        ),
        CheckConstraint(
            "prior_version > 0 AND result_version > prior_version",
            name="ck_identity_review_operations_versions",
        ),
        CheckConstraint(
            "char_length(operation_hash) = 64",
            name="ck_identity_review_operations_hash_length",
        ),
        UniqueConstraint(
            "case_id",
            "operation",
            "idempotency_key",
            name="uq_identity_review_operations_idempotency",
        ),
        Index("ix_identity_review_operations_case", "case_id"),
        Index("ix_identity_review_operations_actor", "actor_id"),
    )


__all__ = [
    "CreateIdentityReviewCaseRequest",
    "ClaimIdentityReviewCaseRequest",
    "RecoverIdentityReviewSessionRequest",
    "SubmitIdentityReviewDispositionRequest",
    "IdentityReviewCaseStatus",
    "IdentityReviewOutcome",
    "IdentityReviewReasonCode",
    "IdentityReviewMutationOperation",
    "IdentityReviewCaseRecord",
    "IdentityReviewCaseRouteRecord",
    "IdentityReviewDispositionRecord",
    "IdentityReviewOperationRecord",
    "IDENTITY_REVIEW_CONTRACT_VERSION",
    "IDENTITY_REVIEW_POLICY_VERSION",
    "REASONS_BY_OUTCOME",
]
