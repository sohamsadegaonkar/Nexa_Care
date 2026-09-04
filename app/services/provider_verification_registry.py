"""Pure fail-closed registry adapter contract and normalized observation boundary.

Slice 5 Phase 5C defines:
- Server-owned registry adapter descriptors and closed resource vocabulary
- Normalized request contracts for professional and facility lookup
- Normalized immutable observation results matching the 5B evidence schema
- Response digest calculation for response-integrity provenance
- Closed outcome and identity-binding vocabularies
- Public template-method adapter boundary enforcing provenance and purpose validation by construction
- Deterministic synthetic adapter for testing without network/DB/Redis

Permanent authority invariant:
    REGISTRY LOOKUP REQUEST
    != REGISTRY OBSERVATION
    != VERIFICATION EVIDENCE RECORD
    != LIFECYCLE DECISION
    != SYSTEM AUTOMATION AUTHORITY
    != CLINICAL AUTHORITY

A RegistryObservation is pure observed data; it never confers authority.
"""

from __future__ import annotations

import enum
import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import ClassVar

from app.models.provider import (
    VerificationEvidenceLookupPurpose,
    VerificationEvidenceOutcome,
    VerificationIdentityBindingResult,
)

PROVIDER_VERIFICATION_REGISTRY_CONTRACT_VERSION = "provider-verification-registry/1.0"

_MAX_IDENTIFIER_LEN = 64
_MAX_REGISTRATION_NUMBER_LEN = 128
_MAX_AUTHORITY_CODE_LEN = 64
_MAX_REFERENCE_LEN = 255
_MAX_METHOD_LEN = 64
_MAX_TRANSACTION_ID_LEN = 128

_CANONICAL_TOKEN_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_.-]{0,63}$")
_CANONICAL_PROF_REG_PATTERN = re.compile(r"^[A-Z0-9/]{1,128}$")
_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_FUTURE_TOLERANCE_SECONDS = 300  # 5 minutes clock skew tolerance


class RegistryResourceType(str, enum.Enum):
    """Closed vocabulary of external registry verification targets."""

    PROFESSIONAL = "PROFESSIONAL"
    FACILITY = "FACILITY"


# ---------------------------------------------------------------------------
# Sanitized Error Model (Closed Class-Owned Error Codes)
# ---------------------------------------------------------------------------


class RegistryAdapterError(Exception):
    """Base exception for all registry adapter failures."""

    error_code: ClassVar[str] = "REGISTRY_ADAPTER_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return f"[{self.error_code}] {self.message}"


class RegistryAdapterContractError(RegistryAdapterError):
    """Programming or configuration contract violation.

    Raised when an adapter returns a response violating the contract, such as
    provenance mismatches, purpose mutation, invalid output shapes, or unimplemented operations.
    """

    error_code: ClassVar[str] = "REGISTRY_CONTRACT_ERROR"


class RegistryUnsupportedResourceError(RegistryAdapterContractError):
    """Raised when an adapter is invoked for an unsupported resource type."""

    error_code: ClassVar[str] = "REGISTRY_UNSUPPORTED_RESOURCE"


class RegistryRequestInvalidError(RegistryAdapterError):
    """Raised when a lookup request fails normalization or boundary validation."""

    error_code: ClassVar[str] = "REGISTRY_REQUEST_INVALID"


class RegistryObservationInvalidError(RegistryAdapterContractError):
    """Raised when an observation fails normalization, integrity, or bounds validation."""

    error_code: ClassVar[str] = "REGISTRY_OBSERVATION_INVALID"


# ---------------------------------------------------------------------------
# Server-Owned Source Descriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegistrySourceDescriptor:
    """Immutable, server-owned descriptor of an authoritative registry source.

    Must never contain raw API credentials, bearer tokens, or client secrets.
    """

    source_id: str
    adapter_version: str
    supported_resource_types: tuple[RegistryResourceType, ...]
    authority_namespace: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not _CANONICAL_TOKEN_PATTERN.match(
            self.source_id
        ):
            raise RegistryAdapterContractError(
                "source_id must be a canonical uppercase alphanumeric token (up to 64 chars)"
            )
        if (
            not isinstance(self.adapter_version, str)
            or not self.adapter_version.strip()
            or len(self.adapter_version) > _MAX_IDENTIFIER_LEN
            or any(c < " " or c == "\x7f" for c in self.adapter_version)
            or self.adapter_version != self.adapter_version.strip()
        ):
            raise RegistryAdapterContractError(
                "adapter_version must be a bounded canonical string without control characters"
            )
        if not self.supported_resource_types:
            raise RegistryAdapterContractError(
                "supported_resource_types must contain at least one RegistryResourceType"
            )
        for rt in self.supported_resource_types:
            if not isinstance(rt, RegistryResourceType):
                raise RegistryAdapterContractError(
                    "Invalid resource type in supported_resource_types"
                )
        if self.authority_namespace is not None:
            if not isinstance(
                self.authority_namespace, str
            ) or not _CANONICAL_TOKEN_PATTERN.match(self.authority_namespace):
                raise RegistryAdapterContractError(
                    "authority_namespace must be a canonical uppercase alphanumeric token (up to 64 chars)"
                )


# ---------------------------------------------------------------------------
# Lookup Requests
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProfessionalLookupRequest:
    """Immutable normalized request for an external professional registration check.

    Accepts already-normalized parameters. Must never carry provider credentials,
    sessions, tokens, permissions, capabilities, or patient PII.
    """

    registration_authority_code: str
    registration_number_normalized: str
    lookup_purpose: VerificationEvidenceLookupPurpose

    def __post_init__(self) -> None:
        if not isinstance(
            self.registration_authority_code, str
        ) or not _CANONICAL_TOKEN_PATTERN.match(self.registration_authority_code):
            raise RegistryRequestInvalidError(
                "registration_authority_code must be already-normalized canonical token (up to 64 chars)"
            )
        if not isinstance(
            self.registration_number_normalized, str
        ) or not _CANONICAL_PROF_REG_PATTERN.match(self.registration_number_normalized):
            raise RegistryRequestInvalidError(
                "registration_number_normalized must be already-normalized canonical professional registration (uppercase alphanumeric/slash, up to 128 chars)"
            )
        if not isinstance(self.lookup_purpose, VerificationEvidenceLookupPurpose):
            raise RegistryRequestInvalidError(
                "lookup_purpose must be a valid VerificationEvidenceLookupPurpose"
            )


@dataclass(frozen=True, slots=True)
class FacilityLookupRequest:
    """Immutable normalized request for an external facility registration check.

    Accepts already-normalized parameters. External verification is keyed on
    authoritative registration pair, never Nexa's internal hospital facility_code.
    """

    registration_authority_code: str
    registration_number_normalized: str
    lookup_purpose: VerificationEvidenceLookupPurpose

    def __post_init__(self) -> None:
        if not isinstance(
            self.registration_authority_code, str
        ) or not _CANONICAL_TOKEN_PATTERN.match(self.registration_authority_code):
            raise RegistryRequestInvalidError(
                "registration_authority_code must be already-normalized canonical token (up to 64 chars)"
            )
        if (
            not isinstance(self.registration_number_normalized, str)
            or not self.registration_number_normalized.strip()
            or len(self.registration_number_normalized) > _MAX_REGISTRATION_NUMBER_LEN
            or self.registration_number_normalized
            != self.registration_number_normalized.strip()
            or any(c < " " or c == "\x7f" for c in self.registration_number_normalized)
        ):
            raise RegistryRequestInvalidError(
                "registration_number_normalized must be already-normalized non-empty bounded string without whitespace or control characters"
            )
        if not isinstance(self.lookup_purpose, VerificationEvidenceLookupPurpose):
            raise RegistryRequestInvalidError(
                "lookup_purpose must be a valid VerificationEvidenceLookupPurpose"
            )


# ---------------------------------------------------------------------------
# Response Integrity Digest Helper
# ---------------------------------------------------------------------------


def compute_response_digest(response_bytes: bytes) -> str:
    """Compute the canonical lowercase SHA-256 hex digest over exact upstream response bytes.

    This helper provides integrity and tamper-evidence provenance only.
    It is not encryption, anonymization, or authentication by itself.
    """
    if not isinstance(response_bytes, (bytes, bytearray)):
        raise RegistryAdapterContractError("response_bytes must be bytes or bytearray")
    return hashlib.sha256(response_bytes).hexdigest()


# ---------------------------------------------------------------------------
# Normalized Observation Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegistryObservation:
    """Immutable normalized observation returned by a registry adapter.

    Contains pure facts observed from an authoritative source or synthetic test harness.
    Must never contain raw response payloads, full headers, cookies, tokens, or authority fields.
    Timezone-aware datetimes are automatically normalized to UTC on creation.
    """

    resource_type: RegistryResourceType
    source_id: str
    adapter_version: str
    observed_at: datetime
    lookup_purpose: VerificationEvidenceLookupPurpose
    outcome: VerificationEvidenceOutcome
    source_record_reference: str | None = None
    observed_valid_from: datetime | None = None
    observed_valid_until: datetime | None = None
    identity_binding_result: VerificationIdentityBindingResult = (
        VerificationIdentityBindingResult.NOT_EVALUATED
    )
    binding_method: str | None = None
    response_digest: str | None = None
    external_transaction_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.resource_type, RegistryResourceType):
            raise RegistryObservationInvalidError(
                "resource_type must be a valid RegistryResourceType"
            )
        if not isinstance(self.source_id, str) or not _CANONICAL_TOKEN_PATTERN.match(
            self.source_id
        ):
            raise RegistryObservationInvalidError("source_id must be a canonical token")
        if (
            not isinstance(self.adapter_version, str)
            or not self.adapter_version.strip()
            or len(self.adapter_version) > _MAX_IDENTIFIER_LEN
            or self.adapter_version != self.adapter_version.strip()
            or any(c < " " or c == "\x7f" for c in self.adapter_version)
        ):
            raise RegistryObservationInvalidError(
                "adapter_version must be a bounded canonical string"
            )

        if not isinstance(self.observed_at, datetime):
            raise RegistryObservationInvalidError("observed_at must be a datetime")
        if self.observed_at.tzinfo is None:
            raise RegistryObservationInvalidError("observed_at must be timezone-aware")

        # UTC normalization
        norm_observed_at = self.observed_at.astimezone(timezone.utc)
        object.__setattr__(self, "observed_at", norm_observed_at)

        now_utc = datetime.now(timezone.utc)
        if (
            norm_observed_at > now_utc
            and (norm_observed_at - now_utc).total_seconds() > _FUTURE_TOLERANCE_SECONDS
        ):
            raise RegistryObservationInvalidError(
                "observed_at cannot exceed future clock-skew tolerance"
            )

        if not isinstance(self.lookup_purpose, VerificationEvidenceLookupPurpose):
            raise RegistryObservationInvalidError(
                "lookup_purpose must be a valid VerificationEvidenceLookupPurpose"
            )
        if not isinstance(self.outcome, VerificationEvidenceOutcome):
            raise RegistryObservationInvalidError(
                "outcome must be a valid VerificationEvidenceOutcome"
            )
        if not isinstance(
            self.identity_binding_result, VerificationIdentityBindingResult
        ):
            raise RegistryObservationInvalidError(
                "identity_binding_result must be a valid VerificationIdentityBindingResult"
            )

        if self.source_record_reference is not None:
            if (
                not isinstance(self.source_record_reference, str)
                or not self.source_record_reference.strip()
                or self.source_record_reference != self.source_record_reference.strip()
                or len(self.source_record_reference) > _MAX_REFERENCE_LEN
                or any(c < " " or c == "\x7f" for c in self.source_record_reference)
            ):
                raise RegistryObservationInvalidError(
                    "source_record_reference must be a non-empty, trimmed string without control characters (up to 255 chars)"
                )

        if self.binding_method is not None:
            if not isinstance(
                self.binding_method, str
            ) or not _CANONICAL_TOKEN_PATTERN.match(self.binding_method):
                raise RegistryObservationInvalidError(
                    "binding_method must be a canonical token (up to 64 chars)"
                )

        if self.external_transaction_id is not None:
            if (
                not isinstance(self.external_transaction_id, str)
                or not self.external_transaction_id.strip()
                or self.external_transaction_id != self.external_transaction_id.strip()
                or len(self.external_transaction_id) > _MAX_TRANSACTION_ID_LEN
                or any(c < " " or c == "\x7f" for c in self.external_transaction_id)
            ):
                raise RegistryObservationInvalidError(
                    "external_transaction_id must be a non-empty, trimmed string without control characters (up to 128 chars)"
                )

        if self.response_digest is not None:
            if not isinstance(
                self.response_digest, str
            ) or not _SHA256_HEX_PATTERN.match(self.response_digest):
                raise RegistryObservationInvalidError(
                    "response_digest must be a 64-character lowercase hexadecimal SHA-256 string"
                )

        if self.observed_valid_from is not None:
            if (
                not isinstance(self.observed_valid_from, datetime)
                or self.observed_valid_from.tzinfo is None
            ):
                raise RegistryObservationInvalidError(
                    "observed_valid_from must be timezone-aware"
                )
            norm_valid_from = self.observed_valid_from.astimezone(timezone.utc)
            object.__setattr__(self, "observed_valid_from", norm_valid_from)

        if self.observed_valid_until is not None:
            if (
                not isinstance(self.observed_valid_until, datetime)
                or self.observed_valid_until.tzinfo is None
            ):
                raise RegistryObservationInvalidError(
                    "observed_valid_until must be timezone-aware"
                )
            norm_valid_until = self.observed_valid_until.astimezone(timezone.utc)
            object.__setattr__(self, "observed_valid_until", norm_valid_until)

        if (
            self.observed_valid_from is not None
            and self.observed_valid_until is not None
        ):
            if self.observed_valid_until < self.observed_valid_from:
                raise RegistryObservationInvalidError(
                    "observed_valid_until cannot be earlier than observed_valid_from"
                )


# ---------------------------------------------------------------------------
# Provenance Validation Helper
# ---------------------------------------------------------------------------


def validate_observation_provenance(
    *,
    observation: RegistryObservation,
    descriptor: RegistrySourceDescriptor,
    expected_resource_type: RegistryResourceType,
) -> None:
    """Validate that an observation matches its source descriptor and expected resource type.

    This helper provides low-level provenance assertion. Public callers must use
    RegistryAdapter.lookup_professional or lookup_facility where this validation
    is performed automatically by construction.
    """
    if not isinstance(observation, RegistryObservation):
        raise RegistryAdapterContractError(
            "Registry adapter returned invalid observation type"
        )
    if observation.source_id != descriptor.source_id:
        raise RegistryAdapterContractError(
            "Registry observation source provenance mismatch"
        )
    if observation.adapter_version != descriptor.adapter_version:
        raise RegistryAdapterContractError(
            "Registry observation adapter version provenance mismatch"
        )
    if observation.resource_type != expected_resource_type:
        raise RegistryAdapterContractError(
            "Registry observation resource type mismatch"
        )


# ---------------------------------------------------------------------------
# Abstract Adapter Interface (Public Template Method Architecture)
# ---------------------------------------------------------------------------


class RegistryAdapter(ABC):
    """Abstract fail-closed contract for external verification registry sources.

    Public methods lookup_professional and lookup_facility enforce:
    - Pre-invocation resource support checks
    - Protected concrete implementation invocation
    - Automatic returned observation shape, provenance, and purpose cross-validation
    - Sanitized exception handling for unexpected concrete errors
    """

    @property
    @abstractmethod
    def source_descriptor(self) -> RegistrySourceDescriptor:
        """Return the immutable server-owned source descriptor."""
        ...

    def _resolve_source_descriptor(self) -> RegistrySourceDescriptor:
        """Safely resolve the source descriptor exactly once, sanitizing any errors."""
        failed = False
        raw_descriptor: RegistrySourceDescriptor | None = None
        try:
            raw_descriptor = self.source_descriptor
        except Exception:
            failed = True

        if failed or not isinstance(raw_descriptor, RegistrySourceDescriptor):
            raise RegistryAdapterContractError(
                "Registry adapter descriptor resolution failed"
            )

        return raw_descriptor

    async def lookup_professional(
        self, request: ProfessionalLookupRequest
    ) -> RegistryObservation:
        """Public fail-closed entry point for professional registration lookup."""
        descriptor = self._resolve_source_descriptor()
        if RegistryResourceType.PROFESSIONAL not in descriptor.supported_resource_types:
            raise RegistryUnsupportedResourceError(
                "Registry adapter does not support professional verification"
            )

        failed = False
        observation: RegistryObservation | None = None
        try:
            observation = await self._lookup_professional(request)
        except Exception:
            failed = True

        if failed or observation is None:
            raise RegistryAdapterContractError(
                "Registry adapter unexpected execution error"
            )

        self._validate_returned_observation(
            observation=observation,
            descriptor=descriptor,
            expected_resource_type=RegistryResourceType.PROFESSIONAL,
            expected_lookup_purpose=request.lookup_purpose,
        )
        return observation

    async def lookup_facility(
        self, request: FacilityLookupRequest
    ) -> RegistryObservation:
        """Public fail-closed entry point for facility registration lookup."""
        descriptor = self._resolve_source_descriptor()
        if RegistryResourceType.FACILITY not in descriptor.supported_resource_types:
            raise RegistryUnsupportedResourceError(
                "Registry adapter does not support facility verification"
            )

        failed = False
        observation: RegistryObservation | None = None
        try:
            observation = await self._lookup_facility(request)
        except Exception:
            failed = True

        if failed or observation is None:
            raise RegistryAdapterContractError(
                "Registry adapter unexpected execution error"
            )

        self._validate_returned_observation(
            observation=observation,
            descriptor=descriptor,
            expected_resource_type=RegistryResourceType.FACILITY,
            expected_lookup_purpose=request.lookup_purpose,
        )
        return observation

    def _validate_returned_observation(
        self,
        *,
        observation: RegistryObservation,
        descriptor: RegistrySourceDescriptor,
        expected_resource_type: RegistryResourceType,
        expected_lookup_purpose: VerificationEvidenceLookupPurpose,
    ) -> None:
        """Structural validation of observation returned from protected implementation."""
        validate_observation_provenance(
            observation=observation,
            descriptor=descriptor,
            expected_resource_type=expected_resource_type,
        )
        if observation.lookup_purpose != expected_lookup_purpose:
            raise RegistryAdapterContractError(
                "Registry observation lookup purpose mismatch"
            )

    @abstractmethod
    async def _lookup_professional(
        self, request: ProfessionalLookupRequest
    ) -> RegistryObservation:
        """Protected adapter hook for professional lookup."""
        ...

    @abstractmethod
    async def _lookup_facility(
        self, request: FacilityLookupRequest
    ) -> RegistryObservation:
        """Protected adapter hook for facility lookup."""
        ...


# ---------------------------------------------------------------------------
# Deterministic Synthetic Adapter (Strictly for testing)
# ---------------------------------------------------------------------------


class SyntheticRegistryAdapter(RegistryAdapter):
    """Deterministic synthetic test adapter with in-memory outcome configuration.

    Exercises all closed outcomes and identity binding variations without network I/O,
    credentials, or database interaction.
    """

    def __init__(
        self,
        descriptor: RegistrySourceDescriptor | None = None,
        *,
        default_professional_outcome: VerificationEvidenceOutcome = VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        default_facility_outcome: VerificationEvidenceOutcome = VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        default_binding_result: VerificationIdentityBindingResult = VerificationIdentityBindingResult.MATCHED,
        simulated_response_bytes: bytes
        | None = b'{"status": "ACTIVE", "reg": "SYNTHETIC-001"}',
        override_observations: dict[str, RegistryObservation] | None = None,
        clock: datetime | None = None,
    ) -> None:
        self._descriptor = descriptor or RegistrySourceDescriptor(
            source_id="SYNTHETIC_REGISTRY",
            adapter_version="1.0.0-synthetic",
            supported_resource_types=(
                RegistryResourceType.PROFESSIONAL,
                RegistryResourceType.FACILITY,
            ),
            authority_namespace="SYNTHETIC",
        )
        self._default_professional_outcome = default_professional_outcome
        self._default_facility_outcome = default_facility_outcome
        self._default_binding_result = default_binding_result
        self._simulated_response_bytes = simulated_response_bytes
        self._override_observations = override_observations or {}
        self._clock = clock

    @property
    def source_descriptor(self) -> RegistrySourceDescriptor:
        return self._descriptor

    def _get_now(self) -> datetime:
        if self._clock is not None:
            return self._clock
        return datetime.now(timezone.utc)

    async def _lookup_professional(
        self, request: ProfessionalLookupRequest
    ) -> RegistryObservation:
        key = f"PROFESSIONAL:{request.registration_authority_code}:{request.registration_number_normalized}"
        if key in self._override_observations:
            return self._override_observations[key]

        digest = (
            compute_response_digest(self._simulated_response_bytes)
            if self._simulated_response_bytes is not None
            else None
        )

        return RegistryObservation(
            resource_type=RegistryResourceType.PROFESSIONAL,
            source_id=self._descriptor.source_id,
            adapter_version=self._descriptor.adapter_version,
            observed_at=self._get_now(),
            lookup_purpose=request.lookup_purpose,
            outcome=self._default_professional_outcome,
            source_record_reference=f"REF-{request.registration_authority_code}-{request.registration_number_normalized}",
            observed_valid_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
            observed_valid_until=datetime(2030, 1, 1, tzinfo=timezone.utc),
            identity_binding_result=self._default_binding_result,
            binding_method="SYNTHETIC_EXACT",
            response_digest=digest,
            external_transaction_id="TX-SYNTHETIC-PROF-001",
        )

    async def _lookup_facility(
        self, request: FacilityLookupRequest
    ) -> RegistryObservation:
        key = f"FACILITY:{request.registration_authority_code}:{request.registration_number_normalized}"
        if key in self._override_observations:
            return self._override_observations[key]

        digest = (
            compute_response_digest(self._simulated_response_bytes)
            if self._simulated_response_bytes is not None
            else None
        )

        return RegistryObservation(
            resource_type=RegistryResourceType.FACILITY,
            source_id=self._descriptor.source_id,
            adapter_version=self._descriptor.adapter_version,
            observed_at=self._get_now(),
            lookup_purpose=request.lookup_purpose,
            outcome=self._default_facility_outcome,
            source_record_reference=f"REF-{request.registration_authority_code}-{request.registration_number_normalized}",
            observed_valid_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
            observed_valid_until=datetime(2030, 1, 1, tzinfo=timezone.utc),
            identity_binding_result=self._default_binding_result,
            binding_method="SYNTHETIC_EXACT",
            response_digest=digest,
            external_transaction_id="TX-SYNTHETIC-FAC-001",
        )
