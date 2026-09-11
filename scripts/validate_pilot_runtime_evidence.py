#!/usr/bin/env python3
"""Validate sanitized Slice 7B pilot-runtime qualification evidence.

This validator is intentionally cloud-provider-read-only: it consumes an evidence
manifest produced by an authorized pilot run and refuses to label that run PASS
unless every authority-relevant gate is explicitly evidenced as PASS.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

EXPECTED_SCHEMA = "nexa-slice-7b-pilot-runtime-evidence-v1"
EXPECTED_MIGRATION_HEAD = "20260910_registration_recovery_review"
EXPECTED_REGION = "ap-south-1"
SHA256_IMAGE = re.compile(r"^sha256:[0-9a-f]{64}$")
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:/@+=-]{3,256}$")

REQUIRED_CHECKS = (
    "task_role_credentials",
    "kms_envelope_encryption",
    "s3_encrypted_storage",
    "s3_public_access_block",
    "postgresql_connectivity",
    "redis_tls_connectivity",
    "healthz",
    "health",
    "audit_outbox_health",
    "trusted_hosts",
    "cors",
    "trusted_proxy_boundary",
    "rollback_rehearsal",
    "session_consent_invalidation",
    "redis_unavailability_fail_closed",
    "kms_unavailability_fail_closed",
    "s3_unavailability_fail_closed",
    "database_unavailability_fail_closed",
)

FORBIDDEN_KEYS = {
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
    "password",
    "secret",
    "token",
    "private_key",
    "patient_name",
    "patient_id",
    "abha",
    "phone",
    "email",
}


def _walk_keys(value: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.append(str(key).lower())
            keys.extend(_walk_keys(nested))
    elif isinstance(value, list):
        for item in value:
            keys.extend(_walk_keys(item))
    return keys


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if manifest.get("schema") != EXPECTED_SCHEMA:
        errors.append("schema: unsupported or missing")

    status = manifest.get("status")
    if status not in {"PASS", "FAIL", "BLOCKED"}:
        errors.append("status: must be PASS, FAIL, or BLOCKED")

    backend_digest = manifest.get("backend_image_digest")
    if not isinstance(backend_digest, str) or not SHA256_IMAGE.fullmatch(backend_digest):
        errors.append("backend_image_digest: immutable sha256 digest required")

    frontend_identity = manifest.get("frontend_deployment_identity")
    if not isinstance(frontend_identity, str) or not SAFE_IDENTIFIER.fullmatch(frontend_identity):
        errors.append("frontend_deployment_identity: stable sanitized identifier required")

    if manifest.get("repository_commit") is None:
        errors.append("repository_commit: required")
    else:
        commit = str(manifest["repository_commit"])
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            errors.append("repository_commit: exact 40-character git SHA required")

    if manifest.get("migration_head") != EXPECTED_MIGRATION_HEAD:
        errors.append(f"migration_head: must equal {EXPECTED_MIGRATION_HEAD}")

    if manifest.get("aws_region") != EXPECTED_REGION:
        errors.append(f"aws_region: must equal {EXPECTED_REGION}")

    if manifest.get("data_classification") != "synthetic-only":
        errors.append("data_classification: must be synthetic-only")

    aws_identity = manifest.get("aws_identity")
    if not isinstance(aws_identity, dict):
        errors.append("aws_identity: required")
    else:
        if aws_identity.get("credential_source") != "ecs-task-role":
            errors.append("aws_identity.credential_source: ecs-task-role required")
        if aws_identity.get("static_credentials_present") is not False:
            errors.append("aws_identity.static_credentials_present: must be false")
        arn = aws_identity.get("caller_arn")
        if not isinstance(arn, str) or not arn.startswith("arn:aws:sts::"):
            errors.append("aws_identity.caller_arn: sanitized STS assumed-role ARN required")

    encryption = manifest.get("encryption")
    if not isinstance(encryption, dict):
        errors.append("encryption: required")
    else:
        if encryption.get("backend") != "kms":
            errors.append("encryption.backend: kms required")
        for name in ("kms_key_arn", "s3_kms_key_arn"):
            value = encryption.get(name)
            if not isinstance(value, str) or not value.startswith("arn:aws:kms:ap-south-1:"):
                errors.append(f"encryption.{name}: ap-south-1 KMS ARN required")
        if encryption.get("context_bound") is not True:
            errors.append("encryption.context_bound: must be true")

    storage = manifest.get("storage")
    if not isinstance(storage, dict):
        errors.append("storage: required")
    else:
        if storage.get("provider") != "s3":
            errors.append("storage.provider: s3 required")
        if storage.get("sse_algorithm") != "aws:kms":
            errors.append("storage.sse_algorithm: aws:kms required")
        if storage.get("public_access_blocked") is not True:
            errors.append("storage.public_access_blocked: must be true")

    database = manifest.get("database")
    if not isinstance(database, dict):
        errors.append("database: required")
    else:
        if database.get("engine") != "postgresql":
            errors.append("database.engine: postgresql required")
        if database.get("dedicated") is not True:
            errors.append("database.dedicated: must be true")
        if database.get("migration_head") != EXPECTED_MIGRATION_HEAD:
            errors.append("database.migration_head: exact current head required")

    redis = manifest.get("redis")
    if not isinstance(redis, dict):
        errors.append("redis: required")
    else:
        if redis.get("tls") is not True:
            errors.append("redis.tls: must be true")
        if redis.get("dedicated") is not True:
            errors.append("redis.dedicated: must be true")

    checks = manifest.get("checks")
    if not isinstance(checks, dict):
        errors.append("checks: required")
        checks = {}

    missing = [name for name in REQUIRED_CHECKS if name not in checks]
    if missing:
        errors.append("checks: missing " + ", ".join(missing))

    for name in REQUIRED_CHECKS:
        value = checks.get(name)
        if value not in {"PASS", "FAIL", "BLOCKED", "NOT_RUN"}:
            errors.append(f"checks.{name}: invalid status")

    if status == "PASS":
        non_pass = [name for name in REQUIRED_CHECKS if checks.get(name) != "PASS"]
        if non_pass:
            errors.append("status PASS forbidden while checks are not PASS: " + ", ".join(non_pass))

    evidence_keys = set(_walk_keys(manifest))
    forbidden_present = sorted(FORBIDDEN_KEYS & evidence_keys)
    if forbidden_present:
        errors.append("manifest contains forbidden sensitive key names: " + ", ".join(forbidden_present))

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()

    try:
        payload = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: unable to read evidence manifest ({type(exc).__name__})")
        return 2

    if not isinstance(payload, dict):
        print("FAIL: evidence manifest root must be an object")
        return 2

    errors = validate_manifest(payload)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        print("FAIL: Slice 7B pilot-runtime evidence is not qualification-ready")
        return 1

    print(f"PASS: Slice 7B evidence manifest is structurally valid with status={payload['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
