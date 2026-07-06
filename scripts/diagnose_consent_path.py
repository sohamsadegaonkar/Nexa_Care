import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import redis.asyncio as redis_async
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Add app to path to import models and config if needed
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import get_database_config, get_redis_config

# Colors
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
RESET = "\033[0m"
BOLD = "\033[1m"

CONSENT_TOKEN_PREFIX = "nexa:consent:"


def _redact_token(token: str) -> str:
    if len(token) <= 12:
        return "****"
    return f"{token[:8]}...{token[-4:]}"


def _token_hash(token: str) -> str:
    clean = token[len(CONSENT_TOKEN_PREFIX):] if token.startswith(CONSENT_TOKEN_PREFIX) else token
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


async def get_redis():
    try:
        cfg = get_redis_config()
        client = redis_async.from_url(cfg.url, decode_responses=True)
        await client.ping()
        return client
    except Exception as e:
        print(f"{RED}Error connecting to Redis: {e}{RESET}")
        return None


async def get_db_engine():
    try:
        cfg = get_database_config()
        return create_async_engine(cfg.url)
    except Exception as e:
        print(f"{RED}Error configuring Database: {e}{RESET}")
        return None


async def diagnose_token(token: str, redis, engine):
    h = _token_hash(token)
    redacted = _redact_token(token)
    print(f"\n{BOLD}Diagnosing Token:{RESET} {redacted}")
    print(f"{BOLD}Token Hash:{RESET} {h}")

    # 1. Redis Check
    key = token if token.startswith(CONSENT_TOKEN_PREFIX) else f"{CONSENT_TOKEN_PREFIX}{token}"
    ttl = await redis.ttl(key)
    raw_payload = await redis.get(key)

    if raw_payload:
        print(f"Redis State: {GREEN}ACTIVE{RESET}")
        print(f"TTL Remaining: {ttl} seconds")
        try:
            payload = json.loads(raw_payload)
            print(f"Redis Payload: {json.dumps(payload, indent=2)}")
        except Exception:
            print(f"{RED}Malformed Redis payload{RESET}")
    else:
        print(f"Redis State: {RED}MISSING or EXPIRED{RESET}")

    # 2. Database Check
    async with engine.connect() as conn:
        # Check consent_grant_log
        stmt = text("SELECT * FROM consent_grant_log WHERE token_hash = :h")
        result = await conn.execute(stmt, {"h": h})
        row = result.mappings().first()

        if row:
            print(f"\n{BOLD}Database Grant Log:{RESET}")
            status_color = GREEN
            status_text = "ISSUED"

            if row["revoked_at"]:
                status_color = RED
                status_text = f"REVOKED at {row['revoked_at']} (Reason: {row['revoked_reason']})"
            elif row["consumed_at"]:
                status_color = YELLOW
                status_text = f"CONSUMED at {row['consumed_at']}"
            elif row["expires_at"] < datetime.now(timezone.utc):
                status_color = RED
                status_text = f"EXPIRED at {row['expires_at']}"

            print(f"Status: {status_color}{status_text}{RESET}")
            for k, v in row.items():
                if k not in ["token_hash", "id"]:
                    print(f"  {k}: {v}")

            # 3. Audit Check
            print(f"\n{BOLD}Audit Trail:{RESET}")
            audit_stmt = text(
                "SELECT event_type, status, created_at, payload "
                "FROM system_audit "
                "WHERE target_resource_id = :h "
                "OR (payload->'metadata'->>'consent_token_hash' = :h) "
                "ORDER BY created_at ASC"
            )
            audit_results = await conn.execute(audit_stmt, {"h": h})
            audits = audit_results.mappings().all()

            if audits:
                for a in audits:
                    print(f"  [{a['created_at']}] {BLUE}{a['event_type']}{RESET} -> {a['status']}")
            else:
                print("  No audit logs found for this token hash.")
        else:
            print(f"\nDatabase Grant Log: {RED}NOT FOUND{RESET}")


async def diagnose_patient(patient_id: str, redis, engine):
    print(f"\n{BOLD}Diagnosing Patient:{RESET} {patient_id}")

    async with engine.connect() as conn:
        stmt = text(
            "SELECT token_hash, purpose, expires_at, consumed_at, revoked_at "
            "FROM consent_grant_log "
            "WHERE patient_id = :pid AND (revoked_at IS NULL AND consumed_at IS NULL AND expires_at > NOW())"
        )
        result = await conn.execute(stmt, {"pid": patient_id})
        active_grants = result.mappings().all()

        if active_grants:
            print(f"{GREEN}Found {len(active_grants)} active database grants:{RESET}")
            for g in active_grants:
                print(f"  Hash: {g['token_hash']}")
                print(f"    Purpose: {g['purpose']}, Expires: {g['expires_at']}")
        else:
            print(f"{YELLOW}No active database grants found for this patient.{RESET}")

    # Scan Redis (Expensive, but okay for diagnostic script)
    print(f"\n{BOLD}Scanning Redis for active tokens...{RESET}")
    keys = await redis.keys(f"{CONSENT_TOKEN_PREFIX}*")
    found_in_redis = 0
    for k in keys:
        val = await redis.get(k)
        try:
            payload = json.loads(val)
            if payload.get("patient_id") == patient_id:
                found_in_redis += 1
                ttl = await redis.ttl(k)
                print(f"  {GREEN}VALID TOKEN{RESET}: {_redact_token(k)}")
                print(f"    TTL: {ttl}s, Scope: {payload.get('scope')}")
        except Exception:
            continue

    if found_in_redis == 0:
        print("  No tokens found in Redis for this patient.")


async def main():
    parser = argparse.ArgumentParser(description="Nexa Care Consent Path Diagnostic Tool")
    parser.add_argument("--consent-token", help="Full raw consent token to diagnose")
    parser.add_argument("--patient-id", help="Patient ID to find active tokens for")

    args = parser.parse_args()

    if not args.consent_token and not args.patient_id:
        parser.print_help()
        return

    redis = await get_redis()
    engine = await get_db_engine()

    if not redis or not engine:
        return

    try:
        if args.consent_token:
            await diagnose_token(args.consent_token, redis, engine)

        if args.patient_id:
            await diagnose_patient(args.patient_id, redis, engine)
    finally:
        await redis.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
