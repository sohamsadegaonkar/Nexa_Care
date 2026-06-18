#!/usr/bin/env python3
"""Smoke test for Nexa Care, meant to run against a live staging instance.

Usage:
    BASE_URL=https://staging.example.com python3 scripts/smoke_test.py

Exercises the endpoint chain in dependency order: handshake -> register ->
record reassembly -> consent issuance -> consent-based view. Exits non-zero
on any failure so it can be wired into a deploy pipeline as a post-deploy
gate.

Uses only the standard library (urllib) so it has no extra dependencies
beyond Python itself.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")

# Obviously-fake placeholder data — never run this against real patient records.
SAMPLE_PATIENT = {
    "patient_name": "Test Patient",
    "phone": "9999999999",
    "aadhaar_abha_id": "0000-0000-0000",
    "diagnoses": ["smoke-test-diagnosis"],
    "lab_results": ["smoke-test-lab-result"],
    "prescriptions": ["smoke-test-prescription"],
}

SAMPLE_HANDSHAKE = {
    "nfc_uid": "SMOKE-TEST-NFC-UID",
    "bio_seed": "smoke-test-bio-seed",
}

failures: list[str] = []


def request(method: str, path: str, body: dict | None = None, headers: dict | None = None):
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        status = e.code
        raw = e.read().decode("utf-8")

    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = {"_raw": raw}

    return status, parsed


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"PASS  {label}")
    else:
        print(f"FAIL  {label}  {detail}")
        failures.append(label)


def main() -> int:
    print(f"Running smoke test against {BASE_URL}\n")

    # 1. Health check
    status, body = request("GET", "/health")
    check("GET /health", status == 200 and body.get("status") == "ok", f"status={status} body={body}")

    # 2. Biometric handshake -> session token
    status, body = request("POST", "/api/v1/handshake", SAMPLE_HANDSHAKE)
    session_token = body.get("session_token")
    check("POST /api/v1/handshake", status == 200 and bool(session_token), f"status={status} body={body}")

    if not session_token:
        print("\nCannot continue without a session token from the handshake step.")
        return summarize()

    auth_header = {"Authorization": f"Bearer {session_token}"}

    # 3. Register a patient -> masked_internal_id
    status, body = request("POST", "/register", SAMPLE_PATIENT)
    masked_id = body.get("masked_internal_id")
    check("POST /register", status == 200 and bool(masked_id), f"status={status} body={body}")

    if not masked_id:
        print("\nCannot continue without a masked_internal_id from registration.")
        return summarize()

    # 4. Reassembly engine (requires the handshake session)
    status, body = request("GET", f"/api/v1/record/{masked_id}", headers=auth_header)
    check(
        "GET /api/v1/record/{id}",
        status == 200 and "identity" in body and "clinical" in body,
        f"status={status} body={body}",
    )

    # 5. Request a consent token
    status, body = request("POST", "/request-consent", {"masked_internal_id": masked_id})
    consent_token = body.get("consent_token")
    check("POST /request-consent", status == 200 and bool(consent_token), f"status={status} body={body}")

    # 6. View record via consent token
    if consent_token:
        status, body = request("GET", "/view-record", headers={"X-Consent-Token": consent_token})
        check(
            "GET /view-record",
            status == 200 and body.get("masked_internal_id") == masked_id,
            f"status={status} body={body}",
        )

    return summarize()


def summarize() -> int:
    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())