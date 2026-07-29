"""Structural guardrails for the canonical adversarial scenario catalog."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from tests.ai_extraction.adversarial.scenario_catalog import (
    RUNTIME_AUTO_COMMIT_APPROVED,
    RUNTIME_AUTO_COMMIT_ENABLED,
    SCENARIOS,
    SCENARIOS_BY_ID,
    EvidenceGroup,
)

ROOT = Path(__file__).resolve().parents[3]
STABLE_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def test_catalog_contains_exactly_scenarios_1_through_24():
    ids = [scenario.scenario_id for scenario in SCENARIOS]
    assert len(SCENARIOS) == 24
    assert ids == list(range(1, 25))
    assert len(ids) == len(set(ids))
    assert set(SCENARIOS_BY_ID) == set(range(1, 25))


def test_slugs_are_unique_and_stable_looking():
    slugs = [scenario.slug for scenario in SCENARIOS]
    assert len(slugs) == len(set(slugs))
    assert all(STABLE_SLUG.fullmatch(slug) for slug in slugs)


def test_every_scenario_uses_only_typed_canonical_groups():
    canonical_groups = set(EvidenceGroup)
    assert len(canonical_groups) == 6
    for scenario in SCENARIOS:
        assert scenario.evidence_groups
        assert scenario.evidence_groups <= canonical_groups
        assert all(
            isinstance(group, EvidenceGroup) for group in scenario.evidence_groups
        )


def test_every_canonical_group_has_at_least_three_scenarios():
    counts = Counter(
        group for scenario in SCENARIOS for group in scenario.evidence_groups
    )
    assert set(counts) == set(EvidenceGroup)
    assert all(counts[group] >= 3 for group in EvidenceGroup)


def test_required_cross_group_scenarios_are_pinned():
    assert {
        EvidenceGroup.LIFECYCLE,
        EvidenceGroup.POLICY_EVIDENCE,
    } <= SCENARIOS_BY_ID[17].evidence_groups
    assert {
        EvidenceGroup.IDENTITY,
        EvidenceGroup.LIFECYCLE,
    } <= SCENARIOS_BY_ID[19].evidence_groups
    assert {
        EvidenceGroup.POLICY_EVIDENCE,
        EvidenceGroup.LIFECYCLE,
    } <= SCENARIOS_BY_ID[24].evidence_groups


def test_runtime_coverage_is_explicit_and_references_existing_local_tests():
    runtime_scenarios = [scenario for scenario in SCENARIOS if scenario.runtime_tested]
    assert [scenario.scenario_id for scenario in runtime_scenarios] == [
        1,
        2,
        3,
        4,
        5,
        8,
        12,
        13,
        16,
        17,
        18,
        20,
        21,
        22,
        24,
    ]
    assert len(runtime_scenarios) < len(SCENARIOS)
    for scenario in runtime_scenarios:
        assert scenario.test_reference
        test_file, separator, test_name = scenario.test_reference.partition("::")
        assert separator == "::"
        assert test_name.startswith("test_")
        assert (ROOT / test_file).is_file()
    assert SCENARIOS_BY_ID[17].test_reference == (
        "tests/ai_extraction/adversarial/test_lifecycle.py::"
        "test_scenario_17_outbox_failure_rolls_back_clinical_commit_and_retry_is_safe"
    )


def test_unimplemented_scenarios_do_not_claim_test_references():
    for scenario in SCENARIOS:
        if scenario.runtime_tested:
            assert scenario.test_reference
        else:
            assert scenario.test_reference is None


def test_every_scenario_defines_a_concrete_failure_and_safe_behavior():
    for scenario in SCENARIOS:
        assert scenario.title.strip()
        assert scenario.failure_condition.strip()
        assert scenario.required_behavior.strip()


def test_auto_commit_is_neither_enabled_nor_approved():
    assert RUNTIME_AUTO_COMMIT_ENABLED is False
    assert RUNTIME_AUTO_COMMIT_APPROVED is False
