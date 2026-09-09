#!/usr/bin/env python3
"""Generate deterministic synthetic Nexa FHIR fixtures for ABDM profile validation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.fhir_conformance import validate_fhir_r4_bundle  # noqa: E402
from app.services.fhir_converter import generate_fhir_bundle  # noqa: E402

PATIENT_ID = "11111111-1111-4111-8111-111111111111"
PROFILE_BY_RESOURCE = {
    "Condition": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Condition",
    "MedicationRequest": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/MedicationRequest",
    "Observation": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Observation",
    "AllergyIntolerance": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/AllergyIntolerance",
}


def _records() -> list[dict]:
    return [
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
        },
        {"diagnoses": ["Synthetic diagnosis"]},
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    bundle = generate_fhir_bundle(PATIENT_ID, _records())
    internal = validate_fhir_r4_bundle(bundle, expected_patient_id=PATIENT_ID)
    if internal.get("valid") is not True:
        raise SystemExit("generated qualification bundle failed Nexa internal validation")

    (args.output / "bundle.json").write_text(
        json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    manifest: list[dict[str, str]] = []
    counters: dict[str, int] = {}
    for entry in bundle.get("entry", []):
        resource = entry["resource"]
        resource_type = resource["resourceType"]
        profile = PROFILE_BY_RESOURCE.get(resource_type)
        if profile is None:
            raise SystemExit(f"unsupported qualification resource type: {resource_type}")
        counters[resource_type] = counters.get(resource_type, 0) + 1
        filename = f"{resource_type}-{counters[resource_type]}.json"
        (args.output / filename).write_text(
            json.dumps(resource, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        manifest.append({"file": filename, "profile": profile})

    (args.output / "manifest.json").write_text(
        json.dumps(
            {
                "synthetic_only": True,
                "patient_id": PATIENT_ID,
                "abdm_ig_package": "ndhm.in#6.5.0",
                "fhir_version": "4.0.1",
                "resources": manifest,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"generated": len(manifest), "internal_valid": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
