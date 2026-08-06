"""Run an opt-in, aggregate-only synthetic Amazon Textract qualification.

The default command performs live AnalyzeDocument calls through the production
provider. Unit tests inject a provider and never call AWS. Output is aggregate,
sanitized, and fail-closed: undefined accuracy is serialized as null.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import mimetypes
import re
import sys
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ai.extractor import (  # noqa: E402
    AwsTextractExtractionProvider,
    DocumentExtractionError,
    ExtractionProvider,
)
from app.core.config import DocumentExtractionConfig  # noqa: E402
from app.ai.semantic_evidence import group_semantic_candidates  # noqa: E402
from app.ai.semantic_evidence import SemanticCandidate  # noqa: E402
from app.ai.candidate_eligibility import (  # noqa: E402
    CandidateEligibility,
    classify_semantic_candidate,
)
from app.ai.extraction_normalization import normalize_extracted_value  # noqa: E402
from app.ai.medical_validator import validate_field  # noqa: E402
from scripts.textract_sanitized_replay import (  # noqa: E402
    CaseIndexedCaptureProvider,
    SanitizedCaptureSession,
    SanitizedReplayProvider,
    validate_synthetic_benchmark_scope,
)
from scripts.textract_benchmark_failure_classification import (  # noqa: E402
    FailureClassificationAccumulator,
)

ACCURACY_METRICS = (
    "canonical_field_presence_recall",
    "exact_occurrence_precision",
    "exact_occurrence_recall",
    "exact_raw_value_accuracy",
    "evidence_support_rate",
    "duplicate_provenance_rate",
    "normalized_value_accuracy",
    "unit_accuracy",
    "repeated_field_recall",
    "table_row_accuracy",
    "source_text_accuracy",
    "page_accuracy",
    "bounding_box_presence_and_validity",
    "field_confidence_provenance",
    "patient_identity_mismatch_detection",
)
RATE_METRICS = (
    "successful_document_rate",
    "unexpected_provider_failure_rate",
)
METRICS = (*RATE_METRICS, *ACCURACY_METRICS)
SAFE_PROVIDER_ERROR_CODES = frozenset(
    {
        "EXTRACTION_CREDENTIALS_UNAVAILABLE",
        "INVALID_DOCUMENT",
        "EXTRACTION_PROVIDER_TIMEOUT",
        "EXTRACTION_PROVIDER_THROTTLED",
        "EXTRACTION_UPSTREAM_RETRYABLE",
        "EXTRACTION_RESPONSE_INVALID",
        "EXTRACTION_FAILED",
    }
)
INTERNAL_ERROR_CODE = "BENCHMARK_INTERNAL_ERROR"
IDENTITY_FIELDS = frozenset({"patient_name", "phone", "aadhaar_abha_id"})


def _ratio(numerator: int, denominator: int) -> float | None:
    """Return an ordinary ratio, or None when it is mathematically undefined."""
    return numerator / denominator if denominator else None


def evaluate_gates(
    metrics: dict[str, float | None],
    minimum_gates: dict[str, float],
    maximum_gates: dict[str, float],
) -> dict[str, bool]:
    """Evaluate explicitly directed gates; null metrics always fail."""
    results: dict[str, bool] = {}
    for name, threshold in minimum_gates.items():
        value = metrics.get(name)
        results[name] = value is not None and value >= threshold
    for name, threshold in maximum_gates.items():
        value = metrics.get(name)
        results[name] = value is not None and value <= threshold
    return results


def benchmark_exit_code(result: dict[str, Any]) -> int:
    """Return success only for a valid benchmark result."""
    return 0 if result.get("benchmark_valid") is True else 1


def _safe_provider_error_code(exc: DocumentExtractionError) -> str:
    code = exc.error_code
    return code if code in SAFE_PROVIDER_ERROR_CODES else "EXTRACTION_FAILED"


def _expected_repeated_occurrences(fields: list[dict[str, Any]]) -> int:
    counts = Counter(item["canonical_field"] for item in fields)
    return sum(value for value in counts.values() if value > 1)


def _empty_accuracy_metrics() -> dict[str, None]:
    return dict.fromkeys(ACCURACY_METRICS)


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1]


def _edit_distance_bucket(distance: int) -> str:
    if distance <= 2:
        return str(distance)
    if distance <= 5:
        return "3_to_5"
    return "greater_than_5"


def _comparison_flags(
    *,
    expected_raw: str,
    candidate_raw: str,
    expected_normalized: str | None,
    candidate_normalized: str | None,
    expected_unit: str | None,
    candidate_unit: str | None,
) -> dict[str, bool | str]:
    def collapse(value: str) -> str:
        return " ".join(value.split())

    def strip_punctuation(value: str) -> str:
        return collapse(re.sub(r"[^\w\s]", "", value))

    return {
        "normalized_value_equal": expected_normalized is not None
        and expected_normalized == candidate_normalized,
        "normalized_unit_equal": expected_unit == candidate_unit,
        "whitespace_collapsed_raw_equal": collapse(expected_raw)
        == collapse(candidate_raw),
        "casefold_raw_equal": expected_raw.casefold() == candidate_raw.casefold(),
        "punctuation_stripped_raw_equal": strip_punctuation(expected_raw)
        == strip_punctuation(candidate_raw),
        "expected_raw_contained_in_candidate": expected_raw in candidate_raw,
        "candidate_raw_contained_in_expected": candidate_raw in expected_raw,
        "same_token_count": len(expected_raw.split()) == len(candidate_raw.split()),
        "same_character_length": len(expected_raw) == len(candidate_raw),
        "edit_distance_bucket": _edit_distance_bucket(
            _edit_distance(expected_raw, candidate_raw)
        ),
    }


def _exact_match_occurrences(
    expected: list[dict[str, Any]],
    candidates: list[SemanticCandidate],
    candidate_indexes: set[int] | None = None,
) -> tuple[list[tuple[int, int]], set[int]]:
    """Match raw occurrences one-to-one using the established exact ranking."""
    available = (
        set(range(len(candidates)))
        if candidate_indexes is None
        else set(candidate_indexes)
    )
    matches: list[tuple[int, int]] = []
    for expected_index, target in enumerate(expected):
        eligible = [
            index
            for index in available
            if candidates[index].representative.canonical_field_name
            == target["canonical_field"]
            and candidates[index].representative.raw_value == target["raw_value"]
        ]
        if not eligible:
            continue

        def rank(index: int) -> tuple[int, int, int, int]:
            support = candidates[index].evidence
            category = target.get("source_category")
            return (
                -int(any(x.source_type == category for x in support)),
                -int(
                    any(
                        x.source_type == category
                        and x.source_text
                        == target.get("source_text", target["raw_value"])
                        for x in support
                    )
                ),
                -int(any(x.page_number == target.get("page") for x in support)),
                index,
            )

        chosen = min(eligible, key=rank)
        available.remove(chosen)
        matches.append((expected_index, chosen))
    return matches, available


def semantic_match_occurrences(
    expected: list[dict[str, Any]],
    candidates: list[SemanticCandidate],
    candidate_indexes: set[int] | None = None,
    seed_matches: list[tuple[int, int]] | None = None,
) -> list[tuple[int, int]]:
    """Match normalized non-identity occurrences one-to-one, deterministically."""
    available = (
        set(range(len(candidates)))
        if candidate_indexes is None
        else set(candidate_indexes)
    )
    matches = list(seed_matches or [])
    matched_expected = {expected_index for expected_index, _ in matches}
    for _, candidate_index in matches:
        available.discard(candidate_index)
    for expected_index, target in enumerate(expected):
        if expected_index in matched_expected:
            continue
        field = target["canonical_field"]
        expected_normalized = target.get("normalized_value")
        expected_unit = target.get("unit")
        if expected_unit is None:
            expected_unit = normalize_extracted_value(field, target["raw_value"]).unit
        if field in IDENTITY_FIELDS or not expected_normalized:
            continue
        eligible = [
            index
            for index in available
            if (
                candidates[index].representative.canonical_field_name == field
                and candidates[index].representative.normalized_value
                and candidates[index].representative.normalized_value
                == expected_normalized
                and (candidates[index].representative.normalized_unit or "").casefold()
                == (expected_unit or "").casefold()
            )
        ]
        if not eligible:
            continue

        def rank(index: int) -> tuple[int, int, int, int, int]:
            candidate = candidates[index].representative
            support = candidates[index].evidence
            category = target.get("source_category")
            return (
                -int(candidate.raw_value == target["raw_value"]),
                -int(any(item.source_type == category for item in support)),
                -int(any(item.page_number == target.get("page") for item in support)),
                -int(candidate.raw_unit == expected_unit),
                index,
            )

        chosen = min(eligible, key=rank)
        available.remove(chosen)
        matches.append((expected_index, chosen))
        matched_expected.add(expected_index)
    return matches


async def run_benchmark(
    directory: Path,
    manifest: dict[str, Any],
    provider: ExtractionProvider,
    *,
    debug: bool = False,
) -> dict[str, Any]:
    """Execute the production benchmark logic with an injectable provider."""
    specifications = manifest.get("documents", [])
    manifest_document_count = len(specifications)
    expected_field_occurrences = sum(
        len(specification.get("fields", [])) for specification in specifications
    )
    counts: Counter[str] = Counter()
    provider_errors: Counter[str] = Counter()
    by_source: Counter[str] = Counter()
    page_by_source: Counter[str] = Counter()
    exact_by_field: Counter[str] = Counter()
    unmatched_expected_by_field: Counter[str] = Counter()
    unmatched_candidate_by_field: Counter[str] = Counter()
    page_missing_by_source: Counter[str] = Counter()
    source_text_match_by_category: Counter[str] = Counter()
    source_text_mismatch_by_category: Counter[str] = Counter()
    identity_incorrect_case_indexes: list[int] = []
    unmatched_expected_cases: dict[str, set[int]] = {}
    unmatched_candidate_cases: dict[str, set[int]] = {}
    unmatched_candidate_signatures: dict[str, Counter[str]] = {}
    identity_failure_reasons: dict[str, Counter[str]] = {}
    unmatched_pair_diagnostics: dict[tuple[int, str], dict[str, Any]] = {}
    query_diagnostic_counts: Counter[tuple[Any, ...]] = Counter()
    query_only_by_field: Counter[str] = Counter()
    identity_failure_diagnostics: list[dict[str, Any]] = []
    semantic_counts: Counter[str] = Counter()
    semantic_matches_added_by_field: Counter[str] = Counter()
    routing_counts: Counter[str] = Counter()
    routing_ineligible_reasons: Counter[str] = Counter()
    routing_ineligible_by_field: Counter[str] = Counter()
    routing_ineligible_cases: dict[str, set[int]] = {}
    failure_classification = FailureClassificationAccumulator()

    for case_index, specification in enumerate(specifications, start=1):
        counts["attempted"] += 1
        expected = specification.get("fields", [])
        try:
            set_case_index = getattr(provider, "set_benchmark_case_index", None)
            if callable(set_case_index):
                set_case_index(case_index)
            path = (directory / specification["file"]).resolve()
            if directory.resolve() not in path.parents or not path.is_file():
                raise ValueError("Benchmark document path is invalid")
            mime = mimetypes.guess_type(path.name)[0]
            if mime not in {
                "application/pdf",
                "image/png",
                "image/jpeg",
                "image/tiff",
            }:
                raise ValueError("Benchmark document type is unsupported")
            result = await provider.extract_bytes(
                path.read_bytes(),
                mime_type=mime,
                request_id="synthetic-accuracy-benchmark",
            )
        except DocumentExtractionError as exc:
            counts["failed"] += 1
            provider_errors[_safe_provider_error_code(exc)] += 1
            if not specification.get("expected_provider_rejection", False):
                counts["unexpected_failed"] += 1
            continue
        except Exception as exc:  # safe benchmark boundary
            counts["failed"] += 1
            counts["unexpected_failed"] += 1
            provider_errors[INTERNAL_ERROR_CODE] += 1
            if debug:
                safe = RuntimeError(INTERNAL_ERROR_CODE)
                traceback.print_exception(type(safe), safe, exc.__traceback__)
            continue

        counts["successful"] += 1
        actual = result.field_evidence
        candidates = group_semantic_candidates(actual)
        counts["evidence"] += len(actual)
        counts["candidates"] += len(candidates)
        counts["supported"] += sum(bool(candidate.evidence) for candidate in candidates)
        counts["duplicate_provenance"] += len(actual) - len(candidates)
        counts["scored_expected"] += len(expected)
        for item in actual:
            source = item.source_type or "UNKNOWN"
            by_source[source] += 1
            if item.page_number is None:
                counts["page_missing"] += 1
                page_missing_by_source[source] += 1
            else:
                counts["page_present"] += 1
                page_by_source[source] += 1

        expected_types = {item["canonical_field"] for item in expected}
        actual_types = {
            candidate.representative.canonical_field_name for candidate in candidates
        }
        counts["expected_types"] += len(expected_types)
        counts["present_types"] += len(expected_types & actual_types)

        available = set(range(len(candidates)))
        matches: list[tuple[int, int]] = []
        for expected_index, target in enumerate(expected):
            eligible = [
                index
                for index in available
                if candidates[index].representative.canonical_field_name
                == target["canonical_field"]
                and candidates[index].representative.raw_value == target["raw_value"]
            ]
            if not eligible:
                unmatched_expected_by_field[target["canonical_field"]] += 1
                unmatched_expected_cases.setdefault(
                    target["canonical_field"], set()
                ).add(case_index)
                continue

            def rank(index: int) -> tuple[int, int, int, int]:
                support = candidates[index].evidence
                category = target.get("source_category")
                return (
                    -int(any(x.source_type == category for x in support)),
                    -int(
                        any(
                            x.source_type == category
                            and x.source_text
                            == target.get("source_text", target["raw_value"])
                            for x in support
                        )
                    ),
                    -int(any(x.page_number == target.get("page") for x in support)),
                    index,
                )

            chosen = min(eligible, key=rank)
            available.remove(chosen)
            matches.append((expected_index, chosen))
            exact_by_field[target["canonical_field"]] += 1

        exact_match_pairs = set(matches)
        semantic_matches = semantic_match_occurrences(
            expected, candidates, seed_matches=matches
        )
        semantic_counts["matches"] += len(semantic_matches)
        semantic_counts["raw_exact"] += sum(
            candidates[candidate_index].representative.raw_value
            == expected[expected_index]["raw_value"]
            for expected_index, candidate_index in semantic_matches
        )
        semantic_counts["unmatched_expected"] += len(expected) - len(semantic_matches)
        semantic_counts["unmatched_candidates"] += len(candidates) - len(
            semantic_matches
        )
        for expected_index, candidate_index in semantic_matches:
            if (expected_index, candidate_index) not in exact_match_pairs:
                semantic_matches_added_by_field[
                    expected[expected_index]["canonical_field"]
                ] += 1

        eligible_indexes: set[int] = set()
        eligibility_by_index: dict[int, CandidateEligibility] = {}
        for candidate_index, candidate in enumerate(candidates):
            classification = classify_semantic_candidate(candidate)
            eligibility_by_index[candidate_index] = classification
            if classification is CandidateEligibility.ELIGIBLE:
                eligible_indexes.add(candidate_index)
                routing_counts["eligible"] += 1
            else:
                routing_counts["ineligible"] += 1
                routing_ineligible_reasons[classification.value] += 1
                field = candidate.representative.canonical_field_name
                routing_ineligible_by_field[field] += 1
                routing_ineligible_cases.setdefault(field, set()).add(case_index)

        routing_exact_matches, _ = _exact_match_occurrences(
            expected, candidates, eligible_indexes
        )
        routing_semantic_matches = semantic_match_occurrences(
            expected,
            candidates,
            eligible_indexes,
            seed_matches=routing_exact_matches,
        )
        routing_counts["exact_matches"] += len(routing_exact_matches)
        routing_counts["semantic_matches"] += len(routing_semantic_matches)

        for index in sorted(available):
            candidate = candidates[index]
            field = candidate.representative.canonical_field_name
            unmatched_candidate_by_field[field] += 1
            unmatched_candidate_cases.setdefault(field, set()).add(case_index)
            signature = "+".join(
                sorted(
                    {
                        item.source_type
                        for item in candidate.evidence
                        if item.source_type
                    }
                )
            )
            if signature:
                unmatched_candidate_signatures.setdefault(field, Counter())[
                    signature
                ] += 1
            if any(item.source_type == "QUERY_RESULT" for item in candidate.evidence):
                non_query_support = any(
                    item.source_type in {"KEY_VALUE_SET", "CELL"}
                    for item in candidate.evidence
                )
                another_exact = any(
                    candidates[matched_index].representative.canonical_field_name
                    == field
                    for _, matched_index in matches
                )
                valid_format = validate_field(
                    field, candidate.representative.raw_value
                ).is_valid
                query_diagnostic_counts[
                    (
                        case_index,
                        field,
                        signature,
                        valid_format,
                        non_query_support,
                        another_exact,
                    )
                ] += 1
                if not non_query_support:
                    query_only_by_field[field] += 1

        matched_expected_indexes = {index for index, _ in matches}
        fields_with_unmatched = {
            item["canonical_field"]
            for index, item in enumerate(expected)
            if index not in matched_expected_indexes
        } | {
            candidates[index].representative.canonical_field_name for index in available
        }
        for field in sorted(fields_with_unmatched):
            expected_indexes = [
                index
                for index, item in enumerate(expected)
                if index not in matched_expected_indexes
                and item["canonical_field"] == field
            ]
            candidate_indexes = [
                index
                for index in sorted(available)
                if candidates[index].representative.canonical_field_name == field
            ]
            for expected_index, candidate_index in zip(
                expected_indexes, candidate_indexes, strict=False
            ):
                target = expected[expected_index]
                candidate = candidates[candidate_index].representative
                flags = _comparison_flags(
                    expected_raw=target["raw_value"],
                    candidate_raw=candidate.raw_value,
                    expected_normalized=target.get("normalized_value"),
                    candidate_normalized=candidate.normalized_value,
                    expected_unit=target.get("unit"),
                    candidate_unit=candidate.normalized_unit,
                )
                aggregate = unmatched_pair_diagnostics.setdefault(
                    (case_index, field),
                    {
                        "case_index": case_index,
                        "canonical_field": field,
                        "pair_count": 0,
                        "boolean_counts": {
                            key: {"true": 0, "false": 0}
                            for key in flags
                            if key != "edit_distance_bucket"
                        },
                        "edit_distance_bucket_counts": {},
                    },
                )
                aggregate["pair_count"] += 1
                for key, value in flags.items():
                    if key == "edit_distance_bucket":
                        buckets = aggregate["edit_distance_bucket_counts"]
                        buckets[value] = buckets.get(value, 0) + 1
                    else:
                        aggregate["boolean_counts"][key][str(value).lower()] += 1
        counts["matched"] += len(matches)
        counts["unmatched_expected"] += len(expected) - len(matches)
        counts["unmatched_candidates"] += len(available)
        repeated_fields = {
            name
            for name, total in Counter(x["canonical_field"] for x in expected).items()
            if total > 1
        }
        counts["repeated_expected"] += sum(
            x["canonical_field"] in repeated_fields for x in expected
        )
        counts["repeated_detected"] += sum(
            expected[i]["canonical_field"] in repeated_fields for i, _ in matches
        )
        table_expected = sum(x.get("source_category") == "CELL" for x in expected)
        counts["table_expected"] += table_expected
        for expected_index, candidate_index in matches:
            target = expected[expected_index]
            support = candidates[candidate_index].evidence
            counts["normalized_exact"] += any(
                item.normalized_value == target.get("normalized_value")
                for item in support
            )
            counts["unit_exact"] += any(
                item.raw_unit == target.get("unit") for item in support
            )
            compatible = [
                x for x in support if x.source_type == target.get("source_category")
            ]
            source_exact = any(
                x.source_text == target.get("source_text", target["raw_value"])
                for x in compatible
            )
            counts["source_exact"] += source_exact
            category = target.get("source_category")
            if isinstance(category, str):
                (
                    source_text_match_by_category
                    if source_exact
                    else source_text_mismatch_by_category
                )[category] += 1
            counts["page_exact"] += any(
                x.page_number == target.get("page") for x in support
            )
            counts["bbox_valid"] += any(x.bounding_box is not None for x in support)
            counts["confidence_provenance"] += any(
                x.field_confidence is not None for x in support
            )
            if target.get("source_category") == "CELL":
                counts["table_exact"] += any(
                    x.source_type == "CELL"
                    and x.structured_value is not None
                    and x.source_text == target.get("source_text")
                    for x in support
                )
        extracted_identity = {
            key: {item.raw_value for item in actual if item.canonical_field_name == key}
            for key in {"patient_name", "phone", "aadhaar_abha_id"}
        }
        bound_identity = specification.get("bound_identity", {})
        identity_statuses: dict[str, str] = {}
        for key, value in bound_identity.items():
            values = extracted_identity[key]
            if not values:
                status = "missing"
            elif value not in values:
                status = "nonmatching"
            elif values != {value}:
                status = "conflicting"
            else:
                status = "exact"
            identity_statuses[key] = status
            if status != "exact":
                identity_failure_reasons.setdefault(key, Counter())[status] += 1
        failure_classification.add_case(
            case_index=case_index,
            expected=expected,
            candidates=candidates,
            exact_matches=matches,
            semantic_matches=semantic_matches,
            identity_statuses=identity_statuses,
            eligibility_by_index=eligibility_by_index,
        )
        actual_match = bool(bound_identity) and all(
            status == "exact" for status in identity_statuses.values()
        )
        identity_correct = actual_match == bool(
            specification["patient_binding_matches"]
        )
        counts["identity_detection_correct"] += identity_correct
        counts[
            "identity_cases_correct" if identity_correct else "identity_cases_incorrect"
        ] += 1
        if not identity_correct:
            identity_incorrect_case_indexes.append(case_index)
            for key, status in sorted(identity_statuses.items()):
                if status == "exact":
                    continue
                values = extracted_identity[key]
                bound_value = bound_identity[key]
                comparison_value = next(
                    (value for value in sorted(values) if value != bound_value), ""
                )
                if comparison_value:
                    flags = _comparison_flags(
                        expected_raw=bound_value,
                        candidate_raw=comparison_value,
                        expected_normalized=normalize_extracted_value(
                            key, bound_value
                        ).value,
                        candidate_normalized=normalize_extracted_value(
                            key, comparison_value
                        ).value,
                        expected_unit=None,
                        candidate_unit=None,
                    )
                else:
                    flags = {
                        "normalized_value_equal": False,
                        "same_token_count": False,
                        "same_character_length": False,
                        "edit_distance_bucket": "greater_than_5",
                    }
                identity_failure_diagnostics.append(
                    {
                        "case_index": case_index,
                        "canonical_field": key,
                        "status": status,
                        "normalized_equivalent": flags["normalized_value_equal"],
                        "same_token_count": flags["same_token_count"],
                        "same_character_length": flags["same_character_length"],
                        "edit_distance_bucket": flags["edit_distance_bucket"],
                    }
                )

    attempted = counts["attempted"]
    successful = counts["successful"]
    failed = counts["failed"]
    metrics: dict[str, float | None] = {
        "successful_document_rate": _ratio(successful, attempted),
        "unexpected_provider_failure_rate": _ratio(
            counts["unexpected_failed"], attempted
        ),
        **_empty_accuracy_metrics(),
    }
    if successful:
        metrics.update(
            {
                "canonical_field_presence_recall": _ratio(
                    counts["present_types"], counts["expected_types"]
                ),
                "exact_occurrence_precision": _ratio(
                    counts["matched"], counts["candidates"]
                ),
                "exact_occurrence_recall": _ratio(
                    counts["matched"], counts["scored_expected"]
                ),
                "exact_raw_value_accuracy": _ratio(
                    counts["matched"], counts["scored_expected"]
                ),
                "evidence_support_rate": _ratio(
                    counts["supported"], counts["candidates"]
                ),
                "duplicate_provenance_rate": _ratio(
                    counts["duplicate_provenance"], counts["evidence"]
                ),
                "normalized_value_accuracy": _ratio(
                    counts["normalized_exact"], counts["matched"]
                ),
                "unit_accuracy": _ratio(counts["unit_exact"], counts["matched"]),
                "repeated_field_recall": _ratio(
                    counts["repeated_detected"], counts["repeated_expected"]
                ),
                "table_row_accuracy": _ratio(
                    counts["table_exact"], counts["table_expected"]
                ),
                "source_text_accuracy": _ratio(
                    counts["source_exact"], counts["matched"]
                ),
                "page_accuracy": _ratio(counts["page_exact"], counts["matched"]),
                "bounding_box_presence_and_validity": _ratio(
                    counts["bbox_valid"], counts["matched"]
                ),
                "field_confidence_provenance": _ratio(
                    counts["confidence_provenance"], counts["matched"]
                ),
                "patient_identity_mismatch_detection": _ratio(
                    counts["identity_detection_correct"], successful
                ),
            }
        )

    metrics_valid = all(metrics[name] is not None for name in METRICS)
    gate_results = evaluate_gates(
        metrics,
        manifest.get("minimum_gates", {}),
        manifest.get("maximum_gates", {}),
    )
    benchmark_valid = all(
        (
            attempted == manifest_document_count,
            successful > 0,
            counts["unexpected_failed"] == 0,
            metrics_valid,
            all(gate_results.values()),
        )
    )
    return {
        "benchmark_valid": benchmark_valid,
        "metrics_valid": metrics_valid,
        "attempted_documents": attempted,
        "successful_documents": successful,
        "failed_documents": failed,
        "expected_field_occurrences": expected_field_occurrences,
        "actual_field_occurrences": counts["evidence"],
        "matched_field_occurrences": counts["matched"],
        "evidence_occurrences": counts["evidence"],
        "semantic_candidate_occurrences": counts["candidates"],
        "supporting_evidence_occurrences": counts["evidence"],
        "duplicate_provenance_occurrences": counts["duplicate_provenance"],
        "unmatched_semantic_candidates": counts["unmatched_candidates"],
        "unmatched_expected_occurrences": counts["unmatched_expected"],
        "evidence_occurrences_by_source_type": dict(sorted(by_source.items())),
        "semantic_candidate_count": counts["candidates"],
        "exact_match_count": counts["matched"],
        "unmatched_expected_count": counts["unmatched_expected"],
        "unmatched_candidate_count": counts["unmatched_candidates"],
        "duplicate_provenance_count": counts["duplicate_provenance"],
        "page_present_count": counts["page_present"],
        "page_missing_count": counts["page_missing"],
        "page_present_by_source_type": dict(sorted(page_by_source.items())),
        "page_missing_count_by_source_type": dict(
            sorted(page_missing_by_source.items())
        ),
        "exact_matches_by_canonical_field": dict(sorted(exact_by_field.items())),
        "unmatched_expected_by_canonical_field": dict(
            sorted(unmatched_expected_by_field.items())
        ),
        "unmatched_candidates_by_canonical_field": dict(
            sorted(unmatched_candidate_by_field.items())
        ),
        "identity_cases_correct": counts["identity_cases_correct"],
        "identity_cases_incorrect": counts["identity_cases_incorrect"],
        "identity_incorrect_case_indexes": identity_incorrect_case_indexes,
        "unmatched_expected_case_indexes_by_canonical_field": {
            key: sorted(value)
            for key, value in sorted(unmatched_expected_cases.items())
        },
        "unmatched_candidate_case_indexes_by_canonical_field": {
            key: sorted(value)
            for key, value in sorted(unmatched_candidate_cases.items())
        },
        "unmatched_candidate_support_signatures": {
            key: dict(sorted(value.items()))
            for key, value in sorted(unmatched_candidate_signatures.items())
        },
        "source_text_match_count_by_source_category": dict(
            sorted(source_text_match_by_category.items())
        ),
        "source_text_mismatch_count_by_source_category": dict(
            sorted(source_text_mismatch_by_category.items())
        ),
        "identity_failure_reason_counts_by_canonical_field": {
            key: dict(sorted(value.items()))
            for key, value in sorted(identity_failure_reasons.items())
        },
        "unmatched_pair_diagnostics": [
            unmatched_pair_diagnostics[key]
            for key in sorted(unmatched_pair_diagnostics)
        ],
        "unmatched_query_candidate_diagnostics": [
            {
                "case_index": key[0],
                "canonical_field": key[1],
                "source_signature": key[2],
                "field_format_valid": key[3],
                "compatible_form_or_table_evidence": key[4],
                "another_exact_candidate_for_field_matched": key[5],
                "count": count,
            }
            for key, count in sorted(query_diagnostic_counts.items())
        ],
        "query_only_candidate_count_by_canonical_field": dict(
            sorted(query_only_by_field.items())
        ),
        "identity_failure_diagnostics": identity_failure_diagnostics,
        "semantic_occurrence_match_count": semantic_counts["matches"],
        "semantic_occurrence_precision": _ratio(
            semantic_counts["matches"], counts["candidates"]
        ),
        "semantic_occurrence_recall": _ratio(
            semantic_counts["matches"], counts["scored_expected"]
        ),
        "semantic_raw_exact_count": semantic_counts["raw_exact"],
        "semantic_raw_exact_rate": _ratio(
            semantic_counts["raw_exact"], semantic_counts["matches"]
        ),
        "semantic_matches_added_beyond_exact": sum(
            semantic_matches_added_by_field.values()
        ),
        "semantic_unmatched_expected_count": semantic_counts["unmatched_expected"],
        "semantic_unmatched_candidate_count": semantic_counts["unmatched_candidates"],
        "semantic_matches_added_by_canonical_field": dict(
            sorted(semantic_matches_added_by_field.items())
        ),
        "routing_eligible_candidate_count": routing_counts["eligible"],
        "routing_ineligible_candidate_count": routing_counts["ineligible"],
        "routing_ineligible_count_by_reason": dict(
            sorted(routing_ineligible_reasons.items())
        ),
        "routing_ineligible_count_by_canonical_field": dict(
            sorted(routing_ineligible_by_field.items())
        ),
        "routing_ineligible_case_indexes_by_canonical_field": {
            key: sorted(value)
            for key, value in sorted(routing_ineligible_cases.items())
        },
        "routing_exact_match_count": routing_counts["exact_matches"],
        "routing_exact_occurrence_precision": _ratio(
            routing_counts["exact_matches"], routing_counts["eligible"]
        ),
        "routing_exact_occurrence_recall": _ratio(
            routing_counts["exact_matches"], counts["scored_expected"]
        ),
        "routing_semantic_match_count": routing_counts["semantic_matches"],
        "routing_semantic_occurrence_precision": _ratio(
            routing_counts["semantic_matches"], routing_counts["eligible"]
        ),
        "routing_semantic_occurrence_recall": _ratio(
            routing_counts["semantic_matches"], counts["scored_expected"]
        ),
        "provider_error_counts": dict(sorted(provider_errors.items())),
        "metrics": metrics,
        "gate_results": gate_results,
        "failure_classification": failure_classification.result(
            expected_field_occurrences=expected_field_occurrences,
            exact_match_count=counts["matched"],
            semantic_match_count=semantic_counts["matches"],
            semantic_candidate_count=counts["candidates"],
            evidence_occurrences=counts["evidence"],
            routing_eligible_count=routing_counts["eligible"],
            routing_ineligible_count=routing_counts["ineligible"],
        ),
    }


async def run(
    directory: Path,
    manifest_path: Path,
    *,
    region: str,
    timeout: float,
    attempts: int,
    provider: ExtractionProvider | None = None,
    debug: bool = False,
    capture_sanitized_replay: Path | None = None,
    replay_sanitized: Path | None = None,
) -> dict[str, Any]:
    """Load the manifest and run with the real provider unless one is injected."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if capture_sanitized_replay is not None and replay_sanitized is not None:
        raise ValueError("CAPTURE_AND_REPLAY_ARE_MUTUALLY_EXCLUSIVE")
    if capture_sanitized_replay is not None or replay_sanitized is not None:
        validate_synthetic_benchmark_scope(directory, manifest_path, manifest)
    capture_session: SanitizedCaptureSession | None = None
    if replay_sanitized is not None:
        if provider is not None:
            raise ValueError("REPLAY_PROVIDER_OVERRIDE_FORBIDDEN")
        selected_provider: ExtractionProvider = SanitizedReplayProvider(
            replay_sanitized, len(manifest.get("documents", []))
        )
        provider_mode = "sanitized_replay"
    elif capture_sanitized_replay is not None:
        if provider is not None:
            raise ValueError("CAPTURE_PROVIDER_OVERRIDE_FORBIDDEN")
        capture_session = SanitizedCaptureSession(
            capture_sanitized_replay, len(manifest.get("documents", []))
        )
        holder: dict[str, CaseIndexedCaptureProvider] = {}
        aws_provider = AwsTextractExtractionProvider(
            DocumentExtractionConfig(
                provider="aws_textract",
                environment="benchmark",
                aws_region=region,
                timeout_seconds=timeout,
                max_attempts=attempts,
            ),
            successful_response_observer=lambda response: holder["provider"].observe(
                response
            ),
        )
        capture_provider = CaseIndexedCaptureProvider(aws_provider, capture_session)
        holder["provider"] = capture_provider
        selected_provider = capture_provider
        provider_mode = "live_capture"
    else:
        selected_provider = provider or AwsTextractExtractionProvider(
            DocumentExtractionConfig(
                provider="aws_textract",
                environment="benchmark",
                aws_region=region,
                timeout_seconds=timeout,
                max_attempts=attempts,
            )
        )
        provider_mode = "injected" if provider is not None else "live"
    result = await run_benchmark(directory, manifest, selected_provider, debug=debug)
    if capture_session is not None:
        result["captured_fixture_count"] = capture_session.finalize()
    result["provider_mode"] = provider_mode
    result["live_provider_calls"] = (
        0 if provider_mode == "sanitized_replay" else result["attempted_documents"]
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("documents", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--region", default="ap-south-1")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--attempts", type=int, default=2)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--capture-sanitized-replay", type=Path)
    modes.add_argument("--replay-sanitized", type=Path)
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print a sanitized local traceback for internal benchmark errors",
    )
    args = parser.parse_args()
    # The CLI owns stdout/stderr in aggregate mode; provider event logs would
    # violate the single-JSON-object output contract.
    logging.getLogger("nexa_logger").disabled = True
    try:
        result = asyncio.run(
            run(
                args.documents,
                args.manifest,
                region=args.region,
                timeout=args.timeout,
                attempts=args.attempts,
                debug=args.debug,
                capture_sanitized_replay=args.capture_sanitized_replay,
                replay_sanitized=args.replay_sanitized,
            )
        )
    except Exception as exc:  # manifest/config boundary before per-document execution
        if args.debug:
            safe = RuntimeError(INTERNAL_ERROR_CODE)
            traceback.print_exception(type(safe), safe, exc.__traceback__)
        result = {
            "benchmark_valid": False,
            "metrics_valid": False,
            "attempted_documents": 0,
            "successful_documents": 0,
            "failed_documents": 0,
            "expected_field_occurrences": 0,
            "actual_field_occurrences": 0,
            "matched_field_occurrences": 0,
            "evidence_occurrences": 0,
            "semantic_candidate_occurrences": 0,
            "supporting_evidence_occurrences": 0,
            "duplicate_provenance_occurrences": 0,
            "unmatched_semantic_candidates": 0,
            "unmatched_expected_occurrences": 0,
            "evidence_occurrences_by_source_type": {},
            "semantic_candidate_count": 0,
            "exact_match_count": 0,
            "unmatched_expected_count": 0,
            "unmatched_candidate_count": 0,
            "duplicate_provenance_count": 0,
            "page_present_count": 0,
            "page_missing_count": 0,
            "page_present_by_source_type": {},
            "page_missing_count_by_source_type": {},
            "exact_matches_by_canonical_field": {},
            "unmatched_expected_by_canonical_field": {},
            "unmatched_candidates_by_canonical_field": {},
            "identity_cases_correct": 0,
            "identity_cases_incorrect": 0,
            "identity_incorrect_case_indexes": [],
            "unmatched_expected_case_indexes_by_canonical_field": {},
            "unmatched_candidate_case_indexes_by_canonical_field": {},
            "unmatched_candidate_support_signatures": {},
            "source_text_match_count_by_source_category": {},
            "source_text_mismatch_count_by_source_category": {},
            "identity_failure_reason_counts_by_canonical_field": {},
            "unmatched_pair_diagnostics": [],
            "unmatched_query_candidate_diagnostics": [],
            "query_only_candidate_count_by_canonical_field": {},
            "identity_failure_diagnostics": [],
            "semantic_occurrence_match_count": 0,
            "semantic_occurrence_precision": None,
            "semantic_occurrence_recall": None,
            "semantic_raw_exact_count": 0,
            "semantic_raw_exact_rate": None,
            "semantic_matches_added_beyond_exact": 0,
            "semantic_unmatched_expected_count": 0,
            "semantic_unmatched_candidate_count": 0,
            "semantic_matches_added_by_canonical_field": {},
            "routing_eligible_candidate_count": 0,
            "routing_ineligible_candidate_count": 0,
            "routing_ineligible_count_by_reason": {},
            "routing_ineligible_count_by_canonical_field": {},
            "routing_ineligible_case_indexes_by_canonical_field": {},
            "routing_exact_match_count": 0,
            "routing_exact_occurrence_precision": None,
            "routing_exact_occurrence_recall": None,
            "routing_semantic_match_count": 0,
            "routing_semantic_occurrence_precision": None,
            "routing_semantic_occurrence_recall": None,
            "provider_error_counts": {INTERNAL_ERROR_CODE: 1},
            "metrics": {
                "successful_document_rate": None,
                "unexpected_provider_failure_rate": None,
                **_empty_accuracy_metrics(),
            },
            "gate_results": {},
            "provider_mode": (
                "sanitized_replay" if args.replay_sanitized is not None else "live"
            ),
            "live_provider_calls": 0 if args.replay_sanitized is not None else 0,
        }
    if "failure_classification" not in result:
        result["failure_classification"] = FailureClassificationAccumulator().result(
            expected_field_occurrences=0,
            exact_match_count=0,
            semantic_match_count=0,
            semantic_candidate_count=0,
            evidence_occurrences=0,
            routing_eligible_count=0,
            routing_ineligible_count=0,
        )
    print(json.dumps(result, sort_keys=True))
    return benchmark_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
