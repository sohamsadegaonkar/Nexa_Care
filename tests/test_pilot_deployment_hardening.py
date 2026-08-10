from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import check_pilot_environment, run_pilot_migrations

ROOT = Path(__file__).resolve().parents[1]


def valid_pilot_environment() -> dict[str, str]:
    return {
        "ENVIRONMENT": "pilot",
        "DOCUMENT_EXTRACTION_PROVIDER": "aws_textract",
        "DOCUMENT_AI_AWS_REGION": "ap-south-1",
        "DOCUMENT_AI_TIMEOUT_SECONDS": "30",
        "DOCUMENT_AI_MAX_ATTEMPTS": "3",
        "DOCUMENT_STORAGE_PROVIDER": "s3",
        "DOCUMENT_STORAGE_S3_BUCKET": "synthetic-pilot-bucket",
        "DOCUMENT_STORAGE_S3_REGION": "ap-south-1",
        "DOCUMENT_STORAGE_S3_KMS_KEY_ID": "alias/synthetic-storage",
        "DOCUMENT_STORAGE_ENCRYPTION_KEY": "synthetic-storage-key",
        "ENCRYPTION_BACKEND": "kms",
        "AWS_REGION": "ap-south-1",
        "KMS_KEY_ID": "alias/synthetic-envelope",
        "AWS_PATIENT_SPECIFIC_KMS_KEYS": "false",
        "DATABASE_URL": (
            "postgresql+asyncpg://synthetic:synthetic@db.example.test:5432/nexa"
        ),
        "UPSTASH_REDIS_URL": "rediss://synthetic@redis.example.test:6379/0",
        "CORS_ALLOWED_ORIGINS": "https://doctor.example.test",
        "TRUSTED_HOSTS": "api.example.test",
        "TRUSTED_PROXY_NETWORKS": "10.0.0.0/24",
        "FORWARDED_ALLOW_IPS": "10.0.0.0/24",
        "SUPABASE_URL": "https://synthetic.supabase.example.test",
        "SUPABASE_KEY": "synthetic-supabase-key",
        "HANDSHAKE_PEPPER_SECRET": "synthetic-handshake-secret",
        "MFA_ENCRYPTION_KEY": "synthetic-mfa-key",
        "PII_ENCRYPTION_KEY": "synthetic-pii-key",
        "PATIENT_JWT_SECRET": "synthetic-jwt-secret",
        "OTP_RATE_LIMIT_HMAC_SECRET": "synthetic-otp-secret",
        "PUSH_STATUS_TRANSPORT": "poll",
    }


def test_dockerfile_starts_only_safe_container_entrypoint() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "alembic" not in dockerfile.lower()
    assert 'CMD ["sh", "/app/scripts/container_start.sh"]' in dockerfile
    assert "USER nexa_user" in dockerfile


def test_container_entrypoint_requires_explicit_forwarded_allow_ips() -> None:
    path = ROOT / "scripts" / "container_start.sh"
    content = path.read_text(encoding="utf-8")

    assert b"\r\n" not in path.read_bytes()
    assert "set -eu" in content
    assert "staging|preview|pilot|production" in content
    assert 'forwarded_allow_ips="${FORWARDED_ALLOW_IPS:-}"' in content
    assert "--proxy-headers" in content
    assert "--forwarded-allow-ips" in content
    assert "exec uvicorn app.main:app" in content
    assert "alembic" not in content.lower()


def test_migration_script_requires_migration_database_url() -> None:
    environment = dict(os.environ)
    environment["ENVIRONMENT"] = "pilot"
    environment.pop("MIGRATION_DATABASE_URL", None)

    result = subprocess.run(
        [sys.executable, "scripts/run_pilot_migrations.py"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode != 0
    assert "MIGRATION_DATABASE_URL is required" in result.stdout


def test_migration_script_requires_exact_single_repository_head() -> None:
    assert run_pilot_migrations.repository_heads() == ("20260810_identity_review",)


def test_migration_script_scopes_url_and_redacts_command_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sensitive_url = (
        "postgresql+asyncpg://credential_user:credential_password@db.example.test/nexa"
    )
    calls: list[list[str]] = []
    monkeypatch.setenv("ENVIRONMENT", "pilot")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", sensitive_url)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    def fake_alembic(
        arguments: list[str], environment: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        assert environment["DATABASE_URL"] == sensitive_url
        assert "MIGRATION_DATABASE_URL" not in environment
        stdout = (
            "20260810_identity_review (head)\n" if arguments == ["current"] else ""
        )
        return subprocess.CompletedProcess(arguments, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(run_pilot_migrations, "_run_alembic", fake_alembic)

    assert run_pilot_migrations.main() == 0
    output = capsys.readouterr().out
    assert calls == [["upgrade", "head"], ["current"]]
    assert "credential_user" not in output
    assert "credential_password" not in output
    assert sensitive_url not in output
    assert "DATABASE_URL" not in os.environ


@pytest.mark.parametrize(
    ("name", "value", "expected_error"),
    [
        (
            "DOCUMENT_EXTRACTION_PROVIDER",
            "remote",
            "DOCUMENT_EXTRACTION_PROVIDER",
        ),
        ("ENCRYPTION_BACKEND", "local", "ENCRYPTION_BACKEND"),
        ("UPSTASH_REDIS_URL", "redis://redis.example.test:6379", "UPSTASH_REDIS_URL"),
        (
            "CORS_ALLOWED_ORIGINS",
            "http://doctor.example.test",
            "CORS_ALLOWED_ORIGINS",
        ),
        ("TRUSTED_HOSTS", "*", "TRUSTED_HOSTS"),
        ("TRUSTED_PROXY_NETWORKS", "0.0.0.0/0", "TRUSTED_PROXY_NETWORKS"),
        ("FORWARDED_ALLOW_IPS", "*", "FORWARDED_ALLOW_IPS"),
        ("DOCUMENT_AI_AWS_REGION", "us-east-1", "DOCUMENT_AI_AWS_REGION"),
        ("DOCUMENT_STORAGE_S3_REGION", "us-east-1", "DOCUMENT_STORAGE_S3_REGION"),
        ("AWS_REGION", "us-east-1", "AWS_REGION"),
    ],
)
def test_pilot_preflight_rejects_unsafe_configuration(
    name: str, value: str, expected_error: str
) -> None:
    environment = valid_pilot_environment()
    environment[name] = value

    errors = check_pilot_environment.validate_configuration(environment)

    assert any(error.startswith(expected_error) for error in errors)


@pytest.mark.parametrize(
    "name",
    ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"],
)
def test_pilot_preflight_rejects_static_aws_credentials(name: str) -> None:
    environment = valid_pilot_environment()
    environment[name] = "synthetic-static-credential"

    errors = check_pilot_environment.validate_configuration(environment)

    assert f"{name}: static AWS credentials are forbidden" in errors


def test_valid_synthetic_pilot_configuration_passes() -> None:
    assert (
        check_pilot_environment.validate_configuration(valid_pilot_environment()) == []
    )


def test_render_manifest_is_retired_with_required_warning() -> None:
    assert not (ROOT / "render.yaml").exists()
    legacy = (ROOT / "deploy" / "legacy" / "render.remote-pilot.yaml").read_text(
        encoding="utf-8"
    )

    assert "Historical remote-provider configuration only" in legacy
    assert "not approved for Milestone 6" in legacy
    assert "must not be used for AWS" in legacy
    assert "obsolete provider and encryption" in legacy
    assert "Amazon ECS Fargate" in legacy


def test_pilot_operations_document_uses_current_contract() -> None:
    content = (ROOT / "docs" / "pilot-security-operations.md").read_text(
        encoding="utf-8"
    )

    assert "CORS_ALLOWED_ORIGINS" in content
    assert "DOCUMENT_EXTRACTION_PROVIDER=aws_textract" in content
    assert "20260810_identity_review" in content
    assert "DOCUMENT_EXTRACTION_PROVIDER=remote" not in content
    assert "DOCUMENT_AI_API_URL` or `DOCUMENT_AI_API_KEY" in content
    assert "desiredCount=1" in content


def test_governance_contract_names_current_migration_head() -> None:
    constitution = (
        ROOT / "docs" / "governance" / "NEXA_CARE_ENGINEERING_CONSTITUTION.md"
    ).read_text(encoding="utf-8")
    security = (ROOT / "docs" / "governance" / "SECURITY_NON_REGRESSION.md").read_text(
        encoding="utf-8"
    )

    assert "Current head is `20260810_identity_review`" in constitution
    assert "current head `20260810_identity_review`" in security
    assert "API containers never run migrations during startup" in security
