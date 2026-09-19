"""Deterministic medication-catalog manifest and integrity utilities.

The emitted manifest shape intentionally contains only JSON strings, booleans,
nulls, arrays, objects and IEEE-754-safe integers. Floating point values are
forbidden. Canonicalization follows RFC 8785/JCS rules for this complete emitted
shape, including UTF-16 property ordering.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from app.models.medication_catalog import (
    MedicationCatalogEntry,
    MedicationCatalogEvidence,
    MedicationCatalogRelease,
)

MANIFEST_SCHEMA_VERSION = "nexa-medication-catalog-manifest/v1"
_MAX_SAFE_INTEGER = (1 << 53) - 1


class MedicationCatalogCanonicalizationError(ValueError):
    pass


def _utf16_sort_key(value: str) -> bytes:
    try:
        return value.encode("utf-16be")
    except UnicodeEncodeError as exc:
        raise MedicationCatalogCanonicalizationError(
            "INVALID_UNICODE_STRING"
        ) from exc


def _json_string(value: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise MedicationCatalogCanonicalizationError(
            "INVALID_UNICODE_STRING"
        ) from exc


def canonicalize_json(value: Any) -> bytes:
    """Serialize the supported JSON domain using RFC 8785/JCS rules."""

    if value is None:
        return b"null"
    if value is True:
        return b"true"
    if value is False:
        return b"false"
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise MedicationCatalogCanonicalizationError(
                "INTEGER_OUTSIDE_JCS_SAFE_RANGE"
            )
        return str(value).encode("ascii")
    if isinstance(value, float):
        raise MedicationCatalogCanonicalizationError("FLOAT_NOT_ALLOWED")
    if isinstance(value, str):
        return _json_string(value)
    if isinstance(value, Mapping):
        parts: list[bytes] = []
        keys = list(value.keys())
        if not all(isinstance(key, str) for key in keys):
            raise MedicationCatalogCanonicalizationError(
                "OBJECT_KEYS_MUST_BE_STRINGS"
            )
        for key in sorted(keys, key=_utf16_sort_key):
            parts.append(_json_string(key) + b":" + canonicalize_json(value[key]))
        return b"{" + b",".join(parts) + b"}"
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return b"[" + b",".join(canonicalize_json(item) for item in value) + b"]"
    raise MedicationCatalogCanonicalizationError("UNSUPPORTED_JSON_TYPE")


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _value(value: object) -> object:
    return value.value if isinstance(value, Enum) else value


def _iso_date(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _iso_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MedicationCatalogCanonicalizationError("NAIVE_DATETIME")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def evidence_projection(evidence: MedicationCatalogEvidence) -> dict[str, object]:
    return {
        "finding_dimension": str(_value(evidence.finding_dimension)),
        "source_authority": str(_value(evidence.source_authority)),
        "source_document_version": evidence.source_document_version,
        "source_reference": evidence.source_reference,
        "publication_date": _iso_date(evidence.publication_date),
        "effective_date": _iso_date(evidence.effective_date),
        "checked_at": _iso_datetime(evidence.checked_at),
        "finding_value": evidence.finding_value,
        "rationale_code": evidence.rationale_code,
        "evidence_sha256": evidence.evidence_sha256,
    }


def _ordered_evidence(
    evidence_rows: Sequence[MedicationCatalogEvidence],
) -> list[dict[str, object]]:
    projected = [evidence_projection(item) for item in evidence_rows]
    return sorted(
        projected,
        key=lambda item: (
            str(item["finding_dimension"]),
            str(item["source_authority"]),
            str(item["source_reference"]),
            str(item["evidence_sha256"]),
        ),
    )


def candidate_entry_projection(
    entry: MedicationCatalogEntry,
    evidence_rows: Sequence[MedicationCatalogEvidence],
    *,
    policy_version: str,
) -> dict[str, object]:
    """Projection bound by both human positive-review slots."""

    return {
        "policy_version": policy_version,
        "medication_code": entry.medication_code,
        "code_system": entry.code_system,
        "code_system_version": entry.code_system_version,
        "canonical_generic_name": entry.canonical_generic_name,
        "medication_display": entry.medication_display,
        "ingredient_identity": entry.ingredient_identity,
        "dose_form": entry.dose_form,
        "identity_strength_descriptor": entry.identity_strength_descriptor,
        "identity_granularity_sufficient": entry.identity_granularity_sufficient,
        "terminology_status": str(_value(entry.terminology_status)),
        "drug_schedule_class": str(_value(entry.drug_schedule_class)),
        "ndps_class": str(_value(entry.ndps_class)),
        "telemedicine_class": str(_value(entry.telemedicine_class)),
        "special_recordkeeping_class": str(
            _value(entry.special_recordkeeping_class)
        ),
        "nexa_high_risk_class": str(_value(entry.nexa_high_risk_class)),
        "regulatory_product_status": str(
            _value(entry.regulatory_product_status)
        ),
        "classification_rationale_code": entry.classification_rationale_code,
        "evidence": _ordered_evidence(evidence_rows),
    }


def candidate_entry_digest(
    entry: MedicationCatalogEntry,
    evidence_rows: Sequence[MedicationCatalogEvidence],
    *,
    policy_version: str,
) -> str:
    return sha256_hex(
        canonicalize_json(
            candidate_entry_projection(
                entry,
                evidence_rows,
                policy_version=policy_version,
            )
        )
    )


def release_entry_projection(
    entry: MedicationCatalogEntry,
    evidence_rows: Sequence[MedicationCatalogEvidence],
) -> dict[str, object]:
    return {
        "medication_code": entry.medication_code,
        "code_system": entry.code_system,
        "code_system_version": entry.code_system_version,
        "canonical_generic_name": entry.canonical_generic_name,
        "medication_display": entry.medication_display,
        "ingredient_identity": entry.ingredient_identity,
        "dose_form": entry.dose_form,
        "identity_strength_descriptor": entry.identity_strength_descriptor,
        "identity_granularity_sufficient": entry.identity_granularity_sufficient,
        "terminology_status": str(_value(entry.terminology_status)),
        "drug_schedule_class": str(_value(entry.drug_schedule_class)),
        "ndps_class": str(_value(entry.ndps_class)),
        "telemedicine_class": str(_value(entry.telemedicine_class)),
        "special_recordkeeping_class": str(
            _value(entry.special_recordkeeping_class)
        ),
        "nexa_high_risk_class": str(_value(entry.nexa_high_risk_class)),
        "regulatory_product_status": str(
            _value(entry.regulatory_product_status)
        ),
        "classification_rationale_code": entry.classification_rationale_code,
        "v1_universal_allowed": bool(entry.v1_universal_allowed),
        "entry_integrity_digest": entry.entry_integrity_digest,
        "evidence": _ordered_evidence(evidence_rows),
    }


def build_release_manifest(
    release: MedicationCatalogRelease,
    entries: Sequence[MedicationCatalogEntry],
    evidence_by_entry: Mapping[object, Sequence[MedicationCatalogEvidence]],
) -> dict[str, object]:
    ordered_entries = sorted(entries, key=lambda item: item.medication_code)
    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "release_version": release.version,
        "policy_version": release.policy_version,
        "source_cutoff_at": _iso_datetime(release.source_cutoff_at),
        "source_terminology_version": release.source_terminology_version,
        "entries": [
            release_entry_projection(
                entry,
                evidence_by_entry.get(entry.id, ()),
            )
            for entry in ordered_entries
        ],
    }


def build_release_manifest_bytes(
    release: MedicationCatalogRelease,
    entries: Sequence[MedicationCatalogEntry],
    evidence_by_entry: Mapping[object, Sequence[MedicationCatalogEvidence]],
) -> bytes:
    return canonicalize_json(build_release_manifest(release, entries, evidence_by_entry))
