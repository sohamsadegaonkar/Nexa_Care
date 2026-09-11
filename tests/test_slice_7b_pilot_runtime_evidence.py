from __future__ import annotations

from copy import deepcopy

from scripts.validate_pilot_runtime_evidence import REQUIRED_CHECKS, validate_manifest


def _valid_manifest() -> dict:
    return {
        "schema": "nexa-slice-7b-pilot-runtime-evidence-v1",
        "status": "PASS",
        "repository_commit": "0" * 40,
        "backend_image_digest": "sha256:" + "a" * 64,
        "frontend_deployment_identity": "vercel:pilot-deployment-20260909",
        "migration_head": "20260910_registration_recovery_review",
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
            "migration_head": "20260910_registration_recovery_review",
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

    assert any("20260910_registration_recovery_review" in error for error in errors)


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
