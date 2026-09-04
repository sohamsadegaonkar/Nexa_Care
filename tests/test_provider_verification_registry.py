"""Unit tests and architectural guards for the pure registry adapter contract (Phase 5C)."""

import ast
import traceback
from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.models.provider import (
    VerificationEvidenceLookupPurpose,
    VerificationEvidenceOutcome,
    VerificationIdentityBindingResult,
)
from app.services.provider_verification_registry import (
    PROVIDER_VERIFICATION_REGISTRY_CONTRACT_VERSION,
    FacilityLookupRequest,
    ProfessionalLookupRequest,
    RegistryAdapter,
    RegistryAdapterContractError,
    RegistryAdapterError,
    RegistryObservation,
    RegistryObservationInvalidError,
    RegistryRequestInvalidError,
    RegistryResourceType,
    RegistrySourceDescriptor,
    RegistryUnsupportedResourceError,
    SyntheticRegistryAdapter,
    compute_response_digest,
    validate_observation_provenance,
)


def test_contract_version_frozen() -> None:
    """The normalized contract version is code-owned and frozen."""
    assert (
        PROVIDER_VERIFICATION_REGISTRY_CONTRACT_VERSION
        == "provider-verification-registry/1.0"
    )


def test_resource_type_vocabulary() -> None:
    """Registry resource vocabulary is strictly closed to PROFESSIONAL and FACILITY."""
    assert set(RegistryResourceType) == {
        RegistryResourceType.PROFESSIONAL,
        RegistryResourceType.FACILITY,
    }
    for forbidden in ("PATIENT", "CONSENT", "CLINICAL_RECORD", "DOCUMENT", "USER"):
        assert not hasattr(RegistryResourceType, forbidden)


def test_source_descriptor_validation() -> None:
    """RegistrySourceDescriptor enforces non-empty, bounded, canonical, and immutable metadata."""
    desc = RegistrySourceDescriptor(
        source_id="TEST_SRC_PROF",
        adapter_version="1.0.0",
        supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
        authority_namespace="TEST_AUTH_NAMESPACE",
    )
    assert desc.source_id == "TEST_SRC_PROF"
    assert desc.adapter_version == "1.0.0"
    assert desc.supported_resource_types == (RegistryResourceType.PROFESSIONAL,)
    assert desc.authority_namespace == "TEST_AUTH_NAMESPACE"

    # Immutability
    with pytest.raises(FrozenInstanceError):
        desc.source_id = "OTHER"  # type: ignore[misc]

    # Blank / whitespace source_id
    with pytest.raises(RegistryAdapterContractError, match="source_id"):
        RegistrySourceDescriptor(
            source_id="   ",
            adapter_version="1.0.0",
            supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
        )

    # Non-canonical source_id (lowercase, leading hyphen)
    with pytest.raises(RegistryAdapterContractError, match="source_id"):
        RegistrySourceDescriptor(
            source_id="test_src",
            adapter_version="1.0.0",
            supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
        )
    with pytest.raises(RegistryAdapterContractError, match="source_id"):
        RegistrySourceDescriptor(
            source_id="-INVALID",
            adapter_version="1.0.0",
            supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
        )

    # Oversized source_id (> 64)
    with pytest.raises(RegistryAdapterContractError, match="source_id"):
        RegistrySourceDescriptor(
            source_id="X" * 65,
            adapter_version="1.0.0",
            supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
        )

    # Blank / whitespace / control char adapter_version
    with pytest.raises(RegistryAdapterContractError, match="adapter_version"):
        RegistrySourceDescriptor(
            source_id="TEST_SRC",
            adapter_version="",
            supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
        )
    with pytest.raises(RegistryAdapterContractError, match="adapter_version"):
        RegistrySourceDescriptor(
            source_id="TEST_SRC",
            adapter_version="1.0.0\n",
            supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
        )
    with pytest.raises(RegistryAdapterContractError, match="adapter_version"):
        RegistrySourceDescriptor(
            source_id="TEST_SRC",
            adapter_version=" " + "1.0.0",
            supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
        )

    # Empty supported_resource_types
    with pytest.raises(RegistryAdapterContractError, match="supported_resource_types"):
        RegistrySourceDescriptor(
            source_id="TEST_SRC",
            adapter_version="1.0.0",
            supported_resource_types=(),
        )

    # Invalid type in supported_resource_types
    with pytest.raises(RegistryAdapterContractError, match="supported_resource_types"):
        RegistrySourceDescriptor(
            source_id="TEST_SRC",
            adapter_version="1.0.0",
            supported_resource_types=("INVALID",),  # type: ignore[arg-type]
        )

    # Non-canonical authority_namespace
    with pytest.raises(RegistryAdapterContractError, match="authority_namespace"):
        RegistrySourceDescriptor(
            source_id="TEST_SRC",
            adapter_version="1.0.0",
            supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
            authority_namespace="invalid_lower",
        )
    with pytest.raises(RegistryAdapterContractError, match="authority_namespace"):
        RegistrySourceDescriptor(
            source_id="TEST_SRC",
            adapter_version="1.0.0",
            supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
            authority_namespace="X" * 65,
        )


def test_professional_request_validation() -> None:
    """ProfessionalLookupRequest enforces canonical token authority and normalized alphanumeric/slash number."""
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTHORITY",
        registration_number_normalized="REG1001/A",
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    assert req.registration_authority_code == "TEST_PROF_AUTHORITY"
    assert req.registration_number_normalized == "REG1001/A"
    assert req.lookup_purpose == VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION

    # Immutability
    with pytest.raises(FrozenInstanceError):
        req.registration_authority_code = "OTHER"  # type: ignore[misc]

    # Non-canonical registration_authority_code
    with pytest.raises(
        RegistryRequestInvalidError, match="registration_authority_code"
    ):
        ProfessionalLookupRequest(
            registration_authority_code="test_lower",
            registration_number_normalized="REG1001",
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        )
    with pytest.raises(
        RegistryRequestInvalidError, match="registration_authority_code"
    ):
        ProfessionalLookupRequest(
            registration_authority_code="X" * 65,
            registration_number_normalized="REG1001",
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        )

    # Non-canonical registration_number_normalized: hyphens rejected
    with pytest.raises(
        RegistryRequestInvalidError, match="registration_number_normalized"
    ):
        ProfessionalLookupRequest(
            registration_authority_code="TEST_PROF_AUTHORITY",
            registration_number_normalized="REG-1001",
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        )

    # Lowercase characters rejected
    with pytest.raises(
        RegistryRequestInvalidError, match="registration_number_normalized"
    ):
        ProfessionalLookupRequest(
            registration_authority_code="TEST_PROF_AUTHORITY",
            registration_number_normalized="reg1001",
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        )

    # Whitespace rejected (leading, trailing, internal)
    with pytest.raises(
        RegistryRequestInvalidError, match="registration_number_normalized"
    ):
        ProfessionalLookupRequest(
            registration_authority_code="TEST_PROF_AUTHORITY",
            registration_number_normalized=" REG1001",
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        )
    with pytest.raises(
        RegistryRequestInvalidError, match="registration_number_normalized"
    ):
        ProfessionalLookupRequest(
            registration_authority_code="TEST_PROF_AUTHORITY",
            registration_number_normalized="REG 1001",
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        )

    # Non-canonical punctuation rejected
    with pytest.raises(
        RegistryRequestInvalidError, match="registration_number_normalized"
    ):
        ProfessionalLookupRequest(
            registration_authority_code="TEST_PROF_AUTHORITY",
            registration_number_normalized="REG#1001",
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        )

    # Empty string rejected
    with pytest.raises(
        RegistryRequestInvalidError, match="registration_number_normalized"
    ):
        ProfessionalLookupRequest(
            registration_authority_code="TEST_PROF_AUTHORITY",
            registration_number_normalized="",
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        )

    # Oversized registration number rejected (> 128)
    with pytest.raises(
        RegistryRequestInvalidError, match="registration_number_normalized"
    ):
        ProfessionalLookupRequest(
            registration_authority_code="TEST_PROF_AUTHORITY",
            registration_number_normalized="A" * 129,
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        )

    # Invalid purpose
    with pytest.raises(RegistryRequestInvalidError, match="lookup_purpose"):
        ProfessionalLookupRequest(
            registration_authority_code="TEST_PROF_AUTHORITY",
            registration_number_normalized="REG1001",
            lookup_purpose="INVALID",  # type: ignore[arg-type]
        )


def test_facility_request_validation() -> None:
    """FacilityLookupRequest enforces canonical authority and non-empty bounded string without leading/trailing whitespace or control characters."""
    req = FacilityLookupRequest(
        registration_authority_code="TEST_FACILITY_AUTHORITY",
        registration_number_normalized="FAC500",
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    assert req.registration_authority_code == "TEST_FACILITY_AUTHORITY"
    assert req.registration_number_normalized == "FAC500"
    assert req.lookup_purpose == VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION

    # Immutability
    with pytest.raises(FrozenInstanceError):
        req.registration_authority_code = "OTHER"  # type: ignore[misc]

    # Non-canonical authority
    with pytest.raises(
        RegistryRequestInvalidError, match="registration_authority_code"
    ):
        FacilityLookupRequest(
            registration_authority_code="lower_auth",
            registration_number_normalized="FAC500",
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        )

    # Blank / whitespace registration number
    with pytest.raises(
        RegistryRequestInvalidError, match="registration_number_normalized"
    ):
        FacilityLookupRequest(
            registration_authority_code="TEST_FACILITY_AUTHORITY",
            registration_number_normalized="",
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        )
    with pytest.raises(
        RegistryRequestInvalidError, match="registration_number_normalized"
    ):
        FacilityLookupRequest(
            registration_authority_code="TEST_FACILITY_AUTHORITY",
            registration_number_normalized="   ",
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        )
    with pytest.raises(
        RegistryRequestInvalidError, match="registration_number_normalized"
    ):
        FacilityLookupRequest(
            registration_authority_code="TEST_FACILITY_AUTHORITY",
            registration_number_normalized=" FAC500",
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        )
    with pytest.raises(
        RegistryRequestInvalidError, match="registration_number_normalized"
    ):
        FacilityLookupRequest(
            registration_authority_code="TEST_FACILITY_AUTHORITY",
            registration_number_normalized="FAC500 ",
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        )

    # Control characters in registration number
    with pytest.raises(
        RegistryRequestInvalidError, match="registration_number_normalized"
    ):
        FacilityLookupRequest(
            registration_authority_code="TEST_FACILITY_AUTHORITY",
            registration_number_normalized="FAC500\x00",
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        )
    with pytest.raises(
        RegistryRequestInvalidError, match="registration_number_normalized"
    ):
        FacilityLookupRequest(
            registration_authority_code="TEST_FACILITY_AUTHORITY",
            registration_number_normalized="FAC\n500",
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        )

    # Oversized registration number (> 128)
    with pytest.raises(
        RegistryRequestInvalidError, match="registration_number_normalized"
    ):
        FacilityLookupRequest(
            registration_authority_code="TEST_FACILITY_AUTHORITY",
            registration_number_normalized="F" * 129,
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        )

    # Internal ordinary space is permitted by common contract
    req_with_space = FacilityLookupRequest(
        registration_authority_code="TEST_FACILITY_AUTHORITY",
        registration_number_normalized="FAC 500",
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    assert req_with_space.registration_number_normalized == "FAC 500"

    # Invalid purpose
    with pytest.raises(RegistryRequestInvalidError, match="lookup_purpose"):
        FacilityLookupRequest(
            registration_authority_code="TEST_FACILITY_AUTHORITY",
            registration_number_normalized="FAC500",
            lookup_purpose="INVALID",  # type: ignore[arg-type]
        )


def test_observation_utc_normalization() -> None:
    """RegistryObservation normalizes timezone-aware datetimes to UTC."""
    ist = timezone(timedelta(hours=5, minutes=30))
    ist_time = datetime(2026, 9, 4, 17, 30, 0, tzinfo=ist)
    ist_from = datetime(2025, 6, 1, 10, 0, 0, tzinfo=ist)
    ist_until = datetime(2030, 6, 1, 10, 0, 0, tzinfo=ist)

    obs = RegistryObservation(
        resource_type=RegistryResourceType.PROFESSIONAL,
        source_id="TEST_SRC_PROF",
        adapter_version="1.0.0",
        observed_at=ist_time,
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        observed_valid_from=ist_from,
        observed_valid_until=ist_until,
    )

    # Must be normalized to UTC
    assert obs.observed_at.tzinfo == timezone.utc
    assert obs.observed_at == datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    assert obs.observed_valid_from is not None
    assert obs.observed_valid_from.tzinfo == timezone.utc
    assert obs.observed_valid_from == datetime(
        2025, 6, 1, 4, 30, 0, tzinfo=timezone.utc
    )
    assert obs.observed_valid_until is not None
    assert obs.observed_valid_until.tzinfo == timezone.utc
    assert obs.observed_valid_until == datetime(
        2030, 6, 1, 4, 30, 0, tzinfo=timezone.utc
    )

    # Naive datetimes rejected
    with pytest.raises(RegistryObservationInvalidError, match="observed_at"):
        RegistryObservation(
            resource_type=RegistryResourceType.PROFESSIONAL,
            source_id="TEST_SRC_PROF",
            adapter_version="1.0.0",
            observed_at=datetime(2026, 9, 4, 12, 0, 0),  # naive
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        )

    with pytest.raises(RegistryObservationInvalidError, match="observed_valid_from"):
        RegistryObservation(
            resource_type=RegistryResourceType.PROFESSIONAL,
            source_id="TEST_SRC_PROF",
            adapter_version="1.0.0",
            observed_at=datetime.now(timezone.utc),
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            observed_valid_from=datetime(2025, 1, 1),  # naive
        )

    with pytest.raises(RegistryObservationInvalidError, match="observed_valid_until"):
        RegistryObservation(
            resource_type=RegistryResourceType.PROFESSIONAL,
            source_id="TEST_SRC_PROF",
            adapter_version="1.0.0",
            observed_at=datetime.now(timezone.utc),
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            observed_valid_until=datetime(2030, 1, 1),  # naive
        )


def test_observation_future_clock_skew() -> None:
    """RegistryObservation accepts timestamps <= 300s in the future, rejects > 300s."""
    now_utc = datetime.now(timezone.utc)

    # 250s in future: accepted
    obs_ok = RegistryObservation(
        resource_type=RegistryResourceType.PROFESSIONAL,
        source_id="TEST_SRC_PROF",
        adapter_version="1.0.0",
        observed_at=now_utc + timedelta(seconds=250),
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
    )
    assert obs_ok.observed_at > now_utc

    # 350s in future: rejected
    with pytest.raises(RegistryObservationInvalidError, match="future"):
        RegistryObservation(
            resource_type=RegistryResourceType.PROFESSIONAL,
            source_id="TEST_SRC_PROF",
            adapter_version="1.0.0",
            observed_at=now_utc + timedelta(seconds=350),
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        )


def test_observation_validity_dates() -> None:
    """RegistryObservation enforces validity date ordering."""
    now_utc = datetime.now(timezone.utc)
    t_from = datetime(2025, 1, 1, tzinfo=timezone.utc)
    t_until = datetime(2030, 1, 1, tzinfo=timezone.utc)

    # Valid: from < until
    obs = RegistryObservation(
        resource_type=RegistryResourceType.PROFESSIONAL,
        source_id="TEST_SRC_PROF",
        adapter_version="1.0.0",
        observed_at=now_utc,
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        observed_valid_from=t_from,
        observed_valid_until=t_until,
    )
    assert obs.observed_valid_from == t_from
    assert obs.observed_valid_until == t_until

    # Valid: from == until
    obs_eq = RegistryObservation(
        resource_type=RegistryResourceType.PROFESSIONAL,
        source_id="TEST_SRC_PROF",
        adapter_version="1.0.0",
        observed_at=now_utc,
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        observed_valid_from=t_from,
        observed_valid_until=t_from,
    )
    assert obs_eq.observed_valid_from == obs_eq.observed_valid_until

    # Invalid: until < from
    with pytest.raises(RegistryObservationInvalidError, match="earlier than"):
        RegistryObservation(
            resource_type=RegistryResourceType.PROFESSIONAL,
            source_id="TEST_SRC_PROF",
            adapter_version="1.0.0",
            observed_at=now_utc,
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            observed_valid_from=t_until,
            observed_valid_until=t_from,
        )


def test_observation_bounds_and_format() -> None:
    """RegistryObservation enforces bounds, digest format, and string hygiene."""
    now_utc = datetime.now(timezone.utc)
    valid_digest = "a" * 64

    # Valid observation with optional fields
    obs = RegistryObservation(
        resource_type=RegistryResourceType.PROFESSIONAL,
        source_id="TEST_SRC_PROF",
        adapter_version="1.0.0",
        observed_at=now_utc,
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        response_digest=valid_digest,
        source_record_reference="REF 12345",
        binding_method="SYNTHETIC_EXACT",
        external_transaction_id="TX 98765",
    )
    assert obs.response_digest == valid_digest
    assert obs.source_record_reference == "REF 12345"
    assert obs.binding_method == "SYNTHETIC_EXACT"
    assert obs.external_transaction_id == "TX 98765"

    # Invalid response digest: uppercase hex
    with pytest.raises(RegistryObservationInvalidError, match="response_digest"):
        RegistryObservation(
            resource_type=RegistryResourceType.PROFESSIONAL,
            source_id="TEST_SRC_PROF",
            adapter_version="1.0.0",
            observed_at=now_utc,
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            response_digest="A" * 64,
        )

    # Invalid response digest: wrong length
    with pytest.raises(RegistryObservationInvalidError, match="response_digest"):
        RegistryObservation(
            resource_type=RegistryResourceType.PROFESSIONAL,
            source_id="TEST_SRC_PROF",
            adapter_version="1.0.0",
            observed_at=now_utc,
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            response_digest="a" * 63,
        )


def test_observation_string_hygiene_and_binding_method() -> None:
    """RegistryObservation enforces control-character rejections and canonical binding_method."""
    now_utc = datetime.now(timezone.utc)

    # source_record_reference: leading/trailing whitespace rejected
    with pytest.raises(
        RegistryObservationInvalidError, match="source_record_reference"
    ):
        RegistryObservation(
            resource_type=RegistryResourceType.PROFESSIONAL,
            source_id="TEST_SRC_PROF",
            adapter_version="1.0.0",
            observed_at=now_utc,
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            source_record_reference=" REF123",
        )
    with pytest.raises(
        RegistryObservationInvalidError, match="source_record_reference"
    ):
        RegistryObservation(
            resource_type=RegistryResourceType.PROFESSIONAL,
            source_id="TEST_SRC_PROF",
            adapter_version="1.0.0",
            observed_at=now_utc,
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            source_record_reference="REF123 ",
        )

    # source_record_reference: control characters (newlines, tabs, null bytes) rejected
    with pytest.raises(
        RegistryObservationInvalidError, match="source_record_reference"
    ):
        RegistryObservation(
            resource_type=RegistryResourceType.PROFESSIONAL,
            source_id="TEST_SRC_PROF",
            adapter_version="1.0.0",
            observed_at=now_utc,
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            source_record_reference="REF\n123",
        )
    with pytest.raises(
        RegistryObservationInvalidError, match="source_record_reference"
    ):
        RegistryObservation(
            resource_type=RegistryResourceType.PROFESSIONAL,
            source_id="TEST_SRC_PROF",
            adapter_version="1.0.0",
            observed_at=now_utc,
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            source_record_reference="REF\t123",
        )

    # source_record_reference: oversized rejected (> 255)
    with pytest.raises(
        RegistryObservationInvalidError, match="source_record_reference"
    ):
        RegistryObservation(
            resource_type=RegistryResourceType.PROFESSIONAL,
            source_id="TEST_SRC_PROF",
            adapter_version="1.0.0",
            observed_at=now_utc,
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            source_record_reference="X" * 256,
        )

    # external_transaction_id: leading/trailing whitespace rejected
    with pytest.raises(
        RegistryObservationInvalidError, match="external_transaction_id"
    ):
        RegistryObservation(
            resource_type=RegistryResourceType.PROFESSIONAL,
            source_id="TEST_SRC_PROF",
            adapter_version="1.0.0",
            observed_at=now_utc,
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            external_transaction_id=" TX123",
        )
    with pytest.raises(
        RegistryObservationInvalidError, match="external_transaction_id"
    ):
        RegistryObservation(
            resource_type=RegistryResourceType.PROFESSIONAL,
            source_id="TEST_SRC_PROF",
            adapter_version="1.0.0",
            observed_at=now_utc,
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            external_transaction_id="TX123 ",
        )

    # external_transaction_id: control characters rejected
    with pytest.raises(
        RegistryObservationInvalidError, match="external_transaction_id"
    ):
        RegistryObservation(
            resource_type=RegistryResourceType.PROFESSIONAL,
            source_id="TEST_SRC_PROF",
            adapter_version="1.0.0",
            observed_at=now_utc,
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            external_transaction_id="TX\r123",
        )

    # external_transaction_id: oversized rejected (> 128)
    with pytest.raises(
        RegistryObservationInvalidError, match="external_transaction_id"
    ):
        RegistryObservation(
            resource_type=RegistryResourceType.PROFESSIONAL,
            source_id="TEST_SRC_PROF",
            adapter_version="1.0.0",
            observed_at=now_utc,
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            external_transaction_id="X" * 129,
        )

    # binding_method: canonical token required
    valid_methods = ("SYNTHETIC_EXACT", "SOURCE_SUBJECT_MATCH", "CONTACT_CORRELATION")
    for method in valid_methods:
        obs = RegistryObservation(
            resource_type=RegistryResourceType.PROFESSIONAL,
            source_id="TEST_SRC_PROF",
            adapter_version="1.0.0",
            observed_at=now_utc,
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
            binding_method=method,
        )
        assert obs.binding_method == method

    # binding_method: non-canonical rejected (lowercase, spaces, newlines, symbols)
    for invalid_method in (
        "synthetic_exact",
        "SYNTHETIC EXACT",
        "SYN\nEXACT",
        "URL/123",
        "M#1",
    ):
        with pytest.raises(RegistryObservationInvalidError, match="binding_method"):
            RegistryObservation(
                resource_type=RegistryResourceType.PROFESSIONAL,
                source_id="TEST_SRC_PROF",
                adapter_version="1.0.0",
                observed_at=now_utc,
                lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
                outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
                binding_method=invalid_method,
            )


def test_zero_authority_and_no_raw_payload_invariants() -> None:
    """RegistryObservation contains zero authority fields and retains no raw payloads."""
    field_names = {f.name for f in fields(RegistryObservation)}

    # Zero authority invariants
    forbidden_authority_fields = {
        "provider_id",
        "facility_id",
        "trust_state",
        "role",
        "roles",
        "permission",
        "permissions",
        "capability",
        "capabilities",
        "is_active",
        "tier",
        "decision",
        "automation_authorized",
        "clinical_authorized",
        "verified",
        "approved",
        "authority_granted",
        "clinical_allowed",
        "lifecycle_transition",
        "patient_access",
        "consent_authority",
    }
    assert not (field_names & forbidden_authority_fields)

    # No raw response payloads, HTML, tokens, or credentials
    forbidden_payload_fields = {
        "raw_response",
        "raw_payload",
        "raw_html",
        "raw_bytes",
        "body",
        "headers",
        "cookies",
        "token",
        "access_token",
        "session_id",
        "credentials",
        "response_bytes",
    }
    assert not (field_names & forbidden_payload_fields)


def test_compute_response_digest() -> None:
    """compute_response_digest computes a deterministic 64-char lowercase SHA-256 hex string."""
    payload = b'{"status": "ACTIVE", "code": 200}'
    digest = compute_response_digest(payload)
    assert len(digest) == 64
    assert digest == digest.lower()
    assert all(c in "0123456789abcdef" for c in digest)

    # Determinism
    assert compute_response_digest(payload) == digest

    # Bytearray support
    assert compute_response_digest(bytearray(payload)) == digest

    # Rejection of non-bytes
    with pytest.raises(RegistryAdapterContractError, match="response_bytes"):
        compute_response_digest("not_bytes")  # type: ignore[arg-type]


def test_closed_error_model() -> None:
    """Exceptions use class-owned closed vocabulary without constructor overrides."""
    assert RegistryAdapterError.error_code == "REGISTRY_ADAPTER_ERROR"
    assert RegistryAdapterContractError.error_code == "REGISTRY_CONTRACT_ERROR"
    assert (
        RegistryUnsupportedResourceError.error_code == "REGISTRY_UNSUPPORTED_RESOURCE"
    )
    assert RegistryRequestInvalidError.error_code == "REGISTRY_REQUEST_INVALID"
    assert RegistryObservationInvalidError.error_code == "REGISTRY_OBSERVATION_INVALID"

    # Subclass inheritance
    assert issubclass(RegistryAdapterContractError, RegistryAdapterError)
    assert issubclass(RegistryUnsupportedResourceError, RegistryAdapterContractError)
    assert issubclass(RegistryRequestInvalidError, RegistryAdapterError)
    assert issubclass(RegistryObservationInvalidError, RegistryAdapterContractError)

    # String format includes error code
    err = RegistryRequestInvalidError("invalid field")
    assert str(err) == "[REGISTRY_REQUEST_INVALID] invalid field"


def test_safe_error_messages() -> None:
    """Registry errors use safe static messages without echoing untrusted strings."""
    malicious_input = "<script>alert(1)</script>\r\nADMIN_INJECTION"
    with pytest.raises(RegistryRequestInvalidError) as exc_info:
        ProfessionalLookupRequest(
            registration_authority_code=malicious_input,
            registration_number_normalized="REG1001",
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        )
    assert malicious_input not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Public Template Method Safe-by-Construction Tests (Gates 1, 2, 3, 11, 14)
# ---------------------------------------------------------------------------


class MockExplodingAdapter(RegistryAdapter):
    """Adapter that raises an assertion error if hooks are invoked."""

    def __init__(self, supported: tuple[RegistryResourceType, ...]) -> None:
        self._descriptor = RegistrySourceDescriptor(
            source_id="EXPLODING_SRC",
            adapter_version="1.0.0",
            supported_resource_types=supported,
        )

    @property
    def source_descriptor(self) -> RegistrySourceDescriptor:
        return self._descriptor

    async def _lookup_professional(
        self, request: ProfessionalLookupRequest
    ) -> RegistryObservation:
        raise AssertionError("Professional hook must not be called")

    async def _lookup_facility(
        self, request: FacilityLookupRequest
    ) -> RegistryObservation:
        raise AssertionError("Facility hook must not be called")


@pytest.mark.asyncio
async def test_unsupported_resource_rejected_before_invocation() -> None:
    """Public template methods reject unsupported resources before invoking protected hooks."""
    prof_only = MockExplodingAdapter(supported=(RegistryResourceType.PROFESSIONAL,))
    fac_req = FacilityLookupRequest(
        registration_authority_code="TEST_FACILITY_AUTHORITY",
        registration_number_normalized="FAC500",
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    with pytest.raises(RegistryUnsupportedResourceError, match="facility verification"):
        await prof_only.lookup_facility(fac_req)

    fac_only = MockExplodingAdapter(supported=(RegistryResourceType.FACILITY,))
    prof_req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTHORITY",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    with pytest.raises(
        RegistryUnsupportedResourceError, match="professional verification"
    ):
        await fac_only.lookup_professional(prof_req)


class MockMismatchedAdapter(RegistryAdapter):
    """Adapter returning intentionally mutated or invalid observations."""

    def __init__(
        self,
        descriptor: RegistrySourceDescriptor,
        bad_observation: object,
    ) -> None:
        self._desc = descriptor
        self._bad_obs = bad_observation

    @property
    def source_descriptor(self) -> RegistrySourceDescriptor:
        return self._desc

    async def _lookup_professional(
        self, request: ProfessionalLookupRequest
    ) -> RegistryObservation:
        return self._bad_obs  # type: ignore[return-value]

    async def _lookup_facility(
        self, request: FacilityLookupRequest
    ) -> RegistryObservation:
        return self._bad_obs  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_template_method_catches_mismatched_source_id() -> None:
    """Public template method rejects observation with wrong source_id."""
    desc = RegistrySourceDescriptor(
        source_id="SRC_EXPECTED",
        adapter_version="1.0.0",
        supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
    )
    bad_obs = RegistryObservation(
        resource_type=RegistryResourceType.PROFESSIONAL,
        source_id="SRC_WRONG",
        adapter_version="1.0.0",
        observed_at=datetime.now(timezone.utc),
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
    )
    adapter = MockMismatchedAdapter(desc, bad_obs)
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTHORITY",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    with pytest.raises(
        RegistryAdapterContractError, match="source provenance mismatch"
    ):
        await adapter.lookup_professional(req)


@pytest.mark.asyncio
async def test_template_method_catches_mismatched_adapter_version() -> None:
    """Public template method rejects observation with wrong adapter_version."""
    desc = RegistrySourceDescriptor(
        source_id="SRC_EXPECTED",
        adapter_version="1.0.0",
        supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
    )
    bad_obs = RegistryObservation(
        resource_type=RegistryResourceType.PROFESSIONAL,
        source_id="SRC_EXPECTED",
        adapter_version="2.0.0",
        observed_at=datetime.now(timezone.utc),
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
    )
    adapter = MockMismatchedAdapter(desc, bad_obs)
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTHORITY",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    with pytest.raises(
        RegistryAdapterContractError, match="adapter version provenance mismatch"
    ):
        await adapter.lookup_professional(req)


@pytest.mark.asyncio
async def test_template_method_catches_mismatched_resource_type() -> None:
    """Public template method rejects observation with wrong resource_type."""
    desc = RegistrySourceDescriptor(
        source_id="SRC_EXPECTED",
        adapter_version="1.0.0",
        supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
    )
    bad_obs = RegistryObservation(
        resource_type=RegistryResourceType.FACILITY,
        source_id="SRC_EXPECTED",
        adapter_version="1.0.0",
        observed_at=datetime.now(timezone.utc),
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
    )
    adapter = MockMismatchedAdapter(desc, bad_obs)
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTHORITY",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    with pytest.raises(RegistryAdapterContractError, match="resource type mismatch"):
        await adapter.lookup_professional(req)


@pytest.mark.asyncio
async def test_template_method_catches_mutated_lookup_purpose() -> None:
    """Public template method rejects observation where adapter altered lookup_purpose (Gate 14)."""
    desc = RegistrySourceDescriptor(
        source_id="SRC_EXPECTED",
        adapter_version="1.0.0",
        supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
    )
    bad_obs = RegistryObservation(
        resource_type=RegistryResourceType.PROFESSIONAL,
        source_id="SRC_EXPECTED",
        adapter_version="1.0.0",
        observed_at=datetime.now(timezone.utc),
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
    )
    adapter = MockMismatchedAdapter(desc, bad_obs)
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTHORITY",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    with pytest.raises(RegistryAdapterContractError, match="lookup purpose mismatch"):
        await adapter.lookup_professional(req)


@pytest.mark.asyncio
async def test_template_method_catches_non_observation_return() -> None:
    """Public template method rejects None or arbitrary returned objects."""
    desc = RegistrySourceDescriptor(
        source_id="SRC_EXPECTED",
        adapter_version="1.0.0",
        supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
    )
    adapter_none = MockMismatchedAdapter(desc, None)
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTHORITY",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    with pytest.raises(
        RegistryAdapterContractError, match="unexpected execution error"
    ):
        await adapter_none.lookup_professional(req)

    adapter_dict = MockMismatchedAdapter(desc, {"status": "ACTIVE"})
    with pytest.raises(RegistryAdapterContractError, match="invalid observation type"):
        await adapter_dict.lookup_professional(req)


class MockCrashingAdapter(RegistryAdapter):
    """Adapter that raises an internal exception containing secrets."""

    @property
    def source_descriptor(self) -> RegistrySourceDescriptor:
        return RegistrySourceDescriptor(
            source_id="CRASHING_SRC",
            adapter_version="1.0.0",
            supported_resource_types=(
                RegistryResourceType.PROFESSIONAL,
                RegistryResourceType.FACILITY,
            ),
        )

    async def _lookup_professional(
        self, request: ProfessionalLookupRequest
    ) -> RegistryObservation:
        raise RuntimeError("postgres://admin:SUPER_SECRET_PASSWORD@db:5432/nexa")

    async def _lookup_facility(
        self, request: FacilityLookupRequest
    ) -> RegistryObservation:
        raise ConnectionRefusedError("Upstream server 10.0.0.1:443 refused connection")


@pytest.mark.asyncio
async def test_guarded_execution_sanitizes_exceptions_and_tracebacks() -> None:
    """Protected hook exceptions are sanitized and do not leak into messages, causes, or tracebacks (Items 1 & 2)."""
    adapter = MockCrashingAdapter()

    prof_req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTHORITY",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    with pytest.raises(RegistryAdapterContractError) as exc_info:
        await adapter.lookup_professional(prof_req)

    assert exc_info.value.message == "Registry adapter unexpected execution error"
    assert exc_info.value.error_code == "REGISTRY_CONTRACT_ERROR"
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None

    # Formatted traceback must NOT contain sensitive strings
    tb_lines = traceback.format_exception(exc_info.type, exc_info.value, exc_info.tb)
    tb_text = "".join(tb_lines)
    assert "SUPER_SECRET_PASSWORD" not in tb_text
    assert "postgres://" not in tb_text
    assert "RuntimeError" not in tb_text

    fac_req = FacilityLookupRequest(
        registration_authority_code="TEST_FACILITY_AUTHORITY",
        registration_number_normalized="FAC500",
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    with pytest.raises(RegistryAdapterContractError) as exc_info:
        await adapter.lookup_facility(fac_req)

    assert exc_info.value.message == "Registry adapter unexpected execution error"
    assert exc_info.value.error_code == "REGISTRY_CONTRACT_ERROR"
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None

    tb_lines = traceback.format_exception(exc_info.type, exc_info.value, exc_info.tb)
    tb_text = "".join(tb_lines)
    assert "10.0.0.1" not in tb_text
    assert "ConnectionRefusedError" not in tb_text


class MockInternalErrorRaisingAdapter(RegistryAdapter):
    """Adapter that raises internal RegistryAdapterContractError with secret strings from protected hook."""

    @property
    def source_descriptor(self) -> RegistrySourceDescriptor:
        return RegistrySourceDescriptor(
            source_id="INTERNAL_ERROR_SRC",
            adapter_version="1.0.0",
            supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
        )

    async def _lookup_professional(
        self, request: ProfessionalLookupRequest
    ) -> RegistryObservation:
        raise RegistryAdapterContractError("TOKEN=SUPER_SECRET_INTERNAL_TOKEN")

    async def _lookup_facility(
        self, request: FacilityLookupRequest
    ) -> RegistryObservation:
        raise AssertionError("Not called")


@pytest.mark.asyncio
async def test_protected_hook_registry_adapter_error_sanitization() -> None:
    """Any Exception raised inside a protected hook is sanitized to prevent secret leaks (Items 7 & 8)."""
    adapter = MockInternalErrorRaisingAdapter()
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTHORITY",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    with pytest.raises(RegistryAdapterContractError) as exc_info:
        await adapter.lookup_professional(req)

    assert exc_info.value.message == "Registry adapter unexpected execution error"
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None

    tb_lines = traceback.format_exception(exc_info.type, exc_info.value, exc_info.tb)
    tb_text = "".join(tb_lines)
    assert "SUPER_SECRET_INTERNAL_TOKEN" not in tb_text


class MockCrashingDescriptorAdapter(RegistryAdapter):
    """Adapter whose source_descriptor raises an exception with secrets."""

    @property
    def source_descriptor(self) -> RegistrySourceDescriptor:
        raise RuntimeError("SECRET_DESCRIPTOR_CREDENTIAL_LEAK")

    async def _lookup_professional(
        self, request: ProfessionalLookupRequest
    ) -> RegistryObservation:
        raise AssertionError("Not called")

    async def _lookup_facility(
        self, request: FacilityLookupRequest
    ) -> RegistryObservation:
        raise AssertionError("Not called")


@pytest.mark.asyncio
async def test_descriptor_resolution_exception_sanitization() -> None:
    """Descriptor property exceptions are sanitized and leak no secrets into traceback (Item 5)."""
    adapter = MockCrashingDescriptorAdapter()
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTHORITY",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    with pytest.raises(RegistryAdapterContractError) as exc_info:
        await adapter.lookup_professional(req)

    assert exc_info.value.message == "Registry adapter descriptor resolution failed"
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None

    tb_lines = traceback.format_exception(exc_info.type, exc_info.value, exc_info.tb)
    tb_text = "".join(tb_lines)
    assert "SECRET_DESCRIPTOR_CREDENTIAL_LEAK" not in tb_text


class MockDescriptorCounterAdapter(RegistryAdapter):
    """Adapter that counts property accesses and mutates descriptor on each call."""

    def __init__(self) -> None:
        self.access_count = 0

    @property
    def source_descriptor(self) -> RegistrySourceDescriptor:
        self.access_count += 1
        return RegistrySourceDescriptor(
            source_id=f"SRC_{self.access_count}",
            adapter_version="1.0.0",
            supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
        )

    async def _lookup_professional(
        self, request: ProfessionalLookupRequest
    ) -> RegistryObservation:
        return RegistryObservation(
            resource_type=RegistryResourceType.PROFESSIONAL,
            source_id="SRC_1",
            adapter_version="1.0.0",
            observed_at=datetime.now(timezone.utc),
            lookup_purpose=request.lookup_purpose,
            outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        )

    async def _lookup_facility(
        self, request: FacilityLookupRequest
    ) -> RegistryObservation:
        raise AssertionError("Not called")


@pytest.mark.asyncio
async def test_descriptor_resolved_exactly_once_per_invocation() -> None:
    """Public lookup resolves source_descriptor exactly once, freezing identity for the call (Items 4 & 5)."""
    adapter = MockDescriptorCounterAdapter()
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTHORITY",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    obs = await adapter.lookup_professional(req)
    assert obs.source_id == "SRC_1"
    assert adapter.access_count == 1


class MockInvalidDescriptorTypeAdapter(RegistryAdapter):
    """Adapter returning non-RegistrySourceDescriptor values."""

    def __init__(self, bad_value: object) -> None:
        self._bad_value = bad_value

    @property
    def source_descriptor(self) -> RegistrySourceDescriptor:
        return self._bad_value  # type: ignore[return-value]

    async def _lookup_professional(
        self, request: ProfessionalLookupRequest
    ) -> RegistryObservation:
        raise AssertionError("Not called")

    async def _lookup_facility(
        self, request: FacilityLookupRequest
    ) -> RegistryObservation:
        raise AssertionError("Not called")


@pytest.mark.asyncio
async def test_invalid_descriptor_type_fails_closed() -> None:
    """Public lookup fails closed with safe message when source_descriptor returns invalid type (Item 6)."""
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTHORITY",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    for bad_val in (None, {}, "string_descriptor", 12345):
        adapter = MockInvalidDescriptorTypeAdapter(bad_val)
        with pytest.raises(RegistryAdapterContractError) as exc_info:
            await adapter.lookup_professional(req)
        assert exc_info.value.message == "Registry adapter descriptor resolution failed"


def test_validate_observation_provenance_helper() -> None:
    """validate_observation_provenance helper verifies observation against descriptor."""
    desc = RegistrySourceDescriptor(
        source_id="SRC_ALPHA",
        adapter_version="1.0.0",
        supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
    )
    obs = RegistryObservation(
        resource_type=RegistryResourceType.PROFESSIONAL,
        source_id="SRC_ALPHA",
        adapter_version="1.0.0",
        observed_at=datetime.now(timezone.utc),
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
    )
    # Passes
    validate_observation_provenance(
        observation=obs,
        descriptor=desc,
        expected_resource_type=RegistryResourceType.PROFESSIONAL,
    )

    # Fails source_id
    bad_source_obs = RegistryObservation(
        resource_type=RegistryResourceType.PROFESSIONAL,
        source_id="SRC_BETA",
        adapter_version="1.0.0",
        observed_at=datetime.now(timezone.utc),
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
    )
    with pytest.raises(
        RegistryAdapterContractError, match="source provenance mismatch"
    ):
        validate_observation_provenance(
            observation=bad_source_obs,
            descriptor=desc,
            expected_resource_type=RegistryResourceType.PROFESSIONAL,
        )

    # Fails non-observation
    with pytest.raises(RegistryAdapterContractError, match="invalid observation type"):
        validate_observation_provenance(
            observation="not_an_observation",  # type: ignore[arg-type]
            descriptor=desc,
            expected_resource_type=RegistryResourceType.PROFESSIONAL,
        )


# ---------------------------------------------------------------------------
# Synthetic Registry Adapter Full Flow Tests (Gate 19)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthetic_adapter_full_flow() -> None:
    """SyntheticRegistryAdapter deterministically produces professional and facility observations."""
    fixed_clock = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    adapter = SyntheticRegistryAdapter(
        clock=fixed_clock,
        default_professional_outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        default_facility_outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        default_binding_result=VerificationIdentityBindingResult.MATCHED,
    )

    prof_req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTHORITY",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    prof_obs = await adapter.lookup_professional(prof_req)
    assert prof_obs.resource_type == RegistryResourceType.PROFESSIONAL
    assert prof_obs.source_id == "SYNTHETIC_REGISTRY"
    assert prof_obs.adapter_version == "1.0.0-synthetic"
    assert prof_obs.observed_at == fixed_clock
    assert prof_obs.outcome == VerificationEvidenceOutcome.CONFIRMED_ACTIVE
    assert prof_obs.identity_binding_result == VerificationIdentityBindingResult.MATCHED
    assert prof_obs.response_digest is not None
    assert len(prof_obs.response_digest) == 64

    fac_req = FacilityLookupRequest(
        registration_authority_code="TEST_FACILITY_AUTHORITY",
        registration_number_normalized="FAC500",
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    fac_obs = await adapter.lookup_facility(fac_req)
    assert fac_obs.resource_type == RegistryResourceType.FACILITY
    assert fac_obs.source_id == "SYNTHETIC_REGISTRY"
    assert fac_obs.adapter_version == "1.0.0-synthetic"
    assert fac_obs.observed_at == fixed_clock
    assert fac_obs.outcome == VerificationEvidenceOutcome.CONFIRMED_ACTIVE
    assert fac_obs.identity_binding_result == VerificationIdentityBindingResult.MATCHED
    assert fac_obs.response_digest is not None


@pytest.mark.asyncio
async def test_synthetic_adapter_deterministic_outcomes() -> None:
    """SyntheticRegistryAdapter exercises all closed outcomes."""
    for outcome in VerificationEvidenceOutcome:
        adapter = SyntheticRegistryAdapter(default_professional_outcome=outcome)
        req = ProfessionalLookupRequest(
            registration_authority_code="TEST_PROF_AUTHORITY",
            registration_number_normalized="REG1001",
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        )
        obs = await adapter.lookup_professional(req)
        assert obs.outcome == outcome


@pytest.mark.asyncio
async def test_synthetic_adapter_identity_bindings() -> None:
    """SyntheticRegistryAdapter exercises all closed identity binding results."""
    for binding in VerificationIdentityBindingResult:
        adapter = SyntheticRegistryAdapter(default_binding_result=binding)
        req = ProfessionalLookupRequest(
            registration_authority_code="TEST_PROF_AUTHORITY",
            registration_number_normalized="REG1001",
            lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        )
        obs = await adapter.lookup_professional(req)
        assert obs.identity_binding_result == binding


@pytest.mark.asyncio
async def test_synthetic_adapter_overrides() -> None:
    """SyntheticRegistryAdapter supports observation overrides for deterministic edge testing."""
    override_obs = RegistryObservation(
        resource_type=RegistryResourceType.PROFESSIONAL,
        source_id="SYNTHETIC_REGISTRY",
        adapter_version="1.0.0-synthetic",
        observed_at=datetime.now(timezone.utc),
        lookup_purpose=VerificationEvidenceLookupPurpose.ADVERSE_SIGNAL_CHECK,
        outcome=VerificationEvidenceOutcome.CONFIRMED_INACTIVE,
    )
    adapter = SyntheticRegistryAdapter(
        override_observations={
            "PROFESSIONAL:TEST_PROF_AUTHORITY:REG9999": override_obs,
        }
    )
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTHORITY",
        registration_number_normalized="REG9999",
        lookup_purpose=VerificationEvidenceLookupPurpose.ADVERSE_SIGNAL_CHECK,
    )
    obs = await adapter.lookup_professional(req)
    assert obs.outcome == VerificationEvidenceOutcome.CONFIRMED_INACTIVE
    assert obs.lookup_purpose == VerificationEvidenceLookupPurpose.ADVERSE_SIGNAL_CHECK


# ---------------------------------------------------------------------------
# AST Architectural Isolation Guard (Gate 18)
# ---------------------------------------------------------------------------


def test_ast_architectural_isolation() -> None:
    """AST analysis verifies that provider_verification_registry has zero forbidden dependencies."""
    target_path = (
        Path(__file__).parent.parent
        / "app"
        / "services"
        / "provider_verification_registry.py"
    )
    assert target_path.is_file()

    tree = ast.parse(target_path.read_text(encoding="utf-8"))

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_modules.add(node.module.split(".")[0])

    # Strictly forbidden module categories
    forbidden_modules = {
        # Networking / HTTP
        "httpx",
        "requests",
        "urllib",
        "aiohttp",
        "socket",
        "http",
        # Database / ORM
        "sqlalchemy",
        "sqlmodel",
        "psycopg2",
        "asyncpg",
        "alembic",
        # Caching
        "redis",
        "aioredis",
        # Web / Framework
        "fastapi",
        "starlette",
    }

    violation = imported_modules & forbidden_modules
    assert (
        not violation
    ), f"Forbidden modules imported in provider_verification_registry.py: {violation}"
