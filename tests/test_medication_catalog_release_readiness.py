from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from app.models.medication_catalog import (
    DrugScheduleClass,
    MedicationCatalogEmergencyDeny,
    MedicationCatalogEntry,
    MedicationCatalogEvidence,
    MedicationCatalogRelease,
    MedicationCatalogReleaseStatus,
    MedicationEmergencyAction,
    MedicationEmergencyReason,
    MedicationEvidenceAuthority,
    MedicationEvidenceDimension,
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
from app.services.medication_catalog_policy import MEDICATION_CATALOG_POLICY_VERSION
from app.services.medication_catalog_release_readiness import (
    MedicationCatalogReleaseReadinessError,
    assess_release_readiness,
    parse_source_metadata,
    verify_package_digest,
)
from app.services.medication_catalog_signing import (
    CATALOG_SIGNATURE_ALGORITHM,
    InMemoryMedicationCatalogTestSigner,
    signature_to_text,
)

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
PACKAGE_DIGEST = "a" * 64
PACKAGE_VERSION = "synthetic-cdci-2026-08-31"


def _source_payload(**overrides):
    payload = {
        "schema": "nexa-medication-catalog-source-metadata/v1",
        "terminology_authority": "NRCES",
        "package_name": "Synthetic protected terminology package",
        "package_version": PACKAGE_VERSION,
        "package_release_date": "2026-08-31",
        "package_sha256": PACKAGE_DIGEST,
        "official_source_reference": "https://official.example.test/release",
        "licence_governance_reference": "LICENCE-EVIDENCE-TEST-ONLY",
        "checked_at": NOW.isoformat(),
    }
    payload.update(overrides)
    return payload


def _release(*, status=MedicationCatalogReleaseStatus.DRAFT.value):
    preparer = uuid4()
    return MedicationCatalogRelease(
        id=uuid4(),
        version="synthetic-in-v1-2026-09-20.1",
        status=status,
        source_cutoff_at=NOW,
        policy_version=MEDICATION_CATALOG_POLICY_VERSION,
        source_terminology_version=PACKAGE_VERSION,
        prepared_by=preparer,
    )


def _entry(release, *, schedule=DrugScheduleClass.NONE_CONFIRMED.value):
    return MedicationCatalogEntry(
        id=uuid4(),
        release_id=release.id,
        medication_code="SYNTH-CDCI-0001",
        code_system="SYNTHETIC-TEST-ONLY",
        code_system_version=PACKAGE_VERSION,
        canonical_generic_name="Synthetic generic concept",
        medication_display="Synthetic medication display",
        ingredient_identity="Synthetic ingredient identity",
        dose_form="Synthetic dose form",
        identity_strength_descriptor="Synthetic strength",
        identity_granularity_sufficient=True,
        terminology_status=TerminologyConceptStatus.ACTIVE.value,
        drug_schedule_class=schedule,
        ndps_class=NdpsClass.NOT_CONTROLLED_CONFIRMED.value,
        telemedicine_class=TelemedicineClass.LIST_O_ANY_MODE.value,
        special_recordkeeping_class=SpecialRecordkeepingClass.NONE_CONFIRMED.value,
        nexa_high_risk_class=NexaHighRiskClass.NONE_CONFIRMED.value,
        regulatory_product_status=RegulatoryProductStatus.CURRENT.value,
        classification_rationale_code="SYNTHETIC_TEST_ONLY",
        v1_universal_allowed=False,
    )


def _evidence(release, entry):
    rows = []
    dimensions = (
        MedicationEvidenceDimension.IDENTITY,
        MedicationEvidenceDimension.DRUG_SCHEDULE,
        MedicationEvidenceDimension.NDPS,
        MedicationEvidenceDimension.TELEMEDICINE,
        MedicationEvidenceDimension.SPECIAL_RECORDKEEPING,
        MedicationEvidenceDimension.HIGH_RISK,
        MedicationEvidenceDimension.REGULATORY_PRODUCT_STATUS,
    )
    for index, dimension in enumerate(dimensions, start=1):
        rows.append(
            MedicationCatalogEvidence(
                id=uuid4(),
                release_id=release.id,
                entry_id=entry.id,
                finding_dimension=dimension.value,
                source_authority=(
                    MedicationEvidenceAuthority.NRCES.value
                    if dimension is MedicationEvidenceDimension.IDENTITY
                    else MedicationEvidenceAuthority.CDSCO.value
                ),
                source_document_version=(
                    PACKAGE_VERSION
                    if dimension is MedicationEvidenceDimension.IDENTITY
                    else "synthetic-regulatory-source-v1"
                ),
                source_reference=f"https://official.example.test/{dimension.value}",
                publication_date=date(2026, 8, 31),
                effective_date=date(2026, 8, 31),
                checked_at=NOW,
                finding_value="SYNTHETIC_CONFIRMED",
                rationale_code="SYNTHETIC_TEST_ONLY",
                evidence_sha256=(
                    PACKAGE_DIGEST
                    if dimension is MedicationEvidenceDimension.IDENTITY
                    else f"{index:x}" * 64
                ),
                prepared_by=release.prepared_by,
            )
        )
    return rows


def _bind_reviews(release, entry, evidence):
    first = uuid4()
    second = uuid4()
    digest = candidate_entry_digest(
        entry,
        evidence,
        policy_version=release.policy_version,
    )
    entry.first_reviewer_provider_id = first
    entry.second_reviewer_provider_id = second
    entry.first_review_digest = digest
    entry.second_review_digest = digest
    entry.first_reviewed_at = NOW
    entry.second_reviewed_at = NOW
    return digest, first, second


def test_source_metadata_is_strict_and_package_digest_is_verified(tmp_path):
    provenance = parse_source_metadata(_source_payload())
    package = tmp_path / "protected-package.bin"
    package.write_bytes(b"synthetic package bytes")

    actual = __import__("hashlib").sha256(package.read_bytes()).hexdigest()
    provenance = parse_source_metadata(
        _source_payload(package_sha256=actual)
    )
    assert verify_package_digest(provenance, package) is True

    with pytest.raises(MedicationCatalogReleaseReadinessError) as unknown:
        parse_source_metadata({**_source_payload(), "licensed_payload": "forbidden"})
    assert unknown.value.code == "SOURCE_METADATA_INVALID"


@pytest.mark.asyncio
async def test_draft_release_is_ready_only_with_safe_policy_reviews_and_package_binding():
    provenance = parse_source_metadata(_source_payload())
    release = _release()
    entry = _entry(release)
    evidence = _evidence(release, entry)
    _bind_reviews(release, entry, evidence)
    signer = InMemoryMedicationCatalogTestSigner()

    report = await assess_release_readiness(
        release=release,
        entries=[entry],
        evidence_by_entry={entry.id: evidence},
        latest_emergency_by_code={},
        active_release_count=0,
        provenance=provenance,
        package_digest_verified=True,
        signing_provider=signer,
    )

    assert report.candidate_positive_count == 1
    assert report.persisted_positive_count == 0
    assert report.identity_package_binding_ok is True
    assert report.qualification_ready is True
    assert report.activation_ready is False


@pytest.mark.asyncio
async def test_unknown_or_restricted_or_unreviewed_entry_cannot_be_ready():
    provenance = parse_source_metadata(_source_payload())
    signer = InMemoryMedicationCatalogTestSigner()

    for schedule in (DrugScheduleClass.H.value, DrugScheduleClass.UNKNOWN.value):
        release = _release()
        entry = _entry(release, schedule=schedule)
        evidence = _evidence(release, entry)
        _bind_reviews(release, entry, evidence)
        report = await assess_release_readiness(
            release=release,
            entries=[entry],
            evidence_by_entry={entry.id: evidence},
            latest_emergency_by_code={},
            active_release_count=0,
            provenance=provenance,
            package_digest_verified=True,
            signing_provider=signer,
        )
        assert report.candidate_positive_count == 0
        assert report.qualification_ready is False

    release = _release()
    entry = _entry(release)
    evidence = _evidence(release, entry)
    report = await assess_release_readiness(
        release=release,
        entries=[entry],
        evidence_by_entry={entry.id: evidence},
        latest_emergency_by_code={},
        active_release_count=0,
        provenance=provenance,
        package_digest_verified=True,
        signing_provider=signer,
    )
    assert report.candidate_positive_count == 0
    assert report.qualification_ready is False


@pytest.mark.asyncio
async def test_identity_evidence_must_bind_exact_terminology_package_digest():
    provenance = parse_source_metadata(_source_payload())
    release = _release()
    entry = _entry(release)
    evidence = _evidence(release, entry)
    evidence[0].evidence_sha256 = "b" * 64
    _bind_reviews(release, entry, evidence)

    report = await assess_release_readiness(
        release=release,
        entries=[entry],
        evidence_by_entry={entry.id: evidence},
        latest_emergency_by_code={},
        active_release_count=0,
        provenance=provenance,
        package_digest_verified=True,
        signing_provider=InMemoryMedicationCatalogTestSigner(),
    )
    assert report.candidate_positive_count == 1
    assert report.identity_package_binding_ok is False
    assert report.qualification_ready is False


@pytest.mark.asyncio
async def test_qualified_release_requires_projection_signature_and_no_emergency_deny():
    provenance = parse_source_metadata(_source_payload())
    release = _release(status=MedicationCatalogReleaseStatus.QUALIFIED.value)
    entry = _entry(release)
    evidence = _evidence(release, entry)
    digest, first, _ = _bind_reviews(release, entry, evidence)
    entry.v1_universal_allowed = True
    entry.entry_integrity_digest = digest

    signer = InMemoryMedicationCatalogTestSigner()
    release.qualified_by = first
    release.qualified_at = NOW
    manifest = build_release_manifest_bytes(
        release,
        [entry],
        {entry.id: evidence},
    )
    release.canonical_manifest = manifest.decode("utf-8")
    release.integrity_digest = sha256_hex(manifest)
    release.artifact_key_id = signer.key_identifier
    release.signature_algorithm = CATALOG_SIGNATURE_ALGORITHM
    release.artifact_signature = signature_to_text(
        await signer.sign_release_digest(bytes.fromhex(release.integrity_digest))
    )

    report = await assess_release_readiness(
        release=release,
        entries=[entry],
        evidence_by_entry={entry.id: evidence},
        latest_emergency_by_code={},
        active_release_count=0,
        provenance=provenance,
        package_digest_verified=True,
        signing_provider=signer,
    )
    assert report.manifest_projection_ok is True
    assert report.signature_verified is True
    assert report.activation_ready is True

    deny = MedicationCatalogEmergencyDeny(
        id=uuid4(),
        medication_code=entry.medication_code,
        version=1,
        action=MedicationEmergencyAction.DENY.value,
        reason_code=MedicationEmergencyReason.PATIENT_SAFETY_HOLD.value,
        evidence_reference="https://official.example.test/emergency",
        evidence_sha256="c" * 64,
        actor_provider_id=uuid4(),
        effective_at=NOW,
    )
    denied = await assess_release_readiness(
        release=release,
        entries=[entry],
        evidence_by_entry={entry.id: evidence},
        latest_emergency_by_code={entry.medication_code: deny},
        active_release_count=0,
        provenance=provenance,
        package_digest_verified=True,
        signing_provider=signer,
    )
    assert denied.emergency_compatibility_ok is False
    assert denied.activation_ready is False


@pytest.mark.asyncio
async def test_qualified_release_projection_tamper_fails_closed():
    provenance = parse_source_metadata(_source_payload())
    release = _release(status=MedicationCatalogReleaseStatus.QUALIFIED.value)
    entry = _entry(release)
    evidence = _evidence(release, entry)
    digest, first, _ = _bind_reviews(release, entry, evidence)
    entry.v1_universal_allowed = True
    entry.entry_integrity_digest = digest
    signer = InMemoryMedicationCatalogTestSigner()
    release.qualified_by = first
    release.qualified_at = NOW
    manifest = build_release_manifest_bytes(release, [entry], {entry.id: evidence})
    release.canonical_manifest = manifest.decode("utf-8")
    release.integrity_digest = sha256_hex(manifest)
    release.artifact_key_id = signer.key_identifier
    release.signature_algorithm = CATALOG_SIGNATURE_ALGORITHM
    release.artifact_signature = signature_to_text(
        await signer.sign_release_digest(bytes.fromhex(release.integrity_digest))
    )

    entry.medication_display = "Tampered synthetic display"
    report = await assess_release_readiness(
        release=release,
        entries=[entry],
        evidence_by_entry={entry.id: evidence},
        latest_emergency_by_code={},
        active_release_count=0,
        provenance=provenance,
        package_digest_verified=True,
        signing_provider=signer,
    )
    assert report.manifest_projection_ok is False
    assert report.activation_ready is False


@pytest.mark.asyncio
async def test_multiple_active_releases_block_readiness():
    provenance = parse_source_metadata(_source_payload())
    release = _release()
    entry = _entry(release)
    evidence = _evidence(release, entry)
    _bind_reviews(release, entry, evidence)

    report = await assess_release_readiness(
        release=release,
        entries=[entry],
        evidence_by_entry={entry.id: evidence},
        latest_emergency_by_code={},
        active_release_count=2,
        provenance=provenance,
        package_digest_verified=True,
        signing_provider=InMemoryMedicationCatalogTestSigner(),
    )
    assert report.active_uniqueness_ok is False
    assert report.qualification_ready is False
