"""Conflict Detection Engine for AI Ingestion Pipeline (Workstream 5).

Detects intra-job value discrepancies and clinical contraindications:
- Same observation category with materially different values (e.g., conflicting
  sugar / BP readings)
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

# Sugar discrepancy threshold in mg/dL — readings farther apart than this
# are flagged as materially different.
_SUGAR_DISCREPANCY_THRESHOLD = 15.0


class Conflict(BaseModel):
    """Clinical conflict or data discrepancy diagnostic model."""

    conflict_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    conflict_type: str  # "VALUE_DISCREPANCY" or "CONTRAINDICATION"
    field_ids: list[str]
    message: str


# ── Helpers ──────────────────────────────────────────────────────────────────


def _set_conflict_flag(field: ExtractedField) -> None:
    """Mark a field as conflicted (in-place) on both the top-level flag and
    the nested ``validation_result``."""
    field.has_conflict = True
    val_res: Any = field.validation_result
    if val_res is not None:
        if isinstance(val_res, dict):
            val_res["has_conflict"] = True
        elif hasattr(val_res, "has_conflict"):
            val_res.has_conflict = True


def _extract_number(val: str) -> float | None:
    """Extract the first numeric value from a string."""
    match = re.search(r"(\d+(?:\.\d+)?)", str(val))
    return float(match.group(1)) if match else None


def _normalize_bp(value: str) -> str:
    """Normalize a BP reading to just the ``NNN/NNN`` core.

    Strips surrounding text like units so that ``"120/80"`` and
    ``"120/80 mmHg"`` compare equal.
    """
    match = re.search(r"(\d{2,3}/\d{2,3})", str(value).strip().lower())
    return match.group(1) if match else str(value).strip().lower()


# ── Field-name category helpers ─────────────────────────────────────────────

_SUGAR_NAMES = frozenset({"sugar", "fasting_glucose", "glucose", "random_glucose"})
_BP_NAMES = frozenset({"bp", "blood_pressure"})
_ALLERGY_NAMES = frozenset({"allergy", "allergen"})
_MEDICATION_NAMES = frozenset({"medication", "prescription", "drug"})


def _canonical_category(field_name: str) -> str:
    """Map a raw field name to a canonical category for grouping."""
    fn = field_name.lower().strip()
    if fn in _SUGAR_NAMES:
        return "sugar"
    if fn in _BP_NAMES:
        return "bp"
    return fn


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

        if cat == "sugar":
            for i in range(len(flist)):
                for j in range(i + 1, len(flist)):
                    f1, f2 = flist[i], flist[j]
                    n1 = _extract_number(f1.normalized_value or f1.raw_value)
                    n2 = _extract_number(f2.normalized_value or f2.raw_value)
                    if (
                        n1 is not None
                        and n2 is not None
                        and abs(n1 - n2) > _SUGAR_DISCREPANCY_THRESHOLD
                    ):
                        _set_conflict_flag(f1)
                        _set_conflict_flag(f2)
                        conflicts.append(
                            Conflict(
                                conflict_type="VALUE_DISCREPANCY",
                                field_ids=[f1.field_id, f2.field_id],
                                message=(
                                    f"Material discrepancy between blood sugar "
                                    f"readings ({n1} vs {n2})"
                                ),
                            )
                        )

        elif cat == "bp":
            for i in range(len(flist)):
                for j in range(i + 1, len(flist)):
                    f1, f2 = flist[i], flist[j]
                    v1 = _normalize_bp(str(f1.normalized_value or f1.raw_value))
                    v2 = _normalize_bp(str(f2.normalized_value or f2.raw_value))
                    if v1 != v2:
                        _set_conflict_flag(f1)
                        _set_conflict_flag(f2)
                        conflicts.append(
                            Conflict(
                                conflict_type="VALUE_DISCREPANCY",
                                field_ids=[f1.field_id, f2.field_id],
                                message=(
                                    f"Material discrepancy between blood pressure "
                                    f"readings ({v1} vs {v2})"
                                ),
                            )
                        )

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
