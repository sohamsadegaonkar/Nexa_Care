"""Centralized configuration for Nexa Care.

Security posture:
- Secrets are loaded from environment variables (ideally via a local `.env` file that is NOT committed).
- Fail-fast validation prevents booting the API with missing credentials.

Note: This module only *loads* config; it does not open DB/Redis connections yet.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Resolve the repository .env independently of the process working directory.
# Real deployment environment variables retain precedence over the local file.
load_dotenv(PROJECT_ROOT / ".env", override=False)


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    key: str


@dataclass(frozen=True)
class RedisConfig:
    url: str


@dataclass(frozen=True)
class OtpRateLimitConfig:
    hmac_secret: str


@dataclass(frozen=True)
class HandshakeConfig:
    pepper_secret: str


@dataclass(frozen=True)
class ClinicConfig:
    api_key: str


@dataclass(frozen=True)
class DatabaseConfig:
    url: str
    echo_sql: bool = False


@dataclass(frozen=True)
class DocumentExtractionConfig:
    provider: str
    environment: str
    api_url: str | None = None
    api_key: str | None = None
    aws_region: str = "ap-south-1"
    timeout_seconds: float = 30.0
    provider_max_attempts: int = 3
    job_max_attempts: int = 3


@dataclass(frozen=True)
class DocumentStorageConfig:
    provider: str
    environment: str
    local_root: Path | None = None
    encryption_key: str | None = None
    s3_bucket: str | None = None
    s3_region: str | None = None
    s3_kms_key_id: str | None = None


logger = logging.getLogger("nexa_config")


class RuntimeEnvironment(str, Enum):
    LOCAL = "local"
    DEVELOPMENT = "development"
    TEST = "test"
    ALPHA = "alpha"
    STAGING = "staging"
    PREVIEW = "preview"
    PILOT = "pilot"
    PRODUCTION = "production"

    @property
    def is_production_like(self) -> bool:
        return self in {self.STAGING, self.PREVIEW, self.PILOT, self.PRODUCTION}

    @property
    def is_test(self) -> bool:
        return self is self.TEST

    @property
    def is_demo_allowed(self) -> bool:
        return self in {self.LOCAL, self.DEVELOPMENT, self.TEST, self.ALPHA}

    @property
    def allows_simulator(self) -> bool:
        return self in {self.LOCAL, self.DEVELOPMENT, self.TEST}


_LEGACY_ENVIRONMENT_ALIASES = {"alpha-demo": "alpha"}
_SAFE_DEMO_ENVIRONMENTS = frozenset(
    env.value for env in RuntimeEnvironment if env.is_demo_allowed
)
_PRODUCTION_LIKE_ENVIRONMENTS = frozenset(
    env.value for env in RuntimeEnvironment if env.is_production_like
)


def _normalize_environment(raw: str) -> str:
    normalized = raw.strip().lower()
    if normalized in _LEGACY_ENVIRONMENT_ALIASES:
        logger.warning(
            "Deprecated runtime environment name used; migrate to canonical ENVIRONMENT value"
        )
        return _LEGACY_ENVIRONMENT_ALIASES[normalized]
    return normalized


def get_runtime_environment() -> RuntimeEnvironment:
    canonical_raw = os.getenv("ENVIRONMENT")
    legacy_raw = os.getenv("ENV")
    if canonical_raw and legacy_raw:
        canonical = _normalize_environment(canonical_raw)
        legacy = _normalize_environment(legacy_raw)
        if canonical != legacy:
            raise ConfigError("ENVIRONMENT and legacy ENV disagree")
        value = canonical
        logger.warning(
            "Legacy ENV is deprecated; remove it after migration to ENVIRONMENT"
        )
    elif canonical_raw:
        value = _normalize_environment(canonical_raw)
    elif legacy_raw:
        value = _normalize_environment(legacy_raw)
        logger.warning("Legacy ENV is deprecated; configure ENVIRONMENT instead")
    else:
        raise ConfigError(
            "ENVIRONMENT must explicitly identify the runtime environment"
        )
    try:
        return RuntimeEnvironment(value)
    except ValueError as exc:
        raise ConfigError("Unsupported ENVIRONMENT value") from exc


def runtime_environment() -> str:
    return get_runtime_environment().value


def get_break_glass_mfa_max_age_seconds() -> int:
    raw = os.getenv("BREAK_GLASS_MFA_MAX_AGE_SECONDS", "600")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError("BREAK_GLASS_MFA_MAX_AGE_SECONDS must be an integer") from exc
    if not 60 <= value <= 1800:
        raise ConfigError("BREAK_GLASS_MFA_MAX_AGE_SECONDS must be between 60 and 1800")
    return value


def get_document_extraction_config() -> DocumentExtractionConfig:
    """Validate the explicitly selected extraction provider, fail closed."""

    environment = runtime_environment()
    if not environment:
        raise ConfigError(
            "ENVIRONMENT (or ENV) must explicitly identify the runtime environment"
        )
    provider = _require_env("DOCUMENT_EXTRACTION_PROVIDER").strip().lower()
    if provider not in {"remote", "demo", "aws_textract"}:
        raise ConfigError(
            "DOCUMENT_EXTRACTION_PROVIDER must be 'remote', 'demo', or 'aws_textract'"
        )
    if provider == "demo":
        if environment not in _SAFE_DEMO_ENVIRONMENTS:
            raise ConfigError(
                f"Demo document extraction is forbidden in environment '{environment}'"
            )
        return DocumentExtractionConfig(provider=provider, environment=environment)

    try:
        timeout_seconds = float(os.getenv("DOCUMENT_AI_TIMEOUT_SECONDS", "30"))
        legacy_max_attempts = os.getenv("DOCUMENT_AI_MAX_ATTEMPTS")
        provider_max_attempts = int(
            os.getenv("DOCUMENT_AI_PROVIDER_MAX_ATTEMPTS", legacy_max_attempts or "3")
        )
        job_max_attempts = int(
            os.getenv("DOCUMENT_AI_JOB_MAX_ATTEMPTS", legacy_max_attempts or "3")
        )
    except ValueError as exc:
        raise ConfigError(
            "Document extraction timeout/retry configuration is invalid"
        ) from exc
    if timeout_seconds <= 0 or not (
        1 <= provider_max_attempts <= 5 and 1 <= job_max_attempts <= 5
    ):
        raise ConfigError(
            "Document extraction timeout must be positive and retry budgets must be 1..5"
        )
    if legacy_max_attempts is not None and (
        os.getenv("DOCUMENT_AI_PROVIDER_MAX_ATTEMPTS") is None
        or os.getenv("DOCUMENT_AI_JOB_MAX_ATTEMPTS") is None
    ):
        logger.warning(
            "DOCUMENT_AI_MAX_ATTEMPTS is deprecated; configure separate provider and job retry budgets"
        )
    if provider == "aws_textract":
        aws_region = os.getenv("DOCUMENT_AI_AWS_REGION", "ap-south-1").strip()
        if not aws_region:
            raise ConfigError("DOCUMENT_AI_AWS_REGION must not be empty")
        return DocumentExtractionConfig(
            provider=provider,
            environment=environment,
            aws_region=aws_region,
            timeout_seconds=timeout_seconds,
            provider_max_attempts=provider_max_attempts,
            job_max_attempts=job_max_attempts,
        )

    api_url = _require_env("DOCUMENT_AI_API_URL").strip()
    api_key = _require_env("DOCUMENT_AI_API_KEY").strip()
    if environment in _PRODUCTION_LIKE_ENVIRONMENTS and not api_url.lower().startswith(
        "https://"
    ):
        raise ConfigError(
            "DOCUMENT_AI_API_URL must use HTTPS in production-like environments"
        )
    return DocumentExtractionConfig(
        provider=provider,
        environment=environment,
        api_url=api_url,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        provider_max_attempts=provider_max_attempts,
        job_max_attempts=job_max_attempts,
    )


def get_document_storage_config() -> DocumentStorageConfig:
    environment = runtime_environment()
    if not environment:
        raise ConfigError(
            "ENVIRONMENT (or ENV) must explicitly identify the runtime environment"
        )
    provider = _require_env("DOCUMENT_STORAGE_PROVIDER").strip().lower()
    if provider == "local":
        if environment not in _SAFE_DEMO_ENVIRONMENTS:
            raise ConfigError(
                f"Local document storage is forbidden in environment '{environment}'"
            )
        root = Path(_require_env("DOCUMENT_STORAGE_LOCAL_ROOT")).expanduser().resolve()
        key = _require_env("DOCUMENT_STORAGE_ENCRYPTION_KEY")
        return DocumentStorageConfig(
            provider=provider,
            environment=environment,
            local_root=root,
            encryption_key=key,
        )
    if provider == "s3":
        return DocumentStorageConfig(
            provider=provider,
            environment=environment,
            encryption_key=_require_env("DOCUMENT_STORAGE_ENCRYPTION_KEY"),
            s3_bucket=_require_env("DOCUMENT_STORAGE_S3_BUCKET"),
            s3_region=_require_env("DOCUMENT_STORAGE_S3_REGION"),
            s3_kms_key_id=_require_env("DOCUMENT_STORAGE_S3_KMS_KEY_ID"),
        )
    raise ConfigError("DOCUMENT_STORAGE_PROVIDER must be 'local' or 's3'")


def get_supabase_config() -> SupabaseConfig:
    """Load Supabase connection info from environment.

    Expected variables:
    - SUPABASE_URL
    - SUPABASE_KEY
    """

    return SupabaseConfig(
        url=_require_env("SUPABASE_URL"),
        key=_require_env("SUPABASE_KEY"),
    )


def get_redis_config() -> RedisConfig:
    """Load Upstash Redis URL from environment.

    Expected variables:
    - UPSTASH_REDIS_URL
    """

    return RedisConfig(url=_require_env("UPSTASH_REDIS_URL"))


def get_otp_rate_limit_config() -> OtpRateLimitConfig:
    """Load the independent secret used to pseudonymize OTP limiter keys."""

    secret = _require_env("OTP_RATE_LIMIT_HMAC_SECRET")
    if len(secret.encode("utf-8")) < 32:
        raise ConfigError("OTP_RATE_LIMIT_HMAC_SECRET must be at least 32 bytes")
    return OtpRateLimitConfig(hmac_secret=secret)


def get_handshake_config() -> HandshakeConfig:
    """Load the server-side HMAC pepper used to verify biometric bindings
    (see app/services/biometric_registry.py).

    Expected variable:
    - HANDSHAKE_PEPPER_SECRET

    This value must never be derivable from nfc_uid or bio_seed, and must
    never be written to the database -- only held in environment/secrets
    config. Rotating it invalidates every existing biometric binding,
    since old verifiers can no longer be recomputed and matched -- treat
    rotation as a full re-enrollment event, not a routine secret rotation.
    """

    return HandshakeConfig(pepper_secret=_require_env("HANDSHAKE_PEPPER_SECRET"))


def get_clinic_config() -> ClinicConfig:
    """Load the legacy shared facility-level credential (Phase 0).

    Deprecated: provider routes now authenticate individual clinicians via
    ``get_provider_context`` and ``provider_credential``. Retained only for
    scripts that have not yet migrated.

    Expected variable:
    - CLINIC_API_KEY
    """

    return ClinicConfig(api_key=_require_env("CLINIC_API_KEY"))


@dataclass(frozen=True)
class KMSConfig:
    kek_root_secret: str | None
    encryption_backend: str = "local"
    kms_key_id: str | None = None
    aws_region: str | None = None


def get_kms_config() -> KMSConfig:
    """Load configuration for the Key Management System (KMS).

    Expected variables:
    - KEK_ROOT_SECRET — root secret used to derive the Key Encryption Key (KEK)
    - ENCRYPTION_BACKEND — optional 'local' (default) or 'kms'
    """
    backend = os.getenv("ENCRYPTION_BACKEND", "local").strip().lower()
    root_secret = os.getenv("KEK_ROOT_SECRET")
    if backend == "local" and not root_secret:
        raise ConfigError("Missing required environment variable: KEK_ROOT_SECRET")
    key_id = os.getenv("KMS_KEY_ID")
    region = os.getenv("AWS_REGION")
    if backend == "kms" and (not key_id or not region):
        raise ConfigError("ENCRYPTION_BACKEND=kms requires KMS_KEY_ID and AWS_REGION")
    return KMSConfig(
        kek_root_secret=root_secret,
        encryption_backend=backend,
        kms_key_id=key_id,
        aws_region=region,
    )


def get_database_config() -> DatabaseConfig:
    """Load async Postgres connection settings for the provider layer.

    Expected variables:
    - DATABASE_URL — async SQLAlchemy URL, e.g.
      ``postgresql+asyncpg://user:pass@host:5432/nexa_care``
    - DATABASE_ECHO_SQL — optional ``true`` to log SQL statements
    """

    echo_raw = os.getenv("DATABASE_ECHO_SQL", "false").strip().lower()
    return DatabaseConfig(
        url=_require_env("DATABASE_URL"),
        echo_sql=echo_raw in {"1", "true", "yes", "on"},
    )
