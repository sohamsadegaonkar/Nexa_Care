"""Architecture guardrails (docs/CURRENT-STATE.md Section 3).

Static, AST-based checks on which consent modules production route files
import. No running server or DB required.

Phase 1 (2026-07-03): consent_service.py, routine.py, and break_glass.py
were removed and replaced by app.services.consent_engine. This test now
ensures the old consent families cannot re-enter the v2 production
surface and that no new v2 route starts importing more than one consent
authority.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


# The "production surface". Now scanning all files under app/ recursively.
def _get_all_app_files() -> list[str]:
    app_path = REPO_ROOT / "app"
    return [str(p.relative_to(REPO_ROOT)) for p in app_path.rglob("*.py")]


V2_SCANNED_FILES = _get_all_app_files()

# Module path -> consent "family" name.
FAMILY_MODULE_PREFIXES = {
    "app.services.consent_service": "consent_service",
    "app.services.consent.routine": "routine",
    "app.services.consent.break_glass": "break_glass",
    "app.services.consent_engine": "consent_engine",
    "app.services.nexa_consent_engine": "nexa_consent_engine",
}


def _families_imported(file_path: Path) -> set[str]:
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for prefix, family in FAMILY_MODULE_PREFIXES.items():
                # Style A: "from app.services.consent.routine import X"
                # — the submodule is already part of the dotted module path.
                if node.module == prefix or node.module.startswith(prefix + "."):
                    found.add(family)
                    continue
                # Style B: "from app.services.consent import routine"
                # — the submodule is imported by name from its parent
                # package, so it only shows up in node.names, not
                # node.module. Match on package prefix + imported name.
                package, _, leaf = prefix.rpartition(".")
                if package and node.module == package:
                    for alias in node.names:
                        if alias.name == leaf:
                            found.add(family)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                for prefix, family in FAMILY_MODULE_PREFIXES.items():
                    if alias.name == prefix or alias.name.startswith(prefix + "."):
                        found.add(family)
    return found


def _scan_all() -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for rel_path in V2_SCANNED_FILES:
        path = REPO_ROOT / rel_path
        families = _families_imported(path)
        if families:
            result[rel_path] = families
    return result


class TestConsentSystemDrift(unittest.TestCase):
    """Guards against the exact bug that previously made
    GET /api/v2/patient/{id}/record permanently unreachable: consent was
    issued by one module and validated by another, with disjoint Redis
    key prefixes and payload schemas. A cheap static check on the import
    graph would have caught that the day it was introduced.

    As of Phase 1, all v2 consent flows go through ConsentEngine. This
    test therefore fails if:
      - any v2 production file imports one of the removed old families
        (consent_service, routine, break_glass), or
      - any v2 production file imports more than one distinct consent
        family at the same time.
    """

    def test_no_removed_consent_families_in_v2_surface(self):
        """Must always pass. The old consent modules are gone; bringing
        them back into v2 routes is a regression."""
        found = _scan_all()
        removed_families = {
            "consent_service",
            "routine",
            "break_glass",
            "nexa_consent_engine",
        }
        offenders = {
            path: families & removed_families
            for path, families in found.items()
            if families & removed_families
        }

        self.assertFalse(
            offenders,
            f"Removed consent families (consent_service/routine/break_glass/nexa_consent_engine) "
            f"found in app files: {offenders}. "
            f"See docs/CURRENT-STATE.md Section 1.",
        )

    def test_legacy_engine_file_is_absent(self):
        """Verify the physical deletion of the legacy engine."""
        legacy_path = REPO_ROOT / "app" / "services" / "nexa_consent_engine.py"
        self.assertFalse(
            legacy_path.exists(), f"Legacy engine file still exists at {legacy_path}"
        )

    def test_v2_consent_surface_uses_at_most_one_family(self):
        """Must always pass. Tripwire against drift. A single v2 file
        should never import more than one consent family (e.g.
        ConsentEngine plus a resurrected old module)."""
        found = _scan_all()
        multi_family = {
            path: families for path, families in found.items() if len(families) > 1
        }

        self.assertFalse(
            multi_family,
            f"Multiple consent families in the same v2 production file: "
            f"{multi_family}. See docs/CURRENT-STATE.md Section 1.",
        )


class TestAntiDriftGuardrails(unittest.TestCase):
    """Anti-drift guardrails for Days 3-5 alpha execution."""

    def test_frontend_features_no_direct_fetch_or_axios(self):
        """AST guardrail: no frontend feature file imports fetch or axios directly."""
        features_dir = REPO_ROOT / "nexa-client" / "packages" / "app" / "features"
        if not features_dir.exists():
            return
        offenders = []
        for path in features_dir.rglob("*.tsx"):
            text = path.read_text(encoding="utf-8")
            lines = [
                line.strip()
                for line in text.splitlines()
                if not line.strip().startswith("//")
            ]
            for idx, line in enumerate(lines, 1):
                if (
                    "import axios" in line
                    or "from 'axios'" in line
                    or 'from "axios"' in line
                ):
                    offenders.append(f"{path.name}:{idx} imports axios")
                if "fetch(" in line or "await fetch(" in line:
                    offenders.append(f"{path.name}:{idx} calls fetch directly")
        for path in features_dir.rglob("*.ts"):
            text = path.read_text(encoding="utf-8")
            lines = [
                line.strip()
                for line in text.splitlines()
                if not line.strip().startswith("//")
            ]
            for idx, line in enumerate(lines, 1):
                if (
                    "import axios" in line
                    or "from 'axios'" in line
                    or 'from "axios"' in line
                ):
                    offenders.append(f"{path.name}:{idx} imports axios")
                if "fetch(" in line or "await fetch(" in line:
                    offenders.append(f"{path.name}:{idx} calls fetch directly")
        self.assertEqual(
            offenders, [], f"Frontend feature files must use apiClient: {offenders}"
        )

    def test_backend_patient_data_routes_require_consent(self):
        """AST guardrail: no backend patient-data route lacks require_consent dependency."""
        api_v2_dir = REPO_ROOT / "app" / "api" / "v2"
        offenders = []
        for path in api_v2_dir.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in tree.body:
                if isinstance(node, ast.AsyncFunctionDef) or isinstance(
                    node, ast.FunctionDef
                ):
                    route_dec = None
                    for dec in node.decorator_list:
                        if isinstance(dec, ast.Call) and isinstance(
                            dec.func, ast.Attribute
                        ):
                            if dec.func.attr in (
                                "get",
                                "post",
                                "put",
                                "patch",
                                "delete",
                            ):
                                if dec.args and isinstance(dec.args[0], ast.Constant):
                                    route_dec = dec.args[0].value
                    if route_dec and (
                        "/patient/" in route_dec
                        or "/pipeline/" in route_dec
                        or "record" in route_dec
                    ):
                        func_text = ast.unparse(node)
                        has_gate = (
                            "require_consent" in func_text
                            or "require_active_consent" in func_text
                            or "require_self_patient_access" in func_text
                            or "require_role" in func_text
                            or "consent_gated_decrypt" in func_text
                        )
                        if not has_gate:
                            offenders.append(
                                f"{path.name}::{node.name} ({route_dec}) lacks consent/role gate"
                            )
        self.assertEqual(
            offenders,
            [],
            f"Patient/pipeline endpoints must use consent/role gate: {offenders}",
        )

    def test_no_hardcoded_localhost(self):
        """Guardrail: no hardcoded localhost or 127.0.0.1 anywhere in app/ or nexa-client/ source files."""
        scanned_dirs = [REPO_ROOT / "app", REPO_ROOT / "nexa-client" / "packages"]
        offenders = []
        for d in scanned_dirs:
            if not d.exists():
                continue
            for ext in ("*.py", "*.ts", "*.tsx"):
                for path in d.rglob(ext):
                    if "node_modules" in path.parts:
                        continue
                    text = path.read_text(encoding="utf-8")
                    for idx, line in enumerate(text.splitlines(), 1):
                        stripped = line.strip()
                        if stripped.startswith("#") or stripped.startswith("//"):
                            continue
                        if (
                            "http://localhost" in line
                            or "http://127.0.0.1" in line
                            or "https://localhost" in line
                        ):
                            offenders.append(
                                f"{path.relative_to(REPO_ROOT)}:{idx} has hardcoded localhost"
                            )
        self.assertEqual(offenders, [], f"Hardcoded localhost found: {offenders}")

    def test_no_hardcoded_provider_id_placeholders(self):
        """Guardrail: no hardcoded dummy provider_id placeholder strings."""
        scanned_dirs = [REPO_ROOT / "app"]
        offenders = []
        dummy_strings = [
            "00000000-0000-0000-0000-000000000000",
            "test_provider_id",
            "dummy_provider",
        ]
        for d in scanned_dirs:
            for path in d.rglob("*.py"):
                if path.name.startswith("test_"):
                    continue
                text = path.read_text(encoding="utf-8")
                for idx, line in enumerate(text.splitlines(), 1):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    for ds in dummy_strings:
                        if ds in line:
                            offenders.append(
                                f"{path.relative_to(REPO_ROOT)}:{idx} has dummy string '{ds}'"
                            )
        self.assertEqual(
            offenders, [], f"Hardcoded provider_id placeholders found: {offenders}"
        )

    def test_no_private_key_material_in_tracked_files(self):
        """Guardrail: no private key material or PEM private headers in source or documentation files."""
        scanned_dirs = [
            REPO_ROOT / "app",
            REPO_ROOT / "docs",
            REPO_ROOT / "scripts",
            REPO_ROOT / "nexa-client" / "packages",
        ]
        offenders = []
        private_headers = [
            "BEGIN PRIVATE KEY",
            "BEGIN RSA PRIVATE KEY",
            "BEGIN EC PRIVATE KEY",
        ]
        for d in scanned_dirs:
            if not d.exists():
                continue
            for ext in ("*.py", "*.ts", "*.tsx", "*.md", "*.json", "*.sh"):
                for path in d.rglob(ext):
                    text = path.read_text(encoding="utf-8")
                    for idx, line in enumerate(text.splitlines(), 1):
                        for ph in private_headers:
                            if ph in line:
                                offenders.append(
                                    f"{path.relative_to(REPO_ROOT)}:{idx} contains private key header '{ph}'"
                                )
        self.assertEqual(
            offenders,
            [],
            f"Private key material detected in tracked source files: {offenders}",
        )


if __name__ == "__main__":
    unittest.main()
