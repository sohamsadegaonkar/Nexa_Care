"""Web-only local startup and same-origin proxy fail-closed contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import check_nexa_web_stack as probe

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "start_nexa_web.ps1"
PROXY_CONFIG = ROOT / "nexa-client" / "apps" / "next" / "next.config.js"


def _response(status: int, kind: str, data: object):
    return probe.CheckResponse(
        status=status,
        content_type=kind,
        body=json.dumps(data).encode("utf-8"),
    )


def _scenario(*, ready=True, proxy_status=401, doctor_status=200):
    responses = {
        f"{probe.BACKEND}/healthz": _response(
            200, "application/json", {"status": "ok"}
        ),
        f"{probe.BACKEND}/health": _response(
            200 if ready else 503,
            "application/json",
            {
                "status": "ok" if ready else "degraded",
                "checks": {
                    "redis": "ok",
                    "postgres": "ok" if ready else "unavailable",
                    "audit_outbox": "ok",
                    "workers": "ok",
                },
            },
        ),
        f"{probe.DOCTOR}/doctor/login": probe.CheckResponse(
            doctor_status, "text/html; charset=utf-8", b"<html/>"
        ),
        f"{probe.DOCTOR}/api/v2/auth/web/session": _response(
            proxy_status,
            "application/json" if proxy_status == 401 else "text/html",
            {"detail": "Browser session required"} if proxy_status == 401 else {},
        ),
    }
    return responses.__getitem__


def test_local_web_readiness_pass_is_explicitly_not_clinical_release(capsys):
    assert probe.check_stack(fetch=_scenario()) is True
    output = capsys.readouterr().out
    assert "backend_liveness=PASS" in output
    assert "backend_readiness=PASS" in output
    assert "doctor_login_page=PASS" in output
    assert "same_origin_auth_proxy=PASS" in output
    assert "local_web_stack=PREREQUISITES_READY" in output
    assert "device_signing=NOT_RUN" in output
    assert "physical_nfc=NOT_RUN" in output
    assert "clinical_release=HOLD" in output


@pytest.mark.parametrize(
    "scenario",
    [
        {"ready": False},
        {"proxy_status": 404},
        {"proxy_status": 200},
        {"doctor_status": 404},
    ],
)
def test_unhealthy_backend_wrong_proxy_or_missing_page_fail_closed(capsys, scenario):
    assert probe.check_stack(fetch=_scenario(**scenario)) is False
    assert "local_web_stack=NO_GO" in capsys.readouterr().out


def test_network_unavailability_and_wrong_json_are_no_go():
    assert probe.check_stack(fetch=lambda _url: None) is False
    response = probe.CheckResponse(200, "application/json", b"<html>not JSON</html>")
    assert probe.check_stack(fetch=lambda _url: response) is False


def test_launcher_keeps_doctor_cookie_auth_same_origin_without_android_dependencies():
    source = LAUNCHER.read_text(encoding="utf-8")
    next_config = PROXY_CONFIG.read_text(encoding="utf-8")
    assert "NEXT_PUBLIC_API_URL = 'http://localhost:3000'" in source
    assert "API_PROXY_TARGET = 'http://127.0.0.1:8000'" in source
    assert "yarn workspace next-app dev --hostname localhost --port 3000" in source
    assert "uvicorn app.main:app --host 127.0.0.1 --port 8000" in source
    assert "--discovery public-id" in source
    assert "scripts\\check_nexa_web_stack.py" in source
    assert "Assert-FreeLocalPort" in source
    assert "NEXA WEB STACK: LOCAL PREREQUISITES READY" in source
    assert "adb reverse" not in source
    assert "java.exe" not in source
    assert "GOOGLE_SERVICES_FILE" not in source
    assert "Stop-Process" not in source
    assert "source: '/api/:path*'" in next_config
    assert "destination: `${target}/api/:path*`" in next_config
