"""Deterministic real provider-session and clinical-trust test harness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from app.models.provider import (
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
)
from app.services.provider_auth_service import issue_provider_session_token

USER_AGENT = "NexaClinicalSecurityTest/1.0"
CLIENT_IP = "198.51.100.42"


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class ClinicalTrustDBAdapter:
    """Intercept only ORM trust selects; delegate route queries unchanged."""

    def __init__(self, fallback, provider, hospital):
        self.fallback = fallback
        self.provider = provider
        self.hospital = hospital

    async def execute(self, statement, *args, **kwargs):
        entities = {
            description.get("entity")
            for description in getattr(statement, "column_descriptions", ())
        }
        if ProviderIdentity in entities:
            return _Result(self.provider)
        if HospitalRegistry in entities:
            return _Result(self.hospital)
        return await self.fallback.execute(statement, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.fallback, name)


@dataclass(frozen=True)
class ClinicalTestSession:
    provider: ProviderIdentity
    hospital: HospitalRegistry
    affiliation: ProviderHospitalAffiliation
    db: ClinicalTrustDBAdapter
    token: str
    headers: dict[str, str]


class ClinicalSessionFactory:
    def __init__(self, fallback_db):
        self.fallback_db = fallback_db

    async def create(
        self,
        *,
        provider_id: UUID | None = None,
        hospital_id: UUID | None = None,
        roles=("clinician",),
        professional_status=ProfessionalVerificationStatus.VERIFIED,
        facility_status=FacilityVerificationStatus.VERIFIED,
        affiliation_status=AffiliationTrustStatus.ACTIVE,
        email_verified=True,
        phone_verified=True,
        mfa_enabled=True,
        mfa_verified=True,
    ):
        now = datetime.now(timezone.utc)
        provider_id = provider_id or uuid4()
        hospital_id = hospital_id or uuid4()
        provider = ProviderIdentity(
            id=provider_id,
            provider_uid=str(provider_id),
            status="active",
            is_active=True,
            contact_email="clinician@example.test",
            contact_phone="+910000000000",
            email_verified_at=now - timedelta(minutes=1) if email_verified else None,
            phone_verified_at=now - timedelta(minutes=1) if phone_verified else None,
        )
        credential = ProviderCredential(
            provider_id=provider_id,
            login_identifier="clinician@example.test",
            password_hash="test",
            is_active=True,
            mfa_enabled=mfa_enabled,
        )
        professional = ProfessionalVerification(
            provider_id=provider_id,
            status=professional_status.value,
            verified_at=now - timedelta(days=1),
            registration_valid_until=now + timedelta(days=30),
            next_review_at=now + timedelta(days=1),
        )
        hospital = HospitalRegistry(
            id=hospital_id,
            facility_code="TEST",
            display_name="Test Hospital",
            legal_name="Test Hospital",
            is_active=True,
        )
        facility = FacilityVerification(
            facility_id=hospital_id,
            status=facility_status.value,
            verified_at=now - timedelta(days=1),
            next_review_at=now + timedelta(days=1),
        )
        affiliation = ProviderHospitalAffiliation(
            id=uuid4(),
            provider_id=provider_id,
            hospital_id=hospital_id,
            affiliation_type=AffiliationType.PERMANENT.value,
            roles=list(roles),
            is_primary=True,
            is_active=True,
            trust_status=affiliation_status.value,
            valid_from=now - timedelta(days=1),
            valid_until=now + timedelta(days=1),
        )
        provider.credential = credential
        provider.professional_verification = professional
        provider.affiliations = [affiliation]
        hospital.verification = facility
        hospital.affiliations = [affiliation]
        affiliation.provider = provider
        affiliation.hospital = hospital
        token = await issue_provider_session_token(
            provider_id,
            USER_AGENT,
            CLIENT_IP,
            mfa_verified_at=now if mfa_verified else None,
        )
        return ClinicalTestSession(
            provider,
            hospital,
            affiliation,
            ClinicalTrustDBAdapter(self.fallback_db, provider, hospital),
            token,
            {
                "Authorization": f"Bearer {token}",
                "X-Hospital-Id": str(hospital_id),
                "User-Agent": USER_AGENT,
            },
        )
