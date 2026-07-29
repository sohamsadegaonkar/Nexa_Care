"""Pure adapter from the current document provider output to field evidence."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from app.models.ai_models import ExtractedMedicalDocument
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


def adapt_current_extracted_field(
    *,
    document: ExtractedMedicalDocument,
    field_name: str,
    raw_value: str,
    binding: CurrentExtractionBinding,
) -> ExtractedFieldEvidence:
    """Represent current output without fabricating field or visual evidence."""
    identity_issues: set[EvidenceIssue] = set()
    if not binding.source_document_hash:
        identity_issues.add(EvidenceIssue.SOURCE_DOCUMENT_HASH_MISSING)

    model_issues = {
        EvidenceIssue.FIELD_CONFIDENCE_UNAVAILABLE,
        EvidenceIssue.PROVIDER_PROVENANCE_INCOMPLETE,
    }
    visual_issues = {
        EvidenceIssue.PAGE_UNAVAILABLE,
        EvidenceIssue.BOUNDING_BOX_UNAVAILABLE,
        EvidenceIssue.SOURCE_TEXT_UNAVAILABLE,
    }

    return ExtractedFieldEvidence(
        evidence_id=str(uuid4()),
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
            normalized_value=None,
            normalization_status=NormalizationStatus.UNRESOLVED,
        ),
        visual=VisualEvidence(
            page_number=None,
            bounding_box=None,
            source_text=None,
            coverage=VisualCoverage.UNAVAILABLE,
            issues=frozenset(visual_issues),
        ),
        model=ModelEvidence(
            provider_name=binding.provider_name,
            model_name=binding.model_name,
            model_version=binding.model_version,
            extracted_at=binding.extracted_at,
            document_confidence=document.extraction_confidence,
            field_confidence=None,
            field_confidence_source=ConfidenceProvenance.UNAVAILABLE,
            verifier_outcome=VerifierOutcome.NOT_RUN,
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
