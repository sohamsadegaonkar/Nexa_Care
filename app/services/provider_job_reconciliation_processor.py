"""Provider-independent bounded reconciliation worker for Scenario 6 A2."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable

from app.observability.safe_exceptions import log_safe_exception
from app.services.provider_job_lifecycle import (
    ProviderJobLifecycleError,
    ProviderReconciliationClaim,
    ProviderReconciliationOutcome,
    apply_reconciliation_outcome,
    claim_due_provider_reconciliations,
)

logger = logging.getLogger("nexa_logger")


async def _invoke_callback(
    reconcile_callback: Callable[
        [ProviderReconciliationClaim],
        ProviderReconciliationOutcome | Awaitable[ProviderReconciliationOutcome],
    ],
    claim: ProviderReconciliationClaim,
) -> ProviderReconciliationOutcome:
    result = reconcile_callback(claim)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, ProviderReconciliationOutcome):
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_CALLBACK_OUTCOME_INVALID")
    return result


async def run_provider_job_reconciliation_processor_forever(
    session_factory,
    *,
    reconcile_callback: Callable[
        [ProviderReconciliationClaim],
        ProviderReconciliationOutcome | Awaitable[ProviderReconciliationOutcome],
    ],
    max_attempts: int = 3,
    window_seconds: int = 900,
    poll_interval_seconds: float = 2.0,
    batch_size: int = 25,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Claim in a short transaction, call outside it, then apply safely.

    The injected callback is intentionally the only provider seam.  This module
    has no AWS, Textract, storage, candidate, or clinical dependency.
    """

    if not 1 <= max_attempts <= 5:
        raise ValueError("max_attempts must be between 1 and 5")
    if not 60 <= window_seconds <= 86400:
        raise ValueError("window_seconds must be between 60 and 86400")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive")
    if not 1 <= batch_size <= 100:
        raise ValueError("batch_size must be between 1 and 100")
    stop = shutdown_event or asyncio.Event()
    while not stop.is_set():
        claims: list[ProviderReconciliationClaim] = []
        try:
            async with session_factory() as db:
                claims = await claim_due_provider_reconciliations(
                    db,
                    max_attempts=max_attempts,
                    window_seconds=window_seconds,
                    interval_seconds=max(1, min(60, int(poll_interval_seconds))),
                    batch_size=batch_size,
                )
                await db.commit()
        except Exception as exc:  # noqa: BLE001 - worker survives bad cycles
            log_safe_exception(
                logger,
                logging.ERROR,
                "provider_job_reconciliation_claim_failed",
                exc,
                subsystem="provider_reconciliation",
                operation="claim_due_provider_reconciliations",
            )
        for claim in claims:
            if stop.is_set():
                break
            try:
                try:
                    outcome = await _invoke_callback(reconcile_callback, claim)
                except Exception as exc:  # noqa: BLE001 - provider visibility loss
                    log_safe_exception(
                        logger,
                        logging.WARNING,
                        "provider_job_reconciliation_callback_unreachable",
                        exc,
                        subsystem="provider_reconciliation",
                        operation="reconcile_callback",
                        fields={
                            "provider_attempt_id": str(claim.provider_attempt_id),
                            "job_id": str(claim.job_id),
                            "claimed_version": claim.claimed_version,
                            "reconciliation_attempt_number": claim.reconciliation_attempt_number,
                        },
                    )
                    outcome = ProviderReconciliationOutcome(outcome="UNREACHABLE")
                async with session_factory() as db:
                    await apply_reconciliation_outcome(
                        db,
                        claim=claim,
                        outcome=outcome,
                        max_attempts=max_attempts,
                        interval_seconds=max(1, min(60, int(poll_interval_seconds))),
                    )
                    await db.commit()
            except ProviderJobLifecycleError as exc:
                log_safe_exception(
                    logger,
                    logging.WARNING,
                    "provider_job_reconciliation_claim_not_applied",
                    exc,
                    subsystem="provider_reconciliation",
                    operation="apply_reconciliation_outcome",
                    fields={
                        "provider_attempt_id": str(claim.provider_attempt_id),
                        "job_id": str(claim.job_id),
                        "claimed_version": claim.claimed_version,
                        "reconciliation_attempt_number": claim.reconciliation_attempt_number,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - isolate one claim
                log_safe_exception(
                    logger,
                    logging.ERROR,
                    "provider_job_reconciliation_apply_failed",
                    exc,
                    subsystem="provider_reconciliation",
                    operation="apply_reconciliation_outcome",
                    fields={
                        "provider_attempt_id": str(claim.provider_attempt_id),
                        "job_id": str(claim.job_id),
                        "claimed_version": claim.claimed_version,
                    },
                )
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_interval_seconds)
        except asyncio.TimeoutError:
            pass
