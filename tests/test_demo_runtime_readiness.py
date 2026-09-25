from __future__ import annotations

from pathlib import Path

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
