#!/usr/bin/env python3
"""Seed live Nexa Care test data for NFC simulator validation.

The script writes only test data:
- one NFC card mapping in ``nfc_card_registry``
- one clinical shard row in ``nexa_clinical``
- one mock provider/hospital/credential set for Basic auth smoke tests

It intentionally does not insert patient PII into the clinical shard or NFC
registry. Run with DATABASE_URL pointed at the target Supabase/Postgres DB.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=True)

from app.core.database import get_session_factory  # noqa: E402
from app.models.nfc_card_registry import NFCCardRegistry, NFCCardStatus  # noqa: E402
from app.models.provider import (  # noqa: E402
    AffiliationType,
    HospitalRegistry,
    ProviderCredential,
    ProviderHospitalAffiliation,
    ProviderIdentity,
)
from app.services.provider_auth_service import hash_provider_password  # noqa: E402

CARD_UID = "04:A2:B4:EA:51:22"
PATIENT_ID = uuid.uuid5(uuid.NAMESPACE_DNS, f"nexa-care-test-patient:{CARD_UID}")

TEST_PROVIDER_EMAIL = "test.doctor@nexa-care.local"
TEST_HOSPITAL_CODE = "NEXA-TEST-HOSPITAL"


async def seed_nfc_card(session, *, patient_id: uuid.UUID, provider_id: uuid.UUID) -> None:
    """Upsert the physical test card mapping without clinical or PII data."""

    stmt = (
        insert(NFCCardRegistry)
        .values(
            card_uid=CARD_UID,
            patient_id=patient_id,
            status=NFCCardStatus.ACTIVE.value,
            issued_by=provider_id,
        )
        .on_conflict_do_update(
            index_elements=[NFCCardRegistry.card_uid],
            set_={
                "patient_id": patient_id,
                "status": NFCCardStatus.ACTIVE.value,
                "issued_by": provider_id,
            },
        )
    )
    await session.execute(stmt)


async def seed_clinical_record(session, *, patient_id: uuid.UUID) -> None:
    """Insert one clinical shard row if the test patient has none."""

    await session.execute(
        text(
            "INSERT INTO nexa_clinical "
            "(masked_internal_id, diagnoses, lab_results, prescriptions) "
            "SELECT :patient_id, :diagnoses, :lab_results, :prescriptions "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM nexa_clinical WHERE masked_internal_id = :patient_id"
            ")"
        ),
        {
            "patient_id": str(patient_id),
            "diagnoses": ["Type 2 Diabetes Mellitus", "Essential Hypertension"],
            "lab_results": ["HbA1c 7.2%", "Blood Pressure 148/92 mmHg"],
            "prescriptions": ["Metformin 500mg OD", "Lisinopril 10mg OD"],
        },
    )


async def seed_provider(session) -> tuple[uuid.UUID, uuid.UUID]:
    """Create or reuse a mock doctor, hospital, credential, and affiliation."""

    hospital = await session.scalar(
        select(HospitalRegistry).where(HospitalRegistry.facility_code == TEST_HOSPITAL_CODE)
    )
    if hospital is None:
        hospital = HospitalRegistry(
            facility_code=TEST_HOSPITAL_CODE,
            legal_name="Nexa Care Test Hospital",
            display_name="Nexa Test Hospital",
            city="Bengaluru",
            state="KA",
            country_code="IN",
            is_active=True,
        )
        session.add(hospital)
        await session.flush()

    provider = await session.scalar(
        select(ProviderIdentity).where(ProviderIdentity.contact_email == TEST_PROVIDER_EMAIL)
    )
    if provider is None:
        provider = ProviderIdentity(
            display_name="Dr. Test Simulator",
            medical_registration_number="NEXA-TEST-MCI-001",
            specialty="Internal Medicine",
            contact_email=TEST_PROVIDER_EMAIL,
            contact_phone=None,
            is_active=True,
        )
        session.add(provider)
        await session.flush()

    credential = await session.scalar(
        select(ProviderCredential).where(
            ProviderCredential.login_identifier == TEST_PROVIDER_EMAIL
        )
    )
    if credential is None:
        password = os.getenv("TEST_PROVIDER_PASSWORD", "")
        if len(password) < 14:
            raise RuntimeError(
                "TEST_PROVIDER_PASSWORD must be configured with at least 14 characters"
            )
        credential = ProviderCredential(
            provider_id=provider.id,
            login_identifier=TEST_PROVIDER_EMAIL,
            password_hash=hash_provider_password(password),
            mfa_enabled=False,
            is_active=True,
        )
        session.add(credential)

    affiliation = await session.scalar(
        select(ProviderHospitalAffiliation).where(
            ProviderHospitalAffiliation.provider_id == provider.id,
            ProviderHospitalAffiliation.hospital_id == hospital.id,
        )
    )
    if affiliation is None:
        affiliation = ProviderHospitalAffiliation(
            provider_id=provider.id,
            hospital_id=hospital.id,
            affiliation_type=AffiliationType.PERMANENT.value,
            department="Internal Medicine",
            roles=["simulator", "fhir_export", "emergency_reader"],
            is_primary=True,
            is_active=True,
        )
        session.add(affiliation)

    await session.flush()
    return provider.id, hospital.id


async def main() -> int:
    """Run the seed transaction."""

    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            provider_id, hospital_id = await seed_provider(session)
            await seed_nfc_card(session, patient_id=PATIENT_ID, provider_id=provider_id)
            await seed_clinical_record(session, patient_id=PATIENT_ID)
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    print("\n" + "=" * 72)
    print("NEXA CARE TEST DATA SEEDED")
    print("=" * 72)
    print(f"Patient ID:      {PATIENT_ID}")
    print(f"NFC UID:         {CARD_UID}")
    print(f"Provider Email:  {TEST_PROVIDER_EMAIL}")
    print(f"Provider ID:     {provider_id}")
    print(f"Hospital ID:     {hospital_id}")
    print("=" * 72)
    print("Simulator emergency tap UID:")
    print(CARD_UID)
    print("Simulator routine checkup Patient ID:")
    print(PATIENT_ID)
    print("=" * 72 + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
