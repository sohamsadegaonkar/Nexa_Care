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

    for specification in specifications:
        counts["attempted"] += 1
        expected = specification.get("fields", [])
        try:
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

        for index in sorted(available):
            unmatched_candidate_by_field[
                candidates[index].representative.canonical_field_name
            ] += 1
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
            counts["source_exact"] += any(
                x.source_text == target.get("source_text", target["raw_value"])
                for x in compatible
            )
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
        actual_match = bool(bound_identity) and all(
            extracted_identity[key] == {value} for key, value in bound_identity.items()
        )
        identity_correct = actual_match == bool(
            specification["patient_binding_matches"]
        )
        counts["identity_detection_correct"] += identity_correct
        counts[
            "identity_cases_correct" if identity_correct else "identity_cases_incorrect"
        ] += 1

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
        "exact_matches_by_canonical_field": dict(sorted(exact_by_field.items())),
        "unmatched_expected_by_canonical_field": dict(
            sorted(unmatched_expected_by_field.items())
        ),
        "unmatched_candidates_by_canonical_field": dict(
            sorted(unmatched_candidate_by_field.items())
        ),
        "identity_cases_correct": counts["identity_cases_correct"],
        "identity_cases_incorrect": counts["identity_cases_incorrect"],
        "provider_error_counts": dict(sorted(provider_errors.items())),
        "metrics": metrics,
        "gate_results": gate_results,
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
) -> dict[str, Any]:
    """Load the manifest and run with the real provider unless one is injected."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selected_provider = provider or AwsTextractExtractionProvider(
        DocumentExtractionConfig(
            provider="aws_textract",
            environment="benchmark",
            aws_region=region,
            timeout_seconds=timeout,
            max_attempts=attempts,
        )
    )
    return await run_benchmark(directory, manifest, selected_provider, debug=debug)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("documents", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--region", default="ap-south-1")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--attempts", type=int, default=2)
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
            "exact_matches_by_canonical_field": {},
            "unmatched_expected_by_canonical_field": {},
            "unmatched_candidates_by_canonical_field": {},
            "identity_cases_correct": 0,
            "identity_cases_incorrect": 0,
            "provider_error_counts": {INTERNAL_ERROR_CODE: 1},
            "metrics": {
                "successful_document_rate": None,
                "unexpected_provider_failure_rate": None,
                **_empty_accuracy_metrics(),
            },
            "gate_results": {},
        }
    print(json.dumps(result, sort_keys=True))
    return benchmark_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
