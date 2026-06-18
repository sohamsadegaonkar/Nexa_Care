import json
from typing import Dict, Any, Optional
from uuid import UUID

from app.core.redis import get_redis_client


async def validate_session_context(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None

    clean_token = token
    if clean_token.startswith("Bearer "):
        clean_token = clean_token.removeprefix("Bearer ").strip()

    if not clean_token:
        return None

    try:
        redis = get_redis_client()
        cached_session = redis.get(clean_token)

        if hasattr(cached_session, "__await__"):
            cached_session = await cached_session

        if not cached_session:
            return None

        if isinstance(cached_session, bytes):
            cached_session = cached_session.decode("utf-8")

        return json.loads(cached_session)

    except Exception:
        return None


def session_authorizes_patient(
    session_context: Optional[Dict[str, Any]], masked_internal_id: str
) -> bool:
    """True only if `session_context` is a non-empty session bound to
    exactly this masked_internal_id.

    Centralizes the scope check used by both GET /api/v1/record/{id} and
    POST /request-consent, so a session minted (via /api/v1/handshake) for
    one patient can never be replayed -- through either route -- against
    another patient. A session created before this check existed (no
    "masked_internal_id" key at all) fails closed rather than matching.

    Pure function, no I/O -- see tests/test_handshake_scoping.py.
    """
    if not session_context:
        return False

    try:
        target = str(UUID(str(masked_internal_id)))
    except (ValueError, AttributeError, TypeError):
        target = masked_internal_id

    return session_context.get("masked_internal_id") == target