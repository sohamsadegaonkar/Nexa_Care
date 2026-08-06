"""Value-free failure classification for synthetic benchmark diagnostics."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

from app.ai.extraction_normalization import normalize_extracted_value
from app.ai.semantic_evidence import SemanticCandidate

PRIMARY_CLASSES = (
    "CROSS_SOURCE_SAME_OCCURRENCE",
    "TRUE_DUPLICATE",
    "SAME_VALUE_DISTINCT_AUTHENTIC_LOCATION",
    "RAW_REPRESENTATION_VARIANCE",
    "MALFORMED_QUERY_ONLY",
    "INCORRECT_FIELD_CLASSIFICATION",
    "UNIT_PARSING_FAILURE",
    "VALUE_PARSING_FAILURE",
    "IDENTITY_MISSING",
    "IDENTITY_NONMATCHING",
    "IDENTITY_CONFLICTING",
    "UNMATCHED_EXPECTED_OCCURRENCE",
    "UNEXPECTED_EXTRACTED_CANDIDATE",
    "UNRESOLVED_SAFE_DIAGNOSIS",
)
IDENTITY_FIELDS = frozenset({"patient_name", "phone", "aadhaar_abha_id"})
IDENTITY_STATUS_CLASSES = {
    "missing": "IDENTITY_MISSING",
    "nonmatching": "IDENTITY_NONMATCHING",
    "conflicting": "IDENTITY_CONFLICTING",
}
MALFORMED_REASON = "INELIGIBLE_QUERY_ONLY_INVALID_FORMAT"
CLASSIFICATION_FAILED_REASON = "INELIGIBLE_CLASSIFICATION_FAILED"


def _signature(candidate: SemanticCandidate) -> str:
    return "+".join(
        sorted({item.source_type for item in candidate.evidence if item.source_type})
    )


def _expected_unit(target: Mapping[str, Any]) -> str | None:
    unit = target.get("unit")
    if unit is not None:
        return unit
    field = target.get("canonical_field")
    raw_value = target.get("raw_value")
    if not isinstance(field, str) or not isinstance(raw_value, str):
        return None
    return normalize_extracted_value(field, raw_value).unit


def _same_value(left: SemanticCandidate, right: SemanticCandidate) -> bool:
    left_value = left.representative
    right_value = right.representative
    return (
        left_value.canonical_field_name == right_value.canonical_field_name
        and left_value.raw_value == right_value.raw_value
        and left_value.normalized_value == right_value.normalized_value
        and left_value.normalized_unit == right_value.normalized_unit
    )


class FailureClassificationAccumulator:
    """Accumulate deterministic, serialized-safe failure classifications."""

    def __init__(self) -> None:
        self._candidate_counts = {
            name: Counter(exact=0, semantic=0) for name in PRIMARY_CLASSES
        }
        self._expected_counts = {
            name: Counter(exact=0, semantic=0) for name in PRIMARY_CLASSES
        }
        self._field_counts: dict[str, dict[str, dict[str, Counter[str]]]] = {
            name: {
                "candidates": {"exact": Counter(), "semantic": Counter()},
                "expected": {"exact": Counter(), "semantic": Counter()},
            }
            for name in PRIMARY_CLASSES
        }
        self._source_counts: dict[str, dict[str, dict[str, Counter[str]]]] = {
            name: {
                "candidates": {"exact": Counter(), "semantic": Counter()},
                "expected": {"exact": Counter(), "semantic": Counter()},
            }
            for name in PRIMARY_CLASSES
        }
        self._case_indexes: dict[str, dict[str, dict[str, set[int]]]] = {
            name: {
                "candidates": {"exact": set(), "semantic": set()},
                "expected": {"exact": set(), "semantic": set()},
            }
            for name in PRIMARY_CLASSES
        }
        self._group_counts = Counter(
            CROSS_SOURCE_SAME_OCCURRENCE=0,
            TRUE_DUPLICATE=0,
        )
        self._group_field_counts = {name: Counter() for name in self._group_counts}
        self._group_source_counts = {name: Counter() for name in self._group_counts}
        self._group_case_indexes = {name: set() for name in self._group_counts}
        self._grouped_support_record_count = 0

    def _record(
        self,
        *,
        primary_class: str,
        entity: str,
        scope: str,
        case_index: int,
        canonical_field: str,
        source_signature: str,
    ) -> None:
        if entity == "candidates":
            self._candidate_counts[primary_class][scope] += 1
        else:
            self._expected_counts[primary_class][scope] += 1
        self._field_counts[primary_class][entity][scope][canonical_field] += 1
        self._source_counts[primary_class][entity][scope][source_signature] += 1
        self._case_indexes[primary_class][entity][scope].add(case_index)

    @staticmethod
    def _identity_class(
        field: str,
        identity_statuses: Mapping[str, str],
    ) -> str | None:
        if field not in IDENTITY_FIELDS:
            return None
        status = identity_statuses.get(field, "exact")
        return IDENTITY_STATUS_CLASSES.get(status)

    @staticmethod
    def _parse_failure_class(
        target: Mapping[str, Any],
        candidate: SemanticCandidate,
    ) -> str | None:
        field = target.get("canonical_field")
        expected_normalized = target.get("normalized_value")
        actual = candidate.representative
        if (
            isinstance(expected_normalized, str)
            and expected_normalized
            and actual.raw_value
            and actual.normalized_value is None
        ):
            return "VALUE_PARSING_FAILURE"
        expected_unit = _expected_unit(target)
        if (
            isinstance(expected_unit, str)
            and expected_unit
            and actual.raw_unit
            and actual.normalized_unit != expected_unit
        ):
            return "UNIT_PARSING_FAILURE"
        if field in IDENTITY_FIELDS:
            return None
        return None

    @staticmethod
    def _query_only(candidate: SemanticCandidate) -> bool:
        return bool(candidate.evidence) and all(
            item.source_type == "QUERY_RESULT" for item in candidate.evidence
        )

    @staticmethod
    def _reason(eligibility: Any) -> str | None:
        if eligibility is None:
            return None
        return str(getattr(eligibility, "value", eligibility))

    def add_case(
        self,
        *,
        case_index: int,
        expected: Sequence[Mapping[str, Any]],
        candidates: Sequence[SemanticCandidate],
        exact_matches: Sequence[tuple[int, int]],
        semantic_matches: Sequence[tuple[int, int]],
        identity_statuses: Mapping[str, str],
        eligibility_by_index: Mapping[int, Any],
    ) -> None:
        exact_expected = {index for index, _ in exact_matches}
        exact_candidates = {index for _, index in exact_matches}
        semantic_expected = {index for index, _ in semantic_matches}
        semantic_candidates = {index for _, index in semantic_matches}
        semantic_by_candidate = {
            candidate_index: expected_index
            for expected_index, candidate_index in semantic_matches
        }
        for candidate in candidates:
            if len(candidate.evidence) <= 1:
                continue
            source_types = {
                item.source_type for item in candidate.evidence if item.source_type
            }
            primary_class = (
                "CROSS_SOURCE_SAME_OCCURRENCE"
                if len(source_types) > 1
                else "TRUE_DUPLICATE"
            )
            self._group_counts[primary_class] += 1
            self._group_field_counts[primary_class][
                candidate.representative.canonical_field_name
            ] += 1
            self._group_source_counts[primary_class][_signature(candidate)] += 1
            self._group_case_indexes[primary_class].add(case_index)
            self._grouped_support_record_count += len(candidate.evidence) - 1

        unmatched_expected_by_field: dict[str, list[int]] = defaultdict(list)
        for expected_index, target in enumerate(expected):
            if expected_index not in exact_expected:
                unmatched_expected_by_field[target["canonical_field"]].append(
                    expected_index
                )
        unmatched_candidates_by_field: dict[str, list[int]] = defaultdict(list)
        for candidate_index, candidate in enumerate(candidates):
            if candidate_index not in exact_candidates:
                unmatched_candidates_by_field[
                    candidate.representative.canonical_field_name
                ].append(candidate_index)

        parse_failures_by_candidate: dict[int, str] = {}
        parse_failures_by_expected: dict[int, str] = {}
        for field in sorted(
            set(unmatched_expected_by_field) | set(unmatched_candidates_by_field)
        ):
            for expected_index, candidate_index in zip(
                unmatched_expected_by_field[field],
                unmatched_candidates_by_field[field],
                strict=False,
            ):
                primary_class = self._parse_failure_class(
                    expected[expected_index], candidates[candidate_index]
                )
                if primary_class is not None:
                    parse_failures_by_expected[expected_index] = primary_class
                    parse_failures_by_candidate[candidate_index] = primary_class

        def expected_class(expected_index: int, *, raw_variance: bool) -> str:
            target = expected[expected_index]
            field = target["canonical_field"]
            if raw_variance and expected_index in semantic_expected:
                candidate_index = next(
                    candidate
                    for expected_index_value, candidate in semantic_matches
                    if expected_index_value == expected_index
                )
                candidate = candidates[candidate_index]
                if (
                    field not in IDENTITY_FIELDS
                    and target.get("normalized_value")
                    and candidate.representative.normalized_value
                    and candidate.representative.normalized_value
                    == target.get("normalized_value")
                    and candidate.representative.normalized_unit
                    == _expected_unit(target)
                ):
                    return "RAW_REPRESENTATION_VARIANCE"
            identity_class = self._identity_class(field, identity_statuses)
            if identity_class is not None:
                return identity_class
            return parse_failures_by_expected.get(
                expected_index, "UNMATCHED_EXPECTED_OCCURRENCE"
            )

        for expected_index, target in enumerate(expected):
            if expected_index not in exact_expected:
                self._record(
                    primary_class=expected_class(expected_index, raw_variance=True),
                    entity="expected",
                    scope="exact",
                    case_index=case_index,
                    canonical_field=target["canonical_field"],
                    source_signature=str(target.get("source_category") or ""),
                )
            if expected_index not in semantic_expected:
                self._record(
                    primary_class=expected_class(expected_index, raw_variance=False),
                    entity="expected",
                    scope="semantic",
                    case_index=case_index,
                    canonical_field=target["canonical_field"],
                    source_signature=str(target.get("source_category") or ""),
                )

        def candidate_class(candidate_index: int, *, raw_variance: bool) -> str:
            candidate = candidates[candidate_index]
            field = candidate.representative.canonical_field_name
            identity_class = self._identity_class(field, identity_statuses)
            if identity_class is not None:
                return identity_class
            if raw_variance and candidate_index in semantic_by_candidate:
                expected_index = semantic_by_candidate[candidate_index]
                target = expected[expected_index]
                if (
                    field not in IDENTITY_FIELDS
                    and target.get("normalized_value")
                    and candidate.representative.normalized_value
                    and candidate.representative.normalized_value
                    == target.get("normalized_value")
                    and candidate.representative.normalized_unit
                    == _expected_unit(target)
                ):
                    return "RAW_REPRESENTATION_VARIANCE"
            reason = self._reason(eligibility_by_index.get(candidate_index))
            if reason == MALFORMED_REASON:
                return "MALFORMED_QUERY_ONLY"
            if reason == CLASSIFICATION_FAILED_REASON:
                return "UNRESOLVED_SAFE_DIAGNOSIS"
            if candidate_index in parse_failures_by_candidate:
                return parse_failures_by_candidate[candidate_index]
            if any(
                other_index != candidate_index and _same_value(candidate, other)
                for other_index, other in enumerate(candidates)
            ):
                return "SAME_VALUE_DISTINCT_AUTHENTIC_LOCATION"
            if self._query_only(candidate) or candidate.evidence:
                return "UNEXPECTED_EXTRACTED_CANDIDATE"
            return "UNRESOLVED_SAFE_DIAGNOSIS"

        for candidate_index, candidate in enumerate(candidates):
            if candidate_index not in exact_candidates:
                self._record(
                    primary_class=candidate_class(candidate_index, raw_variance=True),
                    entity="candidates",
                    scope="exact",
                    case_index=case_index,
                    canonical_field=candidate.representative.canonical_field_name,
                    source_signature=_signature(candidate),
                )
            if candidate_index not in semantic_candidates:
                self._record(
                    primary_class=candidate_class(candidate_index, raw_variance=False),
                    entity="candidates",
                    scope="semantic",
                    case_index=case_index,
                    canonical_field=candidate.representative.canonical_field_name,
                    source_signature=_signature(candidate),
                )

    @staticmethod
    def _sorted_counter(counter: Counter[str]) -> dict[str, int]:
        return dict(sorted(counter.items()))

    def result(
        self,
        *,
        expected_field_occurrences: int,
        exact_match_count: int,
        semantic_match_count: int,
        semantic_candidate_count: int,
        evidence_occurrences: int,
        routing_eligible_count: int,
        routing_ineligible_count: int,
    ) -> dict[str, Any]:
        candidate_counts = {
            name: self._sorted_counter(self._candidate_counts[name])
            for name in PRIMARY_CLASSES
        }
        expected_counts = {
            name: self._sorted_counter(self._expected_counts[name])
            for name in PRIMARY_CLASSES
        }
        counts_by_class = {
            name: {
                "represented_semantic_groups": self._group_counts.get(name, 0),
                "exact_candidates": candidate_counts[name]["exact"],
                "semantic_candidates": candidate_counts[name]["semantic"],
                "exact_expected": expected_counts[name]["exact"],
                "semantic_expected": expected_counts[name]["semantic"],
            }
            for name in PRIMARY_CLASSES
        }
        counts_by_field = {
            name: {
                entity: {
                    scope: self._sorted_counter(self._field_counts[name][entity][scope])
                    for scope in ("exact", "semantic")
                }
                for entity in ("candidates", "expected")
            }
            for name in PRIMARY_CLASSES
        }
        case_indexes = {
            name: {
                entity: {
                    scope: sorted(self._case_indexes[name][entity][scope])
                    for scope in ("exact", "semantic")
                }
                for entity in ("candidates", "expected")
            }
            for name in PRIMARY_CLASSES
        }
        source_signatures = {
            name: {
                entity: {
                    scope: self._sorted_counter(
                        self._source_counts[name][entity][scope]
                    )
                    for scope in ("exact", "semantic")
                }
                for entity in ("candidates", "expected")
            }
            for name in PRIMARY_CLASSES
        }
        for name in self._group_counts:
            counts_by_field[name]["groups"] = self._sorted_counter(
                self._group_field_counts[name]
            )
            source_signatures[name]["groups"] = self._sorted_counter(
                self._group_source_counts[name]
            )
            case_indexes[name]["groups"] = sorted(self._group_case_indexes[name])

        exact_unmatched_candidates = semantic_candidate_count - exact_match_count
        semantic_unmatched_candidates = semantic_candidate_count - semantic_match_count
        exact_unmatched_expected = expected_field_occurrences - exact_match_count
        semantic_unmatched_expected = expected_field_occurrences - semantic_match_count
        candidate_exact_sum = sum(
            values["exact"] for values in candidate_counts.values()
        )
        candidate_semantic_sum = sum(
            values["semantic"] for values in candidate_counts.values()
        )
        identity_classes = {
            name: len(
                set(case_indexes[name]["candidates"]["exact"])
                | set(case_indexes[name]["expected"]["exact"])
            )
            for name in (
                "IDENTITY_MISSING",
                "IDENTITY_NONMATCHING",
                "IDENTITY_CONFLICTING",
            )
        }
        identity_failed_cases = set()
        for name in identity_classes:
            identity_failed_cases.update(case_indexes[name]["candidates"]["exact"])
            identity_failed_cases.update(case_indexes[name]["expected"]["exact"])
        checks = {
            "evidence_minus_semantic_candidates_equals_grouped_support": {
                "left": evidence_occurrences - semantic_candidate_count,
                "right": self._grouped_support_record_count,
            },
            "expected_minus_exact_equals_exact_unmatched": {
                "left": expected_field_occurrences - exact_match_count,
                "right": exact_unmatched_expected,
            },
            "candidates_minus_exact_equals_exact_unmatched": {
                "left": semantic_candidate_count - exact_match_count,
                "right": exact_unmatched_candidates,
            },
            "expected_minus_semantic_equals_semantic_unmatched": {
                "left": expected_field_occurrences - semantic_match_count,
                "right": semantic_unmatched_expected,
            },
            "candidates_minus_semantic_equals_semantic_unmatched": {
                "left": semantic_candidate_count - semantic_match_count,
                "right": semantic_unmatched_candidates,
            },
            "routing_eligible_plus_ineligible_equals_candidates": {
                "left": routing_eligible_count + routing_ineligible_count,
                "right": semantic_candidate_count,
            },
            "exact_unmatched_candidate_primary_sum": {
                "left": candidate_exact_sum,
                "right": exact_unmatched_candidates,
            },
            "semantic_unmatched_candidate_primary_sum": {
                "left": candidate_semantic_sum,
                "right": semantic_unmatched_candidates,
            },
            "identity_failed_case_sum": {
                "left": len(identity_failed_cases),
                "right": sum(identity_classes.values()),
                "by_class": identity_classes,
            },
        }
        for check in checks.values():
            check["valid"] = check["left"] == check["right"]
        return {
            "classification_version": "v1",
            "exact_unmatched_candidate_count": exact_unmatched_candidates,
            "semantic_unmatched_candidate_count": semantic_unmatched_candidates,
            "exact_unmatched_expected_count": exact_unmatched_expected,
            "semantic_unmatched_expected_count": semantic_unmatched_expected,
            "grouped_support_record_count": self._grouped_support_record_count,
            "counts_by_class": counts_by_class,
            "candidate_counts_by_class": candidate_counts,
            "expected_counts_by_class": expected_counts,
            "counts_by_canonical_field": counts_by_field,
            "case_indexes_by_class": case_indexes,
            "source_signatures_by_class": source_signatures,
            "reconciliation": checks,
            "reconciliation_valid": all(check["valid"] for check in checks.values()),
        }
