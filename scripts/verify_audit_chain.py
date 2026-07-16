#!/usr/bin/env python3
"""Audit Ledger Integrity Verifier for Nexa Care.

This script scans the canonical ``public.audit_ledger`` table and verifies the hash chain.
Each record's hash must be SHA-256(payload + previous_hash).
The 'previous_hash' of record N must match the 'record_hash' of record N-1.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Add app to path to import config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import get_database_config

# Colors for output
GREEN = "\033[92m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"


def calculate_hash(payload: dict[str, Any], previous_hash: str) -> str:
    """Recompute the record hash exactly as the app does."""
    minified_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    combined_buffer = minified_payload + previous_hash
    return hashlib.sha256(combined_buffer.encode("utf-8")).hexdigest()


async def verify_audit_chain():
    config = get_database_config()
    engine = create_async_engine(config.url)
    
    print(f"{BOLD}Connecting to database...{RESET}")
    
    total_entries = 0
    broken_links = []
    tampered_payloads = []
    duplicate_previous_hashes = []
    
    try:
        async with engine.connect() as conn:
            stmt = text(
                "SELECT audit_id, details, previous_hash, record_hash "
                "FROM public.audit_ledger"
            )
            result = await conn.execute(stmt)
            rows = [dict(row) for row in result.mappings().all()]
            total_entries = len(rows)

            successors = {}
            for row in rows:
                previous_hash = row["previous_hash"]
                if previous_hash in successors:
                    duplicate_previous_hashes.append(
                        {"id": row["audit_id"], "hash": previous_hash}
                    )
                else:
                    successors[previous_hash] = row

                computed_record_hash = calculate_hash(row["details"], previous_hash)
                if computed_record_hash != row["record_hash"]:
                    tampered_payloads.append({
                        "id": row["audit_id"],
                        "expected_hash": computed_record_hash,
                        "actual_hash": row["record_hash"]
                    })

            visited = set()
            expected_previous = "GENESIS"
            while expected_previous in successors:
                row = successors[expected_previous]
                row_id = row["audit_id"]
                if row_id in visited:
                    broken_links.append(
                        {"id": row_id, "expected_prev": "unvisited", "actual_prev": expected_previous}
                    )
                    break
                visited.add(row_id)
                expected_previous = row["record_hash"]

            for row in rows:
                if row["audit_id"] not in visited:
                    broken_links.append(
                        {
                            "id": row["audit_id"],
                            "expected_prev": "reachable from GENESIS",
                            "actual_prev": row["previous_hash"],
                        }
                    )

        # Report Results
        print("\n" + "=" * 50)
        print(f"{BOLD}Audit Chain Verification Report{RESET}")
        print("=" * 50)
        print(f"Total Entries Scanned: {total_entries}")
        
        integrity_pass = True
        
        if broken_links:
            integrity_pass = False
            print(f"\n{RED}{BOLD}[FAIL] Broken Links Detected: {len(broken_links)}{RESET}")
            for link in broken_links:
                print(f"  Row ID {link['id']}: Expected prev_hash '{link['expected_prev'][:8]}...', "
                      f"got '{link['actual_prev'][:8]}...'")
        
        if tampered_payloads:
            integrity_pass = False
            print(f"\n{RED}{BOLD}[FAIL] Tampered Records Detected: {len(tampered_payloads)}{RESET}")
            for t in tampered_payloads:
                print(f"  Row ID {t['id']}: Stored record_hash '{t['actual_hash'][:8]}...' "
                      f"does not match computed hash '{t['expected_hash'][:8]}...'")

        if duplicate_previous_hashes:
            integrity_pass = False
            print(f"\n{RED}{BOLD}[FAIL] Chain Forks Detected: {len(duplicate_previous_hashes)}{RESET}")
            for d in duplicate_previous_hashes:
                print(f"  Row ID {d['id']}: Multiple rows pointing to previous_hash '{d['hash'][:8]}...'")

        if integrity_pass:
            print(f"\n{GREEN}{BOLD}[PASS] Chain Integrity Verified successfully.{RESET}")
        else:
            print(f"\n{RED}{BOLD}[FAIL] Chain Integrity Compromised.{RESET}")
            sys.exit(1)

    except Exception as e:
        print(f"{RED}Error during verification: {e}{RESET}")
        sys.exit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(verify_audit_chain())
