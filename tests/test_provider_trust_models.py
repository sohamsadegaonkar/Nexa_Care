from app.models.base import Base
from app.models.provider import (
    AffiliationTrustStatus,
    FacilityVerification,
    FacilityVerificationStatus,
    HospitalRegistry,
    ProfessionalVerification,
    ProfessionalVerificationStatus,
    ProviderHospitalAffiliation,
    ProviderIdentity,
)


def test_provider_trust_models_are_registered_and_relationships_are_one_to_one() -> (
    None
):
    tables = {table.name for table in Base.metadata.sorted_tables}
    assert {"professional_verification", "facility_verification"} <= tables
    assert ProviderIdentity.professional_verification.property.uselist is False
    assert HospitalRegistry.verification.property.uselist is False
    assert ProfessionalVerification.__tablename__ in tables
    assert FacilityVerification.__tablename__ in tables
    constraints = {
        constraint.name
        for table in (
            ProviderHospitalAffiliation.__table__,
            ProfessionalVerification.__table__,
            FacilityVerification.__table__,
        )
        for constraint in table.constraints
        if constraint.name
    }
    assert {
        "ck_provider_hospital_affiliation_trust_status",
        "ck_professional_verification_status",
        "ck_facility_verification_status",
    } <= constraints


def test_trust_defaults_are_fail_closed() -> None:
    provider = ProviderIdentity()
    facility = HospitalRegistry()
    assert provider.email_verified_at is None
    assert provider.phone_verified_at is None
    assert facility.facility_type is None
    assert (
        ProviderHospitalAffiliation.__table__.c.trust_status.default.arg
        == AffiliationTrustStatus.PENDING_ACTIVATION.value
    )
    assert (
        ProfessionalVerification.__table__.c.status.default.arg
        == ProfessionalVerificationStatus.NOT_SUBMITTED.value
    )
    assert (
        FacilityVerification.__table__.c.status.default.arg
        == FacilityVerificationStatus.DRAFT.value
    )
