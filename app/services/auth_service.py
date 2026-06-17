import json
from typing import Dict, Any, Optional

from app.core.redis import get_redis_client
from fastapi.concurrency import run_in_threadpool # <--- 1. Import threadpool

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
        
        # [FINDING #4 FIX]: Run Redis synchronous network I/O in a background thread
        cached_session = await run_in_threadpool(redis.get, clean_token)

        if hasattr(cached_session, "__await__"):
            cached_session = await cached_session

        if not cached_session:
            return None

        if isinstance(cached_session, bytes):
            cached_session = cached_session.decode("utf-8")

        return json.loads(cached_session)

    except Exception:
        return None