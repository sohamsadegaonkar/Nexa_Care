"""Adversarial coverage for the production three-lane decision engine."""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import count

import pytest
from pydantic import ValidationError

from app.models.extraction_decision import (
    DECISION_POLICY_VERSION,
    DecisionLane,
    DecisionReason,
    ExtractionDecisionPolicy,
)
from app.models.field_evidence import (
    ClinicalRisk,
    ClinicalValueEvidence,
    ConfidenceProvenance,
    EvidenceIssue,
    ExtractedFieldEvidence,
    IdentityBindingMethod,
    IdentityBindingStatus,
    IdentityEvidence,
    LifecycleEvidence,
    ModelEvidence,
    NormalizationStatus,
    NormalizedBoundingBox,
    PolicyEvidence,
    SnapshotState,
    VerifierOutcome,
    VisualCoverage,
    VisualEvidence,
)
from app.services.extraction_decision_engine import evaluate_extraction_evidence

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def _evidence() -> ExtractedFieldEvidence:
    return ExtractedFieldEvidence(
        evidence_id="evidence-1",
        identity=IdentityEvidence(
            patient_id="patient-1",
            tenant_id="tenant-1",
            organization_id="organization-1",
            source_document_id="document-1",
            source_document_hash="a" * 64,
            ingestion_id="ingestion-1",
            binding_status=IdentityBindingStatus.VERIFIED,
            binding_method=IdentityBindingMethod.SERVER_JOB_AND_DOCUMENT,
        ),
        clinical_value=ClinicalValueEvidence(
            field_name="synthetic_low_risk_field",
            raw_value="synthetic-value",
            normalized_value="synthetic-value",
            clinical_risk=ClinicalRisk.LOW_RISK,
            normalization_status=NormalizationStatus.NORMALIZED,
        ),
        visual=VisualEvidence(
            page_number=0,
            bounding_box=NormalizedBoundingBox(
                left=0.1, top=0.1, right=0.2, bottom=0.2
            ),
            source_text="synthetic-value",
            source_span_start=0,
            source_span_end=15,
            coverage=VisualCoverage.COMPLETE,
        ),
        model=ModelEvidence(
            provider_name="synthetic-provider",
            model_name="synthetic-model",
            model_version="1",
            extracted_at=NOW,
            document_confidence=0.99,
            field_confidence=0.99,
            field_confidence_source=ConfidenceProvenance.PROVIDER_FIELD,
            verifier_outcome=VerifierOutcome.AGREED,
            verifier_provider="synthetic-verifier",
            verifier_model="synthetic-verifier-model",
            verifier_version="1",
            provider_evidence_hash="b" * 64,
        ),
        policy=PolicyEvidence(auto_commit_enabled=False),
        lifecycle=LifecycleEvidence(
            job_id="job-1",
            workflow_id="workflow-1",
            request_id="request-1",
            attempt_number=1,
            attempt_id="attempt-1",
            created_at=NOW,
            extracted_at=NOW,
            source_received_at=NOW,
            consent_state=SnapshotState.ACTIVE,
            erasure_state=SnapshotState.NOT_REQUESTED,
        ),
    )


def _policy(*, enabled: bool = False, **updates) -> ExtractionDecisionPolicy:
    values = {
        "auto_commit_enabled": enabled,
        "patient_id": "patient-1",
        "tenant_id": "tenant-1",
        "organization_id": "organization-1",
        "source_document_id": "document-1",
        "evidence_id": "evidence-1",
        "job_id": "job-1",
        "workflow_id": "workflow-1",
        "request_id": "request-1",
        "attempt_id": "attempt-1",
    }
    values.update(updates)
    return ExtractionDecisionPolicy(**values)


def _evaluate(evidence=None, policy=None, *, decision_id="decision-1", earlier=None):
    return evaluate_extraction_evidence(
        evidence=evidence or _evidence(),
        policy=policy or _policy(),
        decision_id_factory=lambda: decision_id,
        evaluated_at=NOW,
        earlier_decision_id=earlier,
    )


def _with(evidence: ExtractedFieldEvidence, **updates) -> ExtractedFieldEvidence:
    return evidence.model_copy(update=updates)


def test_identical_inputs_have_identical_substantive_decision_and_hashes():
    first = _evaluate(decision_id="decision-a")
    second = _evaluate(decision_id="decision-b")
    assert (first.lane, first.reasons) == (second.lane, second.reasons)
    assert first.evidence_digest == second.evidence_digest
    assert first.policy_configuration_hash == second.policy_configuration_hash


def test_candidate_classification_failure_forces_quarantine_without_exception_text():
    decision = _evaluate(
        policy=_policy(force_quarantine=True),
    )
    assert decision.lane is DecisionLane.QUARANTINE
    assert decision.reasons == (DecisionReason.ELIGIBILITY_CLASSIFICATION_FAILED,)


def test_invalid_constructed_evidence_fails_closed_without_sensitive_reason_text():
    invalid_model = ModelEvidence.model_construct(
        extracted_at=NOW,
        field_confidence=float("nan"),
        field_confidence_source=ConfidenceProvenance.PROVIDER_FIELD,
    )
    evidence = _with(_evidence(), model=invalid_model)
    decision = _evaluate(evidence)
    assert decision.lane is DecisionLane.QUARANTINE
    assert decision.reasons == (DecisionReason.DECISION_INPUT_INVALID,)
    assert "synthetic-value" not in repr(decision.reasons)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            {"binding_status": IdentityBindingStatus.MISMATCH},
            DecisionReason.IDENTITY_MISMATCH,
        ),
        ({"tenant_id": "other-tenant"}, DecisionReason.TENANT_BINDING_MISMATCH),
    ],
)
def test_identity_and_tenant_mismatch_quarantine(change, reason):
    evidence = _evidence()
    decision = _evaluate(
        _with(evidence, identity=evidence.identity.model_copy(update=change))
    )
    assert decision.lane is DecisionLane.QUARANTINE
    assert reason in decision.reasons


@pytest.mark.parametrize(
    ("component", "issue", "reason"),
    [
        ("model", EvidenceIssue.TAMPERED_EVIDENCE, DecisionReason.TAMPERED_EVIDENCE),
        (
            "lifecycle",
            EvidenceIssue.PARTIAL_PROVIDER_RESPONSE,
            DecisionReason.PARTIAL_PROVIDER_RESPONSE,
        ),
    ],
)
def test_integrity_blockers_quarantine(component, issue, reason):
    evidence = _evidence()
    value = getattr(evidence, component)
    value = value.model_copy(update={"issues": frozenset({issue})})
    if component == "lifecycle":
        value = value.model_copy(update={"partial_provider_response": True})
    decision = _evaluate(_with(evidence, **{component: value}))
    assert decision.lane is DecisionLane.QUARANTINE
    assert reason in decision.reasons


def test_inactive_consent_and_erasure_in_progress_quarantine():
    evidence = _evidence()
    inactive = evidence.lifecycle.model_copy(
        update={"consent_state": SnapshotState.INACTIVE}
    )
    erasing = evidence.lifecycle.model_copy(
        update={"erasure_state": SnapshotState.IN_PROGRESS}
    )
    assert (
        DecisionReason.CONSENT_NOT_ACTIVE
        in _evaluate(_with(evidence, lifecycle=inactive)).reasons
    )
    assert (
        DecisionReason.ERASURE_IN_PROGRESS
        in _evaluate(_with(evidence, lifecycle=erasing)).reasons
    )


def test_verifier_disagreement_quarantines_but_abstention_is_source_only():
    evidence = _evidence()
    disagreed = evidence.model.model_copy(
        update={"verifier_outcome": VerifierOutcome.DISAGREED}
    )
    abstained = evidence.model.model_copy(
        update={"verifier_outcome": VerifierOutcome.ABSTAINED}
    )
    assert _evaluate(_with(evidence, model=disagreed)).lane is DecisionLane.QUARANTINE
    abstention = _evaluate(_with(evidence, model=abstained), _policy(enabled=True))
    assert abstention.lane is DecisionLane.SOURCE_ONLY
    assert DecisionReason.VERIFIER_ABSTAINED in abstention.reasons


def test_missing_field_confidence_and_document_confidence_never_auto_commit():
    evidence = _evidence()
    model = evidence.model.model_copy(
        update={
            "document_confidence": 1.0,
            "field_confidence": None,
            "field_confidence_source": ConfidenceProvenance.UNAVAILABLE,
        }
    )
    decision = _evaluate(_with(evidence, model=model), _policy(enabled=True))
    assert decision.lane is DecisionLane.SOURCE_ONLY
    assert DecisionReason.FIELD_CONFIDENCE_UNAVAILABLE in decision.reasons


def test_document_confidence_substitution_issue_quarantines():
    evidence = _evidence()
    model = evidence.model.model_copy(
        update={
            "issues": frozenset(
                {EvidenceIssue.DOCUMENT_CONFIDENCE_USED_AS_FIELD_CONFIDENCE}
            )
        }
    )
    decision = _evaluate(_with(evidence, model=model), _policy(enabled=True))
    assert decision.lane is DecisionLane.QUARANTINE
    assert DecisionReason.TAMPERED_EVIDENCE in decision.reasons


def test_policy_threshold_is_explicit_and_not_a_runtime_default():
    decision = _evaluate(
        _evidence(), _policy(enabled=True, minimum_field_confidence=1.0)
    )
    assert decision.lane is DecisionLane.SOURCE_ONLY
    assert DecisionReason.FIELD_CONFIDENCE_BELOW_POLICY in decision.reasons
    assert _policy().minimum_field_confidence is None


def test_incomplete_visual_model_and_high_risk_evidence_are_source_only():
    evidence = _evidence()
    visual = evidence.visual.model_copy(update={"coverage": VisualCoverage.PARTIAL})
    model = evidence.model.model_copy(update={"model_version": None})
    clinical = evidence.clinical_value.model_copy(
        update={"clinical_risk": ClinicalRisk.HIGH_RISK}
    )
    decision = _evaluate(
        _with(evidence, visual=visual, model=model, clinical_value=clinical),
        _policy(enabled=True),
    )
    assert decision.lane is DecisionLane.SOURCE_ONLY
    assert DecisionReason.VISUAL_EVIDENCE_INCOMPLETE in decision.reasons
    assert DecisionReason.MODEL_PROVENANCE_INCOMPLETE in decision.reasons
    assert DecisionReason.CLINICAL_RISK_REQUIRES_REVIEW in decision.reasons


def test_runtime_default_is_force_disabled_and_synthetic_policy_is_reachable():
    runtime = _evaluate()
    synthetic = _evaluate(policy=_policy(enabled=True))
    assert runtime.lane is DecisionLane.SOURCE_ONLY
    assert runtime.reasons == (DecisionReason.AUTO_COMMIT_DISABLED,)
    assert synthetic.lane is DecisionLane.AUTO_COMMIT
    assert synthetic.reasons == (DecisionReason.ELIGIBLE_UNDER_POLICY,)


def test_unknown_policy_and_evidence_versions_fail_closed():
    policy_decision = _evaluate(policy=_policy(policy_version="unknown-policy"))
    unsupported = _with(_evidence(), contract_version="99.0")
    evidence_decision = _evaluate(unsupported)
    assert policy_decision.reasons == (DecisionReason.POLICY_VERSION_UNSUPPORTED,)
    assert evidence_decision.reasons == (DecisionReason.EVIDENCE_CONTRACT_UNSUPPORTED,)


def test_evidence_policy_identity_mismatch_fails_closed():
    evidence = _evidence()
    embedded = PolicyEvidence(
        evaluation_occurred=True,
        evaluation_id="earlier-evaluation",
        policy_version="different-policy",
        evaluated_at=NOW,
        auto_commit_enabled=False,
    )
    decision = _evaluate(_with(evidence, policy=embedded))
    assert decision.lane is DecisionLane.QUARANTINE
    assert DecisionReason.POLICY_VERSION_UNSUPPORTED in decision.reasons


def test_unchecked_invalid_policy_fails_closed():
    policy = ExtractionDecisionPolicy.model_construct(
        policy_version=DECISION_POLICY_VERSION,
        auto_commit_enabled=True,
    )
    decision = _evaluate(policy=policy)
    assert decision.lane is DecisionLane.QUARANTINE
    assert decision.reasons == (DecisionReason.DECISION_INPUT_INVALID,)
    assert decision.auto_commit_feature_enabled is False


def test_policy_version_is_single_and_immutable():
    policy = _policy()
    assert policy.policy_version == DECISION_POLICY_VERSION
    with pytest.raises(ValidationError):
        policy.policy_version = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ExtractionDecisionPolicy(
            **{
                **policy.model_dump(),
                "policy_version": ["one", "two"],
            }
        )


def test_reevaluation_creates_new_linked_immutable_decision():
    ids = iter(("decision-1", "decision-2"))
    first = evaluate_extraction_evidence(
        evidence=_evidence(),
        policy=_policy(),
        decision_id_factory=lambda: next(ids),
        evaluated_at=NOW,
    )
    second = evaluate_extraction_evidence(
        evidence=_evidence(),
        policy=_policy(),
        decision_id_factory=lambda: next(ids),
        evaluated_at=NOW,
        earlier_decision_id=first.decision_id,
    )
    assert second.decision_id != first.decision_id
    assert second.earlier_decision_id == first.decision_id
    with pytest.raises(ValidationError):
        first.lane = DecisionLane.AUTO_COMMIT  # type: ignore[misc]


def test_reason_order_is_deterministic_and_contains_only_codes():
    evidence = _evidence()
    lifecycle = evidence.lifecycle.model_copy(
        update={
            "partial_provider_response": True,
            "consent_state": SnapshotState.INACTIVE,
        }
    )
    first = _evaluate(_with(evidence, lifecycle=lifecycle), decision_id="one")
    second = _evaluate(_with(evidence, lifecycle=lifecycle), decision_id="two")
    assert first.reasons == second.reasons
    assert all(reason.value.isupper() for reason in first.reasons)
    assert "synthetic-value" not in repr(first.reasons)


def test_unchecked_model_copy_cannot_bypass_boundary_validation():
    evidence = _evidence().model_copy(
        update={"identity": {"tenant_id": "other", "patient_id": "other"}}
    )
    decision = _evaluate(evidence)
    assert decision.lane is DecisionLane.QUARANTINE
    assert DecisionReason.IDENTITY_MISMATCH in decision.reasons
    assert DecisionReason.TENANT_BINDING_MISMATCH in decision.reasons


def test_evaluator_has_no_ambient_id_or_time_and_does_not_mutate_inputs():
    evidence = _evidence()
    policy = _policy()
    before_evidence = evidence.model_dump()
    before_policy = policy.model_dump()
    generated = count(1)
    decision = evaluate_extraction_evidence(
        evidence=evidence,
        policy=policy,
        decision_id_factory=lambda: f"decision-{next(generated)}",
        evaluated_at=NOW,
    )
    assert decision.decision_id == "decision-1"
    assert decision.evaluated_at == NOW
    assert evidence.model_dump() == before_evidence
    assert policy.model_dump() == before_policy
