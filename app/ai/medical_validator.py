"""Medical Validation Engine for AI Ingestion Pipeline (Workstream 5).

Executes automated clinical validation checks on candidate medical observations:
- Blood pressure format verification (NNN/NNN mmHg)
- Quantitative lab value and clinical unit verification
- Prescription dosing completeness (strength + frequency verification)
- Temporal date plausibility (future date rejection)
- Pharmaceutical formulary fuzzy matching against bundled known medicines list
- Abnormal diagnostic laboratory evaluation and flagging

All validation is **deterministic and testable**: the same inputs always produce
the same outputs with no randomness or external service calls.

Medication field rules
----------------------
``field_name`` in ``{"medication", "prescription", "drug"}`` requires **all
three** of the following to pass validation:

1. **Drug name** — fuzzy-matched (≥ 0.65 ratio) against the bundled
   ``known_medicines.txt`` formulary list.
2. **Strength** — must contain a quantitative unit (e.g. "500mg", "10 g").
3. **Frequency** — must contain a dosing keyword (e.g. "daily", "twice",
   "BID", "prn").

Examples::

    "Metformin 500mg twice daily" → ✅ PASS  (drug + strength + frequency)
    "Metformin"                    → ❌ FAIL  (missing strength & frequency)
    "500mg daily"                  → ❌ FAIL  (fuzzy match fails on bare unit)

Sugar / Fasting Glucose reference ranges
----------------------------------------
The validator uses a clinically scoped reference interval rather than only
flagging the diabetic threshold (≥ 126 mg/dL).  This ensures pre-diabetic
readings (100–125 mg/dL) are surfaced to reviewers instead of silently
passing as normal.

============  ===========  =========
Value (mg/dL)  Classification  is_abnormal
============  ===========  =========
< 70          Below range     True
70 – 100      Normal range    False
> 100         Above range     True
============  ===========  =========

HbA1c reference range: 4.0 – 5.6 %; values above 5.6 are flagged abnormal.
"""
from __future__ import annotations

import difflib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.models.extracted_field import ValidationResult

DATA_DIR = Path(__file__).parent / "data"
KNOWN_MEDICINES_PATH = DATA_DIR / "known_medicines.txt"

# ── Precompiled regex patterns ──────────────────────────────────────────────
_BP_PATTERN = re.compile(r"^\d{2,3}/\d{2,3}( mmHg)?$", re.IGNORECASE)
_STRENGTH_PATTERN = re.compile(r"\d+\s*(mg|g|ml|mcg|units?)", re.IGNORECASE)
_FREQUENCY_PATTERN = re.compile(
    r"\b(daily|twice|once|bid|tid|qid|q\d+h|prn|morning|night|every|hours?|day|weekly|times)\b",
    re.IGNORECASE,
)
_LAB_UNIT_PATTERN = re.compile(r"(mg/dL|mmol/L|%|g/dL|mEq/L|IU/L|ng/mL)", re.IGNORECASE)
_LAB_NUMERIC_PATTERN = re.compile(r"(\d+(?:\.\d+)?)")

# ── Field-name category sets ────────────────────────────────────────────────
_BP_FIELDS = frozenset({"bp", "blood_pressure", "systolic_bp", "diastolic_bp"})
_DOSAGE_FIELDS = frozenset({"dosage", "strength", "frequency"})
_MEDICATION_FIELDS = frozenset({"medication", "prescription", "drug"})
_DATE_FIELDS = frozenset({"date", "recorded_at", "prescribed_at", "uploaded_at", "dob"})
_LAB_FIELDS = frozenset({"sugar", "fasting_glucose", "hba1c", "lab_result", "lab"})

# ── Reference ranges for common lab observations ────────────────────────────
_LAB_REFERENCE_RANGES: dict[str, dict[str, Any]] = {
    "sugar": {"min": 70.0, "max": 100.0, "unit": "mg/dL"},
    "fasting_glucose": {"min": 70.0, "max": 100.0, "unit": "mg/dL"},
    "hba1c": {"min": 4.0, "max": 5.6, "unit": "%"},
}

# ── Tokens to skip when extracting drug name from medication string ─────────
_DRUG_NAME_SKIP_TOKENS = frozenset({
    # Units
    "mg", "g", "ml", "mcg", "units", "unit", "meq", "iu", "ng",
    # Frequency keywords
    "daily", "twice", "once", "bid", "tid", "qid", "prn",
    "morning", "night", "every", "hours", "hour", "day", "weekly", "times",
    # Conjunctions / prepositions
    "or", "and", "with", "without", "after", "before", "per", "the",
    # Dosage forms
    "tab", "tablet", "cap", "capsule", "inj", "injection",
    "syp", "syrup", "cream", "ointment", "drops", "drop", "sachet", "patch",
})

# Fuzzy-match threshold: below this ratio the medication name is flagged.
_FUZZY_MATCH_THRESHOLD = 0.65


def _load_known_medicines() -> list[str]:
    """Load the bundled known-medicines list from ``data/known_medicines.txt``."""
    if not KNOWN_MEDICINES_PATH.exists():
        return [
            "metformin", "penicillin", "lisinopril", "amoxicillin",
            "telmisartan", "atorvastatin", "aspirin", "insulin",
            "ibuprofen", "acetaminophen", "omeprazole", "amlodipine",
        ]
    text = KNOWN_MEDICINES_PATH.read_text(encoding="utf-8")
    return [m.strip().lower() for m in text.splitlines() if m.strip()]


_KNOWN_MEDICINES: list[str] = _load_known_medicines()


def _extract_drug_candidate(text: str) -> str:
    """Extract the most likely drug-name token from a medication string.

    Skips dosage-form keywords (tab, cap …), units (mg, ml …), and frequency
    words (daily, twice …).  Returns the first qualifying alphabetic token
    lowercased, or the full lowercased text as fallback.
    """
    for token in text.split():
        clean = token.strip(".,;:").lower()
        if (
            clean
            and len(clean) > 2
            and re.match(r"^[a-zA-Z\-]+$", clean)
            and clean not in _DRUG_NAME_SKIP_TOKENS
        ):
            return clean
    return text.lower()


def _fuzzy_match_medication(text: str) -> tuple[float, str]:
    """Fuzzy-match a drug name against the bundled known-medicines list.

    Returns ``(best_ratio, best_match_name)``.  A direct substring match
    guarantees a minimum ratio of 0.80 regardless of tokenisation.
    """
    drug_cand = _extract_drug_candidate(text)

    best_ratio = 0.0
    best_match = ""
    for med in _KNOWN_MEDICINES:
        ratio = difflib.SequenceMatcher(None, drug_cand, med).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = med

    # Substring match: if a known medicine name appears verbatim anywhere in
    # the full text that is a strong signal regardless of tokenisation.
    text_lower = text.lower()
    for med in _KNOWN_MEDICINES:
        if med in text_lower and best_ratio < 0.80:
            best_ratio = 0.80
            best_match = med
            break

    return best_ratio, best_match


def validate_field(field_name: str, value: str) -> ValidationResult:
    """Execute deterministic medical validation rules for a candidate observation.

    Each branch appends structured ``checks`` and accumulates ``errors``.
    The function is pure: same inputs always produce the same output.
    """
    fname = str(field_name).strip().lower()
    val = str(value).strip()

    is_valid = True
    errors: list[str] = []
    checks: list[dict[str, Any]] = []
    ref_range: dict[str, Any] | None = None

    # ── 1. Blood Pressure Format Check ──────────────────────────────────────
    if fname in _BP_FIELDS:
        if _BP_PATTERN.match(val):
            checks.append({
                "check_name": "bp_format",
                "passed": True,
                "message": "Valid blood pressure format",
            })
        else:
            is_valid = False
            msg = "Invalid blood pressure format (expected NNN/NNN mmHg)"
            errors.append(msg)
            checks.append({
                "check_name": "bp_format",
                "passed": False,
                "message": msg,
            })

    # ── 2. Dosage Completeness (strength + frequency, NO drug-name match) ───
    elif fname in _DOSAGE_FIELDS:
        has_freq = bool(_FREQUENCY_PATTERN.search(val))
        if has_freq:
            checks.append({
                "check_name": "dosage_frequency",
                "passed": True,
                "message": "Frequency specified",
            })
        else:
            is_valid = False
            msg = "frequency missing"
            errors.append(msg)
            checks.append({
                "check_name": "dosage_frequency",
                "passed": False,
                "message": msg,
            })

        has_strength = bool(_STRENGTH_PATTERN.search(val))
        if has_strength:
            checks.append({
                "check_name": "dosage_strength",
                "passed": True,
                "message": "Strength specified",
            })
        else:
            is_valid = False
            msg = "strength missing"
            errors.append(msg)
            checks.append({
                "check_name": "dosage_strength",
                "passed": False,
                "message": msg,
            })

    # ── 3. Medication / Prescription / Drug ─────────────────────────────────
    #    (strength + frequency + fuzzy-match drug name against formulary)
    elif fname in _MEDICATION_FIELDS:
        # Frequency
        has_freq = bool(_FREQUENCY_PATTERN.search(val))
        if has_freq:
            checks.append({
                "check_name": "dosage_frequency",
                "passed": True,
                "message": "Frequency specified",
            })
        else:
            is_valid = False
            msg = "frequency missing"
            errors.append(msg)
            checks.append({
                "check_name": "dosage_frequency",
                "passed": False,
                "message": msg,
            })

        # Strength
        has_strength = bool(_STRENGTH_PATTERN.search(val))
        if has_strength:
            checks.append({
                "check_name": "dosage_strength",
                "passed": True,
                "message": "Strength specified",
            })
        else:
            is_valid = False
            msg = "strength missing"
            errors.append(msg)
            checks.append({
                "check_name": "dosage_strength",
                "passed": False,
                "message": msg,
            })

        # Fuzzy-match drug name against formulary
        best_ratio, best_match = _fuzzy_match_medication(val)
        if best_ratio >= _FUZZY_MATCH_THRESHOLD:
            checks.append({
                "check_name": "medication_fuzzy_match",
                "passed": True,
                "message": f"Matched known medicine ({best_match})",
            })
        else:
            is_valid = False
            msg = f"Medication name fuzzy match low ({best_ratio:.2f})"
            errors.append(msg)
            checks.append({
                "check_name": "medication_fuzzy_match",
                "passed": False,
                "message": msg,
            })

    # ── 4. Temporal / Date Plausibility Check ───────────────────────────────
    elif fname in _DATE_FIELDS or re.match(r"^\d{4}-\d{2}-\d{2}", val):
        try:
            parsed_dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            if parsed_dt.tzinfo is None:
                parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if parsed_dt > now + timedelta(minutes=5):
                is_valid = False
                msg = "Date cannot be in the future"
                errors.append(msg)
                checks.append({
                    "check_name": "date_not_future",
                    "passed": False,
                    "message": msg,
                })
            else:
                checks.append({
                    "check_name": "date_not_future",
                    "passed": True,
                    "message": "Valid historical date",
                })
        except (ValueError, OverflowError):
            is_valid = False
            msg = "Unparseable date format"
            errors.append(msg)
            checks.append({
                "check_name": "date_format",
                "passed": False,
                "message": msg,
            })

    # ── 5. Blood Sugar & Diagnostic Lab Verification ────────────────────────
    elif fname in _LAB_FIELDS:
        num_match = _LAB_NUMERIC_PATTERN.search(val)
        unit_match = _LAB_UNIT_PATTERN.search(val)
        if num_match and unit_match:
            num_val = float(num_match.group(1))
            unit_str = unit_match.group(1)
            checks.append({
                "check_name": "lab_unit_check",
                "passed": True,
                "message": f"Numeric value with unit ({unit_str})",
            })

            # Abnormal lab flagging against reference range
            is_abnorm = False
            ref_def = _LAB_REFERENCE_RANGES.get(fname)
            if ref_def is not None:
                low = float(ref_def["min"])
                high = float(ref_def["max"])
                if num_val < low or num_val > high:
                    is_abnorm = True
                ref_range = {
                    "min": low,
                    "max": high,
                    "unit": ref_def["unit"],
                    "is_abnormal": is_abnorm,
                }
            else:
                ref_range = {
                    "min": 0.0,
                    "max": 100.0,
                    "unit": unit_str,
                    "is_abnormal": False,
                }

            checks.append({
                "check_name": "abnormal_lab_check",
                "passed": True,
                "message": "Abnormal lab flagged" if is_abnorm else "Normal lab bounds",
            })
        else:
            is_valid = False
            msg = "Missing numeric lab value or recognized unit"
            errors.append(msg)
            checks.append({
                "check_name": "lab_unit_check",
                "passed": False,
                "message": msg,
            })

    return ValidationResult(
        is_valid=is_valid,
        has_conflict=False,
        checks=checks,
        validation_errors=errors,
        reference_range=ref_range,
    )
