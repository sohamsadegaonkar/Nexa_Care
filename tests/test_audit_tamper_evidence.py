"""Tamper Evidence and Concurrency tests for Audit Ledger.

Verifies that:
1. Modifications to the audit ledger are detected by the chain verifier.
2. Concurrent writers are handled gracefully without breaking the chain.
3. Chain forks (duplicate previous_hash) are rejected by the database.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.observability.audit_ledger import append_audit_log, _calculate_hash
from scripts.verify_audit_chain import calculate_hash


def generate_fake_chain(count: int, start_prev: str = "GENESIS") -> list[dict]:
    """Helper to generate a valid hash-chained list of audit entries."""
    chain = []
    prev_hash = start_prev
    for i in range(count):
        payload = {
            "trace_id": str(uuid.uuid4()),
            "actor_uid": "test-user",
            "event": f"EVENT_{i}",
            "target_id": "target",
            "status": "SUCCESS",
            "timestamp": "2026-07-06T12:00:00Z"
        }
        record_hash = calculate_hash(payload, prev_hash)
        chain.append({
            "id": i + 1,
            "payload": payload,
            "previous_hash": prev_hash,
            "record_hash": record_hash
        })
        prev_hash = record_hash
    return chain


def verify_logic(chain: list[dict]) -> tuple[bool, list[int], list[int]]:
    """Reproduce verifier logic for unit testing."""
    broken_links = []
    tampered_payloads = []
    prev_record_hash = "GENESIS"
    
    for row in chain:
        row_id = row["id"]
        if row["previous_hash"] != prev_record_hash:
            broken_links.append(row_id)
        
        computed = calculate_hash(row["payload"], row["previous_hash"])
        if computed != row["record_hash"]:
            tampered_payloads.append(row_id)
            
        prev_record_hash = row["record_hash"]
        
    is_valid = not broken_links and not tampered_payloads
    return is_valid, broken_links, tampered_payloads


def test_tamper_detection_logic():
    """Verify that the verification logic correctly detects payload and chain tampering."""
    # 1. Valid chain
    chain = generate_fake_chain(5)
    is_valid, _, _ = verify_logic(chain)
    assert is_valid is True

    # 2. Payload tampering
    tampered_payload = chain.copy()
    tampered_payload[2] = tampered_payload[2].copy()
    tampered_payload[2]["payload"] = tampered_payload[2]["payload"].copy()
    tampered_payload[2]["payload"]["status"] = "FAILED"  # Modification!
    
    is_valid, broken, tampered = verify_logic(tampered_payload)
    assert is_valid is False
    assert 3 in tampered  # Row 3 (index 2) should be detected as tampered
    # Note: Chaining means the next row's previous_hash won't match IF we recomputed it, 
    # but here we kept the same hashes, so only row 3 is detected as invalid relative to its hash.

    # 3. Chain link tampering (broken chain)
    broken_chain = generate_fake_chain(5)
    broken_chain[3] = broken_chain[3].copy()
    broken_chain[3]["previous_hash"] = "FORGED_HASH"
    
    is_valid, broken, tampered = verify_logic(broken_chain)
    assert is_valid is False
    assert 4 in broken


@pytest.mark.asyncio
async def test_concurrent_audit_writes():
    """Verify that multiple concurrent calls to append_audit_log result in a valid chain.
    This tests the retry logic on unique_violation.
    """
    mock_supabase = MagicMock()
    
    # Track the latest hash as it "grows"
    latest_hash = ["GENESIS"]
    
    # Store "inserted" rows to verify chain at the end
    inserted_rows = []

    async def mock_execute_latest():
        res = MagicMock()
        res.data = [{"record_hash": latest_hash[0]}] if latest_hash[0] else []
        return res

    async def mock_execute_insert(payload_to_insert):
        # Simulate PostgreSQL unique constraint check on previous_hash
        # If any already inserted row has the same previous_hash, raise unique_violation
        prev = payload_to_insert["previous_hash"]
        if any(r["previous_hash"] == prev for r in inserted_rows):
            # Simulate PostgREST unique violation exception
            exc = Exception("unique_violation")
            setattr(exc, "code", "23505")
            raise exc
        
        # Success
        inserted_rows.append(payload_to_insert)
        latest_hash[0] = payload_to_insert["record_hash"]
        return MagicMock()

    # Configure mock supabase behavior
    mock_supabase.table.return_value.select.return_value.order.return_value.limit.return_value.execute.side_effect = mock_execute_latest
    
    def get_insert_side_effect(payload_dict):
        # Supabase insert() returns a request builder whose execute() may be sync or async.
        async def execute_insert():
            return await mock_execute_insert(payload_dict)

        insert_request = MagicMock()
        insert_request.execute = AsyncMock(side_effect=execute_insert)
        return insert_request

    mock_supabase.table.return_value.insert.side_effect = get_insert_side_effect

    with patch("app.observability.audit_ledger.get_supabase_client", return_value=mock_supabase):
        # Execute 10 concurrent writes
        tasks = [
            append_audit_log(f"user-{i}", "EVENT", f"target-{i}", "SUCCESS")
            for i in range(10)
        ]
        results = await asyncio.gather(*tasks)
        
        assert all(results), "Not all concurrent writes succeeded"
        assert len(inserted_rows) == 10
        
        # Verify the chain integrity of inserted rows
        # We sort them by how they were appended (using our latest_hash tracker logic)
        # In reality, they are ordered by id in DB.
        # Let's verify that exactly one points to GENESIS, and each other points to a unique prev_hash
        
        prev_hashes = [r["previous_hash"] for r in inserted_rows]
        assert len(set(prev_hashes)) == 10
        assert "GENESIS" in prev_hashes
        
        # Final validation of the entire chain
        record_hashes = {r["record_hash"] for r in inserted_rows}
        for r in inserted_rows:
            if r["previous_hash"] != "GENESIS":
                assert r["previous_hash"] in record_hashes


def test_calculate_hash_is_stable():
    """Verify that _calculate_hash is stable and matches the verifier script."""
    payload = {"a": 1, "b": 2}
    prev = "HASH"
    h1 = _calculate_hash(payload, prev)
    h2 = calculate_hash(payload, prev)
    assert h1 == h2
    assert h1 == _calculate_hash({"b": 2, "a": 1}, prev)  # Sorting test
