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

ACCURACY_METRICS = (
    "field_detection_precision",
    "field_detection_recall",
    "exact_raw_value_accuracy",
    "normalized_value_accuracy",
    "unit_accuracy",
    "repeated_field_recall",
    "table_row_accuracy",
    "source_text_accuracy",
    "page_accuracy",
    "bounding_box_presence_and_validity",
    "field_confidence_provenance",
    "false_positive_rate",
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

    for specification in specifications:
        counts["attempted"] += 1
        expected = specification.get("fields", [])
        expected_keys = Counter(item["canonical_field"] for item in expected)
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
        actual_keys = Counter(item.canonical_field_name for item in actual)
        detected = sum((expected_keys & actual_keys).values())
        counts.update(
            scored_expected=sum(expected_keys.values()),
            actual=sum(actual_keys.values()),
            detected=detected,
        )
        expected_tuples = Counter(
            (item["canonical_field"], item["raw_value"]) for item in expected
        )
        actual_tuples = Counter(
            (item.canonical_field_name, item.raw_value) for item in actual
        )
        counts["raw_exact"] += sum((expected_tuples & actual_tuples).values())
        for item in actual:
            match = next(
                (
                    target
                    for target in expected
                    if target["canonical_field"] == item.canonical_field_name
                    and target["raw_value"] == item.raw_value
                ),
                None,
            )
            if not match:
                continue
            counts["matched"] += 1
            counts["normalized_exact"] += item.normalized_value == match.get(
                "normalized_value"
            )
            counts["unit_exact"] += item.raw_unit == match.get("unit")
            counts["source_exact"] += item.source_text == match.get(
                "source_text", match["raw_value"]
            )
            counts["page_exact"] += item.page_number == match.get("page")
            counts["bbox_valid"] += item.bounding_box is not None
            counts["confidence_provenance"] += item.field_confidence is not None
            counts["table_exact"] += (
                item.source_type != "CELL" or item.structured_value is not None
            )
        repeated_expected = _expected_repeated_occurrences(expected)
        counts["repeated_expected"] += repeated_expected
        counts["repeated_detected"] += sum(
            min(value, actual_keys[key])
            for key, value in expected_keys.items()
            if value > 1
        )
        extracted_identity = {
            item.canonical_field_name: item.raw_value
            for item in actual
            if item.canonical_field_name in {"patient_name", "phone", "aadhaar_abha_id"}
        }
        bound_identity = specification.get("bound_identity", {})
        actual_match = bool(bound_identity) and all(
            extracted_identity.get(key) == value
            for key, value in bound_identity.items()
        )
        counts["identity_detection_correct"] += actual_match == bool(
            specification["patient_binding_matches"]
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
                "field_detection_precision": _ratio(
                    counts["detected"], counts["actual"]
                ),
                "field_detection_recall": _ratio(
                    counts["detected"], counts["scored_expected"]
                ),
                "exact_raw_value_accuracy": _ratio(
                    counts["raw_exact"], counts["scored_expected"]
                ),
                "normalized_value_accuracy": _ratio(
                    counts["normalized_exact"], counts["matched"]
                ),
                "unit_accuracy": _ratio(counts["unit_exact"], counts["matched"]),
                "repeated_field_recall": _ratio(
                    counts["repeated_detected"], counts["repeated_expected"]
                ),
                "table_row_accuracy": _ratio(counts["table_exact"], counts["matched"]),
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
                "false_positive_rate": _ratio(
                    counts["actual"] - counts["detected"], counts["actual"]
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
        "actual_field_occurrences": counts["actual"],
        "matched_field_occurrences": counts["matched"],
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
