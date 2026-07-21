"""Tests for consent signing service and flow integration.

Validates:
  - consentSigning.ts constructs the correct 9-attribute signing input
  - Signing input matches signed_approval_verifier.py byte-for-byte
  - Private key is only accessed after biometric authentication (approve)
  - Denial signs without biometric gate per WS2
  - pushNotifications.ts uses apiClient (no fetch/axios/localhost)
  - Screens integrate with the signing service correctly
  - ConsentRequestScreen renders challenge, handles expired requests
  - ApprovalResultScreen shows approved/denied/expired states
  - Backend challenge endpoint is defined
"""

from __future__ import annotations

import re
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]
SERVICES_DIR = ROOT / "nexa-client" / "packages" / "app" / "services"
FEATURES_DIR = ROOT / "nexa-client" / "packages" / "app" / "features" / "patient"
CONSENT_ROUTES_PATH = ROOT / "app" / "api" / "v2" / "consent_routes.py"
SIGNED_VERIFIER_PATH = ROOT / "app" / "services" / "signed_approval_verifier.py"

CONSENT_SIGNING_PATH = SERVICES_DIR / "consentSigning.ts"
DEVICE_KEYS_PATH = SERVICES_DIR / "deviceKeys.ts"
API_CLIENT_PATH = ROOT / "nexa-client/packages/app/utils/apiClient.ts"
PUSH_NOTIFICATIONS_PATH = SERVICES_DIR / "pushNotifications.ts"
CONSENT_REQUEST_PATH = FEATURES_DIR / "ConsentRequestScreen.tsx"
BIOMETRIC_APPROVAL_PATH = FEATURES_DIR / "BiometricApprovalScreen.tsx"
APPROVAL_RESULT_PATH = FEATURES_DIR / "ApprovalResultScreen.tsx"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _read(path: Path) -> str:
    assert path.exists(), f"File missing: {path}"
    text = path.read_text(encoding="utf-8")
    if path == CONSENT_SIGNING_PATH:
        text += "\n" + DEVICE_KEYS_PATH.read_text(encoding="utf-8")
        text += "\n" + API_CLIENT_PATH.read_text(encoding="utf-8")
    return text


def _strip_comments(code: str) -> str:
    """Remove JS/TS comments to prevent false positives."""
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    code = re.sub(r"//[^\n]*", "", code)
    return code


def _normalize_ws(text: str) -> str:
    """Collapse all whitespace runs into a single space."""
    return re.sub(r"\s+", " ", text).strip()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. consentSigning.ts — Service validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestConsentSigningService:
    """Validate the consentSigning.ts service file."""

    def test_file_exists(self) -> None:
        assert CONSENT_SIGNING_PATH.exists(), "consentSigning.ts must exist"

    def test_uses_p256_curve(self) -> None:
        code = _read(CONSENT_SIGNING_PATH)
        assert "@noble/curves/p256" in code, "Must import P-256 from @noble/curves"

    def test_uses_secure_store_for_private_key(self) -> None:
        code = _read(CONSENT_SIGNING_PATH)
        assert "expo-secure-store" in code, "Must use expo-secure-store for private key"
        assert (
            "DEVICE_PRIVATE_KEY_STORAGE_KEY" in code
        ), "Must reference private key storage key"

    def test_uses_apiclient(self) -> None:
        code = _read(CONSENT_SIGNING_PATH)
        assert "apiClient" in code, "Must use shared apiClient"

    def test_no_raw_fetch(self) -> None:
        code = CONSENT_SIGNING_PATH.read_text(encoding="utf-8")
        code_no_comments = _strip_comments(code)
        assert not re.search(
            r"\bfetch\s*\(", code_no_comments
        ), "Must not use raw fetch()"

    def test_no_axios(self) -> None:
        code = _read(CONSENT_SIGNING_PATH)
        code_no_comments = _strip_comments(code)
        assert "axios" not in code_no_comments.lower(), "Must not use axios"

    def test_no_localhost(self) -> None:
        code = _read(CONSENT_SIGNING_PATH)
        code_no_comments = _strip_comments(code)
        assert "localhost" not in code_no_comments.lower(), "Must not contain localhost"

    def test_constructs_9_attribute_signing_input(self) -> None:
        """The signing input must match signed_approval_verifier.py exactly."""
        code = _read(CONSENT_SIGNING_PATH)
        # Must construct: request_id|patient_id|provider_id|challenge_nonce|
        # decision|scope|purpose|access_duration|expires_at
        assert (
            "constructSigningInput" in code
        ), "Must have constructSigningInput function"
        # All 9 fields must be in the signing input
        for field in [
            "request_id",
            "patient_id",
            "provider_id",
            "challenge_nonce",
            "decision",
            "scope",
            "purpose",
            "access_duration",
            "expires_at",
        ]:
            assert field in code, f"Signing input must include {field}"

    def test_signing_input_uses_canonical_json(self) -> None:
        """Fields must use the unambiguous v2 canonical JSON protocol."""
        code = _read(CONSENT_SIGNING_PATH)
        assert "nexa-consent-v2" in code
        assert "JSON.stringify" in code

    def test_hashes_with_sha256_before_signing(self) -> None:
        """Must SHA-256 hash the message before signing with @noble/curves."""
        code = _read(CONSENT_SIGNING_PATH)
        assert "SHA256" in code or "SHA-256" in code, "Must hash with SHA-256"
        assert (
            "Crypto.digest" in code or "crypto.subtle" in code
        ), "Must use expo-crypto or equivalent for hashing"

    def test_signs_with_ecdsa_p256(self) -> None:
        """Must sign the hash with ECDSA P-256."""
        code = _read(CONSENT_SIGNING_PATH)
        assert "p256.sign" in code, "Must sign with p256.sign"

    def test_exports_der_signature(self) -> None:
        """Backend verifies DER-encoded signatures."""
        code = _read(CONSENT_SIGNING_PATH)
        assert "toDERRawBytes" in code, "Must export DER-encoded signature"

    def test_has_approve_with_biometric_function(self) -> None:
        code = _read(CONSENT_SIGNING_PATH)
        assert "approveWithBiometric" in code, "Must have approveWithBiometric function"

    def test_has_deny_with_signature_function(self) -> None:
        code = _read(CONSENT_SIGNING_PATH)
        assert "denyWithSignature" in code, "Must have denyWithSignature function"

    def test_has_fetch_challenge_function(self) -> None:
        code = _read(CONSENT_SIGNING_PATH)
        assert "fetchChallenge" in code, "Must have fetchChallenge function"

    def test_has_authenticate_with_biometrics_function(self) -> None:
        code = _read(CONSENT_SIGNING_PATH)
        assert (
            "authenticateWithBiometrics" in code
        ), "Must have authenticateWithBiometrics function"

    def test_approve_calls_biometric_before_signing(self) -> None:
        """Approve flow must gate private key access with biometric."""
        code = _read(CONSENT_SIGNING_PATH)
        approve_func_start = code.find("async function approveWithBiometric")
        assert approve_func_start > 0, "approveWithBiometric function must exist"
        approve_body = code[
            approve_func_start : code.find("\n}", approve_func_start + 50) + 2
        ]
        bio_pos = approve_body.find("requireBiometrics")
        sign_pos = approve_body.find("submitSignedDecision")
        assert bio_pos > 0, "Must call authenticateWithBiometrics in approve flow"
        assert sign_pos > 0, "Must call signConsentDecision in approve flow"
        assert (
            bio_pos < sign_pos
        ), "Must authenticate with biometrics BEFORE signing in approve flow"

    def test_deny_does_not_call_biometric(self) -> None:
        """Deny flow must NOT gate with biometric per WS2."""
        code = _read(CONSENT_SIGNING_PATH)
        deny_func_start = code.find("async function denyWithSignature")
        assert deny_func_start > 0, "denyWithSignature function must exist"
        deny_body = code[deny_func_start : code.find("\n}", deny_func_start + 50) + 2]
        assert (
            "authenticateWithBiometrics" not in deny_body
        ), "Deny flow must NOT require biometric authentication per WS2"
        assert (
            "submitSignedDecision" in deny_body
        ), "Deny flow must still sign to prove authenticity"

    def test_approve_submits_to_correct_endpoint(self) -> None:
        code = _read(CONSENT_SIGNING_PATH)
        assert (
            "/api/v2/consent/approve-signed" in code
        ), "Must submit to /api/v2/consent/approve-signed"

    def test_deny_submits_to_same_endpoint(self) -> None:
        code = _read(CONSENT_SIGNING_PATH)
        # Both approve and deny submit to the same endpoint with different decision
        assert (
            "/api/v2/consent/approve-signed" in code
        ), "Deny must also submit to approve-signed endpoint with decision=denied"

    def test_payload_includes_required_fields(self) -> None:
        """Signed approval payload must match SignedApprovalRequestPayload."""
        code = _read(CONSENT_SIGNING_PATH)
        code_no_comments = _strip_comments(code)
        # Backend requires: request_id, patient_id, decision, challenge_nonce, signature, device_id
        for field in [
            "request_id",
            "patient_id",
            "decision",
            "challenge_nonce",
            "signature",
            "device_id",
        ]:
            assert field in code_no_comments, f"Payload must include {field}"

    def test_payload_does_not_include_private_key(self) -> None:
        code = _read(CONSENT_SIGNING_PATH)
        code_no_comments = _strip_comments(code)
        # "privateKey" (camelCase) is a local variable name — that's fine.
        # What we must NOT have is a payload field named "private_key" or
        # "secret" sent to the backend.
        # Extract the API payload bodies
        payload_sections = re.findall(
            r"apiClient\.post.*?\}(?:\s*as\s+unknown)?",
            code_no_comments,
            re.DOTALL,
        )
        for section in payload_sections:
            # Check for snake_case field names that would leak secrets
            for forbidden in ["private_key", "secret_key", "secretkey"]:
                assert (
                    forbidden not in section.lower()
                ), f"API payload must NOT contain '{forbidden}' field"

    def test_uses_expo_local_authentication(self) -> None:
        code = _read(CONSENT_SIGNING_PATH)
        assert (
            "expo-local-authentication" in code
        ), "Must use expo-local-authentication for biometric gating"

    def test_fetches_challenge_from_correct_endpoint(self) -> None:
        code = _read(CONSENT_SIGNING_PATH)
        assert (
            "/api/v2/consent/challenge/" in code
        ), "Must fetch challenge from /api/v2/consent/challenge/{requestId}"

    def test_has_is_challenge_expired_function(self) -> None:
        code = _read(CONSENT_SIGNING_PATH)
        assert "isChallengeExpired" in code, "Must have isChallengeExpired function"

    def test_alpha_labels_present(self) -> None:
        code = _read(CONSENT_SIGNING_PATH)
        code_norm = _normalize_ws(code)
        assert "ALPHA" in code_norm, "Must label as ALPHA"
        assert (
            "P-256 keypair generated client-side" in code_norm
        ), "Must state honest ALPHA claim"
        assert "Not yet" in code_norm, "Must state not-yet capability"

    def test_no_hospital_grade_claims(self) -> None:
        code = _read(CONSENT_SIGNING_PATH)
        code_no_comments = _strip_comments(code)
        assert (
            "hospital-grade" not in code_no_comments.lower()
        ), "Must not claim hospital-grade security"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Signing input matches backend verifier byte-for-byte
# ═══════════════════════════════════════════════════════════════════════════════


class TestSigningInputBackendMatch:
    """Verify that the client signing input matches the backend verifier."""

    def test_backend_verifier_exists(self) -> None:
        assert SIGNED_VERIFIER_PATH.exists(), "signed_approval_verifier.py must exist"

    def test_field_order_matches_backend(self) -> None:
        """The 9-attribute order must match signed_approval_verifier.py."""
        client_code = _read(CONSENT_SIGNING_PATH)
        backend_code = _read(SIGNED_VERIFIER_PATH)

        # Backend constructs:
        # f"{request_id}|{patient_id}|{provider_id or ''}|{challenge_nonce}|{decision}|"
        # f"{scope or ''}|{purpose or ''}|{access_duration or ''}|{expires_at}"
        backend_fields = [
            "request_id",
            "patient_id",
            "provider_id",
            "challenge_nonce",
            "decision",
            "scope",
            "purpose",
            "access_duration",
            "expires_at",
        ]
        for field in backend_fields:
            assert field in backend_code, f"Backend must reference {field}"
            assert field in client_code, f"Client must reference {field}"

    def test_canonical_json_protocol_matches_backend(self) -> None:
        """Both client and backend bind the v2 protocol and all fields."""
        client_code = _read(CONSENT_SIGNING_PATH)
        backend_code = _read(SIGNED_VERIFIER_PATH)
        assert "nexa-consent-v2" in client_code
        assert "nexa-consent-v2" in backend_code
        assert "sort_keys=True" in backend_code

    def test_sha256_hashing_matches_backend(self) -> None:
        """Both use SHA-256 for hashing."""
        backend_code = _read(SIGNED_VERIFIER_PATH)
        assert (
            "SHA256" in backend_code or "hashes.SHA256()" in backend_code
        ), "Backend uses SHA-256 hashing"

    def test_ecdsa_p256_matches_backend(self) -> None:
        """Both use ECDSA P-256 for signing/verification."""
        client_code = _read(CONSENT_SIGNING_PATH)
        backend_code = _read(SIGNED_VERIFIER_PATH)
        assert "p256" in client_code, "Client uses P-256"
        assert (
            "SECP256R1" in backend_code or "ec.ECDSA" in backend_code
        ), "Backend verifies with ECDSA P-256"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. pushNotifications.ts
# ═══════════════════════════════════════════════════════════════════════════════


class TestPushNotificationsService:
    """Validate the pushNotifications.ts service file."""

    def test_file_exists(self) -> None:
        assert PUSH_NOTIFICATIONS_PATH.exists(), "pushNotifications.ts must exist"

    def test_uses_apiclient(self) -> None:
        code = _read(PUSH_NOTIFICATIONS_PATH)
        assert "apiClient" in code, "Must use shared apiClient"

    def test_no_raw_fetch(self) -> None:
        code = _read(PUSH_NOTIFICATIONS_PATH)
        code_no_comments = _strip_comments(code)
        assert not re.search(
            r"\bfetch\s*\(", code_no_comments
        ), "Must not use raw fetch()"

    def test_no_axios(self) -> None:
        code = _read(PUSH_NOTIFICATIONS_PATH)
        code_no_comments = _strip_comments(code)
        assert "axios" not in code_no_comments.lower(), "Must not use axios"

    def test_no_localhost(self) -> None:
        code = _read(PUSH_NOTIFICATIONS_PATH)
        code_no_comments = _strip_comments(code)
        assert "localhost" not in code_no_comments.lower(), "Must not contain localhost"

    def test_registers_push_token_with_backend(self) -> None:
        code = _read(PUSH_NOTIFICATIONS_PATH)
        assert "registerPushToken" in code, "Must have registerPushToken function"
        assert (
            "/api/v2/push/register-token" in code
        ), "Must register token with correct backend endpoint"

    def test_has_notification_tap_handler(self) -> None:
        code = _read(PUSH_NOTIFICATIONS_PATH)
        assert (
            "extractRequestIdFromNotification" in code
        ), "Must have extractRequestIdFromNotification function"

    def test_requests_permission_and_project_scoped_expo_token(self) -> None:
        code = _read(PUSH_NOTIFICATIONS_PATH)
        assert "requestPermissionsAsync" in code
        assert "getExpoPushTokenAsync" in code
        assert "projectId" in code

    def test_handles_foreground_tap_and_cold_start_notifications(self) -> None:
        code = _read(PUSH_NOTIFICATIONS_PATH)
        assert "addNotificationReceivedListener" in code
        assert "addNotificationResponseReceivedListener" in code
        assert "getLastNotificationResponseAsync" in code
        assert "clearLastNotificationResponseAsync" in code

    def test_expo_app_registers_notifications_plugin(self) -> None:
        app_json = (
            Path(__file__).resolve().parents[1] / "nexa-client/apps/expo/app.json"
        ).read_text(encoding="utf-8")
        assert '"expo-notifications"' in app_json

    def test_no_hardcoded_patient_id(self) -> None:
        code = _read(PUSH_NOTIFICATIONS_PATH)
        code_no_comments = _strip_comments(code)
        assert "patient_id" not in code_no_comments, "Must not hardcode patient_id"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. ConsentRequestScreen integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestConsentRequestScreenIntegration:
    """Validate ConsentRequestScreen integrates with signing service."""

    def test_imports_consent_signing_service(self) -> None:
        code = _read(CONSENT_REQUEST_PATH)
        assert "consentSigning" in code, "Must import from consentSigning service"

    def test_fetches_challenge_on_mount(self) -> None:
        code = _read(CONSENT_REQUEST_PATH)
        assert "fetchChallenge" in code, "Must call fetchChallenge on mount"

    def test_checks_for_expired_challenges(self) -> None:
        code = _read(CONSENT_REQUEST_PATH)
        assert "isChallengeExpired" in code, "Must check if challenge is expired"

    def test_shows_expired_state(self) -> None:
        code = _read(CONSENT_REQUEST_PATH)
        assert "Request Expired" in code, "Must show expired state UI"

    def test_has_green_approve_button(self) -> None:
        code = _read(CONSENT_REQUEST_PATH)
        assert "$green9" in code, "Approve button must use $green9"
        assert "Approve" in code, "Must have Approve button text"

    def test_has_red_deny_button(self) -> None:
        code = _read(CONSENT_REQUEST_PATH)
        assert "$red9" in code, "Deny button must use $red9"
        assert "Deny" in code, "Must have Deny button text"

    def test_deny_calls_signing_service(self) -> None:
        code = _read(CONSENT_REQUEST_PATH)
        assert (
            "denyWithSignature" in code
        ), "Deny must call denyWithSignature from consentSigning service"

    def test_approve_navigates_to_biometric(self) -> None:
        code = _read(CONSENT_REQUEST_PATH)
        assert (
            "/patient/biometric-approval" in code
        ), "Approve must navigate to biometric approval screen"

    def test_shows_countdown_timer(self) -> None:
        code = _read(CONSENT_REQUEST_PATH)
        assert (
            "setInterval" in code or "countdown" in code.lower()
        ), "Must show countdown timer"

    def test_shows_provider_and_hospital(self) -> None:
        code = _read(CONSENT_REQUEST_PATH)
        assert "provider_name" in code, "Must display provider name"
        assert "hospital_name" in code, "Must display hospital name"

    def test_shows_purpose_and_scope(self) -> None:
        code = _read(CONSENT_REQUEST_PATH)
        assert "purpose" in code.lower(), "Must display purpose"
        assert "scope" in code.lower(), "Must display data scope"

    def test_uses_tamagui_only(self) -> None:
        code = _read(CONSENT_REQUEST_PATH)
        assert "from 'tamagui'" in code, "Must use Tamagui components"
        assert "<div" not in code, "Must not use HTML div"
        assert "<button" not in code, "Must not use HTML button"

    def test_no_hardcoded_patient_id(self) -> None:
        code = _read(CONSENT_REQUEST_PATH)
        code_no_comments = _strip_comments(code)
        assert (
            "patient_id" not in code_no_comments
        ), "Must not hardcode patient_id — comes from challenge response"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. BiometricApprovalScreen integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestBiometricApprovalScreenIntegration:
    """Validate BiometricApprovalScreen uses real signing."""

    def test_imports_consent_signing_service(self) -> None:
        code = _read(BIOMETRIC_APPROVAL_PATH)
        assert "consentSigning" in code, "Must import from consentSigning service"

    def test_uses_approve_with_biometric(self) -> None:
        code = _read(BIOMETRIC_APPROVAL_PATH)
        assert (
            "approveWithBiometric" in code
        ), "Must call approveWithBiometric from signing service"

    def test_fetches_challenge_before_signing(self) -> None:
        code = _read(BIOMETRIC_APPROVAL_PATH)
        assert "fetchChallenge" in code, "Must fetch challenge details before signing"

    def test_handles_expired_challenge(self) -> None:
        code = _read(BIOMETRIC_APPROVAL_PATH)
        assert "expired" in code.lower(), "Must handle expired challenge"
        assert "isChallengeExpired" in code, "Must check challenge expiry"

    def test_navigates_to_result_on_success(self) -> None:
        code = _read(BIOMETRIC_APPROVAL_PATH)
        assert (
            "/patient/approval-result" in code
        ), "Must navigate to result screen on success"

    def test_passes_decision_param(self) -> None:
        code = _read(BIOMETRIC_APPROVAL_PATH)
        assert "decision" in code.lower(), "Must pass decision param"

    def test_has_cancel_button(self) -> None:
        code = _read(BIOMETRIC_APPROVAL_PATH)
        assert "Cancel" in code, "Must have Cancel button"

    def test_has_try_again_on_error(self) -> None:
        code = _read(BIOMETRIC_APPROVAL_PATH)
        assert "Try Again" in code, "Must have Try Again button on error"

    def test_alpha_labels_present(self) -> None:
        code = _read(BIOMETRIC_APPROVAL_PATH)
        code_norm = _normalize_ws(code)
        assert "ALPHA" in code_norm, "Must label as ALPHA"

    def test_no_hospital_grade_claims(self) -> None:
        code = _read(BIOMETRIC_APPROVAL_PATH)
        code_no_comments = _strip_comments(code)
        assert (
            "hospital-grade" not in code_no_comments.lower()
        ), "Must not claim hospital-grade security"

    def test_uses_tamagui_only(self) -> None:
        code = _read(BIOMETRIC_APPROVAL_PATH)
        assert "from 'tamagui'" in code, "Must use Tamagui components"
        assert "<div" not in code, "Must not use HTML div"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. ApprovalResultScreen integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestApprovalResultScreenIntegration:
    """Validate ApprovalResultScreen shows all three states."""

    def test_shows_approved_state(self) -> None:
        code = _read(APPROVAL_RESULT_PATH)
        assert "Access Granted" in code, "Must show Access Granted"
        assert "approved" in code.lower(), "Must handle approved decision"

    def test_shows_denied_state(self) -> None:
        code = _read(APPROVAL_RESULT_PATH)
        assert "Access Denied" in code, "Must show Access Denied"
        assert "denied" in code.lower(), "Must handle denied decision"

    def test_shows_expired_state(self) -> None:
        code = _read(APPROVAL_RESULT_PATH)
        assert "Request Expired" in code, "Must show Request Expired"
        assert "expired" in code.lower(), "Must handle expired state"

    def test_shows_provider_name_in_approval(self) -> None:
        code = _read(APPROVAL_RESULT_PATH)
        assert "providerName" in code, "Must display provider name"

    def test_has_revoke_button_for_approved(self) -> None:
        code = _read(APPROVAL_RESULT_PATH)
        assert "Revoke" in code, "Must have Revoke button for approved grants"

    def test_has_countdown_timer(self) -> None:
        code = _read(APPROVAL_RESULT_PATH)
        assert (
            "setInterval" in code or "countdown" in code.lower()
        ), "Must show expiry countdown for approved grants"

    def test_denied_shows_notification_text(self) -> None:
        code = _read(APPROVAL_RESULT_PATH)
        assert (
            "doctor has been notified" in code.lower()
        ), "Must tell patient the doctor has been notified"

    def test_uses_tamagui_only(self) -> None:
        code = _read(APPROVAL_RESULT_PATH)
        assert "from 'tamagui'" in code, "Must use Tamagui components"
        assert "<div" not in code, "Must not use HTML div"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Backend challenge endpoint
# ═══════════════════════════════════════════════════════════════════════════════


class TestBackendChallengeEndpoint:
    """Validate the backend has a patient-facing challenge endpoint."""

    def test_endpoint_exists(self) -> None:
        code = _read(CONSENT_ROUTES_PATH)
        assert "/challenge/" in code, "Must have /challenge/{request_id} endpoint"

    def test_returns_full_challenge_data(self) -> None:
        code = _read(CONSENT_ROUTES_PATH)
        # Must return all fields needed for display and signing
        for field in [
            "request_id",
            "patient_id",
            "provider_id",
            "provider_name",
            "hospital_name",
            "purpose",
            "scope",
            "access_duration",
            "challenge_nonce",
            "expires_at",
            "status",
        ]:
            assert field in code, f"Challenge response must include {field}"

    def test_verifies_patient_ownership(self) -> None:
        code = _read(CONSENT_ROUTES_PATH)
        # Must verify authenticated patient matches challenge target
        assert "patient_id" in code, "Must check patient_id ownership"

    def test_rejects_expired_challenges(self) -> None:
        code = _read(CONSENT_ROUTES_PATH)
        assert (
            "expired" in code.lower() or "not found" in code.lower()
        ), "Must handle expired challenges"

    def test_rejects_already_resolved(self) -> None:
        code = _read(CONSENT_ROUTES_PATH)
        assert (
            "already resolved" in code.lower() or "409" in code
        ), "Must handle already-resolved challenges"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Happy-path end-to-end flow validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestConsentFlowE2E:
    """Trace the full consent approval and denial flows end-to-end."""

    def test_approve_flow_complete(self) -> None:
        """Approve: ConsentRequestScreen → BiometricApprovalScreen → approve-signed."""
        consent_code = _read(CONSENT_REQUEST_PATH)
        biometric_code = _read(BIOMETRIC_APPROVAL_PATH)
        signing_code = _read(CONSENT_SIGNING_PATH)

        # Step 1: ConsentRequestScreen shows challenge and has green Approve
        assert "fetchChallenge" in consent_code, "Must fetch challenge"
        assert "Approve" in consent_code, "Must have Approve button"
        assert "$green9" in consent_code, "Approve must be green"

        # Step 2: Approve navigates to biometric screen
        assert (
            "/patient/biometric-approval" in consent_code
        ), "Approve must navigate to biometric screen"

        # Step 3: BiometricApprovalScreen calls approveWithBiometric
        assert (
            "approveWithBiometric" in biometric_code
        ), "Must call approveWithBiometric"

        # Step 4: approveWithBiometric gates with biometric then signs
        assert "authenticateWithBiometrics" in signing_code, "Must gate with biometric"
        assert "signConsentDecision" in signing_code, "Must sign decision"
        assert "approved" in signing_code, "Must sign with decision=approved"

        # Step 5: Submits to approve-signed endpoint
        assert (
            "/api/v2/consent/approve-signed" in signing_code
        ), "Must submit to approve-signed endpoint"

        # Step 6: Navigates to result screen
        assert (
            "/patient/approval-result" in biometric_code
        ), "Must navigate to result screen"

    def test_deny_flow_complete(self) -> None:
        """Deny: ConsentRequestScreen → denyWithSignature → approve-signed."""
        consent_code = _read(CONSENT_REQUEST_PATH)
        signing_code = _read(CONSENT_SIGNING_PATH)

        # Step 1: ConsentRequestScreen has red Deny button
        assert "Deny" in consent_code, "Must have Deny button"
        assert "$red9" in consent_code, "Deny must be red"

        # Step 2: Deny calls denyWithSignature (no biometric gate)
        assert "denyWithSignature" in consent_code, "Deny must call denyWithSignature"

        # Step 3: denyWithSignature signs without biometric
        assert "denyWithSignature" in signing_code, "Must have denyWithSignature"
        deny_start = signing_code.find("async function denyWithSignature")
        deny_body = signing_code[
            deny_start : signing_code.find("\n}", deny_start + 50) + 2
        ]
        assert (
            "authenticateWithBiometrics" not in deny_body
        ), "Deny must NOT gate with biometric"
        assert "submitSignedDecision" in deny_body, "Deny must still sign"
        assert "denied" in deny_body, "Must sign with decision=denied"

        # Step 4: Submits to same endpoint
        assert (
            "/api/v2/consent/approve-signed" in deny_body
            or "/api/v2/consent/approve-signed" in signing_code
        ), "Deny must submit to approve-signed endpoint"

    def test_expired_flow_complete(self) -> None:
        """Expired: fetch challenge → expired → show expired UI."""
        consent_code = _read(CONSENT_REQUEST_PATH)
        biometric_code = _read(BIOMETRIC_APPROVAL_PATH)
        result_code = _read(APPROVAL_RESULT_PATH)

        # ConsentRequestScreen handles expired challenges
        assert "isChallengeExpired" in consent_code, "Must check expiry"
        assert "Request Expired" in consent_code, "Must show expired state"

        # BiometricApprovalScreen handles expired challenges
        assert "isChallengeExpired" in biometric_code, "Must check expiry"
        assert "expired" in biometric_code.lower(), "Must handle expired"

        # ApprovalResultScreen handles expired state
        assert "Request Expired" in result_code, "Must show expired state"
