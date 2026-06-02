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
