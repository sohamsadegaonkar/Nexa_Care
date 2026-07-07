"""Medical Validation Engine for AI Ingestion Pipeline (Workstream 5).

Executes automated clinical validation checks on candidate medical observations:
- Blood pressure format verification (NNN/NNN mmHg)
- Quantitative lab value and clinical unit verification
- Prescription dosing completeness (strength + frequency verification)
- Temporal date plausibility (future date rejection)
- Pharmaceutical formulary fuzzy matching against bundled known medicines list
- Abnormal diagnostic laboratory evaluation and flagging
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


def _load_known_medicines() -> list[str]:
    if not KNOWN_MEDICINES_PATH.exists():
        return [
            "metformin", "penicillin", "lisinopril", "amoxicillin",
            "telmisartan", "atorvastatin", "aspirin", "insulin",
            "ibuprofen", "acetaminophen", "omeprazole", "amlodipine",
        ]
    text = KNOWN_MEDICINES_PATH.read_text(encoding="utf-8")
    return [m.strip().lower() for m in text.splitlines() if m.strip()]


_KNOWN_MEDICINES: list[str] = _load_known_medicines()


def validate_field(field_name: str, value: str) -> ValidationResult:
    """Execute deterministic medical validation rules for a candidate observation."""
    fname = str(field_name).strip().lower()
    val = str(value).strip()

    is_valid = True
    errors: list[str] = []
    checks: list[dict[str, Any]] = []
    ref_range: dict[str, Any] | None = None

    # 1. Blood Pressure Format Check
    if fname in {"bp", "blood_pressure", "systolic_bp", "diastolic_bp"}:
        if re.match(r"^\d{2,3}/\d{2,3}(\s*mmHg)?$", val, re.IGNORECASE):
            checks.append({"check_name": "bp_format", "passed": True, "message": "Valid blood pressure format"})
        else:
            is_valid = False
            msg = "Invalid blood pressure format (expected NNN/NNN mmHg)"
            errors.append(msg)
            checks.append({"check_name": "bp_format", "passed": False, "message": msg})

    # 2. Prescription Dosing Completeness & Formulary Match Check
    elif fname in {"medication", "prescription", "drug", "dosage"}:
        # Check frequency
        has_freq = bool(
            re.search(r"\b(daily|twice|once|bid|tid|qid|q\d+h|prn|morning|night|every|hours?|day|weekly|times)\b", val, re.IGNORECASE)
        )
        if has_freq:
            checks.append({"check_name": "dosage_frequency", "passed": True, "message": "Frequency specified"})
        else:
            is_valid = False
            msg = "frequency missing"
            errors.append(msg)
            checks.append({"check_name": "dosage_frequency", "passed": False, "message": msg})

        # Check strength
        has_strength = bool(re.search(r"\d+\s*(mg|g|ml|mcg|units?)", val, re.IGNORECASE))
        if has_strength:
            checks.append({"check_name": "dosage_strength", "passed": True, "message": "Strength specified"})
        else:
            is_valid = False
            msg = "strength missing"
            errors.append(msg)
            checks.append({"check_name": "dosage_strength", "passed": False, "message": msg})

        # Fuzzy match drug name against known medicines list
        first_tokens = re.split(r"[\d\s]+", val)[0].strip().lower()
        drug_cand = first_tokens if first_tokens else val.lower()
        best_ratio = 0.0
        best_match = ""
        for med in _KNOWN_MEDICINES:
            ratio = difflib.SequenceMatcher(None, drug_cand, med).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = med

        if best_ratio >= 0.65 or any(med in val.lower() for med in _KNOWN_MEDICINES):
            checks.append({"check_name": "medication_fuzzy_match", "passed": True, "message": f"Matched known medicine ({best_match})"})
        else:
            is_valid = False
            msg = f"Medication name fuzzy match low ({best_ratio:.2f})"
            errors.append(msg)
            checks.append({"check_name": "medication_fuzzy_match", "passed": False, "message": msg})

    # 3. Temporal & Date Plausibility Check
    elif fname in {"date", "recorded_at", "prescribed_at", "uploaded_at", "dob"} or re.match(r"^\d{4}-\d{2}-\d{2}", val):
        try:
            parsed_dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            if parsed_dt.tzinfo is None:
                parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if parsed_dt > now + timedelta(minutes=5):
                is_valid = False
                msg = "Date cannot be in the future"
                errors.append(msg)
                checks.append({"check_name": "date_not_future", "passed": False, "message": msg})
            else:
                checks.append({"check_name": "date_not_future", "passed": True, "message": "Valid historical date"})
        except Exception:
            is_valid = False
            msg = "Unparseable date format"
            errors.append(msg)
            checks.append({"check_name": "date_format", "passed": False, "message": msg})

    # 4. Blood Sugar & Diagnostic Lab Verification
    elif fname in {"sugar", "fasting_glucose", "hba1c", "lab_result", "lab"}:
        num_match = re.search(r"(\d+(\.\d+)?)", val)
        unit_match = re.search(r"(mg/dL|mmol/L|%|g/dL|mEq/L|IU/L|ng/mL)", val, re.IGNORECASE)
        if num_match and unit_match:
            num_val = float(num_match.group(1))
            unit_str = unit_match.group(1)
            checks.append({"check_name": "lab_unit_check", "passed": True, "message": f"Numeric value with unit ({unit_str})"})

            # Check reference bounds & flag abnormalities
            is_abnorm = False
            if fname in {"sugar", "fasting_glucose"}:
                if num_val >= 126.0 or num_val < 50.0:
                    is_abnorm = True
                ref_range = {"min": 70.0, "max": 100.0, "unit": unit_str, "is_abnormal": is_abnorm}
            elif fname == "hba1c" or "%" in unit_str:
                if num_val >= 6.5:
                    is_abnorm = True
                ref_range = {"min": 4.0, "max": 5.6, "unit": "%", "is_abnormal": is_abnorm}
            else:
                ref_range = {"min": 0.0, "max": 100.0, "unit": unit_str, "is_abnormal": False}

            checks.append({"check_name": "abnormal_lab_check", "passed": True, "message": "Abnormal lab flagged" if is_abnorm else "Normal lab bounds"})
        else:
            is_valid = False
            msg = "Missing numeric lab value or recognized unit"
            errors.append(msg)
            checks.append({"check_name": "lab_unit_check", "passed": False, "message": msg})

    return ValidationResult(
        is_valid=is_valid,
        has_conflict=False,
        checks=checks,
        validation_errors=errors,
        reference_range=ref_range,
    )
