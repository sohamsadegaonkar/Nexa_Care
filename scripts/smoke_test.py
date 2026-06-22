#!/usr/bin/env python3
"""Smoke test for Nexa Care, meant to run against a live staging instance.

Usage:
    BASE_URL=https://staging.example.com CLINIC_API_KEY=... python3 scripts/smoke_test.py

Exercises the endpoint chain in dependency order: register (as an
authenticated provider) -> enroll-biometric (binds the test device to the
patient) -> handshake (now actually verifiable) -> record reassembly
(session-only, no id in the URL) -> consent issuance -> consent-based
view. Exits non-zero on any failure so it can be wired into a deploy
pipeline as a post-deploy gate.

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
CLINIC_API_KEY = os.environ.get("CLINIC_API_KEY", "")
PROVIDER_HEADER = {"Authorization": f"Bearer {CLINIC_API_KEY}"}

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
    # masked_internal_id is filled in after registration, below.
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

    if not CLINIC_API_KEY:
        print("\nCLINIC_API_KEY not set -- /register and /enroll-biometric will fail with 401.")
        print("Set it to the same value as the target server's CLINIC_API_KEY env var.\n")

    # 2. Register a patient -> masked_internal_id (note: response shape is
    # {"pii_vault": {...}, "clinical_record": {...}}). Provider-gated:
    # patients don't self-register, only an authenticated facility system
    # can call this.
    status, body = request("POST", "/register", SAMPLE_PATIENT, headers=PROVIDER_HEADER)
    masked_id = body.get("pii_vault", {}).get("masked_internal_id")
    check("POST /register", status == 200 and bool(masked_id), f"status={status} body={body}")

    if not masked_id:
        print("\nCannot continue without a masked_internal_id from registration.")
        return summarize()

    # 3. Enroll the test device/biometric pair for this patient. Also
    # provider-gated -- this is the action that decides which physical
    # card/biometric the patient identity will trust at handshake time.
    status, body = request(
        "POST",
        "/api/v1/enroll-biometric",
        {**SAMPLE_HANDSHAKE, "masked_internal_id": masked_id},
        headers=PROVIDER_HEADER,
    )
    check(
        "POST /api/v1/enroll-biometric",
        status == 201 and body.get("status") == "enrolled",
        f"status={status} body={body}",
    )

    # 4. Biometric handshake, scoped to the patient just enrolled ->
    # session token. masked_internal_id is bound into the session here;
    # it is never accepted later from a URL, so it must be supplied now.
    # This now requires step 3 to have actually enrolled a matching
    # binding -- an unenrolled nfc_uid/bio_seed pair is correctly rejected.
    handshake_payload = {**SAMPLE_HANDSHAKE, "masked_internal_id": masked_id}
    status, body = request("POST", "/api/v1/handshake", handshake_payload)
    session_token = body.get("session_token")
    check("POST /api/v1/handshake", status == 200 and bool(session_token), f"status={status} body={body}")

    if not session_token:
        print("\nCannot continue without a session token from the handshake step.")
        return summarize()

    auth_header = {"Authorization": f"Bearer {session_token}"}

    # 5. Reassembly engine. No id in the URL or query string -- the
    # session resolves it server-side. A session scoped to a *different*
    # patient would correctly fail to see this record; that's the fix.
    status, body = request("GET", "/api/v1/record", headers=auth_header)
    check(
        "GET /api/v1/record",
        status == 200 and "pii" in body and "clinical" in body and body.get("masked_internal_id") == masked_id,
        f"status={status} body={body}",
    )

    # 6. Request a consent token. Also session-scoped now (not a
    # masked_internal_id in the body) -- this was the previously-open
    # door: anyone could mint a consent token for any patient with no
    # auth at all. Reuses the same handshake session from step 4.
    status, body = request("POST", "/request-consent", {}, headers=auth_header)
    consent_token = body.get("consent_token")
    check("POST /request-consent", status == 200 and bool(consent_token), f"status={status} body={body}")

    # 7. View clinical shard via consent token
    if consent_token:
        status, body = request("GET", "/view-record/clinical", headers={"X-Consent-Token": consent_token})
        check(
            "GET /view-record/clinical",
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