import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.ai.extraction_normalization import normalize_extracted_value
from app.ai.textract_parser import TextractBlockGraph, parse_textract_blocks


BOX = {"BoundingBox": {"Left": 0.1, "Top": 0.1, "Width": 0.1, "Height": 0.05}}
FIXTURES = Path(__file__).parent / "fixtures"


def word(block_id, text):
    return {
        "BlockType": "WORD",
        "Id": block_id,
        "Text": text,
        "Page": 1,
        "Confidence": 99.0,
        "Geometry": BOX,
    }


def cell(block_id, row, col, children, confidence=96.0, block_type="CELL"):
    return {
        "BlockType": block_type,
        "Id": block_id,
        "RowIndex": row,
        "ColumnIndex": col,
        "Page": 1,
        "Confidence": confidence,
        "Geometry": BOX,
        "Relationships": [{"Type": "CHILD", "Ids": children}],
    }


def parse(blocks, *, validated_document_page_count=None):
    return parse_textract_blocks(
        blocks,
        extracted_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        model_version="1.0",
        validated_document_page_count=validated_document_page_count,
    )


def test_synthetic_json_fixture_exercises_production_parser():
    response = json.loads(
        (FIXTURES / "structured_textract.json").read_text(encoding="utf-8")
    )
    items = parse(response["Blocks"])
    assert [item.raw_value for item in items] == [
        "Synthetic Drug A 5 mg",
        "Synthetic Drug B 10 mg",
    ]


def test_queries_preserve_repeated_conflicting_answers_and_deduplicate_exact_evidence():
    blocks = [
        {
            "BlockType": "QUERY",
            "Id": "q",
            "Query": {"Alias": "medication"},
            "Relationships": [{"Type": "ANSWER", "Ids": ["a1", "a2", "a1"]}],
        },
        {
            "BlockType": "QUERY_RESULT",
            "Id": "a1",
            "Text": "Drug A 5 mg",
            "Page": 1,
            "Confidence": 91.0,
            "Geometry": BOX,
        },
        {
            "BlockType": "QUERY_RESULT",
            "Id": "a2",
            "Text": "Drug B 10 mg",
            "Page": 1,
            "Confidence": 88.0,
            "Geometry": {
                "BoundingBox": {"Left": 0.2, "Top": 0.2, "Width": 0.1, "Height": 0.05}
            },
        },
        {
            "BlockType": "QUERY",
            "Id": "unknown",
            "Query": {"Alias": "invented"},
            "Relationships": [{"Type": "ANSWER", "Ids": ["a1"]}],
        },
    ]
    items = parse(blocks)
    assert [item.raw_value for item in items] == ["Drug A 5 mg", "Drug B 10 mg"]
    assert items[0].source_text == "Drug A 5 mg"
    assert items[0].page_number == 0
    assert items[0].field_confidence == pytest.approx(0.91)
    assert items[0].evidence_hash and items[0].source_block_ids == ("a1",)


def test_page_lineage_resolves_query_form_and_table_descendants_without_cycles():
    graph = TextractBlockGraph(
        [
            {
                "BlockType": "PAGE",
                "Id": "p1",
                "Page": 1,
                "Relationships": [{"Type": "CHILD", "Ids": ["query", "key", "t"]}],
            },
            {
                "BlockType": "QUERY",
                "Id": "query",
                "Relationships": [{"Type": "ANSWER", "Ids": ["q"]}],
            },
            {"BlockType": "QUERY_RESULT", "Id": "q"},
            {
                "BlockType": "KEY_VALUE_SET",
                "Id": "key",
                "Relationships": [{"Type": "VALUE", "Ids": ["v"]}],
            },
            {"BlockType": "KEY_VALUE_SET", "Id": "v"},
            {
                "BlockType": "TABLE",
                "Id": "t",
                "Relationships": [{"Type": "CHILD", "Ids": ["c"]}],
            },
            {
                "BlockType": "CELL",
                "Id": "c",
                "Relationships": [{"Type": "CHILD", "Ids": ["t"]}],
            },
        ]
    )
    assert graph.page_number(graph.by_id["q"]) == 0
    assert graph.page_number(graph.by_id["v"]) == 0
    assert graph.page_number(graph.by_id["c"]) == 0


def test_page_lineage_direct_multiple_ambiguous_and_unknown():
    graph = TextractBlockGraph(
        [
            {
                "BlockType": "PAGE",
                "Id": "p1",
                "Page": 1,
                "Relationships": [{"Type": "CHILD", "Ids": ["x"]}],
            },
            {
                "BlockType": "PAGE",
                "Id": "p2",
                "Page": 2,
                "Relationships": [{"Type": "CHILD", "Ids": ["x"]}],
            },
            {"BlockType": "WORD", "Id": "direct", "Page": 2},
            {"BlockType": "WORD", "Id": "x"},
            {"BlockType": "WORD", "Id": "unknown"},
        ]
    )
    assert graph.page_number(graph.by_id["direct"]) == 1
    assert graph.page_number(graph.by_id["x"]) is None
    assert graph.page_number(graph.by_id["unknown"]) is None


def test_validated_single_page_fallback_requires_authentic_unique_page_ancestry():
    blocks = [
        {
            "BlockType": "PAGE",
            "Id": "p",
            "Relationships": [{"Type": "CHILD", "Ids": ["query", "key", "table"]}],
        },
        {
            "BlockType": "QUERY",
            "Id": "query",
            "Relationships": [{"Type": "ANSWER", "Ids": ["answer"]}],
        },
        {"BlockType": "QUERY_RESULT", "Id": "answer"},
        {
            "BlockType": "KEY_VALUE_SET",
            "Id": "key",
            "Relationships": [{"Type": "VALUE", "Ids": ["value"]}],
        },
        {"BlockType": "KEY_VALUE_SET", "Id": "value"},
        {
            "BlockType": "TABLE",
            "Id": "table",
            "Relationships": [{"Type": "CHILD", "Ids": ["cell"]}],
        },
        {"BlockType": "CELL", "Id": "cell"},
        {"BlockType": "LINE", "Id": "unrelated"},
    ]
    graph = TextractBlockGraph(blocks, validated_document_page_count=1)
    assert graph.page_number(graph.by_id["answer"]) == 0
    assert graph.page_number(graph.by_id["value"]) == 0
    assert graph.page_number(graph.by_id["cell"]) == 0
    assert graph.page_number(graph.by_id["unrelated"]) is None


def test_parser_applies_validated_context_but_unvalidated_call_does_not_assume_page():
    blocks = [
        {
            "BlockType": "PAGE",
            "Id": "p",
            "Relationships": [{"Type": "CHILD", "Ids": ["q"]}],
        },
        {
            "BlockType": "QUERY",
            "Id": "q",
            "Query": {"Alias": "hba1c"},
            "Relationships": [{"Type": "ANSWER", "Ids": ["a"]}],
        },
        {
            "BlockType": "QUERY_RESULT",
            "Id": "a",
            "Text": "7.2 %",
            "Confidence": 95.0,
            "Geometry": BOX,
        },
    ]
    assert parse(blocks)[0].page_number is None
    assert parse(blocks, validated_document_page_count=1)[0].page_number == 0


@pytest.mark.parametrize("validated_count", [None, 2])
def test_missing_page_number_does_not_fallback_without_validated_single_page(
    validated_count,
):
    blocks = [
        {
            "BlockType": "PAGE",
            "Id": "p",
            "Relationships": [{"Type": "CHILD", "Ids": ["x"]}],
        },
        {"BlockType": "WORD", "Id": "x"},
    ]
    graph = TextractBlockGraph(blocks, validated_document_page_count=validated_count)
    assert graph.page_number(graph.by_id["x"]) is None


def test_no_page_multiple_pages_and_ambiguous_ancestry_do_not_fallback():
    no_page = TextractBlockGraph(
        [{"BlockType": "WORD", "Id": "x"}], validated_document_page_count=1
    )
    assert no_page.page_number(no_page.by_id["x"]) is None
    blocks = [
        {
            "BlockType": "PAGE",
            "Id": "p1",
            "Relationships": [{"Type": "CHILD", "Ids": ["x"]}],
        },
        {
            "BlockType": "PAGE",
            "Id": "p2",
            "Relationships": [{"Type": "CHILD", "Ids": ["x"]}],
        },
        {"BlockType": "WORD", "Id": "x"},
    ]
    graph = TextractBlockGraph(blocks, validated_document_page_count=1)
    assert graph.page_number(graph.by_id["x"]) is None


def test_direct_page_wins_and_cycles_remain_safe_with_validated_context():
    blocks = [
        {
            "BlockType": "PAGE",
            "Id": "p",
            "Relationships": [{"Type": "CHILD", "Ids": ["a"]}],
        },
        {
            "BlockType": "WORD",
            "Id": "a",
            "Page": 2,
            "Relationships": [{"Type": "CHILD", "Ids": ["b"]}],
        },
        {
            "BlockType": "WORD",
            "Id": "b",
            "Relationships": [{"Type": "CHILD", "Ids": ["a"]}],
        },
        {"BlockType": "WORD"},
    ]
    graph = TextractBlockGraph(blocks, validated_document_page_count=1)
    assert graph.page_number(graph.by_id["a"]) == 1
    assert graph.page_number(blocks[-1]) is None


@pytest.mark.parametrize("confidence", ["99", True, -1, 101, float("nan")])
def test_malformed_confidence_is_unavailable(confidence):
    blocks = [
        {
            "BlockType": "QUERY",
            "Id": "q",
            "Query": {"Alias": "hba1c"},
            "Relationships": [{"Type": "ANSWER", "Ids": ["a"]}],
        },
        {
            "BlockType": "QUERY_RESULT",
            "Id": "a",
            "Text": "7.2 %",
            "Page": 1,
            "Confidence": confidence,
            "Geometry": BOX,
        },
    ]
    assert parse(blocks)[0].field_confidence is None


def test_missing_answer_relationship_and_malformed_geometry_do_not_fabricate_evidence():
    assert parse([{"BlockType": "QUERY", "Id": "q", "Query": {"Alias": "hba1c"}}]) == []
    blocks = [
        {
            "BlockType": "QUERY",
            "Id": "q",
            "Query": {"Alias": "hba1c"},
            "Relationships": [{"Type": "ANSWER", "Ids": ["a"]}],
        },
        {
            "BlockType": "QUERY_RESULT",
            "Id": "a",
            "Text": "7.2 %",
            "Page": 1,
            "Confidence": 90.0,
            "Geometry": {
                "BoundingBox": {"Left": 0.95, "Top": 0.1, "Width": 0.2, "Height": 0.1}
            },
        },
    ]
    assert parse(blocks)[0].bounding_box is None


def test_form_synonyms_selection_elements_and_unknown_keys():
    blocks = [
        {
            "BlockType": "KEY_VALUE_SET",
            "Id": "k",
            "EntityTypes": ["KEY"],
            "Page": 1,
            "Geometry": BOX,
            "Relationships": [
                {"Type": "CHILD", "Ids": ["kw"]},
                {"Type": "VALUE", "Ids": ["v"]},
            ],
        },
        word("kw", "Pulse"),
        {
            "BlockType": "KEY_VALUE_SET",
            "Id": "v",
            "EntityTypes": ["VALUE"],
            "Page": 1,
            "Confidence": 93.0,
            "Geometry": BOX,
            "Relationships": [{"Type": "CHILD", "Ids": ["vw", "sel"]}],
        },
        word("vw", "72 bpm"),
        {
            "BlockType": "SELECTION_ELEMENT",
            "Id": "sel",
            "SelectionStatus": "SELECTED",
            "Page": 1,
            "Confidence": 90.0,
            "Geometry": BOX,
        },
        {
            "BlockType": "KEY_VALUE_SET",
            "Id": "unknown",
            "EntityTypes": ["KEY"],
            "Relationships": [
                {"Type": "CHILD", "Ids": ["uw"]},
                {"Type": "VALUE", "Ids": ["v"]},
            ],
        },
        word("uw", "Unapproved Context"),
    ]
    items = parse(blocks)
    assert len(items) == 1
    assert items[0].canonical_field_name == "heart_rate"
    assert items[0].raw_value == "72 bpm SELECTED"
    assert items[0].source_type == "KEY_VALUE_SET"


def test_laboratory_and_medication_tables_preserve_rows_headers_and_incompleteness():
    blocks = [
        {
            "BlockType": "TABLE",
            "Id": "lab",
            "Relationships": [
                {
                    "Type": "CHILD",
                    "Ids": ["lh1", "lh2", "lh3", "lr11", "lr12", "lr13", "lr21"],
                }
            ],
        },
        cell("lh1", 1, 1, ["wtest"], block_type="MERGED_CELL"),
        cell("lh2", 1, 2, ["wvalue"]),
        cell("lh3", 1, 3, ["wunit"]),
        word("wtest", "Investigation"),
        word("wvalue", "Value"),
        word("wunit", "Units"),
        cell("lr11", 2, 1, ["whba"]),
        cell("lr12", 2, 2, ["w72"]),
        cell("lr13", 2, 3, ["wpct"]),
        word("whba", "HbA1c"),
        word("w72", "7.2"),
        word("wpct", "%"),
        cell("lr21", 3, 1, ["wglu"]),
        word("wglu", "Glucose"),
        {
            "BlockType": "TABLE",
            "Id": "med",
            "Relationships": [{"Type": "CHILD", "Ids": ["mh", "mr1", "mr2"]}],
        },
        cell("mh", 1, 1, ["wmed"]),
        word("wmed", "Drug"),
        cell("mr1", 2, 1, ["wda"]),
        word("wda", "Drug A"),
        cell("mr2", 3, 1, ["wdb"]),
        word("wdb", "Drug B"),
    ]
    items = parse(blocks)
    labs = [
        item
        for item in items
        if item.source_type == "CELL" and item.canonical_field_name != "medication"
    ]
    meds = [item for item in items if item.canonical_field_name == "medication"]
    assert len(labs) == 2 and len(meds) == 2
    assert labs[0].source_text == "HbA1c | 7.2 | %"
    assert labs[0].raw_unit == "%" and labs[0].reference_range is None
    assert labs[0].canonical_field_name == "hba1c" and labs[0].normalized_value == "7.2"
    assert labs[1].incomplete is True
    assert meds[0].structured_value["medicine"] == "Drug A"


def test_normalization_is_conservative_and_never_guesses_units():
    assert normalize_extracted_value("hba1c", "HbA1c: 7.2 %").value == "7.2"
    assert normalize_extracted_value("blood_glucose", "110").value is None
    assert normalize_extracted_value("blood_glucose", "110 mg/dL").unit == "mg/dl"
    assert normalize_extracted_value("blood_pressure", "120/80").value is None
    assert normalize_extracted_value("blood_pressure", "120/80 mmHg").value == "120/80"
    assert (
        normalize_extracted_value("phone", "+91 98765 43210").value == "+919876543210"
    )
    assert (
        normalize_extracted_value("aadhaar_abha_id", "12-3456-7890-1234").value
        == "12-3456-7890-1234"
    )


def test_graph_reconstructs_line_word_and_selection_children_without_cycles():
    graph = TextractBlockGraph(
        [
            {
                "BlockType": "LINE",
                "Id": "line",
                "Relationships": [{"Type": "CHILD", "Ids": ["w", "s", "line"]}],
            },
            word("w", "Synthetic"),
            {
                "BlockType": "SELECTION_ELEMENT",
                "Id": "s",
                "SelectionStatus": "NOT_SELECTED",
            },
        ]
    )
    assert graph.text(graph.by_id["line"]) == "Synthetic NOT_SELECTED"
