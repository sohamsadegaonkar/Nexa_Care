from __future__ import annotations

import hashlib
import json
import ssl
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

import scripts.check_medication_catalog_release_readiness as readiness_cli
from app.services.medication_catalog_release_readiness import (
    MedicationCatalogReleaseReadinessError,
)
from scripts.check_medication_catalog_release_readiness import run

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RDS_CA_BUNDLE = PROJECT_ROOT / "deploy" / "ssl" / "aws-rds-ca-bundle.pem"


def _metadata(digest: str) -> dict[str, object]:
    return {
        "schema": "nexa-medication-catalog-source-metadata/v1",
        "terminology_authority": "NRCES",
        "package_name": "Synthetic protected terminology package",
        "package_version": "synthetic-cdci-2026-08-31",
        "package_release_date": "2026-08-31",
        "package_sha256": digest,
        "official_source_reference": "https://official.example.test/release",
        "licence_governance_reference": "LICENCE-EVIDENCE-TEST-ONLY",
        "checked_at": datetime(2026, 9, 20, 12, tzinfo=timezone.utc).isoformat(),
    }


def _write_source_fixture(tmp_path: Path, package_bytes: bytes = b"synthetic package bytes"):
    package = tmp_path / "synthetic-package.bin"
    package.write_bytes(package_bytes)
    digest = hashlib.sha256(package_bytes).hexdigest()
    metadata = tmp_path / "source.json"
    metadata.write_text(json.dumps(_metadata(digest)), encoding="utf-8")
    return package, metadata


@pytest.mark.asyncio
async def test_source_only_cli_verifies_local_package_without_mutation(
    tmp_path,
    capsys,
):
    package, metadata = _write_source_fixture(tmp_path)

    code = await run(
        [
            "--source-only",
            "--source-metadata-file",
            str(metadata),
            "--terminology-package-file",
            str(package),
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "READY"
    assert payload["source"]["package_digest_verified"] is True
    assert payload["catalog_state_mutated"] is False
    assert payload["aws_mutated"] is False


@pytest.mark.asyncio
async def test_source_only_does_not_require_database_tls_configuration(
    tmp_path,
    capsys,
    monkeypatch,
):
    package, metadata = _write_source_fixture(tmp_path)
    monkeypatch.setenv("ENVIRONMENT", "pilot")
    monkeypatch.delenv("DATABASE_SSL_CA_PATH", raising=False)
    monkeypatch.delenv(readiness_cli.ENV_DB_URL, raising=False)

    code = await run(
        [
            "--source-only",
            "--source-metadata-file",
            str(metadata),
            "--terminology-package-file",
            str(package),
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "READY"
    assert payload["catalog_state_mutated"] is False
    assert payload["aws_mutated"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ["pilot", "staging", "preview", "production"])
async def test_production_like_database_mode_requires_explicit_ca(
    tmp_path,
    capsys,
    monkeypatch,
    environment: str,
):
    _, metadata = _write_source_fixture(tmp_path)
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.delenv("DATABASE_SSL_CA_PATH", raising=False)
    monkeypatch.setenv(
        readiness_cli.ENV_DB_URL,
        "postgresql+asyncpg://user:pass@db.example.test:5432/nexa",
    )

    code = await run(
        [
            "--source-metadata-file",
            str(metadata),
            "--release-id",
            str(uuid4()),
            "--expected-database-name",
            "nexa",
        ]
    )

    assert code == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload == {
        "aws_mutated": False,
        "catalog_state_mutated": False,
        "error": "DATABASE_TLS_CA_REQUIRED",
        "status": "BLOCKED",
    }


@pytest.mark.asyncio
async def test_production_like_database_mode_rejects_invalid_ca(
    tmp_path,
    capsys,
    monkeypatch,
):
    _, metadata = _write_source_fixture(tmp_path)
    monkeypatch.setenv("ENVIRONMENT", "pilot")
    monkeypatch.setenv(
        "DATABASE_SSL_CA_PATH",
        str(tmp_path / "missing-ca.pem"),
    )
    monkeypatch.setenv(
        readiness_cli.ENV_DB_URL,
        "postgresql+asyncpg://user:pass@db.example.test:5432/nexa",
    )

    code = await run(
        [
            "--source-metadata-file",
            str(metadata),
            "--release-id",
            str(uuid4()),
            "--expected-database-name",
            "nexa",
        ]
    )

    assert code == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"] == "DATABASE_TLS_CONFIGURATION_INVALID"
    assert payload["catalog_state_mutated"] is False
    assert payload["aws_mutated"] is False


def test_valid_ca_uses_shared_verified_connect_args(monkeypatch):
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_create_async_engine(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return sentinel

    monkeypatch.setenv("ENVIRONMENT", "pilot")
    monkeypatch.setenv("DATABASE_SSL_CA_PATH", str(RDS_CA_BUNDLE))
    monkeypatch.setattr(readiness_cli, "create_async_engine", fake_create_async_engine)

    result = readiness_cli._create_readiness_engine(
        "postgresql+asyncpg://user:pass@db.example.test:5432/nexa"
    )

    assert result is sentinel
    assert captured["pool_pre_ping"] is True
    connect_args = captured["connect_args"]
    assert isinstance(connect_args, dict)
    assert connect_args["statement_cache_size"] == 0
    assert connect_args["prepared_statement_cache_size"] == 0
    assert callable(connect_args["prepared_statement_name_func"])

    context = connect_args["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


@pytest.mark.parametrize(
    "query",
    [
        "ssl=require",
        "sslmode=require",
        "sslmode=verify-full",
        "sslrootcert=/tmp/ca.pem",
        "sslcert=/tmp/client.pem",
        "sslkey=/tmp/client.key",
    ],
)
def test_readiness_database_url_cannot_override_tls_policy(
    monkeypatch,
    query: str,
):
    monkeypatch.setenv("ENVIRONMENT", "pilot")
    monkeypatch.setenv("DATABASE_SSL_CA_PATH", str(RDS_CA_BUNDLE))

    with pytest.raises(MedicationCatalogReleaseReadinessError) as exc:
        readiness_cli._create_readiness_engine(
            f"postgresql+asyncpg://user:pass@db.example.test:5432/nexa?{query}"
        )

    assert exc.value.code == "DATABASE_TLS_CONFIGURATION_INVALID"


@pytest.mark.asyncio
async def test_source_only_output_does_not_expose_package_contents(
    tmp_path,
    capsys,
):
    secret_package_marker = b"LICENSED_TERMINOLOGY_CONTENT_MUST_NOT_APPEAR"
    package, metadata = _write_source_fixture(tmp_path, secret_package_marker)

    code = await run(
        [
            "--source-only",
            "--source-metadata-file",
            str(metadata),
            "--terminology-package-file",
            str(package),
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert secret_package_marker.decode("ascii") not in output
    payload = json.loads(output)
    assert payload["source"]["package_digest_verified"] is True
    assert payload["catalog_state_mutated"] is False
    assert payload["aws_mutated"] is False


@pytest.mark.asyncio
async def test_source_only_cli_is_blocked_without_actual_package(tmp_path, capsys):
    metadata = tmp_path / "source.json"
    metadata.write_text(json.dumps(_metadata("a" * 64)), encoding="utf-8")

    code = await run(
        [
            "--source-only",
            "--source-metadata-file",
            str(metadata),
        ]
    )

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "EXTERNALLY_BLOCKED"
    assert payload["source"]["package_digest_verified"] is False
    assert payload["catalog_state_mutated"] is False
    assert payload["aws_mutated"] is False


@pytest.mark.asyncio
async def test_source_only_cli_rejects_digest_mismatch(tmp_path, capsys):
    package = tmp_path / "synthetic-package.bin"
    package.write_bytes(b"synthetic package bytes")
    metadata = tmp_path / "source.json"
    metadata.write_text(json.dumps(_metadata("b" * 64)), encoding="utf-8")

    code = await run(
        [
            "--source-only",
            "--source-metadata-file",
            str(metadata),
            "--terminology-package-file",
            str(package),
        ]
    )

    assert code == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["status"] == "BLOCKED"
    assert payload["error"] == "SOURCE_DIGEST_MISMATCH"
    assert payload["catalog_state_mutated"] is False
    assert payload["aws_mutated"] is False
