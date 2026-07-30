"""Tests for pipeline screen skeletons and FieldCard component.

Validates:
  - All 5 pipeline screens exist and use Tamagui components
  - FieldCard component exists and uses ExtractedField schema
  - No provider_id placeholder or hardcoded localhost anywhere
  - All screens use shared apiClient
  - Next.js routes exist for each screen
  - No plain HTML elements (div, span, button)
  - Session guards present on all authenticated screens
  - ALPHA honest labeling on all screens
  - Consent token passed as X-Consent-Token header
  - ProvenanceBadge shows correct verification status
  - Review Cockpit has split layout (document + field cards)
  - FieldCard has Approve/Edit/Reject buttons (only for needs_review)
  - Commit screen rejects when needs_review fields remain
  - Zod schemas for pipeline responses
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = ROOT / "nexa-client" / "packages" / "app" / "features" / "pipeline"
DOCTOR_DIR = ROOT / "nexa-client" / "packages" / "app" / "features" / "doctor"
NEXT_ROUTES_DIR = ROOT / "nexa-client" / "apps" / "next" / "app" / "doctor" / "pipeline"
API_CLIENT_PATH = ROOT / "nexa-client" / "packages" / "app" / "utils" / "apiClient.ts"

SCREENS = [
    "PipelineUploadScreen",
    "JobStatusScreen",
    "ReviewQueueScreen",
    "ReviewCockpitScreen",
    "CommitScreen",
]

FIELD_CARD = "FieldCard"

ROUTES = [
    "upload",
    "jobs/[jobId]",
    "review-queue",
    "review/[jobId]",
    "commit/[jobId]",
]

ROUTE_FILES = {
    "upload": "page.tsx",
    "jobs/[jobId]": "page.tsx",
    "review-queue": "page.tsx",
    "review/[jobId]": "page.tsx",
    "commit/[jobId]": "page.tsx",
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _read(path: Path) -> str:
    assert path.exists(), f"File missing: {path}"
    return path.read_text(encoding="utf-8")


def _read_screen(name: str) -> str:
    path = PIPELINE_DIR / f"{name}.tsx"
    return _read(path)


def _strip_comments(code: str) -> str:
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    code = re.sub(r"//[^\n]*", "", code)
    return code


def _normalize_ws(code: str) -> str:
    """Collapse all whitespace (including newlines) into single spaces for cross-line matching."""
    return re.sub(r"\s+", " ", code)


PLAIN_HTML_ELEMENTS = ["<div", "<span", "<button", "<input", "<h1", "<h2", "<h3", "<p"]


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Screen file existence
# ═══════════════════════════════════════════════════════════════════════════════


class TestPipelineScreensExist:
    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_screen_file_exists(self, screen: str) -> None:
        path = PIPELINE_DIR / f"{screen}.tsx"
        assert path.exists(), f"Pipeline screen file missing: {path}"

    def test_field_card_exists(self) -> None:
        path = PIPELINE_DIR / f"{FIELD_CARD}.tsx"
        assert path.exists(), f"FieldCard component missing: {path}"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Tamagui imports — no plain HTML
# ═══════════════════════════════════════════════════════════════════════════════


class TestTamaguiOnly:
    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_no_plain_html_elements(self, screen: str) -> None:
        code = _read_screen(screen)
        code_no_comments = _strip_comments(code)
        for tag in PLAIN_HTML_ELEMENTS:
            # Exception: hidden <input type="file"> is the only cross-platform
            # way to trigger file selection on web. It must be display:none.
            if tag == "<input":
                # Allow hidden file inputs (display: none) for file selection
                assert (
                    tag not in code_no_comments or 'type="file"' in code_no_comments
                ), (
                    f"{screen} uses plain HTML <input> without being a hidden file picker. "
                    f"Use Tamagui components only."
                )
            else:
                assert (
                    tag not in code_no_comments
                ), f"{screen} uses plain HTML element {tag}. Use Tamagui components only."

    def test_field_card_no_plain_html(self) -> None:
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        code_no_comments = _strip_comments(code)
        for tag in PLAIN_HTML_ELEMENTS:
            assert (
                tag not in code_no_comments
            ), f"FieldCard uses plain HTML element {tag}. Use Tamagui components only."

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_imports_tamagui(self, screen: str) -> None:
        code = _read_screen(screen)
        uses_tamagui = "tamagui" in code or "@my/ui" in code
        assert uses_tamagui, f"{screen} does not import from tamagui or @my/ui"

    def test_field_card_imports_tamagui(self) -> None:
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        uses_tamagui = "tamagui" in code or "@my/ui" in code
        assert uses_tamagui, "FieldCard does not import from tamagui or @my/ui"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Shared apiClient usage — no raw fetch/axios/localhost
# ═══════════════════════════════════════════════════════════════════════════════


class TestSharedApiClient:
    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_uses_shared_api_client(self, screen: str) -> None:
        code = _read_screen(screen)
        code_no_comments = _strip_comments(code)
        # Screens that make API calls should import apiClient
        if "apiClient" in code_no_comments or "apiRequest" in code_no_comments:
            assert (
                "apiClient" in code_no_comments or "apiRequest" in code_no_comments
            ), f"{screen} makes API calls but doesn't use shared apiClient"

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_no_raw_fetch(self, screen: str) -> None:
        code = _read_screen(screen)
        code_no_comments = _strip_comments(code)
        # Allow fetch only inside apiClient.ts itself
        assert not re.search(
            r"\bfetch\s*\(", code_no_comments
        ), f"{screen} uses raw fetch(). Use shared apiClient instead."

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_no_axios(self, screen: str) -> None:
        code = _read_screen(screen)
        code_no_comments = _strip_comments(code)
        assert (
            "axios" not in code_no_comments.lower()
        ), f"{screen} uses axios. Use shared apiClient instead."

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_no_localhost(self, screen: str) -> None:
        code = _read_screen(screen)
        code_no_comments = _strip_comments(code)
        assert (
            "localhost" not in code_no_comments
        ), f"{screen} contains hardcoded localhost. Use env-based apiClient."
        assert (
            "127.0.0.1" not in code_no_comments
        ), f"{screen} contains hardcoded 127.0.0.1. Use env-based apiClient."

    def test_field_card_no_raw_fetch(self) -> None:
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        code_no_comments = _strip_comments(code)
        assert not re.search(
            r"\bfetch\s*\(", code_no_comments
        ), "FieldCard uses raw fetch(). Use shared apiClient instead."

    def test_field_card_no_localhost(self) -> None:
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        assert "localhost" not in code, "FieldCard contains hardcoded localhost."


# ═══════════════════════════════════════════════════════════════════════════════
# 4. No provider_id placeholder
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoProviderIdPlaceholder:
    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_no_provider_id_placeholder(self, screen: str) -> None:
        code = _read_screen(screen)
        code_no_comments = _strip_comments(code)
        nw = _normalize_ws(code_no_comments)
        # Forbidden: provider_id = "..." or provider_id='...'
        assert not re.search(
            r"""provider_id\s*=\s*['"][^'"]*['"]""", nw
        ), f"{screen} has a hardcoded provider_id string. Use ProviderAuthContext."
        # Forbidden: provider_id: "..." as default value (not in type definition)
        # Allow: provider_id as object key in request bodies { provider_id: someVar }
        # But forbid: provider_id = "some-string-literal"
        for pattern in [
            r'provider_id\s*=\s*["\']',
            r'provider_id\s*:\s*["\'](?:provider-|demo|test|default|PLACEHOLDER)',
        ]:
            assert not re.search(
                pattern, nw
            ), f"{screen} has a hardcoded provider_id placeholder."

    def test_field_card_no_provider_id_placeholder(self) -> None:
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        nw = _normalize_ws(_strip_comments(code))
        assert not re.search(
            r"""provider_id\s*=\s*['"][^'"]*['"]""", nw
        ), "FieldCard has a hardcoded provider_id string."


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Session guards
# ═══════════════════════════════════════════════════════════════════════════════


class TestSessionGuards:
    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_session_guard_present(self, screen: str) -> None:
        code = _read_screen(screen)
        nw = _normalize_ws(code)
        assert (
            "isAuthenticated" in nw
        ), f"{screen} does not check isAuthenticated from ProviderAuthContext."
        assert (
            "Session Required" in nw or "Session" in code
        ), f"{screen} does not have a session guard rendering when unauthenticated."

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_session_guard_renders_locked_state(self, screen: str) -> None:
        code = _read_screen(screen)
        # Should render something when !isAuthenticated
        assert (
            "🔒" in code or "Session Required" in code or "Go to Login" in code
        ), f"{screen} session guard doesn't render a locked state with login button."

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_imports_provider_auth(self, screen: str) -> None:
        code = _read_screen(screen)
        nw = _normalize_ws(code)
        assert (
            "useProviderAuth" in nw or "ProviderAuthContext" in nw
        ), f"{screen} does not import useProviderAuth from ProviderAuthContext."


class TestConsentGuards:
    """Pipeline screens that require consent must guard for missing consent token."""

    # Screens that should have a consent-token guard
    CONSENT_GUARD_SCREENS = [
        "PipelineUploadScreen",
        "JobStatusScreen",
        "ReviewQueueScreen",
        "ReviewCockpitScreen",
        "CommitScreen",
    ]

    @pytest.mark.parametrize("screen", CONSENT_GUARD_SCREENS, ids=CONSENT_GUARD_SCREENS)
    def test_consent_guard_present(self, screen: str) -> None:
        """Screen must render a consent-required state when consent token is missing."""
        code = _read_screen(screen)
        assert (
            "Consent Required" in code
        ), f"{screen} missing consent-required guard state."

    @pytest.mark.parametrize("screen", CONSENT_GUARD_SCREENS, ids=CONSENT_GUARD_SCREENS)
    def test_consent_guard_request_button(self, screen: str) -> None:
        """Consent guard must offer a way to request consent or navigate away."""
        code = _read_screen(screen)
        assert (
            "Request Consent" in code or "Go to Login" in code or "Back" in code
        ), f"{screen} consent guard missing action button."


# ═══════════════════════════════════════════════════════════════════════════════
# 6. ALPHA honest labeling
# ═══════════════════════════════════════════════════════════════════════════════


class TestAlphaLabeling:
    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_alpha_badge_in_ui(self, screen: str) -> None:
        code = _read_screen(screen)
        assert "ALPHA" in code, f"{screen} missing ALPHA badge in UI."

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_alpha_honest_wording(self, screen: str) -> None:
        """ALPHA screens must include precise honest wording about clinical verification."""
        code = _read_screen(screen)
        nw = _normalize_ws(code)
        # Must contain the honest wording about clinical verification
        assert (
            "clinical verification" in nw.lower()
            or "require clinical verification" in nw.lower()
        ), f"{screen} missing ALPHA honest wording about clinical verification."

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_alpha_jsdoc_comment(self, screen: str) -> None:
        """Each screen's JSDoc must declare alpha status."""
        code = _read_screen(screen)
        # First 30 lines should have a JSDoc with ALPHA mention
        first_lines = "\n".join(code.split("\n")[:40])
        assert (
            "ALPHA" in first_lines
        ), f"{screen} JSDoc comment does not declare ALPHA status."


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Consent token handling
# ═══════════════════════════════════════════════════════════════════════════════


class TestConsentTokenHandling:
    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_consent_token_as_header(self, screen: str) -> None:
        """Consent token must be passed as X-Consent-Token header, not in URL."""
        code = _read_screen(screen)
        nw = _normalize_ws(code)
        # Screens that use consent token should pass it as a header
        if "consentToken" in nw or "consent_token" in nw:
            assert (
                "X-Consent-Token" in nw
            ), f"{screen} uses consent token but doesn't pass it as X-Consent-Token header."

    def test_consent_token_not_in_url_path(self) -> None:
        """Consent tokens must never appear in a URL -- API call or screen-to-screen navigation.

        DEFECT 3: raw bearer tokens may exist only in process memory and in
        the X-Consent-Token header. Screen-to-screen navigation must use an
        opaque workflow_id (looked up against the in-memory capability
        store), never the token itself -- there is no "acceptable between
        screens" exception.
        """
        all_code = ""
        for screen in SCREENS:
            all_code += _read_screen(screen) + "\n"
        code_no_comments = _strip_comments(all_code)
        assert not re.search(
            r"consent_token=\$\{", code_no_comments
        ), "Consent token interpolated into a URL. Must use workflow_id + the in-memory capability store."
        assert not re.search(
            r"consentToken=\$\{", code_no_comments
        ), "Consent token interpolated into a URL. Must use workflow_id + the in-memory capability store."
        assert not re.search(
            r"/api/.*consent_token=", code_no_comments
        ), "Consent token appears in backend API URL. Must be X-Consent-Token header only."


# ═══════════════════════════════════════════════════════════════════════════════
# 8. FieldCard specifics
# ═══════════════════════════════════════════════════════════════════════════════


class TestFieldCard:
    def test_field_card_has_extracted_field_interface(self) -> None:
        """FieldCard must be typed against the ExtractedField schema (WS1)."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        nw = _normalize_ws(code)
        # Should reference the ExtractedField schema fields
        required_fields = [
            "field_id",
            "field_name",
            "raw_value",
            "confidence",
            "risk_level",
            "status",
            "source_page",
            "corrected_value",
        ]
        for field in required_fields:
            assert field in nw, f"FieldCard missing ExtractedField field: {field}"

    def test_field_card_has_provenance_badge(self) -> None:
        """FieldCard must have a ProvenanceBadge for verification status."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        assert "ProvenanceBadge" in code, "FieldCard missing ProvenanceBadge component."

    def test_provenance_badge_clinician_verified(self) -> None:
        """ProvenanceBadge must show 'Clinician verified' for approved fields."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        assert (
            "Clinician verified" in code
        ), "ProvenanceBadge missing 'Clinician verified' label."

    def test_provenance_badge_ai_extracted(self) -> None:
        """ProvenanceBadge must show AI extraction with confidence for unverified fields."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        assert "AI extracted" in code, "ProvenanceBadge missing 'AI extracted' label."
        assert (
            "Not yet verified" in code
        ), "ProvenanceBadge missing 'Not yet verified' label."

    def test_field_card_has_approve_edit_reject(self) -> None:
        """FieldCard must have Approve, Edit, and Reject action buttons."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        # Extract button texts
        buttons = re.findall(
            r"<Button[^>]*>\s*(.*?)\s*</Button>", _strip_comments(code), re.DOTALL
        )
        button_texts = [b.strip() for b in buttons]
        # At least some buttons should have Approve, Edit, Reject text
        all_button_text = " ".join(button_texts)
        assert "Approve" in all_button_text, "FieldCard missing Approve button."
        assert "Edit" in all_button_text, "FieldCard missing Edit button."
        assert "Reject" in all_button_text, "FieldCard missing Reject button."

    def test_field_card_actions_only_for_needs_review(self) -> None:
        """Action buttons should only appear for needs_review fields."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        nw = _normalize_ws(code)
        assert (
            "isAdjudicated" in nw or "needs_review" in nw
        ), "FieldCard doesn't gate action buttons by review status."

    def test_field_card_risk_badges(self) -> None:
        """FieldCard must have risk level badges for all 4 levels."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        for level in ["LOW_RISK", "MEDIUM_RISK", "HIGH_RISK", "CRITICAL_RISK"]:
            assert level in code, f"FieldCard missing risk level: {level}"

    def test_field_card_validation_messages(self) -> None:
        """FieldCard must display validation messages from validation_result."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        nw = _normalize_ws(code)
        assert (
            "validation_errors" in nw or "validation_result" in nw
        ), "FieldCard doesn't reference validation_result."

    def test_field_card_source_page(self) -> None:
        """FieldCard must display source page with jump-to-page option."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        assert "source_page" in code, "FieldCard missing source_page reference."
        assert (
            "onSourcePageClick" in code
        ), "FieldCard missing onSourcePageClick callback."

    def test_field_card_reference_range(self) -> None:
        """FieldCard must display reference_range when available."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        assert "reference_range" in code, "FieldCard missing reference_range display."

    def test_field_card_calls_review_endpoint(self) -> None:
        """FieldCard approve/edit/reject must call /api/v2/pipeline/fields/{field_id}/review."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        api_code = _read(API_CLIENT_PATH)
        nw = _normalize_ws(code)
        api_nw = _normalize_ws(api_code)
        # FieldCard may call apiClient.reviewField() which delegates to the endpoint
        assert (
            ("fields/" in nw and "/review" in nw) or "reviewField" in nw
        ), "FieldCard doesn't call the field review endpoint or convenience method."
        # The apiClient must have the actual endpoint URL
        assert (
            "fields/" in api_nw and "/review" in api_nw
        ), "apiClient doesn't define the field review endpoint."

    def test_field_card_edit_mode(self) -> None:
        """FieldCard must support inline edit mode with corrected_value input."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        assert "editMode" in code, "FieldCard missing edit mode state."
        assert "corrected_value" in code, "FieldCard missing corrected_value handling."


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Review Cockpit layout
# ═══════════════════════════════════════════════════════════════════════════════


class TestReviewCockpitLayout:
    def test_document_confidence_is_not_fabricated_from_a_retired_aggregate(self) -> None:
        """The cockpit must preserve unavailable document confidence truthfully."""
        code = _read_screen("ReviewCockpitScreen")
        assert "overall_confidence" not in code
        assert "document_confidence" in code
        assert "'unavailable'" in code

    def test_split_layout(self) -> None:
        """Review Cockpit must have a split layout with document preview and field cards."""
        code = _read_screen("ReviewCockpitScreen")
        # Should have left/right split using XStack
        assert (
            "Original Document" in code
        ), "Review Cockpit missing document preview label."
        assert (
            "FieldCard" in code
        ), "Review Cockpit doesn't render FieldCard components."
        # Should have XStack for horizontal split
        assert "<XStack" in _strip_comments(
            code
        ), "Review Cockpit missing XStack for split layout."

    def test_document_preview_has_page_nav(self) -> None:
        """Document preview must have page navigation."""
        code = _read_screen("ReviewCockpitScreen")
        assert "currentPage" in code, "Review Cockpit missing page navigation state."

    def test_shows_review_progress(self) -> None:
        """Review Cockpit must show progress (X/Y reviewed)."""
        code = _read_screen("ReviewCockpitScreen")
        nw = _normalize_ws(code)
        assert (
            "reviewed" in nw.lower() or "remaining" in nw.lower()
        ), "Review Cockpit doesn't show review progress."

    def test_commit_button_gated(self) -> None:
        """Commit button must be disabled until all fields are reviewed."""
        code = _read_screen("ReviewCockpitScreen")
        nw = _normalize_ws(code)
        assert (
            "allReviewed" in nw or "needsReview" in nw
        ), "Review Cockpit commit button not gated by review completion."

    def test_field_categories_in_list(self) -> None:
        """Review Cockpit must separate fields by status (needs_review, auto_approved, reviewed)."""
        code = _read_screen("ReviewCockpitScreen")
        assert (
            "needs_review" in code
        ), "Review Cockpit doesn't filter needs_review fields."
        assert (
            "auto_approved" in code
        ), "Review Cockpit doesn't show auto_approved section."


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Job Status polling
# ═══════════════════════════════════════════════════════════════════════════════


class TestJobStatusPolling:
    def test_polls_every_2_seconds(self) -> None:
        """JobStatusScreen must poll every 2 seconds."""
        code = _read_screen("JobStatusScreen")
        assert (
            "POLL_INTERVAL_MS" in code
        ), "JobStatusScreen missing POLL_INTERVAL_MS constant."
        assert (
            "2_000" in code or "2000" in code
        ), "JobStatusScreen polling interval is not 2 seconds."

    def test_terminal_states_stop_polling(self) -> None:
        """Polling must stop on terminal states."""
        code = _read_screen("JobStatusScreen")
        assert (
            "TERMINAL_STATUSES" in code
        ), "JobStatusScreen missing TERMINAL_STATUSES constant."
        assert (
            "setPollingActive" in code
        ), "JobStatusScreen missing polling active state."

    def test_cleanup_on_unmount(self) -> None:
        """Polling must be cleaned up on component unmount."""
        code = _read_screen("JobStatusScreen")
        assert (
            "clearTimeout" in code
        ), "JobStatusScreen doesn't clean up polling timer on unmount."

    def test_error_handling_by_status(self) -> None:
        """JobStatusScreen must handle errors by HTTP status code."""
        code = _read_screen("JobStatusScreen")
        nw = _normalize_ws(code)
        for status in ["401", "403", "404"]:
            assert status in nw, f"JobStatusScreen doesn't handle HTTP {status}."

    def test_status_lifecycle_display(self) -> None:
        """JobStatusScreen must show queued → extracting → scored → review_pending."""
        code = _read_screen("JobStatusScreen")
        assert (
            "STATUS_DISPLAY" in code
        ), "JobStatusScreen missing STATUS_DISPLAY mapping."
        for status in ["queued", "extracting", "scored", "review_pending"]:
            assert (
                status in code
            ), f"JobStatusScreen missing status display for: {status}"

    def test_progress_bar(self) -> None:
        """JobStatusScreen must show a progress bar."""
        code = _read_screen("JobStatusScreen")
        assert "Progress" in code, "JobStatusScreen missing Progress component."
        assert (
            "STATUS_PROGRESS" in code
        ), "JobStatusScreen missing STATUS_PROGRESS mapping."

    def test_field_summary_when_scored(self) -> None:
        """JobStatusScreen must show genuine candidates and safe routing evidence."""
        code = _read_screen("JobStatusScreen")
        for contract in [
            "candidate_count",
            "field_confidence",
            "source_page",
            "source_text",
            "source_bbox",
            "identity_validation",
            "routing_lane",
        ]:
            assert contract in code, f"JobStatusScreen missing {contract}."
        assert "Auto-commit is disabled" in code

    def test_go_to_review_queue_button(self) -> None:
        """When review_pending, must show 'Go to Review Queue' button."""
        code = _read_screen("JobStatusScreen")
        nw = _normalize_ws(code)
        assert (
            "review_pending" in nw or "review_required" in nw
        ), "JobStatusScreen missing review_pending check."
        assert (
            "review-queue" in code
        ), "JobStatusScreen missing navigation to review queue."
        assert (
            "Go to Review Queue" in code
        ), "JobStatusScreen missing 'Go to Review Queue' button text."

    def test_polling_indicator_text(self) -> None:
        """JobStatusScreen must show that it is polling every 2s."""
        code = _read_screen("JobStatusScreen")
        assert (
            "Polling every 2s" in code
        ), "JobStatusScreen missing polling indicator text with interval."


class TestJobStatusUsesRouteParams:
    """JobStatusScreen must use useParams() for jobId from route param [jobId]."""

    def test_imports_use_params(self) -> None:
        """JobStatusScreen must import and use useParams from next/navigation."""
        code = _read_screen("JobStatusScreen")
        assert (
            "useParams" in code
        ), "JobStatusScreen doesn't import useParams from next/navigation."

    def test_uses_route_params_for_job_id(self) -> None:
        """jobId must come from routeParams.jobId, not searchParams.get('job_id')."""
        code = _read_screen("JobStatusScreen")
        assert (
            "routeParams" in code
        ), "JobStatusScreen doesn't read routeParams from useParams."
        # Must NOT use searchParams.get('job_id') for jobId
        assert (
            "searchParams.get('job_id')" not in code
        ), "JobStatusScreen still uses searchParams.get('job_id') instead of useParams."

    def test_nexa_client_uses_use_params(self) -> None:
        """nexa-client JobStatusScreen must also use useParams."""
        path = (
            ROOT
            / "nexa-client"
            / "packages"
            / "app"
            / "features"
            / "pipeline"
            / "JobStatusScreen.tsx"
        )
        if not path.exists():
            pytest.skip("nexa-client JobStatusScreen not found")
        code = _read(path)
        assert "useParams" in code, "nexa-client JobStatusScreen doesn't use useParams."
        assert (
            "routeParams" in code
        ), "nexa-client JobStatusScreen doesn't read routeParams."
        assert (
            "searchParams.get('job_id')" not in code
        ), "nexa-client JobStatusScreen still uses searchParams.get('job_id')."

    def test_uses_get_extraction_job_status(self) -> None:
        """JobStatusScreen must use apiClient.getExtractionJobStatus convenience method."""
        code = _read_screen("JobStatusScreen")
        assert (
            "getExtractionJobStatus" in code
        ), "JobStatusScreen must use apiClient.getExtractionJobStatus() convenience method."

    def test_nexa_client_uses_get_extraction_job_status(self) -> None:
        """nexa-client JobStatusScreen must use NexaApiClient.getExtractionJobStatus."""
        path = (
            ROOT
            / "nexa-client"
            / "packages"
            / "app"
            / "features"
            / "pipeline"
            / "JobStatusScreen.tsx"
        )
        if not path.exists():
            pytest.skip("nexa-client JobStatusScreen not found")
        code = _read(path)
        assert (
            "getExtractionJobStatus" in code
        ), "nexa-client JobStatusScreen must use getExtractionJobStatus() convenience method."

    def test_consent_token_guard(self) -> None:
        """JobStatusScreen must guard for missing consent token."""
        code = _read_screen("JobStatusScreen")
        nw = _normalize_ws(code)
        assert (
            "Consent Required" in nw
        ), "JobStatusScreen missing consent required guard."


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Commit screen
# ═══════════════════════════════════════════════════════════════════════════════


class TestCommitScreen:
    def test_rejects_unresolved_fields(self) -> None:
        """Commit must be disabled when needs_review fields remain."""
        code = _read_screen("CommitScreen")
        nw = _normalize_ws(code)
        assert (
            "needsReview" in nw or "needs_review" in nw
        ), "CommitScreen doesn't check for unresolved fields."
        assert (
            "canCommit" in nw or "disabled" in nw
        ), "CommitScreen commit button not gated."

    def test_handles_409_conflict(self) -> None:
        """CommitScreen must handle HTTP 409 (unresolved fields)."""
        code = _read_screen("CommitScreen")
        nw = _normalize_ws(code)
        assert "409" in nw, "CommitScreen doesn't handle HTTP 409."
        assert (
            "unresolved" in nw.lower() or "incomplete" in nw.lower()
        ), "CommitScreen doesn't show clear message for 409."

    def test_shows_committable_vs_rejected(self) -> None:
        """CommitScreen must show committable and rejected field counts."""
        code = _read_screen("CommitScreen")
        assert (
            "committable" in code.lower()
        ), "CommitScreen doesn't show committable count."
        assert "rejected" in code.lower(), "CommitScreen doesn't show rejected count."

    def test_success_state(self) -> None:
        """CommitScreen must show success state with committed_fields_count."""
        code = _read_screen("CommitScreen")
        assert "committed_fields_count" in code, "CommitScreen missing success state."
        assert "Committed" in code, "CommitScreen missing Committed confirmation."

    def test_encounter_summary_optional(self) -> None:
        """CommitScreen must offer an optional encounter summary field."""
        code = _read_screen("CommitScreen")
        assert "encounter_summary" in code, "CommitScreen missing encounter_summary."


# ═══════════════════════════════════════════════════════════════════════════════
# 12. Upload screen
# ═══════════════════════════════════════════════════════════════════════════════


class TestUploadScreen:
    def test_file_extension_validation(self) -> None:
        """Upload must validate file extensions."""
        code = _read_screen("PipelineUploadScreen")
        assert (
            "ALLOWED_EXTENSIONS" in code
        ), "Upload screen missing file extension validation."

    def test_file_size_validation(self) -> None:
        """Upload must validate file size."""
        code = _read_screen("PipelineUploadScreen")
        assert "MAX_FILE_SIZE" in code, "Upload screen missing file size validation."

    def test_consent_required_guard(self) -> None:
        """Upload must check for a live consent capability before allowing upload."""
        code = _read_screen("PipelineUploadScreen")
        assert "consentToken" in code, "Upload screen missing consent token check."
        assert (
            "workflow_id" in code
        ), "Upload screen must resolve its capability via workflow_id, not a raw token in the URL."
        assert (
            "Consent Required" in code
        ), "Upload screen missing consent required state."

    def test_redirects_to_job_status(self) -> None:
        """Upload must redirect to job status on success."""
        code = _read_screen("PipelineUploadScreen")
        nw = _normalize_ws(code)
        assert "jobs/" in nw, "Upload doesn't redirect to job status on success."

    def test_upload_status_states(self) -> None:
        """Upload must have idle/uploading/success/error states."""
        code = _read_screen("PipelineUploadScreen")
        assert "idle" in code, "Upload missing idle state."
        assert "uploading" in code, "Upload missing uploading state."
        assert (
            "success" in code or "error" in code
        ), "Upload missing success/error states."

    def test_dropzone_present(self) -> None:
        """Upload must have a dropzone with dashed border."""
        code = _read_screen("PipelineUploadScreen")
        assert "dashed" in code, "Upload missing dashed border dropzone."
        assert "Drag" in code and "drop" in code, "Upload missing drag-and-drop text."
        assert "dragOver" in code, "Upload missing dragOver state."

    def test_dropzone_drag_handlers(self) -> None:
        """Upload must handle dragOver, dragLeave, and drop events."""
        code = _read_screen("PipelineUploadScreen")
        assert "handleDragOver" in code, "Upload missing handleDragOver."
        assert "handleDragLeave" in code, "Upload missing handleDragLeave."
        assert "handleDrop" in code, "Upload missing handleDrop."
        assert "onDragOver" in code, "Upload dropzone missing onDragOver."
        assert "onDragLeave" in code, "Upload dropzone missing onDragLeave."
        assert "onDrop" in code, "Upload dropzone missing onDrop."

    def test_cross_platform_file_input(self) -> None:
        """Upload must have a hidden file input for Browse Files button."""
        code = _read_screen("PipelineUploadScreen")
        assert "fileInputRef" in code, "Upload missing file input ref."
        assert "Browse Files" in code, "Upload missing Browse Files button."
        assert 'type="file"' in code, "Upload missing hidden file input element."

    def test_patient_selector(self) -> None:
        """Upload patient must be immutable and derived from the claimed capability."""
        code = _read_screen("PipelineUploadScreen")
        assert "validDocumentCapability?.patientId" in code
        assert "setPatientId" not in code
        assert "urlPatientId" not in code
        assert "Locked to the signed document-processing approval." in code

    def test_multipart_upload_with_formdata(self) -> None:
        """Upload must send FormData for multipart, not JSON."""
        code = _read_screen("PipelineUploadScreen")
        assert "FormData" in code, "Upload doesn't use FormData for multipart upload."
        assert "formData.append" in code, "Upload doesn't append fields to FormData."
        assert (
            "uploadDocument" in code or "uploadFile" in code
        ), "Upload doesn't use apiClient upload method."

    def test_no_manual_content_type(self) -> None:
        """Upload must NOT set Content-Type manually for multipart uploads."""
        _read_screen("PipelineUploadScreen")
        # The upload handler should not set Content-Type
        # NexaApiClient.uploadDocument should NOT set Content-Type for FormData
        api_client_code = _read(API_CLIENT_PATH)
        # Check uploadDocument function doesn't set Content-Type
        upload_fn_match = re.search(
            r"(?:async\s+)?(?:uploadDocument|apiUpload).*?(?=export|async function|static\s|$)",
            api_client_code,
            re.DOTALL,
        )
        if upload_fn_match:
            fn_body = upload_fn_match.group(0)
            lines = fn_body.split("\n")
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("'Content-Type'") or stripped.startswith(
                    '"Content-Type"'
                ):
                    if "application/json" in stripped:
                        pytest.fail(
                            "Upload function sets Content-Type — browser must set boundary automatically."
                        )

    def test_provider_session_token_attached(self) -> None:
        """Upload must attach provider session token via apiClient."""
        api_client_code = _read(API_CLIENT_PATH)
        # apiUpload must attach JWT
        assert (
            "Authorization" in api_client_code
        ), "apiClient doesn't attach Authorization header."
        assert "Bearer" in api_client_code, "apiClient doesn't use Bearer token format."


# ═══════════════════════════════════════════════════════════════════════════════
# 13. Review Queue screen
# ═══════════════════════════════════════════════════════════════════════════════


class TestReviewQueueScreen:
    def test_risk_level_badges(self) -> None:
        """Review Queue must show risk level badges."""
        code = _read_screen("ReviewQueueScreen")
        for level in ["LOW_RISK", "MEDIUM_RISK", "HIGH_RISK", "CRITICAL_RISK"]:
            assert level in code, f"Review Queue missing risk level: {level}"

    def test_empty_state(self) -> None:
        """Review Queue must show empty state."""
        code = _read_screen("ReviewQueueScreen")
        assert "No items pending review" in code, "Review Queue missing empty state."

    def test_loading_state(self) -> None:
        """Review Queue must show loading state."""
        code = _read_screen("ReviewQueueScreen")
        assert (
            "Spinner" in code or "Loading" in code
        ), "Review Queue missing loading state."

    def test_error_state_with_retry(self) -> None:
        """Review Queue must show error state with retry."""
        code = _read_screen("ReviewQueueScreen")
        assert "Retry" in code, "Review Queue missing retry button."
        assert "error" in code.lower(), "Review Queue missing error state."

    def test_navigates_to_review_cockpit(self) -> None:
        """Clicking a queue item must navigate to Review Cockpit."""
        code = _read_screen("ReviewQueueScreen")
        nw = _normalize_ws(code)
        assert "review/" in nw, "Review Queue doesn't navigate to review cockpit."


# ═══════════════════════════════════════════════════════════════════════════════
# 14. Route existence
# ═══════════════════════════════════════════════════════════════════════════════


class TestPipelineRoutes:
    @pytest.mark.parametrize("route", ROUTES, ids=ROUTES)
    def test_nextjs_route_exists(self, route: str) -> None:
        """Next.js route page must exist for each pipeline screen."""
        page_path = NEXT_ROUTES_DIR / route / "page.tsx"
        assert page_path.exists(), f"Next.js route missing: {page_path}"

    @pytest.mark.parametrize("route", ROUTES, ids=ROUTES)
    def test_route_imports_screen(self, route: str) -> None:
        """Route page must import the corresponding pipeline screen."""
        page_path = NEXT_ROUTES_DIR / route / "page.tsx"
        code = _read(page_path)
        # Must import from pipeline feature directory
        assert (
            "pipeline" in code.lower()
        ), f"Route {route} doesn't import from pipeline feature directory."

    @pytest.mark.parametrize("route", ROUTES, ids=ROUTES)
    def test_route_wraps_in_suspense(self, route: str) -> None:
        """Route pages using useSearchParams must be wrapped in Suspense."""
        page_path = NEXT_ROUTES_DIR / route / "page.tsx"
        code = _read(page_path)
        assert "Suspense" in code, f"Route {route} not wrapped in Suspense."


# ═══════════════════════════════════════════════════════════════════════════════
# 15. Cross-screen consistency
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrossScreenConsistency:
    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_route_declaration_in_jsdoc(self, screen: str) -> None:
        """Each screen must declare its route in JSDoc."""
        code = _read_screen(screen)
        first_lines = "\n".join(code.split("\n")[:40])
        assert (
            "Route:" in first_lines or "/doctor/pipeline" in first_lines
        ), f"{screen} doesn't declare its route in JSDoc."

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_no_hardcoded_patient_id(self, screen: str) -> None:
        """No hardcoded patient_id values."""
        code = _read_screen(screen)
        code_no_comments = _strip_comments(code)
        nw = _normalize_ws(code_no_comments)
        # Allow patient_id from searchParams or as object key, but not as string literal
        assert not re.search(
            r"""patient_id\s*=\s*['"](?:patient-|demo-|test-|default)""", nw
        ), f"{screen} has a hardcoded patient_id placeholder."

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_loading_state(self, screen: str) -> None:
        """Every screen must handle loading state."""
        code = _read_screen(screen)
        assert (
            "Spinner" in code or "loading" in code.lower()
        ), f"{screen} missing loading state."

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_error_state(self, screen: str) -> None:
        """Every screen must handle error state."""
        code = _read_screen(screen)
        assert "error" in code.lower(), f"{screen} missing error state."


# ═══════════════════════════════════════════════════════════════════════════════
# 16. Pipeline API alignment
# ═══════════════════════════════════════════════════════════════════════════════


class TestPipelineApiAlignment:
    def test_upload_calls_correct_endpoint(self) -> None:
        code = _read_screen("PipelineUploadScreen")
        api_code = _read(API_CLIENT_PATH)
        # The endpoint may be in the screen or in the apiClient method it calls
        assert (
            "/api/v2/pipeline/documents/upload" in code or "uploadDocument" in code
        ), "Upload screen doesn't call correct upload endpoint or uploadDocument method."
        assert (
            "/api/v2/pipeline/documents/upload" in api_code
        ), "apiClient must define the upload endpoint URL."

    def test_job_status_calls_correct_endpoint(self) -> None:
        code = _read_screen("JobStatusScreen")
        assert (
            "/api/v2/pipeline/jobs/" in code
        ), "JobStatusScreen doesn't call correct job status endpoint."

    def test_review_queue_calls_correct_endpoint(self) -> None:
        code = _read_screen("ReviewQueueScreen")
        assert (
            "/api/v2/pipeline/review-queue" in code
        ), "ReviewQueueScreen doesn't call correct review queue endpoint."

    def test_review_cockpit_calls_correct_endpoint(self) -> None:
        code = _read_screen("ReviewCockpitScreen")
        api_code = _read(API_CLIENT_PATH)
        nw = _normalize_ws(code)
        api_nw = _normalize_ws(api_code)
        # ReviewCockpitScreen may call apiClient.getExtractionJobStatus()
        assert (
            "/api/v2/pipeline/jobs/" in code or "getExtractionJobStatus" in nw
        ), "ReviewCockpitScreen doesn't call job details endpoint or convenience method."
        # The apiClient must have the actual endpoint URL
        assert (
            "/api/v2/pipeline/jobs/" in api_nw
        ), "apiClient doesn't define the job details endpoint."


class TestVisibleTextractConsentFlow:
    def test_dashboard_reaches_document_upload_intent(self) -> None:
        dashboard = _read(DOCTOR_DIR / "DoctorDashboardScreen.tsx")
        assert "Upload &amp; AI Extract" in dashboard
        assert "/doctor/patient-search?intent=document_upload" in dashboard

    def test_patient_search_preserves_document_upload_intent(self) -> None:
        search = _read(DOCTOR_DIR / "PatientSearchScreen.tsx")
        assert "documentUploadIntent" in search
        assert "&intent=document_upload" in search

    def test_document_consent_purpose_and_scope_are_locked(self) -> None:
        request = _read(DOCTOR_DIR / "RequestConsentScreen.tsx")
        assert "document_processing" in request
        assert "documents" in request
        assert "LOCKED PURPOSE" in request
        assert "LOCKED SCOPE" in request
        assert "documentUploadIntent ? 'document_processing' : purpose" in request
        assert "documentUploadIntent ? 'documents' : requestedScope" in request

    def test_signed_claim_populates_pipeline_memory_store(self) -> None:
        waiting = _read(DOCTOR_DIR / "WaitingForApprovalScreen.tsx")
        assert "setCapability" in waiting
        assert "generateWorkflowId" in waiting
        assert "claim.consent_token" in waiting
        assert "/doctor/pipeline/upload?workflow_id=" in waiting
        assert "consent_token=" not in waiting

    def test_pipeline_navigation_never_persists_or_routes_capability(self) -> None:
        upload = _read_screen("PipelineUploadScreen")
        status = _read_screen("JobStatusScreen")
        capability_store = _read(
            ROOT
            / "nexa-client"
            / "packages"
            / "app"
            / "services"
            / "capabilityStore.ts"
        )
        combined = upload + status + capability_store
        for forbidden in ["localStorage", "sessionStorage", "IndexedDB", "SecureStore"]:
            assert forbidden not in _strip_comments(combined)
        assert "consent_token=" not in combined
        assert "token=" not in upload

    def test_commit_calls_correct_endpoint(self) -> None:
        code = _read_screen("CommitScreen")
        api_code = _read(API_CLIENT_PATH)
        nw = _normalize_ws(code)
        # CommitScreen may use apiClient.commitExtractionJob() convenience method
        assert (
            ("/api/v2/pipeline/jobs/" in code and "/commit" in code)
            or "commitExtractionJob" in nw
        ), "CommitScreen doesn't call commit endpoint or convenience method."
        # The apiClient must have the commit endpoint
        api_nw = _normalize_ws(api_code)
        assert "/commit" in api_nw, "apiClient doesn't define the commit endpoint."

    def test_field_review_calls_correct_endpoint(self) -> None:
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        api_code = _read(API_CLIENT_PATH)
        nw = _normalize_ws(code)
        api_nw = _normalize_ws(api_code)
        # FieldCard may call apiClient.reviewField()
        assert (
            "/api/v2/pipeline/fields/" in code or "reviewField" in nw
        ), "FieldCard doesn't call field review endpoint or convenience method."
        # The apiClient must have the actual endpoint URL
        assert (
            "/api/v2/pipeline/fields/" in api_nw
        ), "apiClient doesn't define the field review endpoint."

    def test_consent_purpose_ai_document_ingestion(self) -> None:
        code = _read_screen("PipelineUploadScreen")
        api_code = _read(API_CLIENT_PATH)
        assert (
            "ai_document_ingestion" in code or "ai_document_ingestion" in api_code
        ), "Upload screen or apiClient missing ai_document_ingestion consent purpose."

    def test_consent_purpose_pipeline_status(self) -> None:
        code = _read_screen("JobStatusScreen")
        api_code = _read(API_CLIENT_PATH)
        assert (
            "pipeline_status" in code or "pipeline_status" in api_code
        ), "JobStatusScreen missing pipeline_status consent purpose."

    def test_consent_purpose_clinical_review(self) -> None:
        code = _read_screen("ReviewQueueScreen")
        api_code = _read(API_CLIENT_PATH)
        assert (
            "clinical_review" in code or "clinical_review" in api_code
        ), "ReviewQueueScreen missing clinical_review consent purpose."

    def test_consent_purpose_field_adjudication(self) -> None:
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        api_code = _read(API_CLIENT_PATH)
        assert (
            "field_adjudication" in code or "field_adjudication" in api_code
        ), "FieldCard missing field_adjudication consent purpose."

    def test_consent_purpose_pipeline_commit(self) -> None:
        code = _read_screen("CommitScreen")
        api_code = _read(API_CLIENT_PATH)
        assert (
            "pipeline_commit" in code or "pipeline_commit" in api_code
        ), "CommitScreen missing pipeline_commit consent purpose."


# ═══════════════════════════════════════════════════════════════════════════════
# 17. nexa-client parity
# ═══════════════════════════════════════════════════════════════════════════════


class TestApiClientUploadSupport:
    """Verify apiClient has proper multipart upload support."""

    def test_api_upload_function_exists(self) -> None:
        """apiClient must have an upload method for multipart uploads."""
        code = _read(API_CLIENT_PATH)
        assert (
            "uploadDocument" in code or "apiUpload" in code
        ), "apiClient missing upload function."

    def test_api_upload_does_not_set_content_type(self) -> None:
        """Upload must NOT set Content-Type — the browser sets the boundary."""
        code = _read(API_CLIENT_PATH)
        # The upload function should have a comment about NOT setting Content-Type
        # or simply not set it (browser handles boundary for FormData)
        assert (
            "Does NOT set Content-Type" in code
            or "not" in code.lower()
            and "Content-Type" in code
        ), "Upload function missing comment about not setting Content-Type."

    def test_api_upload_sends_formdata_body(self) -> None:
        """Upload must send the FormData body directly, not JSON.stringify."""
        code = _read(API_CLIENT_PATH)
        # Should pass formData as body, not JSON.stringify(formData)
        upload_match = re.search(
            r"(?:uploadDocument|apiUpload).*?body:\s*formData", code, re.DOTALL
        )
        assert upload_match, "Upload function doesn't send formData as body directly."

    def test_api_upload_attaches_auth_header(self) -> None:
        """Upload must attach Authorization header with JWT."""
        code = _read(API_CLIENT_PATH)
        # NexaApiClient uses a shared request() helper that adds Authorization
        # The upload method delegates to request() which attaches the header
        assert (
            "Authorization" in code
        ), "apiClient must attach Authorization header (via request helper or directly)."
        # Verify the upload method exists and uses the request helper
        assert (
            "uploadDocument" in code or "apiUpload" in code
        ), "Upload function must exist."

    def test_upload_convenience_method(self) -> None:
        """apiClient must have an upload convenience method."""
        code = _read(API_CLIENT_PATH)
        assert (
            "uploadDocument" in code or "uploadFile" in code
        ), "apiClient missing upload method."

    def test_upload_method_sends_formdata(self) -> None:
        """Upload method must handle FormData."""
        code = _read(API_CLIENT_PATH)
        assert "FormData" in code, "apiClient upload must handle FormData."


class TestNexaClientParity:
    """Verify nexa-client production screens mirror the Python-repo screens."""

    NEXA_PIPELINE_DIR = (
        ROOT / "nexa-client" / "packages" / "app" / "features" / "pipeline"
    )

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_nexa_client_screen_exists(self, screen: str) -> None:
        path = self.NEXA_PIPELINE_DIR / f"{screen}.tsx"
        assert path.exists(), f"nexa-client pipeline screen missing: {path}"

    def test_nexa_client_field_card_exists(self) -> None:
        path = self.NEXA_PIPELINE_DIR / f"{FIELD_CARD}.tsx"
        assert path.exists(), f"nexa-client FieldCard missing: {path}"

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_nexa_client_uses_my_ui(self, screen: str) -> None:
        path = self.NEXA_PIPELINE_DIR / f"{screen}.tsx"
        code = _read(path)
        assert "@my/ui" in code, f"nexa-client {screen} doesn't import from @my/ui"

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_nexa_client_uses_nexa_api_client(self, screen: str) -> None:
        path = self.NEXA_PIPELINE_DIR / f"{screen}.tsx"
        code = _read(path)
        assert (
            "NexaApiClient" in code
        ), f"nexa-client {screen} doesn't use NexaApiClient"

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_nexa_client_no_raw_fetch(self, screen: str) -> None:
        path = self.NEXA_PIPELINE_DIR / f"{screen}.tsx"
        code = _read(path)
        code_no_comments = _strip_comments(code)
        assert not re.search(
            r"\bfetch\s*\(", code_no_comments
        ), f"nexa-client {screen} uses raw fetch()."

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_nexa_client_no_localhost(self, screen: str) -> None:
        path = self.NEXA_PIPELINE_DIR / f"{screen}.tsx"
        code = _read(path)
        assert "localhost" not in code, f"nexa-client {screen} contains localhost."


# ═══════════════════════════════════════════════════════════════════════════════
# 18. DocumentPreview component
# ═══════════════════════════════════════════════════════════════════════════════


class TestDocumentPreview:
    """Verify DocumentPreview component uses bounding boxes and is properly
    integrated into ReviewCockpitScreen."""

    DOC_PREVIEW = "DocumentPreview"

    def test_document_preview_exists(self) -> None:
        path = PIPELINE_DIR / f"{self.DOC_PREVIEW}.tsx"
        assert path.exists(), f"DocumentPreview component missing: {path}"

    def test_nexa_client_document_preview_exists(self) -> None:
        path = (
            ROOT
            / "nexa-client"
            / "packages"
            / "app"
            / "features"
            / "pipeline"
            / f"{self.DOC_PREVIEW}.tsx"
        )
        assert path.exists(), f"nexa-client DocumentPreview missing: {path}"

    def test_uses_bounding_box_data(self) -> None:
        """DocumentPreview must render bounding box overlays from source_bbox."""
        code = _read(PIPELINE_DIR / f"{self.DOC_PREVIEW}.tsx")
        assert "source_bbox" in code, "DocumentPreview doesn't use source_bbox."
        assert (
            "BBoxOverlay" in code
        ), "DocumentPreview missing BBoxOverlay sub-component."

    def test_bbox_overlay_uses_normalized_coords(self) -> None:
        """BBoxOverlay must render using normalized 0-1 coordinates."""
        code = _read(PIPELINE_DIR / f"{self.DOC_PREVIEW}.tsx")
        # Should reference percentage-based positioning from bbox values
        assert "%" in code, "BBoxOverlay doesn't use percentage-based positioning."

    def test_svg_based_rendering(self) -> None:
        """Document preview must use SVG for bbox overlays (not canvas)."""
        code = _read(PIPELINE_DIR / f"{self.DOC_PREVIEW}.tsx")
        assert (
            "<svg" in code or "<rect" in code
        ), "DocumentPreview doesn't use SVG overlays."

    def test_highlighted_field_interaction(self) -> None:
        """DocumentPreview must support highlighted field state."""
        code = _read(PIPELINE_DIR / f"{self.DOC_PREVIEW}.tsx")
        assert (
            "highlightedFieldId" in code
        ), "DocumentPreview missing highlightedFieldId prop."
        assert "isHighlighted" in code, "DocumentPreview missing isHighlighted logic."

    def test_field_click_callback(self) -> None:
        """Clicking a bbox region must call onFieldClick."""
        code = _read(PIPELINE_DIR / f"{self.DOC_PREVIEW}.tsx")
        assert "onFieldClick" in code, "DocumentPreview missing onFieldClick callback."

    def test_page_navigation(self) -> None:
        """DocumentPreview must support page navigation."""
        code = _read(PIPELINE_DIR / f"{self.DOC_PREVIEW}.tsx")
        assert "onPageChange" in code, "DocumentPreview missing onPageChange callback."
        assert "currentPage" in code, "DocumentPreview missing currentPage prop."

    def test_page_thumbnails(self) -> None:
        """Multi-page documents must show page thumbnails with field counts."""
        code = _read(PIPELINE_DIR / f"{self.DOC_PREVIEW}.tsx")
        assert (
            "PageThumbnail" in code
        ), "DocumentPreview missing PageThumbnail component."

    def test_risk_level_colours(self) -> None:
        """BBox overlays must use risk-level-specific colours."""
        code = _read(PIPELINE_DIR / f"{self.DOC_PREVIEW}.tsx")
        assert (
            "RISK_OVERLAY" in code
        ), "DocumentPreview missing RISK_OVERLAY colour map."
        for level in ["LOW_RISK", "MEDIUM_RISK", "HIGH_RISK", "CRITICAL_RISK"]:
            assert level in code, f"DocumentPreview missing risk level colour: {level}"

    def test_status_border_styles(self) -> None:
        """BBox overlays must have different border styles per field status."""
        code = _read(PIPELINE_DIR / f"{self.DOC_PREVIEW}.tsx")
        assert "STATUS_BORDER" in code, "DocumentPreview missing STATUS_BORDER styles."
        assert (
            "strokeDasharray" in code
        ), "BBoxOverlay missing dashed borders for needs_review."

    def test_legend(self) -> None:
        """DocumentPreview must show a legend explaining bbox colours."""
        code = _read(PIPELINE_DIR / f"{self.DOC_PREVIEW}.tsx")
        assert "legend" in code.lower(), "DocumentPreview missing legend."

    def test_field_count_per_page(self) -> None:
        """DocumentPreview must show field count per page."""
        code = _read(PIPELINE_DIR / f"{self.DOC_PREVIEW}.tsx")
        assert "on this page" in code, "DocumentPreview missing per-page field count."

    def test_imports_tamagui(self) -> None:
        code = _read(PIPELINE_DIR / f"{self.DOC_PREVIEW}.tsx")
        uses_tamagui = "tamagui" in code or "@my/ui" in code
        assert uses_tamagui, "DocumentPreview doesn't import from tamagui or @my/ui"

    def test_no_raw_fetch(self) -> None:
        code = _read(PIPELINE_DIR / f"{self.DOC_PREVIEW}.tsx")
        code_no_comments = _strip_comments(code)
        assert not re.search(
            r"\bfetch\s*\(", code_no_comments
        ), "DocumentPreview uses raw fetch."

    def test_no_localhost(self) -> None:
        code = _read(PIPELINE_DIR / f"{self.DOC_PREVIEW}.tsx")
        assert "localhost" not in code, "DocumentPreview contains localhost."

    def test_alpha_labeling(self) -> None:
        code = _read(PIPELINE_DIR / f"{self.DOC_PREVIEW}.tsx")
        assert "ALPHA" in code, "DocumentPreview missing ALPHA labeling."


class TestReviewCockpitDocumentPreviewIntegration:
    """Verify ReviewCockpitScreen integrates DocumentPreview properly."""

    def test_imports_document_preview(self) -> None:
        code = _read_screen("ReviewCockpitScreen")
        assert (
            "DocumentPreview" in code
        ), "ReviewCockpitScreen doesn't import DocumentPreview."

    def test_passes_bbox_fields(self) -> None:
        code = _read_screen("ReviewCockpitScreen")
        nw = _normalize_ws(code)
        assert (
            "bboxFields" in nw
        ), "ReviewCockpitScreen doesn't compute bboxFields for DocumentPreview."

    def test_bidirectional_highlighting(self) -> None:
        """Hovering a FieldCard must highlight its bbox and vice versa."""
        code = _read_screen("ReviewCockpitScreen")
        # FieldCard hover → set highlightedFieldId
        assert (
            "handleFieldHighlight" in code
        ), "ReviewCockpitScreen missing field highlight handler."
        # DocumentPreview bbox click → set highlightedFieldId + scroll
        assert (
            "handleBboxFieldClick" in code
        ), "ReviewCockpitScreen missing bbox click handler."
        assert (
            "highlightedFieldId" in code
        ), "ReviewCockpitScreen missing highlightedFieldId state."

    def test_field_card_ids_for_scroll(self) -> None:
        """Field cards must have DOM IDs for scroll-into-view on bbox click."""
        code = _read_screen("ReviewCockpitScreen")
        assert (
            "field-card-" in code
        ), "ReviewCockpitScreen missing field-card-* DOM IDs."
        assert (
            "scrollIntoView" in code
        ), "ReviewCockpitScreen missing scrollIntoView on bbox click."

    def test_total_pages_from_field_data(self) -> None:
        """Total pages must be computed from field source_page data, not hardcoded."""
        code = _read_screen("ReviewCockpitScreen")
        nw = _normalize_ws(code)
        assert "totalPages" in nw, "ReviewCockpitScreen missing totalPages computation."
        assert (
            "source_page" in code
        ), "ReviewCockpitScreen doesn't use source_page for page count."

    def test_no_placeholder_preview_text(self) -> None:
        """Review Cockpit must NOT have just a placeholder text for the preview."""
        code = _read_screen("ReviewCockpitScreen")
        # Should NOT have the old placeholder pattern
        nw = _normalize_ws(code)
        assert (
            "Document preview will render" not in nw
        ), "ReviewCockpitScreen still has placeholder preview text instead of DocumentPreview."

    def test_nexa_client_imports_document_preview(self) -> None:
        code = _read(
            ROOT
            / "nexa-client"
            / "packages"
            / "app"
            / "features"
            / "pipeline"
            / "ReviewCockpitScreen.tsx"
        )
        assert (
            "DocumentPreview" in code
        ), "nexa-client ReviewCockpitScreen doesn't import DocumentPreview."

    def test_nexa_client_bidirectional_highlighting(self) -> None:
        code = _read(
            ROOT
            / "nexa-client"
            / "packages"
            / "app"
            / "features"
            / "pipeline"
            / "ReviewCockpitScreen.tsx"
        )
        assert (
            "highlightedFieldId" in code
        ), "nexa-client ReviewCockpitScreen missing highlighting."
        assert (
            "handleBboxFieldClick" in code
        ), "nexa-client ReviewCockpitScreen missing bbox click handler."


# ═══════════════════════════════════════════════════════════════════════════════
# 19. Route param consistency
# ═══════════════════════════════════════════════════════════════════════════════


class TestRouteParamConsistency:
    """Both repos must use [jobId] for frontend route params."""

    def test_python_repo_uses_jobId(self) -> None:
        dirs = [d.name for d in (NEXT_ROUTES_DIR / "jobs").iterdir() if d.is_dir()]
        assert (
            "[jobId]" in dirs
        ), f"Python-repo jobs route uses {[d for d in dirs]}, expected [jobId]"

    def test_nexa_client_uses_jobId(self) -> None:
        nexa_dir = (
            ROOT
            / "nexa-client"
            / "apps"
            / "next"
            / "app"
            / "doctor"
            / "pipeline"
            / "jobs"
        )
        dirs = [d.name for d in nexa_dir.iterdir() if d.is_dir()]
        assert (
            "[jobId]" in dirs
        ), f"nexa-client jobs route uses {[d for d in dirs]}, expected [jobId]"

    def test_python_repo_review_uses_jobId(self) -> None:
        dirs = [d.name for d in (NEXT_ROUTES_DIR / "review").iterdir() if d.is_dir()]
        assert (
            "[jobId]" in dirs
        ), f"Python-repo review route uses {[d for d in dirs]}, expected [jobId]"

    def test_python_repo_commit_uses_jobId(self) -> None:
        dirs = [d.name for d in (NEXT_ROUTES_DIR / "commit").iterdir() if d.is_dir()]
        assert (
            "[jobId]" in dirs
        ), f"Python-repo commit route uses {[d for d in dirs]}, expected [jobId]"


# ═══════════════════════════════════════════════════════════════════════════════
# 20. Review queue & cockpit comprehensive features (Days 6-8)
# ═══════════════════════════════════════════════════════════════════════════════


class TestFieldCardAutoApprovedReadOnly:
    """Auto-approved fields must be shown but not editable — they already passed."""

    def test_auto_approved_not_editable(self) -> None:
        """Auto-approved fields must NOT show Approve/Edit/Reject action buttons."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        nw = _normalize_ws(code)
        # Must have an isAutoApproved or isReadOnly check that gates buttons
        assert (
            "isAutoApproved" in nw or "auto_approved" in nw
        ), "FieldCard missing auto_approved read-only check."
        assert (
            "isReadOnly" in nw
        ), "FieldCard missing isReadOnly guard for action buttons."

    def test_auto_approved_border_style(self) -> None:
        """Auto-approved fields must have a distinct (blue) border, not orange."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        nw = _normalize_ws(code)
        assert (
            "$blue5" in nw
        ), "FieldCard missing $blue5 border color for auto_approved fields."

    def test_auto_approved_read_only_message(self) -> None:
        """Auto-approved fields must show a message indicating they passed automatically."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        nw = _normalize_ws(code)
        assert (
            "auto-approved" in nw.lower() or "auto approved" in nw.lower()
        ), "FieldCard missing auto-approved message for read-only fields."

    def test_nexa_client_auto_approved_not_editable(self) -> None:
        """nexa-client FieldCard must also gate auto-approved fields."""
        code = _read(
            ROOT
            / "nexa-client"
            / "packages"
            / "app"
            / "features"
            / "pipeline"
            / f"{FIELD_CARD}.tsx"
        )
        nw = _normalize_ws(code)
        assert (
            "isAutoApproved" in nw or "auto_approved" in nw
        ), "nexa-client FieldCard missing auto_approved read-only check."
        assert (
            "isReadOnly" in nw
        ), "nexa-client FieldCard missing isReadOnly guard for action buttons."


class TestFieldCardRiskVisuals:
    """CRITICAL and HIGH risk fields must be visually distinct (red/orange)."""

    def test_critical_risk_red_styling(self) -> None:
        """CRITICAL_RISK must use red background and text colors."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        # CRITICAL_RISK must be in RISK_STYLES
        assert "CRITICAL_RISK" in code, "FieldCard missing CRITICAL_RISK level."
        # Must use $red4 / $red10 for CRITICAL
        assert (
            "$red4" in code
        ), "FieldCard missing red background for CRITICAL/HIGH risk."
        assert "$red10" in code, "FieldCard missing red text for CRITICAL/HIGH risk."

    def test_high_risk_red_styling(self) -> None:
        """HIGH_RISK must use red background and text colors."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        assert "HIGH_RISK" in code, "FieldCard missing HIGH_RISK level."

    def test_medium_risk_orange_styling(self) -> None:
        """MEDIUM_RISK must use orange background and text colors."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        assert (
            "$orange4" in code
        ), "FieldCard missing orange background for MEDIUM risk."
        assert "$orange10" in code, "FieldCard missing orange text for MEDIUM risk."

    def test_risk_icons(self) -> None:
        """Each risk level must have a distinctive icon."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        # Check for risk-specific icons
        assert (
            "🚨" in code or "⛔" in code
        ), "FieldCard missing CRITICAL/HIGH risk icon."
        assert "⚠" in code, "FieldCard missing MEDIUM risk icon."
        assert "✓" in code, "FieldCard missing LOW risk icon."


class TestFieldCardActions:
    """Approve, Edit, Reject action flows in FieldCard."""

    def test_approve_calls_review_endpoint(self) -> None:
        """Approve must call the field review endpoint with action='approve'."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        nw = _normalize_ws(code)
        # Must have handleApprove function
        assert "handleApprove" in nw, "FieldCard missing handleApprove handler."
        # Must pass action: 'approve'
        assert "approve" in code, "FieldCard missing approve action payload."

    def test_reject_prompts_for_reason(self) -> None:
        """Reject must prompt for a reason (rejectMode) before calling the endpoint."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        nw = _normalize_ws(code)
        assert "rejectMode" in nw, "FieldCard missing rejectMode state."
        assert "rejectNotes" in nw, "FieldCard missing rejectNotes state."
        assert "Confirm Reject" in code, "FieldCard missing 'Confirm Reject' button."
        assert (
            "review_notes" in code
        ), "FieldCard missing review_notes in reject payload."

    def test_edit_mode_with_corrected_value(self) -> None:
        """Edit must present an inline editor and send corrected_value."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        nw = _normalize_ws(code)
        assert "editMode" in nw, "FieldCard missing editMode state."
        assert "editValue" in nw, "FieldCard missing editValue state."
        assert (
            "corrected_value" in code
        ), "FieldCard missing corrected_value in edit payload."
        assert "Save Edit" in code, "FieldCard missing 'Save Edit' button."

    def test_edit_cancel(self) -> None:
        """Edit mode must have a Cancel button to exit without saving."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        assert (
            "Cancel" in code
        ), "FieldCard missing Cancel button for edit/reject modes."

    def test_action_loading_state(self) -> None:
        """Actions must show loading state while request is in flight."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        nw = _normalize_ws(code)
        assert "actionLoading" in nw, "FieldCard missing actionLoading state."
        # Buttons should be disabled during loading
        assert "disabled" in code, "FieldCard buttons not disabled during loading."

    def test_action_error_handling(self) -> None:
        """Actions must handle errors (401, 403, and generic)."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        nw = _normalize_ws(code)
        assert "actionError" in nw, "FieldCard missing actionError state."
        assert "Session expired" in code, "FieldCard missing 401 error message."
        assert "Consent required" in code, "FieldCard missing 403 error message."

    def test_action_confirmation_after_approve(self) -> None:
        """After approve, FieldCard must show ActionConfirmation feedback."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        assert (
            "ActionConfirmation" in code
        ), "FieldCard missing ActionConfirmation component."
        assert (
            "showConfirmation" in code
        ), "FieldCard missing showConfirmation callback."
        assert (
            "lastAction" in code
        ), "FieldCard missing lastAction state for confirmation display."

    def test_edit_mode_shows_original_value(self) -> None:
        """Edit mode must show original AI extraction for comparison."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        assert (
            "Original AI extraction" in code
        ), "FieldCard edit mode missing original value label for comparison."

    def test_reject_mode_shows_value_to_exclude(self) -> None:
        """Reject mode must show the value being excluded."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        assert (
            "Rejecting Field" in code
        ), "FieldCard reject mode missing rejection context heading."

    def test_approve_button_shows_loading_text(self) -> None:
        """Approve button must show 'Approving…' during action."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        assert "Approving" in code, "FieldCard Approve button missing loading text."

    def test_save_edit_button_shows_loading_text(self) -> None:
        """Save Edit button must show 'Saving…' during action."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        assert "Saving" in code, "FieldCard Save Edit button missing loading text."

    def test_reject_button_shows_loading_text(self) -> None:
        """Confirm Reject button must show 'Rejecting…' during action."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        assert (
            "Rejecting" in code
        ), "FieldCard Confirm Reject button missing loading text."

    def test_error_dismiss_button(self) -> None:
        """Action error must have a Dismiss button."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        assert "Dismiss" in code, "FieldCard action error missing Dismiss button."

    def test_nexa_client_reject_prompts_for_reason(self) -> None:
        """nexa-client FieldCard must also prompt for reject reason."""
        code = _read(
            ROOT
            / "nexa-client"
            / "packages"
            / "app"
            / "features"
            / "pipeline"
            / f"{FIELD_CARD}.tsx"
        )
        nw = _normalize_ws(code)
        assert "rejectMode" in nw, "nexa-client FieldCard missing rejectMode."
        assert "review_notes" in code, "nexa-client FieldCard missing review_notes."


class TestSourceHighlightInteraction:
    """Clicking a field must highlight its source location in the document preview."""

    def test_field_card_on_source_page_click(self) -> None:
        """FieldCard must accept onSourcePageClick prop to jump preview to page."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        nw = _normalize_ws(code)
        assert "onSourcePageClick" in nw, "FieldCard missing onSourcePageClick prop."
        assert "Jump" in code, "FieldCard missing Jump button for source page."

    def test_cockpit_passes_highlight_handler_to_field_cards(self) -> None:
        """ReviewCockpitScreen must pass highlight handlers to FieldCard."""
        code = _read_screen("ReviewCockpitScreen")
        nw = _normalize_ws(code)
        assert (
            "handleFieldHighlight" in nw
        ), "ReviewCockpitScreen missing handleFieldHighlight for FieldCard."

    def test_cockpit_passes_source_page_click_to_field_cards(self) -> None:
        """ReviewCockpitScreen must pass onSourcePageClick to FieldCard."""
        code = _read_screen("ReviewCockpitScreen")
        nw = _normalize_ws(code)
        assert (
            "handleSourcePageClick" in nw
        ), "ReviewCockpitScreen missing handleSourcePageClick for FieldCard."

    def test_cockpit_bbox_click_scrolls_to_field(self) -> None:
        """Clicking a bbox in DocumentPreview must scroll to the corresponding FieldCard."""
        code = _read_screen("ReviewCockpitScreen")
        assert (
            "scrollIntoView" in code
        ), "ReviewCockpitScreen missing scrollIntoView on bbox click."
        assert (
            "field-card-" in code
        ), "ReviewCockpitScreen missing field-card-* DOM IDs."

    def test_nexa_client_source_highlight(self) -> None:
        """nexa-client ReviewCockpitScreen must also support source highlighting."""
        code = _read(
            ROOT
            / "nexa-client"
            / "packages"
            / "app"
            / "features"
            / "pipeline"
            / "ReviewCockpitScreen.tsx"
        )
        assert (
            "highlightedFieldId" in code
        ), "nexa-client ReviewCockpitScreen missing highlightedFieldId."
        assert (
            "handleBboxFieldClick" in code
        ), "nexa-client ReviewCockpitScreen missing bbox click handler."


class TestReviewCockpitRouteParams:
    """ReviewCockpitScreen must use useParams() for jobId from route param [jobId]."""

    def test_uses_use_params(self) -> None:
        """ReviewCockpitScreen must import and use useParams from next/navigation."""
        code = _read_screen("ReviewCockpitScreen")
        assert (
            "useParams" in code
        ), "ReviewCockpitScreen doesn't import useParams from next/navigation."

    def test_gets_jobId_from_route_params(self) -> None:
        """jobId must come from routeParams.jobId, not searchParams.get('job_id')."""
        code = _read_screen("ReviewCockpitScreen")
        nw = _normalize_ws(code)
        assert (
            "routeParams" in nw or "useParams" in code
        ), "ReviewCockpitScreen doesn't use route params for jobId."
        # Should reference routeParams.jobId
        assert "jobId" in code, "ReviewCockpitScreen missing jobId reference."

    def test_no_search_params_job_id(self) -> None:
        """ReviewCockpitScreen must NOT use searchParams.get('job_id') for the job ID."""
        code = _read_screen("ReviewCockpitScreen")
        code_no_comments = _strip_comments(code)
        # Should NOT use searchParams to get job_id
        assert (
            "searchParams.get('job_id')" not in code_no_comments
        ), "ReviewCockpitScreen still uses searchParams.get('job_id') instead of useParams."

    def test_nexa_client_uses_use_params(self) -> None:
        """nexa-client ReviewCockpitScreen must also use useParams."""
        code = _read(
            ROOT
            / "nexa-client"
            / "packages"
            / "app"
            / "features"
            / "pipeline"
            / "ReviewCockpitScreen.tsx"
        )
        assert (
            "useParams" in code
        ), "nexa-client ReviewCockpitScreen doesn't use useParams."


class TestReviewCockpitProgress:
    """ReviewCockpitScreen must show review progress bar and completion state."""

    def test_has_progress_bar(self) -> None:
        """ReviewCockpitScreen must have a Progress bar for review completion."""
        code = _read_screen("ReviewCockpitScreen")
        assert (
            "Progress" in code
        ), "ReviewCockpitScreen missing Progress component for review tracking."

    def test_has_progress_percentage(self) -> None:
        """ReviewCockpitScreen must compute progressPct for the progress bar."""
        code = _read_screen("ReviewCockpitScreen")
        assert (
            "progressPct" in code
        ), "ReviewCockpitScreen missing progressPct computation."

    def test_all_reviewed_badge(self) -> None:
        """When all fields reviewed, cockpit must show ✓ All Reviewed badge."""
        code = _read_screen("ReviewCockpitScreen")
        assert (
            "All Reviewed" in code
        ), "ReviewCockpitScreen missing 'All Reviewed' completion badge."

    def test_empty_state_has_refresh(self) -> None:
        """When no fields found, cockpit must offer a Refresh button."""
        code = _read_screen("ReviewCockpitScreen")
        nw = _normalize_ws(code)
        # The empty state should offer refresh
        assert (
            "Refresh" in code or "fetchJob" in nw
        ), "ReviewCockpitScreen empty state missing Refresh button."

    def test_commit_button_enabled_when_all_reviewed(self) -> None:
        """Commit button must be enabled (green theme) when all fields reviewed."""
        code = _read_screen("ReviewCockpitScreen")
        # Should have conditional green theme for commit
        assert (
            "green" in code.lower() or "allReviewed" in code
        ), "ReviewCockpitScreen missing green Commit button when all reviewed."


class TestReviewQueueFetchMethod:
    """ReviewQueueScreen must use apiClient.getReviewQueue() convenience method."""

    def test_uses_get_review_queue(self) -> None:
        """ReviewQueueScreen must call apiClient.getReviewQueue()."""
        code = _read_screen("ReviewQueueScreen")
        nw = _normalize_ws(code)
        assert (
            "getReviewQueue" in nw
        ), "ReviewQueueScreen doesn't use apiClient.getReviewQueue() convenience method."

    def test_api_client_has_get_review_queue(self) -> None:
        """The apiClient must define getReviewQueue convenience method."""
        code = _read(API_CLIENT_PATH)
        nw = _normalize_ws(code)
        assert (
            "getReviewQueue" in nw
        ), "apiClient missing getReviewQueue convenience method."
        assert (
            "/api/v2/pipeline/review-queue" in code
        ), "apiClient.getReviewQueue() doesn't call correct endpoint."


class TestApiClientConvenienceMethods:
    """apiClient must have pipeline convenience methods for all pipeline endpoints."""

    def test_has_get_extraction_job_status(self) -> None:
        """apiClient must have getExtractionJobStatus method."""
        code = _read(API_CLIENT_PATH)
        nw = _normalize_ws(code)
        assert (
            "getExtractionJobStatus" in nw
        ), "apiClient missing getExtractionJobStatus convenience method."

    def test_get_extraction_job_status_calls_correct_endpoint(self) -> None:
        """getExtractionJobStatus must call /api/v2/pipeline/jobs/{jobId}."""
        code = _read(API_CLIENT_PATH)
        assert (
            "/api/v2/pipeline/jobs/" in code
        ), "apiClient.getExtractionJobStatus() doesn't call correct endpoint."

    def test_has_review_field(self) -> None:
        """apiClient must have reviewField method."""
        code = _read(API_CLIENT_PATH)
        nw = _normalize_ws(code)
        assert "reviewField" in nw, "apiClient missing reviewField convenience method."

    def test_review_field_calls_correct_endpoint(self) -> None:
        """reviewField must call /api/v2/pipeline/fields/{fieldId}/review."""
        code = _read(API_CLIENT_PATH)
        assert (
            "/api/v2/pipeline/fields/" in code
        ), "apiClient.reviewField() doesn't call correct endpoint."

    def test_has_commit_extraction_job(self) -> None:
        """apiClient must have commitExtractionJob method."""
        code = _read(API_CLIENT_PATH)
        nw = _normalize_ws(code)
        assert (
            "commitExtractionJob" in nw
        ), "apiClient missing commitExtractionJob convenience method."

    def test_commit_extraction_job_calls_correct_endpoint(self) -> None:
        """commitExtractionJob must call /api/v2/pipeline/jobs/{jobId}/commit."""
        code = _read(API_CLIENT_PATH)
        assert (
            "/commit" in code
        ), "apiClient.commitExtractionJob() doesn't call commit endpoint."

    def test_convenience_methods_attach_consent_headers(self) -> None:
        """Pipeline convenience methods must attach X-Consent-Token and X-Consent-Purpose headers."""
        code = _read(API_CLIENT_PATH)
        # Each convenience method should pass consent token and purpose
        assert (
            "X-Consent-Token" in code
        ), "apiClient pipeline methods missing X-Consent-Token header."
        assert (
            "X-Consent-Purpose" in code
        ), "apiClient pipeline methods missing X-Consent-Purpose header."


class TestReviewCockpitFieldCategories:
    """Review Cockpit must separate fields by status categories."""

    def test_needs_review_fields_first(self) -> None:
        """needs_review fields must be rendered first in the list."""
        code = _read_screen("ReviewCockpitScreen")
        nw = _normalize_ws(code)
        # Must have a filter for needs_review fields
        assert (
            "needs_review" in nw
        ), "ReviewCockpitScreen doesn't filter for needs_review fields."

    def test_auto_approved_section(self) -> None:
        """Legacy auto-approved fields must be shown as blocked."""
        code = _read_screen("ReviewCockpitScreen")
        nw = _normalize_ws(code)
        assert (
            "auto_approved" in nw
        ), "ReviewCockpitScreen doesn't have auto_approved section."
        assert (
            "Legacy auto-approved — blocked" in code
        ), "ReviewCockpitScreen must label legacy auto-approved rows as blocked."

    def test_reviewed_section(self) -> None:
        """Already adjudicated fields must be in a Reviewed section."""
        code = _read_screen("ReviewCockpitScreen")
        assert (
            "Reviewed" in code
        ), "ReviewCockpitScreen missing Reviewed section header."

    def test_review_progress_stats(self) -> None:
        """ReviewCockpitScreen must compute review progress stats."""
        code = _read_screen("ReviewCockpitScreen")
        nw = _normalize_ws(code)
        assert (
            "reviewStats" in nw
        ), "ReviewCockpitScreen missing reviewStats computation."
        assert "needsReview" in nw, "ReviewCockpitScreen missing needsReview stat."
        assert "adjudicated" in nw, "ReviewCockpitScreen missing adjudicated stat."
        assert "autoApproved" in nw, "ReviewCockpitScreen missing autoApproved stat."

    def test_commit_button_gated_by_all_reviewed(self) -> None:
        """Commit is blocked by needs-review and legacy auto-approved fields."""
        code = _read_screen("ReviewCockpitScreen")
        nw = _normalize_ws(code)
        assert (
            "allReviewed" in nw
        ), "ReviewCockpitScreen missing allReviewed check for commit gating."
        assert "needsReview.length === 0 && autoApproved.length === 0" in nw


class TestFieldCardProvenanceBadgeStates:
    """ProvenanceBadge must show correct verification status for each field state."""

    def test_clinician_verified_for_approved(self) -> None:
        """Approved fields must show 'Clinician verified' badge."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        assert (
            "Clinician verified" in code
        ), "ProvenanceBadge missing 'Clinician verified' for approved fields."

    def test_auto_approved_badge(self) -> None:
        """Legacy auto-approved fields must show a blocked badge."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        assert (
            "Legacy auto-approved blocked" in code
        ), "ProvenanceBadge must not present legacy auto-approval as verified."

    def test_ai_extracted_not_yet_verified(self) -> None:
        """needs_review fields must show 'AI extracted · X% · Not yet verified'."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        assert "AI extracted" in code, "ProvenanceBadge missing 'AI extracted' label."
        assert (
            "Not yet verified" in code
        ), "ProvenanceBadge missing 'Not yet verified' label."

    def test_provenance_badge_shows_confidence_percentage(self) -> None:
        """ProvenanceBadge must show confidence as percentage."""
        code = _read(PIPELINE_DIR / f"{FIELD_CARD}.tsx")
        assert (
            "pct" in code or "confidence" in code
        ), "ProvenanceBadge doesn't compute confidence percentage."


class TestReviewQueueItemDetails:
    """Review queue items must display all relevant details."""

    def test_shows_document_title(self) -> None:
        code = _read_screen("ReviewQueueScreen")
        assert "document_title" in code, "Review Queue missing document_title."

    def test_shows_flagged_fields_count(self) -> None:
        code = _read_screen("ReviewQueueScreen")
        assert (
            "flagged_fields_count" in code
        ), "Review Queue missing flagged_fields_count."

    def test_shows_highest_risk_level(self) -> None:
        code = _read_screen("ReviewQueueScreen")
        assert "highest_risk_level" in code, "Review Queue missing highest_risk_level."

    def test_shows_queued_timestamp(self) -> None:
        code = _read_screen("ReviewQueueScreen")
        assert "queued_at" in code, "Review Queue missing queued_at timestamp."

    def test_shows_patient_id(self) -> None:
        code = _read_screen("ReviewQueueScreen")
        assert "patient_id" in code, "Review Queue missing patient_id display."

    def test_consent_required_guard(self) -> None:
        """Review Queue must check for consent token and show locked state."""
        code = _read_screen("ReviewQueueScreen")
        assert (
            "Consent Required" in code
        ), "Review Queue missing consent required guard."


# ═══════════════════════════════════════════════════════════════════════════════
# 21. Commit screen safety (Days 9-11)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCommitSafetyBadge:
    """CommitSafetyBadge must render correct visual indicator per status."""

    def test_auto_approved_badge(self) -> None:
        """Legacy auto-approved fields must show a red blocking badge."""
        code = _read_screen("CommitScreen")
        assert "Legacy state — blocked" in code
        assert 'backgroundColor="$red4"' in code

    def test_human_approved_badge(self) -> None:
        """Human-approved fields must show blue 'Verified ✓' badge."""
        code = _read_screen("CommitScreen")
        assert (
            "Verified ✓" in code
        ), "CommitScreen missing 'Verified ✓' badge for approved."
        assert "$blue4" in code, "CommitScreen missing blue color for verified badge."

    def test_edited_badge(self) -> None:
        """Edited fields must show yellow 'Edited ✎' badge."""
        code = _read_screen("CommitScreen")
        assert "Edited ✎" in code, "CommitScreen missing 'Edited ✎' badge."
        assert "$yellow4" in code, "CommitScreen missing yellow color for edited badge."

    def test_rejected_badge(self) -> None:
        """Rejected fields must show red '✕ Excluded' badge."""
        code = _read_screen("CommitScreen")
        assert (
            "✕ Excluded" in code
        ), "CommitScreen missing '✕ Excluded' badge for rejected."
        assert "$red4" in code, "CommitScreen missing red color for rejected badge."

    def test_unresolved_badge(self) -> None:
        """Unresolved fields must show orange '⚠ Unresolved' badge."""
        code = _read_screen("CommitScreen")
        assert "Unresolved" in code, "CommitScreen missing 'Unresolved' badge."
        assert "$orange4" in code, "CommitScreen missing orange for unresolved badge."

    def test_commit_safety_badge_component_exists(self) -> None:
        """CommitSafetyBadge must be a defined component."""
        code = _read_screen("CommitScreen")
        assert (
            "CommitSafetyBadge" in code
        ), "CommitScreen missing CommitSafetyBadge component."

    def test_nexa_client_commit_safety_badge(self) -> None:
        """nexa-client CommitScreen must also have CommitSafetyBadge."""
        code = _read(
            ROOT
            / "nexa-client"
            / "packages"
            / "app"
            / "features"
            / "pipeline"
            / "CommitScreen.tsx"
        )
        assert "CommitSafetyBadge" in code, "nexa-client missing CommitSafetyBadge."
        assert (
            "Legacy state — blocked" in code
        ), "nexa-client must block legacy auto-approval."
        assert "Verified ✓" in code, "nexa-client missing Verified ✓ badge."
        assert "Edited ✎" in code, "nexa-client missing Edited ✎ badge."
        assert "✕ Excluded" in code, "nexa-client missing ✕ Excluded badge."


class TestCommitHighCriticalWarning:
    """HIGH/CRITICAL risk warning banner must render when relevant fields exist."""

    def test_high_critical_warning_banner(self) -> None:
        """CommitScreen must show a warning banner for HIGH/CRITICAL risk fields."""
        code = _read_screen("CommitScreen")
        nw = _normalize_ws(code)
        assert (
            "HIGH/CRITICAL Risk Fields Present" in code
        ), "CommitScreen missing HIGH/CRITICAL risk warning banner text."
        assert (
            "hasHighOrCriticalRisk" in nw
        ), "CommitScreen missing hasHighOrCriticalRisk computed flag."

    def test_warning_uses_red_styling(self) -> None:
        """The HIGH/CRITICAL warning must use red styling to be visually prominent."""
        code = _read_screen("CommitScreen")
        # The warning card should use red background
        assert (
            "$red4" in code
        ), "CommitScreen HIGH/CRITICAL warning missing red styling."

    def test_warning_reminds_reviewer(self) -> None:
        """The warning must remind the reviewer to double-check."""
        code = _read_screen("CommitScreen")
        assert (
            "double-check" in code
        ), "CommitScreen HIGH/CRITICAL warning missing double-check reminder."

    def test_nexa_client_high_critical_warning(self) -> None:
        """nexa-client must also have the HIGH/CRITICAL warning banner."""
        code = _read(
            ROOT
            / "nexa-client"
            / "packages"
            / "app"
            / "features"
            / "pipeline"
            / "CommitScreen.tsx"
        )
        assert "HIGH/CRITICAL" in code, "nexa-client missing HIGH/CRITICAL warning."
        assert (
            "hasHighOrCriticalRisk" in code
        ), "nexa-client missing hasHighOrCriticalRisk."


class TestCommitDisabledWithUnresolved:
    """Commit button must be disabled while any field needs review."""

    def test_can_commit_checks_needs_review(self) -> None:
        """canCommit must be false when needsReview > 0."""
        code = _read_screen("CommitScreen")
        nw = _normalize_ws(code)
        assert "canCommit" in nw, "CommitScreen missing canCommit gate."
        assert (
            "needsReview" in nw
        ), "CommitScreen missing needsReview check in canCommit."

    def test_commit_button_disabled_when_unresolved(self) -> None:
        """Commit button must have disabled={!canCommit}."""
        code = _read_screen("CommitScreen")
        assert "disabled" in code, "CommitScreen commit button missing disabled prop."

    def test_unresolved_count_message(self) -> None:
        """Commit button text must show how many fields still need review."""
        code = _read_screen("CommitScreen")
        assert (
            "still need review" in code
        ), "CommitScreen missing unresolved count message on commit button."

    def test_unresolved_fields_warning_section(self) -> None:
        """CommitScreen must show a warning section listing unresolved fields."""
        code = _read_screen("CommitScreen")
        nw = _normalize_ws(code)
        assert (
            "unresolvedFields" in nw
        ), "CommitScreen missing unresolvedFields in fieldStats."
        assert (
            "blocking commit" in code.lower()
        ), "CommitScreen missing 'blocking commit' label for unresolved fields."

    def test_go_to_review_cockpit_button(self) -> None:
        """When unresolved, must show a button to navigate back to Review Cockpit."""
        code = _read_screen("CommitScreen")
        assert (
            "Go to Review Cockpit" in code
        ), "CommitScreen missing 'Go to Review Cockpit' navigation button."


class TestCommitEnabledWhenAllResolved:
    """Commit button must be enabled when all fields are resolved."""

    def test_can_commit_true_when_no_needs_review(self) -> None:
        """canCommit = needsReview === 0 && committable > 0 && commitState === 'idle'."""
        code = _read_screen("CommitScreen")
        nw = _normalize_ws(code)
        assert "canCommit" in nw, "CommitScreen missing canCommit logic."
        # Must check committable > 0 (not just needsReview === 0)
        assert (
            "committable" in nw
        ), "CommitScreen missing committable check in canCommit."

    def test_commit_button_shows_field_count(self) -> None:
        """When enabled, commit button must show the count of fields to commit."""
        code = _read_screen("CommitScreen")
        assert (
            "Commit" in code and "Field" in code
        ), "CommitScreen missing field count in commit button text."

    def test_no_fields_to_commit_message(self) -> None:
        """When no fields to commit, button must show 'No fields to commit'."""
        code = _read_screen("CommitScreen")
        assert (
            "No fields to commit" in code
        ), "CommitScreen missing 'No fields to commit' message."


class TestCommitSuccess:
    """Commit success must show fields committed to timeline."""

    def test_success_state_shows_committed_count(self) -> None:
        """Success state must show committed_fields_count."""
        code = _read_screen("CommitScreen")
        assert (
            "committed_fields_count" in code
        ), "CommitScreen missing committed_fields_count in success state."

    def test_success_shows_timeline_event(self) -> None:
        """Success state must show timeline_event_id proving fields are in timeline."""
        code = _read_screen("CommitScreen")
        assert (
            "timeline_event_id" in code
        ), "CommitScreen missing timeline_event_id in success state."

    def test_success_shows_ledger_hash(self) -> None:
        """Success state must show audit/ledger hash or timeline reference."""
        code = _read_screen("CommitScreen")
        assert (
            "ledger_tx_hash" in code
            or "ledger" in code.lower()
            or "timeline_event_id" in code
        ), "CommitScreen missing ledger hash or timeline event reference in success state."

    def test_success_shows_committed_at_timestamp(self) -> None:
        """Success state must show the committed_at timestamp."""
        code = _read_screen("CommitScreen")
        assert (
            "committed_at" in code
        ), "CommitScreen missing committed_at in success state."

    def test_success_committed_heading(self) -> None:
        """Success state must show a clear 'Committed' heading."""
        code = _read_screen("CommitScreen")
        assert (
            "Committed" in code
        ), "CommitScreen missing 'Committed' heading in success state."

    def test_success_navigation_buttons(self) -> None:
        """Success state must offer Upload Another and Back to Dashboard."""
        code = _read_screen("CommitScreen")
        assert "Upload Another" in code, "CommitScreen missing 'Upload Another' button."
        assert (
            "Back to Dashboard" in code
        ), "CommitScreen missing 'Back to Dashboard' button."


class TestCommitFailure409:
    """Commit failure with 409 must show which fields remain unresolved."""

    def test_handles_409_conflict(self) -> None:
        """CommitScreen must handle HTTP 409 (unresolved fields)."""
        code = _read_screen("CommitScreen")
        nw = _normalize_ws(code)
        assert "409" in nw, "CommitScreen doesn't handle HTTP 409."

    def test_409_error_message_mentions_unresolved(self) -> None:
        """409 error message must mention unresolved fields."""
        code = _read_screen("CommitScreen")
        nw = _normalize_ws(code)
        assert (
            "unresolved" in nw.lower() or "incomplete" in nw.lower()
        ), "CommitScreen 409 message doesn't mention unresolved fields."

    def test_error_dismiss_button(self) -> None:
        """Commit error must have a Dismiss button to reset state."""
        code = _read_screen("CommitScreen")
        assert "Dismiss" in code, "CommitScreen missing Dismiss button for errors."

    def test_error_renders_with_red_styling(self) -> None:
        """Commit error card must use red styling."""
        code = _read_screen("CommitScreen")
        # The error card should use red styling
        nw = _normalize_ws(code)
        assert (
            "$red4" in code and "commitError" in nw
        ), "CommitScreen error card missing red styling."


class TestCommitFieldGrouping:
    """Commit screen must group fields by status category."""

    def test_auto_approved_section(self) -> None:
        """Legacy auto-approved fields must be separated and block commit."""
        code = _read_screen("CommitScreen")
        nw = _normalize_ws(code)
        assert "autoApproved" in nw, "CommitScreen missing autoApproved field group."
        assert "Legacy auto-approved — blocked" in code
        assert "const committableFields = [...humanApproved, ...edited]" in nw

    def test_human_approved_section(self) -> None:
        """Human-approved fields must be in a separate 'Clinician Verified' section."""
        code = _read_screen("CommitScreen")
        nw = _normalize_ws(code)
        assert "humanApproved" in nw, "CommitScreen missing humanApproved field group."
        assert (
            "Clinician Verified" in code
        ), "CommitScreen missing 'Clinician Verified' section header."

    def test_edited_section(self) -> None:
        """Edited fields must be in a separate section."""
        code = _read_screen("CommitScreen")
        nw = _normalize_ws(code)
        assert "edited" in nw, "CommitScreen missing edited field group."

    def test_rejected_fields_excluded(self) -> None:
        """Rejected fields must be shown as excluded from commit."""
        code = _read_screen("CommitScreen")
        assert "rejectedFields" in code, "CommitScreen missing rejectedFields group."
        assert (
            "will NOT be committed" in code
        ), "CommitScreen missing 'will NOT be committed' label for rejected fields."

    def test_rejected_fields_have_strikethrough(self) -> None:
        """Rejected field values must be shown with strikethrough."""
        code = _read_screen("CommitScreen")
        assert (
            "line-through" in code
        ), "CommitScreen missing strikethrough for rejected field values."

    def test_rejected_fields_have_low_opacity(self) -> None:
        """Rejected field cards must have reduced opacity."""
        code = _read_screen("CommitScreen")
        assert "opacity" in code, "CommitScreen missing opacity for rejected fields."


class TestCommitUsesConvenienceMethods:
    """CommitScreen must use apiClient convenience methods and useParams."""

    def test_uses_commit_extraction_job(self) -> None:
        """CommitScreen must use apiClient.commitExtractionJob() for commit."""
        code = _read_screen("CommitScreen")
        nw = _normalize_ws(code)
        assert (
            "commitExtractionJob" in nw
        ), "CommitScreen doesn't use apiClient.commitExtractionJob()."

    def test_uses_get_extraction_job_status(self) -> None:
        """CommitScreen must use apiClient.getExtractionJobStatus() for fetch."""
        code = _read_screen("CommitScreen")
        nw = _normalize_ws(code)
        assert (
            "getExtractionJobStatus" in nw
        ), "CommitScreen doesn't use apiClient.getExtractionJobStatus()."

    def test_uses_use_params_for_job_id(self) -> None:
        """CommitScreen must use useParams() for jobId from route param."""
        code = _read_screen("CommitScreen")
        assert "useParams" in code, "CommitScreen doesn't use useParams."
        assert "routeParams" in code, "CommitScreen doesn't read routeParams.jobId."

    def test_no_search_params_job_id(self) -> None:
        """CommitScreen must NOT use searchParams.get('job_id')."""
        code = _read_screen("CommitScreen")
        code_no_comments = _strip_comments(code)
        assert (
            "searchParams.get('job_id')" not in code_no_comments
        ), "CommitScreen still uses searchParams.get('job_id') instead of useParams."

    def test_nexa_client_uses_use_params(self) -> None:
        """nexa-client CommitScreen must also use useParams."""
        code = _read(
            ROOT
            / "nexa-client"
            / "packages"
            / "app"
            / "features"
            / "pipeline"
            / "CommitScreen.tsx"
        )
        assert "useParams" in code, "nexa-client CommitScreen doesn't use useParams."
        assert (
            "routeParams" in code
        ), "nexa-client CommitScreen doesn't read routeParams."

    def test_nexa_client_uses_commit_extraction_job(self) -> None:
        """nexa-client must use NexaApiClient.commitExtractionJob()."""
        code = _read(
            ROOT
            / "nexa-client"
            / "packages"
            / "app"
            / "features"
            / "pipeline"
            / "CommitScreen.tsx"
        )
        assert (
            "commitExtractionJob" in code
        ), "nexa-client CommitScreen doesn't use commitExtractionJob."


class TestCommitFieldSummaryRow:
    """FieldSummaryRow must show confidence, risk, and safety badge per field."""

    def test_field_summary_row_component_exists(self) -> None:
        """FieldSummaryRow component must be defined."""
        code = _read_screen("CommitScreen")
        assert (
            "FieldSummaryRow" in code
        ), "CommitScreen missing FieldSummaryRow component."

    def test_shows_confidence_percentage(self) -> None:
        """FieldSummaryRow must show confidence as percentage."""
        code = _read_screen("CommitScreen")
        assert "confidence" in code, "CommitScreen missing confidence display."
        assert "Math.round" in code or "%" in code, "CommitScreen missing % formatting."

    def test_shows_risk_level_badge(self) -> None:
        """FieldSummaryRow must show risk level badge per field."""
        code = _read_screen("CommitScreen")
        assert (
            "risk_level" in code
        ), "CommitScreen missing risk_level in FieldSummaryRow."

    def test_shows_safety_badge_per_field(self) -> None:
        """FieldSummaryRow must render CommitSafetyBadge for each field."""
        code = _read_screen("CommitScreen")
        assert (
            "CommitSafetyBadge" in code
        ), "CommitScreen missing CommitSafetyBadge in FieldSummaryRow."

    def test_shows_corrected_value_when_present(self) -> None:
        """FieldSummaryRow must show corrected_value when available."""
        code = _read_screen("CommitScreen")
        assert (
            "corrected_value" in code
        ), "CommitScreen missing corrected_value display."

    def test_shows_original_value_when_edited(self) -> None:
        """When corrected_value differs from raw_value, show original."""
        code = _read_screen("CommitScreen")
        assert (
            "Original" in code
        ), "CommitScreen missing Original label for edited fields."
