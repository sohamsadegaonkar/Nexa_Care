#!/usr/bin/env python3
"""Guarded offline operator CLI for root-of-trust administration (TRUST_PERMISSION_MANAGE).

Operational authority is grounded solely in custody of the dedicated database credential
provided via NEXA_TRUST_ROOT_DATABASE_URL. This tool records operator_actor_id and
approver_actor_id as mandatory governance evidence; it does NOT independently authenticate
dual-control credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.provider_trust_root_governance import (  # noqa: E402
    OPERATOR_REVOCATION_REASONS,
    ProviderTrustRootGovernanceError,
    ProviderTrustRootGovernanceService,
    RootRevocationReasonCode,
)

ENV_DB_URL = "NEXA_TRUST_ROOT_DATABASE_URL"


def _derive_repository_heads() -> tuple[str, ...]:
    """Derive repository heads dynamically from Alembic config."""
    alembic_ini = ROOT / "alembic.ini"
    config = Config(str(alembic_ini))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return tuple(ScriptDirectory.from_config(config).get_heads())


async def _preflight_guards(session: AsyncSession, expected_db_name: str) -> None:
    """Assert database name match and current Alembic schema revision."""
    try:
        # 1. Database name guard
        current_db = (await session.execute(text("SELECT current_database()"))).scalar()
        if current_db != expected_db_name:
            raise ProviderTrustRootGovernanceError(
                "DATABASE_NAME_MISMATCH",
                message=f"Connected database {current_db!r} does not match expected {expected_db_name!r}",
            )

        # 2. Schema revision preflight
        try:
            repo_heads = _derive_repository_heads()
        except Exception as exc:
            raise ProviderTrustRootGovernanceError(
                "SCHEMA_REVISION_MISMATCH",
                message=f"Failed to derive repository Alembic heads: {exc}",
            ) from exc

        if len(repo_heads) != 1:
            raise ProviderTrustRootGovernanceError(
                "SCHEMA_REVISION_MISMATCH",
                message=f"Expected exactly 1 repository head, found: {repo_heads}",
            )
        expected_head = repo_heads[0]

        db_version = (
            await session.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one_or_none()
        if db_version != expected_head:
            raise ProviderTrustRootGovernanceError(
                "SCHEMA_REVISION_MISMATCH",
                message=f"Connected database revision {db_version!r} does not match repository head {expected_head!r}",
            )
    finally:
        if session.in_transaction():
            await session.rollback()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Guarded offline operator CLI for TRUST_PERMISSION_MANAGE administration",
        add_help=True,
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # GRANT_ROOT command
    grant_parser = subparsers.add_parser("grant-root", aliases=["GRANT_ROOT", "grant"])
    grant_parser.add_argument("--expected-database-name", required=True, type=str)
    grant_parser.add_argument("--apply", action="store_true", default=False)
    grant_parser.add_argument("--operator-actor-id", required=True, type=str)
    grant_parser.add_argument("--approver-actor-id", required=True, type=str)
    grant_parser.add_argument("--governance-reference", required=True, type=str)
    grant_parser.add_argument("--idempotency-key", required=True, type=str)
    grant_parser.add_argument("--expected-active-root-count", required=True, type=int)
    grant_parser.add_argument("--target-provider-id", required=True, type=str)
    grant_parser.add_argument("--confirm-target-provider-id", required=True, type=str)
    grant_parser.add_argument("--valid-until", required=True, type=str)

    # REVOKE_ROOT command
    revoke_parser = subparsers.add_parser(
        "revoke-root", aliases=["REVOKE_ROOT", "revoke"]
    )
    revoke_parser.add_argument("--expected-database-name", required=True, type=str)
    revoke_parser.add_argument("--apply", action="store_true", default=False)
    revoke_parser.add_argument("--operator-actor-id", required=True, type=str)
    revoke_parser.add_argument("--approver-actor-id", required=True, type=str)
    revoke_parser.add_argument("--governance-reference", required=True, type=str)
    revoke_parser.add_argument("--idempotency-key", required=True, type=str)
    revoke_parser.add_argument("--expected-active-root-count", required=True, type=int)
    revoke_parser.add_argument("--grant-id", required=True, type=str)
    revoke_parser.add_argument("--confirm-grant-id", required=True, type=str)
    revoke_parser.add_argument(
        "--revocation-reason-code",
        "--reason",
        dest="revocation_reason_code",
        required=True,
        type=str,
        choices=sorted([r.value for r in OPERATOR_REVOCATION_REASONS]),
    )
    revoke_parser.add_argument(
        "--acknowledge-zero-active-roots", action="store_true", default=False
    )

    return parser


async def _execute_grant(
    session: AsyncSession,
    args: argparse.Namespace,
) -> dict:
    if args.target_provider_id != args.confirm_target_provider_id:
        raise ProviderTrustRootGovernanceError("CONFIRMATION_MISMATCH")

    try:
        target_uuid = UUID(args.target_provider_id)
    except (ValueError, TypeError) as exc:
        raise ProviderTrustRootGovernanceError("INVALID_REQUEST") from exc

    try:
        valid_until_dt = datetime.fromisoformat(args.valid_until)
    except (ValueError, TypeError) as exc:
        raise ProviderTrustRootGovernanceError("INVALID_REQUEST") from exc

    service = ProviderTrustRootGovernanceService(session)
    result = await service.grant_root(
        operator_actor_id=args.operator_actor_id,
        approver_actor_id=args.approver_actor_id,
        target_provider_id=target_uuid,
        valid_until=valid_until_dt,
        expected_active_root_count=args.expected_active_root_count,
        governance_reference=args.governance_reference,
        idempotency_key=args.idempotency_key,
    )
    return result.to_dict()


async def _execute_revoke(
    session: AsyncSession,
    args: argparse.Namespace,
) -> dict:
    if args.grant_id != args.confirm_grant_id:
        raise ProviderTrustRootGovernanceError("CONFIRMATION_MISMATCH")

    try:
        grant_uuid = UUID(args.grant_id)
    except (ValueError, TypeError) as exc:
        raise ProviderTrustRootGovernanceError("INVALID_REQUEST") from exc

    try:
        reason_code = RootRevocationReasonCode(args.revocation_reason_code)
    except (ValueError, TypeError) as exc:
        raise ProviderTrustRootGovernanceError("INVALID_REQUEST") from exc

    service = ProviderTrustRootGovernanceService(session)
    result = await service.revoke_root(
        operator_actor_id=args.operator_actor_id,
        approver_actor_id=args.approver_actor_id,
        grant_id=grant_uuid,
        revocation_reason_code=reason_code,
        expected_active_root_count=args.expected_active_root_count,
        governance_reference=args.governance_reference,
        idempotency_key=args.idempotency_key,
        acknowledge_zero_active_roots=args.acknowledge_zero_active_roots,
    )
    return result.to_dict()


async def run_governance(argv: list[str] | None = None) -> int:
    """Asynchronous entry point for the governance tool."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2

    # 1. Explicit apply guard
    if not args.apply:
        sys.stderr.write(json.dumps({"error": "EXPLICIT_APPLY_REQUIRED"}) + "\n")
        return 1

    # 2. Dedicated DB URL check
    db_url = os.environ.get(ENV_DB_URL)
    if not db_url or not db_url.strip():
        sys.stderr.write(
            json.dumps(
                {
                    "error": "INVALID_REQUEST",
                    "message": f"Environment variable {ENV_DB_URL} is required",
                }
            )
            + "\n"
        )
        return 1

    # Ensure asyncpg driver
    clean_url = db_url.strip()
    if clean_url.startswith("postgresql://"):
        clean_url = clean_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(clean_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            # 3. Preflight guards: database name and Alembic head check
            await _preflight_guards(session, args.expected_database_name)

            # 4. Command execution
            normalized_cmd = args.subcommand.upper().replace("-", "_")
            if normalized_cmd in ("GRANT_ROOT", "GRANT"):
                output = await _execute_grant(session, args)
            elif normalized_cmd in ("REVOKE_ROOT", "REVOKE"):
                output = await _execute_revoke(session, args)
            else:
                sys.stderr.write(json.dumps({"error": "INVALID_REQUEST"}) + "\n")
                return 1

        sys.stdout.write(json.dumps(output, indent=2) + "\n")
        return 0
    except ProviderTrustRootGovernanceError as exc:
        sys.stderr.write(json.dumps({"error": exc.code}) + "\n")
        return 1
    except Exception:
        sys.stderr.write(json.dumps({"error": "TRANSACTION_INTEGRITY_FAILURE"}) + "\n")
        return 1
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run_governance(argv))


if __name__ == "__main__":
    sys.exit(main())
