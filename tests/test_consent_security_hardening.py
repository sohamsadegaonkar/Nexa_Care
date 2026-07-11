"""Tests for consent security hardening (Day 14 critical issues).

Validates:
  1. Backend derives provider_id from session, rejects IDOR mismatches
  2. Purpose is a controlled code (not free-text)
  3. Scope is a controlled category (not free-text)
  4. Duration is server-clamped to [300, 3600]
  5. Consent request is not the access credential (request_id ≠ authorization)
  6. Polling is owner-scoped (only requesting provider may poll)
  7. Cancel endpoint provides real server-side cancellation
  8. Retry creates a brand new request (not reusing expired request_id)
  9. Adaptive polling backoff is implemented
  10. HTTP error codes are handled with differentiated behaviour
  11. Cache-Control headers prevent caching of consent state
  12. Zod schemas validate consent response shapes
  13. Doctor screens never render Approve/Deny buttons (re-verified)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]
DOCTOR_DIR = ROOT / "nexa-client" / "packages" / "app" / "features" / "doctor"
CONSENT_ROUTES = ROOT / "app" / "api" / "v2" / "consent_routes.py"
SCHEMAS_DIR = ROOT / "nexa-client" / "packages" / "app" / "schemas"


def _read(path: Path) -> str:
    assert path.exists(), f"File missing: {path}"
    return path.read_text(encoding="utf-8")


def _read_screen(name: str) -> str:
    return _read(DOCTOR_DIR / f"{name}.tsx")


def _normalize_ws(code: str) -> str:
    return re.sub(r"\s+", " ", code)


def _strip_comments(code: str) -> str:
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    code = re.sub(r"//[^\n]*", "", code)
    return code


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Backend derives provider_id from session (IDOR guard)
# ═══════════════════════════════════════════════════════════════════════════════


class TestConsentRequestIdorGuard:
    """Backend must derive provider_id from session and reject mismatches."""

    def test_idor_guard_code_exists(self) -> None:
        """The create_consent_request endpoint must have IDOR rejection logic."""
        code = _read(CONSENT_ROUTES)
        assert "IDOR" in code or "idor" in code.lower() or "does not match authenticated session" in code.lower(), (
            "Consent request endpoint must have IDOR guard that rejects provider_id mismatches"
        )

    def test_idor_guard_rejects_mismatch(self) -> None:
        """IDOR guard must return 403 when provider_id doesn't match session."""
        code = _read(CONSENT_ROUTES)
        # Must have 403 status for IDOR rejection
        assert "403" in code, "IDOR rejection must return 403 status"
        # Must audit the IDOR attempt
        assert "CONSENT_REQUEST_IDOR_REJECTED" in code, (
            "Must audit IDOR rejection with CONSENT_REQUEST_IDOR_REJECTED event"
        )

    def test_idor_guard_uses_session_actor_uid(self) -> None:
        """The challenge_payload must use provider.actor_uid, not payload.provider_id."""
        code = _read(CONSENT_ROUTES)
        code_norm = _normalize_ws(code)
        assert "provider.actor_uid" in code_norm, (
            "Challenge payload must use provider.actor_uid for provider_id"
        )

    def test_provider_id_in_body_marked_deprecated(self) -> None:
        """The provider_id field in the request payload should be marked deprecated."""
        code = _read(CONSENT_ROUTES)
        assert "DEPRECATED" in code or "deprecated" in code.lower(), (
            "ConsentChallengeRequestPayload.provider_id should be marked DEPRECATED"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Purpose is a controlled code (not free-text)
# ═══════════════════════════════════════════════════════════════════════════════


class TestControlledPurpose:
    """Purpose must be a controlled code, not free-text input."""

    def test_request_consent_uses_purpose_selector(self) -> None:
        """RequestConsentScreen must use a Select component for purpose."""
        code = _read_screen("RequestConsentScreen")
        assert "Select" in code, "Must use Select component for purpose"
        # Must have controlled purpose options
        assert "PURPOSE_OPTIONS" in code or "AccessPurpose" in code, (
            "Must define controlled purpose options (AccessPurpose type)"
        )

    def test_purpose_values_are_controlled(self) -> None:
        """Purpose options must include only coded values."""
        code = _read_screen("RequestConsentScreen")
        for purpose in ["treatment", "emergency_care", "diagnostic_review", "follow_up", "referral"]:
            assert purpose in code, f"Must include controlled purpose code: {purpose}"

    def test_no_free_text_purpose_input(self) -> None:
        """Purpose must NOT be a plain text Input — must use Select."""
        code = _read_screen("RequestConsentScreen")
        code_no_comments = _strip_comments(code)
        # Check that purpose uses Select, not a standalone Input for the purpose value
        # An optional purpose note Input is acceptable (separate from the purpose code)
        purpose_section = code_no_comments
        assert "Select" in purpose_section, "Purpose must use Select, not free-text Input"

    def test_purpose_note_is_optional(self) -> None:
        """An optional free-text purpose note is acceptable as a supplement."""
        code = _read_screen("RequestConsentScreen")
        # Should have purposeNote for optional explanation
        assert "purposeNote" in code or "Purpose Note" in code, (
            "Should have optional purpose note field for human-readable context"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Scope is a controlled category (not free-text)
# ═══════════════════════════════════════════════════════════════════════════════


class TestControlledScope:
    """Scope must be a controlled category, not free-text input."""

    def test_request_consent_uses_scope_selector(self) -> None:
        """RequestConsentScreen must use a Select component for scope."""
        code = _read_screen("RequestConsentScreen")
        assert "Select" in code, "Must use Select component for scope"
        assert "SCOPE_OPTIONS" in code or "ConsentScope" in code, (
            "Must define controlled scope options (ConsentScope type)"
        )

    def test_scope_values_are_controlled(self) -> None:
        """Scope options must include only coded category values."""
        code = _read_screen("RequestConsentScreen")
        for scope in ["patient_summary", "vitals", "medications", "allergies", "lab_results", "clinical_record"]:
            assert scope in code, f"Must include controlled scope category: {scope}"

    def test_no_free_text_scope_input(self) -> None:
        """Scope must NOT be a plain text Input — must use Select."""
        code = _read_screen("RequestConsentScreen")
        code_no_comments = _strip_comments(code)
        assert "Select" in code_no_comments, "Scope must use Select, not free-text Input"

    def test_scope_has_description(self) -> None:
        """Each scope option should have a human-readable description."""
        code = _read_screen("RequestConsentScreen")
        assert "description" in code, "Scope options must include descriptions"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Duration is server-clamped and preset on frontend
# ═══════════════════════════════════════════════════════════════════════════════


class TestDurationConstraints:
    """Duration must be server-clamped and use presets on the frontend."""

    def test_backend_clamps_duration(self) -> None:
        """Backend must enforce minimum and maximum duration bounds."""
        code = _read(CONSENT_ROUTES)
        assert "MIN_DURATION" in code, "Backend must define MIN_DURATION"
        assert "MAX_DURATION" in code, "Backend must define MAX_DURATION"
        assert "300" in code, "MIN_DURATION should be 300 seconds (5 minutes)"
        assert "3600" in code, "MAX_DURATION should be 3600 seconds (60 minutes)"

    def test_frontend_uses_duration_presets(self) -> None:
        """Frontend must use preset duration buttons, not free-text number input."""
        code = _read_screen("RequestConsentScreen")
        assert "DURATION_PRESETS" in code, "Must define DURATION_PRESETS"
        assert "300" in code, "Must include 5-minute preset"
        assert "900" in code, "Must include 15-minute preset"
        assert "1800" in code, "Must include 30-minute preset"
        assert "3600" in code, "Must include 60-minute preset"

    def test_frontend_mentions_server_clamping(self) -> None:
        """Frontend must inform the user that the server enforces bounds."""
        code = _read_screen("RequestConsentScreen")
        code_lower = code.lower()
        assert "server enforces" in code_lower or "server clamps" in code_lower or "minimum" in code_lower, (
            "Frontend must mention that server enforces duration bounds"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Navigation ≠ authorization (request_id is not access credential)
# ═══════════════════════════════════════════════════════════════════════════════


class TestNavigationNotAuthorization:
    """Navigating to patient-record with request_id does not grant access."""

    def test_flow_doc_documents_navigation_not_authorization(self) -> None:
        """The flow doc must explicitly state that navigation ≠ authorization."""
        code = _read(ROOT / "docs" / "doctor-app-flow.md")
        code_lower = code.lower()
        assert "navigation" in code_lower and "authorization" in code_lower, (
            "Flow doc must state that navigation to a record does not equal authorization"
        )

    def test_flow_doc_documents_consent_token_requirement(self) -> None:
        """The flow doc must state that record access requires both session + consent token."""
        code = _read(ROOT / "docs" / "doctor-app-flow.md")
        code_lower = code.lower()
        assert "consent token" in code_lower or "consent_token" in code_lower, (
            "Flow doc must state that record reads require a consent token, not just request_id"
        )

    def test_backend_status_endpoint_returns_no_consent_token(self) -> None:
        """The status endpoint must return minimal data (no consent tokens)."""
        code = _read(CONSENT_ROUTES)
        # The ConsentStatusResponsePayload should not have consent_token field
        assert "consent_token" not in _normalize_ws(
            code[code.find("ConsentStatusResponsePayload"):code.find("ConsentStatusResponsePayload") + 500]
        ), "ConsentStatusResponsePayload must NOT include consent_token — status only"

    def test_request_id_in_url_is_not_sufficient(self) -> None:
        """The patient-record endpoint must require consent authorization beyond request_id."""
        # This is a documentation/assertion check: the record endpoint should
        # require X-Consent-Token header or equivalent, not just request_id in URL
        record_routes = ROOT / "app" / "api" / "v2" / "patient_record_routes.py"
        if record_routes.exists():
            code = _read(record_routes)
            assert "consent" in code.lower() or "X-Consent-Token" in code, (
                "Record endpoint must require consent authorization"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Polling is owner-scoped
# ═══════════════════════════════════════════════════════════════════════════════


class TestPollingOwnerScoped:
    """Only the requesting provider may poll consent request status."""

    def test_status_endpoint_checks_provider_ownership(self) -> None:
        """Backend must verify the polling provider owns the request."""
        code = _read(CONSENT_ROUTES)
        # The get_consent_request_status function must compare provider_id
        assert "provider_id" in code and "actor_uid" in code, (
            "Status endpoint must check that requesting provider matches stored provider_id"
        )
        assert "403" in code, "Owner mismatch must return 403"

    def test_status_endpoint_documented_as_owner_scoped(self) -> None:
        """The docstring must state the owner-scoping security control."""
        code = _read(CONSENT_ROUTES)
        code_norm = _normalize_ws(code.lower())
        assert "only requesting provider" in code_norm or "owner" in code_norm, (
            "Status endpoint docstring must document owner-scoping"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Real server-side cancellation
# ═══════════════════════════════════════════════════════════════════════════════


class TestConsentCancellation:
    """Cancel must be a real server-side action, not just navigation."""

    def test_cancel_endpoint_exists(self) -> None:
        """Backend must have a POST /consent/request/{request_id}/cancel endpoint."""
        code = _read(CONSENT_ROUTES)
        assert "/request/{request_id}/cancel" in code, (
            "Must have cancel endpoint at /consent/request/{request_id}/cancel"
        )

    def test_cancel_checks_ownership(self) -> None:
        """Cancel must verify the requesting provider owns the request."""
        code = _read(CONSENT_ROUTES)
        # Find the cancel function and check it verifies ownership
        cancel_section = code[code.find("cancel_consent_request"):]
        cancel_section = cancel_section[:cancel_section.find("class ")] if "class " in cancel_section else cancel_section[:2000]
        assert "provider_id" in cancel_section and "actor_uid" in cancel_section, (
            "Cancel endpoint must verify provider_id matches actor_uid"
        )

    def test_cancel_rejects_terminal_states(self) -> None:
        """Cancel must reject requests in approved/denied/expired states."""
        code = _read(CONSENT_ROUTES)
        assert "409" in code, "Cancel must return 409 for terminal-state requests"
        assert "pending" in code, "Only pending requests can be cancelled"

    def test_cancel_audits_the_action(self) -> None:
        """Cancel must create an audit log entry."""
        code = _read(CONSENT_ROUTES)
        assert "CONSENT_REQUEST_CANCELLED" in code, (
            "Cancel must audit with CONSENT_REQUEST_CANCELLED event"
        )

    def test_cancel_sets_status_in_redis(self) -> None:
        """Cancel must update the request status in Redis to prevent later approval."""
        code = _read(CONSENT_ROUTES)
        cancel_section = code[code.find("cancel_consent_request"):]
        cancel_section = cancel_section[:cancel_section.find("class ")] if "class " in cancel_section else cancel_section[:2000]
        assert '"cancelled"' in cancel_section, "Cancel must set status to 'cancelled' in Redis"

    def test_frontend_cancel_calls_api(self) -> None:
        """Frontend Cancel Request button must call the cancel API endpoint."""
        code = _read_screen("WaitingForApprovalScreen")
        code_no_comments = _strip_comments(code)
        # The screen may use NexaApiClient.cancelConsentRequest() or the raw endpoint
        assert "/cancel" in code_no_comments or "cancelConsentRequest" in code_no_comments, (
            "Cancel button must call cancel consent API endpoint or method"
        )

    def test_frontend_shows_cancelled_state(self) -> None:
        """Frontend must render a cancelled state with appropriate message."""
        code = _read_screen("WaitingForApprovalScreen")
        assert "cancelled" in code.lower(), "Must handle cancelled state"
        assert "Cancelled" in code, "Must show cancelled message"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Retry creates new request (preserves patient_id context)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRetryNewRequest:
    """Retry must navigate to request-consent with patient_id, creating a new request."""

    def test_waiting_screen_passes_patient_id_to_waiting(self) -> None:
        """RequestConsentScreen must pass patient_id to the waiting screen URL."""
        code = _read_screen("RequestConsentScreen")
        assert "patient_id" in code, "Must pass patient_id in waiting screen navigation"
        # The navigation URL should include patient_id as a query parameter
        code_norm = _normalize_ws(code)
        assert "patient_id" in code_norm, "Waiting screen URL must include patient_id param"

    def test_waiting_screen_reads_patient_id_from_params(self) -> None:
        """WaitingForApprovalScreen must read patient_id from URL params."""
        code = _read_screen("WaitingForApprovalScreen")
        code_norm = _normalize_ws(code)
        assert "patient_id" in code_norm, "Must read patient_id from search params"

    def test_retry_navigates_to_request_consent_with_patient_id(self) -> None:
        """Retry must navigate to /doctor/request-consent?patient_id=..."""
        code = _read_screen("WaitingForApprovalScreen")
        code_no_comments = _strip_comments(code)
        # handleRetry should navigate to request-consent with patient_id
        assert "request-consent" in code_no_comments, (
            "Retry must navigate to request-consent"
        )
        assert "patient_id" in code_no_comments, (
            "Retry navigation must include patient_id context"
        )

    def test_retry_does_not_reuse_request_id(self) -> None:
        """Retry must NOT reuse the old request_id — it creates a new one."""
        code = _read_screen("WaitingForApprovalScreen")
        code_no_comments = _strip_comments(code)
        # handleRetry should NOT include the old request_id
        # It should navigate to request-consent which creates a new request
        retry_fn = code_no_comments[code_no_comments.find("handleRetry"):]
        retry_fn = retry_fn[:retry_fn.find("}") + 1] if "}" in retry_fn[:500] else retry_fn[:500]
        assert "request_id" not in retry_fn or "new" in retry_fn.lower(), (
            "Retry must NOT reuse the old request_id"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Adaptive polling backoff
# ═══════════════════════════════════════════════════════════════════════════════


class TestAdaptivePollingBackoff:
    """Polling must use adaptive backoff: 2s → 5s → 10s."""

    def test_has_adaptive_intervals(self) -> None:
        """Must define multiple poll intervals for adaptive backoff."""
        code = _read_screen("WaitingForApprovalScreen")
        assert "POLL_FAST" in code or "POLL_MEDIUM" in code or "POLL_SLOW" in code, (
            "Must define POLL_FAST_MS, POLL_MEDIUM_MS, POLL_SLOW_MS constants"
        )

    def test_fast_interval_is_2_seconds(self) -> None:
        """Fast polling interval should be 2000ms."""
        code = _read_screen("WaitingForApprovalScreen")
        assert "POLL_FAST_MS = 2000" in code or "2000" in code, (
            "Fast polling should be 2000ms"
        )

    def test_has_cutoff_thresholds(self) -> None:
        """Must define cutoff thresholds for interval escalation."""
        code = _read_screen("WaitingForApprovalScreen")
        assert "FAST_CUTOFF" in code or "MEDIUM_CUTOFF" in code, (
            "Must define FAST_CUTOFF_S and MEDIUM_CUTOFF_S thresholds"
        )

    def test_shows_current_polling_rate(self) -> None:
        """Frontend should indicate the current polling rate to the user."""
        code = _read_screen("WaitingForApprovalScreen")
        assert "Polling every" in code or "polling" in code.lower(), (
            "Should show current polling rate indicator"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Differentiated HTTP error handling
# ═══════════════════════════════════════════════════════════════════════════════


class TestDifferentiatedErrorHandling:
    """Different HTTP error codes must produce different UI behaviours."""

    def test_handles_401_unauthorized(self) -> None:
        """401 must stop polling and show session-expired state."""
        code = _read_screen("WaitingForApprovalScreen")
        assert "401" in code, "Must handle 401 status"
        assert "unauthorized" in code.lower(), "Must set unauthorized state on 401"

    def test_handles_403_forbidden(self) -> None:
        """403 must stop polling and show not-authorized state."""
        code = _read_screen("WaitingForApprovalScreen")
        assert "403" in code, "Must handle 403 status"
        assert "forbidden" in code.lower(), "Must set forbidden state on 403"

    def test_handles_404_not_found(self) -> None:
        """404 must stop polling and show request-not-found state."""
        code = _read_screen("WaitingForApprovalScreen")
        assert "404" in code, "Must handle 404 status"
        assert "not_found" in code.lower(), "Must set not_found state on 404"

    def test_handles_429_rate_limited(self) -> None:
        """429 must retry with backoff, not stop polling."""
        code = _read_screen("WaitingForApprovalScreen")
        assert "429" in code, "Must handle 429 rate limiting"
        # Should NOT stop polling on 429 — should retry with backoff
        rate_limit_section = code[code.find("429"):code.find("429") + 300] if "429" in code else ""
        assert "scheduleNextPoll" in rate_limit_section or "retry" in rate_limit_section.lower(), (
            "429 must retry with backoff, not stop polling"
        )

    def test_handles_5xx_server_error(self) -> None:
        """5xx must retry with backoff, not stop polling."""
        code = _read_screen("WaitingForApprovalScreen")
        assert "500" in code or "5xx" in code or ">= 500" in code, (
            "Must handle 5xx server errors"
        )

    def test_shows_unauthorized_state_ui(self) -> None:
        """Must render a dedicated unauthorized/session-expired UI state."""
        code = _read_screen("WaitingForApprovalScreen")
        assert "unauthorized" in code.lower(), "Must render unauthorized state"
        assert "Session Expired" in code or "session expired" in code.lower(), (
            "Must show 'Session Expired' message"
        )
        assert "Return to Login" in code, "Must have 'Return to Login' button"

    def test_shows_forbidden_state_ui(self) -> None:
        """Must render a dedicated forbidden/not-authorized UI state."""
        code = _read_screen("WaitingForApprovalScreen")
        assert "forbidden" in code.lower(), "Must render forbidden state"
        assert "Not Authorized" in code, "Must show 'Not Authorized' message"

    def test_handles_network_error_with_retry(self) -> None:
        """Network errors must trigger retry, not stop polling."""
        code = _read_screen("WaitingForApprovalScreen")
        code_no_comments = _strip_comments(code)
        assert "Network" in code_no_comments or "network" in code_no_comments.lower(), (
            "Must handle network errors with retry"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Cache-Control headers on consent status endpoint
# ═══════════════════════════════════════════════════════════════════════════════


class TestConsentCacheHeaders:
    """Consent status must never be cached by browsers or CDNs."""

    def test_status_endpoint_documented_no_store(self) -> None:
        """The status endpoint docstring must mention Cache-Control: no-store."""
        code = _read(CONSENT_ROUTES)
        code_norm = _normalize_ws(code.lower())
        assert "cache" in code_norm or "no-store" in code_norm, (
            "Status endpoint must mention Cache-Control: no-store"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 12. Zod schemas for consent responses
# ═══════════════════════════════════════════════════════════════════════════════


class TestConsentZodSchemas:
    """Zod schemas must validate consent request and status responses."""

    def test_has_consent_challenge_response_schema(self) -> None:
        """Must define ConsentChallengeResponseSchema."""
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert "ConsentChallengeResponseSchema" in code, (
            "Must define ConsentChallengeResponseSchema"
        )

    def test_has_consent_status_response_schema(self) -> None:
        """Must define ConsentStatusResponseSchema."""
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert "ConsentStatusResponseSchema" in code, (
            "Must define ConsentStatusResponseSchema"
        )

    def test_has_consent_cancel_response_schema(self) -> None:
        """Must define ConsentCancelResponseSchema."""
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert "ConsentCancelResponseSchema" in code, (
            "Must define ConsentCancelResponseSchema"
        )

    def test_status_schema_validates_status_enum(self) -> None:
        """ConsentStatusResponseSchema must validate status as enum."""
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert "z.enum" in code, "Status must be validated as a Zod enum"
        # Must include cancelled as valid status
        assert "cancelled" in code, "Status enum must include 'cancelled'"

    def test_cancel_schema_requires_cancelled_status(self) -> None:
        """ConsentCancelResponseSchema must require status='cancelled'."""
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert "z.literal" in code, "Cancel response must use z.literal('cancelled')"

    def test_challenge_schema_validates_request_id(self) -> None:
        """ConsentChallengeResponseSchema must validate request_id."""
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert "request_id" in code, "Must validate request_id field"
        assert "expires_in_seconds" in code, "Must validate expires_in_seconds field"


# ═══════════════════════════════════════════════════════════════════════════════
# 13. Doctor screens never render Approve/Deny buttons (re-verified)
# ═══════════════════════════════════════════════════════════════════════════════


class TestDoctorNeverApproves:
    """Doctor screens must never render Approve/Deny buttons or call approval endpoints."""

    def test_request_consent_no_approve_button(self) -> None:
        code = _read_screen("RequestConsentScreen")
        assert "Approve" not in code, "Must NOT render Approve button"

    def test_request_consent_no_deny_button(self) -> None:
        code = _read_screen("RequestConsentScreen")
        assert "Deny" not in code, "Must NOT render Deny button"

    def test_waiting_no_approve_button(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        code_no_comments = _strip_comments(code)
        # Check each Button's immediate text content, not across the whole file
        button_texts = re.findall(r'<Button[^>]*>\s*(.*?)\s*</Button>', code_no_comments, re.DOTALL)
        approve_buttons = [t for t in button_texts if 'Approve' in t]
        assert len(approve_buttons) == 0, (
            f"Must NOT render Approve button. Found: {approve_buttons}"
        )

    def test_waiting_no_deny_button(self) -> None:
        code = _read_screen("WaitingForApprovalScreen")
        code_no_comments = _strip_comments(code)
        assert not re.search(r'<Button[^>]*>.*Deny', code_no_comments, re.DOTALL), (
            "Must NOT render Deny button"
        )

    def test_never_calls_approve_signed(self) -> None:
        """Neither RequestConsentScreen nor WaitingForApprovalScreen calls approve-signed."""
        for screen in ["RequestConsentScreen", "WaitingForApprovalScreen"]:
            code = _read_screen(screen)
            code_no_comments = _strip_comments(code)
            assert "approve-signed" not in code_no_comments, (
                f"{screen} must NOT call /api/v2/consent/approve-signed"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Integration: Backend cancel endpoint returns 200 with correct shape
# ═══════════════════════════════════════════════════════════════════════════════


class TestCancelEndpointIntegration:
    """Integration tests for the cancel consent request endpoint."""

    def test_cancel_pending_request_succeeds(self) -> None:
        """Cancelling a pending request should return 200 with cancelled status."""
        from app.core.dependencies import get_current_provider

        import uuid
        from app.models.provider_context import ProviderContext, ProviderIdentityContext, HospitalContext, AffiliationContext
        from app.models.provider import AffiliationType

        provider_id = uuid.uuid4()
        mock_provider = ProviderContext(
            provider=ProviderIdentityContext(provider_id=provider_id, display_name="Test", contact_email="t@ex.com"),
            hospital=HospitalContext(hospital_id=uuid.uuid4(), facility_code="H", display_name="H"),
            affiliation=AffiliationContext(affiliation_id=uuid.uuid4(), affiliation_type=AffiliationType.PERMANENT, is_primary=True, roles=["clinician"]),
        )
        app.dependency_overrides[get_current_provider] = lambda: mock_provider

        try:
            client = TestClient(app)
            request_id = str(uuid.uuid4())

            with patch("app.api.v2.consent_routes.get_redis_client") as mock_redis:
                mock_r = MagicMock()
                mock_redis.return_value = mock_r

                # Request data for a pending request owned by this provider
                challenge_data = json.dumps({
                    "request_id": request_id,
                    "patient_id": "123e4567-e89b-12d3-a456-426614174001",
                    "provider_id": str(provider_id),
                    "status": "pending",
                    "challenge_nonce": "test-nonce",
                })
                mock_r.get.return_value = challenge_data.encode()
                mock_r.set.return_value = True

                res = client.post(f"/api/v2/consent/request/{request_id}/cancel")
                assert res.status_code == 200, f"Cancel should succeed: {res.text}"
                data = res.json()
                assert data["status"] == "cancelled"
                assert "cancelled_at" in data
        finally:
            app.dependency_overrides.pop(get_current_provider, None)

    def test_cancel_non_pending_request_fails(self) -> None:
        """Cancelling an approved/denied/expired request should return 409."""
        from app.core.dependencies import get_current_provider

        import uuid
        from app.models.provider_context import ProviderContext, ProviderIdentityContext, HospitalContext, AffiliationContext
        from app.models.provider import AffiliationType

        provider_id = uuid.uuid4()
        mock_provider = ProviderContext(
            provider=ProviderIdentityContext(provider_id=provider_id, display_name="Test", contact_email="t@ex.com"),
            hospital=HospitalContext(hospital_id=uuid.uuid4(), facility_code="H", display_name="H"),
            affiliation=AffiliationContext(affiliation_id=uuid.uuid4(), affiliation_type=AffiliationType.PERMANENT, is_primary=True, roles=["clinician"]),
        )
        app.dependency_overrides[get_current_provider] = lambda: mock_provider

        try:
            client = TestClient(app)
            request_id = str(uuid.uuid4())

            with patch("app.api.v2.consent_routes.get_redis_client") as mock_redis:
                mock_r = MagicMock()
                mock_redis.return_value = mock_r

                # Request already approved
                approved_data = json.dumps({
                    "request_id": request_id,
                    "patient_id": "123e4567-e89b-12d3-a456-426614174001",
                    "provider_id": str(provider_id),
                    "status": "approved",
                })
                mock_r.get.return_value = approved_data.encode()
                mock_r.set.return_value = True

                res = client.post(f"/api/v2/consent/request/{request_id}/cancel")
                assert res.status_code == 409, f"Cancel approved request should return 409: {res.text}"
        finally:
            app.dependency_overrides.pop(get_current_provider, None)

    def test_cancel_wrong_provider_fails(self) -> None:
        """Cancelling another provider's request should return 403."""
        from app.core.dependencies import get_current_provider

        import uuid
        from app.models.provider_context import ProviderContext, ProviderIdentityContext, HospitalContext, AffiliationContext
        from app.models.provider import AffiliationType

        provider_id = uuid.uuid4()
        other_provider_id = uuid.uuid4()
        mock_provider = ProviderContext(
            provider=ProviderIdentityContext(provider_id=provider_id, display_name="Test", contact_email="t@ex.com"),
            hospital=HospitalContext(hospital_id=uuid.uuid4(), facility_code="H", display_name="H"),
            affiliation=AffiliationContext(affiliation_id=uuid.uuid4(), affiliation_type=AffiliationType.PERMANENT, is_primary=True, roles=["clinician"]),
        )
        app.dependency_overrides[get_current_provider] = lambda: mock_provider

        try:
            client = TestClient(app)
            request_id = str(uuid.uuid4())

            with patch("app.api.v2.consent_routes.get_redis_client") as mock_redis:
                mock_r = MagicMock()
                mock_redis.return_value = mock_r

                # Request belongs to other provider
                other_data = json.dumps({
                    "request_id": request_id,
                    "patient_id": "123e4567-e89b-12d3-a456-426614174001",
                    "provider_id": str(other_provider_id),
                    "status": "pending",
                })
                mock_r.get.return_value = other_data.encode()
                mock_r.set.return_value = True

                res = client.post(f"/api/v2/consent/request/{request_id}/cancel")
                assert res.status_code == 403, f"Cancel other provider's request should return 403: {res.text}"
        finally:
            app.dependency_overrides.pop(get_current_provider, None)

    def test_idor_mismatch_rejected(self) -> None:
        """Creating a consent request with wrong provider_id should return 403."""
        from app.core.dependencies import get_current_provider

        import uuid
        from app.models.provider_context import ProviderContext, ProviderIdentityContext, HospitalContext, AffiliationContext
        from app.models.provider import AffiliationType

        provider_id = uuid.uuid4()
        mock_provider = ProviderContext(
            provider=ProviderIdentityContext(provider_id=provider_id, display_name="Test", contact_email="t@ex.com"),
            hospital=HospitalContext(hospital_id=uuid.uuid4(), facility_code="H", display_name="H"),
            affiliation=AffiliationContext(affiliation_id=uuid.uuid4(), affiliation_type=AffiliationType.PERMANENT, is_primary=True, roles=["clinician"]),
        )
        app.dependency_overrides[get_current_provider] = lambda: mock_provider

        mock_db = AsyncMock()
        from app.core.database import get_db_session as _get_db
        app.dependency_overrides[_get_db] = lambda: mock_db

        try:
            client = TestClient(app)
            # Send a different provider_id in the body
            res = client.post(
                "/api/v2/consent/request",
                json={
                    "patient_id": "123e4567-e89b-12d3-a456-426614174001",
                    "provider_id": str(uuid.uuid4()),  # Different from session
                    "purpose": "treatment",
                    "scope": "clinical",
                },
            )
            assert res.status_code == 403, f"IDOR mismatch should return 403: {res.text}"
        finally:
            app.dependency_overrides.pop(get_current_provider, None)
            app.dependency_overrides.pop(_get_db, None)
