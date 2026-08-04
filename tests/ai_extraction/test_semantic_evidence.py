from datetime import datetime, timezone

from app.ai.semantic_evidence import group_semantic_candidates
from app.models.ai_models import ExtractedMedicalDocument, ProviderFieldEvidence
from app.models.field_evidence import NormalizedBoundingBox
from app.services.pipeline_orchestrator import _candidate_fields


def evidence(source_type: str, top: float, block: str) -> ProviderFieldEvidence:
    return ProviderFieldEvidence(
        canonical_field_name="hba1c",
        raw_value="7.2 %",
        normalized_value="7.2",
        normalized_unit="%",
        source_text="7.2 %",
        page_number=0,
        bounding_box=NormalizedBoundingBox(
            left=0.1, top=top, right=0.3, bottom=top + 0.05
        ),
        provider_name="aws_textract",
        provider_api_version="test",
        extraction_timestamp=datetime(2026, 8, 5, tzinfo=timezone.utc),
        evidence_hash=block * 64,
        source_type=source_type,
        source_block_ids=(block,),
    )


def test_query_and_form_at_same_location_are_one_candidate_with_all_provenance():
    groups = group_semantic_candidates(
        [evidence("QUERY_RESULT", 0.1, "a"), evidence("KEY_VALUE_SET", 0.1, "b")]
    )
    assert len(groups) == 1
    assert len(groups[0].evidence) == 2
    assert groups[0].representative.supporting_evidence_hashes == ("a" * 64, "b" * 64)
    assert groups[0].representative.supporting_source_block_ids == ("a", "b")


def test_identical_values_at_separate_locations_remain_distinct():
    groups = group_semantic_candidates(
        [evidence("QUERY_RESULT", 0.1, "a"), evidence("QUERY_RESULT", 0.5, "b")]
    )
    assert len(groups) == 2


def test_production_staging_uses_one_candidate_and_preserves_support_ids():
    document = ExtractedMedicalDocument(
        patient_name="",
        phone="",
        aadhaar_abha_id="",
        diagnoses=[],
        lab_results=[],
        prescriptions=[],
        field_evidence=[
            evidence("QUERY_RESULT", 0.1, "a"),
            evidence("KEY_VALUE_SET", 0.1, "b"),
        ],
    )
    candidates = _candidate_fields(document)
    assert len(candidates) == 1
    provider = candidates[0]["provider_evidence"]
    assert provider.supporting_evidence_hashes == ("a" * 64, "b" * 64)
    assert provider.supporting_source_block_ids == ("a", "b")
