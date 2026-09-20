"""Read-only readiness checks for the first real medication-catalog release.

This module never creates, reviews, qualifies, activates, revokes, or otherwise
mutates catalog authority.  It exists to make the external 10B.5l release gate
measurable without embedding licensed terminology or weakening the catalog
application-service controls.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

from app.models.medication_catalog import (
    DrugScheduleClass,
    MedicationCatalogEmergencyDeny,
    MedicationCatalogEntry,
    MedicationCatalogEvidence,
    MedicationCatalogRelease,
    MedicationCatalogReleaseStatus,
    MedicationEvidenceAuthority,
    MedicationEvidenceDimension,
    MedicationEmergencyAction,
    NdpsClass,
    NexaHighRiskClass,
    RegulatoryProductStatus,
    SpecialRecordkeepingClass,
    TelemedicineClass,
    TerminologyConceptStatus,
)
from app.services.medication_catalog_manifest import (
    build_release_manifest_bytes,
    candidate_entry_digest,
    sha256_hex,
)
from app.services.medication_catalog_policy import (
    MEDICATION_CATALOG_POLICY_VERSION,
    MedicationCatalogEntryPolicyFacts,
    derive_v1_universal_allowed,
)
from app.services.medication_catalog_signing import (
    CATALOG_SIGNATURE_ALGORITHM,
    MedicationCatalogSigningError,
    MedicationCatalogSigningProvider,
    signature_from_text,
)

SOURCE_METADATA_SCHEMA = "nexa-medication-catalog-source-metadata/v1"
FIRST_RELEASE_POSITIVE_LIMIT = 20
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_SOURCE_KEYS = frozenset(
    {
        "schema",
        "terminology_authority",
        "package_name",
        "package_version",
        "package_release_date",
        "package_sha256",
        "official_source_reference",
        "licence_governance_reference",
        "checked_at",
    }
)


class MedicationCatalogReleaseReadinessError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class TerminologyPackageProvenance:
    schema: str
    terminology_authority: str
    package_name: str
    package_version: str
    package_release_date: date
    package_sha256: str
    official_source_reference: str
    licence_governance_reference: str
    checked_at: datetime


@dataclass(frozen=True, slots=True)
class ReleaseReadinessReport:
    release_id: UUID
    release_version: str
    release_status: str
    policy_version: str
    source_terminology_version: str
    source_cutoff_at: datetime
    entry_count: int
    candidate_positive_count: int
    persisted_positive_count: int
    positive_limit_ok: bool
    positive_present: bool
    reviews_and_policy_ok: bool
    identity_package_binding_ok: bool
    package_digest_verified: bool
    manifest_projection_ok: bool
    signer_ready: bool
    signature_verified: bool
    emergency_compatibility_ok: bool
    active_release_count: int
    active_uniqueness_ok: bool
    qualification_ready: bool
    activation_ready: bool

    def to_sanitized_dict(self) -> dict[str, object]:
        """Return value-safe structural evidence only."""

        return {
            "release_id": str(self.release_id),
            "release_version": self.release_version,
            "release_status": self.release_status,
            "policy_version": self.policy_version,
            "source_terminology_version": self.source_terminology_version,
            "source_cutoff_at": self.source_cutoff_at.isoformat(),
            "entry_count": self.entry_count,
            "candidate_positive_count": self.candidate_positive_count,
            "persisted_positive_count": self.persisted_positive_count,
            "positive_limit_ok": self.positive_limit_ok,
            "positive_present": self.positive_present,
            "reviews_and_policy_ok": self.reviews_and_policy_ok,
            "identity_package_binding_ok": self.identity_package_binding_ok,
            "package_digest_verified": self.package_digest_verified,
            "manifest_projection_ok": self.manifest_projection_ok,
            "signer_ready": self.signer_ready,
            "signature_verified": self.signature_verified,
            "emergency_compatibility_ok": self.emergency_compatibility_ok,
            "active_release_count": self.active_release_count,
            "active_uniqueness_ok": self.active_uniqueness_ok,
            "qualification_ready": self.qualification_ready,
            "activation_ready": self.activation_ready,
        }


def _bounded_text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise MedicationCatalogReleaseReadinessError("SOURCE_METADATA_INVALID")
    clean = value.strip()
    if not clean or len(clean) > maximum or "\x00" in clean:
        raise MedicationCatalogReleaseReadinessError("SOURCE_METADATA_INVALID")
    return clean


def _aware_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise MedicationCatalogReleaseReadinessError("SOURCE_METADATA_INVALID")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MedicationCatalogReleaseReadinessError(
            "SOURCE_METADATA_INVALID"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MedicationCatalogReleaseReadinessError("SOURCE_METADATA_INVALID")
    return parsed.astimezone(timezone.utc)


def parse_source_metadata(payload: Mapping[str, object]) -> TerminologyPackageProvenance:
    """Parse strict metadata without accepting licensed terminology content."""

    if not isinstance(payload, Mapping) or set(payload) != _SOURCE_KEYS:
        raise MedicationCatalogReleaseReadinessError("SOURCE_METADATA_INVALID")
    schema = _bounded_text(payload["schema"], maximum=64)
    if schema != SOURCE_METADATA_SCHEMA:
        raise MedicationCatalogReleaseReadinessError("SOURCE_METADATA_UNSUPPORTED")
    authority = _bounded_text(payload["terminology_authority"], maximum=32)
    if authority != MedicationEvidenceAuthority.NRCES.value:
        raise MedicationCatalogReleaseReadinessError("SOURCE_AUTHORITY_INVALID")
    try:
        release_date = date.fromisoformat(
            _bounded_text(payload["package_release_date"], maximum=10)
        )
    except ValueError as exc:
        raise MedicationCatalogReleaseReadinessError(
            "SOURCE_METADATA_INVALID"
        ) from exc
    digest = _bounded_text(payload["package_sha256"], maximum=64)
    if not _SHA256_RE.fullmatch(digest):
        raise MedicationCatalogReleaseReadinessError("SOURCE_DIGEST_INVALID")
    checked_at = _aware_datetime(payload["checked_at"])
    if checked_at.date() < release_date:
        raise MedicationCatalogReleaseReadinessError("SOURCE_METADATA_INVALID")

    return TerminologyPackageProvenance(
        schema=schema,
        terminology_authority=authority,
        package_name=_bounded_text(payload["package_name"], maximum=128),
        package_version=_bounded_text(payload["package_version"], maximum=128),
        package_release_date=release_date,
        package_sha256=digest,
        official_source_reference=_bounded_text(
            payload["official_source_reference"], maximum=255
        ),
        licence_governance_reference=_bounded_text(
            payload["licence_governance_reference"], maximum=128
        ),
        checked_at=checked_at,
    )


def sha256_file(path: Path) -> str:
    """Stream a local protected package and return its SHA-256 without copying it."""

    if not isinstance(path, Path) or not path.is_file():
        raise MedicationCatalogReleaseReadinessError("SOURCE_PACKAGE_UNAVAILABLE")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_package_digest(
    provenance: TerminologyPackageProvenance,
    package_path: Path,
) -> bool:
    actual = sha256_file(package_path)
    if actual != provenance.package_sha256:
        raise MedicationCatalogReleaseReadinessError("SOURCE_DIGEST_MISMATCH")
    return True


def _enum(enum_type, value):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise MedicationCatalogReleaseReadinessError(
            "CATALOG_CLASSIFICATION_INVALID"
        ) from exc


def _policy_facts(
    release: MedicationCatalogRelease,
    entry: MedicationCatalogEntry,
    rows: Sequence[MedicationCatalogEvidence],
) -> tuple[MedicationCatalogEntryPolicyFacts, str]:
    digest = candidate_entry_digest(
        entry,
        rows,
        policy_version=release.policy_version,
    )
    dimensions = frozenset(
        _enum(MedicationEvidenceDimension, row.finding_dimension) for row in rows
    )
    facts = MedicationCatalogEntryPolicyFacts(
        terminology_status=_enum(TerminologyConceptStatus, entry.terminology_status),
        identity_granularity_sufficient=bool(entry.identity_granularity_sufficient),
        drug_schedule_class=_enum(DrugScheduleClass, entry.drug_schedule_class),
        ndps_class=_enum(NdpsClass, entry.ndps_class),
        telemedicine_class=_enum(TelemedicineClass, entry.telemedicine_class),
        special_recordkeeping_class=_enum(
            SpecialRecordkeepingClass, entry.special_recordkeeping_class
        ),
        nexa_high_risk_class=_enum(
            NexaHighRiskClass, entry.nexa_high_risk_class
        ),
        regulatory_product_status=_enum(
            RegulatoryProductStatus, entry.regulatory_product_status
        ),
        evidence_dimensions=dimensions,
        candidate_digest=digest,
        preparer_provider_id=release.prepared_by,
        first_reviewer_provider_id=entry.first_reviewer_provider_id,
        second_reviewer_provider_id=entry.second_reviewer_provider_id,
        first_review_digest=entry.first_review_digest,
        second_review_digest=entry.second_review_digest,
    )
    return facts, digest


def _identity_package_bound(
    rows: Sequence[MedicationCatalogEvidence],
    provenance: TerminologyPackageProvenance,
) -> bool:
    """Require exact release-version and package-digest identity evidence."""

    return any(
        row.finding_dimension == MedicationEvidenceDimension.IDENTITY.value
        and row.source_authority
        in {
            MedicationEvidenceAuthority.NRCES.value,
            MedicationEvidenceAuthority.SNOMED_IDENTITY_ONLY.value,
        }
        and row.source_document_version == provenance.package_version
        and row.evidence_sha256 == provenance.package_sha256
        for row in rows
    )


async def assess_release_readiness(
    *,
    release: MedicationCatalogRelease,
    entries: Sequence[MedicationCatalogEntry],
    evidence_by_entry: Mapping[UUID, Sequence[MedicationCatalogEvidence]],
    latest_emergency_by_code: Mapping[str, MedicationCatalogEmergencyDeny],
    active_release_count: int,
    provenance: TerminologyPackageProvenance,
    package_digest_verified: bool,
    signing_provider: MedicationCatalogSigningProvider | None,
) -> ReleaseReadinessReport:
    """Assess DRAFT/QUALIFIED release state without mutating any authority."""

    if release.policy_version != MEDICATION_CATALOG_POLICY_VERSION:
        raise MedicationCatalogReleaseReadinessError("POLICY_VERSION_UNSUPPORTED")
    if release.source_terminology_version != provenance.package_version:
        raise MedicationCatalogReleaseReadinessError(
            "TERMINOLOGY_VERSION_MISMATCH"
        )
    if provenance.package_release_date > release.source_cutoff_at.date():
        raise MedicationCatalogReleaseReadinessError("SOURCE_CUTOFF_INVALID")

    candidate_positive_codes: list[str] = []
    policy_consistent = True
    identity_binding_ok = True

    for entry in entries:
        rows = tuple(evidence_by_entry.get(entry.id, ()))
        facts, _ = _policy_facts(release, entry, rows)
        derived = derive_v1_universal_allowed(facts)
        persisted = bool(entry.v1_universal_allowed)

        if derived:
            candidate_positive_codes.append(entry.medication_code)
            if not _identity_package_bound(rows, provenance):
                identity_binding_ok = False

        if release.status != MedicationCatalogReleaseStatus.DRAFT.value:
            if persisted != derived:
                policy_consistent = False
        elif persisted:
            policy_consistent = False

    positive_count = len(candidate_positive_codes)
    persisted_positive_count = sum(
        1 for entry in entries if bool(entry.v1_universal_allowed)
    )
    positive_limit_ok = positive_count <= FIRST_RELEASE_POSITIVE_LIMIT
    positive_present = positive_count > 0

    emergency_ok = True
    for code in candidate_positive_codes:
        event = latest_emergency_by_code.get(code)
        if event is not None and event.action == MedicationEmergencyAction.DENY.value:
            emergency_ok = False
            break

    active_uniqueness_ok = 0 <= active_release_count <= 1

    manifest_ok = release.status == MedicationCatalogReleaseStatus.DRAFT.value
    signature_verified = False
    signer_ready = False
    if signing_provider is not None:
        try:
            await signing_provider.assert_ready()
            signer_ready = True
        except MedicationCatalogSigningError:
            signer_ready = False

    if release.status in {
        MedicationCatalogReleaseStatus.QUALIFIED.value,
        MedicationCatalogReleaseStatus.ACTIVE.value,
        MedicationCatalogReleaseStatus.SUPERSEDED.value,
        MedicationCatalogReleaseStatus.REVOKED.value,
    }:
        if (
            not release.integrity_digest
            or not release.canonical_manifest
            or not release.artifact_signature
            or not release.artifact_key_id
            or release.signature_algorithm != CATALOG_SIGNATURE_ALGORITHM
        ):
            manifest_ok = False
        else:
            manifest_bytes = build_release_manifest_bytes(
                release,
                entries,
                evidence_by_entry,
            )
            digest = sha256_hex(manifest_bytes)
            manifest_ok = (
                digest == release.integrity_digest
                and manifest_bytes == release.canonical_manifest.encode("utf-8")
            )
            if (
                signer_ready
                and signing_provider is not None
                and signing_provider.key_identifier == release.artifact_key_id
                and signing_provider.signature_algorithm
                == release.signature_algorithm
                and manifest_ok
            ):
                try:
                    signature = signature_from_text(release.artifact_signature)
                    signature_verified = await signing_provider.verify_release_signature(
                        bytes.fromhex(digest),
                        signature,
                    )
                except MedicationCatalogSigningError:
                    signature_verified = False

    common_ready = (
        package_digest_verified
        and positive_present
        and positive_limit_ok
        and policy_consistent
        and identity_binding_ok
        and emergency_ok
        and active_uniqueness_ok
        and signer_ready
    )
    qualification_ready = (
        release.status == MedicationCatalogReleaseStatus.DRAFT.value
        and common_ready
    )
    activation_ready = (
        release.status == MedicationCatalogReleaseStatus.QUALIFIED.value
        and common_ready
        and manifest_ok
        and signature_verified
    )

    return ReleaseReadinessReport(
        release_id=release.id,
        release_version=release.version,
        release_status=release.status,
        policy_version=release.policy_version,
        source_terminology_version=release.source_terminology_version,
        source_cutoff_at=release.source_cutoff_at,
        entry_count=len(entries),
        candidate_positive_count=positive_count,
        persisted_positive_count=persisted_positive_count,
        positive_limit_ok=positive_limit_ok,
        positive_present=positive_present,
        reviews_and_policy_ok=policy_consistent,
        identity_package_binding_ok=identity_binding_ok,
        package_digest_verified=package_digest_verified,
        manifest_projection_ok=manifest_ok,
        signer_ready=signer_ready,
        signature_verified=signature_verified,
        emergency_compatibility_ok=emergency_ok,
        active_release_count=active_release_count,
        active_uniqueness_ok=active_uniqueness_ok,
        qualification_ready=qualification_ready,
        activation_ready=activation_ready,
    )
