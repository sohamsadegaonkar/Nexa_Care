from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.models.identity_review import (
    ClaimIdentityReviewCaseRequest,
    CreateIdentityReviewCaseRequest,
    IdentityReviewCaseStatus,
    IdentityReviewOutcome,
    IdentityReviewReasonCode,
    SubmitIdentityReviewDispositionRequest,
)
from app.security.identity_review_policy import IdentityReviewOperation


def _json_validate(model, payload):
    return model.model_validate_json(json.dumps(payload))


def test_identity_review_closed_sets_are_exact():
    assert {item.value for item in IdentityReviewCaseStatus} == {
        "PENDING",
        "IN_REVIEW",
        "RESOLVED_NO_RELEASE",
        "ESCALATED",
    }
    assert {item.value for item in IdentityReviewOutcome} == {
        "REJECTED_FOR_BOUND_PATIENT",
        "VERIFIED_IDENTITY_REQUIRED",
        "SECURITY_ESCALATION_REQUIRED",
        "INSUFFICIENT_IDENTITY_EVIDENCE",
    }
    assert {item.value for item in IdentityReviewReasonCode} == {
        "DOCUMENT_IDENTITY_MISMATCH",
        "CANONICAL_IDENTITY_UNAVAILABLE",
        "VERIFIED_IDENTIFIER_REQUIRED",
        "POSSIBLE_CROSS_PATIENT_DOCUMENT",
        "POSSIBLE_PRIVACY_INCIDENT",
        "IDENTITY_REVIEW_INCONCLUSIVE",
        "DOCUMENT_REJECTED_FOR_BOUND_PATIENT",
    }
    assert {item.value for item in IdentityReviewOperation} == {
        "CREATE_CASE",
        "LIST_CASES",
        "READ_CASE",
        "CLAIM_CASE",
        "RECOVER_SESSION",
        "SUBMIT_DISPOSITION",
    }


@pytest.mark.parametrize(
    "field",
    [
        "patient_id",
        "tenant_id",
        "target_patient_id",
        "new_patient_id",
        "correct_patient_id",
        "replacement_patient_id",
        "document_id",
        "routing_id",
        "decision_id",
        "patient_name",
        "ocr_name",
        "phone",
        "ocr_phone",
        "aadhaar",
        "abha",
        "aadhaar_abha_id",
        "diagnoses",
        "medications",
        "lab_values",
        "vitals",
        "clinical_summary",
        "corrected_values",
        "source_text",
        "confidence_override",
        "notes",
        "comments",
        "free_text",
    ],
)
def test_creation_rejects_hidden_identity_ownership_clinical_and_free_text(field):
    with pytest.raises(ValidationError):
        _json_validate(
            CreateIdentityReviewCaseRequest,
            {"idempotency_key": "create-key-0001", field: "forbidden"},
        )


def test_strict_claim_rejects_coerced_version_and_extra_fields():
    with pytest.raises(ValidationError):
        _json_validate(
            ClaimIdentityReviewCaseRequest,
            {
                "expected_version": "1",
                "idempotency_key": "claim-key-0001",
            },
        )
    with pytest.raises(ValidationError):
        _json_validate(
            ClaimIdentityReviewCaseRequest,
            {
                "expected_version": 1,
                "idempotency_key": "claim-key-0001",
                "patient_id": "hidden-override",
            },
        )


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        ("REJECTED_FOR_BOUND_PATIENT", "DOCUMENT_REJECTED_FOR_BOUND_PATIENT"),
        ("VERIFIED_IDENTITY_REQUIRED", "VERIFIED_IDENTIFIER_REQUIRED"),
        ("SECURITY_ESCALATION_REQUIRED", "POSSIBLE_PRIVACY_INCIDENT"),
        ("INSUFFICIENT_IDENTITY_EVIDENCE", "IDENTITY_REVIEW_INCONCLUSIVE"),
    ],
)
def test_each_closed_outcome_accepts_only_compatible_reasons(outcome, reason):
    request = _json_validate(
        SubmitIdentityReviewDispositionRequest,
        {
            "expected_version": 2,
            "idempotency_key": f"disposition-{outcome.lower()}",
            "outcome": outcome,
            "reason_codes": [reason],
        },
    )
    assert request.outcome.value == outcome
    assert request.reason_codes[0].value == reason


def test_incompatible_or_duplicate_disposition_reasons_are_rejected():
    with pytest.raises(ValidationError):
        _json_validate(
            SubmitIdentityReviewDispositionRequest,
            {
                "expected_version": 2,
                "idempotency_key": "disposition-invalid-1",
                "outcome": "SECURITY_ESCALATION_REQUIRED",
                "reason_codes": ["IDENTITY_REVIEW_INCONCLUSIVE"],
            },
        )
    with pytest.raises(ValidationError):
        _json_validate(
            SubmitIdentityReviewDispositionRequest,
            {
                "expected_version": 2,
                "idempotency_key": "disposition-invalid-2",
                "outcome": "SECURITY_ESCALATION_REQUIRED",
                "reason_codes": [
                    "POSSIBLE_PRIVACY_INCIDENT",
                    "POSSIBLE_PRIVACY_INCIDENT",
                ],
            },
        )


def test_contracts_are_frozen():
    request = _json_validate(
        CreateIdentityReviewCaseRequest, {"idempotency_key": "create-key-0002"}
    )
    with pytest.raises(ValidationError):
        request.idempotency_key = "replacement-key"
