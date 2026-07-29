"""Executable adversarial coverage for the canonical field-evidence contract."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.ai_models import ExtractedMedicalDocument
from app.models.field_evidence import (
    ClinicalValueEvidence,
    EvidenceIssue,
    IdentityBindingMethod,
    IdentityBindingStatus,
    IdentityEvidence,
    ModelEvidence,
    NormalizationStatus,
    NormalizedBoundingBox,
    PolicyEvidence,
    VerifierOutcome,
    VisualCoverage,
    VisualEvidence,
)
from app.services.extraction_evidence_adapter import (
    CurrentExtractionBinding,
    adapt_current_extracted_field,
)

NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)


def _current_evidence(*, document_confidence: float = 0.99):
    document = ExtractedMedicalDocument(
        patient_name="Synthetic Patient",
        aadhaar_abha_id="SYNTHETIC-ID",
        phone="0000000000",
        diagnoses=[],
        lab_results=["HbA1c 7.2 %"],
        prescriptions=[],
        extraction_confidence=document_confidence,
    )
    return adapt_current_extracted_field(
        document=document,
        field_name="lab_result",
        raw_value="HbA1c 7.2 %",
        binding=CurrentExtractionBinding(
            patient_id="patient-1",
            tenant_id="tenant-1",
            organization_id="hospital-1",
            source_document_id="document-1",
            source_document_hash="a" * 64,
            ingestion_id="upload-1",
            job_id="job-1",
            workflow_id="workflow-1",
            request_id="request-1",
            attempt_number=1,
            attempt_id="job-1:1",
            created_at=NOW,
            extracted_at=NOW,
            source_received_at=NOW,
            provider_name="remote",
            model_name=None,
            model_version=None,
            consent_reference="workflow-1",
        ),
    )


def test_scenario_1_document_confidence_never_becomes_field_confidence():
    evidence = _current_evidence(document_confidence=0.99)
    assert evidence.model.document_confidence == 0.99
    assert evidence.model.field_confidence is None
    assert not evidence.model.has_genuine_field_confidence
    assert EvidenceIssue.FIELD_CONFIDENCE_UNAVAILABLE in evidence.issue_codes


def test_scenario_2_missing_page_is_not_complete_visual_evidence():
    evidence = _current_evidence()
    assert evidence.visual.page_number is None
    assert evidence.visual.is_complete is False
    assert EvidenceIssue.PAGE_UNAVAILABLE in evidence.issue_codes


def test_scenario_3_value_and_visual_source_mismatch_is_explicit():
    evidence = _current_evidence().model_copy(
        update={
            "visual": VisualEvidence(
                page_number=0,
                bounding_box=NormalizedBoundingBox(
                    left=0.1, top=0.1, right=0.4, bottom=0.2
                ),
                source_text="HbA1c 5.2 %",
                coverage=VisualCoverage.CONFLICTING,
                issues=frozenset({EvidenceIssue.VISUAL_VALUE_MISMATCH}),
            )
        }
    )
    assert evidence.clinical_value.raw_value == "HbA1c 7.2 %"
    assert evidence.visual.coverage is VisualCoverage.CONFLICTING
    assert EvidenceIssue.VISUAL_VALUE_MISMATCH in evidence.issue_codes


def test_scenario_4_missing_or_malformed_bbox_is_unavailable_or_invalid():
    assert _current_evidence().visual.bounding_box is None
    with pytest.raises(ValidationError):
        NormalizedBoundingBox(left=0.8, top=0.1, right=0.2, bottom=0.4)
    with pytest.raises(ValidationError):
        NormalizedBoundingBox(left=float("nan"), top=0.1, right=0.2, bottom=0.4)


def test_scenario_5_document_only_provider_has_unavailable_field_confidence():
    evidence = _current_evidence(document_confidence=0.73)
    assert evidence.model.document_confidence == 0.73
    assert evidence.model.field_confidence is None
    assert evidence.model.field_confidence_source.value == "UNAVAILABLE"


def test_scenario_8_identity_mismatch_is_explicit_and_blocking():
    evidence = _current_evidence().model_copy(
        update={
            "identity": IdentityEvidence(
                patient_id="patient-1",
                tenant_id="tenant-1",
                source_document_id="document-1",
                source_document_hash="a" * 64,
                ingestion_id="upload-1",
                binding_status=IdentityBindingStatus.MISMATCH,
                binding_method=IdentityBindingMethod.DOCUMENT_IDENTITY,
                issues=frozenset({EvidenceIssue.IDENTITY_MISMATCH}),
            )
        }
    )
    assert evidence.identity.is_complete is False
    assert evidence.has_blocking_issues
    assert EvidenceIssue.IDENTITY_MISMATCH in evidence.issue_codes


def test_scenario_12_tampered_evidence_is_representable_and_blocking():
    evidence = _current_evidence()
    tampered_model = evidence.model.model_copy(
        update={
            "issues": evidence.model.issues
            | frozenset({EvidenceIssue.TAMPERED_EVIDENCE})
        }
    )
    evidence = evidence.model_copy(update={"model": tampered_model})
    assert EvidenceIssue.TAMPERED_EVIDENCE in evidence.issue_codes
    assert evidence.has_blocking_issues


def test_scenario_13_ambiguous_clinical_normalization_remains_unresolved():
    clinical = ClinicalValueEvidence(
        field_name="lab_result",
        raw_value="Glucose 5,6",
        normalized_value=None,
        raw_unit=None,
        normalized_unit=None,
        reference_range=None,
        normalization_status=NormalizationStatus.UNRESOLVED,
        issues=frozenset({EvidenceIssue.CLINICAL_VALUE_AMBIGUOUS}),
    )
    assert clinical.normalized_value is None
    assert clinical.is_structurally_complete is False


def test_scenario_20_cross_tenant_binding_mismatch_is_explicit():
    evidence = _current_evidence()
    identity = evidence.identity.model_copy(
        update={
            "binding_status": IdentityBindingStatus.MISMATCH,
            "issues": evidence.identity.issues
            | frozenset({EvidenceIssue.TENANT_BINDING_MISMATCH}),
        }
    )
    evidence = evidence.model_copy(update={"identity": identity})
    assert EvidenceIssue.TENANT_BINDING_MISMATCH in evidence.issue_codes
    assert evidence.identity.is_complete is False


def test_scenario_21_partial_visual_coverage_is_not_complete():
    visual = VisualEvidence(
        page_number=0,
        bounding_box=NormalizedBoundingBox(left=0.1, top=0.1, right=0.2, bottom=0.2),
        source_text="150",
        coverage=VisualCoverage.PARTIAL,
        issues=frozenset({EvidenceIssue.PARTIAL_VISUAL_COVERAGE}),
    )
    assert visual.source_text == "150"
    assert visual.is_complete is False


def test_scenario_22_verifier_abstention_is_not_agreement():
    evidence = _current_evidence()
    model = ModelEvidence(
        provider_name="remote",
        model_name="extractor",
        model_version="1",
        extracted_at=NOW,
        document_confidence=0.9,
        field_confidence=None,
        verifier_outcome=VerifierOutcome.ABSTAINED,
        issues=frozenset({EvidenceIssue.VERIFIER_ABSTAINED}),
    )
    evidence = evidence.model_copy(update={"model": model})
    assert evidence.model.verifier_outcome is not VerifierOutcome.AGREED
    assert EvidenceIssue.VERIFIER_ABSTAINED in evidence.issue_codes


def test_scenario_24_policy_record_pins_one_immutable_version():
    policy = PolicyEvidence(
        evaluation_occurred=True,
        evaluation_id="evaluation-1",
        policy_version="policy-v1",
        evaluated_at=NOW,
        auto_commit_enabled=False,
    )
    assert policy.policy_version == "policy-v1"
    with pytest.raises(ValidationError):
        PolicyEvidence(
            evaluation_occurred=True,
            policy_version=["policy-v1", "policy-v2"],  # type: ignore[arg-type]
            evaluated_at=NOW,
        )
    with pytest.raises(ValidationError):
        policy.policy_version = "policy-v2"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        PolicyEvidence(auto_commit_enabled=True)  # type: ignore[arg-type]
