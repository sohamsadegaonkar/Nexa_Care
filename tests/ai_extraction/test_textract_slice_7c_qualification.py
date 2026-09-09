from __future__ import annotations

import asyncio
from pathlib import Path

from scripts.run_textract_accuracy_benchmark import run
from scripts.validate_textract_accuracy_qualification import (
    MAXIMUM_IDENTITY_METRICS,
    MAXIMUM_METRICS,
    MINIMUM_IDENTITY_METRICS,
    MINIMUM_METRICS,
    validate_report,
)

BENCHMARK = Path(__file__).parent / "benchmark"
DOCUMENTS = BENCHMARK / "documents"
MANIFEST = BENCHMARK / "synthetic-manifest.json"
REPLAY = BENCHMARK / "sanitized-replay"


def _passing_report() -> dict[str, object]:
    return {
        "benchmark_valid": True,
        "metrics_valid": True,
        "attempted_documents": 15,
        "successful_documents": 15,
        "failed_documents": 0,
        "provider_error_counts": {},
        "metrics": {**MINIMUM_METRICS, **MAXIMUM_METRICS},
        "identity_metrics": {
            **MINIMUM_IDENTITY_METRICS,
            **MAXIMUM_IDENTITY_METRICS,
        },
        "identity_outcome_counts": {
            "TRUE_MATCH_ACCEPTED": 14,
            "TRUE_MATCH_REJECTED": 0,
            "MISMATCH_REJECTED": 1,
            "MISMATCH_ACCEPTED": 0,
        },
        "failure_classification": {"reconciliation_valid": True},
    }


def test_slice_7c_accepts_only_fully_reconciled_extraction_and_identity_report():
    assert validate_report(_passing_report()) == []


def test_legacy_benchmark_pass_cannot_hide_true_match_identity_rejection():
    report = _passing_report()
    report["identity_metrics"] = {
        "patient_identity_match_acceptance_rate": 13 / 14,
        "patient_identity_mismatch_rejection_rate": 1.0,
        "patient_identity_false_accept_rate": 0.0,
        "patient_identity_false_reject_rate": 1 / 14,
    }
    report["identity_outcome_counts"] = {
        "TRUE_MATCH_ACCEPTED": 13,
        "TRUE_MATCH_REJECTED": 1,
        "MISMATCH_REJECTED": 1,
        "MISMATCH_ACCEPTED": 0,
    }

    errors = validate_report(report)

    assert any("patient_identity_match_acceptance_rate" in error for error in errors)
    assert any("patient_identity_false_reject_rate" in error for error in errors)
    assert any("TRUE_MATCH_REJECTED" in error for error in errors)


def test_identity_mismatch_acceptance_is_a_hard_qualification_failure():
    report = _passing_report()
    report["identity_metrics"] = {
        "patient_identity_match_acceptance_rate": 1.0,
        "patient_identity_mismatch_rejection_rate": 0.0,
        "patient_identity_false_accept_rate": 1.0,
        "patient_identity_false_reject_rate": 0.0,
    }
    report["identity_outcome_counts"] = {
        "TRUE_MATCH_ACCEPTED": 14,
        "TRUE_MATCH_REJECTED": 0,
        "MISMATCH_REJECTED": 0,
        "MISMATCH_ACCEPTED": 1,
    }

    errors = validate_report(report)

    assert any("patient_identity_mismatch_rejection_rate" in error for error in errors)
    assert any("patient_identity_false_accept_rate" in error for error in errors)
    assert any("MISMATCH_ACCEPTED" in error for error in errors)


def test_extraction_thresholds_and_failure_reconciliation_are_rechecked():
    report = _passing_report()
    metrics = dict(report["metrics"])
    metrics["exact_occurrence_precision"] = 0.79
    report["metrics"] = metrics
    report["failure_classification"] = {"reconciliation_valid": False}

    errors = validate_report(report)

    assert any("exact_occurrence_precision" in error for error in errors)
    assert any("reconciliation_valid" in error for error in errors)


def test_committed_sanitized_replay_remains_truthfully_unqualified():
    result = asyncio.run(
        run(
            DOCUMENTS,
            MANIFEST,
            region="ap-south-1",
            timeout=30.0,
            attempts=1,
            replay_sanitized=REPLAY,
        )
    )

    errors = validate_report(result)

    assert result["benchmark_valid"] is False
    assert result["provider_error_counts"] == {}
    assert result["identity_outcome_counts"] == {
        "TRUE_MATCH_ACCEPTED": 13,
        "TRUE_MATCH_REJECTED": 1,
        "MISMATCH_REJECTED": 1,
        "MISMATCH_ACCEPTED": 0,
    }
    assert any("patient_identity_match_acceptance_rate" in error for error in errors)
    assert any("patient_identity_false_reject_rate" in error for error in errors)
