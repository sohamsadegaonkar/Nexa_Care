"""FHIR R4 conversion helpers for Nexa Care clinical exports.

This module intentionally builds lightweight raw dictionaries instead of using
large external FHIR packages. Current structured clinical records are the
primary source; legacy shard-shaped dictionaries remain supported as fallback
input for older data.

The converter targets the declared Nexa base-R4 subset in
``app.services.fhir_conformance``. External implementation-guide or partner
conformance is a separate qualification boundary.
"""

from __future__ import annotations

import re
from uuid import uuid4

OBSERVATION_INTERPRETATION_SYSTEM = (
    "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"
)
UCUM_SYSTEM = "http://unitsofmeasure.org"

# Only units whose source representation has an unambiguous UCUM mapping are
# promoted to Quantity. Other values remain lossless valueString exports.
UCUM_UNIT_CODES = {
    "%": "%",
    "mg/dL": "mg/dL",
    "mmol/L": "mmol/L",
    "kg": "kg",
    "g": "g",
    "cm": "cm",
    "mm": "mm",
    "bpm": "/min",
    "beats/min": "/min",
    "mmHg": "mm[Hg]",
    "°C": "Cel",
}
_NUMERIC_VALUE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")


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
    """Export a legacy diagnosis without inventing current lifecycle status."""
    return _entry(
        {
            "resourceType": "Condition",
            "code": {"text": diagnosis},
            "subject": {"reference": f"Patient/{patient_id}"},
        }
    )


def _medication_request(patient_id: str, medication: dict | str) -> dict:
    if isinstance(medication, dict):
        name = medication.get("name") or medication.get("text") or "Medication"
        dosage = " ".join(
            str(part)
            for part in [medication.get("strength"), medication.get("frequency")]
            if part
        )
        authored_on = medication.get("prescribed_at")
    else:
        name = medication
        dosage = ""
        authored_on = None

    # Nexa's Medication model intentionally covers active *or historical*
    # prescriptions and does not store a FHIR lifecycle status. R4 requires a
    # MedicationRequest.status, so use its explicit ``unknown`` code rather than
    # asserting ``active``. ``intent=order`` is supported by the source concept
    # of a prescription/order; status remains deliberately unasserted.
    resource = {
        "resourceType": "MedicationRequest",
        "status": "unknown",
        "intent": "order",
        "medicationCodeableConcept": {"text": str(name)},
        "subject": {"reference": f"Patient/{patient_id}"},
    }
    if dosage:
        resource["dosageInstruction"] = [{"text": dosage}]
    if authored_on:
        resource["authoredOn"] = authored_on
    return _entry(resource)


def _numeric_value(value: object) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not _NUMERIC_VALUE.fullmatch(raw):
        return None
    try:
        number = float(raw)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _observation(patient_id: str, record: dict) -> dict:
    label = record.get("test_name") or record.get("type") or "Observation"
    value = record.get("value")
    unit = str(record.get("unit") or "").strip()
    resource = {
        "resourceType": "Observation",
        "status": "final",
        "code": {"text": str(label)},
        "subject": {"reference": f"Patient/{patient_id}"},
    }
    if value is not None:
        numeric = _numeric_value(value)
        ucum_code = UCUM_UNIT_CODES.get(unit) if unit else None
        if numeric is not None and (not unit or ucum_code is not None):
            quantity: dict[str, object] = {"value": numeric}
            if unit:
                quantity.update(
                    {
                        "unit": unit,
                        "system": UCUM_SYSTEM,
                        "code": ucum_code,
                    }
                )
            resource["valueQuantity"] = quantity
        else:
            resource["valueString"] = (
                f"{value} {unit}".strip() if unit else str(value)
            )
    if record.get("recorded_at"):
        resource["effectiveDateTime"] = record["recorded_at"]
    if record.get("is_abnormal"):
        resource["interpretation"] = [
            {
                "coding": [
                    {
                        "system": OBSERVATION_INTERPRETATION_SYSTEM,
                        "code": "A",
                        "display": "Abnormal",
                    }
                ]
            }
        ]
    if record.get("reference_range"):
        resource["referenceRange"] = [{"text": str(record["reference_range"])}]
    return _entry(resource)


def _allergy_intolerance(patient_id: str, allergy: dict) -> dict:
    """Export only allergy semantics represented authoritatively by Nexa.

    The current ``Allergy`` row stores an allergen plus Nexa provenance/routing
    metadata. It does not store FHIR lifecycle status, reaction manifestation, or
    an authoritative clinical criticality assessment. Those elements therefore
    remain absent until the source model can support them truthfully.
    """

    return _entry(
        {
            "resourceType": "AllergyIntolerance",
            "code": {"text": str(allergy.get("allergen") or "Allergy")},
            "patient": {"reference": f"Patient/{patient_id}"},
        }
    )


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

        # The remaining shape is the deprecated clinical-shard fallback. Its
        # explicit ``diagnoses`` array is preserved for backward compatibility;
        # timeline free text is never reinterpreted as a Condition here.
        for diagnosis in _string_items(record.get("diagnoses")):
            entries.append(_condition(patient_id, diagnosis))
        for prescription in _string_items(record.get("prescriptions")):
            entries.append(_medication_request(patient_id, prescription))
        for lab_result in _string_items(record.get("lab_results")):
            entries.append(
                _observation(
                    patient_id, {"test_name": "Legacy lab result", "value": lab_result}
                )
            )

    return {
        "resourceType": "Bundle",
        "id": str(uuid4()),
        "type": "collection",
        "entry": entries,
    }
