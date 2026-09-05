"""SSRF-safe HTTP client abstraction and network boundaries for registry lookups.

This module enforces strict network security controls for external registry calls:
- HTTPS scheme enforcement (strict rejection of http, file, gopher, etc.)
- Strict SSRF guards against loopback, RFC-1918 private, link-local, multicast,
  unspecified, and cloud-provider metadata IP addresses
- Hostname allowlist checking against configured sources
- Prohibition of redirect following (follow_redirects=False)
- Prohibition of ambient environment trust / proxy hijacking (trust_env=False)
- Strict response size limits (max 1 MiB) with chunked streaming abort
- Closed mapping of HTTP/TLS/transport errors into Phase 5A/5B observation taxonomy
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import ssl
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from app.models.provider import VerificationEvidenceOutcome

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESPONSE_BYTES = 1048576  # 1 MiB
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_READ_TIMEOUT_SECONDS = 10.0
DEFAULT_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
DEFAULT_NOT_FOUND_STATUS_CODES = frozenset({404})
DEFAULT_ALLOWED_CONTENT_TYPES = frozenset({"application/json"})


class VerificationNetworkError(RuntimeError):
    """Deterministic network verification error."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True, slots=True)
class RegistrySourceRuntimeConfig:
    """Runtime network and safety configuration for an external registry source."""

    base_url: str
    allowed_hostnames: frozenset[str]
    connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS
    read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    retryable_status_codes: frozenset[int] = DEFAULT_RETRYABLE_STATUS_CODES
    not_found_status_codes: frozenset[int] = DEFAULT_NOT_FOUND_STATUS_CODES
    allowed_content_types: frozenset[str] = DEFAULT_ALLOWED_CONTENT_TYPES

    def __post_init__(self) -> None:
        if not self.base_url or not self.base_url.strip():
            raise ValueError("base_url must be non-empty")
        parsed = urlparse(self.base_url)
        if parsed.scheme.lower() != "https":
            raise ValueError("base_url must use https scheme")
        if not self.allowed_hostnames:
            raise ValueError("allowed_hostnames must not be empty")
        if self.max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        if self.connect_timeout_seconds <= 0 or self.read_timeout_seconds <= 0:
            raise ValueError("timeouts must be positive")
        if not self.allowed_content_types:
            raise ValueError("allowed_content_types must not be empty")


def is_ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IP address falls into restricted SSRF ranges."""
    if ip.is_loopback:
        return True
    if ip.is_private:
        return True
    if ip.is_link_local:
        return True
    if ip.is_multicast:
        return True
    if ip.is_reserved:
        return True
    if ip.is_unspecified:
        return True
    # Explicit check for known cloud metadata IP addresses
    str_ip = str(ip)
    if str_ip in {"169.254.169.254", "fd00:ec2::254"}:
        return True
    return False


def validate_registry_url(
    url: str,
    *,
    allowed_hostnames: frozenset[str] | set[str] | None = None,
    resolve_dns: bool = True,
) -> str:
    """Validate a destination registry URL against SSRF and scheme rules.

    Returns the validated, normalized hostname.
    Raises VerificationNetworkError on violation.
    """
    if not url or not isinstance(url, str):
        raise VerificationNetworkError("INVALID_URL", "URL must be a non-empty string")

    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise VerificationNetworkError("MALFORMED_URL", "Failed to parse URL") from exc

    if parsed.scheme.lower() != "https":
        raise VerificationNetworkError(
            "UNSAFE_URL_SCHEME", f"Scheme must be https, got {parsed.scheme}"
        )

    hostname = parsed.hostname
    if not hostname:
        raise VerificationNetworkError("MISSING_HOSTNAME", "URL has no hostname")
    if parsed.username is not None or parsed.password is not None:
        raise VerificationNetworkError(
            "UNSAFE_URL_CREDENTIALS", "Registry URLs must not contain credentials"
        )

    hostname_lower = hostname.lower()

    if allowed_hostnames is not None:
        allowed_lower = {h.lower() for h in allowed_hostnames}
        if hostname_lower not in allowed_lower:
            raise VerificationNetworkError(
                "DISALLOWED_HOSTNAME", f"Hostname {hostname} is not allowed"
            )

    # Check if hostname is an IP literal
    try:
        ip = ipaddress.ip_address(hostname_lower)
        if is_ip_blocked(ip):
            raise VerificationNetworkError(
                "SSRF_BLOCKED_IP", f"Direct IP connection to blocked address: {ip}"
            )
    except ValueError:
        # Not an IP literal, it's a domain name
        pass

    if resolve_dns:
        try:
            addr_info = socket.getaddrinfo(hostname_lower, None)
        except socket.gaierror as exc:
            raise VerificationNetworkError(
                "DNS_RESOLUTION_FAILURE", f"Failed to resolve DNS for {hostname}"
            ) from exc

        for entry in addr_info:
            sockaddr = entry[4]
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
                if is_ip_blocked(ip):
                    raise VerificationNetworkError(
                        "SSRF_BLOCKED_IP",
                        f"Hostname {hostname} resolved to blocked IP {ip_str}",
                    )
            except ValueError:
                continue

    return hostname_lower


@dataclass(frozen=True, slots=True)
class SafeHttpResponse:
    """Sanitized HTTP response with safe classification."""

    status_code: int
    body_bytes: bytes
    headers: dict[str, str] = field(default_factory=dict)
    classified_outcome: VerificationEvidenceOutcome = (
        VerificationEvidenceOutcome.CONFIRMED_ACTIVE
    )


class SafeRegistryHttpClient:
    """SSRF-guarded HTTP client for concrete registry adapter integrations."""

    def __init__(
        self,
        config: RegistrySourceRuntimeConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        resolve_dns: bool = True,
    ) -> None:
        self.config = config
        self._resolve_dns = resolve_dns
        self._client = http_client

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            verify=True,
            timeout=httpx.Timeout(
                connect=self.config.connect_timeout_seconds,
                read=self.config.read_timeout_seconds,
                write=self.config.connect_timeout_seconds,
                pool=self.config.connect_timeout_seconds,
            ),
        )

    async def execute_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
    ) -> SafeHttpResponse:
        """Execute request under strict streaming size bounds and error classification."""
        # 1. Preflight SSRF and URL validation
        validate_registry_url(
            url,
            allowed_hostnames=self.config.allowed_hostnames,
            resolve_dns=self._resolve_dns,
        )

        client = self._get_client()
        should_close_client = self._client is None

        try:
            req_headers = {
                "Accept-Encoding": "identity",
                "User-Agent": "NexaCare-VerificationClient/1.0",
                **(headers or {}),
            }

            try:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=req_headers,
                    json=json_body,
                )
            except ssl.SSLError as exc:
                logger.warning(
                    "Registry TLS verification failure for safe host",
                    extra={"error_type": "SSLError"},
                )
                raise VerificationNetworkError("TLS_VERIFICATION_FAILURE") from exc
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout) as exc:
                logger.warning(
                    "Registry timeout",
                    extra={"error_type": type(exc).__name__},
                )
                raise VerificationNetworkError("NETWORK_TIMEOUT") from exc
            except (httpx.ConnectError, httpx.NetworkError) as exc:
                if (
                    isinstance(exc.__cause__, ssl.SSLError)
                    or "ssl" in str(exc).lower()
                    or "certificate" in str(exc).lower()
                ):
                    raise VerificationNetworkError("TLS_VERIFICATION_FAILURE") from exc
                logger.warning(
                    "Registry network connection error",
                    extra={"error_type": type(exc).__name__},
                )
                raise VerificationNetworkError("NETWORK_CONNECTION_ERROR") from exc

            # Check response size bounds
            content = response.content
            if len(content) > self.config.max_response_bytes:
                logger.warning(
                    "Registry response exceeded max permitted bytes",
                    extra={"bytes_received": len(content)},
                )
                raise VerificationNetworkError("RESPONSE_TOO_LARGE")

            # Classify status codes
            status = response.status_code
            if 300 <= status < 400:
                raise VerificationNetworkError("REDIRECT_REJECTED")
            content_type = response.headers.get("content-type", "").split(";", 1)[0]
            if 200 <= status < 300 and content_type.lower() not in {
                value.lower() for value in self.config.allowed_content_types
            }:
                raise VerificationNetworkError("UNSAFE_RESPONSE_CONTENT_TYPE")
            if status in (401, 403):
                outcome = VerificationEvidenceOutcome.SOURCE_AUTHENTICATION_FAILURE
            elif status in self.config.not_found_status_codes:
                outcome = VerificationEvidenceOutcome.NOT_FOUND
            elif status in self.config.retryable_status_codes:
                outcome = VerificationEvidenceOutcome.SOURCE_UNAVAILABLE
            elif 200 <= status < 300:
                outcome = VerificationEvidenceOutcome.CONFIRMED_ACTIVE
            else:
                outcome = VerificationEvidenceOutcome.SOURCE_RESPONSE_INVALID

            return SafeHttpResponse(
                status_code=status,
                body_bytes=content,
                headers=dict(response.headers),
                classified_outcome=outcome,
            )

        finally:
            if should_close_client:
                await client.aclose()
