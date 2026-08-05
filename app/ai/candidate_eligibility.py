"""Pure candidate eligibility classification for benchmark projections."""

from __future__ import annotations

from enum import StrEnum

from app.ai.semantic_evidence import SemanticCandidate
from app.ai.medical_validator import validate_field


class CandidateEligibility(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE_QUERY_ONLY_INVALID_FORMAT = "INELIGIBLE_QUERY_ONLY_INVALID_FORMAT"


def classify_semantic_candidate(candidate: SemanticCandidate) -> CandidateEligibility:
    """Classify without reading expectations or mutating authentic evidence."""
    evidence = candidate.evidence
    query_only = bool(evidence) and all(
        item.source_type == "QUERY_RESULT" for item in evidence
    )
    if (
        query_only
        and not validate_field(
            candidate.representative.canonical_field_name,
            candidate.representative.raw_value,
        ).is_valid
    ):
        return CandidateEligibility.INELIGIBLE_QUERY_ONLY_INVALID_FORMAT
    return CandidateEligibility.ELIGIBLE


def classify_candidate(candidate: SemanticCandidate) -> CandidateEligibility:
    """Short alias for callers that operate on semantic candidates."""
    return classify_semantic_candidate(candidate)
