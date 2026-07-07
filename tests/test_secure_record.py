"""Tests for secure reconstructed record behavior."""

from __future__ import annotations

import pytest

from app.models.secure_record import SecureMergedRecord


def test_secure_record_string_representations_are_redacted() -> None:
    record = SecureMergedRecord(
        {"patient_name": "Jane Doe", "phone": "9999999999"},
        {"diagnoses": ["Diabetes"]},
    )

    assert str(record) == "<SecureMergedRecord: [REDACTED]>"
    assert repr(record) == "<SecureMergedRecord: [REDACTED]>"
    assert "Jane Doe" not in repr(record)
    assert "Diabetes" not in str(record)


@pytest.mark.parametrize("method_name", ["model_dump", "dict", "json"])
def test_secure_record_disables_standard_serialization(method_name: str) -> None:
    record = SecureMergedRecord({"patient_name": "Jane Doe"}, {"diagnoses": ["Diabetes"]})

    with pytest.raises(TypeError):
        getattr(record, method_name)()


def test_to_response_filters_by_namespaced_field_scope() -> None:
    record = SecureMergedRecord(
        {"patient_name": "Jane Doe", "phone": "9999999999"},
        {"diagnoses": ["Diabetes"], "prescriptions": ["Metformin"]},
    )

    response = record.to_response(["pii.patient_name", "clinical.diagnoses"])

    assert response == {
        "pii": {"patient_name": "Jane Doe"},
        "clinical": {"diagnoses": ["Diabetes"]},
    }
    assert "phone" not in str(response)
    assert "Metformin" not in str(response)


def test_to_response_ignores_ambiguous_bare_fields() -> None:
    record = SecureMergedRecord({"status": "verified"}, {"status": "critical"})

    assert record.to_response(["status"]) == {}
