"""Run an opt-in, aggregate-only synthetic Amazon Textract qualification.

This script performs live AnalyzeDocument calls through the production provider.
It must never be pointed at real patient documents and is not run by pytest.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ai.extractor import (  # noqa: E402
    AwsTextractExtractionProvider,
    DocumentExtractionError,
)
from app.core.config import DocumentExtractionConfig  # noqa: E402

METRICS = (
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
    "fail_closed_quarantine_rate",
)


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


async def run(
    directory: Path, manifest_path: Path, *, region: str, timeout: float, attempts: int
) -> dict[str, float]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    provider = AwsTextractExtractionProvider(
        DocumentExtractionConfig(
            provider="aws_textract",
            environment="benchmark",
            aws_region=region,
            timeout_seconds=timeout,
            max_attempts=attempts,
        )
    )
    counts: Counter[str] = Counter()
    for specification in manifest["documents"]:
        expected = specification["fields"]
        expected_keys = Counter(item["canonical_field"] for item in expected)
        path = (directory / specification["file"]).resolve()
        if directory.resolve() not in path.parents or not path.is_file():
            raise ValueError(
                "Every benchmark file must exist inside the supplied synthetic directory"
            )
        mime = mimetypes.guess_type(path.name)[0]
        if mime not in {"application/pdf", "image/png", "image/jpeg", "image/tiff"}:
            raise ValueError("Benchmark document type is unsupported")
        try:
            result = await provider.extract_bytes(
                path.read_bytes(),
                mime_type=mime,
                request_id="synthetic-accuracy-benchmark",
            )
        except DocumentExtractionError:
            counts["fail_closed"] += 1
            counts["documents"] += 1
            continue
        actual = result.field_evidence
        actual_keys = Counter(item.canonical_field_name for item in actual)
        detected = sum((expected_keys & actual_keys).values())
        counts.update(
            expected=sum(expected_keys.values()),
            actual=sum(actual_keys.values()),
            detected=detected,
            documents=1,
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
            counts["unit_exact"] += item.normalized_unit == match.get("unit")
            counts["source_exact"] += item.source_text == match.get(
                "source_text", match["raw_value"]
            )
            counts["page_exact"] += item.page_number == match.get("page")
            counts["bbox_valid"] += item.bounding_box is not None
            counts["confidence_provenance"] += item.field_confidence is not None
            counts["table_exact"] += (
                item.source_type != "CELL" or item.structured_value is not None
            )
        repeated_expected = sum(value for value in expected_keys.values() if value > 1)
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
    metrics = {
        "field_detection_precision": _ratio(counts["detected"], counts["actual"]),
        "field_detection_recall": _ratio(counts["detected"], counts["expected"]),
        "exact_raw_value_accuracy": _ratio(counts["raw_exact"], counts["expected"]),
        "normalized_value_accuracy": _ratio(
            counts["normalized_exact"], counts["matched"]
        ),
        "unit_accuracy": _ratio(counts["unit_exact"], counts["matched"]),
        "repeated_field_recall": _ratio(
            counts["repeated_detected"], counts["repeated_expected"]
        ),
        "table_row_accuracy": _ratio(counts["table_exact"], counts["matched"]),
        "source_text_accuracy": _ratio(counts["source_exact"], counts["matched"]),
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
            counts["identity_detection_correct"],
            counts["documents"] - counts["fail_closed"],
        ),
        "fail_closed_quarantine_rate": _ratio(
            counts["fail_closed"], counts["documents"]
        ),
    }
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("documents", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--region", default="ap-south-1")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--attempts", type=int, default=2)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    metrics = asyncio.run(
        run(
            args.documents,
            args.manifest,
            region=args.region,
            timeout=args.timeout,
            attempts=args.attempts,
        )
    )
    print(
        json.dumps({name: round(metrics[name], 6) for name in METRICS}, sort_keys=True)
    )
    return int(
        any(
            metrics[name] < threshold
            for name, threshold in manifest.get("gates", {}).items()
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
