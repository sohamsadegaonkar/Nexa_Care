"""Regression checks for the post-Slice-6 authority contract documentation."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_canonical_consent_contract_is_v3_and_explicitly_versioned() -> None:
    contract = _read("docs/API-CONTRACTS.md")

    for route in (
        "/api/v2/consent/v3/request",
        "/api/v2/consent/v3/challenge/{request_id}",
        "/api/v2/consent/v3/approve-signed",
        "/api/v2/consent/v3/{request_id}/claim-access",
    ):
        assert route in contract

    assert "nexa-consent-v3" in contract
    assert "SIGNED_CONSENT_V2_ACCESS_RETIRED" in contract
    assert "**Endpoint:** `POST /api/v2/consent/approve-signed`" not in contract


def test_canonical_device_contract_covers_current_authority_paths() -> None:
    contract = _read("docs/API-CONTRACTS.md")

    for route in (
        "/api/v2/patient/devices/enroll",
        "/api/v2/patient/devices/{device_id}/trusted-enrollment/challenge",
        "/api/v2/patient/devices/{device_id}/trusted-enrollment/authorize",
        "/api/v2/patient/devices/recovery/otp/send",
        "/api/v2/patient/devices/recovery/otp/verify",
        "/api/v2/patient/devices/recovery/complete",
        "/api/v2/patient/devices/{device_id}/rotation/challenge",
        "/api/v2/patient/devices/{device_id}/rotate",
        "/api/v2/patient/devices/{device_id}/revoke",
    ):
        assert route in contract

    assert "public_key_fingerprint" in contract
    assert "JavaScript-readable raw P-256 scalar" in contract


def test_fhir_export_is_documented_without_external_certification_claim() -> None:
    contract = _read("docs/API-CONTRACTS.md")

    assert "/api/v2/fhir/export/{patient_id}" in contract
    assert "external FHIR certification" in contract


def test_pilot_operations_migration_head_matches_runtime_migration_tool() -> None:
    runbook = _read("docs/pilot-security-operations.md")
    migration_script = _read("scripts/run_pilot_migrations.py")

    match = re.search(r'^EXPECTED_HEAD = "([^"]+)"$', migration_script, re.MULTILINE)
    assert match is not None
    expected_head = match.group(1)

    assert expected_head == "20260909_device_trust_lifecycle"
    assert f"`{expected_head}`" in runbook
    assert "`20260906_verification_scheduler`" not in runbook


def test_deviation_report_does_not_reassert_resolved_alpha_security_gaps() -> None:
    report = _read("docs/API-CONTRACT-DEVIATIONS.md")

    assert "not a current defect list" in report
    for resolved_boundary in (
        "HIGH/CRITICAL `auto_approved` enforcement missing",
        "Low-confidence fields were not forced to review",
        "No audit chain verifier existed",
        "Break-glass accepted arbitrary reason strings",
        "Pipeline trusted caller-supplied patient IDs",
    ):
        assert resolved_boundary in report

    assert report.count("**RESOLVED**") >= 5


def test_current_state_preserves_external_and_physical_blockers() -> None:
    current = _read("docs/CURRENT-STATE.md")

    assert "BLOCKED BY PHYSICAL PLATFORM / NOT_RUN" in current
    assert "official live ABDM/NHA HPR/HFR" in current
    assert "benchmark_valid" in current
    assert "did not pass extraction accuracy qualification" in current
