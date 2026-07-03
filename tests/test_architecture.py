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

# The "v2 production consent surface". Legacy v1 (app/api/routes.py,
# app/core/redis.py's issue/resolve/revoke_consent_token) is intentionally
# excluded: its full deprecation is tracked as a whole system in
# docs/CURRENT-STATE.md, not as intra-v2 drift, and it is already known and
# accepted to coexist with the v2 systems during Phase 0/1.
V2_SCANNED_FILES = [
    "app/api/v2/consent_routes.py",
    "app/api/v2/patient_routes.py",
    "app/api/v2/fhir_routes.py",
    "app/api/v2/document_routes.py",
    "app/api/v2/review_routes.py",
    "app/api/v2/emergency_routes.py",
    "app/api/v2/auth_routes.py",
    "app/api/v2/nfc_routes.py",
    "app/core/dependencies.py",
]

# Module path -> consent "family" name. Any import whose module equals or
# is nested under one of these prefixes counts as a use of that family.
FAMILY_MODULE_PREFIXES = {
    "app.services.consent_service": "consent_service",
    "app.services.consent.routine": "routine",
    "app.services.consent.break_glass": "break_glass",
    "app.services.consent_engine": "consent_engine",
}


def _families_imported(file_path: Path) -> set[str]:
    tree = ast.parse(file_path.read_text(), filename=str(file_path))
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
        removed_families = {"consent_service", "routine", "break_glass"}
        offenders = {
            path: families & removed_families
            for path, families in found.items()
            if families & removed_families
        }

        self.assertFalse(
            offenders,
            f"Removed consent families (consent_service/routine/break_glass) "
            f"found in v2 production files: {offenders}. "
            f"See docs/CURRENT-STATE.md Section 1.",
        )

    def test_v2_consent_surface_uses_at_most_one_family(self):
        """Must always pass. Tripwire against drift. A single v2 file
        should never import more than one consent family (e.g.
        ConsentEngine plus a resurrected old module)."""
        found = _scan_all()
        multi_family = {path: families for path, families in found.items() if len(families) > 1}

        self.assertFalse(
            multi_family,
            f"Multiple consent families in the same v2 production file: "
            f"{multi_family}. See docs/CURRENT-STATE.md Section 1.",
        )


if __name__ == "__main__":
    unittest.main()
