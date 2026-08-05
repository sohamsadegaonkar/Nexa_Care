"""Immutable contracts for pure extraction-evidence lane decisions."""

from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DECISION_CONTRACT_VERSION = "1.0"
DECISION_POLICY_VERSION = "extraction-decision-policy/1.0"
DECISION_EVALUATOR_VERSION = "extraction-decision-engine/1.0"
RUNTIME_AUTO_COMMIT_ENABLED: Final = False


class DecisionLane(str, Enum):
    AUTO_COMMIT = "AUTO_COMMIT"
    SOURCE_ONLY = "SOURCE_ONLY"
    QUARANTINE = "QUARANTINE"


class DecisionReason(str, Enum):
    ELIGIBLE_UNDER_POLICY = "ELIGIBLE_UNDER_POLICY"
    AUTO_COMMIT_DISABLED = "AUTO_COMMIT_DISABLED"
    FIELD_CONFIDENCE_UNAVAILABLE = "FIELD_CONFIDENCE_UNAVAILABLE"
    FIELD_CONFIDENCE_BELOW_POLICY = "FIELD_CONFIDENCE_BELOW_POLICY"
    VISUAL_EVIDENCE_INCOMPLETE = "VISUAL_EVIDENCE_INCOMPLETE"
    MODEL_PROVENANCE_INCOMPLETE = "MODEL_PROVENANCE_INCOMPLETE"
    VERIFIER_NOT_RUN = "VERIFIER_NOT_RUN"
    VERIFIER_ABSTAINED = "VERIFIER_ABSTAINED"
    VERIFIER_DISAGREED = "VERIFIER_DISAGREED"
    IDENTITY_UNAVAILABLE = "IDENTITY_UNAVAILABLE"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    TENANT_BINDING_MISMATCH = "TENANT_BINDING_MISMATCH"
    SOURCE_DOCUMENT_HASH_MISSING = "SOURCE_DOCUMENT_HASH_MISSING"
    CLINICAL_VALUE_AMBIGUOUS = "CLINICAL_VALUE_AMBIGUOUS"
    CLINICAL_RISK_REQUIRES_REVIEW = "CLINICAL_RISK_REQUIRES_REVIEW"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    TAMPERED_EVIDENCE = "TAMPERED_EVIDENCE"
    PARTIAL_PROVIDER_RESPONSE = "PARTIAL_PROVIDER_RESPONSE"
    CONSENT_NOT_ACTIVE = "CONSENT_NOT_ACTIVE"
    ERASURE_IN_PROGRESS = "ERASURE_IN_PROGRESS"
    POLICY_VERSION_UNAVAILABLE = "POLICY_VERSION_UNAVAILABLE"
    POLICY_VERSION_UNSUPPORTED = "POLICY_VERSION_UNSUPPORTED"
    EVIDENCE_CONTRACT_UNSUPPORTED = "EVIDENCE_CONTRACT_UNSUPPORTED"
    SUPERSESSION_UNRESOLVED = "SUPERSESSION_UNRESOLVED"
    DECISION_INPUT_INVALID = "DECISION_INPUT_INVALID"
    ELIGIBILITY_CLASSIFICATION_FAILED = "ELIGIBILITY_CLASSIFICATION_FAILED"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class ExtractionDecisionPolicy(_FrozenModel):
    """One immutable policy snapshot, including expected security bindings."""

    policy_version: str = DECISION_POLICY_VERSION
    auto_commit_enabled: bool = RUNTIME_AUTO_COMMIT_ENABLED
    accepted_evidence_contract_versions: frozenset[str] = frozenset({"1.0"})
    patient_id: str = Field(min_length=1, repr=False)
    tenant_id: str = Field(min_length=1, repr=False)
    organization_id: str = Field(min_length=1, repr=False)
    source_document_id: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    require_verified_identity: bool = True
    require_complete_visual_evidence: bool = True
    require_complete_model_provenance: bool = True
    require_verifier_agreement: bool = True
    permitted_clinical_risks: frozenset[str] = frozenset({"LOW_RISK"})
    require_validation_success: bool = True
    minimum_field_confidence: float | None = None
    force_quarantine: bool = False

    @field_validator("minimum_field_confidence")
    @classmethod
    def validate_threshold(cls, value: float | None) -> float | None:
        if value is not None and (not math.isfinite(value) or not 0.0 <= value <= 1.0):
            raise ValueError("policy confidence must be finite and within 0.0-1.0")
        return value


class ExtractionDecision(_FrozenModel):
    decision_id: str = Field(min_length=1)
    decision_contract_version: str = DECISION_CONTRACT_VERSION
    evidence_contract_version: str
    evidence_id: str
    patient_id: str = Field(repr=False)
    tenant_id: str = Field(repr=False)
    organization_id: str = Field(repr=False)
    source_document_id: str
    job_id: str
    workflow_id: str
    request_id: str
    attempt_id: str
    lane: DecisionLane
    reasons: tuple[DecisionReason, ...]
    policy_version: str
    policy_configuration_hash: str
    evidence_digest: str
    evaluated_at: datetime
    auto_commit_feature_enabled: bool
    supersedes_earlier_decision: bool = False
    earlier_decision_id: str | None = None
    evaluator_version: str = DECISION_EVALUATOR_VERSION

    @field_validator("evaluated_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("decision timestamp must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_supersession(self) -> "ExtractionDecision":
        if self.supersedes_earlier_decision != (self.earlier_decision_id is not None):
            raise ValueError("decision supersession state is inconsistent")
        return self
