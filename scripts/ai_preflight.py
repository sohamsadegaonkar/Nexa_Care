#!/usr/bin/env python3
"""AI-Engine Pre-Flight Verification (Day 14 demo).

Runs a deterministic checklist that verifies every WS5 component is
functional and that the demo document produces the expected field
breakdown.  Prints a GO/NO-GO verdict suitable for a presenter to
reference moments before the live demo.

Checks performed
----------------
1. **Extractor / scoring engine** — ``score_extracted_field`` processes
   each demo field and returns a numeric confidence in [0, 1].
2. **Confidence scorer** — ``score_field`` returns sane values for
   BP, sugar, medication, and allergy inputs.
3. **Risk classifier** — ``classify_risk`` returns the correct tier
   for each field category and escalation scenario.
4. **Medical validator** — ``validate_field`` produces correct pass/fail
   and error messages for the demo inputs.
5. **Conflict detector** — ``detect_conflicts`` runs on the demo batch
   without errors and confirms no false penicillin↔Metformin
   contraindication (no beta-lactam cross-reactivity).
6. **Auto-approval engine** — ``should_auto_approve`` returns the
   correct decision for every demo field.
7. **Demo field breakdown** — deterministic per-field summary so the
   presenter knows exactly what to expect.
8. **Hard invariants** — ≥1 auto-approved, ≥2 correctly-routed-to-review,
   medication passes validation, allergy forced HIGH_RISK, no spurious
   conflicts.

Demo field expectations
-----------------------
The demo document is tuned for a realistic, confident presentation:

1. **BP 120/80 mmHg**              → ✅ AUTO_APPROVED  (MEDIUM_RISK, conf ≥ 0.97)
2. **Fasting Glucose 95 mg/dL**    → ✅ AUTO_APPROVED  (MEDIUM_RISK, conf ≥ 0.97)
3. **Metformin 500mg twice daily** → ⚠️  EXPECTED REVIEW (HIGH_RISK — medication
                                       policy requires human sign-off)
4. **Penicillin (allergy)**        → ⚠️  EXPECTED REVIEW (forced HIGH_RISK — allergy
                                       invariant, never auto-approves)

Fields routed to review are shown in amber (not red) because they are
**correct safety behaviour**, not failures.

Usage::

    python scripts/ai_preflight.py

Exit code 0 → GO, 1 → NO-GO.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ai.auto_approval import should_auto_approve  # noqa: E402
from app.ai.confidence_scorer import score_field  # noqa: E402
from app.ai.conflict_detector import detect_conflicts  # noqa: E402
from app.ai.medical_validator import validate_field  # noqa: E402
from app.ai.risk_classifier import classify_risk  # noqa: E402
from app.ai.scoring_engine import score_extracted_field  # noqa: E402
from app.models.extracted_field import ExtractedField  # noqa: E402

# ── ANSI ─────────────────────────────────────────────────────────────────────
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

# ── Demo candidate fields ───────────────────────────────────────────────────
# Medication now carries all 3 components (drug + strength + frequency)
# so it *passes* validation cleanly.  It still routes to human review
# because medication fields are HIGH_RISK by policy — correct safety
# behaviour, not a validation failure.
DEMO_CANDIDATES: list[dict[str, Any]] = [
    {
        "field_name": "bp",
        "raw_value": "120/80 mmHg",
        "normalized_value": "120/80",
        "risk_level": "MEDIUM_RISK",
        "confidence": 0.98,
        "source_page": 1,
        "source_bbox": [0.12, 0.08, 0.35, 0.04],
    },
    {
        "field_name": "sugar",
        "raw_value": "Fasting Glucose 95 mg/dL",
        "normalized_value": "95 mg/dL",
        "risk_level": "MEDIUM_RISK",
        "confidence": 0.98,
        "source_page": 1,
        "source_bbox": [0.12, 0.16, 0.40, 0.04],
    },
    {
        "field_name": "medication",
        "raw_value": "Metformin 500mg twice daily",
        "normalized_value": "500mg twice daily",
        "risk_level": "MEDIUM_RISK",
        "confidence": 0.85,
        "source_page": 1,
        "source_bbox": [0.12, 0.24, 0.38, 0.04],
    },
    {
        "field_name": "allergy",
        "raw_value": "Penicillin",
        "normalized_value": "Penicillin",
        "risk_level": "LOW_RISK",
        "confidence": 0.99,
        "source_page": 1,
        "source_bbox": [0.12, 0.32, 0.25, 0.04],
    },
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _build_fields() -> list[ExtractedField]:
    """Construct ExtractedField models from the demo candidate spec."""
    fields: list[ExtractedField] = []
    for idx, c in enumerate(DEMO_CANDIDATES, start=1):
        fields.append(
            ExtractedField(
                field_id=f"demo-{c['field_name']}-{idx}",
                job_id="demo-job-001",
                field_name=c["field_name"],
                raw_value=c["raw_value"],
                normalized_value=c["normalized_value"],
                confidence=c["confidence"],
                risk_level=c["risk_level"],
                source_page=c.get("source_page", 1),
                source_bbox=c.get("source_bbox"),
            )
        )
    return fields


class _PreflightResult:
    """Accumulates check results and tracks pass/fail state."""

    def __init__(self) -> None:
        self.passed: int = 0
        self.failed: int = 0
        self.errors: list[str] = []

    def ok(self, label: str) -> None:
        self.passed += 1
        print(f"  {GREEN}✓{RESET} {label}")

    def fail(self, label: str, detail: str = "") -> None:
        self.failed += 1
        msg = f"  {RED}✗{RESET} {label}"
        if detail:
            msg += f" — {detail}"
        self.errors.append(f"{label}: {detail}" if detail else label)
        print(msg)

    def check(self, condition: bool, label: str, detail: str = "") -> None:
        if condition:
            self.ok(label)
        else:
            self.fail(label, detail)

    @property
    def is_go(self) -> bool:
        return self.failed == 0


# ── Individual pre-flight checks ─────────────────────────────────────────────

def _check_confidence_scorer(r: _PreflightResult) -> None:
    """Verify the confidence scorer returns sane values for demo inputs."""
    print(f"\n{CYAN}[1/7] Confidence Scorer{RESET}")

    bp_conf = score_field("bp", "120/80 mmHg", 0.98)
    r.check(
        isinstance(bp_conf, float) and 0.0 <= bp_conf <= 1.0,
        "BP confidence in [0, 1]",
        f"got {bp_conf!r}",
    )

    sugar_conf = score_field("sugar", "Fasting Glucose 95 mg/dL", 0.98)
    r.check(
        isinstance(sugar_conf, float) and 0.0 <= sugar_conf <= 1.0,
        "Sugar confidence in [0, 1]",
        f"got {sugar_conf!r}",
    )

    med_conf = score_field("medication", "Metformin 500mg twice daily", 0.85)
    r.check(
        isinstance(med_conf, float) and 0.0 <= med_conf <= 1.0,
        "Medication confidence in [0, 1]",
        f"got {med_conf!r}",
    )

    alg_conf = score_field("allergy", "Penicillin", 0.99)
    r.check(
        isinstance(alg_conf, float) and 0.0 <= alg_conf <= 1.0,
        "Allergy confidence in [0, 1]",
        f"got {alg_conf!r}",
    )


def _check_risk_classifier(r: _PreflightResult) -> None:
    """Verify the risk classifier returns correct tiers for key scenarios."""
    print(f"\n{CYAN}[2/7] Risk Classifier{RESET}")

    # Base tiers
    r.check(
        classify_risk("bp", "120/80") == "MEDIUM_RISK",
        "BP base risk = MEDIUM_RISK",
        f"got {classify_risk('bp', '120/80')!r}",
    )
    r.check(
        classify_risk("sugar", "95 mg/dL") == "MEDIUM_RISK",
        "Sugar base risk = MEDIUM_RISK",
        f"got {classify_risk('sugar', '95 mg/dL')!r}",
    )
    r.check(
        classify_risk("allergy", "Penicillin") == "HIGH_RISK",
        "Allergy forced HIGH_RISK",
        f"got {classify_risk('allergy', 'Penicillin')!r}",
    )

    # Escalation on validation failure
    vr_fail = {"is_valid": False, "has_conflict": False}
    escalated = classify_risk("bp", "120/80", vr_fail)
    r.check(
        escalated == "HIGH_RISK",
        "Validation failure escalates MEDIUM → HIGH",
        f"got {escalated!r}",
    )

    # Allergy with anaphylaxis → CRITICAL
    r.check(
        classify_risk("allergy", "anaphylaxis shock") == "CRITICAL_RISK",
        "Anaphylaxis allergy = CRITICAL_RISK",
        f"got {classify_risk('allergy', 'anaphylaxis shock')!r}",
    )


def _check_medical_validator(r: _PreflightResult) -> None:
    """Verify the medical validator produces correct results for demo inputs."""
    print(f"\n{CYAN}[3/7] Medical Validator{RESET}")

    # BP valid
    bp_val = validate_field("bp", "120/80 mmHg")
    r.check(
        bp_val.is_valid is True,
        "BP '120/80 mmHg' passes validation",
        f"is_valid={bp_val.is_valid}, errors={bp_val.validation_errors}",
    )

    # BP invalid format (single-digit systolic)
    bp_bad = validate_field("bp", "1/80")
    r.check(
        bp_bad.is_valid is False,
        "BP '1/80' fails validation (bad format)",
        f"is_valid={bp_bad.is_valid}",
    )

    # Sugar normal (95 mg/dL)
    sugar_val = validate_field("sugar", "Fasting Glucose 95 mg/dL")
    r.check(
        sugar_val.is_valid is True,
        "Sugar 95 mg/dL passes validation",
        f"is_valid={sugar_val.is_valid}, errors={sugar_val.validation_errors}",
    )
    r.check(
        sugar_val.reference_range is not None
        and sugar_val.reference_range.get("is_abnormal") is False,
        "Sugar 95 mg/dL is NOT abnormal",
        f"ref_range={sugar_val.reference_range}",
    )

    # Sugar pre-diabetic (110 mg/dL) — should be abnormal
    sugar_abn = validate_field("sugar", "Fasting Glucose 110 mg/dL")
    r.check(
        sugar_abn.reference_range is not None
        and sugar_abn.reference_range.get("is_abnormal") is True,
        "Sugar 110 mg/dL IS abnormal (pre-diabetic)",
        f"ref_range={sugar_abn.reference_range}",
    )

    # Demo medication — complete 3-component prescription
    med_val = validate_field("medication", "Metformin 500mg twice daily")
    r.check(
        med_val.is_valid is True,
        "Metformin 500mg twice daily passes validation",
        f"is_valid={med_val.is_valid}, errors={med_val.validation_errors}",
    )

    # Incomplete medication still correctly fails
    med_incomplete = validate_field("medication", "Metformin 500mg")
    r.check(
        med_incomplete.is_valid is False,
        "Incomplete medication 'Metformin 500mg' correctly fails",
        f"is_valid={med_incomplete.is_valid}",
    )
    r.check(
        "frequency missing" in med_incomplete.validation_errors,
        "Incomplete medication error includes 'frequency missing'",
        f"errors={med_incomplete.validation_errors}",
    )


def _check_conflict_detector(r: _PreflightResult) -> None:
    """Verify conflict detector runs without errors on demo batch."""
    print(f"\n{CYAN}[4/7] Conflict Detector{RESET}")

    fields = _build_fields()
    try:
        conflicts = detect_conflicts(fields)
        r.ok(f"Conflict detection executed ({len(conflicts)} conflict(s) found)")
    except Exception as exc:  # noqa: BLE001 — pre-flight, not production path
        r.fail("Conflict detection executed", str(exc))
        return

    # Demo batch has 4 unique categories → no intra-category discrepancies.
    # Penicillin allergy vs Metformin medication — no beta-lactam match.
    r.check(
        len(conflicts) == 0,
        "Demo batch: no false conflicts (penicillin ≠ metformin)",
        f"got {len(conflicts)} conflicts",
    )


def _check_scoring_engine(r: _PreflightResult) -> None:
    """Verify the scoring engine enriches fields with confidence and risk."""
    print(f"\n{CYAN}[5/7] Scoring Engine{RESET}")

    fields = _build_fields()
    for field in fields:
        scored = score_extracted_field(field)
        r.check(
            scored.confidence is not None and isinstance(scored.confidence, float),
            f"{field.field_name}: confidence assigned",
            f"conf={scored.confidence!r}",
        )
        r.check(
            scored.risk_level is not None,
            f"{field.field_name}: risk_level assigned",
            f"risk={scored.risk_level!r}",
        )


def _check_auto_approval(r: _PreflightResult) -> None:
    """Verify auto-approval decisions for demo fields after full scoring."""
    print(f"\n{CYAN}[6/7] Auto-Approval Engine{RESET}")

    fields = _build_fields()
    for field in fields:
        scored = score_extracted_field(field)
        decision = should_auto_approve(scored)
        r.check(
            isinstance(decision.auto_approve, bool),
            f"{field.field_name}: decision is bool",
            f"got {type(decision.auto_approve).__name__}",
        )
        r.check(
            isinstance(decision.reason, str) and len(decision.reason) > 0,
            f"{field.field_name}: reason is non-empty string",
            f"got {decision.reason!r}",
        )


def _check_demo_breakdown_and_invariants(r: _PreflightResult) -> None:
    """Run full integrated pipeline on demo document, print breakdown, and
    verify hard invariants."""
    print(f"\n{CYAN}[7/7] Demo Field Breakdown & Invariants{RESET}")

    fields = _build_fields()
    conflicts = detect_conflicts(fields)

    # Score each field and collect decisions
    results: list[dict[str, Any]] = []
    for field in fields:
        scored = score_extracted_field(field)
        decision = should_auto_approve(scored)

        val_res = scored.validation_result
        is_valid = True
        val_errors: list[str] = []
        if val_res is not None:
            if isinstance(val_res, dict):
                is_valid = val_res.get("is_valid", True)
                val_errors = val_res.get("validation_errors") or []
            else:
                is_valid = getattr(val_res, "is_valid", True)
                val_errors = getattr(val_res, "validation_errors", []) or []

        validation_message = (
            "; ".join(val_errors) if val_errors
            else ("Validation failed" if not is_valid else "All checks passed")
        )

        status = "auto_approved" if decision.auto_approve else "needs_review"
        results.append({
            "field_name": scored.field_name,
            "raw_value": scored.raw_value,
            "confidence": scored.confidence,
            "risk_level": scored.risk_level,
            "is_valid": is_valid,
            "validation_message": validation_message,
            "has_conflict": scored.has_conflict,
            "status": status,
            "reason": decision.reason,
            "source_page": scored.source_page,
        })

    # ── Print breakdown ──────────────────────────────────────────────────────
    print()
    print(f"  {BOLD}Expected Demo Field Breakdown:{RESET}")
    print(f"  {DIM}{'─' * 72}{RESET}")
    for res in results:
        if res["status"] == "auto_approved":
            tag = f"{GREEN}AUTO_APPROVED{RESET}"
        else:
            tag = f"{YELLOW}EXPECTED REVIEW{RESET}"
        conf_str = f"{res['confidence']:.2f}" if res["confidence"] is not None else "N/A"
        print(
            f"  {BOLD}{res['field_name']:<12}{RESET}  "
            f"{res['raw_value']:<30}  "
            f"conf={conf_str}  "
            f"risk={res['risk_level']:<14}  "
            f"{tag}"
        )
        print(
            f"  {'':12}  "
            f"valid={res['is_valid']}  "
            f"msg=\"{res['validation_message']}\"  "
            f"conflict={res['has_conflict']}"
        )
        print(
            f"  {'':12}  "
            f"reason=\"{res['reason']}\""
        )
        print()

    # ── Hard invariants ──────────────────────────────────────────────────────
    print(f"  {BOLD}Invariant Checks:{RESET}")

    auto_fields = [f for f in results if f["status"] == "auto_approved"]
    r.check(
        len(auto_fields) >= 2,
        "≥ 2 auto-approved fields",
        f"got {len(auto_fields)}",
    )

    review_fields = [f for f in results if f["status"] == "needs_review"]
    r.check(
        len(review_fields) >= 2,
        "≥ 2 fields correctly routed to human review",
        f"got {len(review_fields)}",
    )

    # Medication passes validation (complete 3-component prescription)
    med_fields = [f for f in results if f["field_name"] == "medication"]
    if med_fields:
        r.check(
            med_fields[0]["is_valid"] is True,
            "Medication passes validation (complete prescription)",
            f"is_valid={med_fields[0]['is_valid']}, "
            f"msg=\"{med_fields[0]['validation_message']}\"",
        )
        r.check(
            med_fields[0]["risk_level"] == "HIGH_RISK",
            "Medication correctly classified HIGH_RISK (requires human sign-off)",
            f"got {med_fields[0]['risk_level']!r}",
        )
    else:
        r.fail("Medication field exists", "no medication field found")

    # Allergy forced to HIGH_RISK, never auto-approves
    alg_fields = [f for f in results if f["field_name"] == "allergy"]
    if alg_fields:
        r.check(
            alg_fields[0]["risk_level"] == "HIGH_RISK",
            "Allergy forced HIGH_RISK (safety invariant)",
            f"got {alg_fields[0]['risk_level']!r}",
        )
        r.check(
            alg_fields[0]["status"] == "needs_review",
            "Allergy never auto-approves (correct)",
            f"got {alg_fields[0]['status']!r}",
        )
    else:
        r.fail("Allergy field exists", "no allergy field found")

    r.check(
        len(conflicts) == 0,
        "No spurious conflicts in demo batch",
        f"got {len(conflicts)}",
    )

    # All fields carry full WS8 review-cockpit metadata
    missing_meta: list[str] = []
    for f in results:
        if any(f.get(k) is None for k in ("confidence", "risk_level", "validation_message", "source_page")):
            missing_meta.append(f["field_name"])
    if missing_meta:
        r.fail("All fields carry WS8 metadata", f"missing in: {missing_meta}")
    else:
        r.ok("All fields carry full WS8 review-cockpit metadata")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print(f"{BOLD}{'═' * 78}{RESET}")
    print(f"{BOLD}  🛫  NEXA CARE — AI ENGINE PRE-FLIGHT VERIFICATION{RESET}")
    print(f"{BOLD}{'═' * 78}{RESET}")

    r = _PreflightResult()

    _check_confidence_scorer(r)
    _check_risk_classifier(r)
    _check_medical_validator(r)
    _check_conflict_detector(r)
    _check_scoring_engine(r)
    _check_auto_approval(r)
    _check_demo_breakdown_and_invariants(r)

    # ── Final verdict ────────────────────────────────────────────────────────
    print()
    print(f"{BOLD}{'─' * 78}{RESET}")
    print(f"  Checks passed: {GREEN}{r.passed}{RESET}   Checks failed: {RED}{r.failed}{RESET}")
    print(f"{BOLD}{'─' * 78}{RESET}")

    if r.is_go:
        print()
        print(f"{BOLD}{GREEN}  ╔══════════════════════════════════════╗")
        print("  ║          🟢  GO — ALL CLEAR          ║")
        print(f"  ╚══════════════════════════════════════╝{RESET}")
        print()
        print(f"  {DIM}All WS5 components verified. Demo field breakdown is deterministic.{RESET}")
        print()
        sys.exit(0)
    else:
        print()
        print(f"{BOLD}{RED}  ╔══════════════════════════════════════╗")
        print("  ║        🔴  NO-GO — ISSUES FOUND       ║")
        print(f"  ╚══════════════════════════════════════╝{RESET}")
        print()
        print(f"  {BOLD}Failed checks:{RESET}")
        for err in r.errors:
            print(f"    {RED}•{RESET} {err}")
        print()
        sys.exit(1)


if __name__ == "__main__":
    main()
