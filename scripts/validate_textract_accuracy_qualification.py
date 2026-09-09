#!/usr/bin/env python3
"""Validate sanitized Textract benchmark output for Slice 7C qualification.

The extraction benchmark historically exposed identity-decision diagnostics but
its top-level ``benchmark_valid`` gate was driven by raw bound-identity equality.
That is useful OCR evidence, but it is not sufficient to prove that the actual
fail-closed identity decision accepted every true match and rejected every
mismatch.

This validator is the canonical Slice 7C qualification boundary.  It consumes
only aggregate/sanitized benchmark JSON and requires both extraction-quality
thresholds and the actual identity-decision outcomes to pass.  It performs no
AWS calls and never prints extracted values.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

QUALIFICATION_CONTRACT_VERSION = "slice-7c-v1"

MINIMUM_METRICS: dict[str, float] = {
    "successful_document_rate": 1.00,
    "canonical_field_presence_recall": 0.75,
    "exact_occurrence_precision": 0.80,
    "exact_occurrence_recall": 0.70,
    "exact_raw_value_accuracy": 0.70,
    "evidence_support_rate": 1.00,
    "normalized_value_accuracy": 0.80,
    "unit_accuracy": 0.85,
    "repeated_field_recall": 0.60,
    "table_row_accuracy": 0.70,
    "source_text_accuracy": 0.70,
    "page_accuracy": 1.00,
    "bounding_box_presence_and_validity": 0.90,
    "field_confidence_provenance": 0.90,
    "patient_identity_mismatch_detection": 1.00,
}

MAXIMUM_METRICS: dict[str, float] = {
    "unexpected_provider_failure_rate": 0.00,
}

MINIMUM_IDENTITY_METRICS: dict[str, float] = {
    "patient_identity_match_acceptance_rate": 1.00,
    "patient_identity_mismatch_rejection_rate": 1.00,
}

MAXIMUM_IDENTITY_METRICS: dict[str, float] = {
    "patient_identity_false_accept_rate": 0.00,
    "patient_identity_false_reject_rate": 0.00,
}

REQUIRED_IDENTITY_OUTCOMES = (
    "TRUE_MATCH_ACCEPTED",
    "TRUE_MATCH_REJECTED",
    "MISMATCH_REJECTED",
    "MISMATCH_ACCEPTED",
)


def _number(mapping: Mapping[str, Any], key: str, errors: list[str], scope: str) -> float | None:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{scope}.{key}: finite numeric metric required")
        return None
    numeric = float(value)
    if not math.isfinite(numeric):
        errors.append(f"{scope}.{key}: finite numeric metric required")
        return None
    return numeric


def _validate_thresholds(
    values: Mapping[str, Any],
    *,
    minimums: Mapping[str, float],
    maximums: Mapping[str, float],
    scope: str,
    errors: list[str],
) -> None:
    for name, threshold in minimums.items():
        value = _number(values, name, errors, scope)
        if value is not None and value < threshold:
            errors.append(f"{scope}.{name}: below Slice 7C minimum")
    for name, threshold in maximums.items():
        value = _number(values, name, errors, scope)
        if value is not None and value > threshold:
            errors.append(f"{scope}.{name}: above Slice 7C maximum")


def validate_report(report: Mapping[str, Any]) -> list[str]:
    """Return value-free qualification errors for one sanitized benchmark report."""

    errors: list[str] = []

    if report.get("benchmark_valid") is not True:
        errors.append("benchmark_valid: extraction benchmark must already pass")
    if report.get("metrics_valid") is not True:
        errors.append("metrics_valid: all required extraction metrics must be defined")

    attempted = report.get("attempted_documents")
    successful = report.get("successful_documents")
    failed = report.get("failed_documents")
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in (attempted, successful, failed)):
        errors.append("documents: integer attempted/successful/failed counts required")
    elif attempted <= 0 or successful != attempted or failed != 0:
        errors.append("documents: every attempted synthetic document must succeed")

    provider_errors = report.get("provider_error_counts")
    if not isinstance(provider_errors, Mapping):
        errors.append("provider_error_counts: object required")
    elif any(value for value in provider_errors.values()):
        errors.append("provider_error_counts: provider failures must be zero")

    metrics = report.get("metrics")
    if not isinstance(metrics, Mapping):
        errors.append("metrics: object required")
    else:
        _validate_thresholds(
            metrics,
            minimums=MINIMUM_METRICS,
            maximums=MAXIMUM_METRICS,
            scope="metrics",
            errors=errors,
        )

    identity_metrics = report.get("identity_metrics")
    if not isinstance(identity_metrics, Mapping):
        errors.append("identity_metrics: object required")
    else:
        _validate_thresholds(
            identity_metrics,
            minimums=MINIMUM_IDENTITY_METRICS,
            maximums=MAXIMUM_IDENTITY_METRICS,
            scope="identity_metrics",
            errors=errors,
        )

    outcomes = report.get("identity_outcome_counts")
    if not isinstance(outcomes, Mapping):
        errors.append("identity_outcome_counts: object required")
    else:
        counts: dict[str, int] = {}
        for name in REQUIRED_IDENTITY_OUTCOMES:
            value = outcomes.get(name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append(f"identity_outcome_counts.{name}: non-negative integer required")
                continue
            counts[name] = value
        if len(counts) == len(REQUIRED_IDENTITY_OUTCOMES):
            if counts["TRUE_MATCH_REJECTED"] != 0:
                errors.append("identity_outcome_counts.TRUE_MATCH_REJECTED: must be zero")
            if counts["MISMATCH_ACCEPTED"] != 0:
                errors.append("identity_outcome_counts.MISMATCH_ACCEPTED: must be zero")
            if isinstance(successful, int) and sum(counts.values()) != successful:
                errors.append("identity_outcome_counts: must reconcile to successful documents")
            if counts["TRUE_MATCH_ACCEPTED"] == 0:
                errors.append("identity_outcome_counts.TRUE_MATCH_ACCEPTED: coverage required")
            if counts["MISMATCH_REJECTED"] == 0:
                errors.append("identity_outcome_counts.MISMATCH_REJECTED: mismatch coverage required")

    classification = report.get("failure_classification")
    if not isinstance(classification, Mapping):
        errors.append("failure_classification: object required")
    elif classification.get("reconciliation_valid") is not True:
        errors.append("failure_classification.reconciliation_valid: must be true")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path, help="sanitized benchmark JSON report")
    arguments = parser.parse_args()

    try:
        report = json.loads(arguments.report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("FAIL: benchmark report could not be loaded")
        return 1
    if not isinstance(report, dict):
        print("FAIL: benchmark report root must be an object")
        return 1

    errors = validate_report(report)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        print(f"FAIL: Textract qualification did not satisfy {QUALIFICATION_CONTRACT_VERSION}")
        return 1

    print(f"PASS: Textract qualification satisfies {QUALIFICATION_CONTRACT_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
