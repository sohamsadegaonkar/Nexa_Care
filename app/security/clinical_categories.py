"""Canonical clinical-category vocabulary for emergency (break-glass) access.

This module is the single source of truth for what "categories" mean when
they appear in a break-glass consent capability. It is deliberately
separate from the routine-consent field-scope grammar used by
``consent_gated_crypto.py`` (``pii.<field>`` / ``clinical.<field>``) --
break-glass access is scoped to whole clinical categories backed by an
authoritative data source, not to arbitrary field paths, and the two
vocabularies must never be silently interchangeable.

Every category listed here must be backed by a real, authoritative data
source. Do not add a category (for example emergency contacts) until a
real, encrypted, verified source for it exists in the repository -- an
unsupported category must fail closed with ``UNSUPPORTED_CLINICAL_CATEGORY``
rather than silently return nothing or default data.
"""

from __future__ import annotations

from enum import Enum

# Bumped whenever the category vocabulary changes in a way that affects
# previously issued (but still live) break-glass capabilities. Stored on
# the capability itself so a mid-flight capability can be checked against
# the protocol version it was minted under.
CLINICAL_CATEGORY_PROTOCOL_VERSION = "2026-07-v1"

UNSUPPORTED_CLINICAL_CATEGORY_ERROR_CODE = "UNSUPPORTED_CLINICAL_CATEGORY"


class ClinicalCategory(str, Enum):
    """Canonical, versioned clinical data categories.

    Values are the wire format used in capability scope, audit metadata,
    and frontend section mapping. Only add a member here when it is backed
    by an authoritative, queryable data source in this repository.
    """

    ALLERGIES = "allergies"
    ACTIVE_MEDICATIONS = "active_medications"
    VITALS = "vitals"
    LAB_RESULTS = "lab_results"
    DIAGNOSES = "diagnoses"
    BLOOD_GROUP = "blood_group"
    DOCUMENT_REFERENCES = "document_references"


class UnsupportedClinicalCategoryError(ValueError):
    """Raised when a requested category is not in the canonical vocabulary.

    Callers must treat this as a fail-closed condition: reject the entire
    request rather than silently dropping the unrecognized category.
    """

    def __init__(self, category: str):
        self.category = category
        self.error_code = UNSUPPORTED_CLINICAL_CATEGORY_ERROR_CODE
        super().__init__(f"Unsupported clinical category: {category!r}")


def parse_clinical_categories(values: list[str]) -> list[ClinicalCategory]:
    """Parse and validate a list of category strings.

    Fails closed: raises ``UnsupportedClinicalCategoryError`` on the first
    unknown value instead of dropping it, per the defect-1 contract that a
    request naming any unknown category must be rejected in full.
    """

    parsed: list[ClinicalCategory] = []
    for raw in values:
        cleaned = raw.strip() if isinstance(raw, str) else ""
        if not cleaned:
            continue
        try:
            parsed.append(ClinicalCategory(cleaned))
        except ValueError:
            raise UnsupportedClinicalCategoryError(cleaned) from None
    return parsed


def narrow_categories(
    approved: frozenset[ClinicalCategory],
    requested: list[str] | None,
) -> list[ClinicalCategory]:
    """Return the categories actually granted for a break-glass request.

    ``requested`` may only narrow ``approved`` -- it can never expand it.
    A requested category outside ``approved`` or outside the canonical
    vocabulary fails the whole request closed.
    """

    if requested is None:
        return sorted(approved, key=lambda c: c.value)

    parsed_requested = parse_clinical_categories(requested)
    if not parsed_requested:
        raise ValueError("Requested emergency categories cannot be empty.")

    unapproved = [c for c in parsed_requested if c not in approved]
    if unapproved:
        raise ValueError(
            "Requested emergency categories exceed the approved policy: "
            f"{[c.value for c in unapproved]}"
        )
    return sorted(set(parsed_requested), key=lambda c: c.value)
