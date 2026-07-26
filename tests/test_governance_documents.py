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
DETAIL_FILES = (SECURITY, REGULATORY, CONSTITUTION)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_governance_sources_exist() -> None:
    for path in (AGENTS, *DETAIL_FILES):
        assert path.is_file(), f"Missing mandatory governance source: {path}"


def test_agents_indexes_all_governance_sources() -> None:
    text = _read(AGENTS)
    for filename in (
        SECURITY.name,
        REGULATORY.name,
        CONSTITUTION.name,
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


def test_governance_sources_do_not_contain_obvious_placeholder_secrets() -> None:
    forbidden = (
        "BEGIN PRIVATE KEY",
        "AKIAIOSFODNN7EXAMPLE",
        "postgresql://user:password@",
        "sk-proj-",
    )
    for path in (AGENTS, *DETAIL_FILES):
        text = _read(path)
        for marker in forbidden:
            assert marker not in text
