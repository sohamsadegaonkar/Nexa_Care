"""Unit tests for SSRF-safe registry network client and URL validation."""

import ipaddress
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.models.provider import VerificationEvidenceOutcome
from app.services.provider_verification_network import (
    RegistrySourceRuntimeConfig,
    SafeRegistryHttpClient,
    VerificationNetworkError,
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

    mock_client = AsyncMock(spec=httpx.AsyncClient)

    # 1. 200 OK
    resp_200 = httpx.Response(
        status_code=200,
        content=b'{"status": "ACTIVE"}',
        headers={"Content-Type": "application/json"},
    )
    mock_client.request.return_value = resp_200
    safe_client = SafeRegistryHttpClient(
        config, http_client=mock_client, resolve_dns=False
    )

    res = await safe_client.execute_request("GET", "https://registry.gov.in/check")
    assert res.status_code == 200
    assert res.classified_outcome == VerificationEvidenceOutcome.CONFIRMED_ACTIVE

    # 2. 401 Auth Failure
    mock_client.request.return_value = httpx.Response(
        status_code=401, content=b"Unauthorized"
    )
    res = await safe_client.execute_request("GET", "https://registry.gov.in/check")
    assert (
        res.classified_outcome
        == VerificationEvidenceOutcome.SOURCE_AUTHENTICATION_FAILURE
    )

    mock_client.request.return_value = httpx.Response(
        status_code=403, content=b"Forbidden"
    )
    res = await safe_client.execute_request("GET", "https://registry.gov.in/check")
    assert (
        res.classified_outcome
        == VerificationEvidenceOutcome.SOURCE_AUTHENTICATION_FAILURE
    )

    # 3. 404 Not Found
    mock_client.request.return_value = httpx.Response(
        status_code=404, content=b"Not found"
    )
    res = await safe_client.execute_request("GET", "https://registry.gov.in/check")
    assert res.classified_outcome == VerificationEvidenceOutcome.NOT_FOUND

    # 4. 503 Service Unavailable
    mock_client.request.return_value = httpx.Response(
        status_code=503, content=b"Unavailable"
    )
    res = await safe_client.execute_request("GET", "https://registry.gov.in/check")
    assert res.classified_outcome == VerificationEvidenceOutcome.SOURCE_UNAVAILABLE


@pytest.mark.asyncio
async def test_safe_http_client_size_limit_rejection() -> None:
    config = RegistrySourceRuntimeConfig(
        base_url="https://registry.gov.in",
        allowed_hostnames=frozenset({"registry.gov.in"}),
        max_response_bytes=100,
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.request.return_value = httpx.Response(
        status_code=200,
        content=b"x" * 200,
    )
    safe_client = SafeRegistryHttpClient(
        config, http_client=mock_client, resolve_dns=False
    )

    with pytest.raises(VerificationNetworkError) as exc:
        await safe_client.execute_request("GET", "https://registry.gov.in/check")
    assert exc.value.code == "RESPONSE_TOO_LARGE"


@pytest.mark.asyncio
async def test_safe_http_client_timeout_and_tls_handling() -> None:
    config = RegistrySourceRuntimeConfig(
        base_url="https://registry.gov.in",
        allowed_hostnames=frozenset({"registry.gov.in"}),
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    safe_client = SafeRegistryHttpClient(
        config, http_client=mock_client, resolve_dns=False
    )

    # Timeout
    mock_client.request.side_effect = httpx.ConnectTimeout("timeout")
    with pytest.raises(VerificationNetworkError) as exc:
        await safe_client.execute_request("GET", "https://registry.gov.in/check")
    assert exc.value.code == "NETWORK_TIMEOUT"

    # TLS error
    import ssl

    mock_client.request.side_effect = ssl.SSLError("cert failure")
    with pytest.raises(VerificationNetworkError) as exc:
        await safe_client.execute_request("GET", "https://registry.gov.in/check")
    assert exc.value.code == "TLS_VERIFICATION_FAILURE"


@pytest.mark.asyncio
async def test_safe_http_client_rejects_redirect_and_unexpected_content_type() -> None:
    config = RegistrySourceRuntimeConfig(
        base_url="https://registry.gov.in",
        allowed_hostnames=frozenset({"registry.gov.in"}),
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    safe_client = SafeRegistryHttpClient(
        config, http_client=mock_client, resolve_dns=False
    )

    mock_client.request.return_value = httpx.Response(
        status_code=302, headers={"location": "https://elsewhere.example"}
    )
    with pytest.raises(VerificationNetworkError, match="REDIRECT_REJECTED"):
        await safe_client.execute_request("GET", "https://registry.gov.in/check")

    mock_client.request.return_value = httpx.Response(
        status_code=200,
        content=b"<html>not a registry result</html>",
        headers={"content-type": "text/html"},
    )
    with pytest.raises(VerificationNetworkError, match="UNSAFE_RESPONSE_CONTENT_TYPE"):
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
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.request.side_effect = httpx.ConnectTimeout("transport failure")
    safe_client = SafeRegistryHttpClient(
        config, http_client=mock_client, resolve_dns=False
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
