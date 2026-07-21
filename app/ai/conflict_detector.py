"""Conflict Detection Engine for AI Ingestion Pipeline (Workstream 5).

Detects intra-job value discrepancies and clinical contraindications:
- Same observation category with materially different values across common labs
  and vitals (e.g., sugar, HbA1c, pulse, SpO2, temperature, weight, BP)
- Contradictory data (e.g., active prescription for a known severe allergen)

Fields involved in a conflict have ``has_conflict=True``, which **forces**
them to the human-review queue regardless of confidence score.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.models.extracted_field import ExtractedField

# Material discrepancy thresholds by canonical observation category. These are
# conservative review triggers, not diagnostic clinical cutoffs.
_VALUE_DISCREPANCY_THRESHOLDS: dict[str, float] = {
    "sugar": 15.0,  # mg/dL
    "hba1c": 0.5,  # percentage points
    "heart_rate": 10.0,  # bpm
    "spo2": 3.0,  # percentage points
    "temperature": 1.0,  # same unit only
    "weight": 2.0,  # same unit only
}
_GENERIC_LAB_RELATIVE_THRESHOLD = 0.20
_GENERIC_LAB_ABSOLUTE_FLOOR = 5.0


class Conflict(BaseModel):
    """Clinical conflict or data discrepancy diagnostic model."""

    conflict_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    conflict_type: str  # "VALUE_DISCREPANCY" or "CONTRAINDICATION"
    field_ids: list[str]
    message: str


# ── Helpers ──────────────────────────────────────────────────────────────────


def _set_conflict_flag(field: ExtractedField) -> None:
    """Mark a field as conflicted in-place on both top-level and validation result."""
    field.has_conflict = True
    val_res: Any = field.validation_result
    if val_res is not None:
        if isinstance(val_res, dict):
            val_res["has_conflict"] = True
        elif hasattr(val_res, "has_conflict"):
            val_res.has_conflict = True


def _extract_number(val: str) -> float | None:
    """Extract the first numeric value from a string."""
    match = re.search(r"(-?\d+(?:\.\d+)?)", str(val))
    return float(match.group(1)) if match else None


def _extract_unit(val: str) -> str | None:
    """Extract and normalize the first recognized clinical unit from a string."""
    match = re.search(
        r"(mg/dL|mmol/L|g/dL|mEq/L|IU/L|ng/mL|bpm|beats/min|kg|lb|%|°C|°F|C|F)",
        str(val),
        re.IGNORECASE,
    )
    if not match:
        return None
    unit = match.group(1).lower().replace("°", "")
    return "bpm" if unit == "beats/min" else unit


def _field_value(field: ExtractedField) -> str:
    """Return the value that should be compared for a field."""
    return str(field.normalized_value or field.raw_value or "")


def _normalize_bp(value: str) -> str:
    """Normalize a BP reading to just the ``NNN/NNN`` core.

    Strips surrounding text like units so that ``"120/80"`` and
    ``"120/80 mmHg"`` compare equal.
    """
    match = re.search(r"(\d{2,3}/\d{2,3})", str(value).strip().lower())
    return match.group(1) if match else str(value).strip().lower()


# ── Field-name category helpers ─────────────────────────────────────────────

_SUGAR_NAMES = frozenset(
    {"sugar", "fasting_glucose", "glucose", "random_glucose", "blood_sugar"}
)
_BP_NAMES = frozenset({"bp", "blood_pressure"})
_HBA1C_NAMES = frozenset({"hba1c", "a1c"})
_HEART_RATE_NAMES = frozenset({"heart_rate", "pulse"})
_SPO2_NAMES = frozenset({"spo2", "sp_o2", "oxygen_saturation", "o2_saturation"})
_TEMPERATURE_NAMES = frozenset({"temperature", "temp"})
_WEIGHT_NAMES = frozenset({"weight", "body_weight"})
_GENERIC_LAB_NAMES = frozenset({"lab", "lab_result", "lab_value", "cbc", "lipid_panel"})
_ALLERGY_NAMES = frozenset({"allergy", "allergen"})
_MEDICATION_NAMES = frozenset({"medication", "prescription", "drug"})


def _canonical_category(field_name: str) -> str:
    """Map a raw field name to a canonical category for grouping."""
    fn = field_name.lower().strip()
    if fn in _SUGAR_NAMES:
        return "sugar"
    if fn in _BP_NAMES:
        return "bp"
    if fn in _HBA1C_NAMES:
        return "hba1c"
    if fn in _HEART_RATE_NAMES:
        return "heart_rate"
    if fn in _SPO2_NAMES:
        return "spo2"
    if fn in _TEMPERATURE_NAMES:
        return "temperature"
    if fn in _WEIGHT_NAMES:
        return "weight"
    if fn in _GENERIC_LAB_NAMES:
        return fn
    return fn


def _numeric_values_conflict(
    cat: str, f1: ExtractedField, f2: ExtractedField
) -> tuple[bool, str]:
    """Return whether two non-BP numeric observations materially conflict."""
    v1 = _field_value(f1)
    v2 = _field_value(f2)
    n1 = _extract_number(v1)
    n2 = _extract_number(v2)
    if n1 is None or n2 is None:
        return False, ""

    unit1 = _extract_unit(v1)
    unit2 = _extract_unit(v2)
    unit_sensitive = cat in {"temperature", "weight"} or cat in _GENERIC_LAB_NAMES
    if unit_sensitive and unit1 != unit2:
        return (
            True,
            f"Incompatible units for {cat} readings ({unit1 or 'missing'} vs {unit2 or 'missing'})",
        )

    if cat in _GENERIC_LAB_NAMES:
        if not unit1 or not unit2:
            return True, f"Missing comparable units for {cat} readings"
        threshold = max(
            _GENERIC_LAB_ABSOLUTE_FLOOR,
            _GENERIC_LAB_RELATIVE_THRESHOLD * max(abs(n1), abs(n2), 1.0),
        )
    else:
        threshold = _VALUE_DISCREPANCY_THRESHOLDS.get(cat)
        if threshold is None:
            return False, ""

    if abs(n1 - n2) > threshold:
        unit = unit1 or unit2 or ""
        suffix = f" {unit}" if unit else ""
        return (
            True,
            f"Material discrepancy between {cat} readings ({n1}{suffix} vs {n2}{suffix})",
        )
    return False, ""


def _append_value_conflict(
    conflicts: list[Conflict],
    f1: ExtractedField,
    f2: ExtractedField,
    message: str,
) -> None:
    """Mark two fields and append a value-discrepancy conflict."""
    _set_conflict_flag(f1)
    _set_conflict_flag(f2)
    conflicts.append(
        Conflict(
            conflict_type="VALUE_DISCREPANCY",
            field_ids=[f1.field_id, f2.field_id],
            message=message,
        )
    )


# ── Main detector ────────────────────────────────────────────────────────────


def detect_conflicts(fields: list[ExtractedField]) -> list[Conflict]:
    """Inspect a batch of extracted fields for clinical conflicts.

    Returns a list of :class:`Conflict` objects and **mutates** the
    ``has_conflict`` flag on every involved field to ``True``, forcing
    those fields into the human-review queue regardless of confidence.
    """
    conflicts: list[Conflict] = []

    # ── 1. Intra-job value discrepancy detection ────────────────────────────
    grouped: dict[str, list[ExtractedField]] = {}
    for f in fields:
        cat = _canonical_category(f.field_name)
        grouped.setdefault(cat, []).append(f)

    for cat, flist in grouped.items():
        if len(flist) < 2:
            continue

        for i in range(len(flist)):
            for j in range(i + 1, len(flist)):
                f1, f2 = flist[i], flist[j]
                if cat == "bp":
                    v1 = _normalize_bp(_field_value(f1))
                    v2 = _normalize_bp(_field_value(f2))
                    if v1 != v2:
                        _append_value_conflict(
                            conflicts,
                            f1,
                            f2,
                            f"Material discrepancy between blood pressure readings ({v1} vs {v2})",
                        )
                    continue

                is_conflict, message = _numeric_values_conflict(cat, f1, f2)
                if is_conflict:
                    _append_value_conflict(conflicts, f1, f2, message)

    # ── 2. Contradictory data: allergy vs. medication ───────────────────────
    allergies = [f for f in fields if f.field_name.lower().strip() in _ALLERGY_NAMES]
    meds = [f for f in fields if f.field_name.lower().strip() in _MEDICATION_NAMES]

    for alg in allergies:
        alg_text = str(alg.raw_value or alg.normalized_value or "").lower().strip()
        if not alg_text:
            continue
        for med in meds:
            med_text = str(med.raw_value or med.normalized_value or "").lower().strip()
            if not med_text:
                continue

            is_contraindicated = False

            # Direct substring match: allergy name appears in medication text
            # or vice-versa (e.g., allergy "Penicillin", med "Penicillin 500mg").
            if alg_text in med_text or med_text in alg_text:
                is_contraindicated = True
            # Beta-lactam cross-reactivity: penicillin allergy ↔ amoxicillin /
            # ampicillin / any "-cillin" drug.
            elif "penicillin" in alg_text and "cillin" in med_text:
                is_contraindicated = True

            if is_contraindicated:
                _set_conflict_flag(alg)
                _set_conflict_flag(med)
                conflicts.append(
                    Conflict(
                        conflict_type="CONTRAINDICATION",
                        field_ids=[alg.field_id, med.field_id],
                        message=(
                            f"Contradictory data: allergy to "
                            f"'{alg.raw_value}' conflicts with "
                            f"prescription '{med.raw_value}'"
                        ),
                    )
                )

    return conflicts
