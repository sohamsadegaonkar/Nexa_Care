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
class HandshakeSecurityConfig:
    pepper: str


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


def get_handshake_security_config() -> HandshakeSecurityConfig:
    """Load the server-side pepper used by app/services/crypto_engine.py to
    derive a unique per-record salt for every biometric handshake.

    This replaces the single hardcoded, globally-shared salt that used to
    live in app/core/handshake.py (_STATIC_SALT) -- that constant meant one
    precomputed table could target every record at once. Combining this
    pepper with each record's nfc_uid gives every record its own salt,
    without anyone needing to provision/store a salt per record up front.

    Expected variable:
    - HANDSHAKE_PEPPER_SECRET (treat this with the same care as a database
      credential -- generate it with something like `openssl rand -hex 32`,
      put it in your local .env, and rotate it like any other secret.
      Rotating it invalidates all sessions issued before the rotation.)
    """

    return HandshakeSecurityConfig(pepper=_require_env("HANDSHAKE_PEPPER_SECRET"))