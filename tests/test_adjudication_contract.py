from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from app.models.adjudication import (
    AdjudicatedClinicalField,
    AdjudicationOutcome,
    AdjudicationSubmission,
    LabClinicalField,
    VitalClinicalField,
)


def _submission(fields):
    now = datetime.now(timezone.utc)
    return AdjudicationSubmission(
        submission_id="s",
        case_id="c",
        patient_id="p",
        tenant_id="t",
        source_document_id="d",
        job_id="j",
        routing_id="r",
        decision_id="x",
        reviewer_id="reviewer",
        reviewer_organization_id="org",
        reviewer_role="clinician",
        review_session_id="session",
        attempt_number=1,
        outcome=AdjudicationOutcome.ACCEPTED,
        fields=fields,
        submitted_at=now,
        resolved_at=now,
    )


def test_vital_and_lab_are_strict_finite_typed_payloads():
    now = datetime.now(timezone.utc)
    vital = VitalClinicalField(
        vital_type="HEART_RATE",
        reviewer_entered_value=72.0,
        normalized_value=72.0,
        unit="bpm",
        effective_at=now,
        provenance_type="HUMAN_TRANSCRIBED",
    )
    lab = LabClinicalField(
        test_name="HbA1c",
        reviewer_entered_value=5.7,
        normalized_value=5.7,
        unit="%",
        reference_range="4.0-5.6",
        is_abnormal=True,
        effective_at=now,
        provenance_type="HUMAN_VERIFIED",
    )
    submission = _submission((vital, lab))
    assert submission.canonical_hash() == submission.canonical_hash()
    assert len(submission.canonical_hash()) == 64
    assert "confidence" not in submission.model_dump(mode="json")


@pytest.mark.parametrize(
    "payload",
    [
        {
            "kind": "VITAL",
            "vital_type": "HEART_RATE",
            "reviewer_entered_value": float("nan"),
            "normalized_value": float("nan"),
            "unit": "bpm",
            "effective_at": datetime.now(timezone.utc),
            "provenance_type": "HUMAN_TRANSCRIBED",
        },
        {
            "kind": "LAB_RESULT",
            "test_name": "X",
            "reviewer_entered_value": 1.0,
            "normalized_value": 1.0,
            "unit": "",
            "reference_range": "0-2",
            "is_abnormal": False,
            "effective_at": datetime.now(timezone.utc),
            "provenance_type": "HUMAN_VERIFIED",
        },
        {"kind": "MEDICATION", "value": "unstructured"},
        {"kind": "ALLERGY", "allergen": "unknown"},
    ],
)
def test_unsupported_or_malformed_clinical_payload_fails_closed(payload):
    with pytest.raises(ValidationError):
        TypeAdapter(AdjudicatedClinicalField).validate_python(payload)


def test_nonaccepted_submission_cannot_carry_clinical_values():
    now = datetime.now(timezone.utc)
    field = VitalClinicalField(
        vital_type="SPO2",
        reviewer_entered_value=98.0,
        normalized_value=98.0,
        unit="%",
        effective_at=now,
        provenance_type="HUMAN_VERIFIED",
    )
    with pytest.raises(ValidationError):
        AdjudicationSubmission(
            submission_id="s",
            case_id="c",
            patient_id="p",
            tenant_id="t",
            source_document_id="d",
            job_id="j",
            routing_id="r",
            decision_id="x",
            reviewer_id="reviewer",
            reviewer_organization_id="org",
            reviewer_role="clinician",
            review_session_id="session",
            attempt_number=1,
            outcome=AdjudicationOutcome.REJECTED,
            fields=(field,),
            submitted_at=now,
            resolved_at=now,
        )


def test_contract_is_immutable():
    field = VitalClinicalField(
        vital_type="TEMPERATURE",
        reviewer_entered_value=37.0,
        normalized_value=37.0,
        unit="C",
        effective_at=datetime.now(timezone.utc),
        provenance_type="HUMAN_TRANSCRIBED",
    )
    with pytest.raises(ValidationError):
        field.normalized_value = 40.0
