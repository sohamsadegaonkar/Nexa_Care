#!/usr/bin/env python3
"""Validate alpha configuration and optional infrastructure without leaking secrets."""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402
from app.core.config import (  # noqa: E402
    get_clinic_config, get_database_config, get_handshake_config,
    get_kms_config, get_redis_config, get_supabase_config,
)
from app.core.database import get_async_engine  # noqa: E402
from app.core.redis import get_redis_client  # noqa: E402
from app.core.security import _load_key  # noqa: E402

PLACEHOLDER = re.compile(r"your-project|your-service-role-key|username:password|user:pass|change-me|REPLACE_WITH|GENERATED_|<[^>]+>", re.I)

def safe_error(exc: Exception) -> str:
    message = str(exc).lower()
    if "authentication" in message or "password" in message or "unauthorized" in message:
        category = "authentication failure"
    elif "certificate" in message or "ssl" in message or "tls" in message:
        category = "TLS failure"
    elif "name or service" in message or "getaddrinfo" in message or "dns" in message:
        category = "DNS failure"
    elif "timeout" in message or "timed out" in message:
        category = "connection timeout"
    elif "driver" in message or "module" in message:
        category = "driver unavailable"
    else:
        category = "connection failed"
    return f"{type(exc).__name__}: {category}; details redacted"

def validate_url(name: str, value: str, schemes: set[str]) -> list[str]:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in schemes or not parsed.hostname:
            return [f"{name}: invalid URL structure or scheme"]
    except ValueError:
        return [f"{name}: invalid URL structure"]
    return []

def validate_config() -> bool:
    print("\nConfiguration validation")
    errors: list[str] = []
    values: dict[str, str] = {}
    getters = {
        "SUPABASE_URL": lambda: get_supabase_config().url,
        "SUPABASE_KEY": lambda: get_supabase_config().key,
        "DATABASE_URL": lambda: get_database_config().url,
        "UPSTASH_REDIS_URL": lambda: get_redis_config().url,
        "HANDSHAKE_PEPPER_SECRET": lambda: get_handshake_config().pepper_secret,
        "KEK_ROOT_SECRET": lambda: get_kms_config().kek_root_secret,
        "CLINIC_API_KEY": lambda: get_clinic_config().api_key,
        "MFA_ENCRYPTION_KEY": lambda: _load_key("MFA_ENCRYPTION_KEY").decode(),
        "PII_ENCRYPTION_KEY": lambda: _load_key("PII_ENCRYPTION_KEY").decode(),
    }
    for name, getter in getters.items():
        try:
            values[name] = getter()
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}")
    for name, value in values.items():
        if PLACEHOLDER.search(value): errors.append(f"{name}: placeholder detected")
    print(f"{'Variable':<28} Configured")
    for name in getters:
        print(f"{name:<28} {name in values and not any(e.startswith(name + ':') for e in errors)}")
    if "DATABASE_URL" in values:
        errors += validate_url("DATABASE_URL", values["DATABASE_URL"], {"postgresql+asyncpg"})
    if "UPSTASH_REDIS_URL" in values:
        errors += validate_url("UPSTASH_REDIS_URL", values["UPSTASH_REDIS_URL"], {"redis", "rediss"})
        host = urlsplit(values["UPSTASH_REDIS_URL"]).hostname or ""
        if host not in {"localhost", "127.0.0.1", "::1"} and not values["UPSTASH_REDIS_URL"].startswith("rediss://"):
            errors.append("UPSTASH_REDIS_URL: hosted Redis must use TLS (rediss)")
    if "SUPABASE_URL" in values: errors += validate_url("SUPABASE_URL", values["SUPABASE_URL"], {"https"})
    for error in errors: print(f"ERROR: {error}")
    return not errors

async def check_postgres() -> bool:
    print("\nPostgreSQL connectivity")
    try:
        engine = get_async_engine()
        async with asyncio.timeout(10):
            async with engine.connect() as conn: await conn.execute(text("SELECT 1"))
        print("PASS: SELECT 1")
        return True
    except Exception as exc:
        print(f"FAIL: {safe_error(exc)}")
        return False
    finally:
        try: await get_async_engine().dispose()
        except Exception: pass

def check_redis() -> bool:
    print("\nRedis connectivity")
    client = None
    try:
        client = get_redis_client()
        client.connection_pool.connection_kwargs["socket_connect_timeout"] = 10
        client.connection_pool.connection_kwargs["socket_timeout"] = 10
        if not client.ping(): raise RuntimeError("PING returned false")
        print("PASS: PING")
        return True
    except Exception as exc:
        print(f"FAIL: {safe_error(exc)}")
        return False
    finally:
        if client:
            try: client.close()
            except Exception: pass

def check_supabase() -> bool:
    print("\nSupabase connectivity")
    try:
        config = get_supabase_config()
        request = Request(
            f"{config.url.rstrip('/')}/auth/v1/health",
            headers={"apikey": config.key},
            method="GET",
        )
        with urlopen(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"health endpoint returned HTTP {response.status}")
        print("PASS: authentication service health endpoint; table access skipped")
        return True
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}")
        return False

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-only", action="store_true")
    parser.add_argument("--check-postgres", action="store_true")
    parser.add_argument("--check-redis", action="store_true")
    parser.add_argument("--check-supabase", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    config_ok = validate_config()
    if args.config_only or not any((args.check_postgres, args.check_redis, args.check_supabase, args.all)):
        return 0 if config_ok else 1
    results = [config_ok]
    if args.all or args.check_postgres: results.append(asyncio.run(check_postgres()))
    if args.all or args.check_redis: results.append(check_redis())
    if args.all or args.check_supabase: results.append(check_supabase())
    print("\nOptional service availability: DOCUMENT_AI_API_KEY and DOCUMENT_AI_API_URL may be blank")
    return 0 if all(results) else 1

if __name__ == "__main__": raise SystemExit(main())
