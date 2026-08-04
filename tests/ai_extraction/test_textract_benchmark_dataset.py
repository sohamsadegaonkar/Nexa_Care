from __future__ import annotations

import json
import re
import struct
from collections import Counter
from pathlib import Path
from typing import Any

import boto3

BENCHMARK = Path(__file__).parent / "benchmark"
DOCUMENTS = BENCHMARK / "documents"
MANIFEST_PATH = BENCHMARK / "synthetic-manifest.json"
SCHEMA_PATH = BENCHMARK / "manifest.schema.json"
SUPPORTED_FIELDS = {
    "patient_name",
    "phone",
    "aadhaar_abha_id",
    "hba1c",
    "blood_glucose",
    "blood_pressure",
    "heart_rate",
    "medication",
    "diagnosis",
}
EXPECTED_GATES = {
    "field_detection_precision": 0.80,
    "field_detection_recall": 0.75,
    "exact_raw_value_accuracy": 0.70,
    "normalized_value_accuracy": 0.80,
    "unit_accuracy": 0.85,
    "repeated_field_recall": 0.60,
    "source_text_accuracy": 0.70,
    "page_accuracy": 1.00,
    "bounding_box_presence_and_validity": 0.90,
    "field_confidence_provenance": 0.90,
    "patient_identity_mismatch_detection": 1.00,
}


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "null": value is None,
    }[expected]


def _validate_schema(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Validate the JSON Schema subset used by the committed manifest schema."""
    expected = schema.get("type")
    if expected:
        choices = expected if isinstance(expected, list) else [expected]
        assert any(_matches_type(value, choice) for choice in choices), path
    if "enum" in schema:
        assert value in schema["enum"], path
    if isinstance(value, dict):
        for required in schema.get("required", []):
            assert required in value, f"{path}.{required}"
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                _validate_schema(item, properties[key], f"{path}.{key}")
            elif isinstance(schema.get("additionalProperties"), dict):
                _validate_schema(item, schema["additionalProperties"], f"{path}.{key}")
        property_names = schema.get("propertyNames", {})
        if "enum" in property_names:
            assert set(value) <= set(property_names["enum"]), path
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _validate_schema(item, schema["items"], f"{path}[{index}]")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        assert value >= schema.get("minimum", value), path
        assert value <= schema.get("maximum", value), path


def _assert_single_image(path: Path) -> None:
    data = path.read_bytes()
    if path.suffix == ".png":
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
        assert data.count(b"IHDR") == 1 and data.count(b"IEND") == 1
        width, height = struct.unpack(">II", data[16:24])
    else:
        assert path.suffix == ".jpg"
        assert data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9")
        start_of_frame = sum(
            data.count(bytes((0xFF, marker)))
            for marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7)
        )
        assert start_of_frame == 1
        width = height = 1
    assert width > 0 and height > 0


def _load() -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        json.loads(MANIFEST_PATH.read_text(encoding="utf-8")),
        json.loads(SCHEMA_PATH.read_text(encoding="utf-8")),
    )


def test_manifest_validates_against_committed_schema_and_expected_gates():
    manifest, schema = _load()
    _validate_schema(manifest, schema)
    assert manifest["gates"] == EXPECTED_GATES
    assert "fail_closed_quarantine_rate" not in manifest["gates"]


def test_documents_are_unique_local_single_page_images_and_no_aws_call(
    monkeypatch,
):
    calls = 0

    def forbidden_aws_call(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("Dataset validation must not construct an AWS client")

    monkeypatch.setattr(boto3, "client", forbidden_aws_call)
    manifest, _ = _load()
    filenames = [item["file"] for item in manifest["documents"]]
    assert len(filenames) == len(set(filenames)) == 15
    root = DOCUMENTS.resolve()
    for filename in filenames:
        path = (DOCUMENTS / filename).resolve()
        assert path.parent == root and path.is_file()
        _assert_single_image(path)
    assert calls == 0


def test_ground_truth_is_supported_nonempty_synthetic_and_preserves_repetition():
    manifest, _ = _load()
    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    generator_text = (BENCHMARK / "generate_documents.ps1").read_text(encoding="utf-8")
    assert not re.search(r"(?<!\d)\d{12}(?!\d)", manifest_text + generator_text)
    repeated: dict[tuple[str, str], int] = {}
    for document in manifest["documents"]:
        assert document["bound_identity"]
        for key, value in document["bound_identity"].items():
            assert key in {"patient_name", "phone", "aadhaar_abha_id"}
            assert (
                "Synthetic" in value
                or value.startswith("99-")
                or set(value.replace(" ", "")) == {"0"}
            )
        counts = Counter(field["canonical_field"] for field in document["fields"])
        repeated.update(
            {
                (document["file"], field): count
                for field, count in counts.items()
                if count > 1
            }
        )
        for field in document["fields"]:
            assert field["canonical_field"] in SUPPORTED_FIELDS
            assert field["raw_value"].strip()
            assert field["source_text"].strip()
            assert field["page"] == 0
            if field["canonical_field"] == "patient_name":
                assert "Synthetic Patient" in field["raw_value"]
    assert len(repeated) == 7
    assert sum(repeated.values()) == 17
    assert repeated[("12-repeated-conflicting-values.jpg", "hba1c")] == 2
    assert repeated[("12-repeated-conflicting-values.jpg", "blood_pressure")] == 2


def test_inventory_has_tables_incomplete_rows_and_one_identity_mismatch():
    manifest, _ = _load()
    fields = [
        field for document in manifest["documents"] for field in document["fields"]
    ]
    assert len(fields) == 53
    assert sum(field["source_category"] == "CELL" for field in fields) == 11
    mismatches = [
        document
        for document in manifest["documents"]
        if not document["patient_binding_matches"]
    ]
    assert len(mismatches) == 1
    mismatch = mismatches[0]
    assert mismatch["file"] == "15-identity-mismatch.png"
    extracted_name = next(
        field["raw_value"]
        for field in mismatch["fields"]
        if field["canonical_field"] == "patient_name"
    )
    assert extracted_name != mismatch["bound_identity"]["patient_name"]
    incomplete_lab = next(
        item for item in manifest["documents"] if item["file"].startswith("13-")
    )
    incomplete_medication = next(
        item for item in manifest["documents"] if item["file"].startswith("14-")
    )
    assert "unit" not in incomplete_lab["fields"][1]
    assert "frequency" not in incomplete_medication["fields"][1]
