"""Immutable typed contracts for human adjudication of archived sources."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ADJUDICATION_CONTRACT_VERSION = "1.0"
ADJUDICATION_POLICY_VERSION = "source-adjudication/1.0"


class AdjudicationOutcome(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    NEEDS_SPECIALIST_REVIEW = "NEEDS_SPECIALIST_REVIEW"
    SUPERSEDED = "SUPERSEDED"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class VitalClinicalField(_FrozenModel):
    kind: Literal["VITAL"] = "VITAL"
    vital_type: Literal[
        "BLOOD_PRESSURE",
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
    review_session_id: str
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
    review_session_id: str
    attempt_number: int = Field(ge=1)
    outcome: AdjudicationOutcome
    fields: tuple[AdjudicatedClinicalField, ...] = ()
    supersedes_submission_id: str | None = None
    submitted_at: datetime
    resolved_at: datetime
    contract_version: str = ADJUDICATION_CONTRACT_VERSION
    policy_version: str = ADJUDICATION_POLICY_VERSION
    reason_codes: tuple[str, ...] = ()
    content_hash: str = ""

    @model_validator(mode="after")
    def validate_outcome_payload(self) -> "AdjudicationSubmission":
        if self.outcome is AdjudicationOutcome.ACCEPTED and not self.fields:
            raise ValueError("accepted adjudication requires structured fields")
        if self.outcome is not AdjudicationOutcome.ACCEPTED and self.fields:
            raise ValueError("non-accepted adjudication cannot carry clinical fields")
        if self.outcome is AdjudicationOutcome.PENDING:
            raise ValueError("pending is a case state, not a submission outcome")
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
