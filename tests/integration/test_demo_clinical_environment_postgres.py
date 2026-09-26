from __future__ import annotations

from datetime import datetime, timezone
import os
import re

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.nfc_card_registry import NFCCardRegistry, NFCCardStatus
from app.models.patient import Patient
from app.models.provider import (
    AffiliationTrustStatus,
    FacilityVerification,
    FacilityVerificationStatus,
    HospitalRegistry,
    ProfessionalVerification,
    ProfessionalVerificationStatus,
    ProviderCredential,
    ProviderHospitalAffiliation,
    ProviderIdentity,
)
from app.security.clinical_policy import CLINICAL_CONTACT_ASSURANCE_POLICY
from app.security.provider_capabilities import ClinicalCapability
from app.services.clinical_eligibility import (
    ClinicalAuthenticationMethod,
    ClinicalEligibilityDenialCode,
    ClinicalEligibilityService,
    InteractiveClinicalAuthentication,
)
from scripts.seed_demo_doctor import (
    DEMO_HOSPITAL_CODE,
    DEMO_NFC_UID,
    DEMO_PATIENT_1_ID,
    DEMO_PATIENT_2_ID,
    DEMO_PROVIDER_EMAIL,
    seed_clinical_records,
    seed_hospital,
    seed_nfc_card,
    seed_patient_identity,
    seed_provider,
    seed_provider_trust,
)
from tests.helpers.qualification_infra import require_loopback_postgres_url


pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]
_PUBLIC_ID_RE = re.compile(r"^NC-[0-9A-F]{24}$")
_REQUIRED_CAPABILITIES = (
    ClinicalCapability.PATIENT_DISCOVER,
    ClinicalCapability.CONSENT_REQUEST,
    ClinicalCapability.RECORD_READ,
    ClinicalCapability.DOCUMENTS_UPLOAD,
    ClinicalCapability.DOCUMENTS_PROCESS,
    ClinicalCapability.DOCUMENTS_REVIEW,
    ClinicalCapability.DOCUMENTS_COMMIT,
    ClinicalCapability.EMERGENCY_ATTEMPT,
)


async def test_canonical_demo_environment_is_idempotent_and_clinically_eligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    require_loopback_postgres_url(database_url)
    monkeypatch.setenv("DEMO_PROVIDER_PASSWORD", "Demo-Qualified-Password-42!")
    monkeypatch.setenv(
        "DEMO_PROVIDER_MFA_SECRET",
        "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
    )

    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            hospital_id = await seed_hospital(db)
            first = await seed_provider(db, hospital_id)
            await seed_provider_trust(db, first.provider_id, hospital_id)
            patient_a = await seed_patient_identity(db, DEMO_PATIENT_1_ID)
            patient_b = await seed_patient_identity(db, DEMO_PATIENT_2_ID)
            await seed_nfc_card(db, patient_a.patient_uuid, first.provider_id)
            await seed_clinical_records(db, patient_a.patient_uuid, "aarav")
            await seed_clinical_records(db, patient_b.patient_uuid, "priya")
            await db.commit()

        async with factory() as db:
            same_hospital_id = await seed_hospital(db)
            second = await seed_provider(db, same_hospital_id)
            await seed_provider_trust(db, second.provider_id, same_hospital_id)
            patient_a_second = await seed_patient_identity(db, DEMO_PATIENT_1_ID)
            patient_b_second = await seed_patient_identity(db, DEMO_PATIENT_2_ID)
            await seed_nfc_card(
                db, patient_a_second.patient_uuid, second.provider_id
            )
            await seed_clinical_records(db, patient_a_second.patient_uuid, "aarav")
            await seed_clinical_records(db, patient_b_second.patient_uuid, "priya")
            await db.commit()

            assert same_hospital_id == hospital_id
            assert second.provider_id == first.provider_id
            assert second.provider_created is False
            assert second.credential_created is False
            assert second.affiliation_created is False
            assert second.password_reset is False

            provider = await db.scalar(
                select(ProviderIdentity).where(
                    ProviderIdentity.contact_email == DEMO_PROVIDER_EMAIL
                )
            )
            assert provider is not None
            assert provider.is_active is True
            assert provider.status == "active"
            assert provider.email_verified_at is not None
            assert provider.phone_verified_at is not None

            credential = await db.scalar(
                select(ProviderCredential).where(
                    ProviderCredential.provider_id == provider.id
                )
            )
            assert credential is not None
            assert credential.is_active is True
            assert credential.mfa_enabled is True
            assert credential.mfa_secret_encrypted
            assert credential.mfa_secret is None

            professional = await db.scalar(
                select(ProfessionalVerification).where(
                    ProfessionalVerification.provider_id == provider.id
                )
            )
            assert professional is not None
            assert professional.status == ProfessionalVerificationStatus.VERIFIED.value
            assert professional.server_provenance_evidence_id is not None
            assert professional.reviewer_id
            assert professional.reviewer_id != str(provider.id)

            facility = await db.scalar(
                select(FacilityVerification).where(
                    FacilityVerification.facility_id == hospital_id
                )
            )
            assert facility is not None
            assert facility.status == FacilityVerificationStatus.VERIFIED.value
            assert facility.server_provenance_evidence_id is not None
            assert facility.reviewer_id
            assert facility.reviewer_id != str(provider.id)

            affiliation = await db.scalar(
                select(ProviderHospitalAffiliation).where(
                    ProviderHospitalAffiliation.provider_id == provider.id,
                    ProviderHospitalAffiliation.hospital_id == hospital_id,
                )
            )
            assert affiliation is not None
            assert affiliation.is_active is True
            assert affiliation.trust_status == AffiliationTrustStatus.ACTIVE.value
            assert "clinician" in {
                str(role).strip().lower() for role in (affiliation.roles or [])
            }

            hospital = await db.scalar(
                select(HospitalRegistry).where(
                    HospitalRegistry.facility_code == DEMO_HOSPITAL_CODE
                )
            )
            assert hospital is not None
            assert hospital.id == hospital_id

            for patient_id in (DEMO_PATIENT_1_ID, DEMO_PATIENT_2_ID):
                patient = await db.get(Patient, patient_id)
                assert patient is not None
                assert patient.is_deleted is False
                assert _PUBLIC_ID_RE.fullmatch(patient.public_patient_id)

            card = await db.scalar(
                select(NFCCardRegistry).where(NFCCardRegistry.card_uid == DEMO_NFC_UID)
            )
            assert card is not None
            assert card.patient_id == DEMO_PATIENT_1_ID
            assert card.status == NFCCardStatus.ACTIVE.value

            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(ProviderIdentity)
                    .where(ProviderIdentity.contact_email == DEMO_PROVIDER_EMAIL)
                )
                == 1
            )
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(ProviderHospitalAffiliation)
                    .where(
                        ProviderHospitalAffiliation.provider_id == provider.id,
                        ProviderHospitalAffiliation.hospital_id == hospital_id,
                    )
                )
                == 1
            )

            now = datetime.now(timezone.utc)
            authentication = InteractiveClinicalAuthentication(
                provider_id=provider.id,
                hospital_id=hospital_id,
                method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
                session_authenticated=True,
                mfa_verified_at=now,
            )
            eligibility = ClinicalEligibilityService(
                contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
            )
            for capability in _REQUIRED_CAPABILITIES:
                result = await eligibility.evaluate_interactive(
                    db,
                    provider,
                    authentication,
                    capability,
                    now=now,
                )
                assert result.allowed is True, (
                    capability.value,
                    result.denial_code,
                )

            prescribe = await eligibility.evaluate_interactive(
                db,
                provider,
                authentication,
                ClinicalCapability.PRESCRIBE_MEDICATION,
                now=now,
            )
            assert prescribe.allowed is False
            assert prescribe.denial_code in {
                ClinicalEligibilityDenialCode.CLINICAL_CAPABILITY_NOT_GRANTED,
                ClinicalEligibilityDenialCode.PRESCRIBING_ELIGIBILITY_REQUIRED,
            }
    finally:
        await engine.dispose()
