"""Immutable typed contracts for human adjudication of archived sources."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ADJUDICATION_CONTRACT_VERSION = "1.0"
ADJUDICATION_POLICY_VERSION = "source-adjudication/1.0"
REVIEW_SESSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,95}$"
IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,191}$"


class AdjudicationOutcome(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    NEEDS_SPECIALIST_REVIEW = "NEEDS_SPECIALIST_REVIEW"
    SUPERSEDED = "SUPERSEDED"


class AdjudicationReasonCode(StrEnum):
    SOURCE_VERIFIED = "SOURCE_VERIFIED"
    MANUAL_TRANSCRIPTION = "MANUAL_TRANSCRIPTION"
    CORRECTED_AGAINST_SOURCE = "CORRECTED_AGAINST_SOURCE"
    NOT_CLINICAL_DATA = "NOT_CLINICAL_DATA"
    ILLEGIBLE_SOURCE = "ILLEGIBLE_SOURCE"
    DUPLICATE_OBSERVATION = "DUPLICATE_OBSERVATION"
    SOURCE_MISMATCH = "SOURCE_MISMATCH"
    SPECIALIST_INTERPRETATION_REQUIRED = "SPECIALIST_INTERPRETATION_REQUIRED"
    AMBIGUOUS_SOURCE = "AMBIGUOUS_SOURCE"
    OUT_OF_SUPPORTED_SCOPE = "OUT_OF_SUPPORTED_SCOPE"


_REASONS_BY_OUTCOME = {
    AdjudicationOutcome.ACCEPTED: frozenset(
        {
            AdjudicationReasonCode.SOURCE_VERIFIED,
            AdjudicationReasonCode.MANUAL_TRANSCRIPTION,
            AdjudicationReasonCode.CORRECTED_AGAINST_SOURCE,
        }
    ),
    AdjudicationOutcome.REJECTED: frozenset(
        {
            AdjudicationReasonCode.NOT_CLINICAL_DATA,
            AdjudicationReasonCode.ILLEGIBLE_SOURCE,
            AdjudicationReasonCode.DUPLICATE_OBSERVATION,
            AdjudicationReasonCode.SOURCE_MISMATCH,
        }
    ),
    AdjudicationOutcome.NEEDS_SPECIALIST_REVIEW: frozenset(
        {
            AdjudicationReasonCode.SPECIALIST_INTERPRETATION_REQUIRED,
            AdjudicationReasonCode.AMBIGUOUS_SOURCE,
            AdjudicationReasonCode.OUT_OF_SUPPORTED_SCOPE,
        }
    ),
}


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class VitalClinicalField(_FrozenModel):
    kind: Literal["VITAL"] = "VITAL"
    vital_type: Literal[
        "HEART_RATE",
        "TEMPERATURE",
        "SPO2",
        "RESPIRATORY_RATE",
    ]
    reviewer_entered_value: float
    normalized_value: float
    unit: str = Field(min_length=1, max_length=32)
    effective_at: datetime
    page_number: int | None = Field(default=None, ge=0)
    provenance_type: Literal["HUMAN_TRANSCRIBED", "HUMAN_VERIFIED"]

    @field_validator("reviewer_entered_value", "normalized_value")
    @classmethod
    def finite_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("clinical numeric value must be finite")
        return value

    @model_validator(mode="after")
    def no_unapproved_normalization(self) -> "VitalClinicalField":
        if self.reviewer_entered_value != self.normalized_value:
            raise ValueError("vital normalization requires an authoritative converter")
        return self

    @field_validator("effective_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("clinical timestamp must be timezone-aware")
        return value


class LabClinicalField(_FrozenModel):
    kind: Literal["LAB_RESULT"] = "LAB_RESULT"
    test_name: str = Field(min_length=1, max_length=128)
    reviewer_entered_value: float
    normalized_value: float
    unit: str = Field(min_length=1, max_length=32)
    reference_range: str = Field(min_length=1, max_length=64)
    is_abnormal: bool
    effective_at: datetime
    page_number: int | None = Field(default=None, ge=0)
    provenance_type: Literal["HUMAN_TRANSCRIBED", "HUMAN_VERIFIED"]

    @field_validator("reviewer_entered_value", "normalized_value")
    @classmethod
    def finite_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("clinical numeric value must be finite")
        return value

    @model_validator(mode="after")
    def no_unapproved_normalization(self) -> "LabClinicalField":
        if self.reviewer_entered_value != self.normalized_value:
            raise ValueError("lab normalization requires an authoritative converter")
        return self

    @field_validator("effective_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("clinical timestamp must be timezone-aware")
        return value


AdjudicatedClinicalField = Annotated[
    VitalClinicalField | LabClinicalField, Field(discriminator="kind")
]


class AdjudicationCase(_FrozenModel):
    case_id: str
    patient_id: str
    tenant_id: str
    organization_id: str
    source_document_id: str
    job_id: str
    routing_id: str | None
    decision_id: str | None
    reviewer_id: str
    reviewer_organization_id: str
    reviewer_role: str
    review_session_id: str = Field(
        min_length=8, max_length=96, pattern=REVIEW_SESSION_PATTERN
    )
    status: AdjudicationOutcome = AdjudicationOutcome.PENDING
    version: int = Field(ge=1)
    created_at: datetime
    contract_version: str = ADJUDICATION_CONTRACT_VERSION
    policy_version: str = ADJUDICATION_POLICY_VERSION


class AdjudicationSubmission(_FrozenModel):
    submission_id: str
    case_id: str
    patient_id: str
    tenant_id: str
    source_document_id: str
    job_id: str
    routing_id: str | None
    decision_id: str | None
    reviewer_id: str
    reviewer_organization_id: str
    reviewer_role: str
    review_session_id: str = Field(
        min_length=8, max_length=96, pattern=REVIEW_SESSION_PATTERN
    )
    attempt_number: int = Field(ge=1)
    outcome: AdjudicationOutcome
    fields: tuple[AdjudicatedClinicalField, ...] = ()
    resolved_conflict_ids: tuple[UUID, ...] = ()
    supersedes_submission_id: str | None = None
    submitted_at: datetime
    resolved_at: datetime
    contract_version: str = ADJUDICATION_CONTRACT_VERSION
    policy_version: str = ADJUDICATION_POLICY_VERSION
    reason_codes: tuple[AdjudicationReasonCode, ...] = Field(default=(), max_length=4)
    content_hash: str = Field(default="", pattern=r"^(|[0-9a-f]{64})$")

    @model_validator(mode="after")
    def validate_outcome_payload(self) -> "AdjudicationSubmission":
        if self.outcome is AdjudicationOutcome.ACCEPTED and not self.fields:
            raise ValueError("accepted adjudication requires structured fields")
        if self.outcome is not AdjudicationOutcome.ACCEPTED and self.fields:
            raise ValueError("non-accepted adjudication cannot carry clinical fields")
        if (
            self.outcome is not AdjudicationOutcome.ACCEPTED
            and self.resolved_conflict_ids
        ):
            raise ValueError("only accepted adjudication can resolve conflicts")
        if len(set(self.resolved_conflict_ids)) != len(self.resolved_conflict_ids):
            raise ValueError("duplicate conflict resolution")
        if self.outcome is AdjudicationOutcome.PENDING:
            raise ValueError("pending is a case state, not a submission outcome")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("duplicate adjudication reason code")
        allowed = _REASONS_BY_OUTCOME.get(self.outcome, frozenset())
        if any(reason not in allowed for reason in self.reason_codes):
            raise ValueError("reason code is invalid for adjudication outcome")
        return self

    def canonical_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude={"content_hash"})
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


class AdjudicationOutcomeRecord(_FrozenModel):
    case_id: str
    submission_id: str
    outcome: AdjudicationOutcome
    resolved_at: datetime
    committed_at: datetime | None = None
