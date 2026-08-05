"""Pure, deterministic, fail-closed evaluation of canonical field evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime

from pydantic import ValidationError

from app.models.extraction_decision import (
    DECISION_CONTRACT_VERSION,
    DECISION_EVALUATOR_VERSION,
    DECISION_POLICY_VERSION,
    DecisionLane,
    DecisionReason,
    ExtractionDecision,
    ExtractionDecisionPolicy,
)
from app.models.field_evidence import (
    FIELD_EVIDENCE_CONTRACT_VERSION,
    EvidenceIssue,
    ExtractedFieldEvidence,
    IdentityBindingStatus,
    NormalizationStatus,
    SnapshotState,
    VerifierOutcome,
    VisualCoverage,
)

_SUPPORTED_POLICY_VERSIONS = frozenset({DECISION_POLICY_VERSION})

_QUARANTINE_ISSUES: tuple[tuple[EvidenceIssue, DecisionReason], ...] = (
    (EvidenceIssue.TENANT_BINDING_MISMATCH, DecisionReason.TENANT_BINDING_MISMATCH),
    (EvidenceIssue.IDENTITY_MISMATCH, DecisionReason.IDENTITY_MISMATCH),
    (EvidenceIssue.IDENTITY_UNAVAILABLE, DecisionReason.IDENTITY_UNAVAILABLE),
    (EvidenceIssue.TAMPERED_EVIDENCE, DecisionReason.TAMPERED_EVIDENCE),
    (
        EvidenceIssue.DOCUMENT_CONFIDENCE_USED_AS_FIELD_CONFIDENCE,
        DecisionReason.TAMPERED_EVIDENCE,
    ),
    (EvidenceIssue.INVALID_FIELD_CONFIDENCE, DecisionReason.DECISION_INPUT_INVALID),
    (EvidenceIssue.PARTIAL_PROVIDER_RESPONSE, DecisionReason.PARTIAL_PROVIDER_RESPONSE),
    (EvidenceIssue.CONSENT_NOT_ACTIVE, DecisionReason.CONSENT_NOT_ACTIVE),
    (EvidenceIssue.ERASURE_IN_PROGRESS, DecisionReason.ERASURE_IN_PROGRESS),
    (EvidenceIssue.SUPERSESSION_UNRESOLVED, DecisionReason.SUPERSESSION_UNRESOLVED),
    (
        EvidenceIssue.POLICY_VERSION_UNAVAILABLE,
        DecisionReason.POLICY_VERSION_UNAVAILABLE,
    ),
    (
        EvidenceIssue.SOURCE_DOCUMENT_HASH_MISSING,
        DecisionReason.SOURCE_DOCUMENT_HASH_MISSING,
    ),
    (EvidenceIssue.VISUAL_VALUE_MISMATCH, DecisionReason.VALIDATION_FAILED),
)


def _canonical_digest(model: object) -> str:
    def normalize(value):
        if isinstance(value, dict):
            return {key: normalize(value[key]) for key in sorted(value)}
        if isinstance(value, (list, tuple, set, frozenset)):
            normalized = [normalize(item) for item in value]
            return sorted(
                normalized,
                key=lambda item: json.dumps(
                    item, sort_keys=True, separators=(",", ":"), ensure_ascii=True
                ),
            )
        return value

    canonical = normalize(
        model.model_dump(mode="json", by_alias=True, exclude_none=False)  # type: ignore[attr-defined]
    )
    payload = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _revalidate(model: object, model_type: type):
    """Round-trip through JSON so construct/copy cannot bypass validation."""
    return model_type.model_validate_json(model.model_dump_json())  # type: ignore[attr-defined]


def _invalid_decision(
    *,
    policy: ExtractionDecisionPolicy,
    reason: DecisionReason,
    decision_id: str,
    evaluated_at: datetime,
    earlier_decision_id: str | None,
) -> ExtractionDecision:
    return ExtractionDecision(
        decision_id=decision_id,
        decision_contract_version=DECISION_CONTRACT_VERSION,
        evidence_contract_version=FIELD_EVIDENCE_CONTRACT_VERSION,
        evidence_id=policy.evidence_id,
        patient_id=policy.patient_id,
        tenant_id=policy.tenant_id,
        organization_id=policy.organization_id,
        source_document_id=policy.source_document_id,
        job_id=policy.job_id,
        workflow_id=policy.workflow_id,
        request_id=policy.request_id,
        attempt_id=policy.attempt_id,
        lane=DecisionLane.QUARANTINE,
        reasons=(reason,),
        policy_version=policy.policy_version,
        policy_configuration_hash=_canonical_digest(policy),
        evidence_digest=hashlib.sha256(b"invalid-evidence").hexdigest(),
        evaluated_at=evaluated_at,
        auto_commit_feature_enabled=policy.auto_commit_enabled,
        supersedes_earlier_decision=earlier_decision_id is not None,
        earlier_decision_id=earlier_decision_id,
        evaluator_version=DECISION_EVALUATOR_VERSION,
    )


def evaluate_extraction_evidence(
    *,
    evidence: ExtractedFieldEvidence,
    policy: ExtractionDecisionPolicy,
    decision_id_factory: Callable[[], str],
    evaluated_at: datetime,
    earlier_decision_id: str | None = None,
) -> ExtractionDecision:
    """Return a lane decision without I/O, persistence, logging, or mutation."""
    decision_id = decision_id_factory()
    try:
        validated_policy = _revalidate(policy, ExtractionDecisionPolicy)
    except (AttributeError, TypeError, ValueError, ValidationError):
        unavailable = "UNAVAILABLE"
        return ExtractionDecision(
            decision_id=decision_id,
            evidence_contract_version=FIELD_EVIDENCE_CONTRACT_VERSION,
            evidence_id=unavailable,
            patient_id=unavailable,
            tenant_id=unavailable,
            organization_id=unavailable,
            source_document_id=unavailable,
            job_id=unavailable,
            workflow_id=unavailable,
            request_id=unavailable,
            attempt_id=unavailable,
            lane=DecisionLane.QUARANTINE,
            reasons=(DecisionReason.DECISION_INPUT_INVALID,),
            policy_version=unavailable,
            policy_configuration_hash=hashlib.sha256(b"invalid-policy").hexdigest(),
            evidence_digest=hashlib.sha256(b"unevaluated-evidence").hexdigest(),
            evaluated_at=evaluated_at,
            auto_commit_feature_enabled=False,
            supersedes_earlier_decision=earlier_decision_id is not None,
            earlier_decision_id=earlier_decision_id,
        )

    if not validated_policy.policy_version:
        return _invalid_decision(
            policy=validated_policy,
            reason=DecisionReason.POLICY_VERSION_UNAVAILABLE,
            decision_id=decision_id,
            evaluated_at=evaluated_at,
            earlier_decision_id=earlier_decision_id,
        )
    if validated_policy.policy_version not in _SUPPORTED_POLICY_VERSIONS:
        return _invalid_decision(
            policy=validated_policy,
            reason=DecisionReason.POLICY_VERSION_UNSUPPORTED,
            decision_id=decision_id,
            evaluated_at=evaluated_at,
            earlier_decision_id=earlier_decision_id,
        )

    raw_contract_version = getattr(evidence, "contract_version", None)
    if (
        raw_contract_version != FIELD_EVIDENCE_CONTRACT_VERSION
        or raw_contract_version
        not in validated_policy.accepted_evidence_contract_versions
    ):
        return _invalid_decision(
            policy=validated_policy,
            reason=DecisionReason.EVIDENCE_CONTRACT_UNSUPPORTED,
            decision_id=decision_id,
            evaluated_at=evaluated_at,
            earlier_decision_id=earlier_decision_id,
        )
    try:
        validated = _revalidate(evidence, ExtractedFieldEvidence)
    except (AttributeError, TypeError, ValueError, ValidationError):
        return _invalid_decision(
            policy=validated_policy,
            reason=DecisionReason.DECISION_INPUT_INVALID,
            decision_id=decision_id,
            evaluated_at=evaluated_at,
            earlier_decision_id=earlier_decision_id,
        )

    quarantine: list[DecisionReason] = []
    source_only: list[DecisionReason] = []
    if validated_policy.force_quarantine:
        quarantine.append(DecisionReason.ELIGIBILITY_CLASSIFICATION_FAILED)
    issues = validated.issue_codes

    identity = validated.identity
    if (
        identity.patient_id != validated_policy.patient_id
        or identity.source_document_id != validated_policy.source_document_id
        or validated.evidence_id != validated_policy.evidence_id
        or validated.lifecycle.job_id != validated_policy.job_id
        or validated.lifecycle.workflow_id != validated_policy.workflow_id
        or validated.lifecycle.request_id != validated_policy.request_id
        or validated.lifecycle.attempt_id != validated_policy.attempt_id
    ):
        quarantine.append(DecisionReason.IDENTITY_MISMATCH)
    if identity.tenant_id != validated_policy.tenant_id:
        quarantine.append(DecisionReason.TENANT_BINDING_MISMATCH)
    if identity.organization_id != validated_policy.organization_id:
        quarantine.append(DecisionReason.IDENTITY_MISMATCH)
    if identity.binding_status is IdentityBindingStatus.MISMATCH:
        quarantine.append(DecisionReason.IDENTITY_MISMATCH)
    elif (
        validated_policy.require_verified_identity
        and identity.binding_status is not IdentityBindingStatus.VERIFIED
    ):
        quarantine.append(DecisionReason.IDENTITY_UNAVAILABLE)
    if not identity.source_document_hash:
        quarantine.append(DecisionReason.SOURCE_DOCUMENT_HASH_MISSING)

    for issue, reason in _QUARANTINE_ISSUES:
        if issue in issues:
            quarantine.append(reason)
    if validated.lifecycle.partial_provider_response:
        quarantine.append(DecisionReason.PARTIAL_PROVIDER_RESPONSE)
    if validated.lifecycle.consent_state is not SnapshotState.ACTIVE:
        quarantine.append(DecisionReason.CONSENT_NOT_ACTIVE)
    if validated.lifecycle.erasure_state in {
        SnapshotState.IN_PROGRESS,
        SnapshotState.ACTIVE,
    }:
        quarantine.append(DecisionReason.ERASURE_IN_PROGRESS)
    if validated.model.verifier_outcome is VerifierOutcome.DISAGREED:
        quarantine.append(DecisionReason.VERIFIER_DISAGREED)
    if validated.visual.coverage is VisualCoverage.CONFLICTING:
        quarantine.append(DecisionReason.VALIDATION_FAILED)
    if validated.lifecycle.supersedes_evidence_id and not earlier_decision_id:
        quarantine.append(DecisionReason.SUPERSESSION_UNRESOLVED)
    if validated.policy.evaluation_occurred:
        if not validated.policy.policy_version:
            quarantine.append(DecisionReason.POLICY_VERSION_UNAVAILABLE)
        elif validated.policy.policy_version != validated_policy.policy_version:
            quarantine.append(DecisionReason.POLICY_VERSION_UNSUPPORTED)

    if (
        validated.model.field_confidence is None
        or not validated.model.has_genuine_field_confidence
        or EvidenceIssue.FIELD_CONFIDENCE_UNAVAILABLE in issues
    ):
        source_only.append(DecisionReason.FIELD_CONFIDENCE_UNAVAILABLE)
    elif (
        validated_policy.minimum_field_confidence is not None
        and validated.model.field_confidence < validated_policy.minimum_field_confidence
    ):
        source_only.append(DecisionReason.FIELD_CONFIDENCE_BELOW_POLICY)
    if validated_policy.require_complete_visual_evidence and (
        not validated.visual.is_complete
        or validated.visual.coverage is not VisualCoverage.COMPLETE
    ):
        source_only.append(DecisionReason.VISUAL_EVIDENCE_INCOMPLETE)
    if validated_policy.require_complete_model_provenance and (
        not validated.model.has_complete_provenance
        or EvidenceIssue.PROVIDER_PROVENANCE_INCOMPLETE in issues
    ):
        source_only.append(DecisionReason.MODEL_PROVENANCE_INCOMPLETE)
    if validated_policy.require_verifier_agreement:
        if validated.model.verifier_outcome in {
            VerifierOutcome.NOT_RUN,
            VerifierOutcome.UNAVAILABLE,
        }:
            source_only.append(DecisionReason.VERIFIER_NOT_RUN)
        elif (
            validated.model.verifier_outcome is VerifierOutcome.ABSTAINED
            or EvidenceIssue.VERIFIER_ABSTAINED in issues
        ):
            source_only.append(DecisionReason.VERIFIER_ABSTAINED)
    if (
        validated.clinical_value.normalization_status is NormalizationStatus.UNRESOLVED
        or EvidenceIssue.CLINICAL_VALUE_AMBIGUOUS in issues
    ):
        source_only.append(DecisionReason.CLINICAL_VALUE_AMBIGUOUS)
    if (
        validated.clinical_value.clinical_risk.value
        not in validated_policy.permitted_clinical_risks
    ):
        source_only.append(DecisionReason.CLINICAL_RISK_REQUIRES_REVIEW)
    if (
        validated_policy.require_validation_success
        and validated.clinical_value.validation_results
    ):
        quarantine.append(DecisionReason.VALIDATION_FAILED)
    if not validated_policy.auto_commit_enabled:
        source_only.append(DecisionReason.AUTO_COMMIT_DISABLED)

    if quarantine:
        lane = DecisionLane.QUARANTINE
        reasons = tuple(dict.fromkeys(quarantine))
    elif source_only:
        lane = DecisionLane.SOURCE_ONLY
        reasons = tuple(dict.fromkeys(source_only))
    else:
        lane = DecisionLane.AUTO_COMMIT
        reasons = (DecisionReason.ELIGIBLE_UNDER_POLICY,)

    return ExtractionDecision(
        decision_id=decision_id,
        evidence_contract_version=validated.contract_version,
        evidence_id=validated.evidence_id,
        patient_id=identity.patient_id or validated_policy.patient_id,
        tenant_id=identity.tenant_id or validated_policy.tenant_id,
        organization_id=identity.organization_id or validated_policy.organization_id,
        source_document_id=identity.source_document_id
        or validated_policy.source_document_id,
        job_id=validated.lifecycle.job_id,
        workflow_id=validated.lifecycle.workflow_id or validated_policy.workflow_id,
        request_id=validated.lifecycle.request_id or validated_policy.request_id,
        attempt_id=validated.lifecycle.attempt_id,
        lane=lane,
        reasons=reasons,
        policy_version=validated_policy.policy_version,
        policy_configuration_hash=_canonical_digest(validated_policy),
        evidence_digest=_canonical_digest(validated),
        evaluated_at=evaluated_at,
        auto_commit_feature_enabled=validated_policy.auto_commit_enabled,
        supersedes_earlier_decision=earlier_decision_id is not None,
        earlier_decision_id=earlier_decision_id,
    )
