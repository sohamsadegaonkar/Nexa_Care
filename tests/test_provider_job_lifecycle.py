"""Pure A2 lifecycle and processor safety contracts."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from app.services.provider_job_lifecycle import (
    LEGAL_PROVIDER_JOB_TRANSITIONS,
    TERMINAL_PROVIDER_JOB_STATES,
    ProviderJobLifecycleError,
    ProviderJobStatus,
    ProviderReconciliationOutcome,
    ReconciliationOutcomeType,
)
from app.services.provider_job_reconciliation_processor import (
    run_provider_job_reconciliation_processor_forever,
)


def test_transition_map_is_central_closed_and_has_no_a2_supersession():
    assert LEGAL_PROVIDER_JOB_TRANSITIONS[ProviderJobStatus.CREATED.value] == {
        ProviderJobStatus.SUBMITTING.value,
        ProviderJobStatus.FAILED_TERMINAL.value,
    }
    assert LEGAL_PROVIDER_JOB_TRANSITIONS[
        ProviderJobStatus.LOCAL_WAIT_EXPIRED.value
    ] == {
        ProviderJobStatus.RECONCILING.value,
        ProviderJobStatus.PROVIDER_UNREACHABLE_MANUAL_REVIEW.value,
    }
    assert (
        LEGAL_PROVIDER_JOB_TRANSITIONS[ProviderJobStatus.COMPLETE.value] == frozenset()
    )
    assert (
        LEGAL_PROVIDER_JOB_TRANSITIONS[ProviderJobStatus.SUPERSEDED.value]
        == frozenset()
    )
    assert ProviderJobStatus.SUPERSEDED.value not in {
        target
        for targets in LEGAL_PROVIDER_JOB_TRANSITIONS.values()
        for target in targets
    }
    assert TERMINAL_PROVIDER_JOB_STATES == {
        ProviderJobStatus.COMPLETE.value,
        ProviderJobStatus.FAILED_RETRYABLE.value,
        ProviderJobStatus.FAILED_TERMINAL.value,
        ProviderJobStatus.PROVIDER_UNREACHABLE_MANUAL_REVIEW.value,
        ProviderJobStatus.SUPERSEDED.value,
    }


def test_reconciliation_outcome_is_provider_independent_and_value_free():
    outcome = ProviderReconciliationOutcome(
        ReconciliationOutcomeType.SUBMITTED, provider_job_id="provider-job-1"
    )
    assert outcome.outcome is ReconciliationOutcomeType.SUBMITTED
    with pytest.raises(ProviderJobLifecycleError) as invalid:
        ProviderReconciliationOutcome(
            ReconciliationOutcomeType.SUCCEEDED, provider_job_id="unexpected"
        )
    assert invalid.value.code == "ASYNC_PROVIDER_OUTCOME_METADATA_INVALID"


def test_lifecycle_error_exposes_only_stable_code():
    error = ProviderJobLifecycleError("ASYNC_PROVIDER_VERSION_CONFLICT")
    assert error.code == "ASYNC_PROVIDER_VERSION_CONFLICT"
    assert str(error) == error.code


def test_provider_job_id_conflict_is_value_free():
    error = ProviderJobLifecycleError("ASYNC_PROVIDER_JOB_ID_CONFLICT")
    assert error.code == "ASYNC_PROVIDER_JOB_ID_CONFLICT"
    assert "JOB_A" not in str(error)
    assert "JOB_B" not in str(error)


@pytest.mark.asyncio
async def test_processor_callback_failure_is_isolated_and_does_not_log_payload(
    monkeypatch, caplog
):
    shutdown = asyncio.Event()
    claim = object()
    outcome = ProviderReconciliationOutcome(ReconciliationOutcomeType.UNREACHABLE)
    claimed = False
    applied = []

    async def claim_rows(*args, **kwargs):
        nonlocal claimed
        if claimed:
            shutdown.set()
            return []
        claimed = True
        return [claim]

    async def apply_row(*args, **kwargs):
        applied.append(kwargs["outcome"])

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def commit(self):
            return None

    def factory():
        return Session()

    callback = AsyncMock(side_effect=RuntimeError("raw provider payload must not log"))
    monkeypatch.setattr(
        "app.services.provider_job_reconciliation_processor.claim_due_provider_reconciliations",
        claim_rows,
    )
    monkeypatch.setattr(
        "app.services.provider_job_reconciliation_processor.apply_reconciliation_outcome",
        apply_row,
    )
    # The fake claim only needs to survive logging fields; use a safe object
    # with the approved attributes rather than real identifiers.
    safe_claim = type(
        "Claim",
        (),
        {
            "provider_attempt_id": "attempt",
            "job_id": "job",
            "claimed_version": 2,
            "reconciliation_attempt_number": 1,
        },
    )()
    monkeypatch.setattr(
        "app.services.provider_job_reconciliation_processor.claim_due_provider_reconciliations",
        lambda *args, **kwargs: [safe_claim] if not claimed else [],
    )

    async def claim_once(*args, **kwargs):
        nonlocal claimed
        if claimed:
            shutdown.set()
            return []
        claimed = True
        return [safe_claim]

    monkeypatch.setattr(
        "app.services.provider_job_reconciliation_processor.claim_due_provider_reconciliations",
        claim_once,
    )
    with caplog.at_level(logging.WARNING):
        await run_provider_job_reconciliation_processor_forever(
            factory,
            reconcile_callback=callback,
            poll_interval_seconds=0.001,
            shutdown_event=shutdown,
        )
    assert applied == [outcome]
    assert "raw provider payload must not log" not in caplog.text
