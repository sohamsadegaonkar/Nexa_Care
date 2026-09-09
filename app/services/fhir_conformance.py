"""Machine-checkable internal FHIR R4 export contract for Slice 7D.

This validator checks the exact base-R4 subset Nexa currently claims to emit. It
is deliberately narrower than the official HL7 validator and does not claim an
external implementation-guide, certification, or partner-system PASS.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from typing import Any

FHIR_VERSION = "4.0.1"
CONFORMANCE_CONTRACT = "nexa-fhir-r4-base-v1"
EXTERNAL_PROFILE_VALIDATION_STATUS = "NOT_RUN"

FHIR_ID = re.compile(r"^[A-Za-z0-9\-.]{1,64}$")
PATIENT_REFERENCE = re.compile(
    r"^Patient/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

ALLOWED_RESOURCE_TYPES = frozenset(
    {"Condition", "MedicationRequest", "Observation", "AllergyIntolerance"}
)
CONDITION_CLINICAL_STATUS_SYSTEM = (
    "http://terminology.hl7.org/CodeSystem/condition-clinical"
)
ALLERGY_CLINICAL_STATUS_SYSTEM = (
    "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical"
)
UCUM_SYSTEM = "http://unitsofmeasure.org"

MEDICATION_STATUSES = frozenset(
    {
        "active",
        "on-hold",
        "cancelled",
        "completed",
        "entered-in-error",
        "stopped",
        "draft",
        "unknown",
    }
)
MEDICATION_INTENTS = frozenset(
    {
        "proposal",
        "plan",
        "order",
        "original-order",
        "reflex-order",
        "filler-order",
        "instance-order",
        "option",
    }
)
OBSERVATION_STATUSES = frozenset(
    {
        "registered",
        "preliminary",
        "final",
        "amended",
        "corrected",
        "cancelled",
        "entered-in-error",
        "unknown",
    }
)
ALLERGY_CRITICALITY = frozenset({"low", "high", "unable-to-assess"})
ALLERGY_REACTION_SEVERITY = frozenset({"mild", "moderate", "severe"})
CONDITION_CLINICAL_STATUSES = frozenset(
    {"active", "recurrence", "relapse", "inactive", "remission", "resolved"}
)
ALLERGY_CLINICAL_STATUSES = frozenset({"active", "inactive", "resolved"})


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_datetime(value: Any) -> bool:
    if not _nonempty_text(value):
        return False
    raw = value.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(raw)
    except ValueError:
        return False
    return True


def _coding_contains(
    concept: Any,
    *,
    system: str,
    allowed_codes: frozenset[str],
) -> bool:
    if not isinstance(concept, Mapping):
        return False
    coding = concept.get("coding")
    if not isinstance(coding, list):
        return False
    return any(
        isinstance(item, Mapping)
        and item.get("system") == system
        and item.get("code") in allowed_codes
        for item in coding
    )


def _concept_has_content(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    if _nonempty_text(value.get("text")):
        return True
    coding = value.get("coding")
    return isinstance(coding, list) and any(
        isinstance(item, Mapping) and _nonempty_text(item.get("code"))
        for item in coding
    )


def _reference(
    resource: Mapping[str, Any],
    field: str,
    *,
    expected_patient_id: str | None,
    errors: list[str],
    path: str,
) -> None:
    holder = resource.get(field)
    reference = holder.get("reference") if isinstance(holder, Mapping) else None
    if not isinstance(reference, str) or not PATIENT_REFERENCE.fullmatch(reference):
        errors.append(f"{path}.{field}: Patient UUID reference required")
        return
    if expected_patient_id is not None and reference != f"Patient/{expected_patient_id}":
        errors.append(f"{path}.{field}: reference must match requested patient")


def _validate_condition(
    resource: Mapping[str, Any],
    *,
    expected_patient_id: str | None,
    errors: list[str],
    path: str,
) -> None:
    if not _coding_contains(
        resource.get("clinicalStatus"),
        system=CONDITION_CLINICAL_STATUS_SYSTEM,
        allowed_codes=CONDITION_CLINICAL_STATUSES,
    ):
        errors.append(f"{path}.clinicalStatus: recognized R4 condition status required")
    if not _concept_has_content(resource.get("code")):
        errors.append(f"{path}.code: non-empty concept required")
    _reference(
        resource,
        "subject",
        expected_patient_id=expected_patient_id,
        errors=errors,
        path=path,
    )
    recorded = resource.get("recordedDate")
    if recorded is not None and not _valid_datetime(recorded):
        errors.append(f"{path}.recordedDate: valid FHIR dateTime required")


def _validate_medication_request(
    resource: Mapping[str, Any],
    *,
    expected_patient_id: str | None,
    errors: list[str],
    path: str,
) -> None:
    if resource.get("status") not in MEDICATION_STATUSES:
        errors.append(f"{path}.status: recognized R4 medication status required")
    if resource.get("intent") not in MEDICATION_INTENTS:
        errors.append(f"{path}.intent: recognized R4 medication intent required")
    if not _concept_has_content(resource.get("medicationCodeableConcept")):
        errors.append(f"{path}.medicationCodeableConcept: non-empty concept required")
    _reference(
        resource,
        "subject",
        expected_patient_id=expected_patient_id,
        errors=errors,
        path=path,
    )
    authored = resource.get("authoredOn")
    if authored is not None and not _valid_datetime(authored):
        errors.append(f"{path}.authoredOn: valid FHIR dateTime required")


def _validate_quantity(value: Any, errors: list[str], path: str) -> None:
    if not isinstance(value, Mapping):
        errors.append(f"{path}: Quantity object required")
        return
    number = value.get("value")
    if isinstance(number, bool) or not isinstance(number, (int, float)):
        errors.append(f"{path}.value: numeric value required")
    code = value.get("code")
    system = value.get("system")
    if code is not None and not _nonempty_text(code):
        errors.append(f"{path}.code: non-empty code required when present")
    if code is not None and not _nonempty_text(system):
        errors.append(f"{path}.system: required when Quantity.code is present")
    if system is not None and system != UCUM_SYSTEM:
        errors.append(f"{path}.system: Nexa coded quantities must use UCUM")


def _validate_observation(
    resource: Mapping[str, Any],
    *,
    expected_patient_id: str | None,
    errors: list[str],
    path: str,
) -> None:
    if resource.get("status") not in OBSERVATION_STATUSES:
        errors.append(f"{path}.status: recognized R4 observation status required")
    if not _concept_has_content(resource.get("code")):
        errors.append(f"{path}.code: non-empty concept required")
    _reference(
        resource,
        "subject",
        expected_patient_id=expected_patient_id,
        errors=errors,
        path=path,
    )
    value_fields = [name for name in ("valueString", "valueQuantity") if name in resource]
    if len(value_fields) != 1:
        errors.append(f"{path}.value[x]: exactly one supported value representation required")
    elif value_fields[0] == "valueString":
        if not _nonempty_text(resource.get("valueString")):
            errors.append(f"{path}.valueString: non-empty string required")
    else:
        _validate_quantity(resource.get("valueQuantity"), errors, f"{path}.valueQuantity")
    effective = resource.get("effectiveDateTime")
    if effective is not None and not _valid_datetime(effective):
        errors.append(f"{path}.effectiveDateTime: valid FHIR dateTime required")


def _validate_allergy_intolerance(
    resource: Mapping[str, Any],
    *,
    expected_patient_id: str | None,
    errors: list[str],
    path: str,
) -> None:
    clinical_status = resource.get("clinicalStatus")
    if clinical_status is not None and not _coding_contains(
        clinical_status,
        system=ALLERGY_CLINICAL_STATUS_SYSTEM,
        allowed_codes=ALLERGY_CLINICAL_STATUSES,
    ):
        errors.append(f"{path}.clinicalStatus: recognized R4 allergy status required")
    if not _concept_has_content(resource.get("code")):
        errors.append(f"{path}.code: non-empty concept required")
    _reference(
        resource,
        "patient",
        expected_patient_id=expected_patient_id,
        errors=errors,
        path=path,
    )
    criticality = resource.get("criticality")
    if criticality is not None and criticality not in ALLERGY_CRITICALITY:
        errors.append(f"{path}.criticality: recognized R4 criticality required")
    reactions = resource.get("reaction")
    if reactions is None:
        return
    if not isinstance(reactions, list):
        errors.append(f"{path}.reaction: array required")
        return
    for index, reaction in enumerate(reactions):
        reaction_path = f"{path}.reaction[{index}]"
        if not isinstance(reaction, Mapping):
            errors.append(f"{reaction_path}: object required")
            continue
        manifestations = reaction.get("manifestation")
        if not isinstance(manifestations, list) or not manifestations:
            errors.append(f"{reaction_path}.manifestation: at least one concept required")
        elif not all(_concept_has_content(item) for item in manifestations):
            errors.append(f"{reaction_path}.manifestation: non-empty concepts required")
        severity = reaction.get("severity")
        if severity is not None and severity not in ALLERGY_REACTION_SEVERITY:
            errors.append(f"{reaction_path}.severity: mild, moderate, or severe required")


def validate_fhir_r4_bundle(
    bundle: Mapping[str, Any], *, expected_patient_id: str | None = None
) -> dict[str, Any]:
    """Validate Nexa's declared base-R4 export subset and return a safe report."""

    errors: list[str] = []
    counts: Counter[str] = Counter()

    if bundle.get("resourceType") != "Bundle":
        errors.append("$.resourceType: Bundle required")
    if bundle.get("type") != "collection":
        errors.append("$.type: collection required")
    bundle_id = bundle.get("id")
    if not isinstance(bundle_id, str) or not FHIR_ID.fullmatch(bundle_id):
        errors.append("$.id: valid FHIR id required")

    entries = bundle.get("entry")
    if not isinstance(entries, list):
        errors.append("$.entry: array required")
        entries = []

    full_urls: set[str] = set()
    for index, entry in enumerate(entries):
        path = f"$.entry[{index}]"
        if not isinstance(entry, Mapping):
            errors.append(f"{path}: object required")
            continue
        full_url = entry.get("fullUrl")
        resource = entry.get("resource")
        if not isinstance(full_url, str) or not full_url.startswith("urn:uuid:"):
            errors.append(f"{path}.fullUrl: urn:uuid identity required")
        elif full_url in full_urls:
            errors.append(f"{path}.fullUrl: duplicate fullUrl forbidden")
        else:
            full_urls.add(full_url)
        if not isinstance(resource, Mapping):
            errors.append(f"{path}.resource: object required")
            continue
        resource_type = resource.get("resourceType")
        if resource_type not in ALLOWED_RESOURCE_TYPES:
            errors.append(f"{path}.resource.resourceType: outside declared Nexa R4 subset")
            continue
        counts[str(resource_type)] += 1
        resource_id = resource.get("id")
        if not isinstance(resource_id, str) or not FHIR_ID.fullmatch(resource_id):
            errors.append(f"{path}.resource.id: valid FHIR id required")
        elif isinstance(full_url, str) and full_url != f"urn:uuid:{resource_id}":
            errors.append(f"{path}.fullUrl: must identify the contained resource")

        resource_path = f"{path}.resource"
        if resource_type == "Condition":
            _validate_condition(
                resource,
                expected_patient_id=expected_patient_id,
                errors=errors,
                path=resource_path,
            )
        elif resource_type == "MedicationRequest":
            _validate_medication_request(
                resource,
                expected_patient_id=expected_patient_id,
                errors=errors,
                path=resource_path,
            )
        elif resource_type == "Observation":
            _validate_observation(
                resource,
                expected_patient_id=expected_patient_id,
                errors=errors,
                path=resource_path,
            )
        else:
            _validate_allergy_intolerance(
                resource,
                expected_patient_id=expected_patient_id,
                errors=errors,
                path=resource_path,
            )

    return {
        "contract": CONFORMANCE_CONTRACT,
        "fhir_version": FHIR_VERSION,
        "valid": not errors,
        "error_count": len(errors),
        "errors": errors,
        "resource_counts": dict(sorted(counts.items())),
        "external_profile_validation": EXTERNAL_PROFILE_VALIDATION_STATUS,
    }
