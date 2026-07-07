"""Confidence Scorer for AI Ingestion Pipeline (Workstream 5).

Combines raw model extraction confidence with heuristic adjustments based on
format conformance, syntax validity, and clinical plausibility.
"""

from __future__ import annotations

import re
from typing import Any


def score_field(
    field_name: str,
    raw_value: str,
    extractor_confidence: float | None = None,
    context: dict[str, Any] | None = None,
) -> float:
    """Calculate normalized confidence score (0.0 to 1.0) for an extracted field."""
    base_conf = float(extractor_confidence) if extractor_confidence is not None else 0.90
    base_conf = max(0.0, min(1.0, base_conf))

    fname = str(field_name).strip().lower()
    val = str(raw_value).strip()

    if not val or val.lower() in {"null", "none", "n/a", "invalid", "unknown"}:
        return max(0.0, base_conf - 0.50)

    adjustment = 0.0

    # Format heuristics
    if fname in {"bp", "blood_pressure", "systolic_bp", "diastolic_bp"}:
        if re.match(r"^\d{2,3}/\d{2,3}(\s*mmHg)?$", val, re.IGNORECASE):
            adjustment += 0.04
        else:
            adjustment -= 0.25
    elif fname in {"sugar", "fasting_glucose", "hba1c", "lab_result"}:
        if re.search(r"\d+(\.\d+)?\s*(mg/dL|mmol/L|%)", val, re.IGNORECASE) or re.match(r"^\d+(\.\d+)?$", val):
            adjustment += 0.03
        else:
            adjustment -= 0.20
    elif fname in {"medication", "prescription", "drug"}:
        has_strength = bool(re.search(r"\d+\s*(mg|g|ml|mcg|units?)", val, re.IGNORECASE))
        has_freq = bool(re.search(r"(daily|twice|once|bid|tid|qid|q\d+h|prn|morning|night)", val, re.IGNORECASE))
        if has_strength and has_freq:
            adjustment += 0.05
        elif not has_strength and not has_freq:
            adjustment -= 0.15
    elif fname in {"allergy", "allergen"}:
        if len(val) >= 3 and not re.search(r"[0-9@#$%^&*]", val):
            adjustment += 0.02
        else:
            adjustment -= 0.15

    final_conf = max(0.0, min(1.0, base_conf + adjustment))
    return round(final_conf, 4)
