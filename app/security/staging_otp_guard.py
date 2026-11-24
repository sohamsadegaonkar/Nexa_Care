"""Fail-closed, staging-only patient SMS OTP allowlist.

No phone number, OTP, identity, or credential is committed to source control.
Set STAGING_OTP_PHONE_ALLOWLIST as a secret on the isolated staging backend
before allowing requests. The Supabase project's public signup must also
be disabled. This guard does not replace Supabase OTP verification, Redis
rate limits, device authority, signed consent or human release reviews.
"""
from __future__ import annotations

import os

from fastapi import HTTPException


def enforce_staging_otp_allowlist(normalized_phone: str) -> None:
    """Reject OTP traffic on staging unless explicitly allowed.

    The phone must already be normalized as +91 followed by ten digits.
    The allowlist is deployment configuration, never a source-code fixture.
    No supplied phone or configured allowlist is included in exception text.
    """
    runtime = (os.getenv("ENVIRONMENT") or os.getenv("ENV") or "").strip().lower()
    if runtime != "staging":
        return

    configured = os.getenv("STAGING_OTP_PHONE_ALLOWLIST", "")
    allowed = {entry.strip() for entry in configured.split(",") if entry.strip()}
    if not allowed:
        raise HTTPException(
            status_code=503,
            detail={"error_code": "STAGING_OTP_ALLOWLIST_NOT_CONFIGURED"},
        )
    if normalized_phone not in allowed:
        raise HTTPException(
            status_code=403,
            detail={"error_code": "STAGING_PHONE_NOT_ALLOWLISTED"},
        )
