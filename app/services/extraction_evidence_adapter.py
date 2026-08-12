"""Pure adapter from the current document provider output to field evidence."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel, ConfigDict

from app.ai.identity_decision import IdentityDecisionState
from app.models.ai_models import ExtractedMedicalDocument, ProviderFieldEvidence
from app.models.field_evidence import (
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
    PolicyEvidence,
    SnapshotState,
    VerifierOutcome,
    VisualCoverage,
    VisualEvidence,
)


class CurrentExtractionBinding(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    patient_id: str
    tenant_id: str
    organization_id: str | None = None
    source_document_id: str
    source_document_hash: str | None = None
    ingestion_id: str
    encounter_id: str | None = None
    job_id: str
    workflow_id: str | None = None
    request_id: str | None = None
    attempt_number: int
    attempt_id: str
    created_at: datetime
    extracted_at: datetime
    source_received_at: datetime | None = None
    provider_name: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    consent_reference: str | None = None
    consent_state: SnapshotState = SnapshotState.UNKNOWN
    erasure_state: SnapshotState = SnapshotState.UNKNOWN
    document_identity_state: IdentityDecisionState | None = None


EVIDENCE_INSTANCE_ID_VERSION = "nexa-evidence-instance:v2"


def _evidence_instance_id(
    *, binding: CurrentExtractionBinding, provider_evidence_hash: str
) -> str:
    """Build a stable ID for one provider observation in one lifecycle.

    The provider hash remains the provider-owned observation fingerprint.  The
    lifecycle bindings make the persisted Nexa instance distinct across jobs,
    workflows, documents, and attempts without including patient or tenant
    identity in provider truth.
    """
    canonical_payload = json.dumps(
        [
            EVIDENCE_INSTANCE_ID_VERSION,
            binding.source_document_id,
            binding.job_id,
            binding.workflow_id,
            binding.attempt_id,
            provider_evidence_hash,
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return str(uuid5(NAMESPACE_URL, canonical_payload))


def adapt_current_extracted_field(
    *,
    document: ExtractedMedicalDocument,
    field_name: str,
    raw_value: str,
    binding: CurrentExtractionBinding,
    provider_evidence: ProviderFieldEvidence | None = None,
) -> ExtractedFieldEvidence:
    """Represent current output without fabricating field or visual evidence."""
    identity_issues: set[EvidenceIssue] = set()
    if not binding.source_document_hash:
        identity_issues.add(EvidenceIssue.SOURCE_DOCUMENT_HASH_MISSING)
    if binding.document_identity_state in {
        IdentityDecisionState.IDENTITY_DISCREPANCY,
        IdentityDecisionState.IDENTITY_CONFLICTING,
    }:
        identity_issues.add(EvidenceIssue.IDENTITY_MISMATCH)
    elif (
        binding.document_identity_state
        is IdentityDecisionState.IDENTITY_INSUFFICIENT
    ):
        identity_issues.add(EvidenceIssue.IDENTITY_UNAVAILABLE)

    model_issues: set[EvidenceIssue] = set()
    visual_issues: set[EvidenceIssue] = set()
    if provider_evidence is None or provider_evidence.field_confidence is None:
        model_issues.add(EvidenceIssue.FIELD_CONFIDENCE_UNAVAILABLE)
    provider_name = (
        provider_evidence.provider_name if provider_evidence else binding.provider_name
    )
    model_name = "AnalyzeDocument" if provider_evidence else binding.model_name
    model_version = (
        provider_evidence.provider_api_version
        if provider_evidence
        else binding.model_version
    )
    if (
        not provider_name
        or not model_name
        or not model_version
        or model_version == "unknown"
    ):
        model_issues.add(EvidenceIssue.PROVIDER_PROVENANCE_INCOMPLETE)

    page_number = provider_evidence.page_number if provider_evidence else None
    bounding_box = provider_evidence.bounding_box if provider_evidence else None
    source_text = provider_evidence.source_text if provider_evidence else None
    if page_number is None:
        visual_issues.add(EvidenceIssue.PAGE_UNAVAILABLE)
    if bounding_box is None:
        visual_issues.add(EvidenceIssue.BOUNDING_BOX_UNAVAILABLE)
    if not source_text:
        visual_issues.add(EvidenceIssue.SOURCE_TEXT_UNAVAILABLE)
    coverage = (
        VisualCoverage.COMPLETE if not visual_issues else VisualCoverage.UNAVAILABLE
    )

    return ExtractedFieldEvidence(
        evidence_id=str(
            _evidence_instance_id(
                binding=binding,
                provider_evidence_hash=provider_evidence.evidence_hash,
            )
            if provider_evidence and provider_evidence.evidence_hash
            else uuid4()
        ),
        identity=IdentityEvidence(
            patient_id=binding.patient_id,
            tenant_id=binding.tenant_id,
            organization_id=binding.organization_id,
            source_document_id=binding.source_document_id,
            source_document_hash=binding.source_document_hash,
            ingestion_id=binding.ingestion_id,
            encounter_id=binding.encounter_id,
            binding_status=IdentityBindingStatus.VERIFIED,
            binding_method=IdentityBindingMethod.SERVER_JOB_AND_DOCUMENT,
            issues=frozenset(identity_issues),
        ),
        clinical_value=ClinicalValueEvidence(
            field_name=field_name,
            raw_value=raw_value,
            normalized_value=(
                provider_evidence.normalized_value if provider_evidence else None
            ),
            raw_unit=(provider_evidence.raw_unit if provider_evidence else None),
            normalized_unit=(
                provider_evidence.normalized_unit if provider_evidence else None
            ),
            reference_range=(
                provider_evidence.reference_range if provider_evidence else None
            ),
            normalization_status=(
                NormalizationStatus.NORMALIZED
                if provider_evidence and provider_evidence.normalized_value is not None
                else NormalizationStatus.UNRESOLVED
            ),
        ),
        visual=VisualEvidence(
            page_number=page_number,
            bounding_box=bounding_box,
            source_text=source_text,
            coverage=coverage,
            issues=frozenset(visual_issues),
        ),
        model=ModelEvidence(
            provider_name=provider_name,
            model_name=model_name,
            model_version=model_version,
            extracted_at=(
                provider_evidence.extraction_timestamp
                if provider_evidence
                else binding.extracted_at
            ),
            document_confidence=document.extraction_confidence,
            field_confidence=(
                provider_evidence.field_confidence if provider_evidence else None
            ),
            field_confidence_source=(
                ConfidenceProvenance.PROVIDER_FIELD
                if provider_evidence and provider_evidence.field_confidence is not None
                else ConfidenceProvenance.UNAVAILABLE
            ),
            verifier_outcome=VerifierOutcome.NOT_RUN,
            provider_evidence_hash=(
                provider_evidence.evidence_hash if provider_evidence else None
            ),
            issues=frozenset(model_issues),
        ),
        policy=PolicyEvidence(
            evaluation_occurred=False,
            auto_commit_enabled=False,
        ),
        lifecycle=LifecycleEvidence(
            job_id=binding.job_id,
            workflow_id=binding.workflow_id,
            request_id=binding.request_id,
            attempt_number=binding.attempt_number,
            attempt_id=binding.attempt_id,
            created_at=binding.created_at,
            extracted_at=binding.extracted_at,
            source_received_at=binding.source_received_at,
            partial_provider_response=False,
            consent_state=binding.consent_state,
            consent_reference=binding.consent_reference,
            erasure_state=binding.erasure_state,
        ),
    )
