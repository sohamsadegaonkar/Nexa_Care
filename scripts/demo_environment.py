"""Fail-closed environment guard shared by alpha/demo mutation tools."""

from __future__ import annotations

import os
from urllib.parse import urlsplit


ALLOWED_DEMO_ENVIRONMENTS = {"alpha", "development", "test"}


def require_demo_environment(script_name: str) -> str:
    raw = (os.getenv("ENV") or os.getenv("ENVIRONMENT") or "").strip().lower()
    environment = "development" if raw == "dev" else raw
    if not environment:
        raise RuntimeError(
            f"{script_name} requires explicit ENV=alpha, ENV=development, or ENV=test"
        )
    if environment not in ALLOWED_DEMO_ENVIRONMENTS:
        raise RuntimeError(
            f"Refusing to run {script_name} in environment {environment!r}"
        )

    database_url = os.getenv("DATABASE_URL", "").strip()
    try:
        database_host = urlsplit(database_url).hostname or "[not-configured]"
    except ValueError:
        database_host = "[invalid-url]"
    print(f"target_environment={environment}")
    print(f"database_host={database_host}")
    return environment
