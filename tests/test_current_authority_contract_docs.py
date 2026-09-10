"""Regression checks for the current authority and backend closure documentation."""

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
    assert "native key aliases" in contract
    assert "JavaScript-readable raw" in contract


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

    assert expected_head == "20260910_registration_recovery_review"
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


def test_current_state_preserves_live_external_and_manual_boundaries() -> None:
    current = _read("docs/CURRENT-STATE.md")

    assert "BLOCKED BY PHYSICAL PLATFORM / NOT_RUN" in current
    assert "BLOCKED_EXTERNAL_CONTRACT" in current
    assert "external_adapter_enabled=false" in current

    assert "8A" in current and "MERGED / INTERNALLY QUALIFIED" in current
    assert "BLOCKED BY MISSING PILOT AWS ACCOUNT WIRING / NOT DEPLOYED" in current
    assert "BLOCKED BY PILOT AWS OIDC / LIVE BENCHMARK NOT_RUN" in current

    assert "ABDM_FHIR_EXTERNAL_VALIDATION=PASS" in current
    assert "partner-sandbox exchange" in current
    assert "Those remain **NOT_RUN / EXTERNAL**" in current

    assert "OPERATIONAL_AUDIT_QUALIFICATION=BLOCKED_MISSING_DATABASE_WIRING" in current
    assert "BLOCKED BY AUTHORIZED DATABASE WIRING / NOT_RUN" in current
    assert "DRAFT — NOT APPROVED — NOT IN EFFECT" in current
    assert "Security and privacy/legal approval remain **PENDING**" in current
    assert "S3 lifecycle application/read-back remains **NOT_RUN**" in current

    assert "ROLLBACK_RUNTIME_QUALIFICATION=BLOCKED_MISSING_ACCOUNT_WIRING" in current
    assert "7 / 7" in current
    assert "live rollback/runtime qualification remains **BLOCKED" in current

    assert "backend repository/software closure is complete" in current
    assert "no repository-defined Slice 9" in current


def test_current_state_preserves_historical_extraction_failure_without_weakening() -> None:
    current = _read("docs/CURRENT-STATE.md")

    assert "benchmark_valid=false" in current
    assert "Synthetic Patient Iota" in current
    assert "Synthetic Patient lota" in current
    assert "no fuzzy matching or threshold weakening" in current
    assert "34400385106" in current


def test_current_state_records_external_fhir_profile_pass_without_partner_claim() -> None:
    current = _read("docs/CURRENT-STATE.md")

    assert "ndhm.in#6.5.0" in current
    assert "ABDM 6.5.0 external profile validation PASS" in current
    assert "partner/certification/production exchange NOT_RUN" in current


def test_slice_7_closure_plan_remains_historical_and_does_not_rewrite_its_boundary() -> None:
    plan = _read("docs/governance/SLICE_7_PILOT_READINESS_PLAN.md")

    assert "SOFTWARE WORK THROUGH 7E MERGED" in plan
    assert "7F EXTERNALLY BLOCKED" in plan
    assert "LIVE PILOT NOT_RUN" in plan
    assert "LIVE ACCURACY NOT QUALIFIED" in plan
    assert "EXTERNAL VALIDATION NOT_RUN" in plan
    assert "OPERATIONAL DATABASE SNAPSHOT NOT_RUN" in plan
    assert "RETENTION APPROVAL PENDING" in plan
    assert "BLOCKED BY PHYSICAL PLATFORM / NOT_RUN" in plan
    assert "no committed Slice 8 plan" in plan
    assert "does not infer or invent one" in plan


def test_current_state_names_partition_aware_audit_verifier() -> None:
    current = _read("docs/CURRENT-STATE.md")

    assert "scripts/verify_audit_partitions.py" in current
    assert "scripts/verify_audit_integrity_evidence.py" in current


def test_current_state_records_slice_8g_fail_closed_registry_activation() -> None:
    current = _read("docs/CURRENT-STATE.md")
    gate = _read("docs/governance/ABDM_HPR_HFR_MACHINE_CONTRACT_GATE.json")

    assert "provider-verification-registry/1.0" in current or "provider_verification_registry.py" in current
    assert '"status": "BLOCKED_EXTERNAL_CONTRACT"' in gate
    assert '"external_adapter_enabled": false' in gate
    assert "server_to_server_authentication_lifecycle" in gate
    assert "rate_limit_semantics" in gate
    assert "sandbox_or_qualification_target" in gate


def test_alpha_architecture_is_explicitly_historical() -> None:
    architecture = _read("docs/ARCHITECTURE.md")

    assert "Historical Alpha snapshot" in architecture
    assert "docs/CURRENT-STATE.md" in architecture
    assert "docs/API-CONTRACTS.md" in architecture
