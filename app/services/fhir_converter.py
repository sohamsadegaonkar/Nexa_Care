"""FHIR R4 conversion helpers for Nexa Care clinical exports.

This module intentionally builds lightweight raw dictionaries instead of using
large external FHIR packages. It only maps clinical shard data and never reads
or emits vault identity fields.
"""

from __future__ import annotations

from uuid import uuid4


def _string_items(value: object) -> list[str]:
    """Return non-empty string items from a clinical list field."""

    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def generate_fhir_bundle(patient_id: str, clinical_records: list[dict]) -> dict:
    """Generate a lightweight FHIR R4 Bundle from clinical shard records.

    The returned Bundle is type ``collection``. Diagnoses are represented as
    ``Condition`` resources and prescriptions are represented as
    ``MedicationRequest`` resources. Every generated resource points back to
    ``Patient/{patient_id}`` using a subject reference, but no patient identity
    attributes are included in the bundle.
    """

    subject = {"reference": f"Patient/{patient_id}"}
    entries: list[dict] = []

    for record in clinical_records:
        for diagnosis in _string_items(record.get("diagnoses")):
            resource_id = str(uuid4())
            entries.append({
                "fullUrl": f"urn:uuid:{resource_id}",
                "resource": {
                    "resourceType": "Condition",
                    "id": resource_id,
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
                    "subject": subject,
                },
            })

        for prescription in _string_items(record.get("prescriptions")):
            resource_id = str(uuid4())
            entries.append({
                "fullUrl": f"urn:uuid:{resource_id}",
                "resource": {
                    "resourceType": "MedicationRequest",
                    "id": resource_id,
                    "status": "active",
                    "intent": "order",
                    "medicationCodeableConcept": {"text": prescription},
                    "subject": subject,
                },
            })

    return {
        "resourceType": "Bundle",
        "id": str(uuid4()),
        "type": "collection",
        "entry": entries,
    }
