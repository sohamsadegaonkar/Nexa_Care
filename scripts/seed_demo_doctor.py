#!/usr/bin/env python3
"""Seed a fully synthetic, disposable demo doctor for the local doctor app.

Creates:
- Hospital: Nexa Care Demo Hospital (NEXA-DEMO-HOSPITAL)
- Provider: Dr. Meera Joshi (password supplied through DEMO_PROVIDER_PASSWORD)
- real TOTP MFA backed by an ignored local demo secret
- only the minimum synthetic provider/facility trust state required by the
  existing clinical-eligibility policy
- Patient: Aarav Sharma (demo NFC card + clinical data)
- Patient: Priya Patel (second demo patient)

Run through `scripts/start_demo_dev.ps1` or with an explicit ignored
`.env.demo.local` selection; the script refuses every other target.
"""

from __future__ import annotations

import asyncio
import argparse
import hashlib
import hmac
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
import pyotp
from sqlalchemy import String, bindparam, func, select, text
from sqlalchemy.dialects.postgresql import JSONB

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_standalone_demo_env() -> None:
    """Load an explicitly selected demo config without replacing caller input."""

    # A caller-selected database target must remain authoritative.  In
    # particular, do not let a stale repository .env redirect a command that
    # was launched with an explicit local DATABASE_URL.  An explicit env file
    # is allowed for ignored, local-only demo configuration, but never falls
    # back to the repository .env when it is missing or malformed.
    configured_env_file = os.getenv("NEXA_DEMO_ENV_FILE", "").strip()
    if configured_env_file:
        env_file = Path(configured_env_file).expanduser()
        if not env_file.is_file():
            raise RuntimeError("NEXA_DEMO_ENV_FILE must point to an existing file")
        load_dotenv(env_file, override=False)
        return

    if os.getenv("DATABASE_URL", "").strip():
        return

    # Preserve direct CLI convenience without ever inheriting the repository's
    # general-purpose .env. Existing parent-shell values always win over this
    # ignored, launcher-owned disposable-demo fallback.
    default_demo_env = ROOT / ".env.demo.local"
    if not default_demo_env.is_file():
        raise RuntimeError(
            "Missing ignored demo configuration: .env.demo.local. Run start_demo_dev.ps1 -InitializeInfrastructure first."
        )
    load_dotenv(default_demo_env, override=False)


if __name__ == "__main__":
    load_standalone_demo_env()

from app.core.database import get_session_factory  # noqa: E402
from app.core.security import decrypt_mfa_secret, encrypt_mfa_secret  # noqa: E402
from app.models.nfc_card_registry import NFCCardRegistry, NFCCardStatus  # noqa: E402
from app.models.patient import Patient  # noqa: E402
from app.models.patient_auth_identity import PatientAuthIdentity  # noqa: E402
from app.models.provider import (  # noqa: E402
    AffiliationTrustStatus,
    AffiliationType,
    FacilityVerification,
    FacilityVerificationStatus,
    HospitalRegistry,
    ProfessionalVerification,
    ProfessionalVerificationStatus,
    ProviderCredential,
    ProviderHospitalAffiliation,
    ProviderIdentity,
    ProviderTrustPermissionGrant,
)
from app.observability.audit_ledger import append_audit_log  # noqa: E402
from app.security.audit_context import AuditContext, AuditDomain  # noqa: E402
from app.services.provider_auth_service import (  # noqa: E402
    hash_provider_password,
    normalize_provider_login_identifier,
    revoke_provider_auth_sessions,
)
from app.services.local_demo_patient_auth import (  # noqa: E402
    LOCAL_DEMO_PATIENT_AUTH_PROVIDER,
    local_demo_patient_subject,
)
from scripts.demo_environment import require_demo_environment  # noqa: E402

# ── Demo credentials ─────────────────────────────────────────────────────────

DEMO_PROVIDER_EMAIL = "demo.doctor@nexacare.in"
DEMO_HOSPITAL_CODE = "NEXA-DEMO-HOSPITAL"
DEMO_NFC_UID = "04:B3:C1:DE:55:01"
_DISPOSABLE_DATABASE_PREFIX = "nexa_qual_demo_"
_DEMO_PROVIDER_DISPLAY_NAME = "Dr. Meera Joshi"
_DEMO_PROVIDER_REGISTRATION_NUMBER = "MMC-2019-45231"
_DEMO_PROVIDER_SPECIALTY = "Internal Medicine"
_DEMO_PROVIDER_PHONE = "+91 98765 00001"
_DEMO_HOSPITAL_LEGAL_NAME = "Nexa Care Demo Hospital Pvt. Ltd."
_DEMO_HOSPITAL_DISPLAY_NAME = "Nexa Demo Hospital"
_DEMO_TRUST_SOURCE = "local-disposable-demo"
_DEMO_TRUST_REVIEWER = "DEMO_SEEDER"
_DEMO_AFFILIATION_DEPARTMENT = "Internal Medicine"
_DEMO_PROFESSIONAL_REFERENCE = "nexa-demo-professional-20260922"
_DEMO_FACILITY_REFERENCE = "nexa-demo-facility-20260922"
_DEMO_TRUST_DECISION_REASON = "SYNTHETIC_DEMO_SEED"
_DEMO_PATIENT_CONSENT_ASSURANCE_POLICY = "STANDARD"

# Demo patient IDs (deterministic UUIDs from namespace)
DEMO_PATIENT_1_ID = uuid.uuid5(
    uuid.NAMESPACE_DNS, "nexa-care-demo:patient:aarav-sharma"
)
DEMO_PATIENT_2_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "nexa-care-demo:patient:priya-patel")
_DEMO_LOCAL_PATIENT_IDENTITIES = (
    (DEMO_PATIENT_1_ID, "aarav"),
    (DEMO_PATIENT_2_ID, "priya"),
)

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
class HospitalSeedResult:
    hospital_id: uuid.UUID
    hospital_created: bool


def require_demo_provider_password() -> str:
    """Load and validate the demo password without ever returning it in output."""

    password = os.getenv("DEMO_PROVIDER_PASSWORD", "")
    if not password:
        raise RuntimeError(
            "Missing required script environment variable: DEMO_PROVIDER_PASSWORD"
        )
    normalized = password.strip().lower()
    obsolete = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    if (
        normalized in _REJECTED_PASSWORDS
        or "generate_a_strong" in normalized
        or obsolete == _OBSOLETE_DEMO_PASSWORD_DIGEST
    ):
        raise RuntimeError(
            "DEMO_PROVIDER_PASSWORD is a placeholder or obsolete example value"
        )
    if len(password) < 14:
        raise RuntimeError("DEMO_PROVIDER_PASSWORD must contain at least 14 characters")
    character_classes = (
        any(char.islower() for char in password),
        any(char.isupper() for char in password),
        any(char.isdigit() for char in password),
        any(not char.isalnum() for char in password),
    )
    if not all(character_classes):
        raise RuntimeError(
            "DEMO_PROVIDER_PASSWORD must contain upper, lower, numeric, and symbol characters"
        )
    return password


def require_demo_provider_totp_secret() -> str:
    """Return the ignored local TOTP secret without logging it or its code."""

    secret = os.getenv("DEMO_PROVIDER_TOTP_SECRET", "").strip().replace(" ", "")
    if not secret:
        raise RuntimeError(
            "Missing required script environment variable: DEMO_PROVIDER_TOTP_SECRET"
        )
    try:
        # Constructing a current code proves that the input is valid base32;
        # neither the secret nor the code leaves this function.
        pyotp.TOTP(secret).now()
    except Exception as exc:
        raise RuntimeError(
            "DEMO_PROVIDER_TOTP_SECRET must be a valid TOTP secret"
        ) from exc
    return secret


def require_disposable_demo_target() -> None:
    """Refuse to seed verification-like synthetic data anywhere but local demo DBs."""

    environment = os.getenv("ENVIRONMENT", "").strip().lower()
    database_url = os.getenv("DATABASE_URL", "").strip()
    try:
        parsed = urlsplit(database_url)
    except ValueError as exc:
        raise RuntimeError("DATABASE_URL must be a valid disposable demo URL") from exc
    database_name = parsed.path.strip("/")
    if (
        environment != "development"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or not database_name.startswith(_DISPOSABLE_DATABASE_PREFIX)
    ):
        raise RuntimeError(
            "seed_demo_doctor only permits a loopback nexa_qual_demo_* "
            "database with ENVIRONMENT=development"
        )


def _is_canonical_demo_hospital(hospital: HospitalRegistry) -> bool:
    """Return whether a matched facility row is the known synthetic facility."""

    return (
        hospital.facility_code == DEMO_HOSPITAL_CODE
        and hospital.legal_name == _DEMO_HOSPITAL_LEGAL_NAME
        and hospital.display_name == _DEMO_HOSPITAL_DISPLAY_NAME
        and hospital.city == "Mumbai"
        and hospital.state == "MH"
        and hospital.country_code == "IN"
    )


def _is_canonical_demo_provider(provider: ProviderIdentity) -> bool:
    """Return whether a matched login identity is the known synthetic provider."""

    return (
        provider.contact_email == DEMO_PROVIDER_EMAIL
        and provider.display_name == _DEMO_PROVIDER_DISPLAY_NAME
        and provider.medical_registration_number == _DEMO_PROVIDER_REGISTRATION_NUMBER
        and provider.specialty == _DEMO_PROVIDER_SPECIALTY
        and provider.contact_phone == _DEMO_PROVIDER_PHONE
        # The demo clinician is not a facility-bound administrator or a
        # legacy-identity bridge.  Those fields would widen its authority and
        # must be rejected rather than silently reused.
        and provider.role == "provider"
        and provider.hospital_id is None
        and provider.provider_uid is None
    )


def _is_aware_at_or_before(value: object, *, now: datetime) -> bool:
    """Return whether ``value`` is an aware timestamp no later than ``now``."""

    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
        and value <= now
    )


def _is_aware_after(value: object, *, now: datetime) -> bool:
    """Return whether ``value`` is an aware timestamp after ``now``."""

    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
        and value > now
    )


def _has_current_demo_affiliation(
    affiliation: ProviderHospitalAffiliation, *, now: datetime
) -> bool:
    """Recognize only an unexpired, already-seeded minimal affiliation."""

    return (
        affiliation.affiliation_type == AffiliationType.PERMANENT.value
        and affiliation.department == _DEMO_AFFILIATION_DEPARTMENT
        and affiliation.roles == ["clinician"]
        and affiliation.is_active is True
        and affiliation.is_primary is True
        and affiliation.trust_status == AffiliationTrustStatus.ACTIVE.value
        and _is_aware_at_or_before(affiliation.valid_from, now=now)
        and _is_aware_after(affiliation.valid_until, now=now)
    )


def _has_current_demo_professional_verification(
    professional: ProfessionalVerification, *, now: datetime
) -> bool:
    """Recognize only the active, unexpired synthetic trust fixture."""

    return (
        professional.status == ProfessionalVerificationStatus.VERIFIED.value
        and professional.verification_method == "synthetic-demo-seed"
        and professional.verification_source == _DEMO_TRUST_SOURCE
        and professional.verification_reference == _DEMO_PROFESSIONAL_REFERENCE
        and professional.registration_authority_code == "NEXA-DEMO"
        and professional.registration_number_normalized == "NEXA-DEMO-MEERA-001"
        and professional.reviewer_id == _DEMO_TRUST_REVIEWER
        and professional.decision_reason_code == _DEMO_TRUST_DECISION_REASON
        and _is_aware_at_or_before(professional.registration_valid_from, now=now)
        and _is_aware_after(professional.registration_valid_until, now=now)
        and _is_aware_at_or_before(professional.verified_at, now=now)
        and _is_aware_at_or_before(professional.last_checked_at, now=now)
        and _is_aware_after(professional.next_review_at, now=now)
        and professional.previous_verification_valid is True
        and professional.grace_expires_at is None
        and professional.recheck_attempted_at is None
        and professional.recheck_failure_reason is None
        and professional.authoritative_adverse_signal_at is None
        and professional.identity_binding_method is None
        and professional.identity_binding_status is None
        and professional.server_provenance_evidence_id is None
        and professional.version == 1
    )


def _has_current_demo_facility_verification(
    facility: FacilityVerification, *, now: datetime
) -> bool:
    """Recognize only the active, unexpired synthetic facility fixture."""

    return (
        facility.status == FacilityVerificationStatus.VERIFIED.value
        and facility.verification_method == "synthetic-demo-seed"
        and facility.verification_source == _DEMO_TRUST_SOURCE
        and facility.verification_reference == _DEMO_FACILITY_REFERENCE
        and facility.registration_authority_code == "NEXA-DEMO"
        and facility.registration_number_normalized == "NEXA-DEMO-HOSPITAL-001"
        and facility.reviewer_id == _DEMO_TRUST_REVIEWER
        and facility.decision_reason_code == _DEMO_TRUST_DECISION_REASON
        and _is_aware_at_or_before(facility.registration_valid_from, now=now)
        and _is_aware_after(facility.registration_valid_until, now=now)
        and _is_aware_at_or_before(facility.verified_at, now=now)
        and _is_aware_at_or_before(facility.last_checked_at, now=now)
        and _is_aware_after(facility.next_review_at, now=now)
        and facility.previous_verification_valid is True
        and facility.grace_expires_at is None
        and facility.recheck_attempted_at is None
        and facility.recheck_failure_reason is None
        and facility.authoritative_adverse_signal_at is None
        and facility.server_provenance_evidence_id is None
        and facility.version == 1
    )


async def seed_hospital(session) -> HospitalSeedResult:
    """Create or safely reuse the exact synthetic demo hospital only."""
    hospital = await session.scalar(
        select(HospitalRegistry).where(
            HospitalRegistry.facility_code == DEMO_HOSPITAL_CODE
        )
    )
    if hospital is None:
        hospital = HospitalRegistry(
            facility_code=DEMO_HOSPITAL_CODE,
            legal_name=_DEMO_HOSPITAL_LEGAL_NAME,
            display_name=_DEMO_HOSPITAL_DISPLAY_NAME,
            city="Mumbai",
            state="MH",
            country_code="IN",
            is_active=True,
        )
        session.add(hospital)
        await session.flush()
        return HospitalSeedResult(hospital_id=hospital.id, hospital_created=True)
    if not _is_canonical_demo_hospital(hospital):
        raise RuntimeError(
            "Demo hospital code is already bound to a non-synthetic facility"
        )
    return HospitalSeedResult(hospital_id=hospital.id, hospital_created=False)


async def seed_provider(
    session,
    hospital_id: uuid.UUID,
    *,
    reset_password: bool = False,
    reactivate_provider: bool = False,
    reactivate_credential: bool = False,
) -> ProviderSeedResult:
    """Create or safely reuse Dr. Meera Joshi and the canonical credential."""

    if (reactivate_provider or reactivate_credential) and not reset_password:
        raise ValueError(
            "Demo reactivation requires the explicit password-reset confirmation path"
        )
    if reactivate_provider != reactivate_credential:
        raise ValueError(
            "Demo provider and credential reactivation must be requested together"
    )

    normalized_login = normalize_provider_login_identifier(DEMO_PROVIDER_EMAIL)
    totp_secret = require_demo_provider_totp_secret()
    now = datetime.now(timezone.utc)
    providers = list(
        (
            await session.scalars(
                select(ProviderIdentity).where(
                    func.lower(func.trim(ProviderIdentity.contact_email))
                    == normalized_login
                )
            )
        ).all()
    )
    if len(providers) > 1:
        raise RuntimeError(
            "Multiple provider identities exist for the normalized demo provider login"
        )
    provider = providers[0] if providers else None
    provider_created = provider is None
    if provider is None:
        provider = ProviderIdentity(
            display_name=_DEMO_PROVIDER_DISPLAY_NAME,
            medical_registration_number=_DEMO_PROVIDER_REGISTRATION_NUMBER,
            specialty=_DEMO_PROVIDER_SPECIALTY,
            contact_email=DEMO_PROVIDER_EMAIL,
            contact_phone=_DEMO_PROVIDER_PHONE,
            status="active",
            is_active=True,
            role="provider",
        )
        session.add(provider)
        await session.flush()
    else:
        if not _is_canonical_demo_provider(provider):
            raise RuntimeError(
                "Demo login is already bound to a non-synthetic provider identity"
            )
        if reactivate_provider:
            provider.is_active = True
            provider.status = "active"

    active_trust_grants = list(
        (
            await session.scalars(
                select(ProviderTrustPermissionGrant).where(
                    ProviderTrustPermissionGrant.provider_id == provider.id,
                    ProviderTrustPermissionGrant.revoked_at.is_(None),
                )
            )
        ).all()
    )
    if active_trust_grants:
        raise RuntimeError(
            "Demo provider has an active trust-management grant and cannot be reused"
        )

    credentials = list(
        (
            await session.scalars(
                select(ProviderCredential).where(
                    func.lower(func.trim(ProviderCredential.login_identifier))
                    == normalized_login
                )
            )
        ).all()
    )
    if len(credentials) > 1:
        raise RuntimeError(
            "Multiple credentials exist for the normalized demo provider login"
        )
    credential = credentials[0] if credentials else None
    credential_created = credential is None
    if credential is None:
        if not provider_created and not (
            reset_password and reactivate_provider and reactivate_credential
        ):
            raise RuntimeError(
                "Existing demo provider is missing its credential; explicit reset and reactivation are required"
            )
        password = require_demo_provider_password()
        credential = ProviderCredential(
            provider_id=provider.id,
            login_identifier=normalized_login,
            password_hash=hash_provider_password(password),
            mfa_enabled=True,
            mfa_secret_encrypted=encrypt_mfa_secret(totp_secret),
            is_active=True,
        )
        session.add(credential)
    else:
        if credential.provider_id != provider.id:
            raise RuntimeError(
                "Demo credential is bound to a different provider identity"
            )
        if credential.login_identifier != normalized_login:
            raise RuntimeError(
                "Demo credential login binding changed; explicit reset and reactivation are required"
            )
        if credential.provider_uid is not None or credential.mfa_secret is not None:
            raise RuntimeError(
                "Demo credential contains unsupported legacy identity material"
            )
        if credential.mfa_enabled and credential.mfa_secret_encrypted:
            existing_secret = decrypt_mfa_secret(credential.mfa_secret_encrypted)
            if not existing_secret or not hmac.compare_digest(
                existing_secret, totp_secret
            ):
                raise RuntimeError(
                    "Demo credential MFA secret does not match the local demo configuration"
                )
        elif reset_password and reactivate_credential:
            credential.mfa_enabled = True
            credential.mfa_secret_encrypted = encrypt_mfa_secret(totp_secret)
        else:
            raise RuntimeError(
                "Demo credential MFA configuration changed; explicit reset and credential reactivation are required"
            )
        if not credential.is_active and not reactivate_credential:
            raise RuntimeError(
                "Demo credential is inactive; explicit reset and reactivation are required"
            )
        if (
            credential.locked_until is not None
            and not _is_aware_at_or_before(credential.locked_until, now=now)
            and not reset_password
        ):
            raise RuntimeError(
                "Demo credential is locked; explicit password reset is required"
            )
        if reset_password:
            credential.password_hash = hash_provider_password(
                require_demo_provider_password()
            )
            credential.failed_login_attempts = 0
            credential.locked_until = None
            credential.password_changed_at = datetime.now(timezone.utc)
        if reactivate_credential:
            credential.is_active = True

    affiliations = list(
        (
            await session.scalars(
                select(ProviderHospitalAffiliation).where(
                    ProviderHospitalAffiliation.provider_id == provider.id
                )
            )
        ).all()
    )
    if len(affiliations) > 1:
        raise RuntimeError("Demo provider has multiple affiliations and cannot be reused")
    affiliation = affiliations[0] if affiliations else None
    if affiliation is not None and affiliation.hospital_id != hospital_id:
        raise RuntimeError(
            "Demo provider affiliation is bound to a different facility"
        )
    affiliation_created = affiliation is None
    if affiliation is None:
        if not provider_created and not (
            reset_password and reactivate_provider and reactivate_credential
        ):
            raise RuntimeError(
                "Existing demo provider is missing its affiliation; explicit reset and reactivation are required"
            )
        affiliation = ProviderHospitalAffiliation(
            provider_id=provider.id,
            hospital_id=hospital_id,
            affiliation_type=AffiliationType.PERMANENT.value,
            department=_DEMO_AFFILIATION_DEPARTMENT,
            roles=["clinician", "emergency_reader"],
            is_primary=True,
            is_active=True,
        )
        session.add(affiliation)

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


async def seed_demo_clinical_trust(
    session,
    hospital_id: uuid.UUID,
    provider_id: uuid.UUID,
    *,
    provider_created: bool = False,
    hospital_created: bool = False,
    affiliation_created: bool = False,
    allow_reactivation: bool = False,
) -> None:
    """Seed synthetic trust without restoring a disabled or changed demo row.

    A routine rerun may create missing first-run rows or preserve a currently
    valid synthetic fixture. It must never reactivate a provider, facility,
    affiliation, or verification state that another process disabled. A caller
    must use the separately confirmed reset/reactivation path to make that
    security-significant change deliberately.
    """

    provider = await session.get(ProviderIdentity, provider_id)
    hospital = await session.get(HospitalRegistry, hospital_id)
    affiliation = await session.scalar(
        select(ProviderHospitalAffiliation).where(
            ProviderHospitalAffiliation.provider_id == provider_id,
            ProviderHospitalAffiliation.hospital_id == hospital_id,
        )
    )
    if provider is None or hospital is None or affiliation is None:
        raise RuntimeError("Canonical demo provider trust rows could not be resolved")
    if not _is_canonical_demo_provider(provider) or not _is_canonical_demo_hospital(
        hospital
    ):
        raise RuntimeError("Demo trust rows do not match the synthetic identities")

    now = datetime.now(timezone.utc)
    observed_at = now - timedelta(minutes=1)
    next_review_at = now + timedelta(days=7)
    registration_valid_until = now + timedelta(days=30)

    provider_requires_reactivation = (
        provider.is_active is not True or provider.status != "active"
    )
    if provider_requires_reactivation and not (provider_created or allow_reactivation):
        raise RuntimeError(
            "Demo provider is inactive; explicit reset and reactivation are required"
        )
    if provider_requires_reactivation:
        provider.is_active = True
        provider.status = "active"

    hospital_requires_reactivation = hospital.is_active is not True
    if hospital_requires_reactivation and not (hospital_created or allow_reactivation):
        raise RuntimeError(
            "Demo hospital is inactive; explicit reset and reactivation are required"
        )
    if hospital_requires_reactivation:
        hospital.is_active = True

    contacts_require_reactivation = not (
        _is_aware_at_or_before(provider.email_verified_at, now=now)
        and _is_aware_at_or_before(provider.phone_verified_at, now=now)
    )
    if contacts_require_reactivation and not (provider_created or allow_reactivation):
        raise RuntimeError(
            "Demo provider contact assurance changed; explicit reset and reactivation are required"
        )
    if contacts_require_reactivation:
        provider.email_verified_at = observed_at
        provider.phone_verified_at = observed_at

    if affiliation_created and not (provider_created or allow_reactivation):
        raise RuntimeError(
            "Existing demo provider is missing its affiliation; explicit reset and reactivation are required"
        )
    affiliation_requires_seed = affiliation_created or allow_reactivation
    if not affiliation_requires_seed and not _has_current_demo_affiliation(
        affiliation, now=now
    ):
        raise RuntimeError(
            "Demo affiliation trust changed or expired; explicit reset and reactivation are required"
        )
    if affiliation_requires_seed:
        # Narrow scope: one current clinician affiliation only. No reviewer,
        # administrator, trust-grant, or prescribing authority is seeded.
        affiliation.roles = ["clinician"]
        affiliation.is_active = True
        affiliation.is_primary = True
        affiliation.trust_status = AffiliationTrustStatus.ACTIVE.value
        affiliation.valid_from = observed_at
        affiliation.valid_until = next_review_at

    professional = await session.scalar(
        select(ProfessionalVerification).where(
            ProfessionalVerification.provider_id == provider_id
        )
    )
    professional_requires_seed = professional is None or allow_reactivation
    if professional is None:
        if not (provider_created or allow_reactivation):
            raise RuntimeError(
                "Existing demo provider is missing professional verification; explicit reset and reactivation are required"
            )
        professional = ProfessionalVerification(provider_id=provider_id)
        session.add(professional)
    elif not professional_requires_seed and not _has_current_demo_professional_verification(
        professional, now=now
    ):
        raise RuntimeError(
            "Demo professional verification changed or expired; explicit reset and reactivation are required"
        )
    if professional_requires_seed:
        professional.registration_authority_code = "NEXA-DEMO"
        professional.registration_number_normalized = "NEXA-DEMO-MEERA-001"
        professional.status = ProfessionalVerificationStatus.VERIFIED.value
        professional.verification_method = "synthetic-demo-seed"
        professional.verification_source = _DEMO_TRUST_SOURCE
        professional.verification_reference = _DEMO_PROFESSIONAL_REFERENCE
        professional.registration_valid_from = observed_at
        professional.registration_valid_until = registration_valid_until
        professional.verified_at = observed_at
        professional.last_checked_at = observed_at
        professional.next_review_at = next_review_at
        professional.previous_verification_valid = True
        professional.reviewer_id = _DEMO_TRUST_REVIEWER
        professional.decision_reason_code = _DEMO_TRUST_DECISION_REASON

    facility = await session.scalar(
        select(FacilityVerification).where(
            FacilityVerification.facility_id == hospital_id
        )
    )
    facility_requires_seed = facility is None or allow_reactivation
    if facility is None:
        if not (hospital_created or allow_reactivation):
            raise RuntimeError(
                "Existing demo hospital is missing facility verification; explicit reset and reactivation are required"
            )
        facility = FacilityVerification(facility_id=hospital_id)
        session.add(facility)
    elif not facility_requires_seed and not _has_current_demo_facility_verification(
        facility, now=now
    ):
        raise RuntimeError(
            "Demo facility verification changed or expired; explicit reset and reactivation are required"
        )
    if facility_requires_seed:
        facility.status = FacilityVerificationStatus.VERIFIED.value
        facility.verification_method = "synthetic-demo-seed"
        facility.verification_source = _DEMO_TRUST_SOURCE
        facility.verification_reference = _DEMO_FACILITY_REFERENCE
        facility.registration_authority_code = "NEXA-DEMO"
        facility.registration_number_normalized = "NEXA-DEMO-HOSPITAL-001"
        facility.registration_valid_from = observed_at
        facility.registration_valid_until = registration_valid_until
        facility.verified_at = observed_at
        facility.last_checked_at = observed_at
        facility.next_review_at = next_review_at
        facility.previous_verification_valid = True
        facility.reviewer_id = _DEMO_TRUST_REVIEWER
        facility.decision_reason_code = _DEMO_TRUST_DECISION_REASON

    await session.flush()


async def seed_nfc_card(
    session,
    patient_id: uuid.UUID,
    provider_id: uuid.UUID,
    *,
    patient_created: bool = False,
    allow_reactivation: bool = False,
) -> None:
    """Create the demo NFC card without reassigning an existing card UID."""

    existing_card = await session.scalar(
        select(NFCCardRegistry).where(NFCCardRegistry.card_uid == DEMO_NFC_UID)
    )
    if existing_card is not None:
        if (
            existing_card.patient_id != patient_id
            or existing_card.issued_by != provider_id
        ):
            raise RuntimeError("Demo NFC UID is already bound to another identity")
        # Preserve a lost/revoked status on routine reruns. Re-enrollment is a
        # separate security workflow and must not be simulated by this seeder.
        return
    if not (patient_created or allow_reactivation):
        raise RuntimeError(
            "Existing demo patient is missing its NFC card; explicit reset and reactivation are required"
        )
    session.add(
        NFCCardRegistry(
            card_uid=DEMO_NFC_UID,
            patient_id=patient_id,
            status=NFCCardStatus.ACTIVE.value,
            issued_by=provider_id,
        )
    )
    await session.flush()


async def seed_authoritative_patients(session) -> frozenset[uuid.UUID]:
    """Ensure the two synthetic demo identities exist before dependent rows."""

    created_patient_ids: set[uuid.UUID] = set()
    for patient_id in (DEMO_PATIENT_1_ID, DEMO_PATIENT_2_ID):
        patient = await session.get(Patient, patient_id)
        if patient is None:
            session.add(
                Patient(
                    patient_uuid=patient_id,
                    is_deleted=False,
                    dek_id=None,
                )
            )
            created_patient_ids.add(patient_id)
        elif patient.patient_uuid != patient_id:
            raise RuntimeError("Demo patient lookup returned the wrong identity")
        elif patient.is_deleted:
            raise RuntimeError(
                "Demo patient canonical identity is soft-deleted and cannot be reused"
            )
        elif (
            patient.dek_id is not None
            or patient.consent_assurance_policy
            != _DEMO_PATIENT_CONSENT_ASSURANCE_POLICY
        ):
            raise RuntimeError(
                "Demo patient canonical identity contains non-synthetic security state"
            )

    # Establish canonical Patient rows before the NFC and clinical shard writes
    # which reference the same deterministic synthetic IDs.
    await session.flush()
    return frozenset(created_patient_ids)


async def seed_local_demo_patient_auth_identities(
    session, *, newly_created_patient_ids: frozenset[uuid.UUID] = frozenset()
) -> None:
    """Link only the two canonical synthetic patients to the local demo provider.

    These are not Supabase identities and cannot be used by normal patient
    authentication.  The runtime route additionally requires explicit
    development configuration and local/emulator transport before it can issue
    a normal session for either subject.
    """

    for patient_id, demo_patient in _DEMO_LOCAL_PATIENT_IDENTITIES:
        subject = local_demo_patient_subject(demo_patient)
        if subject is None:
            raise RuntimeError("Local demo patient subject configuration is invalid")
        identities = list(
            (
                await session.scalars(
                    select(PatientAuthIdentity).where(
                        PatientAuthIdentity.patient_id == patient_id
                    )
                )
            ).all()
        )
        if not identities and patient_id not in newly_created_patient_ids:
            raise RuntimeError(
                "Existing demo patient is missing its local authentication identity"
            )
        if not identities:
            session.add(
                PatientAuthIdentity(
                    patient_id=patient_id,
                    provider=LOCAL_DEMO_PATIENT_AUTH_PROVIDER,
                    provider_subject=subject,
                )
            )
            continue
        if len(identities) != 1:
            raise RuntimeError(
                "Demo patient has unexpected additional authentication identities"
            )
        identity = identities[0]
        if identity.patient_id != patient_id:
            raise RuntimeError(
                "Local demo patient authentication identity is bound to a different patient"
            )
        if (
            identity.provider != LOCAL_DEMO_PATIENT_AUTH_PROVIDER
            or identity.provider_subject != subject
        ):
            raise RuntimeError(
                "Demo patient has a non-demo authentication identity"
            )
        elif identity.revoked_at is not None:
            raise RuntimeError(
                "Local demo patient authentication identity is revoked and cannot be reused"
            )
    await session.flush()


def _demo_clinical_payload(name: str) -> dict[str, list[str]]:
    """Return the fixed, synthetic clinical fixture for one demo patient."""

    if name == "aarav":
        return {
            "diagnoses": ["Type 2 Diabetes Mellitus", "Essential Hypertension"],
            "lab_results": ["HbA1c 7.2%", "Blood Pressure 148/92 mmHg"],
            "prescriptions": ["Metformin 500mg OD", "Lisinopril 10mg OD"],
        }
    if name == "priya":
        return {
            "diagnoses": ["Hypothyroidism", "Vitamin D Deficiency"],
            "lab_results": ["TSH 6.8 mIU/L", "Vitamin D 18 ng/mL"],
            "prescriptions": ["Levothyroxine 50mcg OD", "Cholecalciferol 60000 IU weekly"],
        }
    raise ValueError("Unknown synthetic demo patient")


def _is_canonical_demo_clinical_row(
    row: object, *, expected: dict[str, list[str]]
) -> bool:
    """Return whether an existing clinical shard row is exactly the fixture."""

    return bool(
        getattr(row, "get", lambda _key, _default=None: _default)("diagnoses")
        == expected["diagnoses"]
        and getattr(row, "get", lambda _key, _default=None: _default)(
            "lab_results"
        )
        == expected["lab_results"]
        and getattr(row, "get", lambda _key, _default=None: _default)(
            "prescriptions"
        )
        == expected["prescriptions"]
        and getattr(row, "get", lambda _key, _default=None: _default)(
            "clinical_data"
        )
        is None
    )


async def seed_clinical_records(
    session,
    patient_id: uuid.UUID,
    name: str,
    *,
    patient_created: bool = False,
    allow_reactivation: bool = False,
) -> None:
    """Insert a first-run synthetic clinical row without recreating one silently."""

    expected = _demo_clinical_payload(name)
    patient_id_text = str(patient_id)
    # The clinical shard intentionally lacks a global uniqueness constraint for
    # legacy data.  Serialize only this local demo fixture, within the caller's
    # transaction, so concurrent first-run seeders cannot create duplicate
    # rows. The fixed insertion order in ``main`` prevents lock-order cycles.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(CAST(:patient_id AS text)))").bindparams(
            bindparam("patient_id", type_=String(64))
        ),
        {"patient_id": patient_id_text},
    )
    existing_records = list(
        (
            await session.execute(
                text(
                    "SELECT diagnoses, lab_results, prescriptions, clinical_data "
                    "FROM nexa_clinical "
                    "WHERE masked_internal_id = CAST(:patient_id AS VARCHAR(64))"
                ).bindparams(bindparam("patient_id", type_=String(64))),
                {"patient_id": patient_id_text},
            )
        ).mappings().all()
    )
    if existing_records:
        if len(existing_records) != 1 or not _is_canonical_demo_clinical_row(
            existing_records[0], expected=expected
        ):
            raise RuntimeError(
                "Demo patient clinical fixture is altered or duplicated and cannot be reused"
            )
        return
    if not (patient_created or allow_reactivation):
        raise RuntimeError(
            "Existing demo patient is missing synthetic clinical records; explicit reset and reactivation are required"
        )

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
            "patient_id": patient_id_text,
            **expected,
        },
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed the canonical Nexa Care demo provider"
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
    if args.reactivate_provider != args.reactivate_credential:
        parser.error(
            "provider and credential reactivation must be requested together"
        )
    return args


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    require_demo_environment("seed_demo_doctor")
    require_disposable_demo_target()

    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            allow_reactivation = (
                args.reset_password
                and args.reactivate_provider
                and args.reactivate_credential
            )
            hospital_result = await seed_hospital(session)
            hospital_id = hospital_result.hospital_id
            provider_result = await seed_provider(
                session,
                hospital_id,
                reset_password=args.reset_password,
                reactivate_provider=allow_reactivation,
                reactivate_credential=allow_reactivation,
            )
            provider_id = provider_result.provider_id
            await seed_demo_clinical_trust(
                session,
                hospital_id,
                provider_id,
                provider_created=provider_result.provider_created,
                hospital_created=hospital_result.hospital_created,
                affiliation_created=provider_result.affiliation_created,
                allow_reactivation=allow_reactivation,
            )

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
            created_patient_ids = await seed_authoritative_patients(session)
            await seed_local_demo_patient_auth_identities(
                session, newly_created_patient_ids=created_patient_ids
            )
            await seed_nfc_card(
                session,
                DEMO_PATIENT_1_ID,
                provider_id,
                patient_created=DEMO_PATIENT_1_ID in created_patient_ids,
                allow_reactivation=allow_reactivation,
            )
            await seed_clinical_records(
                session,
                DEMO_PATIENT_1_ID,
                "aarav",
                patient_created=DEMO_PATIENT_1_ID in created_patient_ids,
                allow_reactivation=allow_reactivation,
            )

            # Patient 2: Priya Patel (manual search only)
            await seed_clinical_records(
                session,
                DEMO_PATIENT_2_ID,
                "priya",
                patient_created=DEMO_PATIENT_2_ID in created_patient_ids,
                allow_reactivation=allow_reactivation,
            )

            await session.commit()
        except Exception:
            await session.rollback()
            raise

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
    print("clinical_trust=synthetic_disposable_verified")
    print("mfa=totp_required")
    print("patient_auth=synthetic_local_development_only")
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
