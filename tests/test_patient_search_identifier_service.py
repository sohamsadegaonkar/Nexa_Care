"""Pure qualification for Slice 10A searchable-identifier primitives."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.patient_discovery_index_config import (
    PatientDiscoveryIndexConfigError,
    get_patient_discovery_index_config,
)
from app.models.patient_search_identifier import PatientSearchIdentifier
from app.services.patient_search_identifier_service import (
    PatientSearchIdentifierError,
    phone_index_fingerprints,
)


ROOT = Path(__file__).resolve().parents[1]


def _configure(monkeypatch, *, active: int = 2) -> None:
    monkeypatch.setenv(
        "PATIENT_DISCOVERY_INDEX_HMAC_KEYS_JSON",
        json.dumps({"1": "a" * 48, "2": "b" * 48}),
    )
    monkeypatch.setenv("PATIENT_DISCOVERY_INDEX_ACTIVE_KEY_VERSION", str(active))


def test_keyring_requires_explicit_independent_secret_material(monkeypatch) -> None:
    monkeypatch.delenv("PATIENT_DISCOVERY_INDEX_HMAC_KEYS_JSON", raising=False)
    monkeypatch.delenv("PATIENT_DISCOVERY_INDEX_ACTIVE_KEY_VERSION", raising=False)
    with pytest.raises(PatientDiscoveryIndexConfigError):
        get_patient_discovery_index_config()

    monkeypatch.setenv(
        "PATIENT_DISCOVERY_INDEX_HMAC_KEYS_JSON",
        json.dumps({"1": "x" * 48, "2": "x" * 48}),
    )
    monkeypatch.setenv("PATIENT_DISCOVERY_INDEX_ACTIVE_KEY_VERSION", "2")
    with pytest.raises(PatientDiscoveryIndexConfigError):
        get_patient_discovery_index_config()


def test_keyring_rejects_unconfigured_active_version(monkeypatch) -> None:
    monkeypatch.setenv(
        "PATIENT_DISCOVERY_INDEX_HMAC_KEYS_JSON",
        json.dumps({"1": "a" * 48}),
    )
    monkeypatch.setenv("PATIENT_DISCOVERY_INDEX_ACTIVE_KEY_VERSION", "2")
    with pytest.raises(PatientDiscoveryIndexConfigError):
        get_patient_discovery_index_config()


def test_phone_fingerprint_is_deterministic_keyed_and_versioned(monkeypatch) -> None:
    _configure(monkeypatch)
    active, first = phone_index_fingerprints("+91 98765 43210")
    _, second = phone_index_fingerprints("9876543210")

    assert active == 2
    assert first == second
    assert set(first) == {1, 2}
    assert first[1] != first[2]
    assert all(len(value) == 64 for value in first.values())
    assert all("9876543210" not in value for value in first.values())


def test_phone_fingerprint_changes_when_key_material_changes(monkeypatch) -> None:
    _configure(monkeypatch, active=1)
    _, baseline = phone_index_fingerprints("9876543210")

    monkeypatch.setenv(
        "PATIENT_DISCOVERY_INDEX_HMAC_KEYS_JSON",
        json.dumps({"1": "c" * 48}),
    )
    monkeypatch.setenv("PATIENT_DISCOVERY_INDEX_ACTIVE_KEY_VERSION", "1")
    _, rotated = phone_index_fingerprints("9876543210")

    assert baseline[1] != rotated[1]


def test_invalid_phone_never_produces_search_index(monkeypatch) -> None:
    _configure(monkeypatch)
    with pytest.raises(PatientSearchIdentifierError) as exc_info:
        phone_index_fingerprints("not-a-phone")
    assert exc_info.value.code == "PATIENT_SEARCH_IDENTIFIER_INVALID"


def test_search_identifier_schema_contains_no_raw_pii_column() -> None:
    columns = set(PatientSearchIdentifier.__table__.columns.keys())
    assert "phone" not in columns
    assert "normalized_phone" not in columns
    assert "raw_value" not in columns
    assert "normalized_value" not in columns
    assert "value_hmac" in columns


def test_low_entropy_phone_mode_is_public_only_with_explicit_hardening() -> None:
    source = (ROOT / "app/api/v2/patient_discovery_routes.py").read_text()
    assert 'Literal["NEXA_PUBLIC_ID", "PHONE", "QR_PUBLIC_ID"]' in source
    assert "require_recent_mfa_for_phone_discovery" in source
    assert "enforce_patient_discovery_budget" in source
    assert "resolve_verified_phone_patient" in source
