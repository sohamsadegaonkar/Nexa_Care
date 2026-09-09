#!/usr/bin/env python3
"""Validate a Nexa FHIR export against the declared internal R4 subset."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.fhir_conformance import validate_fhir_r4_bundle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path, help="FHIR Bundle JSON file")
    parser.add_argument(
        "--patient-id",
        help="optional expected patient UUID for subject/reference binding",
    )
    arguments = parser.parse_args()

    try:
        payload = json.loads(arguments.bundle.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print(
            json.dumps(
                {
                    "valid": False,
                    "error_count": 1,
                    "errors": ["bundle: JSON object could not be loaded"],
                },
                sort_keys=True,
            )
        )
        return 1

    if not isinstance(payload, dict):
        print(
            json.dumps(
                {
                    "valid": False,
                    "error_count": 1,
                    "errors": ["bundle: JSON object required"],
                },
                sort_keys=True,
            )
        )
        return 1

    report = validate_fhir_r4_bundle(
        payload,
        expected_patient_id=arguments.patient_id,
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
