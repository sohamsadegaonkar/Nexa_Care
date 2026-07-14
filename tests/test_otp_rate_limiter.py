from __future__ import annotations

import asyncio
import hashlib
import threading

import pytest

from app.core.rate_limiter import (
    OtpRateLimitBackendUnavailable,
    OtpRateLimitExceeded,
    OtpRedisRateLimiter,
)

HMAC_SECRET = "otp-rate-limit-test-secret-with-32-plus-bytes"


class AtomicFakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expires_at: dict[str, int] = {}
        self.now = 0
        self.keys_seen: list[str] = []
        self.lock = threading.Lock()

    def eval(self, _script: str, _number_of_keys: int, key: str, ttl: int) -> int:
        with self.lock:
            if self.expires_at.get(key, self.now + 1) <= self.now:
                self.counts.pop(key, None)
                self.expires_at.pop(key, None)
            count = self.counts.get(key, 0) + 1
            self.counts[key] = count
            if count == 1:
                self.expires_at[key] = self.now + int(ttl)
            self.keys_seen.append(key)
            return count

    def advance(self, seconds: int) -> None:
        self.now += seconds


@pytest.mark.asyncio
async def test_same_ip_is_limited_independently_of_phone() -> None:
    redis = AtomicFakeRedis()
    limiter = OtpRedisRateLimiter(
        redis_client=redis, hmac_secret=HMAC_SECRET, send_ip_limit=2, send_phone_limit=100
    )
    await limiter.check(action="send", ip="10.0.0.1", normalized_phone="+918000000001")
    await limiter.check(action="send", ip="10.0.0.1", normalized_phone="+918000000002")
    with pytest.raises(OtpRateLimitExceeded):
        await limiter.check(action="send", ip="10.0.0.1", normalized_phone="+918000000003")


@pytest.mark.asyncio
async def test_same_phone_across_different_ips_is_limited() -> None:
    redis = AtomicFakeRedis()
    limiter = OtpRedisRateLimiter(
        redis_client=redis, hmac_secret=HMAC_SECRET, send_ip_limit=100, send_phone_limit=2
    )
    phone = "+918975073895"
    await limiter.check(action="send", ip="10.0.0.1", normalized_phone=phone)
    await limiter.check(action="send", ip="10.0.0.2", normalized_phone=phone)
    with pytest.raises(OtpRateLimitExceeded):
        await limiter.check(action="send", ip="10.0.0.3", normalized_phone=phone)


@pytest.mark.asyncio
async def test_keys_have_required_dimensions_and_no_raw_identifiers() -> None:
    redis = AtomicFakeRedis()
    limiter = OtpRedisRateLimiter(redis_client=redis, hmac_secret=HMAC_SECRET)
    phone = "+918975073895"
    ip = "192.168.29.249"
    await limiter.check(action="send", ip=ip, normalized_phone=phone)
    await limiter.check(action="verify", ip=ip, normalized_phone=phone)
    assert any(key.startswith("otp:send:ip:") for key in redis.keys_seen)
    assert any(key.startswith("otp:send:phone:") for key in redis.keys_seen)
    assert any(key.startswith("otp:verify:ip:") for key in redis.keys_seen)
    assert any(key.startswith("otp:verify:phone:") for key in redis.keys_seen)
    assert all(phone not in key and ip not in key for key in redis.keys_seen)
    plain_phone_digest = hashlib.sha256(phone.encode()).hexdigest()
    assert all(plain_phone_digest not in key for key in redis.keys_seen)


def test_hmac_secret_changes_identifier() -> None:
    first = OtpRedisRateLimiter(hmac_secret=HMAC_SECRET)
    second = OtpRedisRateLimiter(hmac_secret="another-independent-secret-with-32-plus-bytes")
    assert first.redis_key("send", "phone", "+918975073895") != second.redis_key(
        "send", "phone", "+918975073895"
    )


@pytest.mark.asyncio
async def test_expiry_resets_limit() -> None:
    redis = AtomicFakeRedis()
    limiter = OtpRedisRateLimiter(
        redis_client=redis,
        hmac_secret=HMAC_SECRET,
        send_ip_limit=1,
        send_phone_limit=1,
        window_seconds=10,
    )
    await limiter.check(action="send", ip="10.0.0.1", normalized_phone="+918975073895")
    with pytest.raises(OtpRateLimitExceeded):
        await limiter.check(action="send", ip="10.0.0.1", normalized_phone="+918975073895")
    redis.advance(10)
    await limiter.check(action="send", ip="10.0.0.1", normalized_phone="+918975073895")


@pytest.mark.asyncio
async def test_concurrent_requests_cannot_bypass_atomic_limit() -> None:
    redis = AtomicFakeRedis()
    limiter = OtpRedisRateLimiter(
        redis_client=redis, hmac_secret=HMAC_SECRET, verify_ip_limit=5, verify_phone_limit=100
    )

    async def attempt(index: int) -> bool:
        try:
            await limiter.check(
                action="verify",
                ip="10.0.0.1",
                normalized_phone=f"+91800000{index:04d}",
            )
            return True
        except OtpRateLimitExceeded:
            return False

    results = await asyncio.gather(*(attempt(index) for index in range(20)))
    assert sum(results) == 5


@pytest.mark.asyncio
async def test_redis_failure_is_fail_closed() -> None:
    class BrokenRedis:
        def eval(self, *_args):
            raise ConnectionError("redis unavailable")

    limiter = OtpRedisRateLimiter(redis_client=BrokenRedis(), hmac_secret=HMAC_SECRET)
    with pytest.raises(OtpRateLimitBackendUnavailable):
        await limiter.check(
            action="send", ip="10.0.0.1", normalized_phone="+918975073895"
        )
