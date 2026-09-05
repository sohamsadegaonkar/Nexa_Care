"""Unit tests for SSRF-safe registry network client and URL validation."""

import ipaddress
import ssl
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from app.models.provider import VerificationEvidenceOutcome
from app.services.provider_verification_network import (
    DEFAULT_NOT_FOUND_STATUS_CODES,
    DEFAULT_RETRYABLE_STATUS_CODES,
    RegistrySourceRuntimeConfig,
    SafeRegistryHttpClient,
    VerificationNetworkError,
    classify_network_error,
    is_ip_blocked,
    validate_registry_url,
)


def test_ip_blocking_restricted_ranges() -> None:
    # Loopback
    assert is_ip_blocked(ipaddress.ip_address("127.0.0.1")) is True
    assert is_ip_blocked(ipaddress.ip_address("::1")) is True

    # Private
    assert is_ip_blocked(ipaddress.ip_address("10.0.0.5")) is True
    assert is_ip_blocked(ipaddress.ip_address("172.16.0.1")) is True
    assert is_ip_blocked(ipaddress.ip_address("192.168.1.100")) is True

    # Link-local & cloud metadata
    assert is_ip_blocked(ipaddress.ip_address("169.254.169.254")) is True
    assert is_ip_blocked(ipaddress.ip_address("fe80::1")) is True

    # Multicast & unspecified
    assert is_ip_blocked(ipaddress.ip_address("224.0.0.1")) is True
    assert is_ip_blocked(ipaddress.ip_address("0.0.0.0")) is True

    # Public valid IPs
    assert is_ip_blocked(ipaddress.ip_address("8.8.8.8")) is False
    assert is_ip_blocked(ipaddress.ip_address("1.1.1.1")) is False


def test_validate_registry_url_rejects_non_https() -> None:
    with pytest.raises(VerificationNetworkError) as exc:
        validate_registry_url("http://registry.gov.in/api", resolve_dns=False)
    assert exc.value.code == "UNSAFE_URL_SCHEME"

    with pytest.raises(VerificationNetworkError) as exc:
        validate_registry_url("ftp://registry.gov.in/api", resolve_dns=False)
    assert exc.value.code == "UNSAFE_URL_SCHEME"


def test_validate_registry_url_checks_allowed_hostnames() -> None:
    allowed = frozenset({"registry.gov.in", "nmc.org.in"})

    # Allowed host
    host = validate_registry_url(
        "https://registry.gov.in/lookup",
        allowed_hostnames=allowed,
        resolve_dns=False,
    )
    assert host == "registry.gov.in"

    # Disallowed host
    with pytest.raises(VerificationNetworkError) as exc:
        validate_registry_url(
            "https://malicious.org/lookup",
            allowed_hostnames=allowed,
            resolve_dns=False,
        )
    assert exc.value.code == "DISALLOWED_HOSTNAME"


def test_validate_registry_url_rejects_ssrf_ip_literals() -> None:
    # Loopback IP literal
    with pytest.raises(VerificationNetworkError) as exc:
        validate_registry_url("https://127.0.0.1/api", resolve_dns=False)
    assert exc.value.code == "SSRF_BLOCKED_IP"

    with pytest.raises(VerificationNetworkError) as exc:
        validate_registry_url(
            "https://user:credential@registry.gov.in/api", resolve_dns=False
        )
    assert exc.value.code == "UNSAFE_URL_CREDENTIALS"

    # Metadata IP literal
    with pytest.raises(VerificationNetworkError) as exc:
        validate_registry_url(
            "https://169.254.169.254/latest/meta-data", resolve_dns=False
        )
    assert exc.value.code == "SSRF_BLOCKED_IP"

    # Private IP literal
    with pytest.raises(VerificationNetworkError) as exc:
        validate_registry_url("https://10.1.2.3/api", resolve_dns=False)
    assert exc.value.code == "SSRF_BLOCKED_IP"


def test_validate_registry_url_rejects_dns_resolving_to_private() -> None:
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]
        with pytest.raises(VerificationNetworkError) as exc:
            validate_registry_url("https://spoofed.internal/api", resolve_dns=True)
        assert exc.value.code == "SSRF_BLOCKED_IP"


@pytest.mark.asyncio
async def test_safe_http_client_status_classification() -> None:
    config = RegistrySourceRuntimeConfig(
        base_url="https://registry.gov.in",
        allowed_hostnames=frozenset({"registry.gov.in"}),
    )

    # 1. 200 OK
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            status_code=200,
            content=b'{"status": "ACTIVE"}',
            headers={"Content-Type": "application/json"},
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        safe_client = SafeRegistryHttpClient(
            config, http_client=client, resolve_dns=False
        )
        res = await safe_client.execute_request("GET", "https://registry.gov.in/check")
        assert res.status_code == 200
        assert res.classified_outcome == VerificationEvidenceOutcome.CONFIRMED_ACTIVE

    # 2. 401 Auth Failure
    transport = httpx.MockTransport(
        lambda req: httpx.Response(status_code=401, content=b"Unauthorized")
    )
    async with httpx.AsyncClient(transport=transport) as client:
        safe_client = SafeRegistryHttpClient(
            config, http_client=client, resolve_dns=False
        )
        res = await safe_client.execute_request("GET", "https://registry.gov.in/check")
        assert (
            res.classified_outcome
            == VerificationEvidenceOutcome.SOURCE_AUTHENTICATION_FAILURE
        )

    # 3. 403 Auth Failure
    transport = httpx.MockTransport(
        lambda req: httpx.Response(status_code=403, content=b"Forbidden")
    )
    async with httpx.AsyncClient(transport=transport) as client:
        safe_client = SafeRegistryHttpClient(
            config, http_client=client, resolve_dns=False
        )
        res = await safe_client.execute_request("GET", "https://registry.gov.in/check")
        assert (
            res.classified_outcome
            == VerificationEvidenceOutcome.SOURCE_AUTHENTICATION_FAILURE
        )

    # 4. Default 404 is NOT_FOUND by default? NO! Section 8: default 404 -> SOURCE_RESPONSE_INVALID
    transport = httpx.MockTransport(
        lambda req: httpx.Response(status_code=404, content=b"Not found")
    )
    async with httpx.AsyncClient(transport=transport) as client:
        safe_client = SafeRegistryHttpClient(
            config, http_client=client, resolve_dns=False
        )
        res = await safe_client.execute_request("GET", "https://registry.gov.in/check")
        assert (
            res.classified_outcome
            == VerificationEvidenceOutcome.SOURCE_RESPONSE_INVALID
        )

    # 5. Explicit source opt-in not_found_status_codes={404} -> NOT_FOUND
    opt_in_404_config = RegistrySourceRuntimeConfig(
        base_url="https://registry.gov.in",
        allowed_hostnames=frozenset({"registry.gov.in"}),
        not_found_status_codes=frozenset({404}),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        safe_client = SafeRegistryHttpClient(
            opt_in_404_config, http_client=client, resolve_dns=False
        )
        res = await safe_client.execute_request("GET", "https://registry.gov.in/check")
        assert res.classified_outcome == VerificationEvidenceOutcome.NOT_FOUND

    # 6. Default 429 is retryable by default? NO! Section 7: default 429 -> SOURCE_RESPONSE_INVALID
    transport = httpx.MockTransport(
        lambda req: httpx.Response(status_code=429, content=b"Too Many Requests")
    )
    async with httpx.AsyncClient(transport=transport) as client:
        safe_client = SafeRegistryHttpClient(
            config, http_client=client, resolve_dns=False
        )
        res = await safe_client.execute_request("GET", "https://registry.gov.in/check")
        assert (
            res.classified_outcome
            == VerificationEvidenceOutcome.SOURCE_RESPONSE_INVALID
        )

    # 7. Explicit source opt-in retryable_status_codes={..., 429} -> SOURCE_UNAVAILABLE
    opt_in_429_config = RegistrySourceRuntimeConfig(
        base_url="https://registry.gov.in",
        allowed_hostnames=frozenset({"registry.gov.in"}),
        retryable_status_codes=frozenset({408, 429, 500, 502, 503, 504}),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        safe_client = SafeRegistryHttpClient(
            opt_in_429_config, http_client=client, resolve_dns=False
        )
        res = await safe_client.execute_request("GET", "https://registry.gov.in/check")
        assert res.classified_outcome == VerificationEvidenceOutcome.SOURCE_UNAVAILABLE

    # 8. 503 Service Unavailable
    transport = httpx.MockTransport(
        lambda req: httpx.Response(status_code=503, content=b"Unavailable")
    )
    async with httpx.AsyncClient(transport=transport) as client:
        safe_client = SafeRegistryHttpClient(
            config, http_client=client, resolve_dns=False
        )
        res = await safe_client.execute_request("GET", "https://registry.gov.in/check")
        assert res.classified_outcome == VerificationEvidenceOutcome.SOURCE_UNAVAILABLE


@pytest.mark.asyncio
async def test_safe_http_client_streaming_size_limit_rejection() -> None:
    config = RegistrySourceRuntimeConfig(
        base_url="https://registry.gov.in",
        allowed_hostnames=frozenset({"registry.gov.in"}),
        max_response_bytes=100,
    )

    chunks_generated = 0

    async def streaming_generator():
        nonlocal chunks_generated
        while True:
            chunks_generated += 1
            yield b"x" * 60  # Chunk 1: 60 bytes, Chunk 2: 120 bytes > 100 max

    transport = httpx.MockTransport(
        lambda req: httpx.Response(status_code=200, content=streaming_generator())
    )
    async with httpx.AsyncClient(transport=transport) as client:
        safe_client = SafeRegistryHttpClient(
            config, http_client=client, resolve_dns=False
        )
        with pytest.raises(VerificationNetworkError) as exc:
            await safe_client.execute_request("GET", "https://registry.gov.in/check")
        assert exc.value.code == "RESPONSE_TOO_LARGE"
        # Proves reading aborted at chunk 2, never continued streaming infinitely
        assert chunks_generated == 2


@pytest.mark.asyncio
async def test_safe_http_client_unexpected_content_encoding() -> None:
    config = RegistrySourceRuntimeConfig(
        base_url="https://registry.gov.in",
        allowed_hostnames=frozenset({"registry.gov.in"}),
    )

    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            status_code=200,
            content=b"compressed data",
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        safe_client = SafeRegistryHttpClient(
            config, http_client=client, resolve_dns=False
        )
        with pytest.raises(VerificationNetworkError) as exc:
            await safe_client.execute_request("GET", "https://registry.gov.in/check")
        assert exc.value.code == "UNEXPECTED_CONTENT_ENCODING"


@pytest.mark.asyncio
async def test_safe_http_client_timeout_and_tls_handling() -> None:
    config = RegistrySourceRuntimeConfig(
        base_url="https://registry.gov.in",
        allowed_hostnames=frozenset({"registry.gov.in"}),
    )

    # Timeout
    def timeout_handler(req: httpx.Request) -> Any:
        raise httpx.ConnectTimeout("timeout")

    transport = httpx.MockTransport(timeout_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        safe_client = SafeRegistryHttpClient(
            config, http_client=client, resolve_dns=False
        )
        with pytest.raises(VerificationNetworkError) as exc:
            await safe_client.execute_request("GET", "https://registry.gov.in/check")
        assert exc.value.code == "NETWORK_TIMEOUT"

    # TLS error
    def ssl_handler(req: httpx.Request) -> Any:
        raise ssl.SSLError("cert failure")

    transport = httpx.MockTransport(ssl_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        safe_client = SafeRegistryHttpClient(
            config, http_client=client, resolve_dns=False
        )
        with pytest.raises(VerificationNetworkError) as exc:
            await safe_client.execute_request("GET", "https://registry.gov.in/check")
        assert exc.value.code == "TLS_VERIFICATION_FAILURE"


@pytest.mark.asyncio
async def test_safe_http_client_rejects_redirect_and_unexpected_content_type() -> None:
    config = RegistrySourceRuntimeConfig(
        base_url="https://registry.gov.in",
        allowed_hostnames=frozenset({"registry.gov.in"}),
    )

    transport_302 = httpx.MockTransport(
        lambda req: httpx.Response(
            status_code=302, headers={"location": "https://elsewhere.example"}
        )
    )
    async with httpx.AsyncClient(transport=transport_302) as client:
        safe_client = SafeRegistryHttpClient(
            config, http_client=client, resolve_dns=False
        )
        with pytest.raises(VerificationNetworkError, match="REDIRECT_REJECTED"):
            await safe_client.execute_request("GET", "https://registry.gov.in/check")

    transport_html = httpx.MockTransport(
        lambda req: httpx.Response(
            status_code=200,
            content=b"<html>not a registry result</html>",
            headers={"content-type": "text/html"},
        )
    )
    async with httpx.AsyncClient(transport=transport_html) as client:
        safe_client = SafeRegistryHttpClient(
            config, http_client=client, resolve_dns=False
        )
        with pytest.raises(
            VerificationNetworkError, match="UNSAFE_RESPONSE_CONTENT_TYPE"
        ):
            await safe_client.execute_request("GET", "https://registry.gov.in/check")


@pytest.mark.asyncio
async def test_default_client_disables_redirects_and_environment_trust() -> None:
    config = RegistrySourceRuntimeConfig(
        base_url="https://registry.gov.in",
        allowed_hostnames=frozenset({"registry.gov.in"}),
    )
    client = SafeRegistryHttpClient(config)._get_client()
    try:
        assert client.follow_redirects is False
        assert client.trust_env is False
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_network_error_and_logs_do_not_include_request_secrets_or_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = RegistrySourceRuntimeConfig(
        base_url="https://registry.gov.in",
        allowed_hostnames=frozenset({"registry.gov.in"}),
    )

    transport = httpx.MockTransport(
        lambda req: (_ for _ in ()).throw(httpx.ConnectTimeout("transport failure"))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        safe_client = SafeRegistryHttpClient(
            config, http_client=client, resolve_dns=False
        )

        with pytest.raises(VerificationNetworkError) as exc:
            await safe_client.execute_request(
                "GET",
                "https://registry.gov.in/check",
                headers={"Authorization": "Bearer test-secret"},
            )
        assert exc.value.code == "NETWORK_TIMEOUT"
        rendered = caplog.text
        assert "test-secret" not in rendered
        assert "Authorization" not in rendered


def test_classify_network_error_closed_taxonomy() -> None:
    assert (
        classify_network_error(VerificationNetworkError("DNS_RESOLUTION_FAILURE"))
        == VerificationEvidenceOutcome.SOURCE_UNAVAILABLE
    )
    assert (
        classify_network_error(VerificationNetworkError("NETWORK_TIMEOUT"))
        == VerificationEvidenceOutcome.SOURCE_UNAVAILABLE
    )
    assert (
        classify_network_error(VerificationNetworkError("NETWORK_CONNECTION_ERROR"))
        == VerificationEvidenceOutcome.SOURCE_UNAVAILABLE
    )
    assert (
        classify_network_error(VerificationNetworkError("TLS_VERIFICATION_FAILURE"))
        == VerificationEvidenceOutcome.SOURCE_INTEGRITY_FAILURE
    )
    assert (
        classify_network_error(VerificationNetworkError("RESPONSE_TOO_LARGE"))
        == VerificationEvidenceOutcome.SOURCE_RESPONSE_INVALID
    )
    assert (
        classify_network_error(VerificationNetworkError("UNSAFE_RESPONSE_CONTENT_TYPE"))
        == VerificationEvidenceOutcome.SOURCE_RESPONSE_INVALID
    )
    assert (
        classify_network_error(VerificationNetworkError("REDIRECT_REJECTED"))
        == VerificationEvidenceOutcome.SOURCE_RESPONSE_INVALID
    )
    assert (
        classify_network_error(VerificationNetworkError("UNEXPECTED_CONTENT_ENCODING"))
        == VerificationEvidenceOutcome.SOURCE_RESPONSE_INVALID
    )
    # SSRF and internal configuration violations must return None (not an outage)
    assert classify_network_error(VerificationNetworkError("SSRF_BLOCKED_IP")) is None
    assert (
        classify_network_error(VerificationNetworkError("DISALLOWED_HOSTNAME")) is None
    )
    assert classify_network_error(VerificationNetworkError("UNSAFE_URL_SCHEME")) is None


def test_network_default_status_codes_freeze() -> None:
    assert DEFAULT_NOT_FOUND_STATUS_CODES == frozenset()
    assert DEFAULT_RETRYABLE_STATUS_CODES == frozenset({408, 500, 502, 503, 504})
    assert 404 not in DEFAULT_NOT_FOUND_STATUS_CODES
    assert 429 not in DEFAULT_RETRYABLE_STATUS_CODES
