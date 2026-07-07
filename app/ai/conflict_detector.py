"""Conflict Detection Engine for AI Ingestion Pipeline (Workstream 5).

Detects intra-job value discrepancies and clinical contraindications:
- Same observation category with materially different values (e.g., conflicting sugar/BP)
- Contradictory data (e.g., active prescription for a known severe allergen)
Forces involved fields to route to human review via has_conflict=True.
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from pydantic import BaseModel, Field

from app.models.extracted_field import ExtractedField


class Conflict(BaseModel):
    """Clinical conflict or data discrepancy diagnostic model."""
    conflict_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    conflict_type: str  # "VALUE_DISCREPANCY" or "CONTRAINDICATION"
    field_ids: list[str]
    message: str


def _set_conflict_flag(field: ExtractedField) -> None:
    field.has_conflict = True
    val_res: Any = field.validation_result
    if val_res is not None:
        if isinstance(val_res, dict):
            val_res["has_conflict"] = True
        elif hasattr(val_res, "has_conflict"):
            val_res.has_conflict = True


def _extract_number(val: str) -> float | None:
    match = re.search(r"(\d+(\.\d+)?)", str(val))
    return float(match.group(1)) if match else None


def detect_conflicts(fields: list[ExtractedField]) -> list[Conflict]:
    """Inspect a batch of extracted fields for clinical conflicts and discrepancies."""
    conflicts: list[Conflict] = []

    # 1. Intra-job value discrepancy detection (e.g., conflicting blood sugar readings)
    grouped: dict[str, list[ExtractedField]] = {}
    for f in fields:
        fn = f.field_name.lower().strip()
        if fn in {"sugar", "fasting_glucose", "glucose", "random_glucose"}:
            grouped.setdefault("sugar", []).append(f)
        elif fn in {"bp", "blood_pressure"}:
            grouped.setdefault("bp", []).append(f)
        else:
            grouped.setdefault(fn, []).append(f)

    for cat, flist in grouped.items():
        if len(flist) > 1:
            if cat == "sugar":
                # Compare pairwise numeric sugar readings
                for i in range(len(flist)):
                    for j in range(i + 1, len(flist)):
                        f1, f2 = flist[i], flist[j]
                        n1 = _extract_number(f1.normalized_value or f1.raw_value)
                        n2 = _extract_number(f2.normalized_value or f2.raw_value)
                        if n1 is not None and n2 is not None and abs(n1 - n2) > 15.0:
                            _set_conflict_flag(f1)
                            _set_conflict_flag(f2)
                            conflicts.append(
                                Conflict(
                                    conflict_type="VALUE_DISCREPANCY",
                                    field_ids=[f1.field_id, f2.field_id],
                                    message=f"Material discrepancy between blood sugar readings ({n1} vs {n2})",
                                )
                            )
            elif cat == "bp":
                for i in range(len(flist)):
                    for j in range(i + 1, len(flist)):
                        f1, f2 = flist[i], flist[j]
                        v1 = str(f1.normalized_value or f1.raw_value).strip()
                        v2 = str(f2.normalized_value or f2.raw_value).strip()
                        if v1 != v2:
                            _set_conflict_flag(f1)
                            _set_conflict_flag(f2)
                            conflicts.append(
                                Conflict(
                                    conflict_type="VALUE_DISCREPANCY",
                                    field_ids=[f1.field_id, f2.field_id],
                                    message=f"Material discrepancy between blood pressure readings ({v1} vs {v2})",
                                )
                            )

    # 2. Contradictory data detection (Allergy vs Medication contraindication)
    allergies = [f for f in fields if f.field_name.lower().strip() in {"allergy", "allergen"}]
    meds = [f for f in fields if f.field_name.lower().strip() in {"medication", "prescription", "drug"}]

    for alg in allergies:
        alg_text = str(alg.raw_value or alg.normalized_value or "").lower().strip()
        for med in meds:
            med_text = str(med.raw_value or med.normalized_value or "").lower().strip()
            if not alg_text or not med_text:
                continue

            # Check direct match or beta-lactam cross-reactivity (penicillin <-> amoxicillin/ampicillin)
            is_contraindicated = False
            if alg_text in med_text or med_text in alg_text:
                is_contraindicated = True
            elif "penicillin" in alg_text and "cillin" in med_text:
                is_contraindicated = True

            if is_contraindicated:
                _set_conflict_flag(alg)
                _set_conflict_flag(med)
                conflicts.append(
                    Conflict(
                        conflict_type="CONTRAINDICATION",
                        field_ids=[alg.field_id, med.field_id],
                        message=f"Contradictory data: allergy to '{alg.raw_value}' conflicts with prescription '{med.raw_value}'",
                    )
                )

    return conflicts
