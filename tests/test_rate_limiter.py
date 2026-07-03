"""Tests for the in-memory rate limiter."""

from __future__ import annotations

import unittest

from app.core.rate_limiter import RateLimiter, client_ip_key


class FakeRequest:
    def __init__(self, host: str, forwarded: str | None = None):
        self.client = type("Client", (), {"host": host})
        self.headers = {}
        if forwarded:
            self.headers["x-forwarded-for"] = forwarded


class TestRateLimiter(unittest.TestCase):
    def test_allows_requests_up_to_max(self):
        limiter = RateLimiter(max_requests=3, window_seconds=60, key_func=lambda r: "key")
        self.assertTrue(limiter.is_allowed("key"))
        self.assertTrue(limiter.is_allowed("key"))
        self.assertTrue(limiter.is_allowed("key"))
        self.assertFalse(limiter.is_allowed("key"))

    def test_bypasses_window_after_timeout(self):
        limiter = RateLimiter(max_requests=1, window_seconds=0, key_func=lambda r: "key")
        self.assertTrue(limiter.is_allowed("key"))
        # Window is 0 seconds, so next call resets the bucket.
        self.assertTrue(limiter.is_allowed("key"))

    def test_client_ip_key_prefers_forwarded_header(self):
        request = FakeRequest(host="10.0.0.1", forwarded="203.0.113.5, 10.0.0.1")
        self.assertEqual(client_ip_key(request), "203.0.113.5")

    def test_client_ip_key_falls_back_to_client_host(self):
        request = FakeRequest(host="10.0.0.1")
        self.assertEqual(client_ip_key(request), "10.0.0.1")


if __name__ == "__main__":
    unittest.main()
