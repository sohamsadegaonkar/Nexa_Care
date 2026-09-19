from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from scripts.validate_pilot_runtime_evidence import (
    CURRENT_MIGRATION_HEAD,
    CURRENT_REQUIRED_CHECKS,
    CURRENT_SCHEMA,
    REQUIRED_CHECKS,
    validate_manifest,
)


CURRENT_HEAD = "20260917_treatment_session_operations"


def _valid_manifest() -> dict:
    return {
        "schema": "nexa-slice-7b-pilot-runtime-evidence-v1",
        "status": "PASS",
        "repository_commit": "0" * 40,
        "backend_image_digest": "sha256:" + "a" * 64,
        "frontend_deployment_identity": "vercel:pilot-deployment-20260909",
        "migration_head": CURRENT_HEAD,
        "aws_region": "ap-south-1",
        "data_classification": "synthetic-only",
        "aws_identity": {
            "credential_source": "ecs-task-role",
            "static_credentials_present": False,
            "caller_arn": "arn:aws:sts::123456789012:assumed-role/nexa-pilot-task/example",
        },
        "encryption": {
            "backend": "kms",
            "kms_key_arn": "arn:aws:kms:ap-south-1:123456789012:key/example",
            "s3_kms_key_arn": "arn:aws:kms:ap-south-1:123456789012:key/example",
            "context_bound": True,
        },
        "storage": {
            "provider": "s3",
            "sse_algorithm": "aws:kms",
            "public_access_blocked": True,
        },
        "database": {
            "engine": "postgresql",
            "dedicated": True,
            "migration_head": CURRENT_HEAD,
        },
        "redis": {"tls": True, "dedicated": True},
        "checks": {name: "PASS" for name in REQUIRED_CHECKS},
    }


def test_valid_pass_manifest_is_accepted() -> None:
    assert validate_manifest(_valid_manifest()) == []


def test_pass_cannot_hide_not_run_fail_closed_gate() -> None:
    manifest = _valid_manifest()
    manifest["checks"]["kms_unavailability_fail_closed"] = "NOT_RUN"

    errors = validate_manifest(manifest)

    assert any("status PASS forbidden" in error for error in errors)


def test_missing_authority_gate_is_rejected() -> None:
    manifest = _valid_manifest()
    del manifest["checks"]["audit_outbox_health"]

    errors = validate_manifest(manifest)

    assert any("audit_outbox_health" in error for error in errors)


def test_static_aws_credentials_are_rejected() -> None:
    manifest = _valid_manifest()
    manifest["aws_identity"]["static_credentials_present"] = True

    errors = validate_manifest(manifest)

    assert "aws_identity.static_credentials_present: must be false" in errors


def test_non_task_role_credential_source_is_rejected() -> None:
    manifest = _valid_manifest()
    manifest["aws_identity"]["credential_source"] = "environment"

    errors = validate_manifest(manifest)

    assert "aws_identity.credential_source: ecs-task-role required" in errors


def test_mutable_backend_image_reference_is_rejected() -> None:
    manifest = _valid_manifest()
    manifest["backend_image_digest"] = "nexa-care:latest"

    errors = validate_manifest(manifest)

    assert "backend_image_digest: immutable sha256 digest required" in errors


def test_wrong_migration_head_is_rejected() -> None:
    manifest = _valid_manifest()
    manifest["migration_head"] = "old_head"
    manifest["database"]["migration_head"] = "old_head"

    errors = validate_manifest(manifest)

    assert any(CURRENT_HEAD in error for error in errors)


def test_non_tls_redis_is_rejected() -> None:
    manifest = _valid_manifest()
    manifest["redis"]["tls"] = False

    errors = validate_manifest(manifest)

    assert "redis.tls: must be true" in errors


def test_public_s3_access_is_rejected() -> None:
    manifest = _valid_manifest()
    manifest["storage"]["public_access_blocked"] = False

    errors = validate_manifest(manifest)

    assert "storage.public_access_blocked: must be true" in errors


def test_manifest_must_be_synthetic_only() -> None:
    manifest = _valid_manifest()
    manifest["data_classification"] = "real-patient-data"

    errors = validate_manifest(manifest)

    assert "data_classification: must be synthetic-only" in errors


def test_sensitive_key_names_are_rejected_even_when_nested() -> None:
    manifest = _valid_manifest()
    manifest["notes"] = {"AWS_SECRET_ACCESS_KEY": "redacted"}

    errors = validate_manifest(manifest)

    assert any("aws_secret_access_key" in error for error in errors)


def test_blocked_manifest_can_truthfully_record_not_run_checks() -> None:
    manifest = _valid_manifest()
    manifest["status"] = "BLOCKED"
    manifest["checks"]["rollback_rehearsal"] = "NOT_RUN"

    assert validate_manifest(manifest) == []


def test_failure_manifest_can_truthfully_record_failed_checks() -> None:
    manifest = deepcopy(_valid_manifest())
    manifest["status"] = "FAIL"
    manifest["checks"]["health"] = "FAIL"

    assert validate_manifest(manifest) == []


def _valid_v2_manifest() -> dict:
    manifest = _valid_manifest()
    manifest["schema"] = CURRENT_SCHEMA
    manifest["migration_head"] = CURRENT_MIGRATION_HEAD
    manifest["database"]["migration_head"] = CURRENT_MIGRATION_HEAD
    manifest["scanner_image_digest"] = "sha256:" + "b" * 64
    manifest["scanner"] = {
        "provider": "clamd",
        "topology": "same-task-clamd-sidecar",
        "task_local_transport": True,
        "public_port_exposed": False,
        "signature_max_age_hours": 48,
    }
    manifest["checks"] = {name: "PASS" for name in CURRENT_REQUIRED_CHECKS}
    return manifest


def test_current_v2_manifest_requires_complete_d6_live_evidence() -> None:
    assert validate_manifest(_valid_v2_manifest()) == []


def test_current_v2_manifest_requires_immutable_scanner_digest() -> None:
    manifest = _valid_v2_manifest()
    manifest["scanner_image_digest"] = "clamd:latest"
    errors = validate_manifest(manifest)
    assert "scanner_image_digest: immutable sha256 digest required" in errors


def test_current_v2_manifest_rejects_public_scanner_exposure() -> None:
    manifest = _valid_v2_manifest()
    manifest["scanner"]["public_port_exposed"] = True
    errors = validate_manifest(manifest)
    assert "scanner.public_port_exposed: must be false" in errors


def test_current_v2_pass_cannot_hide_unrun_eicar_or_outage_gate() -> None:
    manifest = _valid_v2_manifest()
    manifest["checks"]["eicar_blocked_before_extraction"] = "NOT_RUN"
    manifest["checks"]["scanner_outage_fail_closed"] = "NOT_RUN"
    errors = validate_manifest(manifest)
    assert any("status PASS forbidden" in error for error in errors)
    assert any("eicar_blocked_before_extraction" in error for error in errors)
    assert any("scanner_outage_fail_closed" in error for error in errors)


def test_current_v2_blocked_manifest_may_truthfully_record_not_run_scanner_checks() -> None:
    manifest = _valid_v2_manifest()
    manifest["status"] = "BLOCKED"
    for name in CURRENT_REQUIRED_CHECKS:
        manifest["checks"][name] = "NOT_RUN"
    assert validate_manifest(manifest) == []


def test_current_v2_template_starts_blocked_with_all_live_checks_not_run() -> None:
    root = Path(__file__).resolve().parents[1]
    payload = json.loads(
        (root / "deploy" / "ecs" / "pilot-runtime-evidence-v2.template.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["schema"] == CURRENT_SCHEMA
    assert payload["status"] == "BLOCKED"
    assert payload["migration_head"] == CURRENT_MIGRATION_HEAD
    assert set(payload["checks"]) == set(CURRENT_REQUIRED_CHECKS)
    assert set(payload["checks"].values()) == {"NOT_RUN"}
