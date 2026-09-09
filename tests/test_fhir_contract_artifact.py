from __future__ import annotations

import json
from pathlib import Path

from app.services.fhir_conformance import (
    ALLOWED_RESOURCE_TYPES,
    CONFORMANCE_CONTRACT,
    EXTERNAL_PROFILE_VALIDATION_STATUS,
    FHIR_VERSION,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "interop" / "fhir-r4-export-contract.json"


def test_machine_readable_fhir_contract_matches_runtime_validator() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    assert contract["contract"] == CONFORMANCE_CONTRACT
    assert contract["fhir_version"] == FHIR_VERSION
    assert contract["bundle"] == {
        "resource_type": "Bundle",
        "profile": "http://hl7.org/fhir/StructureDefinition/Bundle",
        "type": "collection",
    }
    assert {item["resource_type"] for item in contract["emitted_resources"]} == set(
        ALLOWED_RESOURCE_TYPES
    )
    assert all(
        item["profile"]
        == f"http://hl7.org/fhir/StructureDefinition/{item['resource_type']}"
        for item in contract["emitted_resources"]
    )
    assert contract["patient_reference"]["patient_resource_emitted"] is False
    assert contract["provenance"]["fhir_provenance_resource_emitted"] is False
    assert contract["external_implementation_guide"] is None
    assert (
        contract["external_profile_validation"]
        == EXTERNAL_PROFILE_VALIDATION_STATUS
    )
    assert contract["external_partner_interoperability"] == "NOT_RUN"
