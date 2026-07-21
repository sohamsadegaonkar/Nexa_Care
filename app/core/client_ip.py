"""Canonical trusted-proxy client address resolution."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Iterable

from fastapi import Request


def trusted_proxy_networks(
    raw: str | None = None,
) -> tuple[ipaddress._BaseNetwork, ...]:
    value = os.getenv("TRUSTED_PROXY_NETWORKS", "") if raw is None else raw
    networks = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        networks.append(ipaddress.ip_network(item, strict=False))
    return tuple(networks)


def _is_trusted(
    address: ipaddress._BaseAddress, networks: Iterable[ipaddress._BaseNetwork]
) -> bool:
    return any(
        address.version == network.version and address in network
        for network in networks
    )


def resolve_client_ip(
    request: Request, networks: Iterable[ipaddress._BaseNetwork] | None = None
) -> str:
    """Resolve a client IP only through a fully validated trusted proxy chain."""

    client = getattr(request, "client", None)
    if client is None or not getattr(client, "host", None):
        return ""
    try:
        peer = ipaddress.ip_address(client.host)
    except ValueError:
        return ""
    configured = tuple(networks) if networks is not None else trusted_proxy_networks()
    if not _is_trusted(peer, configured):
        return peer.compressed
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return peer.compressed
    try:
        chain = [ipaddress.ip_address(item.strip()) for item in forwarded.split(",")]
    except ValueError:
        return peer.compressed
    if not chain:
        return peer.compressed
    for address in reversed(chain):
        if not _is_trusted(address, configured):
            return address.compressed
    return chain[0].compressed
