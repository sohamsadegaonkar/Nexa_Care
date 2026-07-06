"""Centralized configuration for Nexa Care.

Security posture:
- Secrets are loaded from environment variables (ideally via a local `.env` file that is NOT committed).
- Fail-fast validation prevents booting the API with missing credentials.

Note: This module only *loads* config; it does not open DB/Redis connections yet.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv() # This forces Python to read your .env file!


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
class HandshakeConfig:
    pepper_secret: str


@dataclass(frozen=True)
class ClinicConfig:
    api_key: str


@dataclass(frozen=True)
class DatabaseConfig:
    url: str
    echo_sql: bool = False


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
    kek_root_secret: str
    encryption_backend: str = "local"


def get_kms_config() -> KMSConfig:
    """Load configuration for the Key Management System (KMS).

    Expected variables:
    - KEK_ROOT_SECRET — root secret used to derive the Key Encryption Key (KEK)
    - ENCRYPTION_BACKEND — optional 'local' (default) or 'kms'
    """
    return KMSConfig(
        kek_root_secret=_require_env("KEK_ROOT_SECRET"),
        encryption_backend=os.getenv("ENCRYPTION_BACKEND", "local"),
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