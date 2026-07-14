from __future__ import annotations

from pathlib import Path

from app.main import app

ROOT = Path(__file__).resolve().parents[1]


def _source_files():
    for directory in (ROOT / "app", ROOT / "scripts"):
        yield from directory.rglob("*.py")


def test_no_source_references_retired_system_audit_table():
    forbidden = ('table("system_audit")', 'from("system_audit")', "public.system_audit")
    violations = []
    for path in _source_files():
        source = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in source:
                violations.append(f"{path.relative_to(ROOT)}: {marker}")
    assert not violations, "Retired audit table references: " + ", ".join(violations)


def test_patient_record_routes_use_canonical_audit_service_only():
    source = (ROOT / "app" / "api" / "v2" / "patient_record_routes.py").read_text(encoding="utf-8")
    assert "read_audit_events" in source
    assert "append_audit_log_or_503" in source
    assert ".table(" not in source
    assert "INSERT INTO" not in source


def test_openapi_still_generates():
    schema = app.openapi()
    assert schema["openapi"].startswith("3.")
    assert "/api/v2/patient/me/access-history" in schema["paths"]
