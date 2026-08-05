"""Deterministic Amazon Textract block-graph parser without inference."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from app.ai.extraction_normalization import normalize_extracted_value
from app.models.ai_models import ProviderFieldEvidence
from app.models.field_evidence import NormalizedBoundingBox

QUERY_ALIASES = frozenset(
    {
        "patient_name",
        "phone",
        "aadhaar_abha_id",
        "hba1c",
        "blood_glucose",
        "blood_pressure",
        "heart_rate",
        "medication",
        "diagnosis",
    }
)
FORM_SYNONYMS = {
    "patient name": "patient_name",
    "name of patient": "patient_name",
    "abha id": "aadhaar_abha_id",
    "health id": "aadhaar_abha_id",
    "mobile": "phone",
    "phone": "phone",
    "contact": "phone",
    "hba1c": "hba1c",
    "glycated haemoglobin": "hba1c",
    "blood glucose": "blood_glucose",
    "glucose": "blood_glucose",
    "fbs": "blood_glucose",
    "ppbs": "blood_glucose",
    "rbs": "blood_glucose",
    "blood pressure": "blood_pressure",
    "bp": "blood_pressure",
    "heart rate": "heart_rate",
    "pulse": "heart_rate",
    "diagnosis": "diagnosis",
    "provisional diagnosis": "diagnosis",
    "medication": "medication",
    "medicine": "medication",
    "drug": "medication",
}
LAB_HEADERS = {
    "test": "test",
    "test name": "test",
    "investigation": "test",
    "investigation name": "test",
    "result": "result",
    "value": "result",
    "unit": "unit",
    "units": "unit",
    "reference range": "reference_range",
    "normal range": "reference_range",
    "flag": "abnormal_flag",
    "abnormal flag": "abnormal_flag",
    "date": "date",
}
MED_HEADERS = {
    "medicine": "medicine",
    "medication": "medicine",
    "drug": "medicine",
    "drug name": "medicine",
    "strength": "strength",
    "dose": "strength",
    "frequency": "frequency",
    "route": "route",
    "duration": "duration",
}


def _label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


class TextractBlockGraph:
    def __init__(
        self,
        blocks: list[Any],
        *,
        validated_document_page_count: int | None = None,
    ) -> None:
        self.blocks = [block for block in blocks if isinstance(block, dict)]
        self.validated_document_page_count = validated_document_page_count
        self.by_id = {
            block["Id"]: block
            for block in self.blocks
            if isinstance(block.get("Id"), str)
        }
        self.parents: dict[str, set[str]] = {}
        for parent in self.blocks:
            parent_id = parent.get("Id")
            if not isinstance(parent_id, str):
                continue
            for relationship_type in ("CHILD", "ANSWER", "VALUE"):
                for child_id in self.relationship_ids(parent, relationship_type):
                    self.parents.setdefault(child_id, set()).add(parent_id)

    @staticmethod
    def relationship_ids(block: dict[str, Any], kind: str) -> list[str]:
        relationships = block.get("Relationships", [])
        if not isinstance(relationships, list):
            return []
        return [
            item
            for rel in relationships
            if isinstance(rel, dict)
            and rel.get("Type") == kind
            and isinstance(rel.get("Ids"), list)
            for item in rel["Ids"]
            if isinstance(item, str)
        ]

    def children(self, block: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            self.by_id[item]
            for item in self.relationship_ids(block, "CHILD")
            if item in self.by_id
        ]

    def text(self, block: dict[str, Any], seen: set[str] | None = None) -> str:
        if isinstance(block.get("Text"), str):
            return block["Text"]
        if block.get("BlockType") == "SELECTION_ELEMENT":
            return (
                "SELECTED"
                if block.get("SelectionStatus") == "SELECTED"
                else "NOT_SELECTED"
            )
        seen = set() if seen is None else seen
        block_id = block.get("Id")
        if isinstance(block_id, str):
            if block_id in seen:
                return ""
            seen.add(block_id)
        return " ".join(
            value for child in self.children(block) if (value := self.text(child, seen))
        ).strip()

    def page_number(self, block: dict[str, Any]) -> int | None:
        """Resolve an authentic page, returning None for ambiguity or no proof."""
        direct = _page(block)
        if direct is not None:
            return direct
        start = block.get("Id")
        if not isinstance(start, str):
            return None
        page_ids: set[str] = set()
        pending = list(self.parents.get(start, set()))
        seen = {start}
        while pending:
            parent_id = pending.pop()
            if parent_id in seen:
                continue
            seen.add(parent_id)
            parent = self.by_id.get(parent_id)
            if parent is None:
                continue
            if parent.get("BlockType") == "PAGE":
                page_ids.add(parent_id)
            pending.extend(self.parents.get(parent_id, set()))
        if len(page_ids) != 1:
            return None
        page_block = self.by_id[next(iter(page_ids))]
        if (page := _page(page_block)) is not None:
            return page
        graph_page_ids = {
            item["Id"]
            for item in self.blocks
            if item.get("BlockType") == "PAGE" and isinstance(item.get("Id"), str)
        }
        if self.validated_document_page_count == 1 and graph_page_ids == page_ids:
            return 0
        return None


def _confidence(block: dict[str, Any]) -> float | None:
    value = block.get("Confidence")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number / 100 if math.isfinite(number) and 0 <= number <= 100 else None


def _page(block: dict[str, Any]) -> int | None:
    value = block.get("Page")
    return (
        value - 1
        if isinstance(value, int) and not isinstance(value, bool) and value >= 1
        else None
    )


def _bbox(block: dict[str, Any]) -> NormalizedBoundingBox | None:
    geometry = block.get("Geometry")
    value = geometry.get("BoundingBox") if isinstance(geometry, dict) else None
    if not isinstance(value, dict):
        return None
    try:
        left, top, width, height = (
            float(value[key]) for key in ("Left", "Top", "Width", "Height")
        )
        return NormalizedBoundingBox(
            left=left, top=top, right=left + width, bottom=top + height
        )
    except (KeyError, TypeError, ValueError, ValidationError):
        return None


def _union_bbox(blocks: list[dict[str, Any]]) -> NormalizedBoundingBox | None:
    boxes = [_bbox(block) for block in blocks]
    valid = [box for box in boxes if box is not None]
    if len(valid) != len(blocks) or not valid:
        return None
    return NormalizedBoundingBox(
        left=min(x.left for x in valid),
        top=min(x.top for x in valid),
        right=max(x.right for x in valid),
        bottom=max(x.bottom for x in valid),
    )


def _make(
    field: str,
    raw: str,
    source: str,
    block: dict[str, Any],
    *,
    source_type: str,
    block_ids: list[str],
    extracted_at: datetime,
    model_version: str,
    page_number: int | None = None,
    bounding_box: NormalizedBoundingBox | None = None,
    structured: dict[str, str | bool | None] | None = None,
    incomplete: bool = False,
    raw_unit: str | None = None,
    reference_range: str | None = None,
) -> ProviderFieldEvidence:
    normalized = normalize_extracted_value(field, raw)
    page = page_number if page_number is not None else _page(block)
    bbox = bounding_box if bounding_box is not None else _bbox(block)
    safe_ids = sorted({item for item in block_ids if item})
    identity = {
        "field": field,
        "raw": raw,
        "page": page,
        "bbox": bbox.model_dump() if bbox else None,
        "blocks": safe_ids,
        "source_type": source_type,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ProviderFieldEvidence(
        canonical_field_name=field,
        raw_value=raw,
        source_text=source,
        page_number=page,
        bounding_box=bbox,
        field_confidence=_confidence(block),
        provider_name="aws_textract",
        provider_api_version=model_version,
        extraction_timestamp=extracted_at,
        evidence_hash=digest,
        source_type=source_type,
        source_block_ids=tuple(safe_ids),
        normalized_value=normalized.value,
        raw_unit=raw_unit or normalized.raw_unit,
        normalized_unit=normalized.unit,
        reference_range=reference_range,
        structured_value=structured,
        incomplete=incomplete,
    )


def parse_textract_blocks(
    blocks: list[Any],
    *,
    extracted_at: datetime,
    model_version: str,
    validated_document_page_count: int | None = None,
) -> list[ProviderFieldEvidence]:
    graph = TextractBlockGraph(
        blocks, validated_document_page_count=validated_document_page_count
    )
    evidence: list[ProviderFieldEvidence] = []
    _parse_queries(graph, evidence, extracted_at, model_version)
    _parse_forms(graph, evidence, extracted_at, model_version)
    _parse_tables(graph, evidence, extracted_at, model_version)
    unique: dict[str, ProviderFieldEvidence] = {}
    for item in evidence:
        unique.setdefault(item.evidence_hash or "", item)
    return list(unique.values())


def _parse_queries(
    graph: TextractBlockGraph,
    output: list[ProviderFieldEvidence],
    extracted_at: datetime,
    model_version: str,
) -> None:
    for query in graph.blocks:
        if query.get("BlockType") != "QUERY" or not isinstance(
            query.get("Query"), dict
        ):
            continue
        alias = query["Query"].get("Alias")
        if alias not in QUERY_ALIASES:
            continue
        for answer_id in graph.relationship_ids(query, "ANSWER"):
            answer = graph.by_id.get(answer_id)
            if (
                answer
                and answer.get("BlockType") == "QUERY_RESULT"
                and (raw := graph.text(answer).strip())
            ):
                output.append(
                    _make(
                        alias,
                        raw,
                        graph.text(answer),
                        answer,
                        source_type="QUERY_RESULT",
                        block_ids=[answer_id],
                        extracted_at=extracted_at,
                        model_version=model_version,
                        page_number=graph.page_number(answer),
                    )
                )


def _parse_forms(
    graph: TextractBlockGraph,
    output: list[ProviderFieldEvidence],
    extracted_at: datetime,
    model_version: str,
) -> None:
    for key in graph.blocks:
        entities = key.get("EntityTypes", [])
        if (
            key.get("BlockType") != "KEY_VALUE_SET"
            or not isinstance(entities, list)
            or "KEY" not in entities
        ):
            continue
        field = FORM_SYNONYMS.get(_label(graph.text(key)))
        if not field:
            continue
        for value_id in graph.relationship_ids(key, "VALUE"):
            value = graph.by_id.get(value_id)
            value_entities = value.get("EntityTypes", []) if value else []
            if (
                not value
                or value.get("BlockType") != "KEY_VALUE_SET"
                or "VALUE" not in value_entities
            ):
                continue
            if raw := graph.text(value).strip():
                key_text = graph.text(key).strip()
                output.append(
                    _make(
                        field,
                        raw,
                        f"{key_text}: {raw}",
                        value,
                        source_type="KEY_VALUE_SET",
                        block_ids=[
                            str(key.get("Id", "")),
                            value_id,
                            *graph.relationship_ids(key, "CHILD"),
                            *graph.relationship_ids(value, "CHILD"),
                        ],
                        extracted_at=extracted_at,
                        model_version=model_version,
                        page_number=graph.page_number(value),
                        bounding_box=_union_bbox([key, value]),
                    )
                )


def _parse_tables(
    graph: TextractBlockGraph,
    output: list[ProviderFieldEvidence],
    extracted_at: datetime,
    model_version: str,
) -> None:
    for table in (item for item in graph.blocks if item.get("BlockType") == "TABLE"):
        cells = [
            graph.by_id[item]
            for item in graph.relationship_ids(table, "CHILD")
            if item in graph.by_id
            and graph.by_id[item].get("BlockType") in {"CELL", "MERGED_CELL"}
        ]
        by_row: dict[int, dict[int, dict[str, Any]]] = {}
        for cell in cells:
            row, col = cell.get("RowIndex"), cell.get("ColumnIndex")
            if isinstance(row, int) and isinstance(col, int):
                by_row.setdefault(row, {})[col] = cell
        if not by_row:
            continue
        header_row = min(by_row)
        headers = {
            col: graph.text(cell).strip() for col, cell in by_row[header_row].items()
        }
        lab_map = {
            col: LAB_HEADERS[_label(text)]
            for col, text in headers.items()
            if _label(text) in LAB_HEADERS
        }
        med_map = {
            col: MED_HEADERS[_label(text)]
            for col, text in headers.items()
            if _label(text) in MED_HEADERS
        }
        if {"test", "result"} & set(lab_map.values()):
            kind, mapping = "lab_result", lab_map
        elif "medicine" in med_map.values():
            kind, mapping = "medication", med_map
        else:
            continue
        for row_index in sorted(row for row in by_row if row > header_row):
            row_cells = by_row[row_index]
            parts = {
                name: graph.text(row_cells[col]).strip()
                for col, name in mapping.items()
                if col in row_cells and graph.text(row_cells[col]).strip()
            }
            if not parts:
                continue
            primary = (
                (parts.get("result") if kind == "lab_result" else parts.get("medicine"))
                or parts.get("test")
                or " | ".join(parts.values())
            )
            canonical = kind
            if kind == "lab_result":
                test_label = _label(parts.get("test", ""))
                if test_label == "hba1c":
                    canonical = "hba1c"
                elif test_label in {"blood glucose", "glucose", "fbs", "ppbs", "rbs"}:
                    canonical = "blood_glucose"
            ordered = [row_cells[col] for col in sorted(row_cells)]
            row_text = " | ".join(
                graph.text(cell).strip() for cell in ordered if graph.text(cell).strip()
            )
            primary_name = "result" if kind == "lab_result" else "medicine"
            primary_cell = next(
                (
                    row_cells[col]
                    for col, name in mapping.items()
                    if name == primary_name and col in row_cells
                ),
                ordered[0],
            )
            incomplete = (
                not ({"test", "result"} <= parts.keys())
                if kind == "lab_result"
                else "medicine" not in parts
            )
            item = _make(
                canonical,
                primary,
                row_text,
                primary_cell,
                source_type="CELL",
                block_ids=[
                    str(cell["Id"])
                    for cell in ordered
                    if isinstance(cell.get("Id"), str)
                ],
                extracted_at=extracted_at,
                model_version=model_version,
                page_number=graph.page_number(primary_cell),
                bounding_box=_union_bbox(ordered),
                structured={**parts, "row_index": str(row_index)},
                incomplete=incomplete,
                raw_unit=parts.get("unit"),
                reference_range=parts.get("reference_range"),
            )
            if canonical != kind and parts.get("result") and parts.get("unit"):
                normalized = normalize_extracted_value(
                    canonical, f"{parts['result']} {parts['unit']}"
                )
                item = item.model_copy(
                    update={
                        "normalized_value": normalized.value,
                        "normalized_unit": normalized.unit,
                    }
                )
            output.append(item)
