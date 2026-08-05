from __future__ import annotations

import json
from pathlib import Path
import boto3
import pytest

from app.ai.extractor import AwsTextractExtractionProvider
from scripts.textract_sanitized_replay import (
    SanitizedCaptureSession,
    SanitizedReplayError,
    SanitizedReplayProvider,
    sanitize_textract_response,
    validate_synthetic_benchmark_scope,
)
from scripts.run_textract_accuracy_benchmark import run

BENCHMARK = Path(__file__).parent / "benchmark"


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
    provider = SanitizedReplayProvider(directory, 1)
    provider.set_benchmark_case_index(1)
    replayed = await provider.extract_bytes(
        b"ignored", mime_type="image/png", request_id="ignored"
    )
    live_parsed = AwsTextractExtractionProvider._parse_response(response())
    assert [
        (item.canonical_field_name, item.raw_value, item.page_number)
        for item in replayed.field_evidence
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
    result = await run(
        BENCHMARK / "documents",
        BENCHMARK / "synthetic-manifest.json",
        region="ap-south-1",
        timeout=1,
        attempts=1,
        replay_sanitized=directory,
    )
    assert result["provider_mode"] == "sanitized_replay"
    assert result["live_provider_calls"] == 0


def test_replay_rejects_missing_extra_and_malformed_fixture_sets(tmp_path):
    directory = tmp_path / "replay"
    directory.mkdir()
    with pytest.raises(SanitizedReplayError, match="SANITIZED_REPLAY_SET_INVALID"):
        SanitizedReplayProvider(directory, 1)
    malformed = tmp_path / "malformed"
    malformed.mkdir()
    (malformed / "case-01.json").write_text("not-json", encoding="utf-8")
    with pytest.raises(SanitizedReplayError, match="SANITIZED_REPLAY_FIXTURE_INVALID"):
        SanitizedReplayProvider(malformed, 1)
    (directory / "case-01.json").write_text("{}", encoding="utf-8")
    (directory / "extra.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SanitizedReplayError, match="SANITIZED_REPLAY_SET_INVALID"):
        SanitizedReplayProvider(directory, 1)
