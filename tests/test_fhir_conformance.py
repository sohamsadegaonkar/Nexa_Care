from __future__ import annotations

import uuid

from app.services.fhir_conformance import (
    CONFORMANCE_CONTRACT,
    EXTERNAL_PROFILE_VALIDATION_STATUS,
    UCUM_SYSTEM,
    validate_fhir_r4_bundle,
)
from app.services.fhir_converter import generate_fhir_bundle


def test_structured_export_satisfies_declared_internal_r4_subset() -> None:
    patient_id = str(uuid.uuid4())
    bundle = generate_fhir_bundle(
        patient_id,
        [
            {
                "record_type": "lab",
                "test_name": "HbA1c",
                "value": "7.2",
                "unit": "%",
                "recorded_at": "2026-09-09T10:00:00+00:00",
                "is_abnormal": True,
                "reference_range": "4.0-5.6",
            },
            {
                "record_type": "vital",
                "type": "BP",
                "value": "130/85",
                "unit": "mmHg",
                "recorded_at": "2026-09-09T10:01:00+00:00",
            },
            {
                "record_type": "medication",
                "name": "SyntheticMed",
                "strength": "500 mg",
                "frequency": "twice daily",
                "prescribed_at": "2026-09-09T10:02:00+00:00",
            },
            {
                "record_type": "allergy",
                "allergen": "Synthetic Allergen",
                "severity": "Severe",
                "risk_level": "HIGH_RISK",
            },
            {
                "record_type": "timeline_diagnosis",
                "summary": "Synthetic diagnosis",
                "occurred_at": "2026-09-09T10:03:00+00:00",
            },
        ],
    )

    report = validate_fhir_r4_bundle(bundle, expected_patient_id=patient_id)

    assert report["valid"] is True
    assert report["error_count"] == 0
    assert report["contract"] == CONFORMANCE_CONTRACT
    assert report["external_profile_validation"] == EXTERNAL_PROFILE_VALIDATION_STATUS
    assert report["resource_counts"] == {
        "AllergyIntolerance": 1,
        "Condition": 1,
        "MedicationRequest": 1,
        "Observation": 2,
    }

    resources = [entry["resource"] for entry in bundle["entry"]]
    lab = next(
        item
        for item in resources
        if item["resourceType"] == "Observation" and item["code"]["text"] == "HbA1c"
    )
    assert lab["valueQuantity"] == {
        "value": 7.2,
        "unit": "%",
        "system": UCUM_SYSTEM,
        "code": "%",
    }

    blood_pressure = next(
        item
        for item in resources
        if item["resourceType"] == "Observation" and item["code"]["text"] == "BP"
    )
    assert blood_pressure["valueString"] == "130/85 mmHg"
    assert "valueQuantity" not in blood_pressure

    allergy = next(
        item for item in resources if item["resourceType"] == "AllergyIntolerance"
    )
    assert allergy["criticality"] == "high"
    assert "reaction" not in allergy

    condition = next(item for item in resources if item["resourceType"] == "Condition")
    assert condition["recordedDate"] == "2026-09-09T10:03:00+00:00"


def test_allergy_reaction_without_required_manifestation_is_rejected() -> None:
    patient_id = str(uuid.uuid4())
    bundle = generate_fhir_bundle(
        patient_id,
        [{"record_type": "allergy", "allergen": "Synthetic Allergen"}],
    )
    resource = bundle["entry"][0]["resource"]
    resource["reaction"] = [{"severity": "severe"}]

    report = validate_fhir_r4_bundle(bundle, expected_patient_id=patient_id)

    assert report["valid"] is False
    assert any("reaction[0].manifestation" in error for error in report["errors"])


def test_reference_to_different_patient_is_rejected() -> None:
    patient_id = str(uuid.uuid4())
    other_patient = str(uuid.uuid4())
    bundle = generate_fhir_bundle(
        patient_id,
        [
            {
                "record_type": "medication",
                "name": "SyntheticMed",
                "prescribed_at": "2026-09-09T10:02:00+00:00",
            }
        ],
    )
    bundle["entry"][0]["resource"]["subject"]["reference"] = (
        f"Patient/{other_patient}"
    )

    report = validate_fhir_r4_bundle(bundle, expected_patient_id=patient_id)

    assert report["valid"] is False
    assert any("reference must match requested patient" in error for error in report["errors"])


def test_quantity_code_requires_system_and_nexa_uses_ucum() -> None:
    patient_id = str(uuid.uuid4())
    bundle = generate_fhir_bundle(
        patient_id,
        [
            {
                "record_type": "lab",
                "test_name": "Synthetic Lab",
                "value": "1.5",
                "unit": "mg/dL",
            }
        ],
    )
    quantity = bundle["entry"][0]["resource"]["valueQuantity"]
    quantity.pop("system")

    report = validate_fhir_r4_bundle(bundle, expected_patient_id=patient_id)

    assert report["valid"] is False
    assert any("required when Quantity.code is present" in error for error in report["errors"])


def test_empty_collection_bundle_is_valid_internal_export() -> None:
    patient_id = str(uuid.uuid4())
    bundle = generate_fhir_bundle(patient_id, [])

    report = validate_fhir_r4_bundle(bundle, expected_patient_id=patient_id)

    assert report["valid"] is True
    assert report["resource_counts"] == {}
