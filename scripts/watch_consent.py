#!/usr/bin/env python3
"""Live Read-Only Consent Monitor for Day 14 Live Demo (Workstream 2).

Watches Upstash Redis in real-time for `consent_request:*` keys and prints
status changes (created -> pending -> approved/denied) with timestamps.
Redacts patient IDs and tokens in output.

Architectural Scaling Note:
For the Alpha Demo (Day 14), polling via `scan_iter(match="consent_request:*")`
is appropriate given the controlled demo concurrency. For production scaling,
this monitor should migrate to Redis Streams, Pub/Sub keyspace notifications,
or an indexed active request set (`nexa:active_requests`) to prevent full
keyspace scanning under heavy concurrency.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.redis import get_redis_client  # noqa: E402
from scripts.consent_preflight import redact  # noqa: E402


async def watch_live_consent(interval_seconds: float = 1.0) -> None:
    print("==========================================================================")
    print(" 📡 DAY 14 LIVE DEMO — REAL-TIME CONSENT MONITOR (READ-ONLY)")
    print("==========================================================================")
    print(" Watching Redis keyspace for `consent_request:*` status transitions...")
    print(" Press Ctrl+C to stop.")
    print("--------------------------------------------------------------------------")

    try:
        redis = get_redis_client()
    except Exception as exc:
        print(f" ❌ Unable to connect to Redis: {exc}")
        return

    known_states: dict[str, str] = {}

    try:
        while True:
            try:
                # Read-only scan of consent requests
                keys: list[str] = []
                for key in redis.scan_iter(match="consent_request:*", count=100):
                    keys.append(key)

                for key in sorted(keys):
                    raw = redis.get(key)
                    if not raw:
                        continue
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    try:
                        data = json.loads(raw)
                    except Exception:
                        continue

                    req_id = redact(data.get("request_id") or key.split(":")[-1])
                    pat_id = redact(data.get("patient_id"))
                    prov_id = redact(data.get("provider_id"))
                    status = data.get("status", "pending")
                    purpose = data.get("purpose", "UNKNOWN")

                    state_signature = f"{status}:{data.get('responded_at', '')}"
                    if known_states.get(key) != state_signature:
                        known_states[key] = state_signature
                        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

                        if status == "pending":
                            print(
                                f" [{now_str}] 🟡 NEW REQUEST   | Req={req_id} | Patient={pat_id} | Doctor={prov_id} | Purpose={purpose} | Status: PENDING"
                            )
                        elif status == "approved":
                            print(
                                f" [{now_str}] 🟢 APPROVED      | Req={req_id} | Patient={pat_id} | Doctor={prov_id} | Status: APPROVED (Grant Issued)"
                            )
                        elif status == "denied":
                            print(
                                f" [{now_str}] 🔴 DENIED        | Req={req_id} | Patient={pat_id} | Doctor={prov_id} | Status: DENIED by Patient"
                            )
                        else:
                            print(
                                f" [{now_str}] ⚪ UPDATE        | Req={req_id} | Patient={pat_id} | Status: {status.upper()}"
                            )
            except Exception as loop_exc:
                print(f" [WARN] Monitor polling error: {loop_exc}")

            await asyncio.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("\n -> Live monitor stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(watch_live_consent())
