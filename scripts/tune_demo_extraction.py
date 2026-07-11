#!/usr/bin/env python3
"""Demo Extraction Tuning Script (Days 12–13).

Runs the demo document through the full WS5 intelligence engine
(validation, scoring, conflict detection, auto-approval) and prints a
field-by-field breakdown suitable for the live demo preview.

Demo document candidate fields
------------------------------
The demo is deliberately tuned to produce a compelling, realistic mix:

1. **BP 120/80 mmHg**              → auto-approved (MEDIUM_RISK, conf ≥ 0.97)
2. **Fasting Glucose 95 mg/dL**    → auto-approved (MEDIUM_RISK, normal range)
3. **Metformin 500mg twice daily** → needs_review  (HIGH_RISK — medication
                                      policy requires human sign-off)
4. **Penicillin (allergy)**        → needs_review  (forced HIGH_RISK,
                                      never auto-approve)

The medication carries all 3 components (drug + strength + frequency)
so it passes validation cleanly.  It still routes to human review
because medication fields are HIGH_RISK by policy — correct safety
behaviour, not a validation failure.

Usage::

    python scripts/tune_demo_extraction.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ai.auto_approval import should_auto_approve  # noqa: E402
from app.ai.conflict_detector import detect_conflicts  # noqa: E402
from app.ai.scoring_engine import score_extracted_field  # noqa: E402
from app.models.extracted_field import ExtractedField  # noqa: E402

# ── ANSI helpers ─────────────────────────────────────────────────────────────
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
RESET = "\033[0m"

# ── Demo candidate fields ───────────────────────────────────────────────────
# These mirror the extraction output from Aarav Sharma's clinical panel
# and are tuned to demonstrate the full range of WS5 intelligence decisions.

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


def _extract_validation_info(
    field: ExtractedField,
) -> tuple[bool, list[str], dict[str, Any] | None]:
    """Return (is_valid, validation_errors, reference_range) from any shape."""
    vr = field.validation_result
    if vr is None:
        return True, [], None
    if isinstance(vr, dict):
        return (
            vr.get("is_valid", True),
            vr.get("validation_errors") or [],
            vr.get("reference_range"),
        )
    return (
        getattr(vr, "is_valid", True),
        getattr(vr, "validation_errors", []) or [],
        getattr(vr, "reference_range", None),
    )


def run_demo_tuning() -> dict[str, Any]:
    """Execute the full WS5 engine on the demo document and return results."""
    fields = _build_fields()

    # ── Step 1: Conflict detection ──────────────────────────────────────────
    conflicts = detect_conflicts(fields)

    # ── Step 2: Score + validate + classify each field ─────────────────────
    results: list[dict[str, Any]] = []
    auto_count = 0
    review_count = 0

    for field in fields:
        scored = score_extracted_field(field)
        decision = should_auto_approve(scored)

        is_valid, val_errors, ref_range = _extract_validation_info(scored)

        # Validation message for WS8 field card
        if val_errors:
            validation_message = "; ".join(val_errors)
        elif not is_valid:
            validation_message = "Validation failed"
        else:
            validation_message = "All checks passed"

        if decision.auto_approve:
            auto_count += 1
            status = "auto_approved"
        else:
            review_count += 1
            status = "needs_review"

        results.append({
            "field_id": scored.field_id,
            "field_name": scored.field_name,
            "raw_value": scored.raw_value,
            "confidence": round(scored.confidence, 4) if scored.confidence else None,
            "risk_level": scored.risk_level,
            "is_valid": is_valid,
            "validation_message": validation_message,
            "reference_range": ref_range,
            "has_conflict": scored.has_conflict,
            "status": status,
            "auto_approve_reason": decision.reason,
            "source_page": scored.source_page,
            "source_bbox": scored.source_bbox,
        })

    return {
        "job_id": "demo-job-001",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "conflict_count": len(conflicts),
        "auto_approved_count": auto_count,
        "needs_review_count": review_count,
        "fields": results,
    }


def print_demo_report(report: dict[str, Any]) -> None:
    """Print a formatted field-by-field breakdown for demo preview."""
    print()
    print(f"{BOLD}{'=' * 78}{RESET}")
    print(f"{BOLD}  🔬 NEXA CARE — DEMO EXTRACTION TUNING REPORT{RESET}")
    print(f"{BOLD}{'=' * 78}{RESET}")
    print(f"  Job ID:             {report['job_id']}")
    print(f"  Generated at:       {report['generated_at']}")
    print(f"  Conflicts detected: {report['conflict_count']}")
    print(f"{BOLD}{'─' * 78}{RESET}")

    for f in report["fields"]:
        status_tag = (
            f"{GREEN}AUTO_APPROVED{RESET}"
            if f["status"] == "auto_approved"
            else f"{RED}NEEDS_REVIEW{RESET}"
        )

        print()
        print(f"  {BOLD}Field:{RESET}           {f['field_name']}")
        print(f"  {BOLD}Raw value:{RESET}       {f['raw_value']}")
        print(f"  {BOLD}Status:{RESET}          {status_tag}")
        print(f"  {BOLD}Confidence:{RESET}      {f['confidence']:.2f}" if f["confidence"] else f"  {BOLD}Confidence:{RESET}      N/A")
        print(f"  {BOLD}Risk level:{RESET}      {f['risk_level']}")
        print(f"  {BOLD}Valid:{RESET}           {f['is_valid']}")
        print(f"  {BOLD}Validation msg:{RESET}  {f['validation_message']}")
        print(f"  {BOLD}Conflict:{RESET}        {f['has_conflict']}")
        print(f"  {BOLD}Auto-approve reason:{RESET} {f['auto_approve_reason']}")
        print(f"  {BOLD}Source page:{RESET}     {f['source_page']}")
        if f["reference_range"]:
            rr = f["reference_range"]
            abn = rr.get("is_abnormal", False)
            abn_tag = f"{RED}ABNORMAL{RESET}" if abn else f"{GREEN}normal{RESET}"
            print(f"  {BOLD}Reference range:{RESET} {rr.get('min')}–{rr.get('max')} {rr.get('unit')} ({abn_tag})")

    print()
    print(f"{BOLD}{'─' * 78}{RESET}")
    print(f"  {BOLD}Summary:{RESET}  {GREEN}{report['auto_approved_count']} auto-approved{RESET}  ·  "
          f"{RED}{report['needs_review_count']} needs review{RESET}  ·  "
          f"{YELLOW}{report['conflict_count']} conflicts{RESET}")
    print(f"{BOLD}{'=' * 78}{RESET}")
    print()


def verify_demo_invariants(report: dict[str, Any]) -> bool:
    """Verify the demo output satisfies the hard requirements."""
    ok = True
    fields = report["fields"]

    # At least one auto-approved
    auto_fields = [f for f in fields if f["status"] == "auto_approved"]
    if len(auto_fields) < 2:
        print(f"  {RED}✗ FAIL: Need ≥ 2 auto-approved fields, got {len(auto_fields)}{RESET}")
        ok = False
    else:
        print(f"  {GREEN}✓ At least 2 auto-approved fields ({len(auto_fields)}){RESET}")

    # At least two needs_review
    review_fields = [f for f in fields if f["status"] == "needs_review"]
    if len(review_fields) < 2:
        print(f"  {RED}✗ FAIL: Need ≥ 2 needs_review fields, got {len(review_fields)}{RESET}")
        ok = False
    else:
        print(f"  {GREEN}✓ At least 2 needs_review fields ({len(review_fields)}){RESET}")

    # Medication must pass validation (complete 3-component prescription)
    med_fields = [f for f in fields if f["field_name"] == "medication"]
    if not med_fields:
        print(f"  {RED}✗ FAIL: No medication field found{RESET}")
        ok = False
    else:
        med = med_fields[0]
        if med["is_valid"]:
            print(f"  {GREEN}✓ Medication passes validation (complete prescription){RESET}")
        else:
            print(f"  {RED}✗ FAIL: Medication validation failed: '{med['validation_message']}'{RESET}")
            ok = False
        if med["risk_level"] == "HIGH_RISK":
            print(f"  {GREEN}✓ Medication correctly classified HIGH_RISK (requires human sign-off){RESET}")
        else:
            print(f"  {RED}✗ FAIL: Medication risk is {med['risk_level']}, expected HIGH_RISK{RESET}")
            ok = False

    # Allergy must be forced to HIGH_RISK and never auto-approve
    alg_fields = [f for f in fields if f["field_name"] == "allergy"]
    if not alg_fields:
        print(f"  {RED}✗ FAIL: No allergy field found{RESET}")
        ok = False
    else:
        alg = alg_fields[0]
        if alg["risk_level"] == "HIGH_RISK":
            print(f"  {GREEN}✓ Allergy forced to HIGH_RISK (safety invariant){RESET}")
        else:
            print(f"  {RED}✗ FAIL: Allergy risk is {alg['risk_level']}, expected HIGH_RISK{RESET}")
            ok = False
        if alg["status"] == "needs_review":
            print(f"  {GREEN}✓ Allergy never auto-approves (correct){RESET}")
        else:
            print(f"  {RED}✗ FAIL: Allergy status is {alg['status']}, expected needs_review{RESET}")
            ok = False

    # Every field has full WS8 metadata
    for f in fields:
        has_metadata = all(
            f.get(k) is not None
            for k in ("confidence", "risk_level", "validation_message", "source_page")
        )
        if not has_metadata:
            print(f"  {RED}✗ FAIL: Field '{f['field_name']}' missing WS8 metadata{RESET}")
            ok = False

    if ok:
        print(f"  {GREEN}✓ All fields carry full WS8 review-cockpit metadata{RESET}")

    return ok


def main() -> None:
    report = run_demo_tuning()
    print_demo_report(report)

    print(f"{BOLD}Invariant Verification:{RESET}")
    passed = verify_demo_invariants(report)
    print()

    if not passed:
        print(f"{BOLD}{RED}  DEMO TUNING: NO-GO — fix the issues above.{RESET}")
        sys.exit(1)

    print(f"{BOLD}{GREEN}  DEMO TUNING: GO — engine produces the correct field mix.{RESET}")
    print()

    # Write field-card JSON for downstream consumers
    output_path = ROOT / "docs" / "demo-field-cards.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"  Field cards written to {output_path}")


if __name__ == "__main__":
    main()
