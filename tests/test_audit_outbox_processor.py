from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services.audit_outbox_processor import (
    DEFAULT_MAX_ATTEMPTS,
    get_outbox_health,
    process_outbox_batch,
    run_outbox_processor_forever,
)


class _FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def one(self):
        return self._rows[0]


def _row(**overrides):
    base = {
        "id": uuid.uuid4(),
        "event_id": uuid.uuid4(),
        "idempotency_key": "idem-" + uuid.uuid4().hex[:8],
        "chain_partition": "global",
        "event_type": "PATIENT_POLICY_CHANGED",
        "actor_id": "doctor-1",
        "tenant_id": None,
        "patient_id": "patient-1",
        "payload": {"new_policy": "push_biometric"},
        "attempt_count": 0,
    }
    base.update(overrides)
    return base


class _FakeDB:
    def __init__(self, claim_rows):
        self._claim_rows = claim_rows
        self.executed: list[tuple[str, dict]] = []
        self.commits = 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.executed.append((sql, params or {}))
        if "FOR UPDATE SKIP LOCKED" in sql:
            return _FakeResult(self._claim_rows)
        return _FakeResult()

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_empty_queue_is_a_no_op():
    db = _FakeDB([])
    result = await process_outbox_batch(db)
    assert result == {"claimed": 0, "processed": 0, "retried": 0, "dead_lettered": 0}


@pytest.mark.asyncio
async def test_successful_events_are_appended_and_marked_processed():
    rows = [_row(), _row()]
    db = _FakeDB(rows)
    with patch("app.services.audit_outbox_processor.append_audit_log", AsyncMock(return_value=True)) as mock_append:
        result = await process_outbox_batch(db)

    assert result["processed"] == 2
    assert result["retried"] == 0
    assert result["dead_lettered"] == 0
    assert mock_append.await_count == 2
    for call in mock_append.await_args_list:
        assert call.kwargs["chain_partition"] == "global"
        assert call.kwargs["idempotency_key"]

    processed_sql = [sql for sql, _ in db.executed if "status = 'processed'" in sql]
    assert len(processed_sql) == 2


@pytest.mark.asyncio
async def test_failed_event_below_max_attempts_is_retried_with_backoff():
    row = _row(attempt_count=0)
    db = _FakeDB([row])
    with patch("app.services.audit_outbox_processor.append_audit_log", AsyncMock(side_effect=RuntimeError("db down"))):
        result = await process_outbox_batch(db)

    assert result["retried"] == 1
    assert result["dead_lettered"] == 0
    retry_calls = [(sql, params) for sql, params in db.executed if "attempt_count = attempt_count + 1" in sql and "status = 'pending'" in sql]
    assert len(retry_calls) == 1
    _, params = retry_calls[0]
    assert params["available_at"] > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_failed_event_at_max_attempts_is_dead_lettered():
    row = _row(attempt_count=DEFAULT_MAX_ATTEMPTS - 1)
    db = _FakeDB([row])
    with patch("app.services.audit_outbox_processor.append_audit_log", AsyncMock(side_effect=RuntimeError("permanent"))):
        result = await process_outbox_batch(db)

    assert result["dead_lettered"] == 1
    assert result["retried"] == 0
    dead_letter_calls = [sql for sql, _ in db.executed if "dead_letter" in sql]
    assert len(dead_letter_calls) == 1


@pytest.mark.asyncio
async def test_one_bad_event_does_not_stop_the_batch():
    good_row = _row()
    bad_row = _row(attempt_count=DEFAULT_MAX_ATTEMPTS)

    async def append_side_effect(**kwargs):
        if kwargs["idempotency_key"] == bad_row["idempotency_key"]:
            raise RuntimeError("boom")
        return True

    db = _FakeDB([bad_row, good_row])
    with patch("app.services.audit_outbox_processor.append_audit_log", AsyncMock(side_effect=append_side_effect)):
        result = await process_outbox_batch(db)

    assert result["processed"] == 1
    assert result["dead_lettered"] == 1


@pytest.mark.asyncio
async def test_claim_uses_skip_locked_for_multi_instance_safety():
    db = _FakeDB([])
    await process_outbox_batch(db)
    claim_sql = [sql for sql, _ in db.executed if "SELECT" in sql][0]
    assert "FOR UPDATE SKIP LOCKED" in claim_sql


@pytest.mark.asyncio
async def test_health_reports_only_aggregate_backlog_counts():
    db = _FakeDB([])
    db._claim_rows = [{"dead_letter_backlog": 2, "stalled_pending_events": 3}]

    # The health query is not the claim query, so provide its aggregate result.
    async def execute(statement, params=None):
        db.executed.append((str(statement), params or {}))
        return _FakeResult(db._claim_rows)

    db.execute = execute
    assert await get_outbox_health(db) == {
        "dead_letter_backlog": 2,
        "stalled_pending_events": 3,
    }


@pytest.mark.asyncio
async def test_forever_worker_survives_batch_failure_and_stops_on_signal():
    shutdown = asyncio.Event()

    class SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return None

    attempts = 0

    async def batch(_db):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient")
        shutdown.set()

    with patch("app.services.audit_outbox_processor.process_outbox_batch", side_effect=batch):
        await run_outbox_processor_forever(
            lambda: SessionContext(), poll_interval_seconds=0, shutdown_event=shutdown,
        )

    assert attempts == 2
