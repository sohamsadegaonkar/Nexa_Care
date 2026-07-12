#!/usr/bin/env python3
"""Seed the demo doctor (Dr. Meera Joshi) for the doctor app demo.

Creates:
- Hospital: Nexa Care Demo Hospital (NEXA-DEMO-HOSPITAL)
- Provider: Dr. Meera Joshi (password supplied through DEMO_PROVIDER_PASSWORD)
- MFA: disabled (for demo simplicity)
- Patient: Aarav Sharma (demo NFC card + clinical data)
- Patient: Priya Patel (second demo patient)

Run with DATABASE_URL pointed at the target database.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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

# ── Demo credentials ─────────────────────────────────────────────────────────

DEMO_PROVIDER_EMAIL = "demo.doctor@nexacare.in"
DEMO_PROVIDER_PASSWORD = os.getenv("DEMO_PROVIDER_PASSWORD")
DEMO_HOSPITAL_CODE = "NEXA-DEMO-HOSPITAL"
DEMO_NFC_UID = "04:B3:C1:DE:55:01"

# Demo patient IDs (deterministic UUIDs from namespace)
DEMO_PATIENT_1_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "nexa-care-demo:patient:aarav-sharma")
DEMO_PATIENT_2_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "nexa-care-demo:patient:priya-patel")


async def seed_hospital(session) -> uuid.UUID:
    """Create or reuse the demo hospital."""
    hospital = await session.scalar(
        select(HospitalRegistry).where(HospitalRegistry.facility_code == DEMO_HOSPITAL_CODE)
    )
    if hospital is None:
        hospital = HospitalRegistry(
            facility_code=DEMO_HOSPITAL_CODE,
            legal_name="Nexa Care Demo Hospital Pvt. Ltd.",
            display_name="Nexa Demo Hospital",
            city="Mumbai",
            state="MH",
            country_code="IN",
            is_active=True,
        )
        session.add(hospital)
        await session.flush()
    return hospital.id


async def seed_provider(session, hospital_id: uuid.UUID) -> uuid.UUID:
    """Create or reuse Dr. Meera Joshi."""
    provider = await session.scalar(
        select(ProviderIdentity).where(ProviderIdentity.contact_email == DEMO_PROVIDER_EMAIL)
    )
    if provider is None:
        provider = ProviderIdentity(
            display_name="Dr. Meera Joshi",
            medical_registration_number="MMC-2019-45231",
            specialty="Internal Medicine",
            contact_email=DEMO_PROVIDER_EMAIL,
            contact_phone="+91 98765 00001",
            is_active=True,
        )
        session.add(provider)
        await session.flush()

    credential = await session.scalar(
        select(ProviderCredential).where(
            ProviderCredential.login_identifier == DEMO_PROVIDER_EMAIL
        )
    )
    if credential is None:
        credential = ProviderCredential(
            provider_id=provider.id,
            login_identifier=DEMO_PROVIDER_EMAIL,
            password_hash=hash_provider_password(DEMO_PROVIDER_PASSWORD),
            mfa_enabled=False,
            is_active=True,
        )
        session.add(credential)

    affiliation = await session.scalar(
        select(ProviderHospitalAffiliation).where(
            ProviderHospitalAffiliation.provider_id == provider.id,
            ProviderHospitalAffiliation.hospital_id == hospital_id,
        )
    )
    if affiliation is None:
        affiliation = ProviderHospitalAffiliation(
            provider_id=provider.id,
            hospital_id=hospital_id,
            affiliation_type=AffiliationType.PERMANENT.value,
            department="Internal Medicine",
            roles=["clinician", "emergency_reader"],
            is_primary=True,
            is_active=True,
        )
        session.add(affiliation)

    await session.flush()
    return provider.id


async def seed_nfc_card(session, patient_id: uuid.UUID, provider_id: uuid.UUID) -> None:
    """Upsert the demo NFC card."""
    stmt = (
        insert(NFCCardRegistry)
        .values(
            card_uid=DEMO_NFC_UID,
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


async def seed_clinical_records(session, patient_id: uuid.UUID, name: str) -> None:
    """Insert clinical shard row if missing."""
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
            "diagnoses": (
                ["Type 2 Diabetes Mellitus", "Essential Hypertension"]
                if name == "aarav"
                else ["Hypothyroidism", "Vitamin D Deficiency"]
            ),
            "lab_results": (
                ["HbA1c 7.2%", "Blood Pressure 148/92 mmHg"]
                if name == "aarav"
                else ["TSH 6.8 mIU/L", "Vitamin D 18 ng/mL"]
            ),
            "prescriptions": (
                ["Metformin 500mg OD", "Lisinopril 10mg OD"]
                if name == "aarav"
                else ["Levothyroxine 50mcg OD", "Cholecalciferol 60000 IU weekly"]
            ),
        },
    )


async def main() -> int:
    env = os.getenv("ENV", os.getenv("ENVIRONMENT", "development")).lower().strip()
    if env in {"prod", "production"}:
        raise RuntimeError(f"Refusing to seed demo doctor in production environment ('{env}').")
    if not DEMO_PROVIDER_PASSWORD:
        raise RuntimeError("Missing required script environment variable: DEMO_PROVIDER_PASSWORD")

    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            hospital_id = await seed_hospital(session)
            provider_id = await seed_provider(session, hospital_id)

            # Patient 1: Aarav Sharma (NFC card holder)
            await seed_nfc_card(session, DEMO_PATIENT_1_ID, provider_id)
            await seed_clinical_records(session, DEMO_PATIENT_1_ID, "aarav")

            # Patient 2: Priya Patel (manual search only)
            await seed_clinical_records(session, DEMO_PATIENT_2_ID, "priya")

            await session.commit()
        except Exception:
            await session.rollback()
            raise

    print("\n" + "=" * 72)
    print("NEXA CARE DEMO DOCTOR SEEDED")
    print("=" * 72)
    print("Doctor Name:     Dr. Meera Joshi")
    print(f"Doctor Email:    {DEMO_PROVIDER_EMAIL}")
    print(f"Provider ID:     {provider_id}")
    print(f"Hospital ID:     {hospital_id}")
    print()
    print("Patient 1 (NFC): Aarav Sharma")
    print(f"  Patient ID:    {DEMO_PATIENT_1_ID}")
    print(f"  NFC Card UID:  {DEMO_NFC_UID}")
    print()
    print("Patient 2 (Manual): Priya Patel")
    print(f"  Patient ID:    {DEMO_PATIENT_2_ID}")
    print("=" * 72 + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
