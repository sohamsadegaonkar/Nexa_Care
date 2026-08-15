"""Synthetic-only sanitized Textract response capture and offline replay."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.ai.extractor import (
    TEXTRACT_PILOT_QUERY_SET_VERSION,
    AwsTextractExtractionProvider,
    ExtractionProvider,
    ExtractionProviderResult,
)

ALLOWED_BLOCK_FIELDS = frozenset(
    {
        "BlockType",
        "Id",
        "Relationships",
        "Text",
        "Confidence",
        "Geometry",
        "EntityTypes",
        "Query",
        "RowIndex",
        "ColumnIndex",
        "RowSpan",
        "ColumnSpan",
        "SelectionStatus",
        "Page",
    }
)
BOUNDING_BOX_FIELDS = ("Left", "Top", "Width", "Height")


class SanitizedReplayError(ValueError):
    """A stable value-free capture/replay validation failure."""


def validate_sanitized_query_registry(
    response: Mapping[str, Any], *, expected_queries: Sequence[tuple[str, str]]
) -> None:
    expected = list(expected_queries)
    if (
        not expected
        or any(
            not isinstance(item, (tuple, list))
            or len(item) != 2
            or not isinstance(item[0], str)
            or not item[0].strip()
            or not isinstance(item[1], str)
            or not item[1].strip()
            for item in expected
        )
        or len({item[0] for item in expected}) != len(expected)
    ):
        raise SanitizedReplayError("SANITIZED_QUERY_REGISTRY_INVALID")

    blocks = response.get("Blocks")
    if not isinstance(blocks, list):
        raise SanitizedReplayError("SANITIZED_QUERY_REGISTRY_INVALID")
    observed: dict[str, str] = {}
    for block in blocks:
        if not isinstance(block, dict) or block.get("BlockType") != "QUERY":
            continue
        query = block.get("Query")
        if not isinstance(query, dict):
            raise SanitizedReplayError("SANITIZED_QUERY_REGISTRY_INVALID")
        alias = query.get("Alias")
        text = query.get("Text")
        if (
            not isinstance(alias, str)
            or not alias.strip()
            or not isinstance(text, str)
            or not text.strip()
            or alias in observed
        ):
            raise SanitizedReplayError("SANITIZED_QUERY_REGISTRY_INVALID")
        observed[alias] = text

    expected_map = dict(expected)
    if set(observed) != set(expected_map):
        raise SanitizedReplayError("SANITIZED_QUERY_REGISTRY_DRIFT")
    if any(observed[alias] != text for alias, text in expected_map.items()):
        raise SanitizedReplayError("SANITIZED_QUERY_REGISTRY_DRIFT")


def validate_synthetic_benchmark_scope(
    documents: Path, manifest_path: Path, manifest: dict[str, Any]
) -> None:
    root = Path(__file__).resolve().parents[1]
    benchmark = root / "tests" / "ai_extraction" / "benchmark"
    if (
        manifest.get("synthetic_only") is not True
        or documents.resolve() != (benchmark / "documents").resolve()
        or manifest_path.resolve() != (benchmark / "synthetic-manifest.json").resolve()
    ):
        raise SanitizedReplayError("SANITIZED_CAPTURE_SCOPE_INVALID")


def sanitize_textract_response(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise SanitizedReplayError("SANITIZED_RESPONSE_INVALID")
    blocks = response.get("Blocks")
    metadata = response.get("DocumentMetadata")
    version = response.get("AnalyzeDocumentModelVersion")
    if (
        not isinstance(blocks, list)
        or not isinstance(metadata, dict)
        or not isinstance(metadata.get("Pages"), int)
        or isinstance(metadata.get("Pages"), bool)
        or not isinstance(version, str)
        or not version.strip()
    ):
        raise SanitizedReplayError("SANITIZED_RESPONSE_INVALID")
    ids: list[str] = []
    for block in blocks:
        block_id = block.get("Id") if isinstance(block, dict) else None
        if not isinstance(block_id, str) or not block_id or block_id in ids:
            raise SanitizedReplayError("SANITIZED_BLOCK_ID_INVALID")
        ids.append(block_id)
    id_map = {value: f"b{index:06d}" for index, value in enumerate(ids, start=1)}
    sanitized_blocks = [_sanitize_block(block, id_map) for block in blocks]
    return {
        "AnalyzeDocumentModelVersion": version,
        "DocumentMetadata": {"Pages": metadata["Pages"]},
        "Blocks": sanitized_blocks,
    }


def _sanitize_block(block: dict[str, Any], id_map: dict[str, str]) -> dict[str, Any]:
    clean: dict[str, Any] = {"Id": id_map[block["Id"]]}
    for key in ALLOWED_BLOCK_FIELDS - {"Id", "Relationships", "Geometry", "Query"}:
        if key in block:
            clean[key] = block[key]
    relationships = block.get("Relationships")
    if relationships is not None:
        if not isinstance(relationships, list):
            raise SanitizedReplayError("SANITIZED_RELATIONSHIP_INVALID")
        rewritten = []
        for relationship in relationships:
            if (
                not isinstance(relationship, dict)
                or not isinstance(relationship.get("Type"), str)
                or not isinstance(relationship.get("Ids"), list)
                or any(
                    not isinstance(value, str) or value not in id_map
                    for value in relationship["Ids"]
                )
            ):
                raise SanitizedReplayError("SANITIZED_RELATIONSHIP_INVALID")
            rewritten.append(
                {
                    "Type": relationship["Type"],
                    "Ids": [id_map[value] for value in relationship["Ids"]],
                }
            )
        clean["Relationships"] = rewritten
    geometry = block.get("Geometry")
    if geometry is not None:
        bbox = geometry.get("BoundingBox") if isinstance(geometry, dict) else None
        if not isinstance(bbox, dict):
            raise SanitizedReplayError("SANITIZED_GEOMETRY_INVALID")
        clean["Geometry"] = {
            "BoundingBox": {
                key: bbox[key] for key in BOUNDING_BOX_FIELDS if key in bbox
            }
        }
    query = block.get("Query")
    if query is not None:
        if not isinstance(query, dict):
            raise SanitizedReplayError("SANITIZED_QUERY_INVALID")
        clean["Query"] = {
            key: query[key]
            for key in ("Alias", "Text")
            if isinstance(query.get(key), str)
        }
    return clean


class SanitizedCaptureSession:
    def __init__(self, destination: Path, expected_count: int) -> None:
        self.destination = destination.resolve()
        self.expected_count = expected_count
        self._responses: dict[int, dict[str, Any]] = {}

    def capture(self, case_index: int, response: Any) -> None:
        if case_index in self._responses or not 1 <= case_index <= self.expected_count:
            raise SanitizedReplayError("SANITIZED_CAPTURE_INDEX_INVALID")
        self._responses[case_index] = sanitize_textract_response(response)

    def finalize(self) -> int:
        expected = set(range(1, self.expected_count + 1))
        if set(self._responses) != expected or self.destination.exists():
            raise SanitizedReplayError("SANITIZED_CAPTURE_INCOMPLETE")
        parent = self.destination.parent
        parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".textract-replay-", dir=parent))
        try:
            for index in sorted(self._responses):
                path = temporary / f"case-{index:02d}.json"
                path.write_text(
                    json.dumps(
                        self._responses[index], sort_keys=True, separators=(",", ":")
                    )
                    + "\n",
                    encoding="utf-8",
                )
            os.replace(temporary, self.destination)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
        return self.expected_count


class CaseIndexedCaptureProvider(ExtractionProvider):
    def __init__(
        self, provider: ExtractionProvider, session: SanitizedCaptureSession
    ) -> None:
        self.provider = provider
        self.session = session
        self.case_index: int | None = None

    @property
    def adapter_identity(self) -> str:
        return self.provider.adapter_identity

    @property
    def contract_version(self) -> str:
        return self.provider.contract_version

    def set_benchmark_case_index(self, case_index: int) -> None:
        self.case_index = case_index

    async def extract_bytes(
        self, document_bytes: bytes, *, mime_type: str, request_id: str
    ) -> ExtractionProviderResult:
        return await self.provider.extract_bytes(
            document_bytes, mime_type=mime_type, request_id=request_id
        )

    def observe(self, response: dict[str, Any]) -> None:
        if self.case_index is None:
            raise SanitizedReplayError("SANITIZED_CAPTURE_INDEX_MISSING")
        self.session.capture(self.case_index, response)


class SanitizedReplayProvider(ExtractionProvider):
    adapter_identity = "aws_textract"
    contract_version = TEXTRACT_PILOT_QUERY_SET_VERSION

    def __init__(
        self,
        directory: Path,
        expected_count: int,
        *,
        expected_queries: Sequence[tuple[str, str]],
        expected_query_registry_version: str,
        fixture_query_registry_version: str,
    ) -> None:
        self.directory = directory.resolve()
        self.expected_count = expected_count
        self.expected_queries = tuple(expected_queries)
        self.expected_query_registry_version = expected_query_registry_version
        self.fixture_query_registry_version = fixture_query_registry_version
        if (
            not isinstance(expected_query_registry_version, str)
            or not expected_query_registry_version.strip()
            or not isinstance(fixture_query_registry_version, str)
            or not fixture_query_registry_version.strip()
        ):
            raise SanitizedReplayError("SANITIZED_QUERY_REGISTRY_INVALID")
        expected = {f"case-{index:02d}.json" for index in range(1, expected_count + 1)}
        actual = (
            {path.name for path in self.directory.iterdir()}
            if self.directory.is_dir()
            else set()
        )
        if actual != expected:
            raise SanitizedReplayError("SANITIZED_REPLAY_SET_INVALID")
        self._fixtures: dict[int, dict[str, Any]] = {}
        for index in range(1, expected_count + 1):
            try:
                value = json.loads(
                    (self.directory / f"case-{index:02d}.json").read_text(
                        encoding="utf-8"
                    )
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise SanitizedReplayError("SANITIZED_REPLAY_FIXTURE_INVALID") from exc
            sanitized = sanitize_textract_response(value)
            if sanitized != value:
                raise SanitizedReplayError("SANITIZED_REPLAY_FIXTURE_INVALID")
            validate_sanitized_query_registry(
                sanitized, expected_queries=self.expected_queries
            )
            self._fixtures[index] = value
        if self.fixture_query_registry_version != self.expected_query_registry_version:
            raise SanitizedReplayError("SANITIZED_QUERY_REGISTRY_DRIFT")
        self.case_index: int | None = None

    def set_benchmark_case_index(self, case_index: int) -> None:
        self.case_index = case_index

    async def extract_bytes(
        self, document_bytes: bytes, *, mime_type: str, request_id: str
    ) -> ExtractionProviderResult:
        _ = (document_bytes, mime_type, request_id)
        if self.case_index is None or self.case_index not in self._fixtures:
            raise SanitizedReplayError("SANITIZED_REPLAY_INDEX_INVALID")
        response = self._fixtures[self.case_index]
        model_version = response.get("AnalyzeDocumentModelVersion")
        return ExtractionProviderResult(
            document=AwsTextractExtractionProvider._parse_response(response),
            provider_adapter=self.adapter_identity,
            provider_contract_version=self.contract_version,
            provider_model_version=(
                model_version.strip()
                if isinstance(model_version, str) and model_version.strip()
                else "unknown"
            ),
            response_complete=True,
            provider_attempt_traces=(),
        )
