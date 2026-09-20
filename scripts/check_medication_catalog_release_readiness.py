#!/usr/bin/env python3
"""Read-only 10B.5l medication-catalog first-release readiness CLI.

The tool never mutates catalog state and never signs a new release.  It can
optionally describe/verify the configured production KMS signing key through
the existing signing provider.  Licensed terminology packages remain local to
the operator environment and are only streamed for SHA-256 verification.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import UUID

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import (  # noqa: E402
    ConfigError,
    DatabaseConfig,
    RuntimeEnvironment,
)
from app.core.database import build_database_connect_args  # noqa: E402
from app.models.medication_catalog import (  # noqa: E402
    MedicationCatalogEmergencyDeny,
    MedicationCatalogEntry,
    MedicationCatalogEvidence,
    MedicationCatalogRelease,
    MedicationCatalogReleaseStatus,
)
from app.services.medication_catalog_release_readiness import (  # noqa: E402
    MedicationCatalogReleaseReadinessError,
    assess_release_readiness,
    parse_source_metadata,
    verify_package_digest,
)
from app.services.medication_catalog_signing import (  # noqa: E402
    MedicationCatalogSigningError,
    build_production_medication_catalog_signer,
)

ENV_DB_URL = "NEXA_MEDICATION_CATALOG_DATABASE_URL"
_PRODUCTION_LIKE_ENVIRONMENTS = frozenset(
    environment.value
    for environment in RuntimeEnvironment
    if environment.is_production_like
)


def _derive_repository_heads() -> tuple[str, ...]:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return tuple(ScriptDirectory.from_config(config).get_heads())


def _load_source_metadata(path: Path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MedicationCatalogReleaseReadinessError(
            "SOURCE_METADATA_UNAVAILABLE"
        ) from exc
    if not isinstance(payload, dict):
        raise MedicationCatalogReleaseReadinessError("SOURCE_METADATA_INVALID")
    return parse_source_metadata(payload)


def _safe_source_summary(provenance, *, package_digest_verified: bool) -> dict:
    return {
        "schema": provenance.schema,
        "terminology_authority": provenance.terminology_authority,
        "package_name": provenance.package_name,
        "package_version": provenance.package_version,
        "package_release_date": provenance.package_release_date.isoformat(),
        "package_sha256": provenance.package_sha256,
        "checked_at": provenance.checked_at.isoformat(),
        "licence_governance_reference_present": bool(
            provenance.licence_governance_reference
        ),
        "official_source_reference_present": bool(
            provenance.official_source_reference
        ),
        "package_digest_verified": package_digest_verified,
    }


def _create_readiness_engine(db_url: str):
    """Create the read-only engine through Nexa's shared database TLS contract."""

    environment = os.getenv("ENVIRONMENT", "").strip().lower()
    ca_raw = os.getenv("DATABASE_SSL_CA_PATH", "").strip()
    if environment in _PRODUCTION_LIKE_ENVIRONMENTS and not ca_raw:
        raise MedicationCatalogReleaseReadinessError("DATABASE_TLS_CA_REQUIRED")

    config = DatabaseConfig(
        url=db_url,
        echo_sql=False,
        ssl_ca_path=Path(ca_raw) if ca_raw else None,
    )
    try:
        connect_args = build_database_connect_args(config)
    except ConfigError as exc:
        raise MedicationCatalogReleaseReadinessError(
            "DATABASE_TLS_CONFIGURATION_INVALID"
        ) from exc

    return create_async_engine(
        config.url,
        echo=config.echo_sql,
        pool_pre_ping=True,
        poolclass=NullPool,
        connect_args=connect_args,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only first medication-catalog release readiness checker. "
            "It never creates, qualifies, activates, or edits a catalog release."
        )
    )
    parser.add_argument("--source-metadata-file", required=True, type=Path)
    parser.add_argument("--terminology-package-file", type=Path)
    parser.add_argument(
        "--source-only",
        action="store_true",
        help="Validate source metadata/package digest without connecting to PostgreSQL.",
    )
    parser.add_argument("--release-id", type=str)
    parser.add_argument("--expected-database-name", type=str)
    parser.add_argument(
        "--live-kms-preflight",
        action="store_true",
        help=(
            "Use the configured production signer for read-only DescribeKey and, "
            "when a signed release exists, Verify. No Sign call is made."
        ),
    )
    return parser


async def _database_preflight(session, expected_database_name: str) -> str:
    current_db = (await session.execute(text("SELECT current_database()"))).scalar_one()
    if current_db != expected_database_name:
        raise MedicationCatalogReleaseReadinessError("DATABASE_NAME_MISMATCH")

    heads = _derive_repository_heads()
    if len(heads) != 1:
        raise MedicationCatalogReleaseReadinessError("SCHEMA_REVISION_MISMATCH")
    db_head = (
        await session.execute(text("SELECT version_num FROM alembic_version"))
    ).scalar_one_or_none()
    if db_head != heads[0]:
        raise MedicationCatalogReleaseReadinessError("SCHEMA_REVISION_MISMATCH")
    if session.in_transaction():
        await session.rollback()
    return heads[0]


async def _load_release_projection(session, release_id: UUID):
    release = await session.get(MedicationCatalogRelease, release_id)
    if release is None:
        raise MedicationCatalogReleaseReadinessError("RELEASE_NOT_FOUND")

    entries = list(
        (
            await session.execute(
                select(MedicationCatalogEntry)
                .where(MedicationCatalogEntry.release_id == release_id)
                .order_by(MedicationCatalogEntry.medication_code)
            )
        )
        .scalars()
        .all()
    )
    evidence_rows = list(
        (
            await session.execute(
                select(MedicationCatalogEvidence)
                .where(MedicationCatalogEvidence.release_id == release_id)
                .order_by(
                    MedicationCatalogEvidence.entry_id,
                    MedicationCatalogEvidence.finding_dimension,
                    MedicationCatalogEvidence.evidence_sha256,
                )
            )
        )
        .scalars()
        .all()
    )
    evidence_by_entry = {entry.id: [] for entry in entries}
    for row in evidence_rows:
        evidence_by_entry.setdefault(row.entry_id, []).append(row)

    emergency_rows = list(
        (
            await session.execute(
                select(MedicationCatalogEmergencyDeny)
                .order_by(
                    MedicationCatalogEmergencyDeny.medication_code,
                    MedicationCatalogEmergencyDeny.version.desc(),
                    MedicationCatalogEmergencyDeny.id.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    latest_emergency = {}
    for row in emergency_rows:
        latest_emergency.setdefault(row.medication_code, row)

    active_count = int(
        await session.scalar(
            select(func.count())
            .select_from(MedicationCatalogRelease)
            .where(
                MedicationCatalogRelease.status
                == MedicationCatalogReleaseStatus.ACTIVE.value
            )
        )
        or 0
    )
    return release, entries, evidence_by_entry, latest_emergency, active_count


async def run(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        provenance = _load_source_metadata(args.source_metadata_file)
        package_verified = False
        if args.terminology_package_file is not None:
            package_verified = verify_package_digest(
                provenance,
                args.terminology_package_file,
            )

        source_summary = _safe_source_summary(
            provenance,
            package_digest_verified=package_verified,
        )
        if args.source_only:
            sys.stdout.write(
                json.dumps(
                    {
                        "status": (
                            "READY" if package_verified else "EXTERNALLY_BLOCKED"
                        ),
                        "source": source_summary,
                        "catalog_state_mutated": False,
                        "aws_mutated": False,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
            return 0 if package_verified else 2

        if not args.release_id or not args.expected_database_name:
            raise MedicationCatalogReleaseReadinessError("INVALID_REQUEST")
        db_url = os.environ.get(ENV_DB_URL, "").strip()
        if not db_url:
            raise MedicationCatalogReleaseReadinessError("DATABASE_URL_REQUIRED")
        if db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

        try:
            release_id = UUID(args.release_id)
        except ValueError as exc:
            raise MedicationCatalogReleaseReadinessError("INVALID_REQUEST") from exc

        signer = None
        if args.live_kms_preflight:
            signer = build_production_medication_catalog_signer()

        engine = _create_readiness_engine(db_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                repository_head = await _database_preflight(
                    session,
                    args.expected_database_name,
                )
                (
                    release,
                    entries,
                    evidence_by_entry,
                    emergency,
                    active_count,
                ) = await _load_release_projection(session, release_id)
                report = await assess_release_readiness(
                    release=release,
                    entries=entries,
                    evidence_by_entry=evidence_by_entry,
                    latest_emergency_by_code=emergency,
                    active_release_count=active_count,
                    provenance=provenance,
                    package_digest_verified=package_verified,
                    signing_provider=signer,
                )
        finally:
            await engine.dispose()

        sanitized = report.to_sanitized_dict()
        overall_ready = bool(
            sanitized["qualification_ready"] or sanitized["activation_ready"]
        )
        sys.stdout.write(
            json.dumps(
                {
                    "status": "READY" if overall_ready else "BLOCKED",
                    "repository_head": repository_head,
                    "source": source_summary,
                    "release": sanitized,
                    "catalog_state_mutated": False,
                    "aws_mutated": False,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        return 0 if overall_ready else 2
    except (
        MedicationCatalogReleaseReadinessError,
        MedicationCatalogSigningError,
    ) as exc:
        sys.stderr.write(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "error": exc.code,
                    "catalog_state_mutated": False,
                    "aws_mutated": False,
                },
                sort_keys=True,
            )
            + "\n"
        )
        return 1
    except Exception:
        sys.stderr.write(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "error": "READINESS_CHECK_FAILED",
                    "catalog_state_mutated": False,
                    "aws_mutated": False,
                },
                sort_keys=True,
            )
            + "\n"
        )
        return 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
