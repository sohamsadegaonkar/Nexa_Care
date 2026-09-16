from __future__ import annotations

from datetime import datetime, timezone
import inspect
import uuid

import pytest

from app.ai.extractor import (
    DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION,
    DemoExtractionProvider,
    ExtractionProviderResult,
    ProviderResponseError,
)
from app.models.ai_models import ExtractedMedicalDocument, ProviderFieldEvidence
from app.services.patient_external_record_extraction import (
    _candidate_evidence_id,
    _failure_status,
    _reviewable_field_evidence,
    _source_digest,
    _validated_provider_result,
    process_patient_external_record,
)


def _evidence(field: str, value: str, *, evidence_hash: str | None = None):
    return ProviderFieldEvidence(
        canonical_field_name=field,
        raw_value=value,
        source_text=f"source for {field}",
        page_number=0,
        bounding_box=None,
        field_confidence=0.91,
        provider_name="demo",
        provider_api_version="demo-v1",
        extraction_timestamp=datetime.now(timezone.utc),
        evidence_hash=evidence_hash,
        source_type="QUERY_RESULT",
    )


def _result(*evidence: ProviderFieldEvidence, adapter: str = "demo"):
    document = ExtractedMedicalDocument(
        patient_name="compat identity",
        aadhaar_abha_id="compat id",
        phone="compat phone",
        diagnoses=["summary-only diagnosis"],
        lab_results=["summary-only lab"],
        prescriptions=["summary-only prescription"],
        extraction_confidence=0.82,
        field_evidence=list(evidence),
    )
    return ExtractionProviderResult(
        document=document,
        provider_adapter=adapter,
        provider_contract_version=DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION,
        provider_model_version=None,
        response_complete=True,
        provider_attempt_traces=(),
    )


def test_process_service_accepts_only_patient_import_authority() -> None:
    parameter_names = set(inspect.signature(process_patient_external_record).parameters)
    assert parameter_names == {"db", "patient_id", "import_id"}
    assert {
        "provider_id",
        "hospital_id",
        "tenant_id",
        "consent_token",
        "consent_request_id",
        "clinical_access_session_id",
    }.isdisjoint(parameter_names)


def test_process_service_contains_required_fail_closed_boundaries() -> None:
    source = inspect.getsource(process_patient_external_record)
    assert "check_erasure_registry" in source
    assert "get_patient_document_bytes" in source
    assert "_source_digest" in source
    assert "get_medical_document_extractor" in source
    assert "get_encryption_provider" in source
    assert "REVIEW_REQUIRED" in source
    assert "final_record_id" not in source
    assert "timeline_event_id" not in source
    assert "authorize_document_processing" not in source
    assert "ClinicalAccessSession" not in source


def test_provider_result_must_match_server_configured_adapter() -> None:
    extractor = DemoExtractionProvider()
    valid = _result(_evidence("hba1c", "6.1%"))
    assert (
        _validated_provider_result(
            configured_provider="demo",
            extractor=extractor,
            result=valid,
        )
        is valid
    )

    forged = _result(_evidence("hba1c", "6.1%"), adapter="remote")
    with pytest.raises(ProviderResponseError):
        _validated_provider_result(
            configured_provider="demo",
            extractor=extractor,
            result=forged,
        )


def test_identity_fields_and_summary_arrays_never_become_review_candidates() -> None:
    result = _result(
        _evidence("patient_name", "Someone"),
        _evidence("phone", "0000000000"),
        _evidence("aadhaar_abha_id", "identifier"),
        _evidence("hba1c", "6.1%"),
        _evidence("diagnosis", ""),
    )

    reviewable = _reviewable_field_evidence(result)
    assert [item.canonical_field_name for item in reviewable] == ["hba1c"]
    assert all("summary-only" not in item.raw_value for item in reviewable)


def test_candidate_evidence_id_is_server_owned_deterministic_and_value_free() -> None:
    import_id = uuid.uuid4()
    first = _evidence("hba1c", "6.1%", evidence_hash="provider-hash-1")
    second = _evidence("hba1c", "9.9%", evidence_hash="provider-hash-1")

    assert _candidate_evidence_id(import_id, first, 1) == _candidate_evidence_id(
        import_id, second, 1
    )
    assert _candidate_evidence_id(import_id, first, 1) != _candidate_evidence_id(
        import_id, first, 2
    )


def test_failure_state_mapping_is_closed_and_patient_safe() -> None:
    assert _failure_status(True) == "FAILED_RETRYABLE"
    assert _failure_status(False) == "FAILED_TERMINAL"


def test_source_integrity_digest_is_sha256_and_deterministic() -> None:
    data = b"synthetic-patient-owned-source"
    assert len(_source_digest(data)) == 64
    assert _source_digest(data) == _source_digest(data)
    assert _source_digest(data) != _source_digest(data + b"-changed")
