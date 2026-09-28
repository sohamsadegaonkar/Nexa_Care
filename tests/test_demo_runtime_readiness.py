from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts import demo_preflight

ROOT = Path(__file__).resolve().parents[1]
START = ROOT / "scripts" / "start_nexa_dev.ps1"
PREFLIGHT = ROOT / "scripts" / "demo_preflight.py"


def test_launcher_prefers_active_then_dot_venv_then_venv() -> None:
    source = START.read_text(encoding="utf-8")
    active = source.index("$env:VIRTUAL_ENV")
    dot_venv = source.index("'.venv\\Scripts\\python.exe'")
    legacy_venv = source.index("'venv\\Scripts\\python.exe'")
    assert active < dot_venv < legacy_venv
    assert "Get-Command python" not in source


def test_launcher_does_not_force_missing_firebase_config() -> None:
    source = START.read_text(encoding="utf-8")
    assert "google-services.development.local.json" in source
    assert "Firebase Android client config not present; push-dependent demo features may be unavailable." in source
    assert "GOOGLE_SERVICES_FILE = './google-services.json'" not in source
    assert "Remove-Item Env:GOOGLE_SERVICES_FILE" in source


def test_launcher_preserves_required_adb_reverse_ports_and_go_no_go() -> None:
    source = START.read_text(encoding="utf-8")
    assert "adb reverse tcp:8081 tcp:8081" in source
    assert "adb reverse tcp:8000 tcp:8000" in source
    assert "NEXA STARTUP: NO-GO" in source
    assert "NEXA STARTUP: GO" in source


def test_preflight_is_read_only_and_reports_schema_drift() -> None:
    source = PREFLIGHT.read_text(encoding="utf-8")
    assert "DATABASE_SCHEMA_OUTDATED" in source
    assert "recommended_command=python -m alembic upgrade head" in source
    assert "MigrationContext.configure" in source
    assert "command.upgrade" not in source
    assert "alembic stamp" not in source.lower()
    assert "ALTER TABLE" not in source.upper()
    assert "DELETE FROM" not in source.upper()


def test_preflight_uses_real_clinical_eligibility_and_erasure_checks() -> None:
    source = PREFLIGHT.read_text(encoding="utf-8")
    assert "ClinicalEligibilityService" in source
    assert "ClinicalCapability.PATIENT_DISCOVER" in source
    assert "check_erasure_registry" in source
    assert "clinical_denial_code=" in source


class _CloseOnlyRedisClient:
    def __init__(self) -> None:
        self.closed = False

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_preflight_closes_redis_4_client_without_aclose(monkeypatch, capsys) -> None:
    client = _CloseOnlyRedisClient()

    monkeypatch.setattr(demo_preflight, "require_demo_environment", lambda _: "development")
    monkeypatch.setattr(
        demo_preflight,
        "get_database_config",
        lambda: SimpleNamespace(url="postgresql+asyncpg://localhost/nexa_demo"),
    )
    monkeypatch.setattr(
        demo_preflight,
        "get_redis_config",
        lambda: SimpleNamespace(url="redis://localhost:6379/0"),
    )
    monkeypatch.setattr(
        demo_preflight,
        "_database_revisions",
        AsyncMock(return_value=("20260919_medication_catalog", "20260919_medication_catalog")),
    )
    monkeypatch.setattr(demo_preflight, "_schema_ready", AsyncMock(return_value=(True, [])))
    monkeypatch.setattr(demo_preflight, "_demo_state", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(demo_preflight.redis_async, "from_url", lambda _: client)

    assert await demo_preflight.run_preflight() is True
    assert client.closed is True
    assert "redis=reachable" in capsys.readouterr().out
