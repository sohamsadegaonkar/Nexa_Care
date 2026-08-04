from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
import pytest

from app.ai.extractor import InvalidDocumentError
from app.models.ai_models import ExtractedMedicalDocument, ProviderFieldEvidence
from app.models.field_evidence import NormalizedBoundingBox
from scripts.run_textract_accuracy_benchmark import (
    ACCURACY_METRICS,
    INTERNAL_ERROR_CODE,
    benchmark_exit_code,
    evaluate_gates,
    run_benchmark,
)

BENCHMARK = Path(__file__).parent / "benchmark"
DOCUMENTS = BENCHMARK / "documents"
MANIFEST = json.loads(
    (BENCHMARK / "synthetic-manifest.json").read_text(encoding="utf-8")
)
SENSITIVE_MARKERS = (
    "Synthetic Patient Alpha",
    "7.2 %",
    "01-simple-laboratory-form.png",
    "provider secret message",
)


def _document(specification: dict[str, Any]) -> ExtractedMedicalDocument:
    timestamp = datetime(2026, 8, 5, tzinfo=timezone.utc)
    evidence = [
        ProviderFieldEvidence(
            canonical_field_name=field["canonical_field"],
            raw_value=field["raw_value"],
            source_text=field["source_text"],
            page_number=field["page"],
            bounding_box=NormalizedBoundingBox(
                left=0.1, top=0.1, right=0.2, bottom=0.2
            ),
            field_confidence=0.95,
            provider_name="aws_textract",
            provider_api_version="synthetic-test",
            extraction_timestamp=timestamp,
            source_type=field["source_category"],
            source_block_ids=(f"block-{index}",),
            normalized_value=field.get("normalized_value"),
            raw_unit=field.get("unit"),
            normalized_unit=field.get("unit"),
            structured_value=(
                {"row_index": str(index)}
                if field["source_category"] == "CELL"
                else None
            ),
        )
        for index, field in enumerate(specification["fields"])
    ]
    identity = {
        name: next(
            (item.raw_value for item in evidence if item.canonical_field_name == name),
            "",
        )
        for name in ("patient_name", "phone", "aadhaar_abha_id")
    }
    return ExtractedMedicalDocument(
        patient_name=identity["patient_name"],
        phone=identity["phone"],
        aadhaar_abha_id=identity["aadhaar_abha_id"],
        diagnoses=[],
        lab_results=[],
        prescriptions=[],
        extraction_confidence=None,
        field_evidence=evidence,
    )


class SequenceProvider:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    async def extract_bytes(self, *args, **kwargs) -> ExtractedMedicalDocument:
        _ = (args, kwargs)
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _successful_outcomes(manifest: dict[str, Any]) -> list[ExtractedMedicalDocument]:
    return [_document(specification) for specification in manifest["documents"]]


@pytest.mark.asyncio
async def test_all_provider_failures_are_invalid_null_and_sanitized(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        boto3,
        "client",
        lambda *args, **kwargs: pytest.fail("AWS client must not be constructed"),
    )
    failures = [
        InvalidDocumentError("provider secret message with raw clinical value")
        for _ in MANIFEST["documents"]
    ]
    result = await run_benchmark(DOCUMENTS, MANIFEST, SequenceProvider(failures))

    assert benchmark_exit_code(result) == 1
    assert result["benchmark_valid"] is False
    assert result["metrics_valid"] is False
    assert result["attempted_documents"] == 15
    assert result["successful_documents"] == 0
    assert result["failed_documents"] == 15
    assert result["provider_error_counts"] == {"INVALID_DOCUMENT": 15}
    assert all(result["metrics"][name] is None for name in ACCURACY_METRICS)
    assert result["metrics"]["successful_document_rate"] == 0.0
    assert result["metrics"]["unexpected_provider_failure_rate"] == 1.0
    serialized = json.dumps(result, sort_keys=True)
    assert all(marker not in serialized for marker in SENSITIVE_MARKERS)
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.asyncio
async def test_one_unexpected_failure_invalidates_otherwise_successful_corpus():
    outcomes: list[Any] = _successful_outcomes(MANIFEST)
    outcomes[4] = InvalidDocumentError("provider secret message")
    result = await run_benchmark(DOCUMENTS, MANIFEST, SequenceProvider(outcomes))
    assert result["attempted_documents"] == 15
    assert result["successful_documents"] == 14
    assert result["failed_documents"] == 1
    assert result["benchmark_valid"] is False
    assert benchmark_exit_code(result) == 1


@pytest.mark.asyncio
async def test_explicit_expected_rejection_is_not_an_unexpected_failure():
    manifest = deepcopy(MANIFEST)
    manifest["documents"][0]["expected_provider_rejection"] = True
    manifest["minimum_gates"]["successful_document_rate"] = 14 / 15
    outcomes: list[Any] = _successful_outcomes(manifest)
    outcomes[0] = InvalidDocumentError("expected sanitized rejection")
    result = await run_benchmark(DOCUMENTS, manifest, SequenceProvider(outcomes))
    assert result["failed_documents"] == 1
    assert result["metrics"]["unexpected_provider_failure_rate"] == 0.0
    assert result["benchmark_valid"] is True


@pytest.mark.asyncio
async def test_required_null_metric_fails_even_after_successful_provider_call():
    empty = ExtractedMedicalDocument(
        patient_name="",
        phone="",
        aadhaar_abha_id="",
        diagnoses=[],
        lab_results=[],
        prescriptions=[],
        extraction_confidence=None,
    )
    manifest = deepcopy(MANIFEST)
    manifest["documents"] = manifest["documents"][:1]
    result = await run_benchmark(DOCUMENTS, manifest, SequenceProvider([empty]))
    assert result["successful_documents"] == 1
    assert result["metrics"]["field_detection_precision"] is None
    assert result["metrics_valid"] is False
    assert result["benchmark_valid"] is False


def test_minimum_and_maximum_gate_directions_are_explicit():
    metrics = {
        "field_detection_recall": 0.8,
        "false_positive_rate": 0.1,
    }
    assert evaluate_gates(
        metrics,
        {"field_detection_recall": 0.75},
        {"false_positive_rate": 0.2},
    ) == {"field_detection_recall": True, "false_positive_rate": True}
    assert evaluate_gates(
        metrics,
        {"field_detection_recall": 0.85},
        {"false_positive_rate": 0.05},
    ) == {"field_detection_recall": False, "false_positive_rate": False}
    assert (
        evaluate_gates({"false_positive_rate": 0.9}, {}, {"false_positive_rate": 0.2})[
            "false_positive_rate"
        ]
        is False
    )


@pytest.mark.asyncio
async def test_unexpected_exception_fails_safely_without_message_or_filename():
    outcomes: list[Any] = _successful_outcomes(MANIFEST)
    outcomes[0] = RuntimeError(
        "provider secret message 01-simple-laboratory-form.png 7.2 %"
    )
    result = await run_benchmark(DOCUMENTS, MANIFEST, SequenceProvider(outcomes))
    assert result["provider_error_counts"] == {INTERNAL_ERROR_CODE: 1}
    assert result["benchmark_valid"] is False
    serialized = json.dumps(result, sort_keys=True)
    assert all(marker not in serialized for marker in SENSITIVE_MARKERS)


@pytest.mark.asyncio
async def test_fully_successful_injected_provider_passes_without_aws(monkeypatch):
    monkeypatch.setattr(
        boto3,
        "client",
        lambda *args, **kwargs: pytest.fail("AWS client must not be constructed"),
    )
    provider = SequenceProvider(_successful_outcomes(MANIFEST))
    result = await run_benchmark(DOCUMENTS, MANIFEST, provider)
    assert provider.calls == 15
    assert result["benchmark_valid"] is True
    assert result["metrics_valid"] is True
    assert result["attempted_documents"] == 15
    assert result["successful_documents"] == 15
    assert result["failed_documents"] == 0
    assert result["expected_field_occurrences"] == 53
    assert result["actual_field_occurrences"] == 53
    assert result["matched_field_occurrences"] == 53
    assert all(result["gate_results"].values())
    assert benchmark_exit_code(result) == 0
