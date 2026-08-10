from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError

import pytest

from app.ai.identity_decision import (
    IdentityDecisionState,
    IdentityFieldStatus,
    decide_identity_state,
)


def test_authoritative_context_and_all_exact_is_confirmed():
    result = decide_identity_state(
        authoritative_context_present=True,
        field_statuses={
            "patient_name": IdentityFieldStatus.EXACT,
            "phone": IdentityFieldStatus.EXACT,
        },
    )

    assert result.state is IdentityDecisionState.IDENTITY_CONFIRMED


def test_no_authoritative_context_is_insufficient_regardless_of_assertions():
    result = decide_identity_state(
        authoritative_context_present=False,
        field_statuses={"patient_name": IdentityFieldStatus.CONFLICTING},
    )

    assert result.state is IdentityDecisionState.IDENTITY_INSUFFICIENT


def test_empty_field_statuses_are_insufficient():
    result = decide_identity_state(
        authoritative_context_present=True,
        field_statuses={},
    )

    assert result.state is IdentityDecisionState.IDENTITY_INSUFFICIENT


def test_missing_field_is_insufficient():
    result = decide_identity_state(
        authoritative_context_present=True,
        field_statuses={"patient_name": IdentityFieldStatus.MISSING},
    )

    assert result.state is IdentityDecisionState.IDENTITY_INSUFFICIENT


def test_nonmatching_field_is_discrepancy():
    result = decide_identity_state(
        authoritative_context_present=True,
        field_statuses={"patient_name": IdentityFieldStatus.NONMATCHING},
    )

    assert result.state is IdentityDecisionState.IDENTITY_DISCREPANCY


def test_exact_and_nonmatching_is_discrepancy():
    result = decide_identity_state(
        authoritative_context_present=True,
        field_statuses={
            "patient_name": IdentityFieldStatus.EXACT,
            "phone": IdentityFieldStatus.NONMATCHING,
        },
    )

    assert result.state is IdentityDecisionState.IDENTITY_DISCREPANCY


def test_conflicting_field_is_conflicting():
    result = decide_identity_state(
        authoritative_context_present=True,
        field_statuses={"patient_name": IdentityFieldStatus.CONFLICTING},
    )

    assert result.state is IdentityDecisionState.IDENTITY_CONFLICTING


def test_conflicting_precedes_nonmatching():
    result = decide_identity_state(
        authoritative_context_present=True,
        field_statuses={
            "patient_name": IdentityFieldStatus.CONFLICTING,
            "phone": IdentityFieldStatus.NONMATCHING,
        },
    )

    assert result.state is IdentityDecisionState.IDENTITY_CONFLICTING


def test_conflicting_precedes_missing():
    result = decide_identity_state(
        authoritative_context_present=True,
        field_statuses={
            "patient_name": IdentityFieldStatus.CONFLICTING,
            "phone": IdentityFieldStatus.MISSING,
        },
    )

    assert result.state is IdentityDecisionState.IDENTITY_CONFLICTING


def test_decision_counts_reconcile_to_evaluated_fields():
    result = decide_identity_state(
        authoritative_context_present=True,
        field_statuses={
            "patient_name": IdentityFieldStatus.EXACT,
            "phone": IdentityFieldStatus.MISSING,
            "aadhaar_abha_id": IdentityFieldStatus.NONMATCHING,
            "provider_patient_id": IdentityFieldStatus.CONFLICTING,
        },
    )

    assert result.evaluated_field_count == 4
    assert result.exact_count == 1
    assert result.missing_count == 1
    assert result.nonmatching_count == 1
    assert result.conflicting_count == 1
    assert result.evaluated_field_count == sum(
        (
            result.exact_count,
            result.missing_count,
            result.nonmatching_count,
            result.conflicting_count,
        )
    )


def test_decision_result_is_immutable():
    result = decide_identity_state(
        authoritative_context_present=True,
        field_statuses={"patient_name": IdentityFieldStatus.EXACT},
    )

    with pytest.raises(FrozenInstanceError):
        result.exact_count = 0  # type: ignore[misc]


def test_api_does_not_accept_or_represent_source_identity_values():
    source_identity_value = "forbidden synthetic identity value"
    result = decide_identity_state(
        authoritative_context_present=True,
        field_statuses={"patient_name": IdentityFieldStatus.EXACT},
    )

    assert source_identity_value not in repr(result)
    assert "patient_name" not in repr(result)
    with pytest.raises(TypeError):
        decide_identity_state(
            authoritative_context_present=True,
            field_statuses={},
            patient_name=source_identity_value,  # type: ignore[call-arg]
        )


def test_unexpected_status_fails_closed_instead_of_coercing_to_exact():
    with pytest.raises(TypeError, match="IdentityFieldStatus"):
        decide_identity_state(
            authoritative_context_present=True,
            field_statuses={"patient_name": "EXACT"},  # type: ignore[dict-item]
        )


def test_similarity_and_heuristic_inputs_are_not_part_of_the_contract():
    parameters = inspect.signature(decide_identity_state).parameters
    assert set(parameters) == {"authoritative_context_present", "field_statuses"}

    with pytest.raises(TypeError):
        decide_identity_state(
            authoritative_context_present=True,
            field_statuses={"patient_name": IdentityFieldStatus.NONMATCHING},
            similarity_score=1.0,  # type: ignore[call-arg]
        )
