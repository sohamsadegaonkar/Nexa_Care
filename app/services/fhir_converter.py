"""FHIR R4 conversion helpers for Nexa Care clinical exports.

This module intentionally builds lightweight raw dictionaries instead of using
large external FHIR packages. Current structured clinical records are the
primary source; legacy shard-shaped dictionaries remain supported as fallback
input for older data.
"""

from __future__ import annotations

from uuid import uuid4


def _string_items(value: object) -> list[str]:
    """Return non-empty string items from a clinical list field."""

    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _entry(resource: dict) -> dict:
    resource_id = str(uuid4())
    resource.setdefault("id", resource_id)
    return {"fullUrl": f"urn:uuid:{resource_id}", "resource": resource}


def _condition(patient_id: str, diagnosis: str) -> dict:
    return _entry({
        "resourceType": "Condition",
        "clinicalStatus": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                    "code": "active",
                    "display": "Active",
                }
            ]
        },
        "code": {"text": diagnosis},
        "subject": {"reference": f"Patient/{patient_id}"},
    })


def _medication_request(patient_id: str, medication: dict | str) -> dict:
    if isinstance(medication, dict):
        name = medication.get("name") or medication.get("text") or "Medication"
        dosage = " ".join(str(part) for part in [medication.get("strength"), medication.get("frequency")] if part)
        authored_on = medication.get("prescribed_at")
    else:
        name = medication
        dosage = ""
        authored_on = None

    resource = {
        "resourceType": "MedicationRequest",
        "status": "active",
        "intent": "order",
        "medicationCodeableConcept": {"text": str(name)},
        "subject": {"reference": f"Patient/{patient_id}"},
    }
    if dosage:
        resource["dosageInstruction"] = [{"text": dosage}]
    if authored_on:
        resource["authoredOn"] = authored_on
    return _entry(resource)


def _observation(patient_id: str, record: dict) -> dict:
    label = record.get("test_name") or record.get("type") or "Observation"
    value = record.get("value")
    unit = record.get("unit")
    resource = {
        "resourceType": "Observation",
        "status": "final",
        "code": {"text": str(label)},
        "subject": {"reference": f"Patient/{patient_id}"},
    }
    if value is not None:
        resource["valueString"] = f"{value} {unit}".strip() if unit else str(value)
    if record.get("recorded_at"):
        resource["effectiveDateTime"] = record["recorded_at"]
    if record.get("is_abnormal"):
        resource["interpretation"] = [{"coding": [{"code": "A", "display": "Abnormal"}]}]
    if record.get("reference_range"):
        resource["referenceRange"] = [{"text": str(record["reference_range"])}]
    return _entry(resource)


def _allergy_intolerance(patient_id: str, allergy: dict) -> dict:
    return _entry({
        "resourceType": "AllergyIntolerance",
        "clinicalStatus": {"coding": [{"code": "active"}]},
        "code": {"text": str(allergy.get("allergen") or "Allergy")},
        "patient": {"reference": f"Patient/{patient_id}"},
        "criticality": "high" if str(allergy.get("risk_level") or "").upper() in {"HIGH_RISK", "CRITICAL_RISK"} else "unable-to-assess",
        "reaction": [{"severity": str(allergy.get("severity") or "unknown").lower()}],
    })


def generate_fhir_bundle(patient_id: str, clinical_records: list[dict]) -> dict:
    """Generate a lightweight FHIR R4 collection Bundle from clinical records."""

    entries: list[dict] = []

    for record in clinical_records:
        record_type = record.get("record_type")
        if record_type == "medication":
            entries.append(_medication_request(patient_id, record))
            continue
        if record_type in {"vital", "lab"}:
            entries.append(_observation(patient_id, record))
            continue
        if record_type == "allergy":
            entries.append(_allergy_intolerance(patient_id, record))
            continue
        if record_type == "timeline_diagnosis":
            entries.append(_condition(patient_id, str(record.get("summary") or record.get("diagnosis") or "Diagnosis")))
            continue

        for diagnosis in _string_items(record.get("diagnoses")):
            entries.append(_condition(patient_id, diagnosis))
        for prescription in _string_items(record.get("prescriptions")):
            entries.append(_medication_request(patient_id, prescription))
        for lab_result in _string_items(record.get("lab_results")):
            entries.append(_observation(patient_id, {"test_name": "Legacy lab result", "value": lab_result}))

    return {
        "resourceType": "Bundle",
        "id": str(uuid4()),
        "type": "collection",
        "entry": entries,
    }
