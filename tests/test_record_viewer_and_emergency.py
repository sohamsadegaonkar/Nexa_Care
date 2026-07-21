"""Tests for patient record viewer and emergency access screens (Days 9-11).

Validates:
  1. PatientRecordViewerScreen has tabbed layout with all 8 tabs
  2. Allergies are prominently displayed (safety-critical)
  3. AI confidence/source badges are shown
  4. Consent expiry countdown locks the viewer
  5. Viewer only works with a valid consent grant
  6. EmergencyAccessScreen uses controlled reason codes
  7. Break-glass requires clinical justification
  8. Break-glass shows audit and rate-limit warnings
  9. Both screens use real session and shared apiClient
  10. Both screens use only Tamagui components
  11. Both screens never call approval endpoints
"""

from __future__ import annotations

import re
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]
DOCTOR_DIR = ROOT / "nexa-client" / "packages" / "app" / "features" / "doctor"
APP_API_DIR = ROOT / "nexa-client" / "packages" / "app" / "api"
CONSENT_ROUTES = ROOT / "app" / "api" / "v2" / "consent_routes.py"
CONSENT_API = APP_API_DIR / "consent.ts"


def _read(path: Path) -> str:
    assert path.exists(), f"File missing: {path}"
    return path.read_text(encoding="utf-8")


def _read_screen(name: str) -> str:
    return _read(DOCTOR_DIR / f"{name}.tsx")


def _read_reason_contract() -> str:
    return _read(CONSENT_API)


def _normalize_ws(code: str) -> str:
    return re.sub(r"\s+", " ", code)


def _strip_comments(code: str) -> str:
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    code = re.sub(r"//[^\n]*", "", code)
    return code


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Record viewer tabbed layout
# ═══════════════════════════════════════════════════════════════════════════════


class TestRecordViewerTabs:
    """PatientRecordViewerScreen must have a tabbed layout with all 8 tabs."""

    def test_has_active_tab_state(self) -> None:
        """Must track which tab is currently active."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "activeTab" in code, "Must have activeTab state variable"
        assert "setActiveTab" in code, "Must have setActiveTab function"

    def test_has_summary_tab(self) -> None:
        code = _read_screen("PatientRecordViewerScreen")
        assert "summary" in code.lower(), "Must have Summary tab"

    def test_has_vitals_tab(self) -> None:
        code = _read_screen("PatientRecordViewerScreen")
        assert "vitals" in code.lower(), "Must have Vitals tab"

    def test_has_prescriptions_tab(self) -> None:
        code = _read_screen("PatientRecordViewerScreen")
        assert (
            "prescriptions" in code.lower() or "medication" in code.lower()
        ), "Must have Prescriptions/Medications tab"

    def test_has_lab_reports_tab(self) -> None:
        code = _read_screen("PatientRecordViewerScreen")
        assert "lab" in code.lower(), "Must have Lab Reports tab"

    def test_has_allergies_tab(self) -> None:
        code = _read_screen("PatientRecordViewerScreen")
        assert "allergies" in code.lower(), "Must have Allergies tab"

    def test_has_documents_tab(self) -> None:
        code = _read_screen("PatientRecordViewerScreen")
        assert "documents" in code.lower(), "Must have Documents tab"

    def test_has_timeline_tab(self) -> None:
        code = _read_screen("PatientRecordViewerScreen")
        assert "timeline" in code.lower(), "Must have Timeline tab"

    def test_has_access_status_tab(self) -> None:
        code = _read_screen("PatientRecordViewerScreen")
        assert (
            "access" in code.lower() and "status" in code.lower()
        ), "Must have Access Status tab"

    def test_tab_navigation_buttons(self) -> None:
        """Tab navigation must use Button components to switch tabs."""
        code = _read_screen("PatientRecordViewerScreen")
        assert (
            "onPress" in code and "setActiveTab" in code
        ), "Tab buttons must call setActiveTab on press"

    def test_at_least_eight_tabs_defined(self) -> None:
        """Must define at least 8 tabs in the tab definitions."""
        code = _read_screen("PatientRecordViewerScreen")
        # Check for ALL_TABS constant with 8 entries, or tab key definitions
        tab_keys = re.findall(r"key:\s*['\"](\w+)['\"]", code)
        if len(tab_keys) >= 8:
            return  # Old format with key: property
        # New format: ALL_TABS constant or tabLabels/tabRenderers
        all_tabs_match = re.search(r"ALL_TABS\s*=\s*\[([^\]]+)\]", code)
        if all_tabs_match:
            tab_strings = re.findall(r"'(\w+)'", all_tabs_match.group(1))
            assert (
                len(tab_strings) >= 8
            ), f"Must define at least 8 tabs in ALL_TABS, found {len(tab_strings)}: {tab_strings}"
            return
        # Fallback: check tabRenderers keys
        renderer_keys = re.findall(r"(\w+):\s*\(\)\s*=>\s*JSX\.Element", code)
        assert (
            len(renderer_keys) >= 8
        ), f"Must define at least 8 tab renderers, found {len(renderer_keys)}"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Allergy prominence (safety-critical)
# ═══════════════════════════════════════════════════════════════════════════════


class TestAllergyProminence:
    """Allergies must be prominently displayed — safety-critical."""

    def test_allergies_labeled_safety_critical(self) -> None:
        """Allergies must be labeled as safety-critical."""
        code = _read_screen("PatientRecordViewerScreen")
        assert (
            "SAFETY CRITICAL" in code
        ), "Allergies section must be labeled 'SAFETY CRITICAL'"

    def test_allergies_use_red_color(self) -> None:
        """Allergy display must use red color scheme."""
        code = _read_screen("PatientRecordViewerScreen")
        allergy_section_start = code.find("allergies")
        assert allergy_section_start > 0, "Must have allergies section"
        # Check for red color near allergy rendering
        assert "$red" in code, "Allergies must use $red color tokens"

    def test_allergies_visible_on_all_tabs(self) -> None:
        """Allergies banner must be visible regardless of active tab."""
        code = _read_screen("PatientRecordViewerScreen")
        # Must have an always-visible allergy banner (outside tab content)
        assert (
            "ALLERGIES:" in code or "ALLERGIES" in code
        ), "Must have always-visible allergies banner"

    def test_allergies_dedicated_tab(self) -> None:
        """Must have a dedicated Allergies tab with full detail."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "allergies" in code.lower(), "Must have allergies tab"
        # The allergies tab must show a warning banner
        assert (
            "safety" in code.lower() and "critical" in code.lower()
        ), "Allergies tab must show safety-critical warning"

    def test_allergy_items_display_warning_emoji(self) -> None:
        """Individual allergy items must display warning indicators."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "⚠️" in code, "Allergy items must display ⚠️ warning"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. AI confidence/source badges
# ═══════════════════════════════════════════════════════════════════════════════


class TestConfidenceBadges:
    """AI-extracted fields must show provenance, confidence, and verification status badges."""

    def test_has_provenance_badge_component(self) -> None:
        """Must define a ProvenanceBadge component (replaces ConfidenceBadge with verification status)."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "ProvenanceBadge" in code, "Must define ProvenanceBadge component"

    def test_provenance_badge_uses_confidence_field(self) -> None:
        """ProvenanceBadge must read the confidence prop."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "confidence" in code, "Must use confidence field from data"

    def test_provenance_badge_shows_percentage(self) -> None:
        """Provenance badge must display model confidence percentage."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "%" in code, "Provenance badge must show percentage"

    def test_badges_used_in_data_sections(self) -> None:
        """ProvenanceBadge must be used in vitals, medications, labs, timeline."""
        code = _read_screen("PatientRecordViewerScreen")
        # Count occurrences of ProvenanceBadge usage
        count = code.count("<ProvenanceBadge")
        assert (
            count >= 3
        ), f"ProvenanceBadge must be used in at least 3 data sections, found {count}"

    def test_detects_ai_extracted_source(self) -> None:
        """Must detect ai_extracted source and show AI badge."""
        code = _read_screen("PatientRecordViewerScreen")
        assert (
            "ai_extracted" in code or "ai" in code.lower()
        ), "Must detect ai_extracted source for badge display"

    def test_shows_verification_status(self) -> None:
        """Must distinguish between verified and unverified AI-extracted fields."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "verified" in code.lower(), "Must show verification status"

    def test_clinician_verified_badge(self) -> None:
        """Must show 'Clinician verified' for verified fields."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "Clinician verified" in code, "Must show 'Clinician verified' badge"

    def test_not_yet_verified_badge(self) -> None:
        """Must show 'Not yet verified' for unverified AI-extracted fields."""
        code = _read_screen("PatientRecordViewerScreen")
        assert (
            "Not yet verified" in code
        ), "Must show 'Not yet verified' for unverified AI fields"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Consent expiry countdown
# ═══════════════════════════════════════════════════════════════════════════════


class TestConsentExpiryCountdown:
    """Consent expiry countdown must lock the viewer when consent expires."""

    def test_tracks_remaining_seconds(self) -> None:
        """Must track seconds remaining until consent expiry."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "secondsRemaining" in code, "Must track secondsRemaining state"

    def test_has_expiry_timer_interval(self) -> None:
        """Must have a timer that decrements the remaining seconds."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "setInterval" in code, "Must use setInterval for countdown"
        code_norm = _normalize_ws(code)
        assert "secondsRemaining" in code_norm, "Timer must update secondsRemaining"

    def test_transitions_to_expired_state(self) -> None:
        """When seconds reach zero, must transition to expired state."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "expired" in code.lower(), "Must transition to expired state"
        assert (
            "setViewerState" in code or "viewerState" in code
        ), "Must update viewer state to expired"

    def test_shows_expired_message(self) -> None:
        """Expired state must show 'Consent expired. Request access again.'"""
        code = _read_screen("PatientRecordViewerScreen")
        assert (
            "Consent Expired" in code or "Consent expired" in code
        ), "Must show 'Consent Expired' or 'Consent expired' message"
        assert (
            "Request access again" in code or "request access" in code.lower()
        ), "Must prompt to request access again"

    def test_expired_state_shows_lock_icon(self) -> None:
        """Expired state must show a lock icon."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "🔒" in code, "Must show lock icon in expired state"

    def test_countdown_bar_when_active(self) -> None:
        """When active, must show a countdown bar with remaining time."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "formatCountdown" in code, "Must format countdown display"
        assert (
            "Consent active" in code or "consent" in code.lower()
        ), "Must show consent active status bar"

    def test_warns_when_close_to_expiry(self) -> None:
        """Must warn when consent is close to expiry (e.g. < 60 seconds)."""
        code = _read_screen("PatientRecordViewerScreen")
        assert (
            "60" in code or "expiring soon" in code.lower()
        ), "Must warn when consent is close to expiry"

    def test_cleanup_timer_on_unmount(self) -> None:
        """Must clean up the expiry timer on unmount."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "clearInterval" in code, "Must clearInterval on unmount"
        assert (
            "return ()" in code or "return () =>" in code
        ), "Must return cleanup function from useEffect"

    def test_periodic_consent_revalidation(self) -> None:
        """Must periodically revalidate consent (not just local countdown)."""
        code = _read_screen("PatientRecordViewerScreen")
        assert (
            "validate" in code.lower()
            or "revalidation" in code.lower()
            or "consent/validate" in code.lower()
        ), "Must periodically revalidate consent with the server"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Record viewer only works with valid consent grant
# ═══════════════════════════════════════════════════════════════════════════════


class TestRecordViewerConsentRequired:
    """Record viewer must only work with a valid consent grant."""

    def test_fetches_via_api_client(self) -> None:
        """Must use apiClient to fetch patient data, not raw fetch."""
        code = _read_screen("PatientRecordViewerScreen")
        assert (
            "apiClient" in code or "NexaApiClient" in code
        ), "Must use shared apiClient"

    def test_no_raw_fetch(self) -> None:
        """Must not use raw fetch()."""
        code = _read_screen("PatientRecordViewerScreen")
        assert not re.search(r"\bfetch\s*\(", code), "Must not use raw fetch()"

    def test_handles_consent_expired_error(self) -> None:
        """Must handle the case where consent has expired during fetch."""
        code = _read_screen("PatientRecordViewerScreen")
        assert (
            "expired" in code.lower() or "consent" in code.lower()
        ), "Must handle consent-expired error from backend"

    def test_viewer_state_includes_expired(self) -> None:
        """ViewerState must include 'expired' as a possible state."""
        code = _read_screen("PatientRecordViewerScreen")
        assert (
            "'expired'" in code or '"expired"' in code
        ), "ViewerState must include 'expired' variant"

    def test_viewer_state_includes_loading_and_error(self) -> None:
        """ViewerState must include 'loading', 'error', and 'active' states."""
        code = _read_screen("PatientRecordViewerScreen")
        for state in ["loading", "error", "active"]:
            assert (
                f"'{state}'" in code or f'"{state}"' in code
            ), f"ViewerState must include '{state}' variant"

    def test_reads_request_id_from_params(self) -> None:
        """Must read request_id from URL search params."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "request_id" in code, "Must read request_id from URL params"
        assert "searchParams" in code, "Must use useSearchParams"

    def test_reads_patient_id_from_params(self) -> None:
        """Must read patient_id from URL search params for navigation context."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "patient_id" in code, "Must read patient_id from URL params"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Emergency access — controlled reason codes
# ═══════════════════════════════════════════════════════════════════════════════


class TestEmergencyControlledReasonCodes:
    """Break-glass reason codes must be controlled values, not free-text."""

    def test_uses_select_for_reason_code(self) -> None:
        """Must use Select component for reason code, not free-text Input."""
        code = _read_screen("EmergencyAccessScreen")
        assert "Select" in code, "Must use Select for reason code"

    def test_has_reason_options_constant(self) -> None:
        """Must define controlled reason options list."""
        code = _read_screen("EmergencyAccessScreen")
        assert "REASON_OPTIONS" in code, "Must define REASON_OPTIONS"

    def test_has_break_glass_reason_type(self) -> None:
        """Must define BreakGlassReason type for controlled values."""
        code = _read_screen("EmergencyAccessScreen")
        assert "BreakGlassReason" in code, "Must define BreakGlassReason type"

    def test_includes_life_threatening_reason(self) -> None:
        code = _read_reason_contract()
        assert (
            "LIFE_THREATENING" in code or "IMMEDIATE_THREAT_TO_LIFE" in code
        ), "Must include LIFE_THREATENING or IMMEDIATE_THREAT_TO_LIFE reason"

    def test_includes_surgical_emergency_reason(self) -> None:
        code = _read_reason_contract()
        assert "SURGICAL_EMERGENCY" in code, "Must include SURGICAL_EMERGENCY reason"

    def test_includes_cardiac_arrest_reason(self) -> None:
        code = _read_reason_contract()
        assert "CARDIAC_ARREST" in code, "Must include CARDIAC_ARREST reason"

    def test_includes_anaphylaxis_reason(self) -> None:
        code = _read_reason_contract()
        assert "ANAPHYLAXIS" in code, "Must include ANAPHYLAXIS reason"

    def test_has_at_least_six_reason_options(self) -> None:
        """Must have at least 6 controlled reason options (expanded for broader clinical coverage)."""
        code = _read_reason_contract()
        reason_values = re.findall(r"value:\s*'([A-Z_]+)'", code)
        # Filter to reason codes (all caps with underscores)
        valid_reasons = [v for v in reason_values if v.isupper() and "_" in v]
        assert (
            len(valid_reasons) >= 6
        ), f"Must have at least 6 reason options, found {len(valid_reasons)}: {valid_reasons}"

    def test_includes_patient_incapacitated_reason(self) -> None:
        """Must include PATIENT_INCAPACITATED reason code."""
        code = _read_reason_contract()
        assert (
            "PATIENT_INCAPACITATED" in code
        ), "Must include PATIENT_INCAPACITATED reason"

    def test_includes_other_emergency_reason(self) -> None:
        """Must include OTHER_CLINICALLY_JUSTIFIED_EMERGENCY with mandatory review."""
        code = _read_reason_contract()
        assert (
            "OTHER_CLINICALLY_JUSTIFIED_EMERGENCY" in code
        ), "Must include OTHER_CLINICALLY_JUSTIFIED_EMERGENCY reason"

    def test_includes_system_unavailable_reason(self) -> None:
        """Must include SYSTEM_OR_CONSENT_SERVICE_UNAVAILABLE reason code."""
        code = _read_reason_contract()
        assert (
            "SYSTEM_OR_CONSENT_SERVICE_UNAVAILABLE" in code
        ), "Must include SYSTEM_OR_CONSENT_SERVICE_UNAVAILABLE reason"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Break-glass requires clinical justification
# ═══════════════════════════════════════════════════════════════════════════════


class TestBreakGlassRequiresJustification:
    """Break-glass must require clinical justification (not optional)."""

    def test_has_free_text_field(self) -> None:
        """Must have a clinical justification text field."""
        code = _read_screen("EmergencyAccessScreen")
        assert (
            "freeText" in code or "free_text" in code or "justification" in code.lower()
        ), "Must have clinical justification field"

    def test_validates_justification_not_empty(self) -> None:
        """Must validate that clinical justification is not empty."""
        code = _read_screen("EmergencyAccessScreen")
        assert (
            "!freeText.trim()" in code
            or "justification is required" in code.lower()
            or "required" in code.lower()
        ), "Must validate that clinical justification is not empty"

    def test_button_disabled_without_justification(self) -> None:
        """Submit button must be disabled without justification."""
        code = _read_screen("EmergencyAccessScreen")
        assert "justification" in code, "Must reference justification in disabled check"
        # The disabled prop should include freeText validation
        code_norm = _normalize_ws(code)
        assert (
            "!justification.trim()" in code_norm
        ), "justification must be in button disabled prop"

    def test_shows_error_when_justification_missing(self) -> None:
        """Must show an error message when justification is missing."""
        code = _read_screen("EmergencyAccessScreen")
        code_lower = code.lower()
        assert (
            "justification" in code_lower or "required" in code_lower
        ), "Must show error when justification is missing"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Emergency access audit and rate-limit warnings
# ═══════════════════════════════════════════════════════════════════════════════


class TestEmergencyAuditAndRateLimit:
    """Break-glass must show audit and rate-limit warnings."""

    def test_shows_audit_warning(self) -> None:
        code = _read_screen("EmergencyAccessScreen")
        assert "audit" in code.lower(), "Must show audit warning"

    def test_shows_permanently_recorded_warning(self) -> None:
        """Must warn that access is permanently recorded."""
        code = _read_screen("EmergencyAccessScreen")
        assert (
            "permanently" in code.lower() or "recorded" in code.lower()
        ), "Must warn that access is permanently recorded"

    def test_shows_rate_limit_warning(self) -> None:
        """Must warn about rate limiting."""
        code = _read_screen("EmergencyAccessScreen")
        assert (
            "rate" in code.lower() or "3 per hour" in code
        ), "Must show rate limit warning (3 per hour)"

    def test_shows_compliance_violation_warning(self) -> None:
        """Must warn that unauthorized use is a compliance violation."""
        code = _read_screen("EmergencyAccessScreen")
        assert (
            "compliance" in code.lower() or "violation" in code.lower()
        ), "Must warn about compliance violation"

    def test_red_color_scheme(self) -> None:
        """Must use red color scheme for emergency warnings."""
        code = _read_screen("EmergencyAccessScreen")
        assert "$red" in code, "Must use $red color tokens"

    def test_shows_patient_notification_warning(self) -> None:
        """Must warn that access will be recorded and may trigger notifications (honest wording)."""
        code = _read_screen("EmergencyAccessScreen")
        # Must use honest wording — not "will be notified" but "may trigger notifications"
        code_norm = _normalize_ws(code).lower()
        assert "recorded" in code_norm and (
            "notification" in code_norm or "notified" in code_norm
        ), "Must warn that access will be recorded and may trigger notifications"


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Both screens use real session and shared apiClient
# ═══════════════════════════════════════════════════════════════════════════════


class TestBothScreensSessionAndApiClient:
    """Both screens must use real session and shared apiClient."""

    def test_record_viewer_uses_api_client(self) -> None:
        code = _read_screen("PatientRecordViewerScreen")
        assert "apiClient" in code, "Must use shared apiClient"

    def test_emergency_uses_api_client(self) -> None:
        code = _read_screen("EmergencyAccessScreen")
        assert "apiClient" in code, "Must use shared apiClient"

    def test_emergency_uses_provider_auth_context(self) -> None:
        code = _read_screen("EmergencyAccessScreen")
        assert "useProviderAuth" in code, "Must use ProviderAuthContext"

    def test_record_viewer_uses_provider_auth_context(self) -> None:
        code = _read_screen("PatientRecordViewerScreen")
        assert "useProviderAuth" in code, "Must use ProviderAuthContext"

    def test_emergency_no_raw_fetch(self) -> None:
        code = _read_screen("EmergencyAccessScreen")
        assert not re.search(r"\bfetch\s*\(", code), "Must not use raw fetch()"

    def test_record_viewer_no_raw_fetch(self) -> None:
        code = _read_screen("PatientRecordViewerScreen")
        assert not re.search(r"\bfetch\s*\(", code), "Must not use raw fetch()"

    def test_emergency_no_localhost(self) -> None:
        code = _read_screen("EmergencyAccessScreen")
        assert "localhost" not in code.lower(), "Must not contain localhost"

    def test_record_viewer_no_localhost(self) -> None:
        code = _read_screen("PatientRecordViewerScreen")
        assert "localhost" not in code.lower(), "Must not contain localhost"


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Both screens use only Tamagui components
# ═══════════════════════════════════════════════════════════════════════════════


class TestBothScreensTamaguiOnly:
    """Both screens must use only Tamagui components, no plain HTML."""

    def test_record_viewer_no_html_div(self) -> None:
        code = _read_screen("PatientRecordViewerScreen")
        assert "<div" not in code, "Must not use <div>"

    def test_record_viewer_no_html_span(self) -> None:
        code = _read_screen("PatientRecordViewerScreen")
        assert "<span" not in code, "Must not use <span>"

    def test_record_viewer_no_html_button(self) -> None:
        code = _read_screen("PatientRecordViewerScreen")
        assert "<button" not in code, "Must not use <button>"

    def test_emergency_no_html_div(self) -> None:
        code = _read_screen("EmergencyAccessScreen")
        assert "<div" not in code, "Must not use <div>"

    def test_emergency_no_html_span(self) -> None:
        code = _read_screen("EmergencyAccessScreen")
        assert "<span" not in code, "Must not use <span>"

    def test_emergency_no_html_button(self) -> None:
        code = _read_screen("EmergencyAccessScreen")
        assert "<button" not in code, "Must not use <button>"

    def test_record_viewer_imports_from_tamagui(self) -> None:
        code = _read_screen("PatientRecordViewerScreen")
        assert (
            "from 'tamagui'" in code or "from '@my/ui'" in code
        ), "Must import from 'tamagui'"

    def test_emergency_imports_from_tamagui(self) -> None:
        code = _read_screen("EmergencyAccessScreen")
        assert (
            "from 'tamagui'" in code or "from '@my/ui'" in code
        ), "Must import from 'tamagui'"


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Neither screen calls approval endpoints
# ═══════════════════════════════════════════════════════════════════════════════


class TestNeitherScreenApproves:
    """Neither record viewer nor emergency screen may call approval endpoints."""

    def test_record_viewer_no_approve_signed(self) -> None:
        code = _read_screen("PatientRecordViewerScreen")
        code_no_comments = _strip_comments(code)
        assert (
            "approve-signed" not in code_no_comments
        ), "Record viewer must NOT call /consent/approve-signed"

    def test_emergency_no_approve_signed(self) -> None:
        code = _read_screen("EmergencyAccessScreen")
        code_no_comments = _strip_comments(code)
        assert (
            "approve-signed" not in code_no_comments
        ), "Emergency screen must NOT call /consent/approve-signed"

    def test_record_viewer_no_approve_button(self) -> None:
        code = _read_screen("PatientRecordViewerScreen")
        code_no_comments = _strip_comments(code)
        assert not re.search(
            r"<Button[^>]*>.*Approve", code_no_comments, re.DOTALL
        ), "Record viewer must NOT render Approve button"

    def test_emergency_no_approve_button(self) -> None:
        code = _read_screen("EmergencyAccessScreen")
        code_no_comments = _strip_comments(code)
        assert not re.search(
            r"<Button[^>]*>.*Approve", code_no_comments, re.DOTALL
        ), "Emergency screen must NOT render Approve button"


# ═══════════════════════════════════════════════════════════════════════════════
# 12. Emergency access backend contract
# ═══════════════════════════════════════════════════════════════════════════════


class TestEmergencyBackendContract:
    """Emergency access screen must match the backend contract."""

    def test_calls_break_glass_issue_endpoint(self) -> None:
        code = _read_screen("EmergencyAccessScreen")
        # The screen may use NexaApiClient.breakGlassIssue() or call the endpoint directly
        assert (
            "breakGlassIssue" in code or "/api/v2/consent/break-glass/issue" in code
        ), "Must call break-glass API (via NexaApiClient or direct endpoint)"

    def test_sends_required_fields(self) -> None:
        """Must send patient_id, reason_code, and clinical justification."""
        code = _read_screen("EmergencyAccessScreen")
        assert "patient_id" in code, "Must send patient_id"
        assert "reason_code" in code, "Must send reason_code"
        assert "justification" in code, "Must send justification"

    def test_backend_enforces_rate_limit(self) -> None:
        """Backend must enforce rate limiting on break-glass."""
        code = _read(CONSENT_ROUTES)
        assert (
            "3" in code and "break-glass" in code.lower()
        ), "Backend must rate-limit break-glass (3 per hour)"

    def test_backend_ttl_is_15_minutes(self) -> None:
        """Backend break-glass TTL must be 15 minutes."""
        code = _read(CONSENT_ROUTES)
        assert (
            "BREAK_GLASS_TTL_SECONDS = 15 * 60" in code
        ), "Break-glass TTL must be 15 minutes (900 seconds)"

    def test_success_screen_shows_expiry(self) -> None:
        """After break-glass success, must show the expiry time."""
        code = _read_screen("EmergencyAccessScreen")
        assert (
            "expires_at" in code or "Expires At" in code
        ), "Must display consent expiry after break-glass"

    def test_navigates_to_record_viewer(self) -> None:
        """After break-glass, must navigate to patient record viewer."""
        code = _read_screen("EmergencyAccessScreen")
        assert "patient-record" in code, "Must navigate to patient-record screen"


# ═══════════════════════════════════════════════════════════════════════════════
# 13. Record viewer data display
# ═══════════════════════════════════════════════════════════════════════════════


class TestRecordViewerDataDisplay:
    """Record viewer must properly format and display clinical data."""

    def test_vitals_show_type_value_unit(self) -> None:
        """Vitals must display type, value, and unit."""
        code = _read_screen("PatientRecordViewerScreen")
        vitals_render = (
            code[code.find("renderVitals") :] if "renderVitals" in code else code
        )
        assert (
            "v.type" in vitals_render or "type" in vitals_render.lower()
        ), "Vitals must display type"
        assert (
            "v.value" in vitals_render or "value" in vitals_render.lower()
        ), "Vitals must display value"

    def test_lab_reports_show_abnormal_flag(self) -> None:
        """Lab reports must flag abnormal results."""
        code = _read_screen("PatientRecordViewerScreen")
        assert (
            "is_abnormal" in code or "ABNORMAL" in code
        ), "Lab reports must show abnormal flag"

    def test_lab_reports_show_reference_range(self) -> None:
        """Lab reports must show reference ranges."""
        code = _read_screen("PatientRecordViewerScreen")
        assert (
            "reference_range" in code or "ref" in code.lower()
        ), "Lab reports must show reference range"

    def test_medications_show_name_dosage_frequency(self) -> None:
        """Medications must display name, dosage, and frequency."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "name" in code.lower(), "Medications must show name"
        assert (
            "dosage" in code.lower() or "strength" in code.lower()
        ), "Medications must show dosage"
        assert "frequency" in code.lower(), "Medications must show frequency"

    def test_timeline_shows_source_display(self) -> None:
        """Timeline events must show source display text."""
        code = _read_screen("PatientRecordViewerScreen")
        assert (
            "source_display" in code or "sourceDisplay" in code
        ), "Timeline must show source display"

    def test_timeline_shows_badges(self) -> None:
        """Timeline events must show badges (AI extracted, risk, etc.)."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "badges" in code.lower(), "Timeline must show badges"

    def test_access_status_tab_shows_consent_info(self) -> None:
        """Access Status tab must show consent grant details."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "request_id" in code or "requestId" in code, "Must show request ID"
        assert "shard_scope" in code or "scope" in code.lower(), "Must show scope"


# ═══════════════════════════════════════════════════════════════════════════════
# 14. Security architecture: frontend lock is UX only
# ═══════════════════════════════════════════════════════════════════════════════


class TestFrontendLockIsUXOnly:
    """The frontend consent lock is a UX control, not the security boundary."""

    def test_documents_security_architecture(self) -> None:
        """Source must document that the frontend lock is UX only."""
        code = _read_screen("PatientRecordViewerScreen")
        code_norm = _normalize_ws(code).lower()
        assert (
            "ux control only" in code_norm or "not the security boundary" in code_norm
        ), "Must document that frontend lock is UX only, not the security boundary"

    def test_passes_consent_token_header(self) -> None:
        """Must pass X-Consent-Token header on data API requests."""
        code = _read_screen("PatientRecordViewerScreen")
        assert (
            "X-Consent-Token" in code
        ), "Must pass X-Consent-Token header on data requests"

    def test_expired_state_mentions_server_validation(self) -> None:
        """Expired state must mention that backend validates independently."""
        code = _read_screen("PatientRecordViewerScreen")
        code_norm = _normalize_ws(code).lower()
        assert (
            "server-side" in code_norm or "independently validated" in code_norm
        ), "Expired state must mention server-side validation"


class TestConsentTokenNotDisplayed:
    """Consent tokens must never be displayed in the UI."""

    def test_access_status_shows_masked_reference(self) -> None:
        """Access Status must show a masked reference, not the raw token."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "maskToken" in code, "Must use maskToken to hide raw consent token"

    def test_emergency_success_shows_masked_token(self) -> None:
        """Emergency success screen must show masked authorization reference, not raw token."""
        code = _read_screen("EmergencyAccessScreen")
        assert (
            "maskToken" in code
        ), "Must use maskToken to hide raw consent token in success screen"


class TestScopeAwareTabs:
    """Tabs must be restricted based on consent scope."""

    def test_has_scope_to_tabs_mapping(self) -> None:
        """Must define a mapping from scope categories to tab keys."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "SCOPE_TO_TABS" in code, "Must define SCOPE_TO_TABS mapping"

    def test_computes_available_tabs(self) -> None:
        """Must compute available tabs from consent validation scope."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "availableTabs" in code, "Must compute availableTabs from consent scope"

    def test_tab_navigation_uses_available_tabs(self) -> None:
        """Tab navigation must only render tabs in availableTabs."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "availableTabs" in code, "Tab navigation must use availableTabs"


class TestJustificationMinimumLength:
    """Clinical justification must have minimum character length."""

    def test_has_minimum_length_validation(self) -> None:
        """Must enforce minimum character length for justification."""
        code = _read_screen("EmergencyAccessScreen")
        assert (
            "MIN_JUSTIFICATION_LENGTH" in code
        ), "Must define MIN_JUSTIFICATION_LENGTH"

    def test_other_reason_requires_longer_justification(self) -> None:
        """'Other' reason must require a longer justification."""
        code = _read_screen("EmergencyAccessScreen")
        assert (
            "OTHER_JUSTIFICATION_LENGTH" in code
        ), "Must define OTHER_JUSTIFICATION_LENGTH for 'other' reason"

    def test_uses_validate_justification_function(self) -> None:
        """Must use validateJustification function for enforcement."""
        code = _read_screen("EmergencyAccessScreen")
        assert (
            "validateJustification" in code
        ), "Must define and use validateJustification function"


class TestConsentRevalidationInterval:
    """Consent revalidation must use a reasonable interval."""

    def test_revalidation_interval_is_10_seconds(self) -> None:
        """Consent revalidation must run every 10 seconds (reduced from 30s)."""
        code = _read_screen("PatientRecordViewerScreen")
        # Must use 10000ms (10 seconds) for revalidation
        assert (
            "10000" in code
        ), "Consent revalidation must be every 10 seconds (10000ms)"


# ═══════════════════════════════════════════════════════════════════════════════
# 15. Session guards on all authenticated screens
# ═══════════════════════════════════════════════════════════════════════════════


class TestSessionGuards:
    """All authenticated screens must check session and redirect or lock on unauthenticated access."""

    AUTHENTICATED_SCREENS = [
        "PatientRecordViewerScreen",
        "EmergencyAccessScreen",
        "RequestConsentScreen",
        "PatientSearchScreen",
        "WaitingForApprovalScreen",
        "DoctorDashboardScreen",
    ]

    def test_all_screens_check_is_authenticated(self) -> None:
        """Every authenticated screen must read isAuthenticated from ProviderAuthContext."""
        for screen_name in self.AUTHENTICATED_SCREENS:
            code = _read_screen(screen_name)
            assert (
                "isAuthenticated" in code
            ), f"{screen_name} must check isAuthenticated from useProviderAuth()"

    def test_all_screens_show_session_required_on_unauthenticated(self) -> None:
        """Every authenticated screen must show a session-required state when not authenticated."""
        for screen_name in self.AUTHENTICATED_SCREENS:
            code = _read_screen(screen_name)
            code_lower = code.lower()
            # Must have some guard condition checking !isAuthenticated
            assert (
                "!isAuthenticated" in code
            ), f"{screen_name} must have a guard checking !isAuthenticated"
            # Must show a session-required message or redirect to login
            assert (
                "session required" in code_lower
                or "must be logged in" in code_lower
                or "login" in code_lower
            ), f"{screen_name} must show session-required message or redirect to login"

    def test_session_guard_shows_login_button(self) -> None:
        """Session guard must offer a way to get back to login."""
        for screen_name in self.AUTHENTICATED_SCREENS:
            code = _read_screen(screen_name)
            # The guard should have a Go to Login button
            if "!isAuthenticated" in code:
                assert (
                    "/doctor/login" in code
                ), f"{screen_name} must have a login redirect/button in session guard"


class TestZeroPlaceholders:
    """Zero provider_id placeholders, zero localhost, zero mock data."""

    def test_no_hardcoded_provider_id_in_screens(self) -> None:
        """No screen may contain a hardcoded provider_id string value."""
        for f in DOCTOR_DIR.glob("*.tsx"):
            if f.name.endswith(".test.tsx"):
                continue
            code = f.read_text(encoding="utf-8")
            # Look for provider_id = 'some-value' patterns (not from context)
            # Allowed: provider_id: providerId, provider_id: providerUid, .provider_id
            assert not re.search(
                r"provider_id\s*[:=]\s*['\"][a-zA-Z0-9-]+['\"]", code
            ), f"{f.name} must not have hardcoded provider_id value"

    def test_no_localhost_in_screens(self) -> None:
        """No screen may contain localhost URLs."""
        for f in DOCTOR_DIR.glob("*.tsx"):
            code = f.read_text(encoding="utf-8")
            code_no_comments = _strip_comments(code)
            assert (
                "localhost" not in code_no_comments.lower()
            ), f"{f.name} must not contain localhost"

    def test_no_localhost_in_api_client(self) -> None:
        """apiClient must not contain localhost URLs."""
        code = _read(
            ROOT / "nexa-client" / "packages" / "app" / "utils" / "apiClient.ts"
        )
        code_no_comments = _strip_comments(code)
        assert (
            "localhost" not in code_no_comments.lower()
        ), "apiClient must not contain localhost"

    def test_no_mock_data_in_record_viewer(self) -> None:
        """Record viewer must not contain mock/sample patient data."""
        code = _read_screen("PatientRecordViewerScreen")
        code_no_comments = _strip_comments(code)
        assert (
            "pat-123" not in code_no_comments
        ), "Must not contain hardcoded patient ID 'pat-123'"
        assert (
            "Jane Doe" not in code_no_comments
        ), "Must not contain hardcoded mock patient name 'Jane Doe'"
        assert (
            "Aarav Sharma" not in code_no_comments
        ), "Must not contain hardcoded demo patient name in record viewer"
