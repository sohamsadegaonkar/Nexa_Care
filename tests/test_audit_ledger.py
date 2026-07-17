from __future__ import annotations

import asyncio
import datetime
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.core.request_context import trace_id_var
from app.observability.audit_ledger import (
    _calculate_hash,
    append_audit_log,
    append_audit_log_or_503,
)


class ScalarResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class MappingResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self.rows)


class AuditStore:
    def __init__(self):
        self.rows: list[dict] = []
        self.lock = asyncio.Lock()


class Connection:
    def __init__(self, store: AuditStore):
        self.store = store

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "pg_advisory_xact_lock" in sql:
            return ScalarResult()
        if "idempotency_key = :idempotency_key" in sql:
            match = next(
                (row["record_hash"] for row in self.store.rows
                 if row.get("idempotency_key") == params["idempotency_key"]),
                None,
            )
            return ScalarResult(match)
        if "SELECT audit_id, previous_hash, record_hash" in sql:
            return MappingResult(self.store.rows)
        if "INSERT INTO public.audit_ledger" in sql:
            if any(row["previous_hash"] == params["previous_hash"] for row in self.store.rows):
                error = RuntimeError("23505 duplicate previous_hash")
                error.code = "23505"
                raise error
            row = dict(params)
            row["audit_id"] = len(self.store.rows) + 1
            row["payload"] = json.loads(row["details"])
            row["details"] = row["payload"]
            self.store.rows.append(row)
            return ScalarResult()
        raise AssertionError(sql)


class Transaction:
    def __init__(self, store: AuditStore):
        self.store = store

    async def __aenter__(self):
        await self.store.lock.acquire()
        return Connection(self.store)

    async def __aexit__(self, exc_type, exc, traceback):
        self.store.lock.release()
        return False


class Engine:
    def __init__(self, store: AuditStore):
        self.store = store

    def begin(self):
        return Transaction(self.store)


def test_calculate_hash_is_deterministic_and_chained():
    payload = {"status": "OK", "event": "X"}
    assert _calculate_hash(payload, "GENESIS") == _calculate_hash(payload, "GENESIS")
    assert _calculate_hash(payload, "GENESIS") != _calculate_hash(payload, "other")
    assert len(_calculate_hash(payload, "GENESIS")) == 64


@pytest.mark.asyncio
async def test_first_and_second_events_form_genesis_chain():
    store = AuditStore()
    token = trace_id_var.set("trace-test")
    try:
        with patch("app.observability.audit_ledger.get_async_engine", return_value=Engine(store)):
            assert await append_audit_log("actor-1", "FIRST", "target", "SUCCESS")
            assert await append_audit_log("actor-2", "SECOND", "target", "SUCCESS")
    finally:
        trace_id_var.reset(token)

    assert store.rows[0]["previous_hash"] == "GENESIS"
    assert isinstance(store.rows[0]["event_timestamp"], datetime.datetime)
    assert store.rows[1]["previous_hash"] == store.rows[0]["record_hash"]
    for row in store.rows:
        assert row["record_hash"] == _calculate_hash(row["payload"], row["previous_hash"])


@pytest.mark.asyncio
async def test_concurrent_writers_do_not_fork_chain():
    store = AuditStore()
    with patch("app.observability.audit_ledger.get_async_engine", return_value=Engine(store)):
        results = await asyncio.gather(
            *(append_audit_log(f"actor-{i}", "EVENT", f"target-{i}", "SUCCESS") for i in range(10))
        )

    assert all(results)
    assert len(store.rows) == 10
    assert len({row["previous_hash"] for row in store.rows}) == 10
    assert store.rows[0]["previous_hash"] == "GENESIS"
    for previous, current in zip(store.rows, store.rows[1:]):
        assert current["previous_hash"] == previous["record_hash"]


def _ledger_row(payload: dict, previous_hash: str, audit_id: int) -> dict:
    return {
        "audit_id": audit_id,
        "previous_hash": previous_hash,
        "record_hash": _calculate_hash(payload, previous_hash),
        "details": payload,
        "protocol_version": payload.get("protocol_version", 2),
        "idempotency_key": None,
    }


@pytest.mark.asyncio
async def test_live_append_rejects_two_tip_fork_and_emits_metric():
    root_payload = {"protocol_version": 2, "event": "ROOT"}
    root = _ledger_row(root_payload, "GENESIS", 1)
    left = _ledger_row({"protocol_version": 2, "event": "LEFT"}, root["record_hash"], 2)
    right = _ledger_row({"protocol_version": 2, "event": "RIGHT"}, root["record_hash"], 3)
    store = AuditStore()
    store.rows.extend([root, left, right])
    with patch("app.observability.audit_ledger.get_async_engine", return_value=Engine(store)), patch(
        "app.observability.audit_ledger.AUDIT_LEDGER_INTEGRITY_FAILURES"
    ) as metric:
        assert await append_audit_log("actor", "AFTER_FORK", "target", "SUCCESS") is False
    metric.labels.assert_called_once()
    assert len(store.rows) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["missing_predecessor", "altered_payload"])
async def test_live_append_rejects_invalid_existing_chain(mutation):
    payload = {"protocol_version": 2, "event": "ROOT"}
    row = _ledger_row(payload, "GENESIS", 1)
    if mutation == "missing_predecessor":
        row["previous_hash"] = "absent"
    else:
        row["details"] = {**payload, "event": "ALTERED"}
    store = AuditStore()
    store.rows.append(row)
    with patch("app.observability.audit_ledger.get_async_engine", return_value=Engine(store)):
        assert await append_audit_log("actor", "EVENT", "target", "SUCCESS") is False


@pytest.mark.asyncio
async def test_global_scope_orders_tenant_events_without_independent_heads():
    store = AuditStore()
    with patch("app.observability.audit_ledger.get_async_engine", return_value=Engine(store)):
        assert await append_audit_log("actor-a", "READ", "target-a", "SUCCESS", metadata={"hospital_id": "tenant-a"})
        assert await append_audit_log("actor-b", "READ", "target-b", "SUCCESS", metadata={"hospital_id": "tenant-b"})
    assert store.rows[1]["previous_hash"] == store.rows[0]["record_hash"]
    assert {row["chain_scope"] for row in store.rows} == {"global"}


@pytest.mark.asyncio
async def test_explicit_idempotency_key_does_not_duplicate_event():
    store = AuditStore()
    with patch("app.observability.audit_ledger.get_async_engine", return_value=Engine(store)):
        assert await append_audit_log(
            "actor", "EVENT", "target", "SUCCESS", idempotency_key="request-123"
        )
        assert await append_audit_log(
            "actor", "EVENT", "target", "SUCCESS", idempotency_key="request-123"
        )
    assert len(store.rows) == 1


@pytest.mark.asyncio
async def test_unique_previous_hash_conflict_retries_with_same_payload_identity():
    error = RuntimeError("23505 duplicate previous_hash")
    error.code = "23505"
    successful_row = {"record_hash": "ok"}
    append_once = AsyncMock(side_effect=[error, successful_row])

    with patch("app.observability.audit_ledger._append_once", append_once):
        assert await append_audit_log(
            "actor",
            "EVENT",
            "target",
            "SUCCESS",
            event_timestamp="2026-07-13T12:00:00+00:00",
        )

    assert append_once.await_count == 2
    first = append_once.await_args_list[0].kwargs
    second = append_once.await_args_list[1].kwargs
    assert first["timestamp"] == second["timestamp"]
    assert first["trace_id"] == second["trace_id"]


@pytest.mark.asyncio
async def test_database_failure_fails_closed():
    with patch(
        "app.observability.audit_ledger._append_once",
        new=AsyncMock(side_effect=ConnectionError("database unavailable")),
    ):
        assert await append_audit_log("actor", "EVENT", "target", "SUCCESS") is False


@pytest.mark.asyncio
async def test_append_or_503_aborts_when_audit_fails():
    with patch(
        "app.observability.audit_ledger.append_audit_log",
        new=AsyncMock(return_value=False),
    ):
        with pytest.raises(HTTPException) as exc:
            await append_audit_log_or_503("actor", "EVENT", "target", "SUCCESS")
    assert exc.value.status_code == 503
