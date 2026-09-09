#!/usr/bin/env python3
"""Validate the sanitized Slice 6I physical-pilot evidence manifest.

This validator does not perform physical qualification. It prevents a blocked
manifest from being mislabeled as PASS and rejects common secret/PII fields from
repository evidence.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

REPOSITORY = "sohamsadegaonkar/Nexa_Care"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")

REQUIRED_TEST_IDS = (
    "6I-PHY-001-native-key-custody",
    "6I-PHY-002-signed-consent-v3",
    "6I-PHY-003-key-rotation",
    "6I-PHY-004-trusted-device-revocation",
    "6I-PHY-005-account-recovery",
    "6I-PHY-006-session-logout",
    "6I-PHY-007-fail-closed-network",
)

SENSITIVE_KEYS = {
    "token",
    "secret",
    "password",
    "cookie",
    "authorization",
    "phone",
    "email",
    "patient_id",
    "provider_id",
    "database_url",
    "redis_url",
    "private_key",
    "public_key_der",
    "signature_b64",
}

FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
    re.compile(r"postgres(?:ql)?://", re.IGNORECASE),
    re.compile(r"rediss?://", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE),
)

TOP_LEVEL_KEYS = {
    "schema_version",
    "repository",
    "baseline_git_sha",
    "execution_status",
    "qualification_result",
    "synthetic_data_only",
    "executed_at",
    "device",
    "native_key_attestation",
    "tests",
    "evidence_artifacts",
    "audit_event_ids",
}

DEVICE_KEYS = {"physical", "platform", "model", "os_version", "build_git_sha", "build_id"}
ATTESTATION_KEYS = {
    "platform",
    "custody",
    "non_exportable",
    "hardware_backed",
    "strongbox_backed",
    "public_key_fingerprint_sha256",
}
TEST_KEYS = {"id", "status"}
ARTIFACT_KEYS = {"kind", "sha256", "captured_at"}


class EvidenceError(ValueError):
    pass


def fail(message: str) -> None:
    raise EvidenceError(message)


def require_type(value: Any, expected: type, name: str) -> None:
    if not isinstance(value, expected):
        fail(f"{name} must be {expected.__name__}")


def require_exact_keys(value: dict[str, Any], allowed: set[str], name: str) -> None:
    extra = set(value) - allowed
    if extra:
        fail(f"{name} contains unsupported fields: {', '.join(sorted(extra))}")
    missing = allowed - set(value)
    if missing:
        fail(f"{name} is missing fields: {', '.join(sorted(missing))}")


def scan_for_sensitive_content(value: Any, path: str = "$", key_name: str | None = None) -> None:
    if key_name is not None and key_name.lower() in SENSITIVE_KEYS:
        fail(f"sensitive field is forbidden at {path}: {key_name}")

    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                fail(f"non-string key at {path}")
            scan_for_sensitive_content(child, f"{path}.{key}", key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            scan_for_sensitive_content(child, f"{path}[{index}]")
    elif isinstance(value, str):
        for pattern in FORBIDDEN_VALUE_PATTERNS:
            if pattern.search(value):
                fail(f"secret-bearing value is forbidden at {path}")


def validate_tests(tests: Any, expected_statuses: set[str]) -> None:
    require_type(tests, list, "tests")
    if len(tests) != len(REQUIRED_TEST_IDS):
        fail("tests must contain every required physical scenario exactly once")

    seen: set[str] = set()
    for index, item in enumerate(tests):
        require_type(item, dict, f"tests[{index}]")
        require_exact_keys(item, TEST_KEYS, f"tests[{index}]")
        test_id = item["id"]
        status = item["status"]
        if test_id not in REQUIRED_TEST_IDS:
            fail(f"unknown physical test id: {test_id!r}")
        if test_id in seen:
            fail(f"duplicate physical test id: {test_id}")
        seen.add(test_id)
        if status not in expected_statuses:
            fail(f"invalid status {status!r} for {test_id}")

    if seen != set(REQUIRED_TEST_IDS):
        fail("required physical test set does not match")


def validate_blocked(data: dict[str, Any]) -> None:
    if data["qualification_result"] != "NOT_RUN":
        fail("blocked physical execution must use qualification_result NOT_RUN")
    if data["executed_at"] is not None:
        fail("blocked physical execution cannot have executed_at")
    if data["device"] is not None:
        fail("blocked physical execution cannot claim a device")
    if data["native_key_attestation"] is not None:
        fail("blocked physical execution cannot claim native-key attestation")
    validate_tests(data["tests"], {"BLOCKED"})
    if data["evidence_artifacts"] != []:
        fail("blocked physical execution cannot claim evidence artifacts")
    if data["audit_event_ids"] != []:
        fail("blocked physical execution cannot claim audit event ids")


def validate_device(device: Any, baseline_sha: str) -> str:
    require_type(device, dict, "device")
    require_exact_keys(device, DEVICE_KEYS, "device")
    if device["physical"] is not True:
        fail("PASS/FAIL physical execution requires device.physical=true")
    platform = device["platform"]
    if platform not in {"ios", "android"}:
        fail("device.platform must be ios or android")
    for field in ("model", "os_version", "build_id"):
        if not isinstance(device[field], str) or not device[field].strip():
            fail(f"device.{field} must be a non-empty string")
    if not isinstance(device["build_git_sha"], str) or not SHA_RE.fullmatch(device["build_git_sha"]):
        fail("device.build_git_sha must be a full lowercase Git SHA")
    if device["build_git_sha"] != baseline_sha:
        fail("device.build_git_sha must match baseline_git_sha for this evidence record")
    return platform


def validate_attestation(attestation: Any, device_platform: str) -> None:
    require_type(attestation, dict, "native_key_attestation")
    require_exact_keys(attestation, ATTESTATION_KEYS, "native_key_attestation")
    if attestation["platform"] != device_platform:
        fail("native_key_attestation.platform must match device.platform")
    if attestation["non_exportable"] is not True:
        fail("physical key attestation must report non_exportable=true")
    if not isinstance(attestation["hardware_backed"], bool):
        fail("native_key_attestation.hardware_backed must be boolean")
    if not isinstance(attestation["strongbox_backed"], bool):
        fail("native_key_attestation.strongbox_backed must be boolean")
    fingerprint = attestation["public_key_fingerprint_sha256"]
    if not isinstance(fingerprint, str) or not HASH_RE.fullmatch(fingerprint):
        fail("public_key_fingerprint_sha256 must be lowercase 64-hex")

    custody = attestation["custody"]
    allowed = {
        "ios": {"ios-secure-enclave"},
        "android": {"android-strongbox", "android-keystore-hardware", "android-keystore"},
    }[device_platform]
    if custody not in allowed:
        fail(f"custody {custody!r} is inconsistent with platform {device_platform}")

    hardware = attestation["hardware_backed"]
    strongbox = attestation["strongbox_backed"]
    if custody == "ios-secure-enclave":
        if hardware is not True or strongbox is not False:
            fail("ios-secure-enclave requires hardware_backed=true and strongbox_backed=false")
    elif custody == "android-strongbox":
        if hardware is not True or strongbox is not True:
            fail("android-strongbox requires hardware_backed=true and strongbox_backed=true")
    elif custody == "android-keystore-hardware":
        if hardware is not True or strongbox is not False:
            fail("android-keystore-hardware requires hardware_backed=true and strongbox_backed=false")
    elif custody == "android-keystore":
        if hardware is not False or strongbox is not False:
            fail("android-keystore must not be relabeled as hardware/StrongBox evidence")


def validate_artifacts(artifacts: Any) -> None:
    require_type(artifacts, list, "evidence_artifacts")
    if not artifacts:
        fail("executed physical evidence requires at least one sanitized evidence artifact")
    for index, item in enumerate(artifacts):
        require_type(item, dict, f"evidence_artifacts[{index}]")
        require_exact_keys(item, ARTIFACT_KEYS, f"evidence_artifacts[{index}]")
        if item["kind"] not in {"screenshot", "video", "log", "json"}:
            fail(f"unsupported evidence artifact kind at index {index}")
        if not isinstance(item["sha256"], str) or not HASH_RE.fullmatch(item["sha256"]):
            fail(f"evidence_artifacts[{index}].sha256 must be lowercase 64-hex")
        if not isinstance(item["captured_at"], str) or not item["captured_at"].strip():
            fail(f"evidence_artifacts[{index}].captured_at must be present")


def validate_executed(data: dict[str, Any]) -> None:
    result = data["qualification_result"]
    if result not in {"PASS", "FAIL"}:
        fail("executed physical qualification_result must be PASS or FAIL")
    if not isinstance(data["executed_at"], str) or not data["executed_at"].strip():
        fail("executed physical evidence requires executed_at")

    device_platform = validate_device(data["device"], data["baseline_git_sha"])
    validate_attestation(data["native_key_attestation"], device_platform)
    validate_tests(data["tests"], {"PASS", "FAIL"})
    validate_artifacts(data["evidence_artifacts"])

    audit_ids = data["audit_event_ids"]
    require_type(audit_ids, list, "audit_event_ids")
    if not audit_ids or any(not isinstance(item, str) or not item.strip() for item in audit_ids):
        fail("executed physical evidence requires at least one non-empty audit event id")

    if result == "PASS":
        failed = [item["id"] for item in data["tests"] if item["status"] != "PASS"]
        if failed:
            fail("PASS cannot be claimed unless every required physical test is PASS")
    elif all(item["status"] == "PASS" for item in data["tests"]):
        fail("qualification_result FAIL requires at least one failed physical test")


def validate(data: Any) -> None:
    require_type(data, dict, "manifest")
    require_exact_keys(data, TOP_LEVEL_KEYS, "manifest")
    scan_for_sensitive_content(data)

    if data["schema_version"] != 1:
        fail("unsupported schema_version")
    if data["repository"] != REPOSITORY:
        fail(f"repository must be {REPOSITORY}")
    baseline_sha = data["baseline_git_sha"]
    if not isinstance(baseline_sha, str) or not SHA_RE.fullmatch(baseline_sha):
        fail("baseline_git_sha must be a full lowercase Git SHA")
    if data["synthetic_data_only"] is not True:
        fail("physical qualification evidence must use synthetic_data_only=true")

    status = data["execution_status"]
    if status == "BLOCKED_BY_PHYSICAL_PLATFORM":
        validate_blocked(data)
    elif status == "EXECUTED":
        validate_executed(data)
    else:
        fail("execution_status must be BLOCKED_BY_PHYSICAL_PLATFORM or EXECUTED")


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) == 2 else Path("docs/qualification/SLICE_6I_PHYSICAL_EVIDENCE.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        validate(data)
    except (OSError, json.JSONDecodeError, EvidenceError) as exc:
        print(f"SLICE 6I EVIDENCE INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"SLICE 6I EVIDENCE VALID: {data['execution_status']} / {data['qualification_result']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
