"""Application-level encryption helpers for sensitive data at rest.

Uses Fernet (AES-128-CBC + HMAC) from the cryptography library. Keys are
loaded from environment variables and must be 32 bytes, base64-encoded.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("nexa_logger")


class EncryptionError(RuntimeError):
    """Raised when encryption/decryption fails or a key is misconfigured."""


def _load_key(env_name: str) -> bytes:
    """Load a base64-encoded Fernet key from the environment."""

    value = os.getenv(env_name)
    if not value:
        raise EncryptionError(f"Missing required encryption key: {env_name}")
    # Fernet keys are 32 bytes raw, base64-encoded to 44 characters.
    try:
        decoded = base64.urlsafe_b64decode(value)
    except Exception as exc:
        raise EncryptionError(f"{env_name} is not valid base64") from exc
    if len(decoded) != 32:
        raise EncryptionError(
            f"{env_name} must decode to 32 bytes (got {len(decoded)})"
        )
    return value.encode("utf-8")


@lru_cache(maxsize=4)
def _fernet(key_env: str) -> Fernet:
    return Fernet(_load_key(key_env))


def encrypt_mfa_secret(plaintext: str) -> str:
    """Encrypt a TOTP shared secret for storage in provider_credential."""

    if not plaintext:
        raise EncryptionError("Refusing to encrypt an empty MFA secret")
    return (
        _fernet("MFA_ENCRYPTION_KEY").encrypt(plaintext.encode("utf-8")).decode("utf-8")
    )


def decrypt_mfa_secret(ciphertext: str | None) -> str | None:
    """Decrypt a stored TOTP shared secret. Returns None for empty input."""

    if not ciphertext:
        return None
    try:
        return (
            _fernet("MFA_ENCRYPTION_KEY")
            .decrypt(ciphertext.encode("utf-8"))
            .decode("utf-8")
        )
    except InvalidToken as exc:
        raise EncryptionError(
            "MFA secret decryption failed: invalid token or key mismatch"
        ) from exc


def encrypt_pii_field(plaintext: str | None) -> str | None:
    """Encrypt a single PII field for the PII vault shard."""

    if plaintext is None:
        return None
    return (
        _fernet("PII_ENCRYPTION_KEY").encrypt(plaintext.encode("utf-8")).decode("utf-8")
    )


def decrypt_pii_field(ciphertext: str | None) -> str | None:
    """Decrypt a single PII field from the PII vault shard."""

    if ciphertext is None:
        return None
    try:
        return (
            _fernet("PII_ENCRYPTION_KEY")
            .decrypt(ciphertext.encode("utf-8"))
            .decode("utf-8")
        )
    except InvalidToken as exc:
        raise EncryptionError(
            "PII decryption failed: invalid token or key mismatch"
        ) from exc


# ── Session binding hashes ───────────────────────────────────────────────
# These are one-way comparisons for token binding, not secrets. Production
# may prefer HMAC with a server-side pepper; SHA-256 is sufficient to
# detect UA rotation and basic IP rotation.


def hash_user_agent(user_agent: str | None) -> str:
    """Hash a User-Agent string for session binding."""
    if not user_agent:
        return ""
    return hashlib.sha256(user_agent.encode("utf-8")).hexdigest()[:32]


def hash_client_ip(ip: str | None) -> str:
    """Hash a client IP for session binding."""
    if not ip:
        return ""
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:32]
