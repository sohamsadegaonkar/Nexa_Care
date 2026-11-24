"""Safe loopback-only readiness check for the doctor website and backend.

Does not authenticate a user, seed data, exercise consent, or claim release readiness.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BACKEND = "http://127.0.0.1:8000"
DOCTOR = "http://localhost:3000"
_MAX_RESPONSE_BYTES = 4096
_CRITICAL = ("postgres", "redis", "audit_outbox", "workers")


@dataclass(frozen=True)
class CheckResponse:
    status: int
    content_type: str
    body: bytes


def fetch_local(url: str) -> CheckResponse | None:
    """Read a bounded public response; do not print response bodies or headers."""
    request = Request(url, headers={"Accept": "application/json, text/html"})
    try:
        with urlopen(request, timeout=3) as response:
            return CheckResponse(
                status=response.status,
                content_type=response.headers.get("Content-Type", ""),
                body=response.read(_MAX_RESPONSE_BYTES),
            )
    except HTTPError as exc:
        try:
            return CheckResponse(
                status=exc.code,
                content_type=exc.headers.get("Content-Type", ""),
                body=exc.read(_MAX_RESPONSE_BYTES),
            )
        finally:
            exc.close()
    except (URLError, TimeoutError, OSError):
        return None


def _json(response: CheckResponse | None) -> dict | None:
    if (
        response is None
        or not response.content_type.lower().startswith("application/json")
    ):
        return None
    try:
        payload = json.loads(response.body)
    except (ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def check_stack(fetch=fetch_local) -> bool:
    """A 401 through the same-origin proxy is expected without doctor credentials."""
    backend_live = fetch(f"{BACKEND}/healthz")
    live_body = _json(backend_live)
    live_ok = (
        backend_live is not None
        and backend_live.status == 200
        and live_body is not None
        and live_body.get("status") == "ok"
    )

    backend_ready = fetch(f"{BACKEND}/health")
    ready_body = _json(backend_ready)
    ready_checks = ready_body.get("checks") if ready_body is not None else None
    ready_ok = (
        backend_ready is not None
        and backend_ready.status == 200
        and ready_body is not None
        and ready_body.get("status") == "ok"
        and isinstance(ready_checks, dict)
        and all(ready_checks.get(name) == "ok" for name in _CRITICAL)
    )

    doctor = fetch(f"{DOCTOR}/doctor/login")
    doctor_ok = (
        doctor is not None
        and doctor.status == 200
        and doctor.content_type.lower().startswith("text/html")
    )

    session = fetch(f"{DOCTOR}/api/v2/auth/web/session")
    session_body = _json(session)
    proxy_ok = (
        session is not None
        and session.status == 401
        and session_body is not None
        and "detail" in session_body
    )

    for label, passed in (
        ("backend_liveness", live_ok),
        ("backend_readiness", ready_ok),
        ("doctor_login_page", doctor_ok),
        ("same_origin_auth_proxy", proxy_ok),
    ):
        print(f"{label}={'PASS' if passed else 'NO_GO'}")

    overall = live_ok and ready_ok and doctor_ok and proxy_ok
    print(f"local_web_stack={'PREREQUISITES_READY' if overall else 'NO_GO'}")
    print("device_signing=NOT_RUN")
    print("physical_nfc=NOT_RUN")
    print("clinical_release=HOLD")
    return bool(overall)


if __name__ == "__main__":
    sys.exit(0 if check_stack() else 1)
