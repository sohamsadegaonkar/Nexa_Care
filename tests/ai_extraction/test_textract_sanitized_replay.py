from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import boto3
import pytest

from app.ai.extractor import (
    AwsTextractExtractionProvider,
    TEXTRACT_PILOT_QUERIES,
    TEXTRACT_PILOT_QUERY_SET_VERSION,
)
from scripts.textract_sanitized_replay import (
    SanitizedCaptureSession,
    SanitizedReplayError,
    SanitizedReplayProvider,
    sanitize_textract_response,
    validate_sanitized_query_registry,
    validate_synthetic_benchmark_scope,
)
import scripts.run_textract_accuracy_benchmark as benchmark_script
from scripts.run_textract_accuracy_benchmark import main, run_benchmark

BENCHMARK = Path(__file__).parent / "benchmark"
COMMITTED_REPLAY = BENCHMARK / "sanitized-replay"
HISTORICAL_QUERIES = tuple(
    (
        alias,
        ("What diagnosis is directly written?" if alias == "diagnosis" else question),
    )
    for alias, question in TEXTRACT_PILOT_QUERIES
)
TEST_RESPONSE_QUERIES = (("hba1c", "Synthetic question"),)


def _assert_value_free(error: SanitizedReplayError, *forbidden: str) -> None:
    assert str(error) in {
        "SANITIZED_QUERY_REGISTRY_INVALID",
        "SANITIZED_QUERY_REGISTRY_DRIFT",
    }
    assert all(value not in str(error) for value in forbidden)


def _provider(
    directory: Path,
    expected_count: int,
    *,
    expected_queries: tuple[tuple[str, str], ...],
    version: str,
) -> SanitizedReplayProvider:
    return SanitizedReplayProvider(
        directory,
        expected_count,
        expected_queries=expected_queries,
        expected_query_registry_version=version,
        fixture_query_registry_version=version,
    )


async def _run_explicit_replay(
    directory: Path,
    manifest: dict,
    *,
    expected_queries: tuple[tuple[str, str], ...],
    version: str,
) -> dict:
    result = await run_benchmark(
        BENCHMARK / "documents",
        manifest,
        _provider(
            directory,
            len(manifest["documents"]),
            expected_queries=expected_queries,
            version=version,
        ),
    )
    result["provider_mode"] = "sanitized_replay"
    result["live_provider_calls"] = 0
    return result


def response() -> dict:
    return {
        "AnalyzeDocumentModelVersion": "1.0",
        "DocumentMetadata": {"Pages": 1, "RequestId": "forbidden"},
        "ResponseMetadata": {"HTTPHeaders": {"authorization": "forbidden"}},
        "Blocks": [
            {
                "BlockType": "PAGE",
                "Id": "page-original",
                "Relationships": [{"Type": "CHILD", "Ids": ["query-original"]}],
                "Geometry": {
                    "BoundingBox": {
                        "Left": 0.0,
                        "Top": 0.0,
                        "Width": 1.0,
                        "Height": 1.0,
                    },
                    "Polygon": [{"X": 0.0, "Y": 0.0}],
                },
                "Custom": "forbidden",
            },
            {
                "BlockType": "QUERY",
                "Id": "query-original",
                "Query": {
                    "Alias": "hba1c",
                    "Text": "Synthetic question",
                    "Pages": ["1"],
                },
                "Relationships": [{"Type": "ANSWER", "Ids": ["answer-original"]}],
            },
            {
                "BlockType": "QUERY_RESULT",
                "Id": "answer-original",
                "Text": "7.2 %",
                "Confidence": 98.0,
                "Geometry": {
                    "BoundingBox": {
                        "Left": 0.1,
                        "Top": 0.2,
                        "Width": 0.2,
                        "Height": 0.1,
                    }
                },
            },
        ],
    }


def test_sanitization_strips_metadata_and_canonicalizes_relationship_ids():
    sanitized = sanitize_textract_response(response())
    serialized = json.dumps(sanitized)
    assert "ResponseMetadata" not in serialized
    assert "RequestId" not in serialized
    assert "HTTPHeaders" not in serialized
    assert "Polygon" not in serialized
    assert "Custom" not in serialized
    assert [block["Id"] for block in sanitized["Blocks"]] == [
        "b000001",
        "b000002",
        "b000003",
    ]
    assert sanitized["Blocks"][0]["Relationships"][0]["Ids"] == ["b000002"]
    assert sanitized["Blocks"][1]["Relationships"][0]["Ids"] == ["b000003"]
    assert sanitized["Blocks"][1]["Query"] == {
        "Alias": "hba1c",
        "Text": "Synthetic question",
    }


def test_sanitization_rejects_missing_relationship_target():
    value = response()
    value["Blocks"][0]["Relationships"][0]["Ids"] = ["missing"]
    with pytest.raises(SanitizedReplayError, match="SANITIZED_RELATIONSHIP_INVALID"):
        sanitize_textract_response(value)


def test_query_registry_validation_is_closed_and_value_free():
    sanitized = sanitize_textract_response(response())
    validate_sanitized_query_registry(sanitized, expected_queries=TEST_RESPONSE_QUERIES)

    drifted = json.loads(json.dumps(sanitized))
    drifted["Blocks"][1]["Query"]["Text"] = "Different question"
    with pytest.raises(SanitizedReplayError) as drift:
        validate_sanitized_query_registry(
            drifted, expected_queries=TEST_RESPONSE_QUERIES
        )
    assert str(drift.value) == "SANITIZED_QUERY_REGISTRY_DRIFT"

    duplicate = json.loads(json.dumps(sanitized))
    duplicate["Blocks"].append(duplicate["Blocks"][1])
    with pytest.raises(SanitizedReplayError) as invalid:
        validate_sanitized_query_registry(
            duplicate, expected_queries=TEST_RESPONSE_QUERIES
        )
    assert str(invalid.value) == "SANITIZED_QUERY_REGISTRY_INVALID"

    unknown = json.loads(json.dumps(sanitized))
    unknown["Blocks"][1]["Query"]["Alias"] = "experimental"
    with pytest.raises(SanitizedReplayError) as unknown_error:
        validate_sanitized_query_registry(
            unknown, expected_queries=TEST_RESPONSE_QUERIES
        )
    assert str(unknown_error.value) == "SANITIZED_QUERY_REGISTRY_DRIFT"


def test_query_registry_rejects_missing_alias_without_value_disclosure():
    sanitized = sanitize_textract_response(response())
    sanitized["Blocks"] = [
        block for block in sanitized["Blocks"] if block.get("BlockType") != "QUERY"
    ]

    with pytest.raises(SanitizedReplayError) as error:
        validate_sanitized_query_registry(
            sanitized, expected_queries=TEST_RESPONSE_QUERIES
        )

    assert str(error.value) == "SANITIZED_QUERY_REGISTRY_DRIFT"
    _assert_value_free(error.value, "hba1c", "Synthetic question", "b000002")


def test_query_registry_rejects_extra_alias_without_value_disclosure():
    sanitized = sanitize_textract_response(response())
    sanitized["Blocks"].append(
        {
            "BlockType": "QUERY",
            "Id": "b-extra",
            "Query": {"Alias": "unregistered", "Text": "Extra synthetic question"},
        }
    )

    with pytest.raises(SanitizedReplayError) as error:
        validate_sanitized_query_registry(
            sanitized, expected_queries=TEST_RESPONSE_QUERIES
        )

    assert str(error.value) == "SANITIZED_QUERY_REGISTRY_DRIFT"
    _assert_value_free(
        error.value, "unregistered", "Extra synthetic question", "b-extra"
    )


def test_query_registry_rejects_empty_alias_without_value_disclosure():
    sanitized = sanitize_textract_response(response())
    sanitized["Blocks"][1]["Query"]["Alias"] = ""

    with pytest.raises(SanitizedReplayError) as error:
        validate_sanitized_query_registry(
            sanitized, expected_queries=TEST_RESPONSE_QUERIES
        )

    assert str(error.value) == "SANITIZED_QUERY_REGISTRY_INVALID"
    _assert_value_free(error.value, "Synthetic question", "b000002")


def test_query_registry_rejects_empty_text_without_value_disclosure():
    sanitized = sanitize_textract_response(response())
    sanitized["Blocks"][1]["Query"]["Text"] = ""

    with pytest.raises(SanitizedReplayError) as error:
        validate_sanitized_query_registry(
            sanitized, expected_queries=TEST_RESPONSE_QUERIES
        )

    assert str(error.value) == "SANITIZED_QUERY_REGISTRY_INVALID"
    _assert_value_free(error.value, "hba1c", "b000002")


def test_generic_sanitizer_accepts_temporary_experimental_aliases():
    value = response()
    value["Blocks"][1]["Query"]["Alias"] = "experimental"
    sanitized = sanitize_textract_response(value)
    assert sanitized["Blocks"][1]["Query"]["Alias"] == "experimental"


def test_historical_registry_cannot_score_current_fixture_even_with_matching_metadata():
    with pytest.raises(SanitizedReplayError) as error:
        _provider(
            COMMITTED_REPLAY,
            15,
            expected_queries=HISTORICAL_QUERIES,
            version="pilot-v1-baseline",
        )
    assert str(error.value) == "SANITIZED_QUERY_REGISTRY_DRIFT"


def test_registry_validation_rejects_malformed_expected_registry():
    with pytest.raises(SanitizedReplayError) as error:
        validate_sanitized_query_registry(
            sanitize_textract_response(response()), expected_queries=(("", ""),)
        )
    assert str(error.value) == "SANITIZED_QUERY_REGISTRY_INVALID"


def test_capture_is_atomic_and_rejects_partial_corpus(tmp_path):
    incomplete = SanitizedCaptureSession(tmp_path / "partial", 2)
    incomplete.capture(1, response())
    with pytest.raises(SanitizedReplayError, match="SANITIZED_CAPTURE_INCOMPLETE"):
        incomplete.finalize()
    assert not (tmp_path / "partial").exists()

    complete = SanitizedCaptureSession(tmp_path / "complete", 15)
    for index in range(1, 16):
        complete.capture(index, response())
    assert complete.finalize() == 15
    assert len(list((tmp_path / "complete").iterdir())) == 15
    assert (tmp_path / "complete" / "case-15.json").is_file()


def test_capture_scope_is_repository_synthetic_manifest_only(tmp_path):
    with pytest.raises(SanitizedReplayError, match="SANITIZED_CAPTURE_SCOPE_INVALID"):
        validate_synthetic_benchmark_scope(
            tmp_path, tmp_path / "manifest.json", {"synthetic_only": True}
        )


@pytest.mark.asyncio
async def test_replay_uses_production_parser_without_boto3(monkeypatch, tmp_path):
    directory = tmp_path / "replay"
    capture = SanitizedCaptureSession(directory, 1)
    capture.capture(1, response())
    capture.finalize()
    monkeypatch.setattr(
        boto3, "client", lambda *args, **kwargs: pytest.fail("boto3 must not be called")
    )
    provider = _provider(
        directory,
        1,
        expected_queries=TEST_RESPONSE_QUERIES,
        version="test-response",
    )
    provider.set_benchmark_case_index(1)
    replayed = await provider.extract_bytes(
        b"ignored", mime_type="image/png", request_id="ignored"
    )
    live_parsed = AwsTextractExtractionProvider._parse_response(response())
    assert [
        (item.canonical_field_name, item.raw_value, item.page_number)
        for item in replayed.document.field_evidence
    ] == [
        (item.canonical_field_name, item.raw_value, item.page_number)
        for item in live_parsed.field_evidence
    ]


@pytest.mark.asyncio
async def test_benchmark_replay_reports_zero_live_calls(monkeypatch, tmp_path):
    directory = tmp_path / "replay"
    capture = SanitizedCaptureSession(directory, 15)
    for index in range(1, 16):
        capture.capture(index, response())
    capture.finalize()
    monkeypatch.setattr(
        boto3,
        "client",
        lambda *args, **kwargs: pytest.fail("boto3 must not be called"),
    )
    result = await _run_explicit_replay(
        directory,
        json.loads((BENCHMARK / "synthetic-manifest.json").read_text(encoding="utf-8")),
        expected_queries=TEST_RESPONSE_QUERIES,
        version="test-response",
    )
    assert result["provider_mode"] == "sanitized_replay"
    assert result["live_provider_calls"] == 0


def test_current_replay_cli_reports_current_registry_without_historical_metrics(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        boto3,
        "client",
        lambda *args, **kwargs: pytest.fail("boto3 must not be called"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_textract_accuracy_benchmark.py",
            str(BENCHMARK / "documents"),
            str(BENCHMARK / "synthetic-manifest.json"),
            "--replay-sanitized",
            str(COMMITTED_REPLAY),
        ],
    )
    assert main() != 0
    output = json.loads(capsys.readouterr().out)
    assert output["provider_mode"] == "sanitized_replay"
    assert output["live_provider_calls"] == 0
    assert output["attempted_documents"] == 15
    assert output["successful_documents"] == 15
    assert output["failed_documents"] == 0
    assert output["query_registry"] == {
        "production_version": TEXTRACT_PILOT_QUERY_SET_VERSION,
        "fixture_version": TEXTRACT_PILOT_QUERY_SET_VERSION,
        "matches_current": True,
        "failure_code": None,
    }
    assert output["failure_classification"]["reconciliation_valid"] is True
    serialized = json.dumps(output, sort_keys=True)
    assert "What diagnosis is directly written?" not in serialized
    assert "pilot-v1-baseline" not in serialized


def test_bare_replay_cli_uses_repository_defaults_from_arbitrary_cwd(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(Path(benchmark_script.__file__).resolve()),
            "--replay-sanitized",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert result.stderr == ""
    output = json.loads(result.stdout)
    assert output["provider_mode"] == "sanitized_replay"
    assert output["live_provider_calls"] == 0
    assert output["attempted_documents"] == 15
    assert output["successful_documents"] == 15
    assert output["failed_documents"] == 0
    assert output["query_registry"] == {
        "production_version": TEXTRACT_PILOT_QUERY_SET_VERSION,
        "fixture_version": TEXTRACT_PILOT_QUERY_SET_VERSION,
        "matches_current": True,
        "failure_code": None,
    }
    assert output["failure_classification"]["reconciliation_valid"] is True
    serialized = json.dumps(output, sort_keys=True)
    assert "What diagnosis is directly written?" not in serialized
    assert "pilot-v1-baseline" not in serialized


def test_explicit_replay_cli_remains_compatible_from_arbitrary_cwd(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(Path(benchmark_script.__file__).resolve()),
            str(BENCHMARK / "documents"),
            str(BENCHMARK / "synthetic-manifest.json"),
            "--replay-sanitized",
            str(COMMITTED_REPLAY),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert result.stderr == ""
    output = json.loads(result.stdout)
    assert output["query_registry"] == {
        "production_version": TEXTRACT_PILOT_QUERY_SET_VERSION,
        "fixture_version": TEXTRACT_PILOT_QUERY_SET_VERSION,
        "matches_current": True,
        "failure_code": None,
    }
    assert output["failure_classification"]["reconciliation_valid"] is True


@pytest.mark.parametrize("arguments", [[], ["--capture-sanitized-replay", "capture"]])
def test_live_and_capture_modes_require_explicit_inputs_before_aws(
    monkeypatch, arguments
):
    monkeypatch.setattr(
        benchmark_script,
        "AwsTextractExtractionProvider",
        lambda *args, **kwargs: pytest.fail("AWS provider must not be constructed"),
    )
    monkeypatch.setattr(sys, "argv", ["run_textract_accuracy_benchmark.py", *arguments])

    with pytest.raises(SystemExit) as error:
        benchmark_script.main()

    assert error.value.code == 2


@pytest.mark.asyncio
async def test_committed_replay_is_the_offline_qualification_baseline(monkeypatch):
    expected_files = {f"case-{index:02d}.json" for index in range(1, 16)}
    assert {path.name for path in COMMITTED_REPLAY.iterdir()} == expected_files
    for path in sorted(COMMITTED_REPLAY.iterdir()):
        value = json.loads(path.read_text(encoding="utf-8"))
        assert sanitize_textract_response(value) == value

    monkeypatch.setattr(
        boto3,
        "client",
        lambda *args, **kwargs: pytest.fail("boto3 must not be called"),
    )
    result = await _run_explicit_replay(
        COMMITTED_REPLAY,
        json.loads((BENCHMARK / "synthetic-manifest.json").read_text(encoding="utf-8")),
        expected_queries=TEXTRACT_PILOT_QUERIES,
        version=TEXTRACT_PILOT_QUERY_SET_VERSION,
    )

    assert result["provider_mode"] == "sanitized_replay"
    assert result["live_provider_calls"] == 0
    assert result["attempted_documents"] == 15
    assert result["successful_documents"] == 15
    assert result["failed_documents"] == 0
    assert result["evidence_occurrences"] == 95
    assert result["semantic_candidate_count"] == 61
    assert result["exact_match_count"] == 49
    assert result["unmatched_expected_count"] == 4
    assert result["unmatched_candidate_count"] == 12
    assert result["page_present_count"] == 95
    assert result["page_missing_count"] == 0
    assert result["identity_cases_correct"] == 14
    assert result["identity_cases_incorrect"] == 1
    assert result["metrics"]["source_text_accuracy"] == pytest.approx(
        0.9183673469387755
    )
    assert result["metrics"]["exact_occurrence_precision"] == pytest.approx(
        0.8032786885245902
    )
    assert result["metrics"]["exact_occurrence_recall"] == pytest.approx(
        0.9245283018867925
    )
    assert result["metrics"]["patient_identity_mismatch_detection"] == pytest.approx(
        0.9333333333333333
    )
    assert {name for name, passed in result["gate_results"].items() if not passed} == {
        "patient_identity_mismatch_detection"
    }
    assert result["benchmark_valid"] is False
    assert result["exact_match_count"] == 49
    assert result["unmatched_expected_count"] == 4
    assert result["unmatched_candidate_count"] == 12
    assert result["semantic_occurrence_match_count"] == 52
    assert result["semantic_occurrence_precision"] == pytest.approx(52 / 61)
    assert result["semantic_occurrence_recall"] == pytest.approx(52 / 53)
    assert result["semantic_raw_exact_count"] == 49
    assert result["semantic_raw_exact_rate"] == pytest.approx(49 / 52)
    assert result["semantic_matches_added_beyond_exact"] == 3
    assert result["semantic_unmatched_expected_count"] == 1
    assert result["semantic_unmatched_candidate_count"] == 9
    assert result["semantic_matches_added_by_canonical_field"] == {"hba1c": 3}
    assert result["routing_eligible_candidate_count"] == 56
    assert result["routing_ineligible_candidate_count"] == 5
    assert result["routing_ineligible_count_by_reason"] == {
        "INELIGIBLE_QUERY_ONLY_INVALID_FORMAT": 5
    }
    assert (
        result["routing_ineligible_count_by_reason"].get(
            "INELIGIBLE_CLASSIFICATION_FAILED", 0
        )
        == 0
    )
    assert result["routing_exact_match_count"] == 49
    assert result["routing_exact_occurrence_precision"] == pytest.approx(49 / 56)
    assert result["routing_exact_occurrence_recall"] == pytest.approx(49 / 53)
    assert result["routing_semantic_match_count"] == 52
    assert result["routing_semantic_occurrence_precision"] == pytest.approx(52 / 56)
    assert result["routing_semantic_occurrence_recall"] == pytest.approx(52 / 53)
    classification = result["failure_classification"]
    assert classification["classification_version"] == "v1"
    assert classification["reconciliation_valid"] is True
    assert classification["grouped_support_record_count"] == 34
    assert (
        classification["counts_by_class"]["CROSS_SOURCE_SAME_OCCURRENCE"][
            "represented_semantic_groups"
        ]
        == 33
    )
    assert (
        classification["counts_by_class"]["TRUE_DUPLICATE"][
            "represented_semantic_groups"
        ]
        == 0
    )
    assert (
        classification["counts_by_class"]["SAME_VALUE_DISTINCT_AUTHENTIC_LOCATION"][
            "exact_candidates"
        ]
        == 0
    )
    assert classification["counts_by_class"]["RAW_REPRESENTATION_VARIANCE"] == {
        "represented_semantic_groups": 0,
        "exact_candidates": 3,
        "semantic_candidates": 0,
        "exact_expected": 3,
        "semantic_expected": 0,
    }
    assert (
        classification["counts_by_class"]["MALFORMED_QUERY_ONLY"]["exact_candidates"]
        == 5
    )
    assert (
        classification["counts_by_class"]["UNEXPECTED_EXTRACTED_CANDIDATE"][
            "exact_candidates"
        ]
        == 3
    )
    assert classification["counts_by_class"]["IDENTITY_NONMATCHING"] == {
        "represented_semantic_groups": 0,
        "exact_candidates": 1,
        "semantic_candidates": 1,
        "exact_expected": 1,
        "semantic_expected": 1,
    }
    assert classification["counts_by_canonical_field"]["RAW_REPRESENTATION_VARIANCE"][
        "candidates"
    ]["exact"] == {"hba1c": 3}
    assert classification["counts_by_canonical_field"]["MALFORMED_QUERY_ONLY"][
        "candidates"
    ]["exact"] == {"blood_pressure": 1, "hba1c": 1, "medication": 3}
    assert classification["counts_by_canonical_field"][
        "UNEXPECTED_EXTRACTED_CANDIDATE"
    ]["candidates"]["exact"] == {
        "blood_glucose": 1,
        "heart_rate": 1,
        "medication": 1,
    }
    assert classification["case_indexes_by_class"]["RAW_REPRESENTATION_VARIANCE"][
        "candidates"
    ]["exact"] == [4, 12, 15]
    assert classification["case_indexes_by_class"]["IDENTITY_NONMATCHING"][
        "candidates"
    ]["exact"] == [12]
    assert classification["source_signatures_by_class"]["CROSS_SOURCE_SAME_OCCURRENCE"][
        "groups"
    ] == {
        "CELL+KEY_VALUE_SET+QUERY_RESULT": 1,
        "CELL+QUERY_RESULT": 2,
        "KEY_VALUE_SET+QUERY_RESULT": 30,
    }
    assert classification["reconciliation"] == {
        "candidates_minus_exact_equals_exact_unmatched": {
            "left": 12,
            "right": 12,
            "valid": True,
        },
        "candidates_minus_semantic_equals_semantic_unmatched": {
            "left": 9,
            "right": 9,
            "valid": True,
        },
        "evidence_minus_semantic_candidates_equals_grouped_support": {
            "left": 34,
            "right": 34,
            "valid": True,
        },
        "exact_unmatched_candidate_primary_sum": {
            "left": 12,
            "right": 12,
            "valid": True,
        },
        "expected_minus_exact_equals_exact_unmatched": {
            "left": 4,
            "right": 4,
            "valid": True,
        },
        "expected_minus_semantic_equals_semantic_unmatched": {
            "left": 1,
            "right": 1,
            "valid": True,
        },
        "identity_failed_case_sum": {
            "by_class": {
                "IDENTITY_CONFLICTING": 0,
                "IDENTITY_MISSING": 0,
                "IDENTITY_NONMATCHING": 1,
            },
            "left": 1,
            "right": 1,
            "valid": True,
        },
        "routing_eligible_plus_ineligible_equals_candidates": {
            "left": 61,
            "right": 61,
            "valid": True,
        },
        "semantic_unmatched_candidate_primary_sum": {
            "left": 9,
            "right": 9,
            "valid": True,
        },
    }
    assert result["metrics"]["exact_occurrence_precision"] == pytest.approx(49 / 61)
    assert result["metrics"]["exact_occurrence_recall"] == pytest.approx(49 / 53)
    assert result["metrics"]["exact_raw_value_accuracy"] == pytest.approx(49 / 53)
    serialized = json.dumps(result, sort_keys=True)
    for forbidden in (
        "Synthetic Patient",
        "7.2 %",
        "01-simple-laboratory-form.png",
        "RequestId",
        "ResponseMetadata",
    ):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_committed_replay_failure_classification_is_byte_stable(monkeypatch):
    monkeypatch.setattr(
        boto3,
        "client",
        lambda *args, **kwargs: pytest.fail("boto3 must not be called"),
    )
    manifest = json.loads(
        (BENCHMARK / "synthetic-manifest.json").read_text(encoding="utf-8")
    )
    first = await _run_explicit_replay(
        COMMITTED_REPLAY,
        manifest,
        expected_queries=TEXTRACT_PILOT_QUERIES,
        version=TEXTRACT_PILOT_QUERY_SET_VERSION,
    )
    second = await _run_explicit_replay(
        COMMITTED_REPLAY,
        manifest,
        expected_queries=TEXTRACT_PILOT_QUERIES,
        version=TEXTRACT_PILOT_QUERY_SET_VERSION,
    )
    assert json.dumps(first["failure_classification"], sort_keys=True) == json.dumps(
        second["failure_classification"], sort_keys=True
    )


def test_replay_rejects_missing_extra_and_malformed_fixture_sets(tmp_path):
    directory = tmp_path / "replay"
    directory.mkdir()
    with pytest.raises(SanitizedReplayError, match="SANITIZED_REPLAY_SET_INVALID"):
        _provider(directory, 1, expected_queries=TEST_RESPONSE_QUERIES, version="test")
    malformed = tmp_path / "malformed"
    malformed.mkdir()
    (malformed / "case-01.json").write_text("not-json", encoding="utf-8")
    with pytest.raises(SanitizedReplayError, match="SANITIZED_REPLAY_FIXTURE_INVALID"):
        _provider(malformed, 1, expected_queries=TEST_RESPONSE_QUERIES, version="test")
    (directory / "case-01.json").write_text("{}", encoding="utf-8")
    (directory / "extra.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SanitizedReplayError, match="SANITIZED_REPLAY_SET_INVALID"):
        _provider(directory, 1, expected_queries=TEST_RESPONSE_QUERIES, version="test")
