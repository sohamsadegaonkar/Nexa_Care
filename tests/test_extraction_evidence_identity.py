from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.models.ai_models import ExtractedMedicalDocument, ProviderFieldEvidence
from app.models.field_evidence import NormalizedBoundingBox, SnapshotState
from app.services.extraction_evidence_adapter import (
    CurrentExtractionBinding,
    EVIDENCE_INSTANCE_ID_VERSION,
    adapt_current_extracted_field,
)
from app.services.pipeline_orchestrator import (
    ExtractionEvidenceInstanceCollision,
    _is_candidate_evidence_unique_violation,
    _candidate_binding_matches,
)


NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)
HASH = "a" * 64


def _document() -> ExtractedMedicalDocument:
    return ExtractedMedicalDocument(
        patient_name="Synthetic",
        phone="",
        aadhaar_abha_id="",
        diagnoses=[],
        lab_results=[],
        prescriptions=[],
        extraction_confidence=None,
        field_evidence=[],
    )


def _provider(hash_value: str = HASH) -> ProviderFieldEvidence:
    return ProviderFieldEvidence(
        canonical_field_name="hba1c",
        raw_value="7.2 %",
        source_text="HbA1c 7.2 %",
        page_number=0,
        bounding_box=NormalizedBoundingBox(
            left=0.1, top=0.1, right=0.2, bottom=0.2
        ),
        field_confidence=0.99,
        provider_name="aws_textract",
        provider_api_version="1.0",
        extraction_timestamp=NOW,
        evidence_hash=hash_value,
        source_type="QUERY_RESULT",
    )


def _binding(*, job="job-a", workflow="workflow-a", document="document-a", attempt="attempt-a"):
    return CurrentExtractionBinding(
        patient_id="patient-a",
        tenant_id="tenant-a",
        source_document_id=document,
        ingestion_id="ingestion-a",
        job_id=job,
        workflow_id=workflow,
        request_id="request-a",
        attempt_number=1,
        attempt_id=attempt,
        created_at=NOW,
        extracted_at=NOW,
        provider_name="aws_textract",
        model_version="1.0",
        consent_state=SnapshotState.ACTIVE,
        erasure_state=SnapshotState.NOT_REQUESTED,
    )


def _adapt(*, binding, provider=None):
    return adapt_current_extracted_field(
        document=_document(),
        field_name="hba1c",
        raw_value="7.2 %",
        provider_evidence=provider or _provider(),
        binding=binding,
    )


def test_evidence_instance_id_is_deterministic_for_same_lifecycle():
    first = _adapt(binding=_binding())
    second = _adapt(binding=_binding())
    assert first.evidence_id == second.evidence_id
    assert UUID(first.evidence_id).version == 5
    assert first.model.provider_evidence_hash == HASH


@pytest.mark.parametrize(
    "changes",
    [
        {"job": "job-b"},
        {"workflow": "workflow-b"},
        {"attempt": "attempt-b"},
        {"document": "document-b"},
    ],
)
def test_evidence_instance_id_is_lifecycle_scoped(changes):
    first = _adapt(binding=_binding())
    second = _adapt(binding=_binding(**changes))
    assert first.evidence_id != second.evidence_id
    assert first.model.provider_evidence_hash == second.model.provider_evidence_hash


def test_different_provider_fingerprints_are_distinct_without_mutation():
    first = _adapt(binding=_binding(), provider=_provider("a" * 64))
    second = _adapt(binding=_binding(), provider=_provider("b" * 64))
    assert first.evidence_id != second.evidence_id
    assert first.model.provider_evidence_hash == "a" * 64
    assert second.model.provider_evidence_hash == "b" * 64


def test_internal_namespace_is_not_public_field_contract_version():
    assert EVIDENCE_INSTANCE_ID_VERSION == "nexa-evidence-instance:v2"
    assert _adapt(binding=_binding()).contract_version == "1.0"


def _candidate(**overrides):
    values = dict(
        evidence_id=overrides.pop("evidence_id"),
        job_id="job-a",
        source_document_id="document-a",
        patient_id="patient-a",
        tenant_id="tenant-a",
        authorization_provider_id="provider-a",
        field_name="hba1c",
        provider_name="aws_textract",
        provider_version="1.0",
        lane="SOURCE_ONLY",
        routing_eligible=True,
        eligibility_reason_code=None,
        eligibility_policy_version="v1",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_candidate_binding_comparison_rejects_wrong_authoritative_lifecycle():
    evidence_id = "00000000-0000-5000-8000-000000000001"
    assert _candidate_binding_matches(
        _candidate(evidence_id=evidence_id), _candidate(evidence_id=evidence_id)
    )
    assert not _candidate_binding_matches(
        _candidate(evidence_id=evidence_id),
        _candidate(evidence_id=evidence_id, job_id="job-b"),
    )
    assert ExtractionEvidenceInstanceCollision.__name__ == (
        "ExtractionEvidenceInstanceCollision"
    )


class _Diagnostic:
    def __init__(self, *, sqlstate=None, constraint_name=None):
        self.sqlstate = sqlstate
        self.constraint_name = constraint_name


class _DriverError(Exception):
    def __init__(self, *, sqlstate=None, constraint_name=None, cause=None):
        super().__init__("duplicate key text is not authoritative")
        self.sqlstate = sqlstate
        self.constraint_name = constraint_name
        self.__cause__ = cause


def test_structured_candidate_duplicate_is_recognized():
    assert _is_candidate_evidence_unique_violation(
        _DriverError(
            sqlstate="23505",
            constraint_name="extraction_candidates_evidence_id_key",
        )
    )


def test_different_unique_constraint_is_not_reconciled():
    assert not _is_candidate_evidence_unique_violation(
        _DriverError(sqlstate="23505", constraint_name="other_unique_constraint")
    )


def test_non_unique_sqlstate_is_not_reconciled():
    assert not _is_candidate_evidence_unique_violation(
        _DriverError(
            sqlstate="23503",
            constraint_name="extraction_candidates_evidence_id_key",
        )
    )


def test_wrapped_orig_structured_markers_are_recognized():
    outer = _DriverError()
    outer.orig = _DriverError(
        sqlstate="23505",
        constraint_name="extraction_candidates_evidence_id_key",
    )
    assert _is_candidate_evidence_unique_violation(outer)


def test_driver_cause_structured_markers_are_recognized():
    outer = _DriverError(cause=_DriverError(
        sqlstate="23505",
        constraint_name="extraction_candidates_evidence_id_key",
    ))
    outer.orig = _DriverError()
    assert _is_candidate_evidence_unique_violation(outer)


def test_missing_structured_diagnostic_is_not_reconciled():
    assert not _is_candidate_evidence_unique_violation(
        _DriverError(sqlstate="23505")
    )


def test_message_only_constraint_text_is_not_reconciled():
    error = _DriverError(sqlstate="23505")
    error.args = ("extraction_candidates_evidence_id_key",)
    assert not _is_candidate_evidence_unique_violation(error)
