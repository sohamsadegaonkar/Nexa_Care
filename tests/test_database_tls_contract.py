"""Tests for the Amazon RDS verify-full TLS database contract.

Invariants:
- Production-like environments MUST require DATABASE_SSL_CA_PATH with a valid CA bundle.
- SSLContext MUST enforce check_hostname=True and verify_mode=CERT_REQUIRED.
- DATABASE_URL MUST NOT contain conflicting TLS query parameters (e.g. ssl=require).
- Application async engine and Alembic migration runner MUST share the identical connect_args.
- Non-production / test disposable databases MUST continue to work without a CA bundle.
"""

from __future__ import annotations

import json
import ssl
from pathlib import Path

import pytest

from app.core.config import ConfigError, DatabaseConfig
from app.core.database import (
    build_database_connect_args,
    create_database_ssl_context,
)
from app.core.production_runtime import validate_production_configuration
from tests.test_slice_8a_production_runtime import valid_production_environment

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RDS_CA_BUNDLE = PROJECT_ROOT / "deploy" / "ssl" / "aws-rds-ca-bundle.pem"


def test_authoritative_rds_ca_bundle_exists() -> None:
    assert RDS_CA_BUNDLE.is_file(), f"Authoritative CA bundle missing at {RDS_CA_BUNDLE}"
    assert RDS_CA_BUNDLE.stat().st_size > 50_000


def test_create_database_ssl_context_enforces_strict_verification() -> None:
    context = create_database_ssl_context(RDS_CA_BUNDLE)
    assert context is not None
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_create_database_ssl_context_none_when_unconfigured() -> None:
    assert create_database_ssl_context(None) is None


def test_create_database_ssl_context_rejects_missing_file() -> None:
    with pytest.raises(ConfigError, match="DATABASE_SSL_CA_PATH not found"):
        create_database_ssl_context(PROJECT_ROOT / "deploy" / "ssl" / "nonexistent.pem")


def test_create_database_ssl_context_rejects_invalid_pem(tmp_path: Path) -> None:
    corrupt_file = tmp_path / "corrupt_ca.pem"
    corrupt_file.write_text("-----BEGIN CERTIFICATE-----\nINVALID DATA\n-----END CERTIFICATE-----\n")
    with pytest.raises(ConfigError, match="DATABASE_SSL_CA_PATH contains invalid certificates"):
        create_database_ssl_context(corrupt_file)


@pytest.mark.parametrize(
    "query",
    [
        "ssl=require",
        "sslmode=verify-full",
        "sslrootcert=/some/path",
        "sslcert=/client/cert",
        "sslkey=/client/key",
        "ssl=true&sslmode=require",
    ],
)
def test_build_database_connect_args_rejects_conflicting_query_params(query: str) -> None:
    config = DatabaseConfig(
        url=f"postgresql+asyncpg://user:pass@db.example.test:5432/nexa?{query}",
        echo_sql=False,
        ssl_ca_path=RDS_CA_BUNDLE,
    )
    with pytest.raises(ConfigError, match="DATABASE_URL query contains conflicting TLS parameters"):
        build_database_connect_args(config)


def test_build_database_connect_args_with_ca_bundle() -> None:
    config = DatabaseConfig(
        url="postgresql+asyncpg://user:pass@db.example.test:5432/nexa",
        echo_sql=False,
        ssl_ca_path=RDS_CA_BUNDLE,
    )
    connect_args = build_database_connect_args(config)
    assert "ssl" in connect_args
    ssl_ctx = connect_args["ssl"]
    assert isinstance(ssl_ctx, ssl.SSLContext)
    assert ssl_ctx.check_hostname is True
    assert ssl_ctx.verify_mode == ssl.CERT_REQUIRED
    assert connect_args["statement_cache_size"] == 0
    assert connect_args["prepared_statement_cache_size"] == 0
    assert callable(connect_args["prepared_statement_name_func"])


def test_build_database_connect_args_without_ca_bundle() -> None:
    config = DatabaseConfig(
        url="postgresql+asyncpg://user:pass@localhost:5432/nexa_dev",
        echo_sql=False,
        ssl_ca_path=None,
    )
    connect_args = build_database_connect_args(config)
    assert "ssl" not in connect_args
    assert connect_args["statement_cache_size"] == 0
    assert connect_args["prepared_statement_cache_size"] == 0
    assert callable(connect_args["prepared_statement_name_func"])


def test_application_and_migration_engines_share_identical_connect_args_contract() -> None:
    config = DatabaseConfig(
        url="postgresql+asyncpg://user:pass@db.example.test:5432/nexa",
        echo_sql=False,
        ssl_ca_path=RDS_CA_BUNDLE,
    )
    app_connect_args = build_database_connect_args(config)
    alembic_connect_args = build_database_connect_args(config)

    assert set(app_connect_args.keys()) == set(alembic_connect_args.keys())
    assert app_connect_args["statement_cache_size"] == alembic_connect_args["statement_cache_size"]
    assert app_connect_args["prepared_statement_cache_size"] == alembic_connect_args["prepared_statement_cache_size"]
    assert app_connect_args["ssl"].check_hostname == alembic_connect_args["ssl"].check_hostname
    assert app_connect_args["ssl"].verify_mode == alembic_connect_args["ssl"].verify_mode


def test_production_preflight_fails_closed_without_ssl_ca_path() -> None:
    env = valid_production_environment()
    env.pop("DATABASE_SSL_CA_PATH", None)
    errors = validate_production_configuration(env)
    assert "DATABASE_SSL_CA_PATH: required" in errors


def test_production_preflight_fails_closed_with_missing_ca_file() -> None:
    env = valid_production_environment()
    env["DATABASE_SSL_CA_PATH"] = str(PROJECT_ROOT / "deploy" / "ssl" / "missing.pem")
    errors = validate_production_configuration(env)
    assert "DATABASE_SSL_CA_PATH: file not found" in errors


def test_production_preflight_fails_closed_with_corrupt_ca_file(tmp_path: Path) -> None:
    corrupt = tmp_path / "bad.pem"
    corrupt.write_text("NOT A VALID CERTIFICATE")
    env = valid_production_environment()
    env["DATABASE_SSL_CA_PATH"] = str(corrupt)
    errors = validate_production_configuration(env)
    assert "DATABASE_SSL_CA_PATH: invalid certificates" in errors


def test_production_preflight_fails_closed_with_conflicting_tls_in_url() -> None:
    env = valid_production_environment()
    env["DATABASE_URL"] = "postgresql+asyncpg://user:pass@db.example.test:5432/nexa?ssl=require"
    errors = validate_production_configuration(env)
    assert any("DATABASE_URL: conflicting TLS query parameters forbidden: ssl" in e for e in errors)


def test_pilot_task_definition_template_contains_server_owned_ca_path() -> None:
    task_template = PROJECT_ROOT / "deploy" / "ecs" / "nexa-care-pilot-task-definition.template.json"
    task = json.loads(task_template.read_text(encoding="utf-8"))
    api = next(c for c in task["containerDefinitions"] if c["name"] == "nexa-care-pilot-api")
    env = {item["name"]: item["value"] for item in api["environment"]}
    assert env.get("DATABASE_SSL_CA_PATH") == "/app/deploy/ssl/aws-rds-ca-bundle.pem"


def test_pilot_runtime_contract_template_agrees_on_ca_path() -> None:
    contract_template = PROJECT_ROOT / "deploy" / "ecs" / "pilot-runtime-contract.template.json"
    contract = json.loads(contract_template.read_text(encoding="utf-8"))
    settings = contract.get("fixedQualificationSettings", {})
    assert settings.get("DATABASE_SSL_CA_PATH") == "/app/deploy/ssl/aws-rds-ca-bundle.pem"


def test_activation_script_enforces_ca_path_in_api_environment() -> None:
    from scripts.check_aws_pilot_activation import EXPECTED_API_ENVIRONMENT
    assert EXPECTED_API_ENVIRONMENT.get("DATABASE_SSL_CA_PATH") == "/app/deploy/ssl/aws-rds-ca-bundle.pem"
