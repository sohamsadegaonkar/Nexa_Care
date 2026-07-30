#!/usr/bin/env python3
"""Seed controlled synthetic clinicians for the doctor app demo.

Creates:
- Hospital: Nexa Care Demo Hospital (NEXA-DEMO-HOSPITAL)
- Provider: Dr. Meera Joshi (password supplied through DEMO_PROVIDER_PASSWORD)
- MFA: disabled (for demo simplicity)
- Patient: Aarav Sharma (demo NFC card + clinical data)
- Patient: Priya Patel (second demo patient)

Run with DATABASE_URL pointed at the target database.

Use ``--doctor-b-only`` to seed only Dr. Arjun Rao for the Milestone 6
cross-doctor isolation check. That path never creates patient, NFC, or clinical
data and reads its password only from ``DEMO_PROVIDER_B_PASSWORD``.
"""

from __future__ import annotations

import asyncio
import argparse
import hashlib
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import String, bindparam, func, or_, select, text
from sqlalchemy.dialects.postgresql import JSONB, insert

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_standalone_demo_env() -> None:
    """Load the ignored repo .env only for direct CLI execution."""

    # Standalone alpha tooling deliberately prefers the ignored repository .env
    # over stale parent-shell values. Imports must not mutate process config.
    load_dotenv(ROOT / ".env", override=True)


if __name__ == "__main__":
    load_standalone_demo_env()

from app.core.database import get_session_factory  # noqa: E402
from app.models.nfc_card_registry import NFCCardRegistry, NFCCardStatus  # noqa: E402
from app.models.provider import (  # noqa: E402
    AffiliationType,
    HospitalRegistry,
    ProviderCredential,
    ProviderHospitalAffiliation,
    ProviderIdentity,
)
from app.observability.audit_ledger import append_audit_log  # noqa: E402
from app.security.audit_context import AuditContext, AuditDomain  # noqa: E402
from app.services.provider_auth_service import (  # noqa: E402
    hash_provider_password,
    normalize_provider_login_identifier,
    revoke_provider_auth_sessions,
)
from scripts.demo_environment import require_demo_environment  # noqa: E402

# ── Demo credentials ─────────────────────────────────────────────────────────

DEMO_PROVIDER_EMAIL = "demo.doctor@nexacare.in"
DEMO_PROVIDER_B_EMAIL = "demo.doctor.b@nexacare.in"
DEMO_PROVIDER_B_DISPLAY_NAME = "Dr. Arjun Rao"
DEMO_PROVIDER_B_MEDICAL_REGISTRATION_NUMBER = "MMC-2021-58372"
DEMO_PROVIDER_B_PASSWORD_ENV = "DEMO_PROVIDER_B_PASSWORD"
DEMO_HOSPITAL_CODE = "NEXA-DEMO-HOSPITAL"
DEMO_NFC_UID = "04:B3:C1:DE:55:01"

# Demo patient IDs (deterministic UUIDs from namespace)
DEMO_PATIENT_1_ID = uuid.uuid5(
    uuid.NAMESPACE_DNS, "nexa-care-demo:patient:aarav-sharma"
)
DEMO_PATIENT_2_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "nexa-care-demo:patient:priya-patel")

_REJECTED_PASSWORDS = {
    "password",
    "changeme",
    "generated_alpha_demo_password",
    "<generate_a_strong_local_demo_password>",
}
_OBSOLETE_DEMO_PASSWORD_DIGEST = (
    "29d1281934b777f0aa3256eba7886479dfab1d2637927b73f6657344a0ea59b0"
)


@dataclass(frozen=True)
class ProviderSeedResult:
    provider_id: uuid.UUID
    provider_created: bool
    credential_created: bool
    affiliation_created: bool
    password_reset: bool
    provider_active: bool
    credential_active: bool


@dataclass(frozen=True)
class DemoProviderDefinition:
    display_name: str
    login_identifier: str
    medical_registration_number: str
    specialty: str
    contact_phone: str
    department: str
    roles: tuple[str, ...]
    identity_role: str | None = None


DOCTOR_A = DemoProviderDefinition(
    display_name="Dr. Meera Joshi",
    login_identifier=DEMO_PROVIDER_EMAIL,
    medical_registration_number="MMC-2019-45231",
    specialty="Internal Medicine",
    contact_phone="+91 98765 00001",
    department="Internal Medicine",
    roles=("clinician", "emergency_reader"),
)

DOCTOR_B = DemoProviderDefinition(
    display_name=DEMO_PROVIDER_B_DISPLAY_NAME,
    login_identifier=DEMO_PROVIDER_B_EMAIL,
    medical_registration_number=DEMO_PROVIDER_B_MEDICAL_REGISTRATION_NUMBER,
    specialty="Internal Medicine",
    contact_phone="+91 98765 00002",
    department="Internal Medicine",
    roles=("clinician",),
    identity_role="clinician",
)


def _require_demo_provider_password(environment_variable: str) -> str:
    """Load and validate one demo password without exposing its value."""

    password = os.getenv(environment_variable, "")
    if not password:
        raise RuntimeError(
            f"Missing required script environment variable: {environment_variable}"
        )
    normalized = password.strip().lower()
    obsolete = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    if (
        normalized in _REJECTED_PASSWORDS
        or "generate_a_strong" in normalized
        or obsolete == _OBSOLETE_DEMO_PASSWORD_DIGEST
    ):
        raise RuntimeError(
            f"{environment_variable} is a placeholder or obsolete example value"
        )
    if len(password) < 14:
        raise RuntimeError(
            f"{environment_variable} must contain at least 14 characters"
        )
    character_classes = (
        any(char.islower() for char in password),
        any(char.isupper() for char in password),
        any(char.isdigit() for char in password),
        any(not char.isalnum() for char in password),
    )
    if not all(character_classes):
        raise RuntimeError(
            f"{environment_variable} must contain upper, lower, numeric, "
            "and symbol characters"
        )
    return password


def require_demo_provider_password() -> str:
    """Load and validate Doctor A's existing demo password."""

    return _require_demo_provider_password("DEMO_PROVIDER_PASSWORD")


def require_demo_provider_b_password() -> str:
    """Load and validate Doctor B's separate demo password."""

    return _require_demo_provider_password(DEMO_PROVIDER_B_PASSWORD_ENV)


async def seed_hospital(session, *, require_existing: bool = False) -> uuid.UUID:
    """Create or reuse the demo hospital, or require Doctor A's existing row."""

    hospital = await session.scalar(
        select(HospitalRegistry).where(
            HospitalRegistry.facility_code == DEMO_HOSPITAL_CODE
        )
    )
    if hospital is None:
        if require_existing:
            raise RuntimeError(
                "Nexa Demo Hospital does not exist; seed Doctor A before Doctor B"
            )
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
    elif require_existing and not hospital.is_active:
        raise RuntimeError("Nexa Demo Hospital is not active")
    return hospital.id


async def _seed_demo_provider(
    session,
    hospital_id: uuid.UUID,
    *,
    definition: DemoProviderDefinition,
    password: str | None = None,
    strict_identity_ownership: bool = False,
    reset_password: bool = False,
    reactivate_provider: bool = False,
    reactivate_credential: bool = False,
) -> ProviderSeedResult:
    """Create or safely reuse one controlled synthetic provider."""

    normalized_login = normalize_provider_login_identifier(definition.login_identifier)
    if strict_identity_ownership:
        provider_candidates = list(
            (
                await session.scalars(
                    select(ProviderIdentity).where(
                        or_(
                            func.lower(func.trim(ProviderIdentity.contact_email))
                            == normalized_login,
                            ProviderIdentity.medical_registration_number
                            == definition.medical_registration_number,
                        )
                    )
                )
            ).all()
        )
        if len(provider_candidates) > 1:
            raise RuntimeError(
                "Doctor B identifiers resolve to different provider identities"
            )
        provider = provider_candidates[0] if provider_candidates else None
        if provider is not None and (
            normalize_provider_login_identifier(provider.contact_email or "")
            != normalized_login
            or provider.medical_registration_number
            != definition.medical_registration_number
        ):
            raise RuntimeError(
                "Doctor B provider identifiers conflict with an existing identity"
            )
    else:
        provider = await session.scalar(
            select(ProviderIdentity).where(
                func.lower(func.trim(ProviderIdentity.contact_email))
                == normalized_login
            )
        )

    provider_created = provider is None
    if provider is None:
        provider_values = {
            "display_name": definition.display_name,
            "medical_registration_number": definition.medical_registration_number,
            "specialty": definition.specialty,
            "contact_email": normalized_login,
            "contact_phone": definition.contact_phone,
            "status": "active",
            "is_active": True,
        }
        if definition.identity_role is not None:
            provider_values["role"] = definition.identity_role
        provider = ProviderIdentity(**provider_values)
        session.add(provider)
        await session.flush()
    elif reactivate_provider:
        provider.is_active = True
        provider.status = "active"
    elif strict_identity_ownership and (
        not provider.is_active or provider.status != "active"
    ):
        raise RuntimeError("Doctor B provider identity is not active")
    elif strict_identity_ownership and (
        provider.display_name != definition.display_name
        or provider.role != definition.identity_role
    ):
        raise RuntimeError(
            "Doctor B provider profile conflicts with the controlled demo definition"
        )

    if strict_identity_ownership:
        credential_query = select(ProviderCredential).where(
            or_(
                func.lower(func.trim(ProviderCredential.login_identifier))
                == normalized_login,
                ProviderCredential.provider_id == provider.id,
            )
        )
    else:
        credential_query = select(ProviderCredential).where(
            func.lower(func.trim(ProviderCredential.login_identifier))
            == normalized_login
        )
    credentials = list((await session.scalars(credential_query)).all())
    if len(credentials) > 1:
        if strict_identity_ownership:
            raise RuntimeError(
                "Doctor B provider or login has conflicting credential ownership"
            )
        raise RuntimeError(
            "Multiple credentials exist for the normalized demo provider login"
        )
    credential = credentials[0] if credentials else None
    credential_created = credential is None
    if credential is None:
        if password is None:
            password = require_demo_provider_password()
        credential = ProviderCredential(
            provider_id=provider.id,
            login_identifier=normalized_login,
            password_hash=hash_provider_password(password),
            mfa_enabled=False,
            is_active=True,
        )
        session.add(credential)
    else:
        if credential.provider_id != provider.id:
            raise RuntimeError(
                "Demo credential is bound to a different provider identity"
            )
        if (
            normalize_provider_login_identifier(credential.login_identifier)
            != normalized_login
        ):
            raise RuntimeError(
                "Demo provider identity already owns a different credential login"
            )
        if credential.login_identifier != normalized_login:
            credential.login_identifier = normalized_login
        if reset_password:
            credential.password_hash = hash_provider_password(
                password if password is not None else require_demo_provider_password()
            )
            credential.failed_login_attempts = 0
            credential.locked_until = None
            credential.password_changed_at = datetime.now(timezone.utc)
        if reactivate_credential:
            credential.is_active = True
        elif strict_identity_ownership and not credential.is_active:
            raise RuntimeError("Doctor B credential is not active")

    affiliation = await session.scalar(
        select(ProviderHospitalAffiliation).where(
            ProviderHospitalAffiliation.provider_id == provider.id,
            ProviderHospitalAffiliation.hospital_id == hospital_id,
        )
    )
    affiliation_created = affiliation is None
    if affiliation is None:
        affiliation = ProviderHospitalAffiliation(
            provider_id=provider.id,
            hospital_id=hospital_id,
            affiliation_type=AffiliationType.PERMANENT.value,
            department=definition.department,
            roles=list(definition.roles),
            is_primary=True,
            is_active=True,
        )
        session.add(affiliation)
    elif strict_identity_ownership and (
        not affiliation.is_active
        or affiliation.affiliation_type != AffiliationType.PERMANENT.value
        or list(affiliation.roles or []) != list(definition.roles)
    ):
        raise RuntimeError(
            "Doctor B affiliation conflicts with the controlled demo definition"
        )

    await session.flush()
    return ProviderSeedResult(
        provider_id=provider.id,
        provider_created=provider_created,
        credential_created=credential_created,
        affiliation_created=affiliation_created,
        password_reset=reset_password and not credential_created,
        provider_active=bool(provider.is_active and provider.status == "active"),
        credential_active=bool(credential.is_active),
    )


async def seed_provider(
    session,
    hospital_id: uuid.UUID,
    *,
    reset_password: bool = False,
    reactivate_provider: bool = False,
    reactivate_credential: bool = False,
) -> ProviderSeedResult:
    """Create or safely reuse Dr. Meera Joshi and the canonical credential."""

    return await _seed_demo_provider(
        session,
        hospital_id,
        definition=DOCTOR_A,
        reset_password=reset_password,
        reactivate_provider=reactivate_provider,
        reactivate_credential=reactivate_credential,
    )


async def seed_provider_b(
    session,
    hospital_id: uuid.UUID,
) -> ProviderSeedResult:
    """Create or safely reuse Doctor B without touching Doctor A or patient data."""

    return await _seed_demo_provider(
        session,
        hospital_id,
        definition=DOCTOR_B,
        password=require_demo_provider_b_password(),
        strict_identity_ownership=True,
    )


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
            "SELECT CAST(:patient_id AS VARCHAR(64)), :diagnoses, :lab_results, :prescriptions "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM nexa_clinical "
            "  WHERE masked_internal_id = CAST(:patient_id AS VARCHAR(64))"
            ")"
        ).bindparams(
            bindparam("patient_id", type_=String(64)),
            bindparam("diagnoses", type_=JSONB),
            bindparam("lab_results", type_=JSONB),
            bindparam("prescriptions", type_=JSONB),
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed the canonical Nexa Care demo provider"
    )
    parser.add_argument(
        "--doctor-b-only",
        action="store_true",
        help="Seed only Dr. Arjun Rao for cross-doctor isolation testing",
    )
    parser.add_argument("--reset-password", action="store_true")
    parser.add_argument("--confirm-demo-provider-reset", action="store_true")
    parser.add_argument("--reactivate-provider", action="store_true")
    parser.add_argument("--reactivate-credential", action="store_true")
    args = parser.parse_args(argv)
    if args.reset_password != args.confirm_demo_provider_reset:
        parser.error(
            "password reset requires both --reset-password and "
            "--confirm-demo-provider-reset"
        )
    if (
        args.reactivate_provider or args.reactivate_credential
    ) and not args.reset_password:
        parser.error(
            "reactivation flags are allowed only during an explicit password reset"
        )
    if args.doctor_b_only and (
        args.reset_password
        or args.confirm_demo_provider_reset
        or args.reactivate_provider
        or args.reactivate_credential
    ):
        parser.error("Doctor B seeding does not accept Doctor A reset flags")
    return args


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    require_demo_environment("seed_demo_doctor")

    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            hospital_id = await seed_hospital(
                session, require_existing=args.doctor_b_only
            )
            if args.doctor_b_only:
                provider_result = await seed_provider_b(session, hospital_id)
                await session.commit()
            else:
                provider_result = await seed_provider(
                    session,
                    hospital_id,
                    reset_password=args.reset_password,
                    reactivate_provider=args.reactivate_provider,
                    reactivate_credential=args.reactivate_credential,
                )
                provider_id = provider_result.provider_id

                if provider_result.password_reset:
                    await revoke_provider_auth_sessions(provider_id)
                    audited = await append_audit_log(
                        audit_context=AuditContext.for_hospital(
                            hospital_id=str(hospital_id),
                            domain=AuditDomain.AUTH,
                        ),
                        actor_uid="DEMO_PROVIDER_RESET_TOOL",
                        event_type="PROVIDER_PASSWORD_RESET",
                        target_id=str(provider_id),
                        status="SUCCESS",
                    )
                    if not audited:
                        raise RuntimeError(
                            "Audit write failed; demo provider password reset aborted"
                        )

                # Patient 1: Aarav Sharma (NFC card holder)
                await seed_nfc_card(session, DEMO_PATIENT_1_ID, provider_id)
                await seed_clinical_records(session, DEMO_PATIENT_1_ID, "aarav")

                # Patient 2: Priya Patel (manual search only)
                await seed_clinical_records(session, DEMO_PATIENT_2_ID, "priya")

                await session.commit()
        except Exception:
            await session.rollback()
            raise

    if args.doctor_b_only:
        print("\n" + "=" * 72)
        print("NEXA CARE DEMO DOCTOR B SEEDED")
        print("=" * 72)
        print(f"provider={'created' if provider_result.provider_created else 'reused'}")
        print(
            f"credential={'created' if provider_result.credential_created else 'reused'}"
        )
        print(
            f"affiliation={'created' if provider_result.affiliation_created else 'reused'}"
        )
        print(f"display_name={DEMO_PROVIDER_B_DISPLAY_NAME}")
        print(f"login={DEMO_PROVIDER_B_EMAIL}")
        print(f"provider_active={str(provider_result.provider_active).lower()}")
        print(f"credential_active={str(provider_result.credential_active).lower()}")
        print("=" * 72 + "\n")
        return 0

    print("\n" + "=" * 72)
    print("NEXA CARE DEMO DOCTOR SEEDED")
    print("=" * 72)
    print(f"provider={'created' if provider_result.provider_created else 'reused'}")
    print(f"credential={'created' if provider_result.credential_created else 'reused'}")
    print(
        f"affiliation={'created' if provider_result.affiliation_created else 'reused'}"
    )
    print(f"password={'reset' if provider_result.password_reset else 'unchanged'}")
    print(f"provider_active={str(provider_result.provider_active).lower()}")
    print(f"credential_active={str(provider_result.credential_active).lower()}")
    print(f"provider_id={provider_id}")
    print(f"hospital_id={hospital_id}")
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
