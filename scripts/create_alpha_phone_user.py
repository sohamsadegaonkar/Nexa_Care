#!/usr/bin/env python3
"""Provision an existing alpha phone as a confirmed Supabase Auth user.

Supports both legacy JWT service-role keys and modern ``sb_secret_`` keys.
The installed supabase-py 2.9.1 client rejects opaque keys locally, so this
script calls the documented Auth Admin HTTP endpoint with the ``apikey``
header instead of constructing the incompatible legacy client.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.patient_auth_service import normalize_indian_phone  # noqa: E402
from scripts.demo_environment import require_demo_environment  # noqa: E402


class ProvisioningError(RuntimeError):
    """Safe operator-facing provisioning failure."""


def build_admin_headers(api_key: str) -> dict[str, str]:
    headers = {
        "apikey": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # Legacy service_role keys are JWTs and can be sent as Bearer tokens.
    if api_key.startswith("eyJ") and api_key.count(".") == 2:
        headers["Authorization"] = f"Bearer {api_key}"

    # Modern sb_secret_ keys must remain in the apikey header only.
    return headers


def _load_config() -> tuple[str, str]:
    # Explicit override prevents stale PowerShell variables from winning over
    # the repository's ignored runtime file.
    load_dotenv(ROOT / ".env", override=True)
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        or os.environ.get("SUPABASE_KEY", "").strip()
    )
    if not url:
        raise ProvisioningError("SUPABASE_URL is missing from .env")
    if not key:
        raise ProvisioningError(
            "SUPABASE_SERVICE_ROLE_KEY or SUPABASE_KEY is missing from .env"
        )
    _validate_server_key(key)
    return url, key


def _validate_server_key(key: str) -> None:
    if key.startswith("sb_secret_"):
        return
    if key.startswith("sb_publishable_"):
        raise ProvisioningError("A publishable Supabase key cannot perform admin provisioning")
    try:
        parts = key.split(".")
        if len(parts) != 3:
            raise ValueError
        padding = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + padding))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise ProvisioningError("Configured Supabase admin key has an unsupported format") from None
    if payload.get("role") != "service_role":
        raise ProvisioningError("Configured Supabase JWT is not a service-role key")


def _request(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    key: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> httpx.Response:
    try:
        response = client.request(
            method,
            url,
            headers=build_admin_headers(key),
            params=params,
            json=json_body,
        )
    except httpx.HTTPError as exc:
        raise ProvisioningError(
            f"Supabase Auth is unreachable ({type(exc).__name__}); details redacted"
        ) from None
    if response.status_code in {401, 403}:
        raise ProvisioningError(
            "Supabase rejected the admin key; verify the secret/service-role key"
        )
    if response.status_code >= 400:
        raise ProvisioningError(
            f"Supabase Auth admin request failed with HTTP {response.status_code}; details redacted"
        )
    return response


def _find_phone_user(
    client: httpx.Client,
    *,
    auth_url: str,
    key: str,
    phone: str,
) -> str | None:
    page = 1
    per_page = 100
    while True:
        response = _request(
            client,
            "GET",
            f"{auth_url}/admin/users",
            key=key,
            params={"page": page, "per_page": per_page},
        )
        payload = response.json()
        users = payload.get("users", []) if isinstance(payload, dict) else []
        for user in users:
            candidate = user.get("phone") if isinstance(user, dict) else None
            if candidate:
                try:
                    if normalize_indian_phone(candidate) == phone:
                        return str(user["id"])
                except (ValueError, KeyError):
                    continue
        if len(users) < per_page:
            return None
        page += 1


def create_or_get_phone_user(
    phone: str,
    *,
    url: str,
    key: str,
    client: httpx.Client,
) -> tuple[str, str]:
    normalized_phone = normalize_indian_phone(phone)
    auth_url = f"{url.rstrip('/')}/auth/v1"
    existing_id = _find_phone_user(
        client,
        auth_url=auth_url,
        key=key,
        phone=normalized_phone,
    )
    if existing_id:
        return "already-exists", existing_id

    response = _request(
        client,
        "POST",
        f"{auth_url}/admin/users",
        key=key,
        json_body={"phone": normalized_phone, "phone_confirm": True},
    )
    payload = response.json()
    user = payload.get("user", payload) if isinstance(payload, dict) else {}
    user_id = user.get("id") if isinstance(user, dict) else None
    if not user_id:
        raise ProvisioningError("Supabase did not return a created user ID")
    return "created", str(user_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or find the alpha Supabase phone user")
    parser.add_argument("phone", help="Indian phone in E.164 or 10-digit format")
    args = parser.parse_args()
    try:
        require_demo_environment("create_alpha_phone_user")
        url, key = _load_config()
        with httpx.Client(timeout=15.0) as client:
            status, user_id = create_or_get_phone_user(
                args.phone,
                url=url,
                key=key,
                client=client,
            )
    except (ProvisioningError, ValueError) as exc:
        print(f"status=rejected reason={exc}", file=sys.stderr)
        return 1
    print(f"status={status}")
    print(f"supabase_user_id={user_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
