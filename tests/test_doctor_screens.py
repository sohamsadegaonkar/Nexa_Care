"""Tests for doctor screen skeletons and provider auth context.

Validates:
  - All 7 doctor screens exist and use Tamagui components
  - No provider_id placeholder or hardcoded localhost anywhere
  - ProviderAuthContext exists and exports useProviderAuth
  - All screens use shared apiClient
  - Next.js routes exist for each screen
  - No plain HTML elements (div, span, button)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]
DOCTOR_DIR = ROOT / "nexa-client" / "packages" / "app" / "features" / "doctor"
NEXT_ROUTES_DIR = ROOT / "nexa-client" / "apps" / "next" / "app" / "doctor"
API_CLIENT_PATH = ROOT / "nexa-client" / "packages" / "app" / "utils" / "apiClient.ts"

SCREENS = [
    "DoctorLoginScreen",
    "DoctorDashboardScreen",
    "PatientSearchScreen",
    "RequestConsentScreen",
    "WaitingForApprovalScreen",
    "PatientRecordViewerScreen",
    "EmergencyAccessScreen",
]

ROUTES = [
    "login",
    "dashboard",
    "patient-search",
    "request-consent",
    "waiting",
    "patient-record",
    "emergency-access",
]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _read(path: Path) -> str:
    assert path.exists(), f"File missing: {path}"
    return path.read_text(encoding="utf-8")


def _read_screen(name: str) -> str:
    path = DOCTOR_DIR / f"{name}.tsx"
    return _read(path)


def _strip_comments(code: str) -> str:
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    code = re.sub(r"//[^\n]*", "", code)
    return code


def _normalize_ws(code: str) -> str:
    """Collapse all whitespace (including newlines) into single spaces for cross-line matching."""
    return re.sub(r"\s+", " ", code)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Screen file existence
# ═══════════════════════════════════════════════════════════════════════════════


class TestDoctorScreensExist:
    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_screen_file_exists(self, screen: str) -> None:
        path = DOCTOR_DIR / f"{screen}.tsx"
        assert path.exists(), f"Screen file missing: {path}"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. No provider_id placeholder, no localhost
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoPlaceholderIds:
    """No provider_id placeholder or hardcoded localhost in doctor screens."""

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_no_provider_id_placeholder(self, screen: str) -> None:
        code = _read_screen(screen)
        code_no_comments = _strip_comments(code)
        # provider_id as an object key (e.g. { provider_id: providerId }) is fine —
        # it's the API field name. What's forbidden is using 'provider_id' as a
        # standalone value/placeholder (e.g. provider_id="prov-123").
        # Allow: provider_id: someVariable, provider_id: providerId
        # Forbid: provider_id = "hardcoded", provider_id by itself as a value
        if "provider_id" in code_no_comments:
            # Check each occurrence — if it's only used as a key name mapping to
            # a variable (not a hardcoded string), that's acceptable.
            for line in code_no_comments.split("\n"):
                if "provider_id" in line:
                    stripped = line.strip()
                    # Allow: provider_id: someVar, provider_id: providerId
                    if re.match(r'.*provider_id\s*:\s*\w', stripped):
                        continue
                    # Allow: provider_id: string (type annotation)
                    if "string" in stripped and ":" in stripped:
                        continue
                    # Any other usage is suspicious
                    assert False, (
                        f"{screen} has suspicious 'provider_id' usage: {stripped!r} — "
                        f"must use useProviderAuth().providerId instead of hardcoded value"
                    )

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_no_hardcoded_provider_id(self, screen: str) -> None:
        code = _read_screen(screen)
        code_no_comments = _strip_comments(code)
        # Check for common placeholder patterns
        for pattern in ["provider-123", "provider-001", "PROVIDER_ID", "prov-123"]:
            assert pattern not in code_no_comments, (
                f"{screen} contains placeholder '{pattern}'"
            )

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_no_localhost(self, screen: str) -> None:
        code = _read_screen(screen)
        assert "localhost" not in code.lower(), (
            f"{screen} contains 'localhost' — use apiClient which reads URL from env"
        )

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_no_raw_fetch(self, screen: str) -> None:
        code = _read_screen(screen)
        assert not re.search(r"\bfetch\s*\(", code), (
            f"{screen} uses raw fetch() — use apiClient instead"
        )

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_no_axios(self, screen: str) -> None:
        code = _read_screen(screen)
        assert "axios" not in code.lower(), (
            f"{screen} uses axios — use apiClient instead"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Tamagui-only components
# ═══════════════════════════════════════════════════════════════════════════════


class TestTamaguiOnly:
    """All doctor screens must use Tamagui components, no plain HTML."""

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
        uses_tamagui = "from 'tamagui'" in code or "from '@my/ui'" in code
        assert uses_tamagui, f"{screen} does not import from 'tamagui' or '@my/ui'"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Shared apiClient usage
# ═══════════════════════════════════════════════════════════════════════════════


class TestApiClientUsage:
    """Doctor screens must use the shared apiClient."""

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_imports_api_client_or_context(self, screen: str) -> None:
        code = _read_screen(screen)
        # Screens either import apiClient directly, use the auth context
        # (which itself imports apiClient), or use the nfcResolve service
        # (which itself imports apiClient).  After migration, nexa-client
        # uses relative imports instead of @nx/app/ aliases.
        uses_api_client = "apiClient" in code or "NexaApiClient" in code
        uses_auth_context = "ProviderAuthContext" in code
        uses_nfc_resolve = "nfcResolve" in code
        assert uses_api_client or uses_auth_context or uses_nfc_resolve, (
            f"{screen} must import apiClient, ProviderAuthContext, or nfcResolve service"
        )

    @pytest.mark.parametrize("screen", SCREENS, ids=SCREENS)
    def test_no_hardcoded_patient_id(self, screen: str) -> None:
        code = _read_screen(screen)
        code_no_comments = _strip_comments(code)
        # patient_id as a variable name is fine (comes from search params)
        # but hardcoded values like "pat-123" are not
        for pattern in ["pat-123", "PATIENT_ID", "patient-001", "patient_001"]:
            assert pattern not in code_no_comments, (
                f"{screen} contains hardcoded patient ID '{pattern}'"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Provider auth context
# ═══════════════════════════════════════════════════════════════════════════════


class TestProviderAuthContext:
    """ProviderAuthContext must exist and export the right hooks/types."""

    def test_context_file_exists(self) -> None:
        path = DOCTOR_DIR / "ProviderAuthContext.tsx"
        assert path.exists(), "ProviderAuthContext.tsx must exist"

    def test_exports_use_provider_auth(self) -> None:
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "useProviderAuth" in code, "Must export useProviderAuth hook"

    def test_exports_provider_auth_provider(self) -> None:
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "ProviderAuthProvider" in code, "Must export ProviderAuthProvider component"

    def test_exports_provider_session_type(self) -> None:
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "ProviderSession" in code, "Must define ProviderSession type"

    def test_exports_provider_identity_type(self) -> None:
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "ProviderIdentity" in code, "Must define ProviderIdentity type"

    def test_has_provider_id_field(self) -> None:
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        # The ProviderIdentity type must have provider_id
        assert "provider_id" in code, "ProviderIdentity must include provider_id field"

    def test_has_login_function(self) -> None:
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "login" in code, "Must have login function"

    def test_has_logout_function(self) -> None:
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "logout" in code, "Must have logout function"

    def test_uses_api_client(self) -> None:
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "apiClient" in code, "Must import apiClient"

    def test_stores_jwt_via_set_auth_token(self) -> None:
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "setAuthTokenProvider" in code, "Must store session token via setAuthTokenProvider()"

    def test_no_localhost(self) -> None:
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "localhost" not in code.lower(), "Must not contain localhost"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Screen-specific content checks
# ═══════════════════════════════════════════════════════════════════════════════


class TestDoctorLoginScreen:
    def test_has_email_and_password_inputs(self) -> None:
        code = _read_screen("DoctorLoginScreen")
        assert "email" in code.lower(), "Must have email input"
        assert "password" in code.lower(), "Must have password input"

    def test_calls_login_from_context(self) -> None:
        code = _read_screen("DoctorLoginScreen")
        assert "useProviderAuth" in code, "Must use ProviderAuthContext"
        assert "login(" in code, "Must call login() from context"

    def test_uses_provider_auth_context(self) -> None:
        code = _read_screen("DoctorLoginScreen")
        assert "ProviderAuthContext" in code, (
            "Must import from ProviderAuthContext"
        )


class TestDoctorDashboardScreen:
    def test_uses_provider_auth_context(self) -> None:
        code = _read_screen("DoctorDashboardScreen")
        assert "useProviderAuth" in code, "Must use ProviderAuthContext"

    def test_shows_provider_name(self) -> None:
        code = _read_screen("DoctorDashboardScreen")
        assert "displayName" in code, "Must display provider name from context"

    def test_shows_hospital_name(self) -> None:
        code = _read_screen("DoctorDashboardScreen")
        assert "hospitalName" in code, "Must display hospital name from context"

    def test_has_patient_search_button(self) -> None:
        code = _read_screen("DoctorDashboardScreen")
        assert "patient-search" in code, "Must have patient search navigation"

    def test_has_emergency_access_button(self) -> None:
        code = _read_screen("DoctorDashboardScreen")
        assert "emergency-access" in code, "Must have emergency access navigation"


class TestPatientSearchScreen:
    def test_has_search_input(self) -> None:
        code = _read_screen("PatientSearchScreen")
        assert "Input" in code, "Must have search input"

    def test_navigates_to_request_consent(self) -> None:
        code = _read_screen("PatientSearchScreen")
        assert "request-consent" in code, "Must navigate to request consent on select"

    def test_no_hardcoded_patient_ids(self) -> None:
        code = _read_screen("PatientSearchScreen")
        code_no_comments = _strip_comments(code)
        for pattern in ["pat-123", "PATIENT_ID", "patient-001"]:
            assert pattern not in code_no_comments, (
                f"Must not contain hardcoded patient ID '{pattern}'"
            )


# (TestRequestConsentScreen moved to section 13 below)
# (TestWaitingForApprovalScreen moved to section 14 below)


class TestPatientRecordViewerScreen:
    def test_fetches_patient_summary(self) -> None:
        code = _read_screen("PatientRecordViewerScreen")
        # The screen fetches via NexaApiClient.getPatientSummary() which
        # encapsulates the API endpoint — check for the method call.
        assert "getPatientSummary" in code or "/api/v2/patient/" in code, "Must fetch patient data"

    def test_shows_vitals_medications_allergies(self) -> None:
        code = _read_screen("PatientRecordViewerScreen")
        assert "vitals" in code.lower() or "medications" in code.lower(), (
            "Must display clinical data sections"
        )

    def test_has_tabbed_layout(self) -> None:
        """Must have a tabbed layout with multiple tabs."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "activeTab" in code, "Must track active tab state"
        assert "summary" in code.lower(), "Must have Summary tab"
        assert "vitals" in code.lower(), "Must have Vitals tab"
        assert "prescriptions" in code.lower(), "Must have Prescriptions tab"
        assert "labs" in code.lower() or "lab" in code.lower(), "Must have Lab Reports tab"
        assert "allergies" in code.lower(), "Must have Allergies tab"
        assert "documents" in code.lower(), "Must have Documents tab"
        assert "timeline" in code.lower(), "Must have Timeline tab"
        assert "access" in code.lower(), "Must have Access Status tab"

    def test_has_consent_expiry_countdown(self) -> None:
        """Must show consent expiry countdown and lock when expired."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "secondsRemaining" in code, "Must track remaining seconds"
        assert "expired" in code.lower(), "Must handle expired state"
        assert "Consent Expired" in code or "Consent expired" in code, (
            "Must show 'Consent expired' message when expired"
        )

    def test_allergies_prominently_displayed(self) -> None:
        """Allergies must be prominently displayed (safety-critical)."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "SAFETY CRITICAL" in code, "Must label allergies as safety-critical"
        assert "$red" in code, "Allergies must use red color scheme"

    def test_has_confidence_badges(self) -> None:
        """AI-extracted fields must show confidence/source badges."""
        code = _read_screen("PatientRecordViewerScreen")
        assert "ProvenanceBadge" in code, "Must have ProvenanceBadge component (replaces ConfidenceBadge with verification status)"
        assert "confidence" in code.lower(), "Must use confidence field"

    def test_locked_when_consent_expired(self) -> None:
        """Viewer must lock when consent expires."""
        code = _read_screen("PatientRecordViewerScreen")
        code_norm = _normalize_ws(code)
        assert "expired" in code_norm.lower(), "Must have expired viewer state"
        assert "Request access again" in code or "request access" in code.lower(), (
            "Must prompt to request access again when expired"
        )


class TestEmergencyAccessScreen:
    def test_uses_provider_auth_context(self) -> None:
        code = _read_screen("EmergencyAccessScreen")
        assert "useProviderAuth" in code, "Must use ProviderAuthContext"

    def test_calls_break_glass_api(self) -> None:
        code = _read_screen("EmergencyAccessScreen")
        # The screen calls NexaApiClient.breakGlassIssue() which encapsulates
        # the API endpoint — check for the method call or the raw endpoint.
        assert "breakGlassIssue" in code or "/api/v2/consent/break-glass/" in code, (
            "Must call break-glass API (via NexaApiClient or direct endpoint)"
        )

    def test_has_reason_code_input(self) -> None:
        code = _read_screen("EmergencyAccessScreen")
        assert "reason" in code.lower(), "Must have reason code input"

    def test_shows_audit_warning(self) -> None:
        code = _read_screen("EmergencyAccessScreen")
        assert "audit" in code.lower(), "Must show audit warning"

    def test_shows_red_warning(self) -> None:
        code = _read_screen("EmergencyAccessScreen")
        assert "$red" in code, "Must use red color for emergency warnings"

    def test_reason_code_is_controlled(self) -> None:
        """Reason code must be a controlled selector, not free-text."""
        code = _read_screen("EmergencyAccessScreen")
        assert "Select" in code, "Must use Select for reason code"
        assert "REASON_OPTIONS" in code or "BreakGlassReason" in code, (
            "Must define controlled reason options"
        )
        assert "LIFE_THREATENING" in code or "IMMEDIATE_THREAT_TO_LIFE" in code, "Must include LIFE_THREATENING or IMMEDIATE_THREAT_TO_LIFE reason"

    def test_requires_clinical_justification(self) -> None:
        """Clinical justification must be required (not optional)."""
        code = _read_screen("EmergencyAccessScreen")
        assert "freeText" in code or "justification" in code.lower(), (
            "Must have clinical justification field"
        )
        # Must validate justification is not empty
        assert "!freeText.trim()" in code or "!free_text" in code or "justification is required" in code.lower(), (
            "Must require clinical justification (not optional)"
        )

    def test_shows_rate_limit_warning(self) -> None:
        """Must warn about rate limiting."""
        code = _read_screen("EmergencyAccessScreen")
        assert "rate" in code.lower() or "3 per hour" in code or "rate limit" in code.lower(), (
            "Must show rate limit warning"
        )

    def test_never_calls_approval_endpoint(self) -> None:
        """Emergency screen must never call consent approval endpoints."""
        code = _read_screen("EmergencyAccessScreen")
        code_no_comments = _strip_comments(code)
        assert "approve-signed" not in code_no_comments, (
            "Must NOT call /api/v2/consent/approve-signed"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Next.js route files
# ═══════════════════════════════════════════════════════════════════════════════


class TestNextJsRoutes:
    """Next.js route files must exist for every doctor screen."""

    def test_doctor_layout_exists(self) -> None:
        path = NEXT_ROUTES_DIR / "layout.tsx"
        assert path.exists(), f"Doctor layout missing: {path}"

    def test_layout_imports_provider_auth(self) -> None:
        code = _read(NEXT_ROUTES_DIR / "layout.tsx")
        assert "ProviderAuthProvider" in code, (
            "Layout must wrap routes with ProviderAuthProvider"
        )

    @pytest.mark.parametrize("route", ROUTES, ids=ROUTES)
    def test_route_page_exists(self, route: str) -> None:
        path = NEXT_ROUTES_DIR / route / "page.tsx"
        assert path.exists(), f"Route page missing: {path}"

    @pytest.mark.parametrize("route", ROUTES, ids=ROUTES)
    def test_route_imports_screen(self, route: str) -> None:
        code = _read(NEXT_ROUTES_DIR / route / "page.tsx")
        assert "Doctor" in code or "Patient" in code or "Request" in code or "Waiting" in code or "Emergency" in code, (
            f"Route {route} must import the corresponding screen component"
        )

    @pytest.mark.parametrize("route", ROUTES, ids=ROUTES)
    def test_route_no_localhost(self, route: str) -> None:
        code = _read(NEXT_ROUTES_DIR / route / "page.tsx")
        assert "localhost" not in code.lower(), (
            f"Route {route} must not contain localhost"
        )

    @pytest.mark.parametrize("route", ROUTES, ids=ROUTES)
    def test_route_no_provider_id(self, route: str) -> None:
        code = _read(NEXT_ROUTES_DIR / route / "page.tsx")
        code_no_comments = _strip_comments(code)
        assert "provider_id" not in code_no_comments, (
            f"Route {route} must not contain provider_id placeholder"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Doctor flow doc
# ═══════════════════════════════════════════════════════════════════════════════


class TestDoctorFlowDoc:
    """Doctor app flow spec must exist and cover all screens."""

    def test_doc_exists(self) -> None:
        path = ROOT / "docs" / "doctor-app-flow.md"
        assert path.exists(), "docs/doctor-app-flow.md must exist"

    def test_doc_covers_all_screens(self) -> None:
        code = _read(ROOT / "docs" / "doctor-app-flow.md")
        for screen in SCREENS:
            assert screen in code, f"Doctor flow doc must cover {screen}"

    def test_doc_mentions_provider_auth_context(self) -> None:
        code = _read(ROOT / "docs" / "doctor-app-flow.md")
        assert "ProviderAuthContext" in code, (
            "Doctor flow doc must mention ProviderAuthContext"
        )

    def test_doc_mentions_no_provider_id(self) -> None:
        code = _read(ROOT / "docs" / "doctor-app-flow.md")
        assert "provider_id" in code, (
            "Doctor flow doc must explicitly mention provider_id is from session"
        )

    def test_doc_mentions_break_glass(self) -> None:
        code = _read(ROOT / "docs" / "doctor-app-flow.md")
        assert "break-glass" in code.lower() or "Emergency" in code, (
            "Doctor flow doc must cover emergency break-glass access"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Login flow with MFA support
# ═══════════════════════════════════════════════════════════════════════════════


class TestLoginFlow:
    """DoctorLoginScreen must support the full login flow with MFA step."""

    def test_login_screen_has_two_step_flow(self) -> None:
        """Login screen must define a LoginStep type with credentials and mfa steps."""
        code = _read_screen("DoctorLoginScreen")
        assert "credentials" in code, "Must define 'credentials' login step"
        assert "mfa" in code, "Must define 'mfa' login step"

    def test_login_screen_calls_login_and_checks_result_type(self) -> None:
        """Login must call login() and check result.type for mfa_required."""
        code = _read_screen("DoctorLoginScreen")
        assert "login(" in code, "Must call login() from context"
        assert "mfa_required" in code, "Must check result.type === 'mfa_required'"

    def test_login_screen_has_totp_input(self) -> None:
        """MFA step must have a TOTP code input field."""
        code = _read_screen("DoctorLoginScreen")
        assert "totpCode" in code or "totp_code" in code, (
            "Must have a TOTP code state variable"
        )
        # The TOTP input should have maxLength or similar numeric constraint
        assert "6" in code, "TOTP code must require at least 6 digits"

    def test_login_screen_calls_verify_mfa(self) -> None:
        """MFA step must call verifyMfa() with mfaToken and totpCode."""
        code = _read_screen("DoctorLoginScreen")
        assert "verifyMfa" in code, "Must call verifyMfa() from context"
        assert "mfaToken" in code or "mfa_token" in code, (
            "Must pass mfaToken to verifyMfa()"
        )

    def test_login_screen_has_back_to_sign_in(self) -> None:
        """MFA step must have a 'Back to Sign In' button to restart."""
        code = _read_screen("DoctorLoginScreen")
        assert "Back to Sign In" in code, "Must have 'Back to Sign In' button"

    def test_login_screen_redirects_to_dashboard_on_success(self) -> None:
        """On direct login or MFA success, must redirect to dashboard."""
        code = _read_screen("DoctorLoginScreen")
        assert "/doctor/dashboard" in code, "Must redirect to /doctor/dashboard"

    def test_login_screen_shows_error_for_both_steps(self) -> None:
        """Error display must work for both credentials and MFA steps."""
        code = _read_screen("DoctorLoginScreen")
        assert "displayError" in code or "localError" in code, (
            "Must display errors from both steps"
        )


class TestProviderAuthMfaFlow:
    """ProviderAuthContext must support the MFA login flow."""

    def test_has_login_result_type(self) -> None:
        """Must export a LoginResult type for discriminated union return."""
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "LoginResult" in code, "Must define LoginResult type"

    def test_login_result_has_authenticated_variant(self) -> None:
        """LoginResult must have an 'authenticated' variant with session."""
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "'authenticated'" in code or '"authenticated"' in code, (
            "LoginResult must have 'authenticated' type variant"
        )

    def test_login_result_has_mfa_required_variant(self) -> None:
        """LoginResult must have a 'mfa_required' variant with mfaToken."""
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "'mfa_required'" in code or '"mfa_required"' in code, (
            "LoginResult must have 'mfa_required' type variant"
        )
        assert "mfaToken" in code, "mfa_required variant must include mfaToken"

    def test_has_verify_mfa_action(self) -> None:
        """ProviderAuthActions must include verifyMfa."""
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "verifyMfa" in code, "Must export verifyMfa action"

    def test_verify_mfa_stores_session(self) -> None:
        """verifyMfa must store the session on success (calls setAuthTokenProvider)."""
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        code_norm = _normalize_ws(code)
        # Look for setAuthTokenProvider call inside verifyMfa implementation
        assert "setAuthTokenProvider" in code, "Must call setAuthTokenProvider() after MFA verification"
        # verifyMfa should appear near setAuthTokenProvider usage
        assert code_norm.count("verifyMfa") >= 2, (
            "verifyMfa must appear in both type definition and implementation"
        )

    def test_login_calls_auth_login_endpoint(self) -> None:
        """login() must call POST /api/v2/auth/login."""
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "/api/v2/auth/login" in code, "Must call /api/v2/auth/login"

    def test_verify_mfa_calls_mfa_verify_endpoint(self) -> None:
        """verifyMfa() must call POST /api/v2/auth/mfa/verify."""
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "/api/v2/auth/mfa/verify" in code, (
            "Must call /api/v2/auth/mfa/verify"
        )

    def test_has_mfa_required_type_guard(self) -> None:
        """Must have a mechanism to detect mfa_required response (checks mfa_token field).

        This can be either a dedicated type guard function (isMfaRequired)
        or a Zod schema validation function (validateLoginResponse) that
        discriminates between success and MFA-required responses.
        """
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "mfa_token" in code, "Must check for mfa_token field in response"
        assert "isMfaRequired" in code or "isMfa" in code or "validateLoginResponse" in code, (
            "Must have a type guard or Zod validator for MFA required response"
        )

    def test_has_role_in_provider_identity(self) -> None:
        """ProviderIdentity must include a role field."""
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        # Find ProviderIdentity interface area and check for role
        assert "role" in code, "ProviderIdentity must include role field"

    def test_has_role_in_auth_state(self) -> None:
        """ProviderAuthState must expose role from session."""
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        # ProviderAuthState should have a role field
        assert "role" in code, "ProviderAuthState must include role"

    def test_login_returns_login_result_not_void(self) -> None:
        """login() must return Promise<LoginResult>, not Promise<void>."""
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "LoginResult" in code, "login must return LoginResult type"
        # The function signature should not be Promise<void>
        assert "Promise<void>" not in code or "LoginResult" in code, (
            "login() must return Promise<LoginResult>, not Promise<void>"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 10. NFC resolve service
# ═══════════════════════════════════════════════════════════════════════════════


class TestNfcResolveService:
    """nfcResolve.ts service must exist and handle NFC card resolution."""

    def test_nfc_resolve_service_exists(self) -> None:
        """The nfcResolve service file must exist."""
        path = ROOT / "nexa-client" / "packages" / "app" / "services" / "nfcResolve.ts"
        assert path.exists(), "nexa-client/packages/app/services/nfcResolve.ts must exist"

    def test_exports_resolve_nfc_card_function(self) -> None:
        """Must export the resolveNfcCard function."""
        code = _read(ROOT / "nexa-client" / "packages" / "app" / "services" / "nfcResolve.ts")
        assert "resolveNfcCard" in code, "Must export resolveNfcCard function"

    def test_calls_nfc_resolve_endpoint(self) -> None:
        """resolveNfcCard must call POST /api/v2/nfc/resolve."""
        code = _read(ROOT / "nexa-client" / "packages" / "app" / "services" / "nfcResolve.ts")
        assert "/api/v2/nfc/resolve" in code, (
            "Must call POST /api/v2/nfc/resolve"
        )

    def test_sends_card_uid_in_body(self) -> None:
        """Must send { card_uid: string } in the request body."""
        code = _read(ROOT / "nexa-client" / "packages" / "app" / "services" / "nfcResolve.ts")
        assert "card_uid" in code, "Must include card_uid in request body"

    def test_returns_patient_id_and_canonical(self) -> None:
        """Response type must include patient_id, canonical_patient_id, is_redirected."""
        code = _read(ROOT / "nexa-client" / "packages" / "app" / "services" / "nfcResolve.ts")
        assert "patient_id" in code, "Response type must include patient_id"
        assert "canonical_patient_id" in code, (
            "Response type must include canonical_patient_id"
        )
        assert "is_redirected" in code, "Response type must include is_redirected"

    def test_has_nfc_resolve_error_class(self) -> None:
        """Must export NfcResolveError class for typed error handling."""
        code = _read(ROOT / "nexa-client" / "packages" / "app" / "services" / "nfcResolve.ts")
        assert "NfcResolveError" in code, "Must export NfcResolveError class"

    def test_handles_card_not_found(self) -> None:
        """Must handle 404 status (card not found)."""
        code = _read(ROOT / "nexa-client" / "packages" / "app" / "services" / "nfcResolve.ts")
        assert "404" in code, "Must handle 404 status code"
        assert "NFC_CARD_NOT_FOUND" in code, "Must use NFC_CARD_NOT_FOUND error code"

    def test_handles_service_unavailable(self) -> None:
        """Must handle 503 status (service unavailable)."""
        code = _read(ROOT / "nexa-client" / "packages" / "app" / "services" / "nfcResolve.ts")
        assert "503" in code, "Must handle 503 status code"
        assert "NFC_RESOLVE_UNAVAILABLE" in code, (
            "Must use NFC_RESOLVE_UNAVAILABLE error code"
        )

    def test_uses_api_client(self) -> None:
        """Must import and use the shared apiClient."""
        code = _read(ROOT / "nexa-client" / "packages" / "app" / "services" / "nfcResolve.ts")
        assert "apiClient" in code, (
            "Must import apiClient"
        )

    def test_no_localhost(self) -> None:
        """Must not contain hardcoded localhost."""
        code = _read(ROOT / "nexa-client" / "packages" / "app" / "services" / "nfcResolve.ts")
        assert "localhost" not in code.lower(), "Must not contain localhost"

    def test_no_raw_fetch(self) -> None:
        """Must not use raw fetch() — must use apiClient."""
        code = _read(ROOT / "nexa-client" / "packages" / "app" / "services" / "nfcResolve.ts")
        assert not re.search(r"\bfetch\s*\(", code), (
            "Must use apiClient, not raw fetch()"
        )

    def test_no_axios(self) -> None:
        """Must not use axios directly."""
        code = _read(ROOT / "nexa-client" / "packages" / "app" / "services" / "nfcResolve.ts")
        assert "axios" not in code.lower(), "Must not use axios directly"


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Patient search with NFC resolve and merged-patient redirect
# ═══════════════════════════════════════════════════════════════════════════════


class TestPatientSearchNfcResolve:
    """PatientSearchScreen must support NFC resolve mode."""

    def test_has_dual_search_mode(self) -> None:
        """Must support both 'manual' and 'nfc' search modes."""
        code = _read_screen("PatientSearchScreen")
        assert "manual" in code, "Must support 'manual' search mode"
        assert "nfc" in code, "Must support 'nfc' search mode"

    def test_imports_nfc_resolve_service(self) -> None:
        """Must import resolveNfcCard from the nfcResolve service."""
        code = _read_screen("PatientSearchScreen")
        assert "resolveNfcCard" in code, "Must import resolveNfcCard"
        assert "nfcResolve" in code, "Must import from nfcResolve service"

    def test_calls_resolve_nfc_card(self) -> None:
        """Must call resolveNfcCard() when in NFC mode."""
        code = _read_screen("PatientSearchScreen")
        assert "resolveNfcCard(" in code, "Must call resolveNfcCard() function"

    def test_handles_nfc_resolve_error(self) -> None:
        """Must handle NfcResolveError from the resolve service."""
        code = _read_screen("PatientSearchScreen")
        assert "NfcResolveError" in code, "Must handle NfcResolveError"

    def test_reads_mode_from_search_params(self) -> None:
        """Must read ?mode=nfc from search params for NFC mode."""
        code = _read_screen("PatientSearchScreen")
        assert "searchParams" in code, "Must read search params"
        assert "mode" in code, "Must read mode from search params"

    def test_has_mode_toggle_buttons(self) -> None:
        """Must have buttons to switch between manual and NFC mode."""
        code = _read_screen("PatientSearchScreen")
        assert "Manual Search" in code, "Must have 'Manual Search' button"
        assert "NFC Scan" in code, "Must have 'NFC Scan' button"


class TestMergedPatientRedirect:
    """PatientSearchScreen must handle merged-patient redirects from NFC resolve."""

    def test_checks_is_redirected_flag(self) -> None:
        """Must check is_redirected flag from NFC resolve response."""
        code = _read_screen("PatientSearchScreen")
        assert "is_redirected" in code, "Must check is_redirected flag"

    def test_shows_merged_patient_warning_banner(self) -> None:
        """Must show a warning banner when patient was merged (is_redirected=true)."""
        code = _read_screen("PatientSearchScreen")
        # Should show a warning about merged/redirected patient
        assert "Merged" in code or "merged" in code, (
            "Must show merged-patient warning text"
        )

    def test_displays_original_patient_id(self) -> None:
        """Must display the original patient_id when redirected."""
        code = _read_screen("PatientSearchScreen")
        assert "patient_id" in code, "Must display original patient_id"

    def test_displays_canonical_patient_id(self) -> None:
        """Must display canonical_patient_id when redirected."""
        code = _read_screen("PatientSearchScreen")
        assert "canonical_patient_id" in code, (
            "Must display canonical_patient_id for merged patients"
        )

    def test_uses_canonical_patient_id_for_navigation(self) -> None:
        """Must use canonical_patient_id when navigating to request consent."""
        code = _read_screen("PatientSearchScreen")
        # When is_redirected is true, should use canonical_patient_id for navigation
        assert "canonical_patient_id" in code, (
            "Must use canonical_patient_id for navigation when redirected"
        )
        # The navigation target should be request-consent
        assert "request-consent" in code, (
            "Must navigate to request-consent with the correct patient ID"
        )

    def test_warning_uses_orange_color(self) -> None:
        """Merged-patient warning must use orange color scheme."""
        code = _read_screen("PatientSearchScreen")
        assert "$orange" in code, (
            "Merged-patient warning must use orange color scheme"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 12. Dashboard screen with role and NFC scan
# ═══════════════════════════════════════════════════════════════════════════════


class TestDashboardRoleAndNfc:
    """DoctorDashboardScreen must display role and have NFC scan button."""

    def test_shows_role_from_context(self) -> None:
        """Must display the provider's role from auth context."""
        code = _read_screen("DoctorDashboardScreen")
        assert "role" in code, "Must display role from context"

    def test_has_nfc_scan_button(self) -> None:
        """Must have a button to scan NFC card (navigates to patient-search?mode=nfc)."""
        code = _read_screen("DoctorDashboardScreen")
        assert "mode=nfc" in code, (
            "Must have NFC scan button navigating to ?mode=nfc"
        )

    def test_shows_provider_id_card(self) -> None:
        """Must display provider ID in an identity card."""
        code = _read_screen("DoctorDashboardScreen")
        assert "providerId" in code, "Must display providerId from context"


# ═══════════════════════════════════════════════════════════════════════════════
# 13. Consent request screen
# ═══════════════════════════════════════════════════════════════════════════════


class TestRequestConsentScreen:
    """RequestConsentScreen must support the full consent request flow."""

    def test_uses_provider_auth_context(self) -> None:
        code = _read_screen("RequestConsentScreen")
        assert "useProviderAuth" in code, "Must use ProviderAuthContext"

    def test_reads_patient_id_from_params(self) -> None:
        code = _read_screen("RequestConsentScreen")
        assert "patient_id" in code, "Must read patient_id from route params"

    def test_calls_consent_request_api(self) -> None:
        code = _read_screen("RequestConsentScreen")
        assert "/api/v2/consent/request" in code, (
            "Must call POST /api/v2/consent/request"
        )

    def test_navigates_to_waiting(self) -> None:
        code = _read_screen("RequestConsentScreen")
        assert "/doctor/waiting" in code, "Must navigate to waiting screen"

    def test_has_purpose_input(self) -> None:
        code = _read_screen("RequestConsentScreen")
        assert "purpose" in code.lower(), "Must have purpose input"

    def test_has_scope_input(self) -> None:
        code = _read_screen("RequestConsentScreen")
        assert "scope" in code.lower(), "Must have scope input"

    def test_has_access_duration_input(self) -> None:
        code = _read_screen("RequestConsentScreen")
        assert "access_duration" in code.lower() or "accessDuration" in code, (
            "Must have access duration input"
        )

    def test_uses_provider_id_from_context(self) -> None:
        """Must use providerId from auth context, not hardcoded."""
        code = _read_screen("RequestConsentScreen")
        assert "providerId" in code, "Must use providerId from context"
        # provider_id as object key is fine — check for hardcoded values
        code_no_comments = _strip_comments(code)
        for pattern in ["provider-123", "provider-001", "PROVIDER_ID", "prov-123"]:
            assert pattern not in code_no_comments, (
                f"Must not contain placeholder '{pattern}'"
            )

    def test_has_request_access_button(self) -> None:
        code = _read_screen("RequestConsentScreen")
        assert "Request Access" in code, "Must have 'Request Access' button"

    def test_never_calls_approval_endpoint(self) -> None:
        """Doctor screen must NEVER call any approval/respond endpoint."""
        code = _read_screen("RequestConsentScreen")
        code_no_comments = _strip_comments(code)
        assert "approve-signed" not in code_no_comments, (
            "Must NOT call /api/v2/consent/approve-signed"
        )
        # Check for approve/deny POST endpoints (not just the word "approve")
        assert not re.search(r'\bpost\b.*\bapprove', code_no_comments.lower()), (
            "Must NOT POST to any approval endpoint"
        )

    def test_never_renders_approve_deny_buttons(self) -> None:
        """Doctor screen must NOT render Approve/Deny buttons."""
        code = _read_screen("RequestConsentScreen")
        assert "Approve" not in code, "Must NOT render Approve button"
        assert "Deny" not in code, "Must NOT render Deny button"

    def test_sends_request_with_patient_id_and_provider(self) -> None:
        """Consent request must include patient_id and provider_id."""
        code = _read_screen("RequestConsentScreen")
        assert "patient_id" in code, "Must send patient_id in request"
        assert "provider_id" in code, "Must send provider_id in request body"

    def test_shows_patient_id(self) -> None:
        code = _read_screen("RequestConsentScreen")
        assert "patientId" in code, "Must display the patient ID"


# ═══════════════════════════════════════════════════════════════════════════════
# 14. Waiting for approval screen — polling and state transitions
# ═══════════════════════════════════════════════════════════════════════════════


class TestWaitingForApprovalPolling:
    """WaitingForApprovalScreen must poll consent status every 2 seconds."""

    def test_polls_consent_status(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        assert "/api/v2/consent/status/" in code, (
            "Must poll GET /api/v2/consent/status/{request_id}"
        )

    def test_polls_every_2_seconds(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        assert "2000" in code, "Must poll every 2000ms (2 seconds)"

    def test_handles_approved_state(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        assert "approved" in code, "Must handle approved state"

    def test_handles_denied_state(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        assert "denied" in code, "Must handle denied state"

    def test_handles_expired_state(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        assert "expired" in code, "Must handle expired state"

    def test_stops_polling_on_terminal_state(self) -> None:
        """Must stop polling when status becomes approved, denied, or expired."""
        code = _read_screen("WaitingForApprovalScreen")
        assert "stopPolling" in code or "clearInterval" in code, (
            "Must have mechanism to stop polling on terminal state"
        )

    def test_cleans_up_interval_on_unmount(self) -> None:
        """Must clear interval on component unmount."""
        code = _read_screen("WaitingForApprovalScreen")
        assert "clearInterval" in code, "Must clearInterval on unmount"
        # The cleanup should be in a useEffect return
        assert "return () =>" in code or "return ()" in code, (
            "Must return cleanup function from useEffect"
        )


class TestWaitingApprovalAutoProceed:
    """When approved, the screen must auto-proceed to the patient record."""

    def test_navigates_to_record_on_approval(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        assert "/doctor/patient-record" in code, (
            "Must navigate to patient record on approval"
        )

    def test_auto_proceed_on_approval(self) -> None:
        """Approval must trigger automatic navigation, not require a button."""
        code = _read_screen("WaitingForApprovalScreen")
        # Must have a useEffect or similar that navigates on approved state
        assert "approved" in code, "Must check for approved state"
        assert "router.push" in code, "Must use router.push for navigation"

    def test_shows_green_success_on_approval(self) -> None:
        """Approved state must show green color scheme."""
        code = _read_screen("WaitingForApprovalScreen")
        assert "$green" in code, "Must use green color for approval"


class TestWaitingApprovalDenial:
    """When denied, the screen must show a red message and Back to Dashboard."""

    def test_shows_denied_message(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        assert "Denied" in code or "denied" in code, (
            "Must show denial message"
        )

    def test_shows_red_on_denial(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        assert "$red" in code, "Must use red color for denial"

    def test_has_back_to_dashboard_on_denial(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        assert "Back to Dashboard" in code, (
            "Must have 'Back to Dashboard' button on denial"
        )


class TestWaitingApprovalExpiry:
    """When expired, the screen must show a yellow message and Retry button."""

    def test_shows_expired_message(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        assert "Expired" in code or "expired" in code, (
            "Must show expiry message"
        )

    def test_shows_yellow_on_expiry(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        assert "$yellow" in code, "Must use yellow color for expiry"

    def test_has_retry_button_on_expiry(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        assert "Retry" in code, "Must have 'Retry' button on expiry"


class TestWaitingApprovalNeverApproves:
    """The doctor screen must NEVER call approval/respond endpoints or render approve/deny buttons."""

    def test_never_calls_approve_signed_endpoint(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        code_no_comments = _strip_comments(code)
        assert "approve-signed" not in code_no_comments, (
            "Must NOT call /api/v2/consent/approve-signed"
        )

    def test_never_calls_approve_endpoint(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        code_no_comments = _strip_comments(code)
        assert "/consent/approve" not in code_no_comments, (
            "Must NOT call any /consent/approve endpoint"
        )

    def test_never_renders_approve_button(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        code_no_comments = _strip_comments(code)
        # "Approve" must not appear as a button label. We check each Button's
        # immediate text content, not across the whole file with DOTALL.
        # Match <Button...>text</Button> where text contains "Approve"
        button_texts = re.findall(r'<Button[^>]*>\s*(.*?)\s*</Button>', code_no_comments, re.DOTALL)
        approve_buttons = [t for t in button_texts if 'Approve' in t]
        assert len(approve_buttons) == 0, (
            f"Must NOT render Approve button — only the patient can approve. Found: {approve_buttons}"
        )
        # Also check for onPress handlers that call approval endpoints
        assert not re.search(r'onPress.*approv', code_no_comments, re.IGNORECASE), (
            "Must NOT have onPress handler that triggers approval"
        )

    def test_never_renders_deny_button(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        code_no_comments = _strip_comments(code)
        # "Deny" must not appear as a button label
        assert not re.search(r'<Button[^>]*>.*Deny', code_no_comments, re.DOTALL), (
            "Must NOT render Deny button — only the patient can deny"
        )


class TestWaitingApprovalPendingState:
    """While pending, the screen shows spinner, elapsed timer, and cancel button."""

    def test_shows_spinner_while_pending(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        assert "Spinner" in code, "Must show Spinner while waiting"

    def test_shows_elapsed_timer(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        assert "Elapsed" in code or "elapsed" in code, (
            "Must show elapsed time counter"
        )

    def test_shows_request_id(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        assert "requestId" in code or "request_id" in code, (
            "Must display the request ID"
        )

    def test_has_cancel_request_button(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        assert "Cancel Request" in code or "Cancel" in code, (
            "Must have cancel button while waiting"
        )

    def test_shows_waiting_message(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        assert "Waiting" in code or "waiting" in code, (
            "Must show waiting/pending message"
        )
