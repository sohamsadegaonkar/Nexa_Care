"""Architecture guardrails (Phase 0, docs/CURRENT-STATE.md Section 3).

Static, AST-based checks on which consent modules production route files
import. No running server or DB required.
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
    "app/core/dependencies.py",
]

# Module path -> consent "family" name. Any import whose module equals or
# is nested under one of these prefixes counts as a use of that family.
FAMILY_MODULE_PREFIXES = {
    "app.services.consent_service": "consent_service",
    "app.services.consent.routine": "routine",
    "app.services.consent.break_glass": "break_glass",
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
    """Guards against the exact bug that made
    GET /api/v2/patient/{id}/record permanently unreachable:
    consent_routes.py issues tokens via consent_service.py while
    patient_routes.py validates them via routine.py — two disjoint token
    systems (different Redis key prefixes, different payload schemas).
    A cheap static check on the import graph would have caught this the
    day it was introduced.
    """

    def test_v2_consent_surface_uses_exactly_one_family(self):
        """No longer @expectedFailure: app/api/v2/consent_routes.py and
        app/api/v2/patient_routes.py were migrated onto ConsentEngine
        (docs/CURRENT-STATE.md, "Patient reconstruction endpoint
        unreachable" — status updated to Fixed), so neither imports
        consent_service or routine anymore and this now genuinely passes
        instead of being an expected, tracked violation.

        app/core/dependencies.py still imports consent_service for
        require_active_consent (used by fhir_routes.py) — that's the one
        remaining family in the union below. It's a separate, not-yet-
        migrated leg of the same consent consolidation (see
        docs/CURRENT-STATE.md Section 4), not new drift, and doesn't
        collide with anything since routine is now gone entirely from the
        v2 production surface.
        """
        found = _scan_all()
        union: set[str] = set()
        for families in found.values():
            union |= families

        self.assertLessEqual(
            len(union), 1,
            f"Multiple consent families in production v2 routes: {found}. "
            f"See docs/CURRENT-STATE.md Section 1 (Consent Systems).",
        )

    def test_no_drift_beyond_the_one_currently_tracked_family(self):
        """Must always pass. Tripwire against the remaining known state
        getting WORSE. The only family still allowed in the v2 production
        surface is consent_service (via dependencies.py's
        require_active_consent, not yet migrated). routine is fully gone
        as of the consent_routes.py / patient_routes.py migration, so it
        is no longer an allowed family here either — if it reappears,
        that's a regression, not tracked debt. break_glass creeping in,
        or a brand new consent module appearing, must also fail CI
        immediately.
        """
        found = _scan_all()
        union: set[str] = set()
        for families in found.values():
            union |= families

        self.assertLessEqual(
            union, {"consent_service"},
            f"A consent family beyond the tracked {{consent_service}} "
            f"footprint is now present in production v2 routes: {found}. "
            f"This is new drift beyond docs/CURRENT-STATE.md and must be "
            f"resolved before merge.",
        )


if __name__ == "__main__":
    unittest.main()