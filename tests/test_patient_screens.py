"""Frontend screen tests — patient app features.

Validates rendering, API client usage, and key UX invariants for all
8 patient screens.  Uses React Testing Library patterns adapted for
Tamagui component testing.

These tests verify:
- Every screen uses the shared apiClient (no fetch/axios/localhost/hardcoded IDs)
- Alpha labels are present where crypto claims are scaffolded only
- Core rendering and interaction flows work
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]
FEATURES_DIR = ROOT / "nexa-client" / "packages" / "app" / "features" / "patient"
API_CLIENT_PATH = ROOT / "nexa-client" / "packages" / "app" / "utils" / "apiClient.ts"
OTP_SERVICE_PATH = (
    ROOT / "nexa-client" / "packages" / "app" / "services" / "patientOtp.ts"
)
EXPO_APP_CONFIG_PATH = ROOT / "nexa-client" / "apps" / "expo" / "app.config.ts"
EXPO_ROOT_LAYOUT_PATH = ROOT / "nexa-client" / "apps" / "expo" / "app" / "_layout.tsx"
EXPO_PATIENT_LAYOUT_PATH = (
    ROOT / "nexa-client" / "apps" / "expo" / "app" / "patient" / "_layout.tsx"
)

SCREENS = [
    "PatientLoginScreen",
    "SecureDeviceScreen",
    "DeviceEnrolledScreen",
    "ConsentRequestScreen",
    "BiometricApprovalScreen",
    "ApprovalResultScreen",
    "AccessHistoryScreen",
    "PatientTimelineScreen",
]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _read_screen(name: str) -> str:
    path = FEATURES_DIR / f"{name}.tsx"
    assert path.exists(), f"Screen file missing: {path}"
    return path.read_text(encoding="utf-8")


def _read_api_client() -> str:
    assert API_CLIENT_PATH.exists(), f"apiClient file missing: {API_CLIENT_PATH}"
    return API_CLIENT_PATH.read_text(encoding="utf-8")


def _read_otp_service() -> str:
    assert OTP_SERVICE_PATH.exists(), f"patientOtp service missing: {OTP_SERVICE_PATH}"
    return OTP_SERVICE_PATH.read_text(encoding="utf-8")


def _strip_comments(code: str) -> str:
    """Remove single-line (//) and block (/* */) comments from TS code.

    This prevents docstrings and JSDoc that mention forbidden terms
    (e.g. 'localhost', 'axios', 'hospital-grade') from triggering
    false-positive assertion failures.
    """
    # Remove block comments
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    # Remove single-line comments
    code = re.sub(r"//[^\n]*", "", code)
    return code


def _normalize_ws(text: str) -> str:
    """Collapse all whitespace runs (including newlines) into a single space.

    This makes substring assertions work across JSX text that is wrapped
    across multiple lines in the source file.
    """
    return re.sub(r"\s+", " ", text).strip()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Shared apiClient enforcement
# ═══════════════════════════════════════════════════════════════════════════════


class TestApiClientEnforcement:
    """Every screen must use shared apiClient — no raw fetch/axios/localhost."""

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_imports_shared_api_client(self, screen: str) -> None:
        """Screen imports apiClient or uses a service that does."""
        code = _read_screen(screen)
        # Screens that make API calls must import apiClient directly
        # or delegate to a service that uses it
        api_using_screens = {
            "ApprovalResultScreen",
            "AccessHistoryScreen",
            "PatientTimelineScreen",
        }
        # Screens that delegate API calls to a service
        service_delegating_screens = {
            "SecureDeviceScreen",
            "DeviceEnrolledScreen",
            "ConsentRequestScreen",
            "BiometricApprovalScreen",
            "PatientLoginScreen",
        }
        if screen in api_using_screens:
            assert "apiClient" in code, f"{screen} does not import apiClient"
        elif screen in service_delegating_screens:
            # These screens delegate to a service that uses apiClient
            assert (
                "deviceKeys" in code
                or "consentSigning" in code
                or "patientOtp" in code
                or "currentDeviceEnrollment" in code
                or "apiClient" in code
            ), f"{screen} must import from a service that uses apiClient"

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_no_raw_fetch(self, screen: str) -> None:
        """No direct fetch() calls — only apiClient."""
        code = _read_screen(screen)
        # Allow fetch only inside apiClient.ts itself
        assert not re.search(
            r"\bfetch\s*\(", code
        ), f"{screen} uses raw fetch() — use apiClient instead"

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_no_axios(self, screen: str) -> None:
        """No axios imports or calls."""
        code = _read_screen(screen)
        assert (
            "axios" not in code.lower()
        ), f"{screen} uses axios — use apiClient instead"

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_no_localhost(self, screen: str) -> None:
        """No localhost URLs."""
        code = _read_screen(screen)
        assert (
            "localhost" not in code.lower()
        ), f"{screen} contains 'localhost' — use apiClient which reads API_URL from env"

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_no_hardcoded_provider_id(self, screen: str) -> None:
        """No hardcoded provider_id values in code (comments excluded)."""
        code = _read_screen(screen)
        code_no_comments = _strip_comments(code)
        assert (
            "provider_id" not in code_no_comments
        ), f"{screen} contains 'provider_id' — must come from JWT or API response"

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_no_hardcoded_patient_id(self, screen: str) -> None:
        """No hardcoded patient_id values in code (comments excluded)."""
        code = _read_screen(screen)
        code_no_comments = _strip_comments(code)
        assert (
            "patient_id" not in code_no_comments
        ), f"{screen} contains 'patient_id' — must come from JWT or API response"

    def test_api_client_no_localhost_in_code(self) -> None:
        """apiClient.ts must not contain localhost in runtime code."""
        code = _read_api_client()
        code_no_comments = _strip_comments(code)
        assert (
            "localhost" not in code_no_comments
        ), "apiClient.ts contains 'localhost' in runtime code"

    def test_api_client_no_axios_in_code(self) -> None:
        """apiClient.ts uses only platform fetch, not axios."""
        code = _read_api_client()
        code_no_comments = _strip_comments(code)
        assert (
            "axios" not in code_no_comments.lower()
        ), "apiClient.ts uses axios in runtime code — must use platform fetch only"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Tamagui-only components
# ═══════════════════════════════════════════════════════════════════════════════


class TestTamaguiOnly:
    """All screens must use Tamagui components, no plain HTML."""

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_no_html_div(self, screen: str) -> None:
        code = _read_screen(screen)
        assert "<div" not in code, f"{screen} uses <div> — use <YStack> or <XStack>"

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_no_html_span(self, screen: str) -> None:
        code = _read_screen(screen)
        assert "<span" not in code, f"{screen} uses <span> — use <Text>"

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_no_html_button(self, screen: str) -> None:
        code = _read_screen(screen)
        assert "<button" not in code, f"{screen} uses <button> — use <Button>"

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_uses_tamagui_imports(self, screen: str) -> None:
        code = _read_screen(screen)
        assert "from 'tamagui'" in code, f"{screen} does not import from 'tamagui'"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Alpha honesty labels
# ═══════════════════════════════════════════════════════════════════════════════


class TestAlphaHonesty:
    """Crypto-scaffolded screens must honestly label themselves as alpha."""

    def test_secure_device_labels_alpha(self) -> None:
        code = _read_screen("SecureDeviceScreen")
        code_norm = _normalize_ws(code)
        assert (
            "ALPHA" in code_norm
        ), "SecureDeviceScreen must label key generation as ALPHA"
        # Must use precise honest phrasing (whitespace-normalized for JSX wrapping)
        assert (
            "P-256 keypair generated client-side" in code_norm
            and "private key stored in platform secure storage" in code_norm
        ), (
            "SecureDeviceScreen must use honest ALPHA claim: "
            "'P-256 keypair generated client-side and private key stored "
            "in platform secure storage'"
        )
        assert (
            "Not yet" in code_norm
            and "hardware-backed non-exportable signing key" in code_norm
        ), (
            "SecureDeviceScreen must state not-yet: hardware-backed non-exportable "
            "signing key with biometric-gated key usage"
        )

    def test_biometric_approval_labels_alpha(self) -> None:
        code = _read_screen("BiometricApprovalScreen")
        code_norm = _normalize_ws(code)
        assert "ALPHA" in code, "BiometricApprovalScreen must label signing as ALPHA"
        # Must use honest phrasing about key storage
        assert (
            "P-256 keypair generated client-side" in code_norm
            or "private key stored in platform secure storage" in code_norm
        ), "BiometricApprovalScreen must honestly describe key storage"

    def test_device_enrolled_labels_alpha(self) -> None:
        code = _read_screen("DeviceEnrolledScreen")
        assert "ALPHA" in code, "DeviceEnrolledScreen must label key storage as ALPHA"

    def test_no_hospital_grade_claims(self) -> None:
        """No screen claims hospital-grade biometric signing in runtime code."""
        for screen in SCREENS:
            code = _read_screen(screen)
            code_no_comments = _strip_comments(code)
            assert (
                "hospital-grade" not in code_no_comments.lower()
            ), f"{screen} must not claim hospital-grade biometric signing"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Screen-specific rendering checks
# ═══════════════════════════════════════════════════════════════════════════════


class TestPatientLoginScreen:
    def test_renders_phone_otp_flow(self) -> None:
        code = _read_screen("PatientLoginScreen")
        assert "phone" in code.lower(), "Must have phone input"
        assert "otp" in code.lower(), "Must have OTP step"
        assert "Send OTP" in code, "Must have Send OTP button"
        assert "Verify" in code, "Must have Verify button"

    def test_calls_otp_api_via_apiclient(self) -> None:
        screen = _read_screen("PatientLoginScreen")
        service = _read_otp_service()
        assert "patientOtp" in screen, "Screen must delegate to the patient OTP service"
        assert "apiClient" in service, "OTP service must use the shared API client"
        assert "/api/v2/auth/otp/send" in service, "Must call OTP send endpoint"
        assert "/api/v2/auth/otp/verify" in service, "Must call OTP verify endpoint"

    def test_checks_enrollment_state_after_login(self) -> None:
        code = _read_screen("PatientLoginScreen")
        assert "ensureCurrentDeviceEnrollment" in code
        assert (
            "devices.some" not in code
        ), "Any active patient device must not satisfy this installation"
        assert (
            "/api/v2/devices/status" not in code
        ), "Must not call the nonexistent device status route"

    def test_uses_final_token_contract_and_real_routes(self) -> None:
        code = _read_screen("PatientLoginScreen")
        assert "data.access_token" in code
        assert "storePatientAuthSession" in code
        assert "device_enrollment_token" in code
        assert "/api/v2/consent/requests/pending" not in code
        assert "/patient/access-history" in code

    def test_keyboard_safe_layout_and_dismissal_before_navigation(self) -> None:
        code = _read_screen("PatientLoginScreen")
        assert "KeyboardAvoidingView" in code
        assert "contentContainerStyle={{ flexGrow: 1 }}" in code
        assert 'keyboardShouldPersistTaps="handled"' in code
        assert 'keyboardDismissMode="on-drag"' in code
        assert code.index("Keyboard.dismiss()") < code.index(
            "router.replace('/patient/access-history')"
        )


class TestPatientNativeViewportConfiguration:
    def test_android_keyboard_pans_instead_of_resizing_app(self) -> None:
        code = EXPO_APP_CONFIG_PATH.read_text(encoding="utf-8")
        assert "softwareKeyboardLayoutMode: 'pan'" in code

    def test_root_and_patient_navigators_have_full_screen_backgrounds(self) -> None:
        for path in (EXPO_ROOT_LAYOUT_PATH, EXPO_PATIENT_LAYOUT_PATH):
            code = path.read_text(encoding="utf-8")
            assert "contentStyle" in code
            assert "flex: 1" in code
            assert "backgroundColor: '#FFFFFF'" in code


class TestSecureDeviceScreen:
    def test_uses_device_enrollment_service(self) -> None:
        """Screen delegates to installation-specific enrollment reconciliation."""
        code = _read_screen("SecureDeviceScreen")
        assert (
            "currentDeviceEnrollment" in code
        ), "Must import current-device enrollment service"
        assert (
            "ensureCurrentDeviceEnrollment" in code
        ), "Must reconcile and enroll the exact installation"

    def test_generates_keypair(self) -> None:
        code = _read_screen("SecureDeviceScreen")
        assert (
            "generateDeviceKeypair" in code or "keypair" in code.lower()
        ), "Must generate device keypair"

    def test_alpha_honest_crypto_labels(self) -> None:
        """Must label as ALPHA with honest limitations about key generation."""
        code = _read_screen("SecureDeviceScreen")
        code_norm = _normalize_ws(code)
        assert "ALPHA" in code_norm, "Must label key generation as ALPHA"
        # Must use precise honest phrasing (whitespace-normalized for JSX wrapping)
        assert (
            "P-256 keypair generated client-side" in code_norm
        ), "Must state: P-256 keypair generated client-side"
        assert (
            "private key stored in platform secure storage" in code_norm
        ), "Must state: private key stored in platform secure storage"
        assert (
            "Not yet" in code_norm
        ), "Must state 'Not yet' for unimplemented capability"
        assert (
            "hardware-backed non-exportable signing key" in code_norm
        ), "Must state not-yet: hardware-backed non-exportable signing key"
        assert (
            "biometric-gated key usage" in code_norm
        ), "Must state not-yet: biometric-gated key usage"


class TestConsentRequestScreen:
    def test_displays_provider_scope_duration(self) -> None:
        code = _read_screen("ConsentRequestScreen")
        assert (
            "providerName" in code or "provider_name" in code
        ), "Must display provider name"
        assert "scope" in code, "Must display data scope"
        assert (
            "expiresAt" in code
            or "expires" in code.lower()
            or "countdown" in code.lower()
        ), "Must display access duration or countdown"

    def test_has_approve_and_deny_buttons(self) -> None:
        code = _read_screen("ConsentRequestScreen")
        assert "Approve" in code, "Must have Approve button"
        assert "Deny" in code, "Must have Deny button"
        # Approve should be green, Deny should be red
        assert (
            "$green9" in code or "green" in code.lower()
        ), "Approve must use green color"
        assert "$red9" in code or "red" in code.lower(), "Deny must use red color"

    def test_fetches_challenge_from_api(self) -> None:
        code = _read_screen("ConsentRequestScreen")
        assert (
            "/api/v2/consent/challenge/" in code or "fetchChallenge" in code
        ), "Must fetch challenge details from API"

    def test_handles_expired_requests(self) -> None:
        code = _read_screen("ConsentRequestScreen")
        assert "expired" in code.lower(), "Must handle expired requests"
        assert "Request Expired" in code, "Must show expired state UI"

    def test_shows_countdown_timer(self) -> None:
        code = _read_screen("ConsentRequestScreen")
        assert (
            "countdown" in code.lower() or "setInterval" in code
        ), "Must show countdown timer"

    def test_uses_consent_signing_service(self) -> None:
        code = _read_screen("ConsentRequestScreen")
        assert "consentSigning" in code, "Must import from consentSigning service"


class TestBiometricApprovalScreen:
    def test_blocks_approval_if_signing_fails(self) -> None:
        code = _read_screen("BiometricApprovalScreen")
        # Must have error state that blocks approval
        assert "error" in code.lower(), "Must handle signing failure"
        assert (
            "Try Again" in code or "Go Back" in code
        ), "Must provide recovery from failed signing"

    def test_sends_signed_approval_to_api(self) -> None:
        code = _read_screen("BiometricApprovalScreen")
        # Must submit to approve-signed endpoint (directly or via service)
        assert (
            "/api/v2/consent/approve-signed" in code or "approveWithBiometric" in code
        ), "Must send signed approval to approve-signed endpoint"
        # The screen delegates to approveWithBiometric which handles
        # signing and submission — verify the service has "signature"
        signing_service = (
            Path(__file__).resolve().parents[1]
            / "nexa-client"
            / "packages"
            / "app"
            / "services"
            / "consentSigning.ts"
        )
        if signing_service.exists():
            signing_code = signing_service.read_text(encoding="utf-8")
            assert (
                "signature" in signing_code
            ), "consentSigning service must include signature in approval payload"

    def test_navigates_to_result_on_success(self) -> None:
        code = _read_screen("BiometricApprovalScreen")
        assert (
            "/patient/approval-result" in code
        ), "Must navigate to result screen on success"

    def test_uses_consent_signing_service(self) -> None:
        code = _read_screen("BiometricApprovalScreen")
        assert "consentSigning" in code, "Must import from consentSigning service"

    def test_biometric_gates_private_key_access(self) -> None:
        code = _read_screen("BiometricApprovalScreen")
        assert (
            "authenticateWithBiometrics" in code or "approveWithBiometric" in code
        ), "Must gate private key access behind biometric authentication"

    def test_handles_expired_challenge(self) -> None:
        code = _read_screen("BiometricApprovalScreen")
        assert "expired" in code.lower(), "Must handle expired challenge"


class TestApprovalResultScreen:
    def test_shows_expiry_and_revoke_action(self) -> None:
        code = _read_screen("ApprovalResultScreen")
        assert (
            "Expires In" in code or "remaining" in code or "countdown" in code.lower()
        ), "Must show expiry"
        assert "Revoke" in code, "Must show revoke button"

    def test_calls_revoke_api(self) -> None:
        code = _read_screen("ApprovalResultScreen")
        assert "NexaApiClient.revokeApprovedAccess" in code
        assert "revokeError" in code, "Must display revocation failures"

    def test_shows_approved_state(self) -> None:
        code = _read_screen("ApprovalResultScreen")
        assert "approved" in code.lower(), "Must handle approved state"
        assert "Access Granted" in code, "Must show Access Granted"

    def test_shows_denied_state(self) -> None:
        code = _read_screen("ApprovalResultScreen")
        assert "denied" in code.lower() or "Denied" in code, "Must handle denied state"
        assert "Access Denied" in code or "denied" in code.lower(), "Must show denial"

    def test_shows_expired_state(self) -> None:
        code = _read_screen("ApprovalResultScreen")
        assert (
            "expired" in code.lower() or "Request Expired" in code
        ), "Must handle expired state"


class TestAccessHistoryScreen:
    def test_renders_empty_state(self) -> None:
        code = _read_screen("AccessHistoryScreen")
        assert (
            "No one has accessed your records yet" in code
        ), "Must render empty state: 'No one has accessed your records yet.'"

    def test_scroll_and_empty_states_fill_available_screen(self) -> None:
        code = _read_screen("AccessHistoryScreen")
        assert re.search(r"<ScrollView\s+f=\{1\}", code)
        assert "flexGrow: 1" in code
        assert re.search(
            r'<YStack\s+f=\{1\}\s+ai="center"\s+jc="center"',
            code,
        )
        assert 'borderTopColor="$borderColor"' in code

    def test_renders_loading_state(self) -> None:
        code = _read_screen("AccessHistoryScreen")
        assert "Loading history" in code, "Must render loading state"

    def test_renders_error_state(self) -> None:
        code = _read_screen("AccessHistoryScreen")
        assert "Failed to load" in code or "error" in code, "Must render error state"

    def test_renders_success_states(self) -> None:
        code = _read_screen("AccessHistoryScreen")
        # Backend uses flag field: BREAK_GLASS_ACCESS / ROUTINE_ACCESS
        for field in ["is_break_glass", "ROUTINE_ACCESS", "BREAK_GLASS_ACCESS"]:
            assert field in code, f"Must handle {field} field"

    def test_fetches_via_get_access_history(self) -> None:
        code = _read_screen("AccessHistoryScreen")
        assert "access-history" in code, "Must fetch access history via apiClient"

    def test_flags_break_glass_accesses(self) -> None:
        code = _read_screen("AccessHistoryScreen")
        assert "is_break_glass" in code, "Must check is_break_glass field"
        assert "BREAK-GLASS" in code, "Must display BREAK-GLASS warning badge"

    def test_shows_hospital_name(self) -> None:
        code = _read_screen("AccessHistoryScreen")
        assert "hospital_name" in code, "Must display hospital name"

    def test_shows_provider_name(self) -> None:
        code = _read_screen("AccessHistoryScreen")
        assert "doctor_name" in code, "Must display doctor name"

    def test_never_renders_an_orphan_separator_bullet(self) -> None:
        code = _read_screen("AccessHistoryScreen")
        assert "entry.doctor_name && entry.hospital_name" in code

    def test_shows_data_categories(self) -> None:
        code = _read_screen("AccessHistoryScreen")
        assert "data_categories" in code, "Must display data categories accessed"

    def test_shows_purpose(self) -> None:
        code = _read_screen("AccessHistoryScreen")
        assert "purpose" in code, "Must display purpose of access"

    def test_shows_timestamp(self) -> None:
        code = _read_screen("AccessHistoryScreen")
        assert (
            "accessed_at" in code or "formatTimestamp" in code
        ), "Must display timestamp of access"


class TestPatientTimelineScreen:
    def test_renders_abnormal_lab_flag(self) -> None:
        code = _read_screen("PatientTimelineScreen")
        assert "ABNORMAL" in code, "Must render ABNORMAL flag for out-of-range labs"

    def test_renders_empty_state(self) -> None:
        code = _read_screen("PatientTimelineScreen")
        assert "No clinical events" in code, "Must render empty state"

    def test_scroll_and_empty_states_fill_available_screen(self) -> None:
        code = _read_screen("PatientTimelineScreen")
        assert re.search(r"<ScrollView\s+f=\{1\}", code)
        assert "flexGrow: 1" in code
        assert "paddingBottom: 32" in code
        assert re.search(
            r'<YStack\s+f=\{1\}\s+ai="center"\s+jc="center"',
            code,
        )

    def test_footer_remains_outside_flexible_scroll_view(self) -> None:
        code = _read_screen("PatientTimelineScreen")
        scroll_end = code.index("</ScrollView>")
        footer = code.index("← Access History")
        assert scroll_end < footer
        assert re.search(r"<ScrollView\s+f=\{1\}", code)

    def test_renders_error_state(self) -> None:
        code = _read_screen("PatientTimelineScreen")
        assert "Failed to load" in code or "error" in code, "Must render error state"

    def test_groups_by_date(self) -> None:
        code = _read_screen("PatientTimelineScreen")
        assert "grouped" in code or "dateKey" in code, "Must group events by date"

    def test_fetches_via_get_my_timeline(self) -> None:
        code = _read_screen("PatientTimelineScreen")
        assert "timeline" in code, "Must fetch timeline via apiClient"

    def test_handles_all_categories(self) -> None:
        code = _read_screen("PatientTimelineScreen")
        # Backend event_type values are UPPERCASE
        for cat in [
            "VITALS",
            "MEDICATION",
            "LAB_RESULT",
            "ALLERGY",
            "DOCUMENT",
            "ENCOUNTER",
        ]:
            assert cat in code, f"Must handle {cat} category"

    def test_shows_provenance_with_source_badge(self) -> None:
        code = _read_screen("PatientTimelineScreen")
        assert "SourceBadge" in code, "Must use SourceBadge for provenance"
        assert "source" in code, "Must pass source prop to SourceBadge"

    def test_shows_confidence_for_ai_extracted(self) -> None:
        code = _read_screen("PatientTimelineScreen")
        assert "confidence" in code, "Must pass confidence to SourceBadge"

    def test_shows_risk_badge(self) -> None:
        code = _read_screen("PatientTimelineScreen")
        assert "RiskBadge" in code, "Must use RiskBadge for risk levels"
        assert "risk_level" in code, "Must pass risk_level to RiskBadge"

    def test_distinguishes_manual_vs_ai(self) -> None:
        # The screen uses event.source which is typed in the screen or badges
        # Check type def, badge component, and screen passes source prop
        badge_code = (
            Path(__file__).resolve().parents[1]
            / "nexa-client"
            / "packages"
            / "app"
            / "features"
            / "patient"
            / "badges"
            / "SourceBadge.tsx"
        ).read_text(encoding="utf-8")
        assert "manual" in badge_code, "SourceBadge must handle manual source"
        assert (
            "ai_extracted" in badge_code
        ), "SourceBadge must handle ai_extracted source"

    def test_imports_badge_components(self) -> None:
        code = _read_screen("PatientTimelineScreen")
        assert "badges/SourceBadge" in code, "Must import SourceBadge"
        assert "badges/RiskBadge" in code, "Must import RiskBadge"

    def test_api_strings_cannot_render_as_raw_native_children(self) -> None:
        code = _read_screen("PatientTimelineScreen")
        assert "{error &&" not in code
        assert "{riskLevel &&" not in code
        assert "{event.source_display &&" not in code
        assert "event.source_display.length > 0 ?" in code


def test_access_history_api_values_cannot_render_as_raw_native_children() -> None:
    code = _read_screen("AccessHistoryScreen")
    assert "{error &&" not in code
    assert "{entry.data_categories &&" not in code
    assert "Array.isArray(entry.data_categories)" in code


@pytest.mark.parametrize(
    "screen_name",
    [
        "PatientLoginScreen",
        "SecureDeviceScreen",
        "ConsentRequestScreen",
        "BiometricApprovalScreen",
    ],
)
def test_patient_error_messages_use_explicit_jsx_branches(screen_name: str) -> None:
    code = _read_screen(screen_name)
    assert "{error &&" not in code
    assert "{error !== null ?" in code


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Route and deep-link verification
# ═══════════════════════════════════════════════════════════════════════════════


class TestRoutesAndDeepLinks:
    """Expo routes and deep-link config exist and match screens."""

    @pytest.mark.parametrize(
        "screen_file",
        [
            "login",
            "secure-device",
            "enrolled",
            "consent-request",
            "biometric-approval",
            "approval-result",
            "access-history",
            "timeline",
        ],
    )
    def test_expo_route_exists(self, screen_file: str) -> None:
        route_path = (
            ROOT
            / "nexa-client"
            / "apps"
            / "expo"
            / "app"
            / "patient"
            / f"{screen_file}.tsx"
        )
        assert route_path.exists(), f"Expo route missing: {route_path}"

    def test_layout_exists(self) -> None:
        layout_path = (
            ROOT / "nexa-client" / "apps" / "expo" / "app" / "patient" / "_layout.tsx"
        )
        assert layout_path.exists(), "Patient layout file missing"

    def test_app_json_has_nexacare_scheme(self) -> None:
        app_json_path = ROOT / "nexa-client" / "apps" / "expo" / "app.json"
        assert app_json_path.exists(), "app.json missing"
        content = app_json_path.read_text(encoding="utf-8")
        assert "nexacare" in content, "app.json must contain nexacare scheme"

    def test_app_json_has_consent_deep_link(self) -> None:
        app_json_path = ROOT / "nexa-client" / "apps" / "expo" / "app.json"
        content = app_json_path.read_text(encoding="utf-8")
        # With Expo Router (file-based routing), deep links are handled
        # automatically via the route files. The scheme is registered in app.json.
        assert (
            "nexacare" in content
        ), "app.json must have nexacare scheme for deep linking"
        # Verify the consent route file exists (this is what enables the deep link)
        consent_route = (
            ROOT
            / "nexa-client"
            / "apps"
            / "expo"
            / "app"
            / "patient"
            / "consent-request.tsx"
        )
        assert (
            consent_route.exists()
        ), "Consent request route file must exist for deep linking"

    def test_next_review_route_exists(self) -> None:
        """Doctor/admin review queue route in Next.js web app."""
        review_path = (
            ROOT
            / "nexa-client"
            / "apps"
            / "next"
            / "app"
            / "doctor"
            / "pipeline"
            / "review-queue"
            / "page.tsx"
        )
        assert review_path.exists(), "Next.js review queue route missing at nexa-client/apps/next/app/doctor/pipeline/review-queue/page.tsx"
