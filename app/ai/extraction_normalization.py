"""Conservative deterministic normalization for directly written evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class NormalizedExtraction:
    value: str | None = None
    raw_unit: str | None = None
    unit: str | None = None


def normalize_extracted_value(field: str, raw: str) -> NormalizedExtraction:
    """Normalize only unambiguous syntax; never infer or convert clinical units."""
    text = raw.strip()
    patterns = {
        "hba1c": r"(?:HbA1c\s*[:=-]?\s*)?(\d+(?:\.\d+)?)\s*(%)",
        "blood_glucose": r"(?:Blood\s+Glucose|Glucose|FBS|PPBS|RBS)?\s*[:=-]?\s*(\d+(?:\.\d+)?)\s*(mg/dL|mmol/L)",
        "heart_rate": r"(?:Heart\s+Rate|Pulse)?\s*[:=-]?\s*(\d{2,3})\s*(bpm)",
    }
    if field in patterns and (match := re.fullmatch(patterns[field], text, re.I)):
        unit = {"hba1c": "%", "heart_rate": "bpm"}.get(field, match.group(2).lower())
        return NormalizedExtraction(match.group(1), match.group(2), unit)
    if field == "blood_pressure" and (
        match := re.fullmatch(
            r"(?:Blood\s+Pressure|BP)?\s*[:=-]?\s*(\d{2,3})\s*/\s*(\d{2,3})\s*(mmHg)",
            text,
            re.I,
        )
    ):
        return NormalizedExtraction(
            f"{match.group(1)}/{match.group(2)}", match.group(3), "mmHg"
        )
    if field == "phone":
        digits = re.sub(r"\D", "", text)
        digits = digits[2:] if len(digits) == 12 and digits.startswith("91") else digits
        if len(digits) == 10 and digits[0] in "6789":
            return NormalizedExtraction(f"+91{digits}")
    if field == "aadhaar_abha_id":
        compact = re.sub(r"[ -]", "", text)
        if re.fullmatch(r"\d{14}", compact):
            return NormalizedExtraction(
                f"{compact[:2]}-{compact[2:6]}-{compact[6:10]}-{compact[10:]}"
            )
        if re.fullmatch(r"\d{12}", compact):
            return NormalizedExtraction(f"{compact[:4]} {compact[4:8]} {compact[8:]}")
    if field in {"diagnosis", "medication"} and text:
        return NormalizedExtraction(text)
    return NormalizedExtraction()
