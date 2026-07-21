"""Upstash Redis utilities for Nexa Care.

This module centralizes Redis connectivity and implements the consent-token storage
used by the Zero-Trust Retrieval Layer.

Uses redis-py with the UPSTASH_REDIS_URL from app.core.config.

Fixes applied in this revision
-------------------------------
SCOPE-AWARE CONSENT TOKENS — the old issue_token()/validate_token() pair
stored a bare string (token -> masked_internal_id), which meant a consent
token was an all-or-nothing key: whoever held it could reach every shard
exposed behind it. That's what let GET /view-record return PII and
clinical data from a single token in a single response, contradicting
the vertical-sharding architecture even though the PII came back redacted.

issue_consent_token() / resolve_consent_token() / revoke_consent_token()
replace that with a JSON payload carrying an explicit `scope`
("clinical" or "full"). "clinical" is the data-minimizing default --
/request-consent issues it unless the caller explicitly asks for
scope="full". app/api/routes.py's two split endpoints
(GET /view-record/clinical, GET /view-record/pii) each check the scope
of the token presented before reading their respective shard.

issue_token()/validate_token()/create_access_token()/get_id_from_token()
are kept as thin backward-compatible wrappers around the new functions
(defaulting to scope="full" so existing callers that expect "a token that
can see everything" keep working) but new code should call the scoped
functions directly.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from uuid import uuid4

import redis
import redis.asyncio as redis_async

from app.core.config import get_redis_config

logger = logging.getLogger("nexa_logger")

# Default TTL for consent tokens (30 minutes)
CONSENT_TOKEN_TTL_SECONDS = 30 * 60

# Valid consent scopes. "clinical" grants only the de-identified
# clinical shard; "full" additionally grants the (redacted) PII shard.
_VALID_CONSENT_SCOPES = {"clinical", "full"}
DEFAULT_CONSENT_SCOPE = "clinical"


# [FINDING #11 FIX]: Cache the Redis client to prevent connection churn.
# This ensures only one connection pool exists per worker process.
@lru_cache()
def get_redis_client() -> redis.Redis:
    """Create a Redis client from UPSTASH_REDIS_URL.

    Upstash URLs are typically rediss:// (TLS). redis-py will handle this.
    """

    cfg = get_redis_config()
    return redis.from_url(cfg.url, decode_responses=True)


@lru_cache()
def get_async_redis_client() -> redis_async.Redis:
    """Unambiguous asyncio client for async request handlers."""
    cfg = get_redis_config()
    return redis_async.from_url(cfg.url, decode_responses=True)


def ping_redis() -> bool:
    """Lightweight Redis health check."""

    client = get_redis_client()
    return bool(client.ping())


# ─────────────────────────────────────────────────────────────────────────
# Scope-aware consent tokens
# ─────────────────────────────────────────────────────────────────────────


def issue_consent_token(
    masked_internal_id: str,
    scope: str = DEFAULT_CONSENT_SCOPE,
    ttl_seconds: int = CONSENT_TOKEN_TTL_SECONDS,
) -> str:
    """Issue a scope-bound consent/access token for a masked internal id.

    Stores: token -> {"masked_internal_id": ..., "scope": ...}, with TTL.

    Args:
        masked_internal_id: UUID string that links PII vault and clinical shard.
        scope: "clinical" (default) or "full". See module docstring.
        ttl_seconds: Expiration in seconds. Defaults to 30 minutes.

    Returns:
        token (UUID string)

    Raises:
        ValueError: if `scope` isn't one of _VALID_CONSENT_SCOPES. Fail
        loudly here rather than silently downgrading to a default -- a
        caller that mistyped "Full" should get an error, not a
        quietly-narrower grant than they asked for.
    """
    if scope not in _VALID_CONSENT_SCOPES:
        raise ValueError(
            f"Invalid consent scope {scope!r}; expected one of {sorted(_VALID_CONSENT_SCOPES)}"
        )

    token = str(uuid4())
    client = get_redis_client()
    payload = json.dumps({"masked_internal_id": masked_internal_id, "scope": scope})
    client.set(name=token, value=payload, ex=int(ttl_seconds))
    return token


def resolve_consent_token(token: str | None) -> dict | None:
    """Resolve a consent token to its grant.

    Returns:
        {"masked_internal_id": str, "scope": str} if the token is valid,
        else None. A token written before this fix shipped (or by any
        other producer of a bare string) is rejected, not guessed at --
        fail closed rather than assuming a legacy token meant "full".
    """
    if not token:
        return None

    client = get_redis_client()
    value = client.get(token)
    if not value:
        return None

    if isinstance(value, bytes):
        value = value.decode("utf-8")

    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(parsed, dict):
        return None

    masked_internal_id = parsed.get("masked_internal_id")
    if not masked_internal_id:
        return None

    scope = parsed.get("scope")
    if scope not in _VALID_CONSENT_SCOPES:
        return None

    return {"masked_internal_id": masked_internal_id, "scope": scope}


def revoke_consent_token(token: str | None) -> None:
    """Best-effort revoke. Used when a token was minted but we couldn't
    prove (via the audit ledger) that its issuance was logged -- an
    unauditable grant of access must not be left valid. Deliberately
    swallows Redis errors: this is already running on a failure path
    (see app/api/routes.py's request_consent), and a second exception
    here must not replace the original 503 being raised."""
    if not token:
        return
    try:
        client = get_redis_client()
        client.delete(token)
    except Exception as exc:
        logger.critical(
            "Consent token revocation failed on an audit failure path",
            extra={"error_type": type(exc).__name__},
        )


# ─────────────────────────────────────────────────────────────────────────
# Backwards-compatible wrappers (kept for existing imports)
# ─────────────────────────────────────────────────────────────────────────
# These default to scope="full" so any pre-existing caller that expects
# "a token that can see everything" keeps working unchanged. New code
# should call the scoped functions above directly instead.


def issue_token(
    masked_internal_id: str, ttl_seconds: int = CONSENT_TOKEN_TTL_SECONDS
) -> str:
    return issue_consent_token(
        masked_internal_id=masked_internal_id, scope="full", ttl_seconds=ttl_seconds
    )


def validate_token(token: str) -> str | None:
    resolved = resolve_consent_token(token)
    return resolved["masked_internal_id"] if resolved else None


def create_access_token(masked_id: str) -> str:
    return issue_token(
        masked_internal_id=masked_id, ttl_seconds=CONSENT_TOKEN_TTL_SECONDS
    )


def get_id_from_token(token: str) -> str | None:
    return validate_token(token)
