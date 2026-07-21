"""Fail-closed environment guard shared by alpha/demo mutation tools."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from app.core.config import get_runtime_environment


def require_demo_environment(script_name: str) -> str:
    runtime = get_runtime_environment()
    environment = runtime.value
    if not runtime.is_demo_allowed:
        raise RuntimeError(
            f"Refusing to run {script_name} in this environment"
        )

    database_url = os.getenv("DATABASE_URL", "").strip()
    try:
        database_host = urlsplit(database_url).hostname or "[not-configured]"
    except ValueError:
        database_host = "[invalid-url]"
    print(f"target_environment={environment}")
    print(f"database_host={database_host}")
    return environment
