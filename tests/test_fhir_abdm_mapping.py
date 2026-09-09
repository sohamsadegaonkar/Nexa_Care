from __future__ import annotations

import uuid

from app.services.fhir_converter import (
    ALLERGY_CLINICAL_STATUS_SYSTEM,
    generate_fhir_bundle,
)


def _resource(bundle: dict, resource_type: str) -> dict:
    return next(
        entry["resource"]
        for entry in bundle["entry"]
        if entry["resource"]["resourceType"] == resource_type
    )


def test_abdm_required_fields_are_emitted_only_from_explicit_authority() -> None:
    patient_id = str(uuid.uuid4())
    provider_id = str(uuid.uuid4())
    bundle = generate_fhir_bundle(
        patient_id,
        [
            {
                "record_type": "medication",
                "name": "SyntheticMed",
                "requester_provider_id": provider_id,
            },
            {
                "record_type": "allergy",
                "allergen": "Synthetic Allergen",
                "clinical_status": "active",
            },
        ],
    )

    medication = _resource(bundle, "MedicationRequest")
    assert medication["requester"] == {
        "reference": f"Practitioner/{provider_id}"
    }

    allergy = _resource(bundle, "AllergyIntolerance")
    assert allergy["clinicalStatus"] == {
        "coding": [
            {
                "system": ALLERGY_CLINICAL_STATUS_SYSTEM,
                "code": "active",
            }
        ]
    }


def test_missing_abdm_authority_is_never_fabricated() -> None:
    patient_id = str(uuid.uuid4())
    bundle = generate_fhir_bundle(
        patient_id,
        [
            {"record_type": "medication", "name": "SyntheticMed"},
            {"record_type": "allergy", "allergen": "Synthetic Allergen"},
        ],
    )

    assert "requester" not in _resource(bundle, "MedicationRequest")
    assert "clinicalStatus" not in _resource(bundle, "AllergyIntolerance")


def test_invalid_requester_or_allergy_status_is_not_reinterpreted() -> None:
    patient_id = str(uuid.uuid4())
    bundle = generate_fhir_bundle(
        patient_id,
        [
            {
                "record_type": "medication",
                "name": "SyntheticMed",
                "requester_provider_id": "not-a-provider-uuid",
            },
            {
                "record_type": "allergy",
                "allergen": "Synthetic Allergen",
                "clinical_status": "probably-active",
            },
        ],
    )

    assert "requester" not in _resource(bundle, "MedicationRequest")
    assert "clinicalStatus" not in _resource(bundle, "AllergyIntolerance")
