from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from app.core.production_runtime import validate_production_configuration
from tests.test_pilot_deployment_hardening import valid_pilot_environment
from tests.test_slice_8a_production_runtime import valid_production_environment

ROOT = Path(__file__).resolve().parents[1]


def test_pilot_preflight_direct_entrypoint_imports_application_contract() -> None:
    environment = dict(os.environ)
    environment.update(valid_pilot_environment())
    for name in (
        "ENV",
        "PYTHONPATH",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "DOCUMENT_AI_API_URL",
        "DOCUMENT_AI_API_KEY",
    ):
        environment.pop(name, None)

    result = subprocess.run(
        [sys.executable, "scripts/check_pilot_environment.py"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: static pilot environment configuration is safe" in result.stdout
    assert "ModuleNotFoundError" not in result.stderr


def test_production_configuration_rejects_placeholder_secret() -> None:
    environment = valid_production_environment()
    environment["PATIENT_JWT_SECRET"] = "<GENERATE_IN_SECRET_MANAGER>"
    errors = validate_production_configuration(environment)
    assert "PATIENT_JWT_SECRET: placeholder value forbidden" in errors


def test_production_configuration_rejects_cross_purpose_secret_reuse() -> None:
    environment = valid_production_environment()
    environment["PROVIDER_CONTACT_ASSURANCE_HMAC_SECRET"] = environment[
        "PROVIDER_REGISTRATION_IDEMPOTENCY_HMAC_SECRET"
    ]
    errors = validate_production_configuration(environment)
    assert (
        "PROVIDER_REGISTRATION_IDEMPOTENCY_HMAC_SECRET: secret value must be independently generated"
        in errors
    )
    assert (
        "PROVIDER_CONTACT_ASSURANCE_HMAC_SECRET: secret value must be independently generated"
        in errors
    )


def test_production_configuration_requires_async_postgres_driver() -> None:
    environment = valid_production_environment()
    environment["DATABASE_URL"] = "postgresql://user:pass@db.example.test:5432/nexa"
    errors = validate_production_configuration(environment)
    assert "DATABASE_URL: postgresql+asyncpg URL required" in errors


def test_ecs_task_supplies_complete_runtime_secret_set() -> None:
    task = json.loads(
        (ROOT / "deploy" / "ecs" / "nexa-care-pilot-task-definition.template.json")
        .read_text(encoding="utf-8")
    )
    secret_names = {
        item["name"] for item in task["containerDefinitions"][0]["secrets"]
    }
    assert {
        "PROVIDER_REGISTRATION_IDEMPOTENCY_HMAC_SECRET",
        "PROVIDER_CONTACT_ASSURANCE_HMAC_SECRET",
        "OPERATIONS_AUTH_TOKEN",
        "DOCUMENT_STORAGE_ENCRYPTION_KEY",
        "DATABASE_URL",
        "UPSTASH_REDIS_URL",
    } <= secret_names


def test_runtime_contract_pins_preflight_and_protected_operations_surfaces() -> None:
    contract = json.loads(
        (ROOT / "deploy" / "ecs" / "pilot-runtime-contract.template.json").read_text(
            encoding="utf-8"
        )
    )
    runtime_keys = set(contract["secretBundles"]["runtime"]["requiredJsonKeys"])
    assert {
        "PROVIDER_REGISTRATION_IDEMPOTENCY_HMAC_SECRET",
        "PROVIDER_CONTACT_ASSURANCE_HMAC_SECRET",
        "OPERATIONS_AUTH_TOKEN",
    } <= runtime_keys
    assert contract["startupPreflight"] == {
        "runsBeforeBackgroundWorkers": True,
        "requiresExactAlembicHead": True,
        "requiresPostgres": True,
        "requiresTlsRedis": True,
        "requiresEnabledKmsKeys": True,
        "requiresS3SseKms": True,
        "requiresS3PublicAccessBlock": True,
        "requiresS3Versioning": True,
        "mutatesAwsResources": False,
        "runsDatabaseMigrations": False,
    }
    operations = contract["operationsSurface"]
    assert operations["publicLivenessPath"] == "/healthz"
    assert operations["publicReadinessPath"] == "/health"
    assert operations["protectedDetailedReadinessPath"] == "/ops/health"
    assert operations["protectedMetricsPath"] == "/metrics"
    assert operations["authHeader"] == "X-Nexa-Operations-Token"


def test_runbook_preserves_forward_only_rollback_and_pending_retention_boundary() -> None:
    operations = (ROOT / "docs" / "pilot-security-operations.md").read_text(
        encoding="utf-8"
    )
    deployment = (
        ROOT / "docs" / "runbooks" / "MILESTONE_6_FARGATE_DEPLOYMENT.md"
    ).read_text(encoding="utf-8")

    assert "Do **not** automatically downgrade PostgreSQL" in operations
    assert "schema it is explicitly compatible" in operations
    assert "/ops/health" in operations
    assert "OPERATIONS_AUTH_TOKEN" in operations
    assert "Do not\nconfigure or change S3 lifecycle/retention rules" in operations

    assert "Do not automatically downgrade PostgreSQL" in deployment
    assert "startup schema gate accepts the current" in deployment
    assert "/ops/health" in deployment
    assert "OPERATIONS_AUTH_TOKEN" in deployment
    assert "Do not add/change lifecycle retention" in deployment


def test_production_rate_limiting_contract_contains_no_fail_open_fallback() -> None:
    source = (ROOT / "app" / "core" / "rate_limiter.py").read_text(encoding="utf-8")
    assert "fail-open" not in source.lower()
    assert "Allowing request" not in source
    assert "RateLimitBackendUnavailable" in source
