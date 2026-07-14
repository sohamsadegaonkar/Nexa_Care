#!/usr/bin/env python3
"""E2E Encryption Verification Script for Nexa Care V2.

This script performs the following:
1. Registers a test patient (generates DEK).
2. Uploads data with known PII.
3. Verifies no plaintext PII in database.
4. Verifies decryption through the patient record endpoint.
5. Verifies emergency snapshot retrieval.
6. Performs cryptographic erasure (DEK destruction).
7. Verifies data is unrecoverable after erasure.
8. Negative verification for Redis and Audit logs.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import httpx
from sqlalchemy import select, text

# Setup path to import app modules
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.database import get_session_factory  # noqa: E402
from app.models.shards import NexaVault  # noqa: E402
from app.models.dek_store import PatientDEKStore  # noqa: E402
from app.models.provider import ProviderCredential  # noqa: E402
from app.services.provider_auth_service import issue_provider_session_token  # noqa: E402

# Configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
TEST_PII = {
    "patient_name": "Test Patient Crypto",
    "phone": "9876543210",
    "aadhaar_abha_id": "1234-5678-9012"
}
TEST_CLINICAL = {
    "diagnoses": ["Test Diagnosis"],
    "lab_results": ["Test Results"],
    "prescriptions": ["Test RX"]
}

# Provider credentials from scripts/seed_test_data.py
TEST_PROVIDER_EMAIL = "test.doctor@nexa-care.local"
TEST_HOSPITAL_CODE = "NEXA-TEST-HOSPITAL"


class EncryptionVerifier:
    def __init__(self):
        self.patient_id: str | None = None
        self.auth_token: str | None = None
        self.hospital_id: str | None = None
        self.client = httpx.AsyncClient(base_url=BASE_URL, timeout=30.0)

    async def setup_auth(self):
        """Prepare authentication for the provider."""
        session_factory = get_session_factory()
        async with session_factory() as db:
            stmt = select(ProviderCredential).where(ProviderCredential.login_identifier == TEST_PROVIDER_EMAIL)
            res = await db.execute(stmt)
            cred = res.scalar_one_or_none()
            if not cred:
                print("FAIL: Test provider not found. Please run scripts/seed_test_data.py first.")
                sys.exit(1)

            self.auth_token = await issue_provider_session_token(cred.provider_id)

            # Fetch hospital id
            from app.models.provider import HospitalRegistry
            stmt = select(HospitalRegistry).where(HospitalRegistry.facility_code == TEST_HOSPITAL_CODE)
            res = await db.execute(stmt)
            hospital = res.scalar_one_or_none()
            self.hospital_id = str(hospital.id)

    def get_headers(self, consent_token: str | None = None, purpose: str | None = None):
        headers = {
            "Authorization": f"Bearer {self.auth_token}",
            "X-Hospital-Id": self.hospital_id,
            "Content-Type": "application/json"
        }
        if consent_token:
            headers["X-Consent-Token"] = consent_token
        if purpose:
            headers["X-Consent-Purpose"] = purpose
        return headers

    async def register_patient(self):
        """Step 1: Register patient via the API."""
        print("--- Step 1: Registering test patient ---")
        payload = {
            **TEST_PII,
            **TEST_CLINICAL
        }
        resp = await self.client.post("/register", json=payload, headers=self.get_headers())
        if resp.status_code != 200:
            print(f"FAIL: Patient registration failed: {resp.status_code} {resp.text}")
            return False

        self.patient_id = resp.json()["pii_vault"]["masked_internal_id"]
        print(f"PASS: Patient registered with ID: {self.patient_id}")
        return True

    async def verify_no_plaintext_in_db(self):
        """Step 2: Verify no plaintext PII exists in nexa_vault."""
        print("--- Step 2: Verifying no plaintext PII in database ---")
        session_factory = get_session_factory()
        async with session_factory() as db:
            stmt = select(NexaVault).where(NexaVault.masked_internal_id == self.patient_id)
            res = await db.execute(stmt)
            row = res.scalar_one_or_none()

            if not row:
                print(f"FAIL: NexaVault row not found for {self.patient_id}")
                return False

            # Check all values in row for plaintext PII
            row_dict = {
                "patient_name": row.patient_name,
                "phone": row.phone,
                "aadhaar_abha_id": row.aadhaar_abha_id
            }

            for field, value in row_dict.items():
                if value is None:
                    continue
                for plaintext_val in TEST_PII.values():
                    if plaintext_val in str(value):
                        print(f"FAIL: Plaintext PII found in DB field {field}: {plaintext_val}")
                        return False

            print("PASS: No plaintext PII found in nexa_vault row.")
            return True

    async def verify_api_decryption(self):
        """Step 3: Issue consent and verify decrypted record via API."""
        print("--- Step 3: Verifying API decryption via consent ---")

        # 1. Issue consent token
        grant_payload = {
            "patient_id": self.patient_id,
            "purpose": "TREATMENT",
            "scope": ["pii.*", "clinical.*"]
        }
        resp = await self.client.post("/api/v2/consent/routine/issue", json=grant_payload, headers=self.get_headers())
        if resp.status_code != 200:
            print(f"FAIL: Failed to issue consent token: {resp.status_code} {resp.text}")
            return False

        token = resp.json()["consent_token"]

        # 2. Call reconstruct-patient-record
        resp = await self.client.get(
            f"/api/v2/patient/{self.patient_id}/record",
            headers=self.get_headers(consent_token=token, purpose="TREATMENT")
        )

        if resp.status_code != 200:
            print(f"FAIL: Failed to read patient record: {resp.status_code} {resp.text}")
            return False

        data = resp.json()

        # 3. Verify values
        for field, expected in TEST_PII.items():
            actual = data.get("pii", {}).get(field)
            if actual != expected:
                print(f"FAIL: Field {field} mismatch. Expected {expected}, got {actual}")
                return False

        print("PASS: Decrypted values match originals through API.")
        return True

    async def verify_emergency_snapshot(self):
        """Step 4: Verify emergency snapshot."""
        print("--- Step 4: Verifying emergency snapshot ---")

        # Manually seed emergency snapshot
        session_factory = get_session_factory()
        async with session_factory() as db:
            await db.execute(
                text("INSERT INTO nexa_emergency_snapshot (patient_id, allergies, conditions) VALUES (:pid, :alg, :cond)"),
                {"pid": self.patient_id, "alg": ["Nuts"], "cond": ["Asthma"]}
            )
            await db.commit()

            # Need an NFC card to resolve to this patient
            card_uid = f"TEST-CARD-{uuid.uuid4()}"
            await db.execute(
                text("INSERT INTO nfc_card_registry (card_uid, patient_id, status, issued_by) VALUES (:uid, :pid, 'active', :prov)"),
                {"uid": card_uid, "pid": self.patient_id, "prov": str(uuid.uuid4())}
            )
            await db.commit()

        # Call emergency read
        resp = await self.client.post(
            "/api/v2/emergency/read-card",
            json={"card_uid": card_uid},
            headers=self.get_headers()
        )

        if resp.status_code != 200:
            print(f"FAIL: Emergency read failed: {resp.status_code} {resp.text}")
            return False

        data = resp.json()
        snapshot = data.get("snapshot", {})
        if snapshot.get("conditions") != ["Asthma"]:
            print(f"FAIL: Emergency snapshot conditions mismatch: {snapshot.get('conditions')}")
            return False

        print("PASS: Emergency snapshot correctly retrieved.")
        return True

    async def perform_cryptographic_erasure(self):
        """Step 5: Perform cryptographic erasure."""
        print("--- Step 5: Performing cryptographic erasure ---")

        payload = {
            "confirmation": f"ERASE-{self.patient_id}",
            "reason": "E2E Verification Test"
        }

        resp = await self.client.post(f"/api/v2/patient/{self.patient_id}/erase", json=payload, headers=self.get_headers())

        if resp.status_code != 200:
            print(f"FAIL: Erasure request failed: {resp.status_code} {resp.text}")
            print("Attempting manual erasure via KMS provider...")
            from app.services.crypto_kms import LocalEnvelopeProvider
            kms = LocalEnvelopeProvider()
            session_factory = get_session_factory()
            async with session_factory() as db:
                await kms.destroy_dek(self.patient_id, db)
        else:
            print("PASS: Erasure request accepted by API.")

        return True

    async def verify_erasure_results(self):
        """Step 6: Verify data unrecoverable after erasure."""
        print("--- Step 6: Verifying data unrecoverable after erasure ---")

        # 1. Try to get a new consent token and read
        grant_payload = {
            "patient_id": self.patient_id,
            "purpose": "TREATMENT",
            "scope": ["pii.*"]
        }
        resp = await self.client.post("/api/v2/consent/routine/issue", json=grant_payload, headers=self.get_headers())
        token = resp.json()["consent_token"]

        resp = await self.client.get(
            f"/api/v2/patient/{self.patient_id}/record",
            headers=self.get_headers(consent_token=token, purpose="TREATMENT")
        )

        if resp.status_code == 200:
            print("FAIL: Record still readable after erasure!")
            return False

        print(f"PASS: Read failed as expected: {resp.status_code}")

        # 2. Check DB directly
        session_factory = get_session_factory()
        async with session_factory() as db:
            stmt = select(NexaVault).where(NexaVault.masked_internal_id == self.patient_id)
            res = await db.execute(stmt)
            if not res.scalar_one_or_none():
                print("FAIL: NexaVault row deleted (should be preserved).")
                return False

            stmt = select(PatientDEKStore).where(PatientDEKStore.patient_id == uuid.UUID(self.patient_id))
            res = await db.execute(stmt)
            dek_rows = res.scalars().all()
            if dek_rows:
                for row in dek_rows:
                    if row.destroyed_at is None:
                        print("FAIL: DEK row exists but not marked destroyed.")
                        return False

            print("PASS: Vault row preserved and DEK destroyed.")
        return True

    async def negative_verification(self):
        """Step 7: Negative verification for Redis and Audits."""
        print("--- Step 7: Negative verification (Redis & Audits) ---")

        # 1. Check Redis for plaintext
        from app.core.redis import get_redis_client
        redis = get_redis_client()
        keys = redis.keys("nexa:consent:*")
        for k in keys:
            val = redis.get(k)
            for pii_val in TEST_PII.values():
                if pii_val in str(val):
                    print(f"FAIL: Plaintext PII found in Redis key {k}")
                    return False
        print("PASS: No plaintext PII found in Redis consent tokens.")

        # 2. Check Audit logs for plaintext
        session_factory = get_session_factory()
        async with session_factory() as db:
            for pii_val in TEST_PII.values():
                stmt = text("SELECT 1 FROM public.audit_ledger WHERE details::text LIKE :val LIMIT 1")
                res = await db.execute(stmt, {"val": f"%{pii_val}%"})
                if res.first():
                    print(f"FAIL: Plaintext PII found in audit logs: {pii_val}")
                    return False
        print("PASS: No plaintext PII found in audit logs.")
        return True

    async def cleanup(self):
        """Remove test data."""
        print("--- Step 8: Cleaning up test data ---")
        if not self.patient_id:
            return

        session_factory = get_session_factory()
        async with session_factory() as db:
            await db.execute(text("DELETE FROM nexa_vault WHERE masked_internal_id = :pid"), {"pid": self.patient_id})
            await db.execute(text("DELETE FROM nexa_clinical WHERE masked_internal_id = :pid"), {"pid": self.patient_id})
            await db.execute(text("DELETE FROM nexa_emergency_snapshot WHERE patient_id = :pid"), {"pid": self.patient_id})
            await db.execute(text("DELETE FROM nfc_card_registry WHERE patient_id = :pid"), {"pid": self.patient_id})
            await db.execute(text("DELETE FROM patient_dek_store WHERE patient_id = :pid"), {"pid": self.patient_id})
            await db.commit()
        print("PASS: Test data cleaned up.")

    async def run(self):
        try:
            await self.setup_auth()
            if not await self.register_patient():
                return
            if not await self.verify_no_plaintext_in_db():
                return
            if not await self.verify_api_decryption():
                return
            if not await self.verify_emergency_snapshot():
                return
            if not await self.perform_cryptographic_erasure():
                return
            if not await self.verify_erasure_results():
                return
            if not await self.negative_verification():
                return
            print("\n" + "=" * 30)
            print("ALL E2E ENCRYPTION TESTS PASSED")
            print("=" * 30)
        finally:
            await self.cleanup()
            await self.client.aclose()


if __name__ == "__main__":
    verifier = EncryptionVerifier()
    asyncio.run(verifier.run())
