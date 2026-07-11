#!/usr/bin/env python3
"""Ground-Truth AI Engine Evaluation (Day 14).

Runs the **actual** WS5 engine (validator, scorer, classifier, conflict
detector, auto-approver) against a comprehensive ground-truth test set
and measures real accuracy per component and per field type.

No synthetic accuracy numbers — every metric comes from running the code.

Components evaluated
--------------------
1. **Validation accuracy** — does ``validate_field()`` produce the
   correct ``is_valid`` + ``validation_errors`` for known inputs?
2. **Risk classification accuracy** — does ``classify_risk()`` return
   the correct risk tier?
3. **Auto-approval decision accuracy** — does ``should_auto_approve()``
   make the correct GO / NO-GO call?
4. **Conflict detection accuracy** — does ``detect_conflicts()`` flag
   the right pairs and stay silent on non-conflicts?
5. **End-to-end pipeline accuracy** — given a full field with all
   metadata, does the pipeline produce the correct final status?

Usage::

    python scripts/evaluate_ground_truth.py

Exit 0 if every component ≥ 97 % accuracy, 1 otherwise.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ai.auto_approval import should_auto_approve  # noqa: E402
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

ACCURACY_FLOOR = 0.97

# ═══════════════════════════════════════════════════════════════════════════════
# GROUND-TRUTH TEST CASES
# ═══════════════════════════════════════════════════════════════════════════════

# Each case: (field_name, raw_value, expected_is_valid, expected_errors_substrings)
# expected_errors_substrings: list of substrings that MUST appear in validation_errors
#   if is_valid=False; empty list if is_valid=True.
VALIDATION_CASES: list[tuple[str, str, bool, list[str]]] = [
    # ── BP: valid ────────────────────────────────────────────────────────────
    ("bp", "120/80 mmHg", True, []),
    ("bp", "120/80", True, []),
    ("bp", "90/60 mmHg", True, []),
    ("bp", "180/110 mmHg", True, []),
    ("blood_pressure", "140/90", True, []),
    # ── BP: invalid ──────────────────────────────────────────────────────────
    ("bp", "1/80", False, ["Invalid blood pressure"]),
    ("bp", "120", False, ["Invalid blood pressure"]),
    ("bp", "120-80", False, ["Invalid blood pressure"]),
    ("bp", "hello", False, ["Invalid blood pressure"]),
    ("bp", "", False, ["Invalid blood pressure"]),
    # ── Sugar: valid, normal ─────────────────────────────────────────────────
    ("sugar", "Fasting Glucose 95 mg/dL", True, []),
    ("sugar", "80 mg/dL", True, []),
    ("sugar", "70 mg/dL", True, []),  # boundary
    ("sugar", "100 mg/dL", True, []),  # boundary
    ("fasting_glucose", "85 mg/dL", True, []),
    # ── Sugar: valid, abnormal ───────────────────────────────────────────────
    ("sugar", "Fasting Glucose 110 mg/dL", True, []),  # abnormal but valid
    ("sugar", "65 mg/dL", True, []),  # below range but valid
    # ── Sugar: invalid ───────────────────────────────────────────────────────
    ("sugar", "high", False, ["Missing numeric"]),
    ("sugar", "95", False, ["Missing numeric"]),  # no unit
    # ── HbA1c ────────────────────────────────────────────────────────────────
    ("hba1c", "5.2 %", True, []),
    ("hba1c", "6.5 %", True, []),  # abnormal but valid
    ("hba1c", "normal", False, ["Missing numeric"]),
    # ── Medication: complete (3 components) ──────────────────────────────────
    ("medication", "Metformin 500mg twice daily", True, []),
    ("medication", "Amlodipine 5mg daily", True, []),
    ("medication", "Insulin 10 units daily", True, []),
    ("medication", "Amoxicillin 250mg tid", True, []),
    ("prescription", "Atorvastatin 20mg once daily", True, []),
    ("drug", "Omeprazole 20mg morning", True, []),
    ("medication", "Ibuprofen 400mg prn", True, []),
    ("medication", "Levothyroxine 50 mcg daily", True, []),
    # ── Medication: incomplete ───────────────────────────────────────────────
    ("medication", "Metformin 500mg", False, ["frequency missing"]),
    ("medication", "Metformin", False, ["frequency missing", "strength missing"]),
    ("medication", "500mg daily", False, ["fuzzy match low"]),
    ("prescription", "Aspirin", False, ["frequency missing", "strength missing"]),
    # ── Dosage: strength + frequency (no drug match) ────────────────────────
    ("dosage", "500mg twice daily", True, []),
    ("dosage", "500mg", False, ["frequency missing"]),
    ("dosage", "daily", False, ["strength missing"]),
    ("strength", "10 mg daily", True, []),
    ("frequency", "twice daily 500mg", True, []),
    # ── Date ─────────────────────────────────────────────────────────────────
    ("date", "2024-01-15", True, []),
    ("date", "2024-06-01T10:30:00", True, []),
    ("dob", "1990-03-20", True, []),
    ("date", "2099-01-01", False, ["future"]),
    ("date", "not a date", False, ["Unparseable"]),
    # ── Allergy ──────────────────────────────────────────────────────────────
    ("allergy", "Penicillin", True, []),  # allergy has no special validation
    ("allergy", "Peanuts", True, []),
]

# Each case: (field_name, normalized_value, validation_result_or_None, expected_risk)
RISK_CASES: list[tuple[str, str, Any, str]] = [
    # ── Base tiers ───────────────────────────────────────────────────────────
    ("bp", "120/80", None, "MEDIUM_RISK"),
    ("sugar", "95 mg/dL", None, "MEDIUM_RISK"),
    ("medication", "Metformin 500mg", None, "HIGH_RISK"),
    ("allergy", "Penicillin", None, "HIGH_RISK"),
    ("allergen", "Peanuts", None, "HIGH_RISK"),
    ("patient_name", "Aarav", None, "LOW_RISK"),
    ("dob", "1990-01-01", None, "LOW_RISK"),
    ("hba1c", "5.2%", None, "MEDIUM_RISK"),
    # ── Allergy special-casing ───────────────────────────────────────────────
    ("allergy", "anaphylaxis shock", None, "CRITICAL_RISK"),
    ("allergy", "critical reaction", None, "CRITICAL_RISK"),
    # ── Escalation: abnormal lab ─────────────────────────────────────────────
    ("sugar", "high", {"reference_range": {"is_abnormal": True}}, "HIGH_RISK"),
    ("bp", "120/80", {"reference_range": {"is_abnormal": True}}, "HIGH_RISK"),
    # ── Escalation: validation failure ───────────────────────────────────────
    ("bp", "120/80", {"is_valid": False}, "HIGH_RISK"),
    ("sugar", "95", {"is_valid": False}, "HIGH_RISK"),
    # ── Escalation: conflict ─────────────────────────────────────────────────
    ("bp", "120/80", {"has_conflict": True}, "HIGH_RISK"),
]

# Each case: (field_name, raw_value, confidence, risk_level, expected_auto_approve)
AUTO_APPROVAL_CASES: list[tuple[str, str, float, str, bool]] = [
    # ── LOW_RISK threshold ≥ 0.95 ───────────────────────────────────────────
    ("patient_name", "Aarav Sharma", 0.97, "LOW_RISK", True),
    ("patient_name", "Aarav Sharma", 0.95, "LOW_RISK", True),
    ("patient_name", "Aarav Sharma", 0.94, "LOW_RISK", False),
    # ── MEDIUM_RISK threshold ≥ 0.97 ────────────────────────────────────────
    ("bp", "120/80 mmHg", 0.98, "MEDIUM_RISK", True),
    ("bp", "120/80 mmHg", 0.97, "MEDIUM_RISK", True),
    ("bp", "120/80 mmHg", 0.96, "MEDIUM_RISK", False),
    ("sugar", "95 mg/dL", 0.99, "MEDIUM_RISK", True),
    # ── HIGH_RISK: never ─────────────────────────────────────────────────────
    ("medication", "Metformin 500mg twice daily", 0.99, "HIGH_RISK", False),
    ("medication", "Aspirin 75mg daily", 1.00, "HIGH_RISK", False),
    # ── CRITICAL_RISK: never ─────────────────────────────────────────────────
    ("allergy", "anaphylaxis", 0.99, "CRITICAL_RISK", False),
    # ── Allergy forced HIGH_RISK ─────────────────────────────────────────────
    ("allergy", "Penicillin", 0.99, "LOW_RISK", False),
    ("allergen", "Peanuts", 0.99, "LOW_RISK", False),
]

# ── End-to-end pipeline cases ────────────────────────────────────────────────
# (field_name, raw_value, normalized_value, extractor_confidence, expected_status)
PIPELINE_CASES: list[tuple[str, str, str, float, str]] = [
    ("bp", "120/80 mmHg", "120/80", 0.98, "auto_approved"),
    ("sugar", "Fasting Glucose 95 mg/dL", "95 mg/dL", 0.98, "auto_approved"),
    ("medication", "Metformin 500mg twice daily", "500mg twice daily", 0.90, "needs_review"),
    ("allergy", "Penicillin", "Penicillin", 0.99, "needs_review"),
    ("hba1c", "5.2 %", "5.2%", 0.97, "auto_approved"),
    ("medication", "Aspirin 75mg daily", "75mg daily", 0.95, "needs_review"),
    ("allergy", "Peanuts", "Peanuts", 0.98, "needs_review"),
]


# ═══════════════════════════════════════════════════════════════════════════════
# EVALUATION RUNNERS
# ═══════════════════════════════════════════════════════════════════════════════

def _evaluate_validation() -> dict[str, Any]:
    """Run validation engine against ground truth and measure accuracy."""
    correct = 0
    total = len(VALIDATION_CASES)
    per_type: dict[str, dict[str, int]] = {}

    for fname, val, exp_valid, exp_err_subs in VALIDATION_CASES:
        result = validate_field(fname, val)

        # Check is_valid
        valid_ok = result.is_valid == exp_valid

        # Check error substrings appear
        err_ok = True
        if not exp_valid:
            for substr in exp_err_subs:
                if not any(substr.lower() in e.lower() for e in result.validation_errors):
                    err_ok = False
                    break

        match = valid_ok and err_ok
        if match:
            correct += 1

        cat = fname
        per_type.setdefault(cat, {"correct": 0, "total": 0})
        per_type[cat]["total"] += 1
        if match:
            per_type[cat]["correct"] += 1

    accuracy = correct / total if total else 0.0
    per_type_acc = {
        k: round(v["correct"] / v["total"], 4) if v["total"] else 0.0
        for k, v in per_type.items()
    }
    return {"accuracy": round(accuracy, 4), "correct": correct, "total": total, "per_type": per_type_acc}


def _evaluate_risk() -> dict[str, Any]:
    """Run risk classifier against ground truth and measure accuracy."""
    correct = 0
    total = len(RISK_CASES)
    per_type: dict[str, dict[str, int]] = {}

    for fname, val, vr, exp_risk in RISK_CASES:
        result = classify_risk(fname, val, vr)
        match = result == exp_risk
        if match:
            correct += 1

        cat = fname
        per_type.setdefault(cat, {"correct": 0, "total": 0})
        per_type[cat]["total"] += 1
        if match:
            per_type[cat]["correct"] += 1

    accuracy = correct / total if total else 0.0
    per_type_acc = {
        k: round(v["correct"] / v["total"], 4) if v["total"] else 0.0
        for k, v in per_type.items()
    }
    return {"accuracy": round(accuracy, 4), "correct": correct, "total": total, "per_type": per_type_acc}


def _evaluate_auto_approval() -> dict[str, Any]:
    """Run auto-approval engine against ground truth and measure accuracy."""
    correct = 0
    total = len(AUTO_APPROVAL_CASES)

    for fname, val, conf, risk, exp_approve in AUTO_APPROVAL_CASES:
        field = ExtractedField(
            field_id="eval-aa",
            job_id="eval-job",
            field_name=fname,
            raw_value=val,
            confidence=conf,
            risk_level=risk,
        )
        decision = should_auto_approve(field)
        if decision.auto_approve == exp_approve:
            correct += 1

    accuracy = correct / total if total else 0.0
    return {"accuracy": round(accuracy, 4), "correct": correct, "total": total}


def _evaluate_conflicts() -> dict[str, Any]:
    """Run conflict detector on known conflict / no-conflict batches."""
    # Batch 1: same sugar values → no conflict
    f1 = ExtractedField(field_id="c1", job_id="j1", field_name="sugar",
                        raw_value="95 mg/dL", normalized_value="95")
    f2 = ExtractedField(field_id="c2", job_id="j1", field_name="sugar",
                        raw_value="96 mg/dL", normalized_value="96")
    no_conflict_batch = [f1, f2]
    r1 = detect_conflicts(no_conflict_batch)
    no_conflict_ok = len(r1) == 0

    # Batch 2: discrepant sugar → conflict
    f3 = ExtractedField(field_id="c3", job_id="j1", field_name="sugar",
                        raw_value="95 mg/dL", normalized_value="95")
    f4 = ExtractedField(field_id="c4", job_id="j1", field_name="sugar",
                        raw_value="130 mg/dL", normalized_value="130")
    conflict_batch = [f3, f4]
    r2 = detect_conflicts(conflict_batch)
    conflict_ok = len(r2) >= 1

    # Batch 3: same BP → no conflict
    f5 = ExtractedField(field_id="c5", job_id="j1", field_name="bp",
                        raw_value="120/80 mmHg", normalized_value="120/80")
    f6 = ExtractedField(field_id="c6", job_id="j1", field_name="bp",
                        raw_value="120/80", normalized_value="120/80")
    r3 = detect_conflicts([f5, f6])
    bp_no_conflict_ok = len(r3) == 0

    # Batch 4: penicillin allergy + amoxicillin → contraindication
    f7 = ExtractedField(field_id="c7", job_id="j1", field_name="allergy",
                        raw_value="Penicillin", normalized_value="Penicillin")
    f8 = ExtractedField(field_id="c8", job_id="j1", field_name="medication",
                        raw_value="Amoxicillin 250mg tid", normalized_value="250mg tid")
    r4 = detect_conflicts([f7, f8])
    contra_ok = len(r4) >= 1 and r4[0].conflict_type == "CONTRAINDICATION"

    # Batch 5: penicillin allergy + metformin → no conflict (different class)
    f9 = ExtractedField(field_id="c9", job_id="j1", field_name="allergy",
                        raw_value="Penicillin", normalized_value="Penicillin")
    f10 = ExtractedField(field_id="c10", job_id="j1", field_name="medication",
                         raw_value="Metformin 500mg twice daily", normalized_value="500mg twice daily")
    r5 = detect_conflicts([f9, f10])
    no_cross_react_ok = len(r5) == 0

    results = {
        "no_conflict_sugar": no_conflict_ok,
        "conflict_sugar_discrepancy": conflict_ok,
        "no_conflict_bp_normalization": bp_no_conflict_ok,
        "contraindication_penicillin_amoxicillin": contra_ok,
        "no_false_cross_reactivity": no_cross_react_ok,
    }
    correct = sum(1 for v in results.values() if v)
    total = len(results)
    return {"accuracy": round(correct / total, 4), "correct": correct, "total": total, "details": results}


def _evaluate_pipeline() -> dict[str, Any]:
    """Run full pipeline (score → classify → auto-approve) on ground truth."""
    correct = 0
    total = len(PIPELINE_CASES)
    per_type: dict[str, dict[str, int]] = {}

    for fname, raw, norm, conf, exp_status in PIPELINE_CASES:
        field = ExtractedField(
            field_id="eval-pipe",
            job_id="eval-job",
            field_name=fname,
            raw_value=raw,
            normalized_value=norm,
            confidence=conf,
            risk_level="MEDIUM_RISK",  # will be overridden by scoring engine
        )
        scored = score_extracted_field(field)
        decision = should_auto_approve(scored)
        actual_status = "auto_approved" if decision.auto_approve else "needs_review"

        match = actual_status == exp_status
        if match:
            correct += 1

        cat = fname
        per_type.setdefault(cat, {"correct": 0, "total": 0})
        per_type[cat]["total"] += 1
        if match:
            per_type[cat]["correct"] += 1

    accuracy = correct / total if total else 0.0
    per_type_acc = {
        k: round(v["correct"] / v["total"], 4) if v["total"] else 0.0
        for k, v in per_type.items()
    }
    return {"accuracy": round(accuracy, 4), "correct": correct, "total": total, "per_type": per_type_acc}


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    print()
    print(f"{BOLD}{'═' * 78}{RESET}")
    print(f"{BOLD}  📊  NEXA CARE — GROUND-TRUTH AI ENGINE EVALUATION{RESET}")
    print(f"{BOLD}{'═' * 78}{RESET}")
    print(f"  {DIM}Running actual WS5 engine against ground-truth test cases{RESET}")
    print()

    val = _evaluate_validation()
    risk = _evaluate_risk()
    auto = _evaluate_auto_approval()
    conflict = _evaluate_conflicts()
    pipe = _evaluate_pipeline()

    # ── Print results ────────────────────────────────────────────────────────
    components = [
        ("Validation Engine", val),
        ("Risk Classifier", risk),
        ("Auto-Approval Engine", auto),
        ("Conflict Detector", conflict),
        ("Full Pipeline (E2E)", pipe),
    ]

    all_pass = True

    for name, result in components:
        acc = result["accuracy"]
        tag = f"{GREEN}{acc:.1%}{RESET}" if acc >= ACCURACY_FLOOR else f"{RED}{acc:.1%}{RESET}"
        status = f"{GREEN}✅ PASS{RESET}" if acc >= ACCURACY_FLOOR else f"{RED}❌ FAIL{RESET}"
        if acc < ACCURACY_FLOOR:
            all_pass = False

        print(f"  {BOLD}{name:<30}{RESET}  accuracy={tag}  "
              f"({result['correct']}/{result['total']})  {status}")

        if "per_type" in result:
            for tname, tacc in sorted(result["per_type"].items()):
                ttag = f"{GREEN}{tacc:.1%}{RESET}" if tacc >= ACCURACY_FLOOR else f"{RED}{tacc:.1%}{RESET}"
                tstatus = "✅" if tacc >= ACCURACY_FLOOR else "❌"
                print(f"    {DIM}├─{RESET} {tname:<18} {ttag}  {tstatus}")

        if "details" in result:
            for dname, dok in result["details"].items():
                dtag = f"{GREEN}PASS{RESET}" if dok else f"{RED}FAIL{RESET}"
                print(f"    {DIM}├─{RESET} {dname:<42} {dtag}")

    # ── Overall ──────────────────────────────────────────────────────────────
    overall_correct = sum(r["correct"] for _, r in components)
    overall_total = sum(r["total"] for _, r in components)
    overall_acc = overall_correct / overall_total if overall_total else 0.0

    print()
    print(f"  {BOLD}{'─' * 72}{RESET}")
    overall_tag = f"{GREEN}{overall_acc:.1%}{RESET}" if overall_acc >= ACCURACY_FLOOR else f"{RED}{overall_acc:.1%}{RESET}"
    print(f"  {BOLD}OVERALL ENGINE ACCURACY{RESET}  {overall_tag}  "
          f"({overall_correct}/{overall_total})")
    print(f"  {BOLD}MEDICAL-GRADE THRESHOLD{RESET}   {ACCURACY_FLOOR:.0%}")
    print()

    # ── Regenerate docs/extraction-accuracy-report.md with real numbers ─────
    _write_real_report(val, risk, auto, conflict, pipe, overall_acc, overall_correct, overall_total)

    if all_pass and overall_acc >= ACCURACY_FLOOR:
        print(f"{BOLD}{GREEN}  ╔══════════════════════════════════════╗")
        print("  ║     🟢  ALL COMPONENTS ≥ 97% — GO    ║")
        print(f"  ╚══════════════════════════════════════╝{RESET}")
        print()
        sys.exit(0)
    else:
        print(f"{BOLD}{RED}  ╔══════════════════════════════════════╗")
        print("  ║  🔴  BELOW 97% — FIX BEFORE DEMO     ║")
        print(f"  ╚══════════════════════════════════════╝{RESET}")
        print()
        sys.exit(1)


def _write_real_report(
    val: dict[str, Any],
    risk: dict[str, Any],
    auto: dict[str, Any],
    conflict: dict[str, Any],
    pipe: dict[str, Any],
    overall_acc: float,
    overall_correct: int,
    overall_total: int,
) -> None:
    """Overwrite docs/extraction-accuracy-report.md with real measured data."""
    from datetime import datetime, timezone

    lines: list[str] = []
    lines.append("# Extraction Accuracy Report — Ground-Truth Evaluation")
    lines.append("")
    lines.append(f"**Generated at:** {datetime.now(timezone.utc).isoformat()}")
    lines.append("")
    lines.append("## Measured Engine Accuracy")
    lines.append("")
    lines.append("| Component | Accuracy | Correct / Total | Status |")
    lines.append("|---|---|---|---|")

    components = [
        ("Validation Engine", val),
        ("Risk Classifier", risk),
        ("Auto-Approval Engine", auto),
        ("Conflict Detector", conflict),
        ("Full Pipeline (E2E)", pipe),
    ]

    for name, result in components:
        acc = result["accuracy"]
        status = "✅ PASS" if acc >= ACCURACY_FLOOR else "❌ BELOW 97%"
        lines.append(f"| {name} | {acc:.1%} | {result['correct']} / {result['total']} | {status} |")

    lines.append("")

    # Per-type breakdowns
    lines.append("## Per-Field-Type Accuracy")
    lines.append("")

    for name, result in components:
        if "per_type" not in result:
            continue
        lines.append(f"### {name}")
        lines.append("")
        lines.append("| Field Type | Accuracy | Status |")
        lines.append("|---|---|---|")
        for tname, tacc in sorted(result["per_type"].items()):
            status = "✅ PASS" if tacc >= ACCURACY_FLOOR else "❌ BELOW 97%"
            lines.append(f"| {tname} | {tacc:.1%} | {status} |")
        lines.append("")

    # Conflict detector details
    if "details" in conflict:
        lines.append("### Conflict Detector — Detailed Results")
        lines.append("")
        lines.append("| Test Case | Result |")
        lines.append("|---|---|")
        for dname, dok in conflict["details"].items():
            status = "✅ PASS" if dok else "❌ FAIL"
            lines.append(f"| {dname} | {status} |")
        lines.append("")

    # Overall
    lines.append("## Overall Engine Accuracy")
    lines.append("")
    overall_status = "✅ MEDICAL READY" if overall_acc >= ACCURACY_FLOOR else "❌ NOT READY"
    lines.append(f"**{overall_acc:.1%}** ({overall_correct}/{overall_total}) — {overall_status}")
    lines.append("")
    lines.append(f"Medical-grade threshold: ≥ {ACCURACY_FLOOR:.0%}")
    lines.append("")

    # Methodology
    lines.append("## Methodology")
    lines.append("")
    lines.append("All metrics are **measured** by running the actual WS5 engine code")
    lines.append("against a ground-truth test set — no synthetic or extrapolated numbers.")
    lines.append("")
    lines.append("- **Validation accuracy**: `validate_field()` produces correct")
    lines.append("  `is_valid` + `validation_errors` for known inputs.")
    lines.append("- **Risk classification accuracy**: `classify_risk()` returns the")
    lines.append("  correct risk tier for known field/value/validation combos.")
    lines.append("- **Auto-approval accuracy**: `should_auto_approve()` makes the")
    lines.append("  correct GO/NO-GO decision for known risk/confidence combos.")
    lines.append("- **Conflict detection accuracy**: `detect_conflicts()` correctly")
    lines.append("  flags or stays silent on known conflict/no-conflict batches.")
    lines.append("- **Full pipeline accuracy**: `score_extracted_field()` →")
    lines.append("  `should_auto_approve()` produces the correct final status.")
    lines.append("")

    output_path = ROOT / "docs" / "extraction-accuracy-report.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  {DIM}Report written to {output_path}{RESET}")


if __name__ == "__main__":
    main()
