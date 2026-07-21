from __future__ import annotations


import pytest

from scripts.verify_audit_partitions import _calculate_hash, verify_partition


class _MappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class FakeConnection:
    def __init__(self, events: list[dict], head: dict | None):
        self.events = events
        self.head = head
        self.marked_unhealthy: list[str] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if "audit_chain_heads" in sql and "UPDATE" in sql:
            self.marked_unhealthy.append(params["chain_partition"])
            return _MappingsResult([])
        if "audit_chain_heads" in sql:
            return _MappingsResult([self.head] if self.head else [])
        return _MappingsResult(self.events)


def _row(audit_id, previous_hash, payload, sequence_number):
    record_hash = _calculate_hash(payload, previous_hash)
    return {
        "audit_id": audit_id, "previous_hash": previous_hash,
        "record_hash": record_hash, "details": payload,
        "sequence_number": sequence_number,
    }


def _build_healthy_chain(n: int) -> list[dict]:
    rows = []
    prev = "GENESIS"
    for i in range(1, n + 1):
        row = _row(i, prev, {"event": f"E{i}"}, i)
        rows.append(row)
        prev = row["record_hash"]
    return rows


@pytest.mark.asyncio
async def test_healthy_chain_passes():
    rows = _build_healthy_chain(5)
    head = {
        "head_event_id": rows[-1]["audit_id"], "head_hash": rows[-1]["record_hash"],
        "sequence_number": 5, "is_healthy": True,
    }
    conn = FakeConnection(rows, head)
    result = await verify_partition(conn, "global", dry_run=False)
    assert result is None
    assert conn.marked_unhealthy == []


@pytest.mark.asyncio
async def test_empty_partition_with_no_head_is_valid():
    conn = FakeConnection([], None)
    result = await verify_partition(conn, "global", dry_run=False)
    assert result is None


@pytest.mark.asyncio
async def test_multiple_genesis_events_fails_and_marks_unhealthy():
    rows = [
        _row(1, "GENESIS", {"event": "A"}, 1),
        _row(2, "GENESIS", {"event": "B"}, 1),
    ]
    conn = FakeConnection(rows, None)
    result = await verify_partition(conn, "global", dry_run=False)
    assert result is not None
    assert "genesis" in result.reason
    assert conn.marked_unhealthy == ["global"]


@pytest.mark.asyncio
async def test_fork_multiple_successors_fails():
    rows = _build_healthy_chain(2)  # GENESIS -> row1 -> row2
    fork = _row(3, rows[0]["record_hash"], {"event": "FORK"}, 2)  # also child of row1
    conn = FakeConnection([rows[0], rows[1], fork], None)
    result = await verify_partition(conn, "global", dry_run=False)
    assert result is not None
    assert "successor" in result.reason or "fork" in result.reason.lower()


@pytest.mark.asyncio
async def test_tampered_payload_fails_hash_recalculation():
    rows = _build_healthy_chain(3)
    rows[1]["details"] = {"event": "TAMPERED"}  # payload changed but record_hash was not recalculated
    head = {
        "head_event_id": rows[-1]["audit_id"], "head_hash": rows[-1]["record_hash"],
        "sequence_number": 3, "is_healthy": True,
    }
    conn = FakeConnection(rows, head)
    result = await verify_partition(conn, "global", dry_run=False)
    assert result is not None
    assert "record_hash mismatch" in result.reason


@pytest.mark.asyncio
async def test_sequence_discontinuity_fails():
    rows = _build_healthy_chain(3)
    rows[2]["sequence_number"] = 99
    head = {
        "head_event_id": rows[-1]["audit_id"], "head_hash": rows[-1]["record_hash"],
        "sequence_number": 3, "is_healthy": True,
    }
    conn = FakeConnection(rows, head)
    result = await verify_partition(conn, "global", dry_run=False)
    assert result is not None
    assert "sequence discontinuity" in result.reason


@pytest.mark.asyncio
async def test_head_hash_mismatch_fails():
    rows = _build_healthy_chain(3)
    head = {
        "head_event_id": rows[-1]["audit_id"], "head_hash": "wrong" * 12 + "0000",
        "sequence_number": 3, "is_healthy": True,
    }
    conn = FakeConnection(rows, head)
    result = await verify_partition(conn, "global", dry_run=False)
    assert result is not None
    assert "head_hash mismatch" in result.reason


@pytest.mark.asyncio
async def test_dry_run_reports_but_does_not_mark_unhealthy():
    rows = [
        _row(1, "GENESIS", {"event": "A"}, 1),
        _row(2, "GENESIS", {"event": "B"}, 1),
    ]
    conn = FakeConnection(rows, None)
    result = await verify_partition(conn, "global", dry_run=True)
    assert result is not None
    assert conn.marked_unhealthy == []  # dry-run must not write
