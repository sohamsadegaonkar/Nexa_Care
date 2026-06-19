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
    """Load the shared facility-level credential used by
    verify_provider_token (app/core/dependencies.py) to authenticate
    hospital/clinic systems calling provider-only routes (/register,
    /api/v1/enroll-biometric).

    Expected variable:
    - CLINIC_API_KEY

    This is a single shared service-to-service secret, not a per-clinician
    credential and not a patient session token -- it proves "this caller
    is a legitimate facility system," nothing about which individual
    patient is involved. Rotate it the same way you would any other
    service API key; never embed it in any client-side or mobile code
    path a patient could extract.
    """

    return ClinicConfig(api_key=_require_env("CLINIC_API_KEY"))