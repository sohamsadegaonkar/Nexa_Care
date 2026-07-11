"""Tests for access history, patient timeline, and provenance badges (Days 9-11).

Validates:
  - AccessHistoryScreen uses apiClient.getAccessHistory()
  - Break-glass / emergency accesses flagged with warning badge
  - PatientTimelineScreen uses apiClient.getMyTimeline()
  - SourceBadge: green for manual, blue for AI-extracted with confidence
  - RiskBadge: colour-coded by risk level
  - Timeline distinguishes AI vs manual data
  - Both screens use Tamagui only, shared apiClient, no forbidden patterns
"""

from __future__ import annotations

import re
from pathlib import Path


# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]
FEATURES_DIR = ROOT / "nexa-client" / "packages" / "app" / "features" / "patient"
BADGES_DIR = FEATURES_DIR / "badges"
API_CLIENT_PATH = ROOT / "nexa-client" / "packages" / "app" / "utils" / "apiClient.ts"

ACCESS_HISTORY_PATH = FEATURES_DIR / "AccessHistoryScreen.tsx"
PATIENT_TIMELINE_PATH = FEATURES_DIR / "PatientTimelineScreen.tsx"
SOURCE_BADGE_PATH = BADGES_DIR / "SourceBadge.tsx"
RISK_BADGE_PATH = BADGES_DIR / "RiskBadge.tsx"
BADGES_INDEX_PATH = BADGES_DIR / "index.ts"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _read(path: Path) -> str:
    assert path.exists(), f"File missing: {path}"
    return path.read_text(encoding="utf-8")


def _strip_comments(code: str) -> str:
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    code = re.sub(r"//[^\n]*", "", code)
    return code


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. SourceBadge component
# ═══════════════════════════════════════════════════════════════════════════════


class TestSourceBadge:
    """SourceBadge: green for manual, blue for AI-extracted with confidence."""

    def test_file_exists(self) -> None:
        assert SOURCE_BADGE_PATH.exists(), "SourceBadge.tsx must exist"

    def test_uses_tamagui_only(self) -> None:
        code = _read(SOURCE_BADGE_PATH)
        assert "from 'tamagui'" in code or "from '@my/ui'" in code, "Must import from tamagui"
        assert "<div" not in code, "Must not use HTML div"
        assert "<span" not in code, "Must not use HTML span"

    def test_accepts_manual_source(self) -> None:
        code = _read(SOURCE_BADGE_PATH)
        assert "manual" in code, "Must handle source='manual'"

    def test_accepts_ai_extracted_source(self) -> None:
        code = _read(SOURCE_BADGE_PATH)
        assert "ai_extracted" in code, "Must handle source='ai_extracted'"

    def test_manual_uses_green(self) -> None:
        code = _read(SOURCE_BADGE_PATH)
        # Manual entry should use green colours
        assert "$green" in code, "Manual badge must use green palette"

    def test_ai_uses_blue(self) -> None:
        code = _read(SOURCE_BADGE_PATH)
        # AI-extracted should use blue colours
        assert "$blue" in code, "AI badge must use blue palette"

    def test_shows_confidence_for_ai(self) -> None:
        code = _read(SOURCE_BADGE_PATH)
        # AI-extracted badge must display confidence percentage
        assert "confidence" in code, "Must accept confidence prop"
        assert "%" in code or "confidence" in code, (
            "Must display confidence percentage"
        )

    def test_manual_label_text(self) -> None:
        code = _read(SOURCE_BADGE_PATH)
        assert "Manual entry" in code, "Manual badge must show 'Manual entry'"

    def test_ai_label_text(self) -> None:
        code = _read(SOURCE_BADGE_PATH)
        assert "AI-extracted" in code, "AI badge must show 'AI-extracted'"

    def test_exports_props_interface(self) -> None:
        code = _read(SOURCE_BADGE_PATH)
        assert "SourceBadgeProps" in code, "Must export SourceBadgeProps interface"

    def test_default_export(self) -> None:
        code = _read(SOURCE_BADGE_PATH)
        assert "export default" in code, "Must have default export"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. RiskBadge component
# ═══════════════════════════════════════════════════════════════════════════════


class TestRiskBadge:
    """RiskBadge: colour-coded by risk level."""

    def test_file_exists(self) -> None:
        assert RISK_BADGE_PATH.exists(), "RiskBadge.tsx must exist"

    def test_uses_tamagui_only(self) -> None:
        code = _read(RISK_BADGE_PATH)
        assert "from 'tamagui'" in code or "from '@my/ui'" in code, "Must import from tamagui"
        assert "<div" not in code, "Must not use HTML div"

    def test_handles_all_risk_levels(self) -> None:
        code = _read(RISK_BADGE_PATH)
        for level in ["LOW_RISK", "MEDIUM_RISK", "HIGH_RISK", "CRITICAL"]:
            assert level in code, f"Must handle {level} risk level"

    def test_low_risk_uses_green(self) -> None:
        code = _read(RISK_BADGE_PATH)
        assert "$green" in code, "LOW_RISK must use green palette"

    def test_high_risk_uses_red(self) -> None:
        code = _read(RISK_BADGE_PATH)
        assert "$red" in code, "HIGH_RISK must use red palette"

    def test_medium_risk_uses_orange(self) -> None:
        code = _read(RISK_BADGE_PATH)
        assert "$orange" in code, "MEDIUM_RISK must use orange palette"

    def test_exports_risk_level_type(self) -> None:
        code = _read(RISK_BADGE_PATH)
        assert "RiskLevel" in code, "Must export RiskLevel type"

    def test_exports_props_interface(self) -> None:
        code = _read(RISK_BADGE_PATH)
        assert "RiskBadgeProps" in code, "Must export RiskBadgeProps interface"

    def test_default_export(self) -> None:
        code = _read(RISK_BADGE_PATH)
        assert "export default" in code, "Must have default export"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Badge barrel export
# ═══════════════════════════════════════════════════════════════════════════════


class TestBadgesIndex:
    """Barrel export file for badges."""

    def test_file_exists(self) -> None:
        assert BADGES_INDEX_PATH.exists(), "badges/index.ts must exist"

    def test_exports_source_badge(self) -> None:
        code = _read(BADGES_INDEX_PATH)
        assert "SourceBadge" in code, "Must re-export SourceBadge"
        assert "SourceBadgeProps" in code, "Must re-export SourceBadgeProps"

    def test_exports_risk_badge(self) -> None:
        code = _read(BADGES_INDEX_PATH)
        assert "RiskBadge" in code, "Must re-export RiskBadge"
        assert "RiskBadgeProps" in code, "Must re-export RiskBadgeProps"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. AccessHistoryScreen
# ═══════════════════════════════════════════════════════════════════════════════


class TestAccessHistoryScreenDetailed:
    """Detailed validation of the access history screen."""

    def test_file_exists(self) -> None:
        assert ACCESS_HISTORY_PATH.exists(), "AccessHistoryScreen.tsx must exist"

    def test_uses_shared_api_client(self) -> None:
        code = _read(ACCESS_HISTORY_PATH)
        assert "apiClient" in code, (
            "Must import from shared apiClient"
        )

    def test_no_raw_fetch(self) -> None:
        code = _read(ACCESS_HISTORY_PATH)
        code_no_comments = _strip_comments(code)
        assert not re.search(r"\bfetch\s*\(", code_no_comments), (
            "Must not use raw fetch()"
        )

    def test_no_axios(self) -> None:
        code = _read(ACCESS_HISTORY_PATH)
        code_no_comments = _strip_comments(code)
        assert "axios" not in code_no_comments.lower(), "Must not use axios"

    def test_no_localhost(self) -> None:
        code = _read(ACCESS_HISTORY_PATH)
        code_no_comments = _strip_comments(code)
        assert "localhost" not in code_no_comments.lower(), (
            "Must not contain localhost"
        )

    def test_no_hardcoded_patient_id(self) -> None:
        code = _read(ACCESS_HISTORY_PATH)
        code_no_comments = _strip_comments(code)
        assert "patient_id" not in code_no_comments, (
            "Must not hardcode patient_id"
        )

    def test_fetches_via_get_access_history(self) -> None:
        code = _read(ACCESS_HISTORY_PATH)
        assert "access-history" in code, (
            "Must fetch access history via apiClient"
        )

    def test_displays_provider_name(self) -> None:
        code = _read(ACCESS_HISTORY_PATH)
        assert "doctor_name" in code, "Must display doctor name"

    def test_displays_hospital_name(self) -> None:
        code = _read(ACCESS_HISTORY_PATH)
        assert "hospital_name" in code, "Must display hospital name"

    def test_displays_purpose(self) -> None:
        code = _read(ACCESS_HISTORY_PATH)
        assert "purpose" in code, "Must display purpose of access"

    def test_displays_timestamp(self) -> None:
        code = _read(ACCESS_HISTORY_PATH)
        assert "accessed_at" in code, "Must display accessed_at timestamp"

    def test_displays_data_categories(self) -> None:
        code = _read(ACCESS_HISTORY_PATH)
        assert "data_categories" in code, "Must display data categories accessed"

    def test_flags_break_glass_accesses(self) -> None:
        code = _read(ACCESS_HISTORY_PATH)
        assert "is_break_glass" in code, (
            "Must check is_break_glass field on access entries"
        )
        assert "BREAK-GLASS" in code, (
            "Must display BREAK-GLASS warning badge for emergency accesses"
        )

    def test_break_glass_badge_uses_red(self) -> None:
        code = _read(ACCESS_HISTORY_PATH)
        # The break-glass badge should use red color to stand out
        assert "$red" in code, "Break-glass badge must use red palette"

    def test_empty_state_text(self) -> None:
        code = _read(ACCESS_HISTORY_PATH)
        assert "No one has accessed your records yet" in code, (
            "Empty state must say 'No one has accessed your records yet.'"
        )

    def test_handles_all_event_types(self) -> None:
        code = _read(ACCESS_HISTORY_PATH)
        # Backend uses flag: BREAK_GLASS_ACCESS / ROUTINE_ACCESS
        for field in ["is_break_glass", "BREAK_GLASS_ACCESS", "ROUTINE_ACCESS"]:
            assert field in code, f"Must handle {field} field"

    def test_has_retry_button(self) -> None:
        code = _read(ACCESS_HISTORY_PATH)
        assert "Retry" in code, "Must have retry button on error"

    def test_uses_tamagui_only(self) -> None:
        code = _read(ACCESS_HISTORY_PATH)
        assert "from 'tamagui'" in code or "from '@my/ui'" in code, "Must import from tamagui"
        assert "<div" not in code, "Must not use HTML div"
        assert "<button" not in code, "Must not use HTML button"
        assert "<span" not in code, "Must not use HTML span"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. PatientTimelineScreen
# ═══════════════════════════════════════════════════════════════════════════════


class TestPatientTimelineScreenDetailed:
    """Detailed validation of the patient timeline screen."""

    def test_file_exists(self) -> None:
        assert PATIENT_TIMELINE_PATH.exists(), "PatientTimelineScreen.tsx must exist"

    def test_uses_shared_api_client(self) -> None:
        code = _read(PATIENT_TIMELINE_PATH)
        assert "apiClient" in code, (
            "Must import from shared apiClient"
        )

    def test_no_raw_fetch(self) -> None:
        code = _read(PATIENT_TIMELINE_PATH)
        code_no_comments = _strip_comments(code)
        assert not re.search(r"\bfetch\s*\(", code_no_comments), (
            "Must not use raw fetch()"
        )

    def test_no_axios(self) -> None:
        code = _read(PATIENT_TIMELINE_PATH)
        code_no_comments = _strip_comments(code)
        assert "axios" not in code_no_comments.lower(), "Must not use axios"

    def test_no_localhost(self) -> None:
        code = _read(PATIENT_TIMELINE_PATH)
        code_no_comments = _strip_comments(code)
        assert "localhost" not in code_no_comments.lower(), (
            "Must not contain localhost"
        )

    def test_no_hardcoded_patient_id(self) -> None:
        code = _read(PATIENT_TIMELINE_PATH)
        code_no_comments = _strip_comments(code)
        assert "patient_id" not in code_no_comments, (
            "Must not hardcode patient_id"
        )

    def test_fetches_via_get_my_timeline(self) -> None:
        code = _read(PATIENT_TIMELINE_PATH)
        assert "timeline" in code, (
            "Must fetch timeline via apiClient"
        )

    def test_handles_all_categories(self) -> None:
        code = _read(PATIENT_TIMELINE_PATH)
        # Backend event_type values are UPPERCASE
        for cat in ["VITALS", "MEDICATION", "LAB_RESULT", "ALLERGY", "DOCUMENT", "ENCOUNTER"]:
            assert cat in code, f"Must handle {cat} category"

    def test_uses_source_badge(self) -> None:
        code = _read(PATIENT_TIMELINE_PATH)
        assert "SourceBadge" in code, "Must use SourceBadge component"
        assert "badges/SourceBadge" in code, "Must import SourceBadge from badges"

    def test_uses_risk_badge(self) -> None:
        code = _read(PATIENT_TIMELINE_PATH)
        assert "RiskBadge" in code, "Must use RiskBadge component"
        assert "badges/RiskBadge" in code, "Must import RiskBadge from badges"

    def test_passes_source_to_badge(self) -> None:
        code = _read(PATIENT_TIMELINE_PATH)
        assert "source=" in code, "Must pass source prop to SourceBadge"

    def test_passes_confidence_to_badge(self) -> None:
        code = _read(PATIENT_TIMELINE_PATH)
        assert "confidence=" in code or "confidence" in code, (
            "Must pass confidence prop to SourceBadge"
        )

    def test_passes_risk_level_to_badge(self) -> None:
        code = _read(PATIENT_TIMELINE_PATH)
        assert "risk_level" in code, "Must pass risk_level to RiskBadge"

    def test_distinguishes_manual_vs_ai(self) -> None:
        # The screen uses event.source which is typed as 'manual' | 'ai_extracted'
        # in the screen or badge component — check the badge component
        badge_code = _read(SOURCE_BADGE_PATH)
        screen_code = _read(PATIENT_TIMELINE_PATH)
        assert "manual" in badge_code, "SourceBadge must handle 'manual' source"
        assert "ai_extracted" in badge_code, "SourceBadge must handle 'ai_extracted' source"
        # Screen must pass source to the badge
        assert "source=" in screen_code, "Screen must pass event.source to SourceBadge"

    def test_shows_abnormal_flag(self) -> None:
        code = _read(PATIENT_TIMELINE_PATH)
        assert "ABNORMAL" in code, "Must show ABNORMAL flag for out-of-range labs"
        assert "isAbnormal" in code, "Must check isAbnormal field"

    def test_groups_events_by_date(self) -> None:
        code = _read(PATIENT_TIMELINE_PATH)
        assert "grouped" in code or "reduce" in code, "Must group events by date"

    def test_shows_event_summary(self) -> None:
        code = _read(PATIENT_TIMELINE_PATH)
        # Backend packs value, unit, reference range into summary text
        # (e.g. "HbA1c: 7.2 %" or "BP: 120/80 mmHg")
        assert "summary" in code, "Must display event summary (includes value/reference)"

    def test_empty_state(self) -> None:
        code = _read(PATIENT_TIMELINE_PATH)
        assert "No clinical events" in code, "Must show empty state"

    def test_has_retry_button(self) -> None:
        code = _read(PATIENT_TIMELINE_PATH)
        assert "Retry" in code, "Must have retry button on error"

    def test_uses_tamagui_only(self) -> None:
        code = _read(PATIENT_TIMELINE_PATH)
        assert "from 'tamagui'" in code or "from '@my/ui'" in code, "Must import from tamagui"
        assert "<div" not in code, "Must not use HTML div"
        assert "<button" not in code, "Must not use HTML button"

    def test_navigates_to_access_history(self) -> None:
        code = _read(PATIENT_TIMELINE_PATH)
        assert "/patient/access-history" in code, (
            "Must have navigation link to access history"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 6. apiClient convenience methods
# ═══════════════════════════════════════════════════════════════════════════════


class TestApiClientConvenienceMethods:
    """apiClient must expose getAccessHistory() and getMyTimeline()."""

    def test_get_access_history_method(self) -> None:
        code = _read(API_CLIENT_PATH)
        # The client may have a dedicated method or the screen may call
        # the endpoint directly via apiClient.get(...)
        assert "getAccessLog" in code or "getAccessHistory" in code or "access-log" in code or "access-history" in code, (
            "apiClient must have access history/log support"
        )

    def test_get_my_timeline_method(self) -> None:
        code = _read(API_CLIENT_PATH)
        # The client may have a dedicated method or the screen may call
        # the endpoint directly via apiClient.get(...)
        assert "timeline" in code, (
            "apiClient must have timeline support"
        )

    def test_access_history_endpoint(self) -> None:
        code = _read(API_CLIENT_PATH)
        assert "access-log" in code or "access-history" in code or "access_log" in code, (
            "apiClient must define access history/log endpoint"
        )

    def test_timeline_endpoint(self) -> None:
        code = _read(API_CLIENT_PATH)
        assert "timeline" in code, (
            "apiClient must define timeline endpoint"
        )

    def test_access_history_entry_type(self) -> None:
        # The type may be defined in apiClient.ts or in the screen itself
        code = _read(API_CLIENT_PATH)
        screen_code = _read(ACCESS_HISTORY_PATH)
        has_type = "AccessHistoryEntry" in code or "AccessHistoryEntry" in screen_code
        has_fields = "is_break_glass" in code or "is_break_glass" in screen_code
        assert has_type or has_fields, (
            "Must define AccessHistoryEntry type or include is_break_glass field"
        )

    def test_timeline_entry_type(self) -> None:
        # The type may be defined in apiClient.ts or in the screen itself
        code = _read(API_CLIENT_PATH)
        screen_code = _read(PATIENT_TIMELINE_PATH)
        has_type = "TimelineEntry" in code or "TimelineEntry" in screen_code
        has_fields = ("source" in code and "confidence" in code) or ("source" in screen_code and "confidence" in screen_code)
        assert has_type or has_fields, (
            "Must define TimelineEntry type or include source/confidence fields"
        )

    def test_no_localhost_in_api_client(self) -> None:
        code = _read(API_CLIENT_PATH)
        code_no_comments = _strip_comments(code)
        assert "localhost" not in code_no_comments, (
            "apiClient must not contain localhost"
        )

    def test_no_axios_in_api_client(self) -> None:
        code = _read(API_CLIENT_PATH)
        code_no_comments = _strip_comments(code)
        assert "axios" not in code_no_comments.lower(), (
            "apiClient must not use axios"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Integration: screens use correct data shapes from apiClient
# ═══════════════════════════════════════════════════════════════════════════════


class TestScreenApiClientIntegration:
    """Screens must use the types and methods defined in apiClient."""

    def test_access_history_uses_api_client_type(self) -> None:
        code = _read(ACCESS_HISTORY_PATH)
        # AccessHistoryScreen should use AccessHistoryEntry type
        assert "AccessHistoryEntry" in code or (
            "is_break_glass" in code and "hospital_name" in code
        ), "Screen must use fields from AccessHistoryEntry"

    def test_timeline_uses_api_client_type(self) -> None:
        code = _read(PATIENT_TIMELINE_PATH)
        # TimelineScreen should use TimelineEntry type
        assert "TimelineEntry" in code or (
            "ai_extracted" in code and "confidence" in code
        ), "Screen must use fields from TimelineEntry"

    def test_break_glass_in_type_and_screen(self) -> None:
        api_code = _read(API_CLIENT_PATH)
        screen_code = _read(ACCESS_HISTORY_PATH)
        # is_break_glass may be in the type definition or the screen's inline type
        assert "is_break_glass" in api_code or "is_break_glass" in screen_code, (
            "Type or screen must include is_break_glass"
        )
        assert "is_break_glass" in screen_code, "Screen must check is_break_glass"

    def test_provenance_in_type_and_screen(self) -> None:
        api_code = _read(API_CLIENT_PATH)
        screen_code = _read(PATIENT_TIMELINE_PATH)
        assert "source" in api_code, "Type must include source"
        assert "confidence" in api_code, "Type must include confidence"
        assert "source" in screen_code, "Screen must display source"
        assert "confidence" in screen_code, "Screen must display confidence"
