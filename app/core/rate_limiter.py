"""Redis-backed fixed-window rate limiter for FastAPI routes.

Uses a single Redis counter per key with a TTL equal to the window. This
is shared across all workers, so the limit is global, not per-process.

On Redis failure we fail OPEN and log a warning. This prevents a Redis
outage from becoming a total login lockout, but it does remove rate
limiting during that window. Accept that trade-off explicitly, or
replace with a fail-closed limiter if your threat model demands it.
"""

import logging
import asyncio
import hashlib
import hmac
import inspect
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request, status

from app.core.config import get_redis_config
from app.core.client_ip import resolve_client_ip

logger = logging.getLogger("nexa_logger")


class OtpRateLimitBackendUnavailable(RuntimeError):
    """Raised when OTP throttling cannot be enforced safely."""


class OtpRateLimitExceeded(RuntimeError):
    """Raised when one OTP throttle bucket exceeds its limit."""


_ATOMIC_FIXED_WINDOW_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
    ttl = tonumber(ARGV[1])
end
return {count, ttl}
"""


async def atomic_fixed_window(redis_client: Any, key: str, window_seconds: int) -> tuple[int, int]:
    """Atomically increment a counter, guarantee a TTL, and return both."""
    result = await redis_client.eval(_ATOMIC_FIXED_WINDOW_SCRIPT, 1, key, int(window_seconds))
    return int(result[0]), int(result[1])


class OtpRedisRateLimiter:
    """Atomic, fail-closed Redis limiter for patient OTP send and verify.

    Identifiers are pseudonymized with an independent HMAC-SHA-256 secret
    before becoming Redis keys, so raw values and plain candidate digests are
    not stored in Redis key names.
    """

    _SCRIPT = """
    local count = redis.call('INCR', KEYS[1])
    if count == 1 then
        redis.call('EXPIRE', KEYS[1], ARGV[1])
    end
    return count
    """

    def __init__(
        self,
        *,
        send_ip_limit: int = 5,
        send_phone_limit: int = 3,
        verify_ip_limit: int = 5,
        verify_phone_limit: int = 5,
        window_seconds: int = 300,
        redis_client: Any | None = None,
        hmac_secret: str | None = None,
    ) -> None:
        self.limits = {
            ("send", "ip"): send_ip_limit,
            ("send", "phone"): send_phone_limit,
            ("verify", "ip"): verify_ip_limit,
            ("verify", "phone"): verify_phone_limit,
        }
        self.window_seconds = window_seconds
        self._redis_client = redis_client
        self._hmac_secret = hmac_secret

    def redis_key(self, action: str, dimension: str, identifier: str) -> str:
        secret = self._hmac_secret
        if secret is None:
            from app.core.config import get_otp_rate_limit_config

            secret = get_otp_rate_limit_config().hmac_secret
        digest = hmac.new(
            secret.encode("utf-8"),
            identifier.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"otp:{action}:{dimension}:{digest}"

    async def _increment(self, key: str) -> int:
        try:
            redis_client = self._redis_client
            if redis_client is None:
                from app.core.redis import get_redis_client

                redis_client = get_redis_client()
            result = await asyncio.to_thread(
                redis_client.eval,
                self._SCRIPT,
                1,
                key,
                self.window_seconds,
            )
            if inspect.isawaitable(result):
                result = await result
            return int(result)
        except Exception as exc:
            logger.error("OTP rate limiter Redis failure; denying request: %s", type(exc).__name__)
            raise OtpRateLimitBackendUnavailable("OTP rate limiting is unavailable") from exc

    async def check(self, *, action: str, ip: str, normalized_phone: str) -> None:
        if action not in {"send", "verify"}:
            raise ValueError("Unsupported OTP rate-limit action")
        for dimension, identifier in (("ip", ip), ("phone", normalized_phone)):
            key = self.redis_key(action, dimension, identifier)
            count = await self._increment(key)
            if count > self.limits[(action, dimension)]:
                raise OtpRateLimitExceeded(f"OTP {action} {dimension} limit exceeded")


def _import_redis() -> Any:
    """Lazy import so tests can patch or skip Redis entirely."""
    import redis.asyncio as redis_async

    return redis_async


def client_ip_key(request: Request) -> str:
    """Bucket by the canonical trusted-proxy-aware client address."""
    return resolve_client_ip(request) or "unknown"


class RateLimiter:
    """Redis fixed-window counter rate limiter.

    Attributes:
        max_requests: maximum allowed requests per window.
        window_seconds: window duration in seconds.
        key_func: function Request -> str used to bucket requests.
        resource_name: short identifier for logs/metrics.
    """

    def __init__(
        self,
        max_requests: int,
        window_seconds: int,
        key_func: Callable[[Request], str],
        resource_name: str = "route",
        redis_client: Any | None = None,
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.key_func = key_func
        self.resource_name = resource_name
        self._prefix = "nexa:rate_limit"
        self._redis_client = redis_client

    def _key(self, identifier: str) -> str:
        return f"{self._prefix}:{self.resource_name}:{identifier}"

    async def is_allowed(self, identifier: str) -> bool:
        redis_async = _import_redis()
        try:
            if self._redis_client is not None:
                redis_client = self._redis_client
            else:
                cfg = get_redis_config()
                redis_client = redis_async.from_url(cfg.url, decode_responses=True)
            key = self._key(identifier)
            count, _ttl = await atomic_fixed_window(redis_client, key, self.window_seconds)
            if self._redis_client is None:
                await redis_client.close()
            return count <= self.max_requests
        except Exception as exc:
            logger.warning(
                f"Rate limiter Redis failure for {self.resource_name}: {exc}. "
                "Allowing request (fail-open)."
            )
            return True

    async def __call__(self, request: Request, provider_id: str | None = None) -> None:
        if provider_id:
            identifier = provider_id
        else:
            identifier = self.key_func(request)

        if not await self.is_allowed(identifier):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
            )


class ConcurrentPushLimiter:
    """Enforces concurrency and rate limits for asynchronous push approvals.

    Rules:
    - 1 pending request per patient.
    - 10 requests per provider per 5 minutes.
    - 5 requests per patient per hour.
    """

    def __init__(self, redis_client: Any | None = None):
        self._redis_client = redis_client

    async def _get_redis(self):
        if self._redis_client:
            return self._redis_client
        redis_async = _import_redis()
        cfg = get_redis_config()
        return redis_async.from_url(cfg.url, decode_responses=True)

    async def check_and_acquire(self, patient_id: str, provider_id: str) -> None:
        """Atomically acquire the per-patient pending push lock and rate-limit.

        The concurrency lock uses Redis ``SET key value NX EX`` so two workers
        cannot both observe an empty lock and then create duplicate pending
        requests. This limiter preserves the route's existing Redis-outage
        behavior: concurrency/rate limiting fail open if Redis is unavailable.
        """
        redis = None
        concurrent_key = f"nexa:push_concurrent:{patient_id}"
        lock_acquired = False
        try:
            redis = await self._get_redis()
            provider_rate_key = f"nexa:push_rate:provider:{provider_id}"
            patient_rate_key = f"nexa:push_rate:patient:{patient_id}"

            lock_result = await redis.set(concurrent_key, "1", nx=True, ex=100)
            if not lock_result:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="A consent request is already pending for this patient",
                )
            lock_acquired = True

            # Provider: 10 per 5 min
            provider_count = await redis.incr(provider_rate_key)
            if provider_count == 1:
                await redis.expire(provider_rate_key, 300)
            if provider_count > 10:
                await redis.delete(concurrent_key)
                lock_acquired = False
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded for provider",
                )

            # Patient: 5 per hour
            patient_count = await redis.incr(patient_rate_key)
            if patient_count == 1:
                await redis.expire(patient_rate_key, 3600)
            if patient_count > 5:
                await redis.delete(concurrent_key)
                lock_acquired = False
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded for patient",
                )

        except HTTPException:
            raise
        except Exception as exc:
            if lock_acquired and redis is not None:
                try:
                    await redis.delete(concurrent_key)
                except Exception as cleanup_exc:
                    logger.error("Concurrent push lock cleanup failed", extra={"error_type": type(cleanup_exc).__name__})
            logger.error("Concurrent push limiter unavailable; failing closed", extra={"error_type": type(exc).__name__})
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Push rate-limit enforcement unavailable",
            ) from exc
        finally:
            if redis is not None and self._redis_client is None:
                await redis.close()

    async def release(self, patient_id: str) -> None:
        """Clear the concurrency lock for a patient."""
        redis = None
        try:
            redis = await self._get_redis()
            await redis.delete(f"nexa:push_concurrent:{patient_id}")
        except Exception as exc:
            logger.warning(
                "ConcurrentPushLimiter release failure",
                extra={"error_type": type(exc).__name__},
            )
        finally:
            if redis is not None and self._redis_client is None:
                await redis.close()
