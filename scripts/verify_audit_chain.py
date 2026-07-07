#!/usr/bin/env python3
"""Audit Ledger Integrity Verifier for Nexa Care.

This script scans the 'system_audit' table and verifies the hash chain.
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
YELLOW = "\033[93m"
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
    
    previous_record_hash = "GENESIS"
    seen_previous_hashes = set()

    try:
        async with engine.connect() as conn:
            # We order by id to follow the append sequence
            stmt = text("SELECT id, payload, previous_hash, record_hash FROM system_audit ORDER BY id ASC")
            result = await conn.execute(stmt)
            
            for row in result:
                total_entries += 1
                row_id = row[0]
                payload = row[1]
                stored_prev_hash = row[2]
                stored_record_hash = row[3]
                
                # 1. Verify previous_hash link
                if stored_prev_hash != previous_record_hash:
                    broken_links.append({
                        "id": row_id,
                        "expected_prev": previous_record_hash,
                        "actual_prev": stored_prev_hash
                    })
                
                # 2. Verify record_hash calculation
                computed_record_hash = calculate_hash(payload, stored_prev_hash)
                if computed_record_hash != stored_record_hash:
                    tampered_payloads.append({
                        "id": row_id,
                        "expected_hash": computed_record_hash,
                        "actual_hash": stored_record_hash
                    })
                
                # 3. Check for duplicates (forks)
                if stored_prev_hash in seen_previous_hashes and stored_prev_hash != "GENESIS":
                    duplicate_previous_hashes.append({
                        "id": row_id,
                        "hash": stored_prev_hash
                    })
                seen_previous_hashes.add(stored_prev_hash)
                
                previous_record_hash = stored_record_hash

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
            # Not necessarily an integrity failure if hashes match, but indicates a chain fork (race condition)
            print(f"\n{YELLOW}{BOLD}[WARN] Chain Forks Detected: {len(duplicate_previous_hashes)}{RESET}")
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
