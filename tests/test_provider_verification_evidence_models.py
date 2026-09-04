"""Unit tests for Slice 5 Phase 5B verification evidence and facility trust schema foundations."""

import sqlalchemy as sa
from sqlalchemy.orm import RelationshipProperty

from app.models.provider import (
    FacilityVerification,
    ProfessionalVerification,
    ProviderTrustVerificationEvidence,
    VerificationEvidenceLookupPurpose,
    VerificationEvidenceOrigin,
    VerificationEvidenceOutcome,
    VerificationIdentityBindingResult,
)


def test_verification_evidence_origin_enum_values() -> None:
    expected = {"MANUAL_REVIEWER_ATTESTATION", "SERVER_REGISTRY_OBSERVATION"}
    actual = {item.value for item in VerificationEvidenceOrigin}
    assert actual == expected
    assert len(VerificationEvidenceOrigin) == 2


def test_verification_evidence_lookup_purpose_enum_values() -> None:
    expected = {
        "INITIAL_VERIFICATION",
        "RECHECK",
        "ADVERSE_SIGNAL_CHECK",
        "MANUAL_REVIEW",
    }
    actual = {item.value for item in VerificationEvidenceLookupPurpose}
    assert actual == expected
    assert len(VerificationEvidenceLookupPurpose) == 4


def test_verification_evidence_outcome_enum_values() -> None:
    expected = {
        "CONFIRMED_ACTIVE",
        "CONFIRMED_INACTIVE",
        "NOT_FOUND",
        "IDENTITY_MISMATCH",
        "AMBIGUOUS",
        "SOURCE_UNAVAILABLE",
        "SOURCE_RESPONSE_INVALID",
        "SOURCE_AUTHENTICATION_FAILURE",
        "SOURCE_INTEGRITY_FAILURE",
        "REVIEW_REQUIRED",
    }
    actual = {item.value for item in VerificationEvidenceOutcome}
    assert actual == expected
    assert len(VerificationEvidenceOutcome) == 10


def test_verification_identity_binding_result_enum_values() -> None:
    expected = {
        "NOT_EVALUATED",
        "MATCHED",
        "MISMATCHED",
        "AMBIGUOUS",
    }
    actual = {item.value for item in VerificationIdentityBindingResult}
    assert actual == expected
    assert len(VerificationIdentityBindingResult) == 4


def test_facility_verification_extended_schema_columns() -> None:
    table = FacilityVerification.__table__
    expected_new_columns = {
        "registration_authority_code",
        "registration_number_normalized",
        "registration_valid_from",
        "registration_valid_until",
        "grace_expires_at",
        "recheck_attempted_at",
        "recheck_failure_reason",
        "previous_verification_valid",
        "authoritative_adverse_signal_at",
    }
    col_names = {col.name for col in table.columns}
    assert expected_new_columns.issubset(col_names)

    # previous_verification_valid defaults to False and is not nullable
    prev_valid_col = table.columns["previous_verification_valid"]
    assert prev_valid_col.nullable is False
    assert prev_valid_col.default.arg is False

    # Check constraints on facility_verification
    ck_names = {
        ck.name for ck in table.constraints if isinstance(ck, sa.CheckConstraint)
    }
    assert "ck_facility_verification_recheck_failure_reason" in ck_names
    assert "ck_facility_verification_validity" in ck_names


def test_professional_verification_invariants_preserved() -> None:
    table = ProfessionalVerification.__table__
    # Must preserve 1:1 relationship with provider_identity (unique constraint)
    col_provider_id = table.columns["provider_id"]
    assert col_provider_id.unique is True or any(
        col_provider_id in uq.columns
        for uq in table.constraints
        if isinstance(uq, sa.UniqueConstraint) and len(uq.columns) == 1
    )
    # Existing unique registration identity constraint remains unchanged
    uq_reg = any(
        uq.name == "uq_professional_verification_authority_registration"
        for uq in table.constraints
        if isinstance(uq, sa.UniqueConstraint)
    )
    assert uq_reg is True
    # Evidence relationship exists
    assert hasattr(ProfessionalVerification, "evidence")
    rel: RelationshipProperty = getattr(ProfessionalVerification, "evidence").property
    assert rel.target == ProviderTrustVerificationEvidence.__table__
    assert rel.passive_deletes == "all"


def test_facility_verification_evidence_relationship() -> None:
    assert hasattr(FacilityVerification, "evidence")
    rel: RelationshipProperty = getattr(FacilityVerification, "evidence").property
    assert rel.target == ProviderTrustVerificationEvidence.__table__
    assert rel.passive_deletes == "all"


def test_provider_trust_verification_evidence_table_definition() -> None:
    table = ProviderTrustVerificationEvidence.__table__
    assert table.name == "provider_trust_verification_evidence"

    # Verify column existence and nullability
    expected_cols = {
        "id": False,
        "professional_verification_id": True,
        "facility_verification_id": True,
        "origin": False,
        "source_id": False,
        "adapter_version": True,
        "observed_at": False,
        "lookup_purpose": False,
        "outcome": False,
        "source_record_reference": True,
        "observed_valid_from": True,
        "observed_valid_until": True,
        "identity_binding_result": False,
        "binding_method": True,
        "response_digest": True,
        "external_transaction_id": True,
        "observed_resource_version": False,
        "created_at": False,
    }
    for col_name, nullable in expected_cols.items():
        assert col_name in table.columns, f"Missing column {col_name}"
        assert (
            table.columns[col_name].nullable is nullable
        ), f"Column {col_name} nullable mismatch: expected {nullable}, got {table.columns[col_name].nullable}"


def test_provider_trust_verification_evidence_foreign_keys() -> None:
    table = ProviderTrustVerificationEvidence.__table__
    fks = {fk.parent.name: fk for fk in table.foreign_keys}

    assert "professional_verification_id" in fks
    assert (
        fks["professional_verification_id"].target_fullname
        == "professional_verification.id"
    )
    assert fks["professional_verification_id"].ondelete == "RESTRICT"

    assert "facility_verification_id" in fks
    assert fks["facility_verification_id"].target_fullname == "facility_verification.id"
    assert fks["facility_verification_id"].ondelete == "RESTRICT"


def test_provider_trust_verification_evidence_check_constraints() -> None:
    table = ProviderTrustVerificationEvidence.__table__
    ck_names = {
        ck.name for ck in table.constraints if isinstance(ck, sa.CheckConstraint)
    }

    expected_checks = {
        "ck_provider_trust_verification_evidence_resource_target",
        "ck_provider_trust_verification_evidence_origin",
        "ck_provider_trust_verification_evidence_lookup_purpose",
        "ck_provider_trust_verification_evidence_outcome",
        "ck_provider_trust_verification_evidence_identity_binding_result",
        "ck_ptve_observed_resource_version",
        "ck_provider_trust_verification_evidence_adapter_version_origin",
        "ck_provider_trust_verification_evidence_validity_interval",
        "ck_provider_trust_verification_evidence_response_digest",
        "ck_provider_trust_verification_evidence_source_id_non_empty",
    }
    for expected_ck in expected_checks:
        assert expected_ck in ck_names, f"Missing check constraint: {expected_ck}"


def test_provider_trust_verification_evidence_indexes() -> None:
    table = ProviderTrustVerificationEvidence.__table__
    index_names = {idx.name for idx in table.indexes}

    expected_indexes = {
        "ix_provider_trust_verification_evidence_prof_id",
        "ix_provider_trust_verification_evidence_fac_id",
        "ix_provider_trust_verification_evidence_source_id",
        "ix_provider_trust_verification_evidence_observed_at",
        "ix_provider_trust_verification_evidence_outcome",
    }
    for expected_idx in expected_indexes:
        assert expected_idx in index_names, f"Missing index: {expected_idx}"


def test_provider_trust_verification_evidence_response_digest_canonical_sha256() -> (
    None
):
    table = ProviderTrustVerificationEvidence.__table__
    digest_ck = next(
        ck
        for ck in table.constraints
        if ck.name == "ck_provider_trust_verification_evidence_response_digest"
    )
    sql_text = str(digest_ck.sqltext)
    assert "^[0-9a-f]{64}$" in sql_text
    assert "~" in sql_text


def test_provider_trust_verification_evidence_has_no_authority_fields() -> None:
    """Verify authority fields MUST NOT exist on evidence table (Gate 3)."""
    table = ProviderTrustVerificationEvidence.__table__
    column_names = {c.name for c in table.columns}
    forbidden_authority_fields = {
        "verified",
        "authority_granted",
        "clinical_allowed",
        "permission_granted",
        "capability_granted",
    }
    for forbidden in forbidden_authority_fields:
        assert (
            forbidden not in column_names
        ), f"Forbidden authority field present on evidence: {forbidden}"
