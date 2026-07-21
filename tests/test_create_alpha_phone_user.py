from __future__ import annotations

import base64
import json
from unittest.mock import patch

import httpx
import pytest

from scripts.create_alpha_phone_user import (
    ProvisioningError,
    ROOT,
    _load_config,
    _validate_server_key,
    build_admin_headers,
    create_or_get_phone_user,
)


def _jwt(role: str) -> str:
    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "HS256"}).encode())
        .decode()
        .rstrip("=")
    )
    payload = (
        base64.urlsafe_b64encode(json.dumps({"role": role}).encode())
        .decode()
        .rstrip("=")
    )
    return f"{header}.{payload}.signature"


def test_env_loading_overrides_stale_process_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://stale.example")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "stale")

    def fake_load(path, *, override):
        assert path == ROOT / ".env"
        assert override is True
        monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", _jwt("service_role"))

    with patch("scripts.create_alpha_phone_user.load_dotenv", side_effect=fake_load):
        url, key = _load_config()
    assert url == "https://project.supabase.co"
    assert key == _jwt("service_role")


def test_accepts_modern_secret_and_legacy_service_role_only() -> None:
    _validate_server_key("sb_secret_example")
    _validate_server_key(_jwt("service_role"))
    with pytest.raises(ProvisioningError):
        _validate_server_key("sb_publishable_example")
    with pytest.raises(ProvisioningError):
        _validate_server_key(_jwt("anon"))


def test_admin_headers_distinguish_modern_secret_from_legacy_jwt() -> None:
    modern = build_admin_headers("sb_secret_example")
    assert modern == {
        "apikey": "sb_secret_example",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    legacy_key = _jwt("service_role")
    legacy = build_admin_headers(legacy_key)
    assert legacy["apikey"] == legacy_key
    assert legacy["Authorization"] == f"Bearer {legacy_key}"
    assert legacy["Content-Type"] == "application/json"


def test_modern_secret_key_uses_admin_http_endpoint_without_sdk_jwt_validation() -> (
    None
):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["apikey"] == "sb_secret_example"
        assert "authorization" not in request.headers
        if request.method == "GET":
            return httpx.Response(200, json={"users": []})
        assert request.url.path == "/auth/v1/admin/users"
        assert json.loads(request.content) == {
            "phone": "+918000000001",
            "phone_confirm": True,
        }
        return httpx.Response(201, json={"id": "user-1"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        status, user_id = create_or_get_phone_user(
            "8000000001",
            url="https://project.supabase.co",
            key="sb_secret_example",
            client=client,
        )
    assert (status, user_id) == ("created", "user-1")
    assert [request.method for request in requests] == ["GET", "POST"]


def test_existing_phone_user_is_idempotent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={"users": [{"id": "existing-user", "phone": "+918000000001"}]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = create_or_get_phone_user(
            "+918000000001",
            url="https://project.supabase.co",
            key="sb_secret_example",
            client=client,
        )
    assert result == ("already-exists", "existing-user")


def test_admin_error_body_is_redacted() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="sensitive upstream details")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProvisioningError) as exc_info:
            create_or_get_phone_user(
                "+918000000001",
                url="https://project.supabase.co",
                key="sb_secret_example",
                client=client,
            )
    assert "sensitive upstream details" not in str(exc_info.value)
