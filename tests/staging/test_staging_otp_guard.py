"""Negative first: staging OTP must not be usable without private fixtures."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.security.staging_otp_guard import enforce_staging_otp_allowlist


def test_staging_otp_denied_without_any_test_phone_config(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.delenv("STAGING_OTP_PHONE_ALLOWLIST", raising=False)
    with pytest.raises(HTTPException) as exc:
        enforce_staging_otp_allowlist("+918000000001")
    assert exc.value.status_code == 503
    assert exc.value.detail["error_code"] == "STAGING_OTP_ALLOWLIST_NOT_CONFIGURED"


def test_staging_otp_denied_when_not_in_explicit_allowlist(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("STAGING_OTP_PHONE_ALLOWLIST", "+918000000002")
    with pytest.raises(HTTPException) as exc:
        enforce_staging_otp_allowlist("+918000000001")
    assert exc.value.status_code == 403
    assert exc.value.detail["error_code"] == "STAGING_PHONE_NOT_ALLOWLISTED"
    assert "+91" not in str(exc.value.detail)


def test_staging_otp_allows_exact_private_test_phone_only(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("STAGING_OTP_PHONE_ALLOWLIST", " +918000000001 , +918000000002 ")
    assert enforce_staging_otp_allowlist("+918000000001") is None
    assert enforce_staging_otp_allowlist("+918000000002") is None
    with pytest.raises(HTTPException) as exc:
        enforce_staging_otp_allowlist("+918000000003")
    assert exc.value.status_code == 403


@pytest.mark.parametrize("environment", ["test", "alpha", "production"])
def test_nonstaging_env_keeps_existing_auth_behavior(monkeypatch, environment):
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.delenv("STAGING_OTP_PHONE_ALLOWLIST", raising=False)
    assert enforce_staging_otp_allowlist("+918000000001") is None


def test_guard_never_treats_wildcard_as_allowall(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("STAGING_OTP_PHONE_ALLOWLIST", "*")
    with pytest.raises(HTTPException) as exc:
        enforce_staging_otp_allowlist("+918000000001")
    assert exc.value.status_code == 403
