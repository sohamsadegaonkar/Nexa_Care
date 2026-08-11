"""Mechanical guards for Nexa Care's repository-governance sources."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"
GOVERNANCE = ROOT / "docs" / "governance"
SECURITY = GOVERNANCE / "SECURITY_NON_REGRESSION.md"
REGULATORY = GOVERNANCE / "INDIA_REGULATORY_BASELINE.md"
CONSTITUTION = GOVERNANCE / "NEXA_CARE_ENGINEERING_CONSTITUTION.md"
IDENTITY_POLICY = GOVERNANCE / "IDENTITY_EVIDENCE_DISCLOSURE_POLICY.md"
DETAIL_FILES = (SECURITY, REGULATORY, CONSTITUTION)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_governance_sources_exist() -> None:
    for path in (AGENTS, *DETAIL_FILES, IDENTITY_POLICY):
        assert path.is_file(), f"Missing mandatory governance source: {path}"


def test_agents_indexes_all_governance_sources() -> None:
    text = _read(AGENTS)
    for filename in (
        SECURITY.name,
        REGULATORY.name,
        CONSTITUTION.name,
        IDENTITY_POLICY.name,
    ):
        assert filename in text
    assert "Mandatory reading order" in text
    assert "Completion report" in text


def test_detailed_sources_cross_link_and_have_maintenance_metadata() -> None:
    expected_links = {
        SECURITY: (REGULATORY.name, CONSTITUTION.name),
        REGULATORY: (SECURITY.name, CONSTITUTION.name),
        CONSTITUTION: (SECURITY.name, REGULATORY.name),
    }
    for path, peers in expected_links.items():
        text = _read(path)
        assert "../../AGENTS.md" in text
        for peer in peers:
            assert peer in text
        assert "Last reviewed:" in text
        assert re.search(r"(?i)update (procedure|process)", text)

    identity_text = _read(IDENTITY_POLICY)
    for link in (
        "../../AGENTS.md",
        SECURITY.name,
        REGULATORY.name,
        CONSTITUTION.name,
    ):
        assert link in identity_text
    assert IDENTITY_POLICY.name in _read(SECURITY)
    assert "Last reviewed:" in identity_text
    assert re.search(r"(?i)update (procedure|process)", identity_text)


def test_security_register_contains_required_findings() -> None:
    text = _read(SECURITY)
    for number in range(1, 21):
        assert f"SEC-{number:03d}" in text


def test_regulatory_warning_and_inventory_are_present() -> None:
    text = _read(REGULATORY)
    assert "engineering compliance baseline, not legal advice" in text
    assert "qualified Indian legal" in text
    for number in range(1, 21):
        assert f"REG-{number:03d}" in text


def test_engineering_mission_is_present() -> None:
    text = _read(CONSTITUTION)
    assert "consent-first healthcare interoperability" in text
    assert "No fabricated clinical data" in text
    assert "Definition of done" in text


def test_identity_evidence_policy_locks_current_boundary() -> None:
    text = _read(IDENTITY_POLICY)
    required_markers = (
        "Nexa Care Identity Evidence Disclosure Policy",
        "Status: Engineering security/privacy boundary",
        "Human privacy/legal approval required before disclosure expansion",
        "This document defines the current engineering disclosure boundary.",
        "It is not a legal or regulatory compliance determination.",
        "CURRENT_IDENTITY_REVIEW_DISCLOSURE_POLICY = E0_METADATA_ONLY",
        "E0 — Identity-review metadata",
        "AUTHORIZED_NOW",
        "E1_RUNTIME_IMPLEMENTATION = NOT_AUTHORIZED_BY_THIS_POLICY_LOCK",
        "E2 — Field-specific value-free statuses",
        "E2_DISCLOSURE = NOT_AUTHORIZED",
        "E3 — Masked identity values",
        "E3_MASKED_IDENTITY = PROHIBITED_UNDER_CURRENT_POLICY",
        "E4 — Raw OCR identity assertions",
        "E4_RAW_OCR_IDENTITY = PROHIBITED_UNDER_CURRENT_POLICY",
        "E5 — Canonical stored identity values",
        "E5_CANONICAL_IDENTITY = PROHIBITED_FOR_IDENTITY_REVIEWER",
        "E6 — Original source document",
        "E6_SOURCE_DOCUMENT = PROHIBITED_UNDER_CURRENT_IDENTITY_REVIEW_POLICY",
        "PROHIBITED_UNDER_CURRENT_POLICY",
        "PROHIBITED_FOR_IDENTITY_REVIEWER",
        "PROHIBITED_UNDER_CURRENT_IDENTITY_REVIEW_POLICY",
        "RAW_IDENTITY_ASSERTION_PERSISTENCE = NOT_AUTHORIZED",
        "DO_NOT_PERSIST_UNKEYED_IDENTITY_VALUE_HASHES",
        "AUTOMATIC_IDENTITY_REEXTRACTION_FOR_REVIEW = PROHIBITED",
        "IDENTITY_REVIEW_SOURCE_ACCESS = PROHIBITED_UNDER_POLICY_V1",
        "identity-review/1.0",
        "identity-review/2.0",
        "IDENTITY_EVIDENCE_VISIBILITY_CREATES_IDENTITY_AUTHORITY = NEVER",
        "CONFIRM_BOUND_PATIENT",
        "RELEASE_FROM_QUARANTINE",
        "REASSIGN_PATIENT",
        "IDENTITY_REVIEW_PATIENT_LOOKUP_AUTHORITY = NOT_GRANTED",
        "Identity-evidence disclosure authority and patient-search/patient-lookup",
        "current literal `identity_reviewer` role",
        "VERIFIED_ABHA_MRN_SOURCE_VIEW_AUTHORITY = NOT_GRANTED",
        "VERIFIED_ABHA_MRN_RELEASE_AUTHORITY = NOT_GRANTED",
        "VERIFIED_ABHA_MRN_PATIENT_REASSIGNMENT_AUTHORITY = NOT_GRANTED",
        "PRIVACY",
        "legal",
    )
    for marker in required_markers:
        assert marker in text


def test_identity_evidence_policy_raw_ocr_is_closed() -> None:
    text = _read(IDENTITY_POLICY)
    assert "E4_RAW_OCR_IDENTITY = PROHIBITED_UNDER_CURRENT_POLICY" in text


def test_identity_evidence_policy_never_creates_identity_authority() -> None:
    text = _read(IDENTITY_POLICY)
    assert "IDENTITY_EVIDENCE_VISIBILITY_CREATES_IDENTITY_AUTHORITY = NEVER" in text
    for operation in (
        "CONFIRM_BOUND_PATIENT",
        "REASSIGN_PATIENT",
        "RELEASE_FROM_QUARANTINE",
    ):
        assert operation in text


def test_identity_evidence_policy_blocks_lookup_and_verified_identifier_authority() -> None:
    text = _read(IDENTITY_POLICY)
    assert "IDENTITY_REVIEW_PATIENT_LOOKUP_AUTHORITY = NOT_GRANTED" in text
    assert "patient-search/patient-lookup" in text
    assert "are separate capabilities" in text
    assert "VERIFIED_ABHA_MRN_SOURCE_VIEW_AUTHORITY = NOT_GRANTED" in text
    assert "VERIFIED_ABHA_MRN_RELEASE_AUTHORITY = NOT_GRANTED" in text


def test_identity_evidence_policy_requires_literal_reviewer_role() -> None:
    text = _read(IDENTITY_POLICY)
    assert "current literal `identity_reviewer` role" in text


def test_governance_sources_do_not_contain_obvious_placeholder_secrets() -> None:
    forbidden = (
        "BEGIN PRIVATE KEY",
        "AKIAIOSFODNN7EXAMPLE",
        "postgresql://user:password@",
        "sk-proj-",
    )
    for path in (AGENTS, *DETAIL_FILES, IDENTITY_POLICY):
        text = _read(path)
        for marker in forbidden:
            assert marker not in text
