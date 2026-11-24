"""Fail closed when a release check could target production or an unsafe URL."""
from __future__ import annotations

import argparse
import ipaddress
import re
from urllib.parse import urlsplit


PRODUCTION_HOSTS = frozenset({
    "nexa-care.onrender.com",
    "nexa-care.com",
    "www.nexa-care.com",
})
AZURE_STAGING_HOST = re.compile(
    r"nexa-stg-web\.[a-z0-9-]+\.centralindia\.azurecontainerapps\.io"
)
LEGACY_STAGING_SUFFIXES = (
    ".onrender.com",
    ".azurewebsites.net",
    ".vercel.app",
    ".nexacare.dev",
)


def validate_staging_target(value: str, *, azure_only: bool = False) -> str:
    url = urlsplit(value.strip())
    host = (url.hostname or "").lower().rstrip(".")
    if (
        url.scheme != "https"
        or not host
        or host in PRODUCTION_HOSTS
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or url.path not in ("", "/")
        or url.port not in (None, 443)
        or host == "localhost"
        or host.endswith((".localhost", ".local", ".internal"))
    ):
        raise ValueError("STAGING_TARGET_INVALID")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("STAGING_TARGET_INVALID")
    if not AZURE_STAGING_HOST.fullmatch(host) and (
        azure_only or not host.endswith(LEGACY_STAGING_SUFFIXES)
    ):
        raise ValueError("STAGING_HOST_NOT_ALLOWLISTED")
    return f"https://{host}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--azure-only", action="store_true")
    parser.add_argument("url")
    args = parser.parse_args()
    try:
        print(validate_staging_target(args.url, azure_only=args.azure_only))
    except ValueError as exc:
        parser.exit(2, f"{exc}\n")
