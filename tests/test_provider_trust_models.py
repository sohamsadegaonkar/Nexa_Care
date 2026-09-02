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
    ProviderTrustPermissionGrant,
)


def test_provider_trust_models_are_registered_and_relationships_are_one_to_one() -> (
    None
):
    tables = {table.name for table in Base.metadata.sorted_tables}
    assert {
        "professional_verification",
        "facility_verification",
        "provider_trust_permission_grant",
    } <= tables
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
            ProviderTrustPermissionGrant.__table__,
        )
        for constraint in table.constraints
        if constraint.name
    }
    assert {
        "ck_provider_hospital_affiliation_trust_status",
        "ck_provider_hospital_affiliation_version_positive",
        "ck_professional_verification_status",
        "ck_professional_verification_version_positive",
        "ck_facility_verification_status",
        "ck_facility_verification_version_positive",
        "ck_provider_trust_permission_grant_permission",
        "ck_provider_trust_permission_grant_scope_type",
        "ck_provider_trust_permission_grant_scope_binding",
        "ck_provider_trust_permission_grant_validity",
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
    for model in (
        ProviderHospitalAffiliation,
        ProfessionalVerification,
        FacilityVerification,
    ):
        assert model.__table__.c.version.default.arg == 1


def test_trust_permission_grants_have_only_explicit_partial_active_uniqueness() -> None:
    indexes = {
        index.name: index for index in ProviderTrustPermissionGrant.__table__.indexes
    }
    assert {
        "uq_provider_trust_permission_grant_global_active",
        "uq_provider_trust_permission_grant_facility_active",
    } <= set(indexes)
    assert all(
        index.unique for index in indexes.values() if index.name.startswith("uq_")
    )
    for name in (
        "uq_provider_trust_permission_grant_global_active",
        "uq_provider_trust_permission_grant_facility_active",
    ):
        predicate = str(indexes[name].dialect_options["postgresql"]["where"])
        assert "revoked_at IS NULL" in predicate
        assert "valid_until" not in predicate
