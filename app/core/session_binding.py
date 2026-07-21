"""Opaque provider-session binding helpers.

Only a one-way digest is exposed. Raw cookie and bearer values must never be
written to logs, audit metadata, database rows, or client responses.
"""

from __future__ import annotations

import hashlib

from fastapi import HTTPException, Request, status


def provider_session_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    scheme, _, credential = authorization.partition(" ")
    if scheme.lower() == "bearer" and credential.strip():
        return credential.strip()
    cookie = request.cookies.get("nexa_provider_session")
    return cookie if isinstance(cookie, str) and cookie else None


def provider_session_binding(request: Request) -> str:
    token = provider_session_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "PROVIDER_SESSION_REQUIRED"},
        )
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
