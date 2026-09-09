"""Fail-closed production runtime validation and dependency preflight.

This module is intentionally value-safe: configuration errors name only the
invalid setting, and runtime failures expose stable error codes rather than
credentials, URLs, AWS identifiers, or provider exception messages.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.database import get_async_engine
from app.core.redis import get_async_redis_client

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_LIKE_ENVIRONMENTS = frozenset(
    {"staging", "preview", "pilot", "production"}
)
STATIC_AWS_CREDENTIALS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
)
MAX_UPLOAD_BYTES_HARD_LIMIT = 20 * 1024 * 1024
MIN_SECRET_BYTES = 32

_REQUIRED_PRODUCTION_SECRETS = (
    "SUPABASE_KEY",
    "HANDSHAKE_PEPPER_SECRET",
    "MFA_ENCRYPTION_KEY",
    "PII_ENCRYPTION_KEY",
    "PATIENT_JWT_SECRET",
    "OTP_RATE_LIMIT_HMAC_SECRET",
    "PROVIDER_REGISTRATION_IDEMPOTENCY_HMAC_SECRET",
    "PROVIDER_CONTACT_ASSURANCE_HMAC_SECRET",
    "OPERATIONS_AUTH_TOKEN",
    "DOCUMENT_STORAGE_ENCRYPTION_KEY",
)
_MINIMUM_LENGTH_SECRETS = (
    "HANDSHAKE_PEPPER_SECRET",
    "PATIENT_JWT_SECRET",
    "OTP_RATE_LIMIT_HMAC_SECRET",
    "PROVIDER_REGISTRATION_IDEMPOTENCY_HMAC_SECRET",
    "PROVIDER_CONTACT_ASSURANCE_HMAC_SECRET",
    "OPERATIONS_AUTH_TOKEN",
)
_PLACEHOLDER_SECRET_PATTERN = re.compile(
    r"(?:<[^>]+>|change[-_ ]?me|replace(?:_with)?|generate|placeholder)", re.I
)


class RuntimePreflightError(RuntimeError):
    """Fail-closed startup error with a stable, non-sensitive code."""

    def __init__(self, code: str, *, issues: tuple[str, ...] = ()) -> None:
        self.code = code
        self.issues = issues
        super().__init__(code)


@dataclass(frozen=True)
class RuntimePreflightReport:
    environment: str
    production_like: bool
    migration_head: str | None
    checks: tuple[str, ...]


def _value(environment: Mapping[str, str], name: str) -> str:
    return environment.get(name, "").strip()


def _runtime_environment(environment: Mapping[str, str]) -> str:
    canonical = _value(environment, "ENVIRONMENT").lower()
    legacy = _value(environment, "ENV").lower()
    if canonical and legacy and canonical != legacy:
        return ""
    return canonical or legacy


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return True
    normalized = host.rstrip(".").lower()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _valid_fernet_key(value: str) -> bool:
    try:
        raw = base64.urlsafe_b64decode(value.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return False
    return len(raw) == 32


def _valid_network_list(raw: str) -> bool:
    entries = [item.strip() for item in raw.split(",") if item.strip()]
    if not entries or any("*" in item for item in entries):
        return False
    for item in entries:
        try:
            network = ipaddress.ip_network(item, strict=False)
        except ValueError:
            return False
        if network.prefixlen == 0:
            return False
    return True


def _valid_https_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme == "https"
            and parsed.netloc
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
            and "*" not in value
        )
    except ValueError:
        return False


def _placeholder_secret(value: str) -> bool:
    return bool(_PLACEHOLDER_SECRET_PATTERN.search(value))


def validate_production_configuration(
    environment: Mapping[str, str],
) -> list[str]:
    """Return value-safe production configuration errors.

    This is the application-level contract. Pilot-specific tooling may add
    stricter provider/region requirements on top of these common invariants.
    """

    errors: list[str] = []
    runtime = _runtime_environment(environment)
    if runtime not in PRODUCTION_LIKE_ENVIRONMENTS:
        errors.append("ENVIRONMENT: production-like runtime required")
        return errors

    configured_secrets: dict[str, str] = {}
    for name in _REQUIRED_PRODUCTION_SECRETS:
        value = _value(environment, name)
        if not value:
            errors.append(f"{name}: required")
            continue
        configured_secrets[name] = value
        if _placeholder_secret(value):
            errors.append(f"{name}: placeholder value forbidden")

    for name in _MINIMUM_LENGTH_SECRETS:
        value = _value(environment, name)
        if value and len(value.encode("utf-8")) < MIN_SECRET_BYTES:
            errors.append(f"{name}: must be at least 32 bytes")

    secret_owners: dict[str, str] = {}
    reused_names: set[str] = set()
    for name, value in configured_secrets.items():
        previous = secret_owners.setdefault(value, name)
        if previous != name:
            reused_names.update({previous, name})
    for name in sorted(reused_names):
        errors.append(f"{name}: secret value must be independently generated")

    for name in ("MFA_ENCRYPTION_KEY", "PII_ENCRYPTION_KEY"):
        value = _value(environment, name)
        if value and not _valid_fernet_key(value):
            errors.append(f"{name}: invalid Fernet key")

    storage_key = _value(environment, "DOCUMENT_STORAGE_ENCRYPTION_KEY")
    if storage_key and not _valid_fernet_key(storage_key):
        errors.append("DOCUMENT_STORAGE_ENCRYPTION_KEY: invalid 32-byte base64 key")

    supabase_url = _value(environment, "SUPABASE_URL")
    if not supabase_url:
        errors.append("SUPABASE_URL: required")
    elif not _valid_https_origin(supabase_url):
        errors.append("SUPABASE_URL: explicit HTTPS origin required")

    database_url = _value(environment, "DATABASE_URL")
    if not database_url:
        errors.append("DATABASE_URL: required")
    else:
        try:
            parsed = urlsplit(database_url)
            valid_database = parsed.scheme == "postgresql+asyncpg"
            database_host = parsed.hostname
        except ValueError:
            valid_database = False
            database_host = None
        if not valid_database:
            errors.append("DATABASE_URL: postgresql+asyncpg URL required")
        if _is_loopback_host(database_host):
            errors.append("DATABASE_URL: loopback host forbidden")

    redis_url = _value(environment, "UPSTASH_REDIS_URL")
    if not redis_url:
        errors.append("UPSTASH_REDIS_URL: required")
    else:
        try:
            parsed = urlsplit(redis_url)
            valid_redis = parsed.scheme == "rediss"
            redis_host = parsed.hostname
        except ValueError:
            valid_redis = False
            redis_host = None
        if not valid_redis:
            errors.append("UPSTASH_REDIS_URL: TLS rediss:// required")
        if _is_loopback_host(redis_host):
            errors.append("UPSTASH_REDIS_URL: loopback host forbidden")

    origins_raw = _value(environment, "CORS_ALLOWED_ORIGINS")
    origins = [item.strip() for item in origins_raw.split(",") if item.strip()]
    if not origins or any(not _valid_https_origin(origin) for origin in origins):
        errors.append("CORS_ALLOWED_ORIGINS: explicit HTTPS origins required")

    trusted_hosts_raw = _value(environment, "TRUSTED_HOSTS")
    trusted_hosts = [item.strip() for item in trusted_hosts_raw.split(",") if item.strip()]
    if not trusted_hosts or any("*" in host for host in trusted_hosts):
        errors.append("TRUSTED_HOSTS: explicit hosts without wildcards required")

    for name in ("TRUSTED_PROXY_NETWORKS", "FORWARDED_ALLOW_IPS"):
        if not _valid_network_list(_value(environment, name)):
            errors.append(f"{name}: explicit non-public networks required")

    if _value(environment, "DOCUMENT_STORAGE_PROVIDER").lower() != "s3":
        errors.append("DOCUMENT_STORAGE_PROVIDER: s3 required")
    for name in (
        "DOCUMENT_STORAGE_S3_BUCKET",
        "DOCUMENT_STORAGE_S3_REGION",
        "DOCUMENT_STORAGE_S3_KMS_KEY_ID",
    ):
        if not _value(environment, name):
            errors.append(f"{name}: required")

    if _value(environment, "ENCRYPTION_BACKEND").lower() != "kms":
        errors.append("ENCRYPTION_BACKEND: kms required")
    for name in ("AWS_REGION", "KMS_KEY_ID"):
        if not _value(environment, name):
            errors.append(f"{name}: required")
    if _value(environment, "AWS_PATIENT_SPECIFIC_KMS_KEYS").lower() != "false":
        errors.append("AWS_PATIENT_SPECIFIC_KMS_KEYS: must be explicitly false")

    extraction_provider = _value(environment, "DOCUMENT_EXTRACTION_PROVIDER").lower()
    if extraction_provider not in {"aws_textract", "remote"}:
        errors.append("DOCUMENT_EXTRACTION_PROVIDER: production provider required")
    elif extraction_provider == "aws_textract":
        if not _value(environment, "DOCUMENT_AI_AWS_REGION"):
            errors.append("DOCUMENT_AI_AWS_REGION: required")
    else:
        api_url = _value(environment, "DOCUMENT_AI_API_URL")
        if not api_url.startswith("https://"):
            errors.append("DOCUMENT_AI_API_URL: HTTPS required")
        if not _value(environment, "DOCUMENT_AI_API_KEY"):
            errors.append("DOCUMENT_AI_API_KEY: required")

    if _value(environment, "PUSH_STATUS_TRANSPORT").lower() != "poll":
        errors.append("PUSH_STATUS_TRANSPORT: poll required")
    if _value(environment, "DATABASE_ECHO_SQL").lower() not in {
        "",
        "false",
        "0",
        "no",
        "off",
    }:
        errors.append("DATABASE_ECHO_SQL: must be false")
    if _value(environment, "AUTO_COMMIT").lower() not in {
        "",
        "false",
        "0",
        "no",
        "off",
    }:
        errors.append("AUTO_COMMIT: must be false")

    raw_upload_limit = _value(environment, "MAX_UPLOAD_BYTES") or str(
        MAX_UPLOAD_BYTES_HARD_LIMIT
    )
    try:
        upload_limit = int(raw_upload_limit)
    except ValueError:
        upload_limit = 0
    if not 1 <= upload_limit <= MAX_UPLOAD_BYTES_HARD_LIMIT:
        errors.append("MAX_UPLOAD_BYTES: must be between 1 and 20971520")

    for name in STATIC_AWS_CREDENTIALS:
        if name in environment:
            errors.append(f"{name}: static AWS credentials forbidden")

    return errors


def get_operations_auth_token(
    environment: Mapping[str, str] | None = None,
) -> str | None:
    values = os.environ if environment is None else environment
    token = _value(values, "OPERATIONS_AUTH_TOKEN")
    runtime = _runtime_environment(values)
    if (
        runtime in PRODUCTION_LIKE_ENVIRONMENTS
        and len(token.encode("utf-8")) < MIN_SECRET_BYTES
    ):
        raise RuntimePreflightError("OPERATIONS_AUTH_TOKEN_INVALID")
    return token or None


def repository_migration_heads() -> tuple[str, ...]:
    config = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return tuple(ScriptDirectory.from_config(config).get_heads())


async def verify_database_runtime(engine: AsyncEngine) -> str:
    heads = repository_migration_heads()
    if len(heads) != 1:
        raise RuntimePreflightError("REPOSITORY_MIGRATION_HEAD_INVALID")
    expected_head = heads[0]
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
            result = await connection.execute(
                text("SELECT version_num FROM alembic_version")
            )
            revisions = tuple(str(row[0]) for row in result.fetchall())
    except RuntimePreflightError:
        raise
    except Exception as exc:
        raise RuntimePreflightError("POSTGRES_PREFLIGHT_FAILED") from exc
    if revisions != (expected_head,):
        raise RuntimePreflightError("SCHEMA_REVISION_MISMATCH")
    return expected_head


async def verify_redis_runtime(redis_client) -> None:
    try:
        result = await redis_client.ping()
    except Exception as exc:
        raise RuntimePreflightError("REDIS_PREFLIGHT_FAILED") from exc
    if not result:
        raise RuntimePreflightError("REDIS_PREFLIGHT_FAILED")


def _verify_aws_runtime_sync(
    environment: Mapping[str, str], *, session=None
) -> None:
    try:
        if session is None:
            import boto3

            session = boto3.Session(region_name=_value(environment, "AWS_REGION"))
        from botocore.config import Config

        client_config = Config(
            region_name=_value(environment, "AWS_REGION"),
            connect_timeout=5,
            read_timeout=10,
            retries={"max_attempts": 2, "mode": "standard"},
        )
        kms = session.client("kms", config=client_config)
        for key_id in {
            _value(environment, "KMS_KEY_ID"),
            _value(environment, "DOCUMENT_STORAGE_S3_KMS_KEY_ID"),
        }:
            metadata = kms.describe_key(KeyId=key_id).get("KeyMetadata", {})
            if (
                metadata.get("KeyState") != "Enabled"
                or metadata.get("KeyUsage") != "ENCRYPT_DECRYPT"
            ):
                raise RuntimePreflightError("KMS_KEY_NOT_READY")

        s3 = session.client("s3", config=client_config)
        bucket = _value(environment, "DOCUMENT_STORAGE_S3_BUCKET")
        s3.head_bucket(Bucket=bucket)
        encryption = s3.get_bucket_encryption(Bucket=bucket)
        rules = encryption.get("ServerSideEncryptionConfiguration", {}).get("Rules", [])
        if not any(
            rule.get("ApplyServerSideEncryptionByDefault", {}).get("SSEAlgorithm")
            == "aws:kms"
            for rule in rules
            if isinstance(rule, dict)
        ):
            raise RuntimePreflightError("S3_DEFAULT_ENCRYPTION_NOT_READY")
        public_block = s3.get_public_access_block(Bucket=bucket).get(
            "PublicAccessBlockConfiguration", {}
        )
        if not all(
            public_block.get(name) is True
            for name in (
                "BlockPublicAcls",
                "IgnorePublicAcls",
                "BlockPublicPolicy",
                "RestrictPublicBuckets",
            )
        ):
            raise RuntimePreflightError("S3_PUBLIC_ACCESS_BLOCK_NOT_READY")
        if s3.get_bucket_versioning(Bucket=bucket).get("Status") != "Enabled":
            raise RuntimePreflightError("S3_VERSIONING_NOT_READY")
    except RuntimePreflightError:
        raise
    except Exception as exc:
        raise RuntimePreflightError("AWS_PREFLIGHT_FAILED") from exc


async def verify_aws_runtime(environment: Mapping[str, str] | None = None) -> None:
    values = os.environ if environment is None else environment
    await asyncio.to_thread(_verify_aws_runtime_sync, values)


async def run_production_startup_preflight(
    *,
    environment: Mapping[str, str] | None = None,
    engine: AsyncEngine | None = None,
    redis_client=None,
) -> RuntimePreflightReport:
    values = os.environ if environment is None else environment
    runtime = _runtime_environment(values)
    if runtime not in PRODUCTION_LIKE_ENVIRONMENTS:
        return RuntimePreflightReport(
            environment=runtime,
            production_like=False,
            migration_head=None,
            checks=("configuration",),
        )

    issues = tuple(validate_production_configuration(values))
    if issues:
        raise RuntimePreflightError("STATIC_CONFIGURATION_INVALID", issues=issues)

    database = engine or get_async_engine()
    redis = redis_client or get_async_redis_client()
    migration_head = await verify_database_runtime(database)
    await verify_redis_runtime(redis)
    await verify_aws_runtime(values)
    return RuntimePreflightReport(
        environment=runtime,
        production_like=True,
        migration_head=migration_head,
        checks=("configuration", "postgres", "schema", "redis", "kms", "s3"),
    )
