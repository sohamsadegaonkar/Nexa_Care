"""Canonical, immutable field-level evidence contract for extraction output."""

from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FIELD_EVIDENCE_CONTRACT_VERSION = "1.0"


def _require_timezone(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("evidence timestamps must be timezone-aware")
    return value


class EvidenceGroup(str, Enum):
    IDENTITY = "IDENTITY"
    CLINICAL_VALUE = "CLINICAL_VALUE"
    VISUAL_EVIDENCE = "VISUAL_EVIDENCE"
    MODEL_EVIDENCE = "MODEL_EVIDENCE"
    POLICY_EVIDENCE = "POLICY_EVIDENCE"
    LIFECYCLE = "LIFECYCLE"


class IdentityBindingStatus(str, Enum):
    VERIFIED = "VERIFIED"
    MISMATCH = "MISMATCH"
    AMBIGUOUS = "AMBIGUOUS"
    UNAVAILABLE = "UNAVAILABLE"


class IdentityBindingMethod(str, Enum):
    SERVER_JOB_AND_DOCUMENT = "SERVER_JOB_AND_DOCUMENT"
    DOCUMENT_IDENTITY = "DOCUMENT_IDENTITY"
    EXTERNAL_IDENTITY_ASSERTION = "EXTERNAL_IDENTITY_ASSERTION"
    UNAVAILABLE = "UNAVAILABLE"


class ClinicalRisk(str, Enum):
    LOW_RISK = "LOW_RISK"
    MEDIUM_RISK = "MEDIUM_RISK"
    HIGH_RISK = "HIGH_RISK"
    CRITICAL_RISK = "CRITICAL_RISK"
    UNAVAILABLE = "UNAVAILABLE"


class NormalizationStatus(str, Enum):
    NORMALIZED = "NORMALIZED"
    UNRESOLVED = "UNRESOLVED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNAVAILABLE = "UNAVAILABLE"


class VisualCoverage(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    CONFLICTING = "CONFLICTING"
    UNAVAILABLE = "UNAVAILABLE"


class ConfidenceProvenance(str, Enum):
    PROVIDER_FIELD = "PROVIDER_FIELD"
    INDEPENDENT_VERIFIER = "INDEPENDENT_VERIFIER"
    DERIVED = "DERIVED"
    UNAVAILABLE = "UNAVAILABLE"


class VerifierOutcome(str, Enum):
    AGREED = "AGREED"
    DISAGREED = "DISAGREED"
    ABSTAINED = "ABSTAINED"
    NOT_RUN = "NOT_RUN"
    UNAVAILABLE = "UNAVAILABLE"


class SnapshotState(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    IN_PROGRESS = "IN_PROGRESS"
    NOT_REQUESTED = "NOT_REQUESTED"
    UNKNOWN = "UNKNOWN"


class EvidenceIssue(str, Enum):
    IDENTITY_UNAVAILABLE = "IDENTITY_UNAVAILABLE"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    SOURCE_DOCUMENT_HASH_MISSING = "SOURCE_DOCUMENT_HASH_MISSING"
    FIELD_CONFIDENCE_UNAVAILABLE = "FIELD_CONFIDENCE_UNAVAILABLE"
    INVALID_FIELD_CONFIDENCE = "INVALID_FIELD_CONFIDENCE"
    DOCUMENT_CONFIDENCE_USED_AS_FIELD_CONFIDENCE = (
        "DOCUMENT_CONFIDENCE_USED_AS_FIELD_CONFIDENCE"
    )
    PAGE_UNAVAILABLE = "PAGE_UNAVAILABLE"
    BOUNDING_BOX_UNAVAILABLE = "BOUNDING_BOX_UNAVAILABLE"
    BOUNDING_BOX_INVALID = "BOUNDING_BOX_INVALID"
    SOURCE_TEXT_UNAVAILABLE = "SOURCE_TEXT_UNAVAILABLE"
    PARTIAL_VISUAL_COVERAGE = "PARTIAL_VISUAL_COVERAGE"
    VISUAL_VALUE_MISMATCH = "VISUAL_VALUE_MISMATCH"
    PROVIDER_PROVENANCE_INCOMPLETE = "PROVIDER_PROVENANCE_INCOMPLETE"
    VERIFIER_ABSTAINED = "VERIFIER_ABSTAINED"
    TAMPERED_EVIDENCE = "TAMPERED_EVIDENCE"
    CLINICAL_VALUE_AMBIGUOUS = "CLINICAL_VALUE_AMBIGUOUS"
    PARTIAL_PROVIDER_RESPONSE = "PARTIAL_PROVIDER_RESPONSE"
    POLICY_VERSION_UNAVAILABLE = "POLICY_VERSION_UNAVAILABLE"
    CONSENT_NOT_ACTIVE = "CONSENT_NOT_ACTIVE"
    ERASURE_IN_PROGRESS = "ERASURE_IN_PROGRESS"
    TENANT_BINDING_MISMATCH = "TENANT_BINDING_MISMATCH"
    SUPERSESSION_UNRESOLVED = "SUPERSESSION_UNRESOLVED"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class NormalizedBoundingBox(_FrozenModel):
    """Zero-based page coordinates normalized to the inclusive 0.0-1.0 scale."""

    left: float
    top: float
    right: float
    bottom: float

    @field_validator("left", "top", "right", "bottom")
    @classmethod
    def validate_coordinate(cls, value: float) -> float:
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(
                "bounding-box coordinates must be finite and within 0.0-1.0"
            )
        return value

    @model_validator(mode="after")
    def validate_ordering(self) -> "NormalizedBoundingBox":
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("bounding box must satisfy left < right and top < bottom")
        return self


class IdentityEvidence(_FrozenModel):
    patient_id: str | None = Field(default=None, repr=False)
    tenant_id: str | None = Field(default=None, repr=False)
    organization_id: str | None = Field(default=None, repr=False)
    source_document_id: str | None = None
    source_document_hash: str | None = None
    ingestion_id: str | None = None
    encounter_id: str | None = None
    binding_status: IdentityBindingStatus = IdentityBindingStatus.UNAVAILABLE
    binding_method: IdentityBindingMethod = IdentityBindingMethod.UNAVAILABLE
    issues: frozenset[EvidenceIssue] = frozenset()

    @property
    def is_complete(self) -> bool:
        return (
            self.binding_status is IdentityBindingStatus.VERIFIED
            and bool(self.patient_id)
            and bool(self.tenant_id)
            and bool(self.source_document_id)
            and bool(self.source_document_hash)
            and bool(self.ingestion_id)
        )


class ClinicalValueEvidence(_FrozenModel):
    field_name: str
    raw_value: str
    normalized_value: str | None = None
    raw_unit: str | None = None
    normalized_unit: str | None = None
    reference_range: str | None = None
    effective_at: datetime | None = None
    clinical_risk: ClinicalRisk = ClinicalRisk.UNAVAILABLE
    validation_results: tuple[str, ...] = ()
    normalization_status: NormalizationStatus = NormalizationStatus.UNAVAILABLE
    issues: frozenset[EvidenceIssue] = frozenset()

    @property
    def is_structurally_complete(self) -> bool:
        return (
            bool(self.field_name.strip())
            and bool(self.raw_value.strip())
            and EvidenceIssue.CLINICAL_VALUE_AMBIGUOUS not in self.issues
        )


class VisualEvidence(_FrozenModel):
    """Visual source evidence using zero-based page numbering."""

    page_number: int | None = Field(default=None, ge=0)
    bounding_box: NormalizedBoundingBox | None = None
    source_text: str | None = None
    source_span_start: int | None = Field(default=None, ge=0)
    source_span_end: int | None = Field(default=None, ge=0)
    coverage: VisualCoverage = VisualCoverage.UNAVAILABLE
    issues: frozenset[EvidenceIssue] = frozenset()

    @model_validator(mode="after")
    def validate_span(self) -> "VisualEvidence":
        if (self.source_span_start is None) != (self.source_span_end is None):
            raise ValueError("source span offsets must be supplied together")
        if (
            self.source_span_start is not None
            and self.source_span_end is not None
            and self.source_span_start >= self.source_span_end
        ):
            raise ValueError("source span must satisfy start < end")
        return self

    @property
    def is_complete(self) -> bool:
        return (
            self.page_number is not None
            and self.bounding_box is not None
            and bool(self.source_text)
            and self.coverage is VisualCoverage.COMPLETE
            and not self.issues
        )


class ModelEvidence(_FrozenModel):
    provider_name: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    extracted_at: datetime
    document_confidence: float | None = None
    field_confidence: float | None = None
    field_confidence_source: ConfidenceProvenance = ConfidenceProvenance.UNAVAILABLE
    verifier_outcome: VerifierOutcome = VerifierOutcome.NOT_RUN
    verifier_provider: str | None = None
    verifier_model: str | None = None
    verifier_version: str | None = None
    provider_evidence_hash: str | None = None
    issues: frozenset[EvidenceIssue] = frozenset()

    @field_validator("document_confidence", "field_confidence")
    @classmethod
    def validate_confidence(cls, value: float | None) -> float | None:
        if value is not None and (not math.isfinite(value) or not 0.0 <= value <= 1.0):
            raise ValueError("confidence must be finite and within 0.0-1.0")
        return value

    @field_validator("extracted_at")
    @classmethod
    def validate_extracted_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("evidence timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_confidence_source(self) -> "ModelEvidence":
        if (
            self.field_confidence is None
            and self.field_confidence_source is not ConfidenceProvenance.UNAVAILABLE
        ):
            raise ValueError("missing field confidence must use UNAVAILABLE provenance")
        if (
            self.field_confidence is not None
            and self.field_confidence_source is ConfidenceProvenance.UNAVAILABLE
        ):
            raise ValueError("numeric field confidence requires explicit provenance")
        return self

    @property
    def has_genuine_field_confidence(self) -> bool:
        return self.field_confidence is not None and self.field_confidence_source in {
            ConfidenceProvenance.PROVIDER_FIELD,
            ConfidenceProvenance.INDEPENDENT_VERIFIER,
        }

    @property
    def has_complete_provenance(self) -> bool:
        return bool(self.provider_name and self.model_name and self.model_version)


class PolicyEvidence(_FrozenModel):
    evaluation_occurred: bool = False
    evaluation_id: str | None = None
    policy_version: str | None = None
    evaluated_at: datetime | None = None
    auto_commit_enabled: Literal[False] = False
    issues: frozenset[EvidenceIssue] = frozenset()

    @field_validator("evaluated_at")
    @classmethod
    def validate_evaluated_at(cls, value: datetime | None) -> datetime | None:
        return _require_timezone(value)


class LifecycleEvidence(_FrozenModel):
    job_id: str
    workflow_id: str | None = None
    request_id: str | None = None
    attempt_number: int = Field(ge=1)
    attempt_id: str
    created_at: datetime
    extracted_at: datetime
    source_received_at: datetime | None = None
    partial_provider_response: bool = False
    previous_attempt_id: str | None = None
    supersedes_evidence_id: str | None = None
    addendum_to_evidence_id: str | None = None
    consent_state: SnapshotState = SnapshotState.UNKNOWN
    consent_reference: str | None = None
    erasure_state: SnapshotState = SnapshotState.UNKNOWN
    erasure_reference: str | None = None
    issues: frozenset[EvidenceIssue] = frozenset()

    @field_validator("created_at", "extracted_at", "source_received_at")
    @classmethod
    def validate_timestamp(cls, value: datetime | None) -> datetime | None:
        return _require_timezone(value)


class ExtractedFieldEvidence(_FrozenModel):
    contract_version: Literal["1.0"] = FIELD_EVIDENCE_CONTRACT_VERSION
    evidence_id: str
    identity: IdentityEvidence
    clinical_value: ClinicalValueEvidence
    visual: VisualEvidence
    model: ModelEvidence
    policy: PolicyEvidence
    lifecycle: LifecycleEvidence

    @property
    def is_partial_response(self) -> bool:
        return self.lifecycle.partial_provider_response

    @property
    def identity_complete(self) -> bool:
        return self.identity.is_complete

    @property
    def clinical_value_complete(self) -> bool:
        return self.clinical_value.is_structurally_complete

    @property
    def visual_evidence_complete(self) -> bool:
        return self.visual.is_complete

    @property
    def genuine_field_confidence_present(self) -> bool:
        return self.model.has_genuine_field_confidence

    @property
    def model_provenance_complete(self) -> bool:
        return self.model.has_complete_provenance

    @property
    def issue_codes(self) -> frozenset[EvidenceIssue]:
        issues = set().union(
            self.identity.issues,
            self.clinical_value.issues,
            self.visual.issues,
            self.model.issues,
            self.policy.issues,
            self.lifecycle.issues,
        )
        if not self.identity.is_complete:
            issues.add(EvidenceIssue.IDENTITY_UNAVAILABLE)
        if not self.identity.source_document_hash:
            issues.add(EvidenceIssue.SOURCE_DOCUMENT_HASH_MISSING)
        if self.model.field_confidence is None:
            issues.add(EvidenceIssue.FIELD_CONFIDENCE_UNAVAILABLE)
        if self.visual.page_number is None:
            issues.add(EvidenceIssue.PAGE_UNAVAILABLE)
        if self.visual.bounding_box is None:
            issues.add(EvidenceIssue.BOUNDING_BOX_UNAVAILABLE)
        if not self.visual.source_text:
            issues.add(EvidenceIssue.SOURCE_TEXT_UNAVAILABLE)
        if not self.model.has_complete_provenance:
            issues.add(EvidenceIssue.PROVIDER_PROVENANCE_INCOMPLETE)
        if self.model.verifier_outcome is VerifierOutcome.ABSTAINED:
            issues.add(EvidenceIssue.VERIFIER_ABSTAINED)
        if self.lifecycle.partial_provider_response:
            issues.add(EvidenceIssue.PARTIAL_PROVIDER_RESPONSE)
        if self.policy.evaluation_occurred and not self.policy.policy_version:
            issues.add(EvidenceIssue.POLICY_VERSION_UNAVAILABLE)
        if self.lifecycle.consent_state is SnapshotState.INACTIVE:
            issues.add(EvidenceIssue.CONSENT_NOT_ACTIVE)
        if self.lifecycle.erasure_state is SnapshotState.IN_PROGRESS:
            issues.add(EvidenceIssue.ERASURE_IN_PROGRESS)
        return frozenset(issues)

    @property
    def has_blocking_issues(self) -> bool:
        return bool(self.issue_codes)
