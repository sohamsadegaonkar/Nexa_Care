#!/usr/bin/env python3
"""Bootstrap Nexa Care pilot PostgreSQL roles without exposing credential material.

This program is designed for a one-off ECS/Fargate task inside the pilot VPC.
It never accepts secret values on the command line and never logs passwords,
secret payloads, SQL containing password literals, or database URLs.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
import secrets
import ssl
from typing import Any, Protocol
from urllib.parse import quote

AWS_REGION = "ap-south-1"
AWS_ACCOUNT_ID = "654654144224"
DATABASE_NAME = "nexacare_pilot"
MASTER_ROLE_NAME = "nexacare_admin"
MIGRATOR_ROLE_NAME = "nexa_migrator"
RUNTIME_ROLE_NAME = "nexa_api_runtime"
FIXED_LOGIN_ROLES = frozenset({MIGRATOR_ROLE_NAME, RUNTIME_ROLE_NAME})
RUNTIME_SECRET_ID = "nexa-care/pilot/db/runtime"
MIGRATOR_SECRET_ID = "nexa-care/pilot/db/migrator"
DATABASE_SSL_CA_PATH = Path("/app/deploy/ssl/aws-rds-ca-bundle.pem")
EXPECTED_MIGRATION_HEAD = "20260919_medication_catalog"

SECRET_KEYS = frozenset(
    {"username", "password", "engine", "host", "port", "dbname", "DATABASE_URL"}
)
ROLE_STATE_SQL = """
SELECT
    rolname,
    rolcanlogin,
    rolsuper,
    rolcreatedb,
    rolcreaterole,
    rolinherit,
    rolreplication,
    rolbypassrls
FROM pg_roles
WHERE rolname = $1
""".strip()
ROLE_MEMBERSHIP_SQL = """
SELECT parent.rolname
FROM pg_auth_members AS membership
JOIN pg_roles AS parent ON parent.oid = membership.roleid
JOIN pg_roles AS member ON member.oid = membership.member
WHERE member.rolname = $1
ORDER BY parent.rolname
""".strip()
MIGRATION_HEAD_SQL = "SELECT version_num FROM public.alembic_version"
ROLE_PASSWORD_DDL_SQL: dict[str, dict[bool, str]] = {
    MIGRATOR_ROLE_NAME: {
        False: (
            "SELECT format('CREATE ROLE nexa_migrator WITH LOGIN NOSUPERUSER "
            "NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS "
            "PASSWORD %L', $1)"
        ),
        True: (
            "SELECT format('ALTER ROLE nexa_migrator WITH LOGIN NOSUPERUSER "
            "NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS "
            "PASSWORD %L', $1)"
        ),
    },
    RUNTIME_ROLE_NAME: {
        False: (
            "SELECT format('CREATE ROLE nexa_api_runtime WITH LOGIN NOSUPERUSER "
            "NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS "
            "PASSWORD %L', $1)"
        ),
        True: (
            "SELECT format('ALTER ROLE nexa_api_runtime WITH LOGIN NOSUPERUSER "
            "NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS "
            "PASSWORD %L', $1)"
        ),
    },
}

MASTER_GRANT_STATEMENTS = (
    "REVOKE ALL ON DATABASE nexacare_pilot FROM PUBLIC",
    "REVOKE ALL ON SCHEMA public FROM PUBLIC",
    "GRANT CONNECT, CREATE ON DATABASE nexacare_pilot TO nexa_migrator",
    "GRANT USAGE, CREATE ON SCHEMA public TO nexa_migrator",
    "GRANT CONNECT ON DATABASE nexacare_pilot TO nexa_api_runtime",
    "GRANT USAGE ON SCHEMA public TO nexa_api_runtime",
    "REVOKE CREATE ON SCHEMA public FROM nexa_api_runtime",
)

MIGRATOR_DEFAULT_PRIVILEGE_STATEMENTS = (
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
    "REVOKE ALL ON TABLES FROM nexa_api_runtime",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
    "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO nexa_api_runtime",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
    "REVOKE ALL ON SEQUENCES FROM nexa_api_runtime",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
    "GRANT USAGE ON SEQUENCES TO nexa_api_runtime",
)

POST_MIGRATION_STATEMENTS = (
    "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM nexa_api_runtime",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
    "TO nexa_api_runtime",
    "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM nexa_api_runtime",
    "GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO nexa_api_runtime",
    "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE public.alembic_version "
    "FROM nexa_api_runtime",
    "GRANT SELECT ON TABLE public.alembic_version TO nexa_api_runtime",
    "REVOKE CREATE ON SCHEMA public FROM nexa_api_runtime",
)


class FailureCode(str, Enum):
    CONFIG_INVALID = "BOOTSTRAP_CONFIG_INVALID"
    PARTIAL_SECRET_STATE = "BOOTSTRAP_PARTIAL_SECRET_STATE"
    SECRET_STATE_INVALID = "BOOTSTRAP_SECRET_STATE_INVALID"
    SECRET_READ_FAILED = "BOOTSTRAP_SECRET_READ_FAILED"
    SECRET_WRITE_FAILED = "BOOTSTRAP_SECRET_WRITE_FAILED"
    SECRET_PAYLOAD_INVALID = "BOOTSTRAP_SECRET_PAYLOAD_INVALID"
    MASTER_SECRET_INVALID = "BOOTSTRAP_MASTER_SECRET_INVALID"
    TLS_CONTEXT_FAILED = "BOOTSTRAP_TLS_CONTEXT_FAILED"
    DB_CONNECT_FAILED = "BOOTSTRAP_DB_CONNECT_FAILED"
    DB_MUTATION_FAILED = "BOOTSTRAP_DB_MUTATION_FAILED"
    ROLE_MEMBERSHIP_INVALID = "BOOTSTRAP_ROLE_MEMBERSHIP_INVALID"
    DEFAULT_PRIVILEGES_FAILED = "BOOTSTRAP_DEFAULT_PRIVILEGES_FAILED"
    MIGRATION_HEAD_MISMATCH = "BOOTSTRAP_MIGRATION_HEAD_MISMATCH"
    POST_MIGRATION_FAILED = "BOOTSTRAP_POST_MIGRATION_FAILED"


class BootstrapError(RuntimeError):
    """Stable redacted bootstrap failure."""

    def __init__(self, code: FailureCode):
        self.code = code
        super().__init__(code.value)


class SecretState(str, Enum):
    EMPTY = "empty"
    CURRENT = "current"


class SecretsClient(Protocol):
    def describe_secret(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_secret_value(self, **kwargs: Any) -> dict[str, Any]: ...
    def put_secret_value(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class DatabaseCredential:
    username: str
    password: str
    host: str
    port: int
    dbname: str = DATABASE_NAME

    @property
    def database_url(self) -> str:
        return build_database_url(self)


@dataclass(frozen=True)
class CredentialPair:
    migrator: DatabaseCredential
    runtime: DatabaseCredential
    source: str


def _safe_environment() -> None:
    if os.getenv("ENVIRONMENT", "").strip().lower() != "pilot":
        raise BootstrapError(FailureCode.CONFIG_INVALID)
    configured_region = os.getenv("AWS_REGION", AWS_REGION).strip()
    if configured_region != AWS_REGION:
        raise BootstrapError(FailureCode.CONFIG_INVALID)
    if os.getenv("DATABASE_ECHO_SQL", "false").strip().lower() not in {
        "false",
        "0",
        "no",
        "off",
    }:
        raise BootstrapError(FailureCode.CONFIG_INVALID)
    master_secret_id = os.getenv("NEXA_DB_MASTER_SECRET_ID", "").strip()
    if not master_secret_id:
        raise BootstrapError(FailureCode.CONFIG_INVALID)


def build_database_url(credential: DatabaseCredential) -> str:
    """Construct a future async SQLAlchemy URL with all credentials percent-encoded."""

    user = quote(credential.username, safe="")
    password = quote(credential.password, safe="")
    database = quote(credential.dbname, safe="")
    return (
        f"postgresql+asyncpg://{user}:{password}@"
        f"{credential.host}:{credential.port}/{database}"
    )


def generate_password() -> str:
    """Generate a high-entropy password without using predictable PRNG state."""

    return secrets.token_urlsafe(48)


def _validate_host(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 253:
        raise BootstrapError(FailureCode.MASTER_SECRET_INVALID)
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")
    if any(character not in allowed for character in value):
        raise BootstrapError(FailureCode.MASTER_SECRET_INVALID)
    if value.startswith(".") or value.endswith(".") or ".." in value:
        raise BootstrapError(FailureCode.MASTER_SECRET_INVALID)
    return value


def _validate_port(value: Any, *, code: FailureCode) -> int:
    if isinstance(value, bool):
        raise BootstrapError(code)
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise BootstrapError(code) from exc
    if not 1 <= port <= 65535:
        raise BootstrapError(code)
    return port


def _secret_state(client: SecretsClient, secret_id: str) -> SecretState:
    try:
        metadata = client.describe_secret(SecretId=secret_id)
    except Exception as exc:
        raise BootstrapError(FailureCode.SECRET_READ_FAILED) from exc
    if metadata.get("DeletedDate") is not None:
        raise BootstrapError(FailureCode.SECRET_STATE_INVALID)
    stages = metadata.get("VersionIdsToStages", {})
    if not isinstance(stages, dict):
        raise BootstrapError(FailureCode.SECRET_STATE_INVALID)
    if not stages:
        return SecretState.EMPTY
    current_versions = [
        version
        for version, version_stages in stages.items()
        if isinstance(version_stages, list) and "AWSCURRENT" in version_stages
    ]
    if len(current_versions) == 1:
        return SecretState.CURRENT
    # Historical versions without AWSCURRENT, or multiple current versions, are ambiguous.
    raise BootstrapError(FailureCode.SECRET_STATE_INVALID)


def _read_secret_string(client: SecretsClient, secret_id: str) -> str:
    try:
        payload = client.get_secret_value(SecretId=secret_id, VersionStage="AWSCURRENT")
    except Exception as exc:
        raise BootstrapError(FailureCode.SECRET_READ_FAILED) from exc
    value = payload.get("SecretString")
    if not isinstance(value, str) or not value:
        raise BootstrapError(FailureCode.SECRET_PAYLOAD_INVALID)
    return value


def _parse_json_secret(value: str, *, code: FailureCode) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise BootstrapError(code) from exc
    if not isinstance(parsed, dict):
        raise BootstrapError(code)
    return parsed


def parse_master_secret(value: str) -> DatabaseCredential:
    payload = _parse_json_secret(value, code=FailureCode.MASTER_SECRET_INVALID)
    if payload.get("username") != MASTER_ROLE_NAME:
        raise BootstrapError(FailureCode.MASTER_SECRET_INVALID)
    password = payload.get("password")
    if not isinstance(password, str) or not password:
        raise BootstrapError(FailureCode.MASTER_SECRET_INVALID)
    host = _validate_host(payload.get("host"))
    port = _validate_port(payload.get("port", 5432), code=FailureCode.MASTER_SECRET_INVALID)
    dbname = payload.get("dbname", DATABASE_NAME)
    if dbname != DATABASE_NAME:
        raise BootstrapError(FailureCode.MASTER_SECRET_INVALID)
    engine = payload.get("engine")
    if engine is not None and engine not in {"postgres", "postgresql"}:
        raise BootstrapError(FailureCode.MASTER_SECRET_INVALID)
    return DatabaseCredential(
        username=MASTER_ROLE_NAME,
        password=password,
        host=host,
        port=port,
    )


def credential_secret_payload(credential: DatabaseCredential) -> str:
    payload = {
        "username": credential.username,
        "password": credential.password,
        "engine": "postgresql+asyncpg",
        "host": credential.host,
        "port": credential.port,
        "dbname": credential.dbname,
        "DATABASE_URL": credential.database_url,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def parse_role_secret(value: str, expected_username: str) -> DatabaseCredential:
    if expected_username not in FIXED_LOGIN_ROLES:
        raise BootstrapError(FailureCode.SECRET_PAYLOAD_INVALID)
    payload = _parse_json_secret(value, code=FailureCode.SECRET_PAYLOAD_INVALID)
    if set(payload) != SECRET_KEYS or payload.get("username") != expected_username:
        raise BootstrapError(FailureCode.SECRET_PAYLOAD_INVALID)
    if payload.get("engine") != "postgresql+asyncpg":
        raise BootstrapError(FailureCode.SECRET_PAYLOAD_INVALID)
    password = payload.get("password")
    host = payload.get("host")
    dbname = payload.get("dbname")
    if not isinstance(password, str) or not password or dbname != DATABASE_NAME:
        raise BootstrapError(FailureCode.SECRET_PAYLOAD_INVALID)
    try:
        validated_host = _validate_host(host)
    except BootstrapError as exc:
        raise BootstrapError(FailureCode.SECRET_PAYLOAD_INVALID) from exc
    port = _validate_port(payload.get("port"), code=FailureCode.SECRET_PAYLOAD_INVALID)
    credential = DatabaseCredential(
        username=expected_username,
        password=password,
        host=validated_host,
        port=port,
    )
    if payload.get("DATABASE_URL") != credential.database_url:
        raise BootstrapError(FailureCode.SECRET_PAYLOAD_INVALID)
    return credential


def _persist_secret(client: SecretsClient, secret_id: str, value: str) -> None:
    try:
        client.put_secret_value(
            SecretId=secret_id,
            SecretString=value,
            VersionStages=["AWSCURRENT"],
        )
    except Exception as exc:
        raise BootstrapError(FailureCode.SECRET_WRITE_FAILED) from exc


def resolve_role_credentials(
    client: SecretsClient, *, host: str, port: int
) -> CredentialPair:
    runtime_state = _secret_state(client, RUNTIME_SECRET_ID)
    migrator_state = _secret_state(client, MIGRATOR_SECRET_ID)
    if runtime_state is not migrator_state:
        raise BootstrapError(FailureCode.PARTIAL_SECRET_STATE)

    if runtime_state is SecretState.EMPTY:
        generated_migrator = DatabaseCredential(
            MIGRATOR_ROLE_NAME, generate_password(), host, port
        )
        generated_runtime = DatabaseCredential(
            RUNTIME_ROLE_NAME, generate_password(), host, port
        )
        # Persist both before any PostgreSQL role mutation. If one write succeeds
        # and the other fails, the next invocation observes the partial state and
        # fails closed rather than silently rotating either side.
        _persist_secret(
            client,
            MIGRATOR_SECRET_ID,
            credential_secret_payload(generated_migrator),
        )
        _persist_secret(
            client,
            RUNTIME_SECRET_ID,
            credential_secret_payload(generated_runtime),
        )
        source = "generated"
    else:
        source = "reused"

    # Always re-read AWSCURRENT and use the persisted material for DB mutation.
    migrator = parse_role_secret(
        _read_secret_string(client, MIGRATOR_SECRET_ID), MIGRATOR_ROLE_NAME
    )
    runtime = parse_role_secret(
        _read_secret_string(client, RUNTIME_SECRET_ID), RUNTIME_ROLE_NAME
    )
    if (migrator.host, migrator.port) != (host, port) or (
        runtime.host,
        runtime.port,
    ) != (host, port):
        raise BootstrapError(FailureCode.SECRET_PAYLOAD_INVALID)
    return CredentialPair(migrator=migrator, runtime=runtime, source=source)


def build_shared_tls_context() -> ssl.SSLContext:
    """Use Nexa's shared database TLS contract and assert verify-full semantics."""

    try:
        from app.core.database import create_database_ssl_context

        context = create_database_ssl_context(DATABASE_SSL_CA_PATH)
    except Exception as exc:
        raise BootstrapError(FailureCode.TLS_CONTEXT_FAILED) from exc
    if (
        context is None
        or context.verify_mode != ssl.CERT_REQUIRED
        or context.check_hostname is not True
    ):
        raise BootstrapError(FailureCode.TLS_CONTEXT_FAILED)
    return context


async def connect_database(credential: DatabaseCredential) -> Any:
    try:
        import asyncpg

        return await asyncpg.connect(
            user=credential.username,
            password=credential.password,
            host=credential.host,
            port=credential.port,
            database=credential.dbname,
            ssl=build_shared_tls_context(),
            timeout=15,
            command_timeout=30,
            statement_cache_size=0,
        )
    except BootstrapError:
        raise
    except Exception as exc:
        raise BootstrapError(FailureCode.DB_CONNECT_FAILED) from exc


async def inspect_existing_fixed_role(connection: Any, role_name: str) -> bool:
    if role_name not in FIXED_LOGIN_ROLES:
        raise BootstrapError(FailureCode.DB_MUTATION_FAILED)
    try:
        state = await connection.fetchrow(ROLE_STATE_SQL, role_name)
        if state is None:
            return False
        if state["rolname"] != role_name:
            raise BootstrapError(FailureCode.DB_MUTATION_FAILED)
        memberships = await connection.fetch(ROLE_MEMBERSHIP_SQL, role_name)
    except BootstrapError:
        raise
    except Exception as exc:
        raise BootstrapError(FailureCode.DB_MUTATION_FAILED) from exc
    if memberships:
        raise BootstrapError(FailureCode.ROLE_MEMBERSHIP_INVALID)
    return True


async def ensure_fixed_login_role(connection: Any, role_name: str, password: str) -> None:
    exists = await inspect_existing_fixed_role(connection, role_name)
    try:
        ddl = await connection.fetchval(ROLE_PASSWORD_DDL_SQL[role_name][exists], password)
        if not isinstance(ddl, str) or not ddl:
            raise BootstrapError(FailureCode.DB_MUTATION_FAILED)
        await connection.execute(ddl)
    except BootstrapError:
        raise
    except Exception as exc:
        raise BootstrapError(FailureCode.DB_MUTATION_FAILED) from exc


async def apply_master_grants(connection: Any) -> None:
    for statement in MASTER_GRANT_STATEMENTS:
        await connection.execute(statement)


async def apply_migrator_default_privileges(connection: Any) -> None:
    for statement in MIGRATOR_DEFAULT_PRIVILEGE_STATEMENTS:
        await connection.execute(statement)


async def verify_exact_migration_head(connection: Any) -> None:
    try:
        rows = await connection.fetch(MIGRATION_HEAD_SQL)
    except Exception as exc:
        raise BootstrapError(FailureCode.MIGRATION_HEAD_MISMATCH) from exc
    try:
        revisions = [row["version_num"] for row in rows]
    except (KeyError, TypeError) as exc:
        raise BootstrapError(FailureCode.MIGRATION_HEAD_MISMATCH) from exc
    if revisions != [EXPECTED_MIGRATION_HEAD]:
        raise BootstrapError(FailureCode.MIGRATION_HEAD_MISMATCH)


async def apply_post_migration_grants(connection: Any) -> None:
    for statement in POST_MIGRATION_STATEMENTS:
        await connection.execute(statement)


async def run_master_phase(connection: Any, pair: CredentialPair) -> None:
    try:
        async with connection.transaction():
            await ensure_fixed_login_role(
                connection, MIGRATOR_ROLE_NAME, pair.migrator.password
            )
            await ensure_fixed_login_role(
                connection, RUNTIME_ROLE_NAME, pair.runtime.password
            )
            await apply_master_grants(connection)
    except BootstrapError:
        raise
    except Exception as exc:
        raise BootstrapError(FailureCode.DB_MUTATION_FAILED) from exc


async def run_migrator_default_privilege_phase(connection: Any) -> None:
    try:
        async with connection.transaction():
            await apply_migrator_default_privileges(connection)
    except BootstrapError:
        raise
    except Exception as exc:
        raise BootstrapError(FailureCode.DEFAULT_PRIVILEGES_FAILED) from exc


async def run_post_migration_phase(connection: Any) -> None:
    try:
        async with connection.transaction():
            await verify_exact_migration_head(connection)
            await run_post_migration_phase(connection)
    except BootstrapError:
        raise
    except Exception as exc:
        raise BootstrapError(FailureCode.POST_MIGRATION_FAILED) from exc


def default_secrets_client() -> SecretsClient:
    try:
        import boto3

        return boto3.client("secretsmanager", region_name=AWS_REGION)
    except Exception as exc:
        raise BootstrapError(FailureCode.CONFIG_INVALID) from exc


async def bootstrap(client: SecretsClient) -> CredentialPair:
    _safe_environment()
    master_secret_id = os.environ["NEXA_DB_MASTER_SECRET_ID"].strip()
    master = parse_master_secret(_read_secret_string(client, master_secret_id))
    pair = resolve_role_credentials(client, host=master.host, port=master.port)

    master_connection = await connect_database(master)
    try:
        await run_master_phase(master_connection, pair)
    finally:
        await master_connection.close()

    migrator_connection = await connect_database(pair.migrator)
    try:
        await run_migrator_default_privilege_phase(migrator_connection)
    finally:
        await migrator_connection.close()
    return pair


async def post_migration(client: SecretsClient) -> None:
    _safe_environment()
    runtime_state = _secret_state(client, RUNTIME_SECRET_ID)
    migrator_state = _secret_state(client, MIGRATOR_SECRET_ID)
    if runtime_state is not SecretState.CURRENT or migrator_state is not SecretState.CURRENT:
        if runtime_state is not migrator_state:
            raise BootstrapError(FailureCode.PARTIAL_SECRET_STATE)
        raise BootstrapError(FailureCode.SECRET_STATE_INVALID)
    migrator = parse_role_secret(
        _read_secret_string(client, MIGRATOR_SECRET_ID), MIGRATOR_ROLE_NAME
    )
    connection = await connect_database(migrator)
    try:
        await apply_post_migration_grants(connection)
    finally:
        await connection.close()


def _emit_success(code: str) -> None:
    print(f"PASS: {code}")


def _emit_failure(code: FailureCode) -> None:
    print(f"ERROR: {code.value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("bootstrap", "post-migration"))
    args = parser.parse_args(argv)
    try:
        client = default_secrets_client()
        if args.phase == "bootstrap":
            pair = asyncio.run(bootstrap(client))
            _emit_success(
                "BOOTSTRAP_SECRET_STATE_GENERATED"
                if pair.source == "generated"
                else "BOOTSTRAP_SECRET_STATE_REUSED"
            )
            _emit_success("BOOTSTRAP_ROLES_AND_DEFAULT_PRIVILEGES_READY")
        else:
            asyncio.run(post_migration(client))
            _emit_success("BOOTSTRAP_POST_MIGRATION_GRANTS_READY")
    except BootstrapError as exc:
        _emit_failure(exc.code)
        return 1
    except Exception:
        _emit_failure(FailureCode.DB_MUTATION_FAILED)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
