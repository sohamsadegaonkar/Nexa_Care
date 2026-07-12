"""Guardrails for legacy/current subsystem reconciliation."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_emergency_snapshot_uses_structured_records_before_legacy_projection():
    code = read("app/services/emergency_snapshot_service.py")
    assert "from app.models.patient_records import" in code
    for model in ["Allergy", "Medication", "Vitals", "LabResult"]:
        assert model in code
    assert "legacy_emergency_projection" in code
    assert "structured_patient_records" in code


def test_fhir_export_uses_structured_records_before_legacy_clinical_fallback():
    code = read("app/api/v2/fhir_routes.py")
    assert "from app.models.patient_records import" in code
    for model in ["Allergy", "Medication", "Vitals", "LabResult"]:
        assert model in code
    assert "_fetch_structured_records" in code
    assert "_fetch_legacy_clinical_records" in code
    assert "structured_patient_records" in code
    assert "legacy_nexa_clinical_fallback" in code


def test_merge_and_card_redirect_have_duplicate_and_cycle_guards():
    merge_code = read("app/services/merge_service.py")
    redirect_code = read("app/services/card_redirect_service.py")
    model_code = read("app/models/patient_tombstone.py")
    assert "Duplicate tombstones" in merge_code
    assert "cycle" in merge_code.lower()
    assert "TombstoneIntegrityError" in redirect_code
    assert "Duplicate tombstones" in redirect_code
    assert "cycle" in redirect_code.lower()
    assert "uq_patient_tombstones_old_patient_uuid" in model_code


def test_websocket_push_checks_redis_keyspace_notifications_when_enabled():
    code = read("app/api/v2/assurance_routes.py")
    assert "PUSH_STATUS_TRANSPORT" in code
    assert "_check_keyspace_notifications" in code
    assert "notify-keyspace-events" in code
    assert "websocket_unavailable" in code
    assert "fallback" in code and "poll" in code
