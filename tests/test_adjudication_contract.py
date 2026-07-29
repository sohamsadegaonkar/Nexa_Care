from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from app.models.adjudication import (
    AdjudicatedClinicalField,
    AdjudicationOutcome,
    AdjudicationReasonCode,
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
        review_session_id="session-1234",
        attempt_number=1,
        outcome=AdjudicationOutcome.ACCEPTED,
        fields=fields,
        reason_codes=(AdjudicationReasonCode.SOURCE_VERIFIED,),
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


def test_scalar_blood_pressure_is_not_a_supported_new_submission():
    with pytest.raises(ValidationError):
        VitalClinicalField(
            vital_type="BLOOD_PRESSURE",
            reviewer_entered_value=120.0,
            normalized_value=120.0,
            unit="mmHg",
            effective_at=datetime.now(timezone.utc),
            provenance_type="HUMAN_TRANSCRIBED",
        )


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
            review_session_id="session-1234",
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


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (AdjudicationOutcome.ACCEPTED, "SOURCE_VERIFIED"),
        (AdjudicationOutcome.REJECTED, "ILLEGIBLE_SOURCE"),
        (
            AdjudicationOutcome.NEEDS_SPECIALIST_REVIEW,
            "SPECIALIST_INTERPRETATION_REQUIRED",
        ),
    ],
)
def test_reason_codes_are_closed_and_outcome_scoped(outcome, reason):
    now = datetime.now(timezone.utc)
    fields = ()
    if outcome is AdjudicationOutcome.ACCEPTED:
        fields = (
            VitalClinicalField(
                vital_type="HEART_RATE",
                reviewer_entered_value=72.0,
                normalized_value=72.0,
                unit="bpm",
                effective_at=now,
                provenance_type="HUMAN_VERIFIED",
            ),
        )
    result = AdjudicationSubmission(
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
        review_session_id="session-1234",
        attempt_number=1,
        outcome=outcome,
        fields=fields,
        submitted_at=now,
        resolved_at=now,
        reason_codes=(AdjudicationReasonCode(reason),),
    )
    assert result.reason_codes[0].value == reason


@pytest.mark.parametrize(
    "reasons",
    [
        ("heart rate was 180",),
        ("ARBITRARY_REASON",),
        ("SOURCE_VERIFIED", "SOURCE_VERIFIED"),
        (
            "SOURCE_VERIFIED",
            "MANUAL_TRANSCRIPTION",
            "CORRECTED_AGAINST_SOURCE",
            "SOURCE_VERIFIED",
            "MANUAL_TRANSCRIPTION",
        ),
        ("ILLEGIBLE_SOURCE",),
    ],
)
def test_reason_codes_reject_text_unknown_duplicates_size_and_wrong_outcome(reasons):
    now = datetime.now(timezone.utc)
    field = VitalClinicalField(
        vital_type="HEART_RATE",
        reviewer_entered_value=72.0,
        normalized_value=72.0,
        unit="bpm",
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
            review_session_id="session-1234",
            attempt_number=1,
            outcome=AdjudicationOutcome.ACCEPTED,
            fields=(field,),
            submitted_at=now,
            resolved_at=now,
            reason_codes=reasons,
        )
