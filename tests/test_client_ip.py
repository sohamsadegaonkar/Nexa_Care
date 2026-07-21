from starlette.requests import Request

from app.core.client_ip import resolve_client_ip, trusted_proxy_networks


def _request(peer: str | None, forwarded: str | None = None) -> Request:
    headers = (
        [] if forwarded is None else [(b"x-forwarded-for", forwarded.encode("ascii"))]
    )
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (peer, 1234) if peer is not None else None,
        "server": ("test", 80),
        "scheme": "http",
        "query_string": b"",
    }
    return Request(scope)


def test_direct_request_uses_peer() -> None:
    assert resolve_client_ip(_request("203.0.113.7"), ()) == "203.0.113.7"


def test_spoofed_header_from_untrusted_peer_is_ignored() -> None:
    assert (
        resolve_client_ip(
            _request("203.0.113.7", "192.0.2.9"), trusted_proxy_networks("10.0.0.0/8")
        )
        == "203.0.113.7"
    )


def test_one_trusted_proxy_returns_forwarded_client() -> None:
    networks = trusted_proxy_networks("10.0.0.0/8")
    assert (
        resolve_client_ip(_request("10.0.0.5", "203.0.113.7"), networks)
        == "203.0.113.7"
    )


def test_multiple_trusted_proxies_walks_right_to_left() -> None:
    networks = trusted_proxy_networks("10.0.0.0/8,192.0.2.0/24")
    request = _request("10.0.0.5", "203.0.113.7, 192.0.2.8")
    assert resolve_client_ip(request, networks) == "203.0.113.7"


def test_malformed_chain_falls_back_to_peer() -> None:
    networks = trusted_proxy_networks("10.0.0.0/8")
    assert resolve_client_ip(_request("10.0.0.5", "not-an-ip"), networks) == "10.0.0.5"


def test_ipv6_chain_is_supported() -> None:
    networks = trusted_proxy_networks("2001:db8:1::/48")
    assert (
        resolve_client_ip(_request("2001:db8:1::5", "2001:db8:2::9"), networks)
        == "2001:db8:2::9"
    )


def test_no_peer_information_returns_empty() -> None:
    assert resolve_client_ip(_request(None), ()) == ""
