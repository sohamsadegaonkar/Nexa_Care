"""Centralized configuration for Nexa Care.

Security posture:
- Secrets are loaded from environment variables (ideally via a local `.env` file that is NOT committed).
- Fail-fast validation prevents booting the API with missing credentials.

Note: This module only *loads* config; it does not open DB/Redis connections yet.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


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
