"""Dedicated cryptographic configuration for patient discovery search indexes.

This module deliberately does not reuse OTP, provider-registration, contact
assurance, or other application secrets.  Searchable low-entropy identifiers
need their own independently generated HMAC key material.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


class PatientDiscoveryIndexConfigError(RuntimeError):
    """Raised when the discovery index keyring is unavailable or malformed."""


@dataclass(frozen=True)
class PatientDiscoveryIndexConfig:
    active_key_version: int
    hmac_keys: Mapping[int, str]


_ENV_KEYS = "PATIENT_DISCOVERY_INDEX_HMAC_KEYS_JSON"
_ENV_ACTIVE = "PATIENT_DISCOVERY_INDEX_ACTIVE_KEY_VERSION"
_MIN_SECRET_BYTES = 32
_MAX_KEY_VERSIONS = 8


def get_patient_discovery_index_config() -> PatientDiscoveryIndexConfig:
    """Load and validate the versioned discovery-index HMAC keyring.

    Expected example::

        PATIENT_DISCOVERY_INDEX_HMAC_KEYS_JSON='{"1":"<random-secret>"}'
        PATIENT_DISCOVERY_INDEX_ACTIVE_KEY_VERSION=1

    The keyring permits safe read compatibility during a controlled key
    rotation.  New writes always use ``active_key_version``.  Old versions may
    remain configured only while their rows are being deliberately reindexed.
    """

    raw_keys = os.getenv(_ENV_KEYS, "").strip()
    raw_active = os.getenv(_ENV_ACTIVE, "").strip()
    if not raw_keys:
        raise PatientDiscoveryIndexConfigError(f"{_ENV_KEYS}: required")
    if not raw_active:
        raise PatientDiscoveryIndexConfigError(f"{_ENV_ACTIVE}: required")

    try:
        parsed = json.loads(raw_keys)
    except json.JSONDecodeError as exc:
        raise PatientDiscoveryIndexConfigError(f"{_ENV_KEYS}: invalid JSON") from exc
    if not isinstance(parsed, dict) or not parsed:
        raise PatientDiscoveryIndexConfigError(f"{_ENV_KEYS}: non-empty object required")
    if len(parsed) > _MAX_KEY_VERSIONS:
        raise PatientDiscoveryIndexConfigError(f"{_ENV_KEYS}: too many key versions")

    keys: dict[int, str] = {}
    for raw_version, secret in parsed.items():
        if not isinstance(raw_version, str) or not raw_version.isdecimal():
            raise PatientDiscoveryIndexConfigError(f"{_ENV_KEYS}: positive integer versions required")
        version = int(raw_version)
        if version <= 0:
            raise PatientDiscoveryIndexConfigError(f"{_ENV_KEYS}: positive integer versions required")
        if not isinstance(secret, str) or len(secret.encode("utf-8")) < _MIN_SECRET_BYTES:
            raise PatientDiscoveryIndexConfigError(
                f"{_ENV_KEYS}: every secret must be at least {_MIN_SECRET_BYTES} bytes"
            )
        keys[version] = secret

    if len(set(keys.values())) != len(keys):
        raise PatientDiscoveryIndexConfigError(
            f"{_ENV_KEYS}: each key version requires independent secret material"
        )

    try:
        active = int(raw_active)
    except ValueError as exc:
        raise PatientDiscoveryIndexConfigError(f"{_ENV_ACTIVE}: integer required") from exc
    if active <= 0 or active not in keys:
        raise PatientDiscoveryIndexConfigError(
            f"{_ENV_ACTIVE}: must reference a configured positive key version"
        )

    return PatientDiscoveryIndexConfig(
        active_key_version=active,
        hmac_keys=MappingProxyType(keys),
    )
