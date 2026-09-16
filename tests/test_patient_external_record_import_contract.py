from __future__ import annotations

from sqlalchemy import CheckConstraint

from app.models.patient_external_record_import import (
    PATIENT_EXTERNAL_RECORD_CATEGORIES,
    PATIENT_EXTERNAL_RECORD_IMPORT_STATUSES,
    PATIENT_EXTERNAL_RECORD_REVIEW_STATUSES,
    PatientExternalRecordCandidate,
    PatientExternalRecordImport,
)


def _check_constraint_texts(model: type) -> set[str]:
    return {
        str(item.sqltext)
        for item in model.__table__.constraints
        if isinstance(item, CheckConstraint)
    }


def test_patient_import_contract_has_required_categories_and_states() -> None:
    assert PATIENT_EXTERNAL_RECORD_CATEGORIES == (
        "PRESCRIPTION",
        "LAB_REPORT",
        "IMAGING_REPORT",
        "DISCHARGE_SUMMARY",
        "OTHER_MEDICAL_RECORD",
    )
    assert PATIENT_EXTERNAL_RECORD_IMPORT_STATUSES == (
        "UPLOADED",
        "PROCESSING",
        "REVIEW_REQUIRED",
        "READY_TO_SAVE",
        "COMPLETED",
        "FAILED_RETRYABLE",
        "FAILED_TERMINAL",
        "CANCELLED",
    )
    assert PATIENT_EXTERNAL_RECORD_REVIEW_STATUSES == (
        "NEEDS_REVIEW",
        "ACCEPTED",
        "CORRECTED",
        "REJECTED",
    )


def test_patient_import_authority_has_no_provider_or_consent_columns() -> None:
    import_columns = set(PatientExternalRecordImport.__table__.columns.keys())
    candidate_columns = set(PatientExternalRecordCandidate.__table__.columns.keys())

    assert "patient_id" in import_columns
    assert "source_document_id" in import_columns
    assert "authorization_provider_id" not in import_columns
    assert "tenant_id" not in import_columns
    assert "consent_request_id" not in import_columns

    assert "patient_id" in candidate_columns
    assert "authorization_provider_id" not in candidate_columns
    assert "tenant_id" not in candidate_columns


def test_patient_import_candidates_store_sensitive_values_only_encrypted() -> None:
    columns = set(PatientExternalRecordCandidate.__table__.columns.keys())

    assert "encrypted_raw_value" in columns
    assert "encrypted_source_text" in columns
    assert "encrypted_reviewed_value" in columns
    assert "raw_value" not in columns
    assert "corrected_value" not in columns


def test_patient_correction_requires_review_provenance() -> None:
    checks = _check_constraint_texts(PatientExternalRecordCandidate)
    correction_check = next(
        item for item in checks if "review_status = 'CORRECTED'" in item
    )

    assert "patient_reviewed" in correction_check
    assert "encrypted_reviewed_value IS NOT NULL" in correction_check
    assert "reviewed_at IS NOT NULL" in correction_check


def test_completed_import_requires_typed_record_and_timeline_references() -> None:
    checks = _check_constraint_texts(PatientExternalRecordImport)
    completion_check = next(item for item in checks if "status = 'COMPLETED'" in item)

    assert "final_record_type IS NOT NULL" in completion_check
    assert "final_record_id IS NOT NULL" in completion_check
    assert "timeline_event_id IS NOT NULL" in completion_check
    assert "completed_at IS NOT NULL" in completion_check
