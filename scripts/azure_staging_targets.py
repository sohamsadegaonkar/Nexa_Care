"""Validate the Azure staging private datastore boundary before startup."""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlsplit


def check_database(environment: Mapping[str, str], *, migration: bool) -> None:
    variable = "MIGRATION_DATABASE_URL" if migration else "DATABASE_URL"
    username = "nexa_migrator" if migration else "nexa_api_runtime"
    expected = environment.get("NEXA_AZURE_STAGING_POSTGRES_HOST", "")
    try:
        url = urlsplit(environment.get(variable, ""))
        valid = (
            environment.get("ENVIRONMENT") == "staging"
            and re.fullmatch(r"nexa-stg-[a-z0-9]{13}\.postgres\.database\.azure\.com", expected)
            and url.scheme == "postgresql+asyncpg"
            and url.hostname == expected
            and url.port in (None, 5432)
            and url.username == username
            and bool(url.password)
            and url.path == "/nexacare_staging"
            and not url.query
            and not url.fragment
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("AZURE_STAGING_DATABASE_TARGET_REJECTED")


def check_redis(environment: Mapping[str, str]) -> None:
    expected = environment.get("NEXA_AZURE_STAGING_REDIS_HOST", "")
    try:
        url = urlsplit(environment.get("UPSTASH_REDIS_URL", ""))
        valid = (
            re.fullmatch(r"nexa-stg-[a-z0-9]{13}\.centralindia\.redis\.azure\.net", expected)
            and url.scheme == "rediss"
            and url.hostname == expected
            and url.port == 10000
            and url.username in (None, "", "default")
            and bool(url.password)
            and url.path == "/0"
            and url.query == "ssl_check_hostname=true"
            and not url.fragment
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("AZURE_STAGING_REDIS_TARGET_REJECTED")
