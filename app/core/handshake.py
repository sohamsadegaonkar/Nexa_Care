"""DEPRECATED -- superseded by app/services/crypto_engine.py.

This module was a second, parallel biometric key-derivation implementation
that was never actually wired into any route (app/api/routes.py imported
generate_soham_alpha/create_secure_session but never called either of
them). app/services/crypto_engine.py is the canonical implementation: it's
the one the live /api/v1/handshake route calls, it now binds sessions to a
specific masked_internal_id, and it now derives its key with a per-record
salt instead of the single hardcoded one below.

Nothing should import from this module going forward. Do not resurrect it
without first replacing _STATIC_SALT with a real per-record salt -- it was
already flagged as MVP-only, shared across every record, when it was
written.
"""

from __future__ import annotations

import hashlib
import os
import uuid

from app.core.redis import get_redis_client

# Static salt for MVP only. Replace with per-user/per-device salt + secret pepper in production.
_STATIC_SALT = b"nexa-care-layer2-static-salt"


def generate_soham_alpha(nfc_uid: str, bio_seed: str) -> str:
    """Derive a deterministic key from NFC UID + biometric seed.

    Uses PBKDF2-HMAC-SHA256 to slow down brute-force attempts.
    """

    # Normalize inputs
    uid = (nfc_uid or "").strip().encode("utf-8")
    bio = (bio_seed or "").strip().encode("utf-8")

    # "Collide" by concatenation (order matters) and key-stretch
    material = uid + b":" + bio

    dk = hashlib.pbkdf2_hmac(
        "sha256",
        material,
        _STATIC_SALT,
        100_000,
        dklen=32,
    )
    return dk.hex()


def create_secure_session(alpha: str) -> str:
    """Store Soham Alpha in Redis under a random session token with TTL=30min."""

    session_token = str(uuid.uuid4())
    client = get_redis_client()

    # TTL 30 minutes
    client.set(name=session_token, value=alpha, ex=1800)
    return session_token