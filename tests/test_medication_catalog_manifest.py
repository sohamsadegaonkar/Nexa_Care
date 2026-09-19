from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.models.medication_catalog import (
    MedicationCatalogEntry,
    MedicationCatalogEvidence,
    MedicationCatalogRelease,
)
from app.services.medication_catalog_manifest import (
    MedicationCatalogCanonicalizationError,
    build_release_manifest_bytes,
    candidate_entry_digest,
    canonicalize_json,
    sha256_hex,
)


NOW = datetime(2026, 9, 19, 18, 0, tzinfo=timezone.utc)


def _release() -> MedicationCatalogRelease:
    return MedicationCatalogRelease(
        id=uuid4(),
        version="synthetic-release-v1",
        status="DRAFT",
        source_cutoff_at=NOW,
        policy_version="medication-catalog/v1",
        source_terminology_version="synthetic-terminology-2026-09",
        prepared_by=uuid4(),
    )


def _entry(code: str = "SYNTH-ALLOW-001") -> MedicationCatalogEntry:
    return MedicationCatalogEntry(
        id=uuid4(),
        release_id=uuid4(),
        medication_code=code,
        code_system="SYNTHETIC",
        code_system_version="1",
        canonical_generic_name="Synthetic Generic Alpha",
        medication_display="Synthetic Generic Alpha tablet",
        ingredient_identity="Synthetic Ingredient Alpha",
        dose_form="tablet",
        identity_strength_descriptor="synthetic identity strength",
        identity_granularity_sufficient=True,
        terminology_status="ACTIVE",
        drug_schedule_class="NONE_CONFIRMED",
        ndps_class="NOT_CONTROLLED_CONFIRMED",
        telemedicine_class="LIST_O_ANY_MODE",
        special_recordkeeping_class="NONE_CONFIRMED",
        nexa_high_risk_class="NONE_CONFIRMED",
        regulatory_product_status="CURRENT",
        classification_rationale_code="SYNTHETIC_TEST_ONLY",
        v1_universal_allowed=True,
        entry_integrity_digest="a" * 64,
    )


def _evidence(
    entry: MedicationCatalogEntry,
    dimension: str,
    digest_char: str,
) -> MedicationCatalogEvidence:
    return MedicationCatalogEvidence(
        id=uuid4(),
        release_id=entry.release_id,
        entry_id=entry.id,
        finding_dimension=dimension,
        source_authority="NRCES",
        source_document_version="synthetic-source-v1",
        source_reference=f"synthetic://{dimension}",
        checked_at=NOW,
        finding_value="SYNTHETIC_CONFIRMED",
        rationale_code="SYNTHETIC_TEST_ONLY",
        evidence_sha256=digest_char * 64,
        prepared_by=uuid4(),
    )


def test_rfc8785_object_order_uses_utf16_code_units() -> None:
    value = {
        "\ufb33": 7,
        "😀": 6,
        "€": 5,
        "ö": 4,
        "\u0080": 3,
        "1": 2,
        "\r": 1,
    }
    assert canonicalize_json(value) == (
        b'{"\\r":1,"1":2,"\xc2\x80":3,"\xc3\xb6":4,'
        b'"\xe2\x82\xac":5,"\xf0\x9f\x98\x80":6,"\xef\xac\xb3":7}'
    )


def test_rfc8785_supported_shape_rejects_float_and_unsafe_integer() -> None:
    with pytest.raises(
        MedicationCatalogCanonicalizationError,
        match="FLOAT_NOT_ALLOWED",
    ):
        canonicalize_json({"unsafe": 1.5})
    with pytest.raises(
        MedicationCatalogCanonicalizationError,
        match="INTEGER_OUTSIDE_JCS_SAFE_RANGE",
    ):
        canonicalize_json({"unsafe": 2**53})


def test_manifest_digest_is_order_independent_and_reproducible() -> None:
    release = _release()
    first = _entry("SYNTH-ALLOW-002")
    second = _entry("SYNTH-ALLOW-001")
    first.release_id = release.id
    second.release_id = release.id
    evidence_first = [_evidence(first, "IDENTITY", "a")]
    evidence_second = [
        _evidence(second, "TELEMEDICINE", "c"),
        _evidence(second, "IDENTITY", "b"),
    ]

    bytes_a = build_release_manifest_bytes(
        release,
        [first, second],
        {first.id: evidence_first, second.id: evidence_second},
    )
    bytes_b = build_release_manifest_bytes(
        release,
        [second, first],
        {second.id: list(reversed(evidence_second)), first.id: evidence_first},
    )
    assert bytes_a == bytes_b
    assert sha256_hex(bytes_a) == sha256_hex(bytes_b)
    assert bytes_a.index(b"SYNTH-ALLOW-001") < bytes_a.index(b"SYNTH-ALLOW-002")


def test_candidate_digest_changes_when_bound_evidence_changes() -> None:
    release = _release()
    entry = _entry()
    entry.release_id = release.id
    first = _evidence(entry, "IDENTITY", "a")
    second = _evidence(entry, "IDENTITY", "b")
    assert candidate_entry_digest(
        entry,
        [first],
        policy_version=release.policy_version,
    ) != candidate_entry_digest(
        entry,
        [second],
        policy_version=release.policy_version,
    )


def test_manifest_does_not_bind_volatile_surrogate_ids() -> None:
    release_a = _release()
    entry_a = _entry()
    entry_a.release_id = release_a.id
    evidence_a = _evidence(entry_a, "IDENTITY", "a")

    release_b = _release()
    release_b.version = release_a.version
    release_b.source_cutoff_at = release_a.source_cutoff_at
    release_b.policy_version = release_a.policy_version
    release_b.source_terminology_version = release_a.source_terminology_version
    entry_b = _entry()
    entry_b.release_id = release_b.id
    evidence_b = _evidence(entry_b, "IDENTITY", "a")

    assert build_release_manifest_bytes(
        release_a,
        [entry_a],
        {entry_a.id: [evidence_a]},
    ) == build_release_manifest_bytes(
        release_b,
        [entry_b],
        {entry_b.id: [evidence_b]},
    )
