"""Tests for the Redis-backed rate limiter using an in-memory fake client."""

from __future__ import annotations

import asyncio
import unittest

from app.core.rate_limiter import RateLimiter, client_ip_key


class FakeAsyncRedisPipeline:
    def __init__(self, client: "FakeAsyncRedisClient"):
        self._client = client
        self._commands: list = []

    def incr(self, key: str):
        self._commands.append(("incr", key))
        return self

    def expire(self, key: str, seconds: int, nx: bool = True):
        self._commands.append(("expire", key, seconds, nx))
        return self

    async def execute(self) -> list:
        results = []
        for cmd in self._commands:
            if cmd[0] == "incr":
                key = cmd[1]
                self._client._counters[key] = self._client._counters.get(key, 0) + 1
                results.append(self._client._counters[key])
            elif cmd[0] == "expire":
                # Fake TTL bookkeeping is not needed for these unit tests.
                results.append(1)
        self._commands.clear()
        return results


class FakeAsyncRedisClient:
    def __init__(self):
        self._counters: dict[str, int] = {}

    def pipeline(self):
        return FakeAsyncRedisPipeline(self)

    async def eval(self, _script, _numkeys, key, window_seconds):
        self._counters[key] = self._counters.get(key, 0) + 1
        return [self._counters[key], int(window_seconds)]

    async def close(self) -> None:
        pass


class FakeRequest:
    def __init__(self, host: str, forwarded: str | None = None):
        self.client = type("Client", (), {"host": host})
        self.headers = {}
        if forwarded:
            self.headers["x-forwarded-for"] = forwarded


class TestRateLimiter(unittest.TestCase):
    def test_allows_requests_up_to_max(self):
        fake = FakeAsyncRedisClient()
        limiter = RateLimiter(
            max_requests=3,
            window_seconds=60,
            key_func=lambda r: "key",
            redis_client=fake,
        )
        self.assertTrue(asyncio.run(limiter.is_allowed("key")))
        self.assertTrue(asyncio.run(limiter.is_allowed("key")))
        self.assertTrue(asyncio.run(limiter.is_allowed("key")))
        self.assertFalse(asyncio.run(limiter.is_allowed("key")))

    def test_separate_keys_do_not_share_counters(self):
        fake = FakeAsyncRedisClient()
        limiter = RateLimiter(
            max_requests=1,
            window_seconds=60,
            key_func=lambda r: "ignored",
            redis_client=fake,
        )
        self.assertTrue(asyncio.run(limiter.is_allowed("key-a")))
        self.assertTrue(asyncio.run(limiter.is_allowed("key-b")))

    def test_client_ip_key_ignores_forwarded_header_from_untrusted_peer(self):
        request = FakeRequest(host="10.0.0.1", forwarded="203.0.113.5, 10.0.0.1")
        self.assertEqual(client_ip_key(request), "10.0.0.1")

    def test_client_ip_key_falls_back_to_client_host(self):
        request = FakeRequest(host="10.0.0.1")
        self.assertEqual(client_ip_key(request), "10.0.0.1")


if __name__ == "__main__":
    unittest.main()
