"""Pure integrity checks for the canonical audit payload/hash format."""

from __future__ import annotations

from app.observability.audit_ledger import _calculate_hash


def _chain(count: int) -> list[dict]:
    rows = []
    previous_hash = "GENESIS"
    for index in range(count):
        payload = {
            "trace_id": f"trace-{index}",
            "actor_uid": "test-user",
            "event": f"EVENT_{index}",
            "target_id": "target",
            "status": "SUCCESS",
            "timestamp": "2026-07-13T12:00:00+00:00",
        }
        record_hash = _calculate_hash(payload, previous_hash)
        rows.append(
            {
                "payload": payload,
                "previous_hash": previous_hash,
                "record_hash": record_hash,
            }
        )
        previous_hash = record_hash
    return rows


def _verify(rows: list[dict]) -> bool:
    expected_previous = "GENESIS"
    for row in rows:
        if row["previous_hash"] != expected_previous:
            return False
        if row["record_hash"] != _calculate_hash(row["payload"], row["previous_hash"]):
            return False
        expected_previous = row["record_hash"]
    return True


def test_valid_chain_verifies():
    assert _verify(_chain(5))


def test_payload_tampering_is_detected():
    rows = _chain(5)
    rows[2]["payload"]["status"] = "TAMPERED"
    assert not _verify(rows)


def test_link_tampering_is_detected():
    rows = _chain(5)
    rows[3]["previous_hash"] = "0" * 64
    assert not _verify(rows)
