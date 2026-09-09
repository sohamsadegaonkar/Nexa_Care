from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_slice6i_physical_evidence.py"
MANIFEST = ROOT / "docs" / "qualification" / "SLICE_6I_PHYSICAL_EVIDENCE.json"

REQUIRED_TEST_IDS = [
    "6I-PHY-001-native-key-custody",
    "6I-PHY-002-signed-consent-v3",
    "6I-PHY-003-key-rotation",
    "6I-PHY-004-trusted-device-revocation",
    "6I-PHY-005-account-recovery",
    "6I-PHY-006-session-logout",
    "6I-PHY-007-fail-closed-network",
]


def _load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _run_manifest(tmp_path: Path, data: dict) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _synthetic_executed_pass() -> dict:
    data = _load_manifest()
    data.update(
        {
            "execution_status": "EXECUTED",
            "qualification_result": "PASS",
            "executed_at": "2030-01-01T00:00:00Z",
            "device": {
                "physical": True,
                "platform": "ios",
                "model": "synthetic-test-device",
                "os_version": "synthetic-os",
                "build_git_sha": data["baseline_git_sha"],
                "build_id": "synthetic-validator-fixture",
            },
            "native_key_attestation": {
                "platform": "ios",
                "custody": "ios-secure-enclave",
                "non_exportable": True,
                "hardware_backed": True,
                "strongbox_backed": False,
                "public_key_fingerprint_sha256": "a" * 64,
            },
            "tests": [{"id": test_id, "status": "PASS"} for test_id in REQUIRED_TEST_IDS],
            "evidence_artifacts": [
                {"kind": "json", "sha256": "b" * 64, "captured_at": "2030-01-01T00:00:01Z"}
            ],
            "audit_event_ids": ["synthetic-audit-event-fixture"],
        }
    )
    return data


def test_committed_blocked_manifest_is_valid() -> None:
    result = subprocess.run(
        [sys.executable, str(VALIDATOR), str(MANIFEST)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "BLOCKED_BY_PHYSICAL_PLATFORM / NOT_RUN" in result.stdout


def test_pass_without_physical_device_is_rejected(tmp_path: Path) -> None:
    data = _synthetic_executed_pass()
    data["device"]["physical"] = False
    result = _run_manifest(tmp_path, data)
    assert result.returncode == 1
    assert "device.physical=true" in result.stderr


def test_sensitive_field_injection_is_rejected(tmp_path: Path) -> None:
    data = copy.deepcopy(_load_manifest())
    data["tests"][0]["token"] = "synthetic-but-forbidden"
    result = _run_manifest(tmp_path, data)
    assert result.returncode == 1
    assert "sensitive field is forbidden" in result.stderr


def test_synthetic_complete_pass_shape_is_accepted_by_contract(tmp_path: Path) -> None:
    # This is a schema/guardrail fixture only. It is not repository physical evidence.
    data = _synthetic_executed_pass()
    result = _run_manifest(tmp_path, data)
    assert result.returncode == 0, result.stderr
    assert "EXECUTED / PASS" in result.stdout


def test_android_keystore_cannot_be_relabeled_hardware_backed(tmp_path: Path) -> None:
    data = _synthetic_executed_pass()
    data["device"]["platform"] = "android"
    data["native_key_attestation"].update(
        {
            "platform": "android",
            "custody": "android-keystore",
            "hardware_backed": True,
            "strongbox_backed": False,
        }
    )
    result = _run_manifest(tmp_path, data)
    assert result.returncode == 1
    assert "must not be relabeled" in result.stderr


def test_strongbox_requires_runtime_strongbox_metadata(tmp_path: Path) -> None:
    data = _synthetic_executed_pass()
    data["device"]["platform"] = "android"
    data["native_key_attestation"].update(
        {
            "platform": "android",
            "custody": "android-strongbox",
            "hardware_backed": True,
            "strongbox_backed": False,
        }
    )
    result = _run_manifest(tmp_path, data)
    assert result.returncode == 1
    assert "android-strongbox requires" in result.stderr
