"""Tests for device key generation and enrollment service.

Validates:
  - deviceKeys.ts uses correct crypto (P-256, DER wrapping, SecureStore)
  - Private key is NEVER sent to the backend
  - apiClient is used for all network calls (no raw fetch / axios / localhost)
  - ALPHA honesty labels present (no hospital-grade claims)
  - Screens integrate with the service correctly
  - Enrollment success and error handling flows
"""

from __future__ import annotations

import re
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]
SERVICES_DIR = ROOT / "nexa-client" / "packages" / "app" / "services"
FEATURES_DIR = ROOT / "nexa-client" / "packages" / "app" / "features" / "patient"
API_CLIENT_PATH = ROOT / "nexa-client" / "packages" / "app" / "utils" / "apiClient.ts"

DEVICE_KEYS_PATH = SERVICES_DIR / "deviceKeys.ts"
SECURE_DEVICE_PATH = FEATURES_DIR / "SecureDeviceScreen.tsx"
DEVICE_ENROLLED_PATH = FEATURES_DIR / "DeviceEnrolledScreen.tsx"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _read(path: Path) -> str:
    assert path.exists(), f"File missing: {path}"
    return path.read_text(encoding="utf-8")


def _normalize_ws(text: str) -> str:
    """Collapse all whitespace runs (including newlines) into a single space.

    This makes substring assertions work across JSX text that is wrapped
    across multiple lines in the source file.
    """
    return re.sub(r"\s+", " ", text).strip()


def _strip_comments(code: str) -> str:
    """Remove JS/TS comments to prevent false positives."""
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    code = re.sub(r"//[^\n]*", "", code)
    return code


# ═══════════════════════════════════════════════════════════════════════════════
# 1. deviceKeys.ts — Key generation service
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeviceKeysService:
    """Validate the deviceKeys.ts service file."""

    def test_file_exists(self) -> None:
        assert DEVICE_KEYS_PATH.exists(), "nexa-client/packages/app/services/deviceKeys.ts must exist"

    def test_uses_p256_curve(self) -> None:
        code = _read(DEVICE_KEYS_PATH)
        assert "@noble/curves/p256" in code, "Must import P-256 from @noble/curves"
        assert "p256" in code, "Must use p256 for key generation"

    def test_uses_secure_store(self) -> None:
        code = _read(DEVICE_KEYS_PATH)
        assert "expo-secure-store" in code, "Must use expo-secure-store for private key storage"
        assert "SecureStore" in code, "Must reference SecureStore API"
        assert "setItemAsync" in code, "Must store private key via SecureStore.setItemAsync"
        assert "getItemAsync" in code, "Must read private key via SecureStore.getItemAsync"

    def test_no_plain_async_storage(self) -> None:
        code = _read(DEVICE_KEYS_PATH)
        code_no_comments = _strip_comments(code)
        assert "AsyncStorage" not in code_no_comments, (
            "Must NOT use plain AsyncStorage for private key storage"
        )

    def test_uses_apiclient(self) -> None:
        code = _read(DEVICE_KEYS_PATH)
        assert "apiClient" in code, "Must import apiClient from shared path"
        assert "apiClient" in code, "Must use shared apiClient for network calls"

    def test_no_raw_fetch(self) -> None:
        code = _read(DEVICE_KEYS_PATH)
        code_no_comments = _strip_comments(code)
        assert not re.search(r"\bfetch\s*\(", code_no_comments), (
            "Must not use raw fetch() — use apiClient"
        )

    def test_no_axios(self) -> None:
        code = _read(DEVICE_KEYS_PATH)
        code_no_comments = _strip_comments(code)
        assert "axios" not in code_no_comments.lower(), (
            "Must not use axios — use apiClient"
        )

    def test_no_localhost(self) -> None:
        code = _read(DEVICE_KEYS_PATH)
        code_no_comments = _strip_comments(code)
        assert "localhost" not in code_no_comments.lower(), (
            "Must not contain localhost — apiClient reads URL from env"
        )

    def test_private_key_never_sent_to_backend(self) -> None:
        """The private key must NEVER be included in any API request."""
        code = _read(DEVICE_KEYS_PATH)
        code_no_comments = _strip_comments(code)
        # The enrollment request should contain public key, not private key
        # Check that the enroll function sends device_public_key (not private)
        assert "device_public_key" in code_no_comments, (
            "Enrollment must send device_public_key"
        )
        # Ensure no private key field is in any API request
        assert "private_key" not in code_no_comments or (
            # Allow in comments about NOT sending it
            "private key" in code.lower() and "never" in code.lower()
        ), "Must not include private_key in API requests"

    def test_public_key_exported_as_der_base64(self) -> None:
        code = _read(DEVICE_KEYS_PATH)
        assert "wrapEcPublicKeyAsDer" in code or "SubjectPublicKeyInfo" in code, (
            "Must wrap public key in DER X.509 SubjectPublicKeyInfo format"
        )
        assert "base64" in code.lower() or "Base64" in code, (
            "Must export public key as base64 for enrollment"
        )

    def test_has_enroll_device_function(self) -> None:
        code = _read(DEVICE_KEYS_PATH)
        assert "enrollDevice" in code, "Must have enrollDevice function"

    def test_has_get_devices_function(self) -> None:
        code = _read(DEVICE_KEYS_PATH)
        assert "getDevices" in code, "Must have getDevices function"

    def test_has_generate_keypair_function(self) -> None:
        code = _read(DEVICE_KEYS_PATH)
        assert "generateDeviceKeypair" in code, "Must have generateDeviceKeypair function"

    def test_has_full_flow_function(self) -> None:
        code = _read(DEVICE_KEYS_PATH)
        assert "generateAndEnrollDevice" in code, (
            "Must have generateAndEnrollDevice for the full enrollment flow"
        )

    def test_uses_correct_enrollment_endpoint(self) -> None:
        code = _read(DEVICE_KEYS_PATH)
        assert "/api/v2/patient/devices/enroll" in code, (
            "Must use correct backend enrollment endpoint"
        )

    def test_uses_correct_devices_list_endpoint(self) -> None:
        code = _read(DEVICE_KEYS_PATH)
        assert "/api/v2/patient/devices" in code, (
            "Must use correct backend devices list endpoint"
        )

    def test_keychain_accessible_device_only(self) -> None:
        code = _read(DEVICE_KEYS_PATH)
        assert "WHEN_UNLOCKED_THIS_DEVICE_ONLY" in code, (
            "Private key must be stored with WHEN_UNLOCKED_THIS_DEVICE_ONLY"
        )

    def test_alpha_labels_present(self) -> None:
        code = _read(DEVICE_KEYS_PATH)
        assert "ALPHA" in code, "Must label as ALPHA"
        # Must not claim hospital-grade security
        code_no_comments = _strip_comments(code)
        assert "hospital-grade" not in code_no_comments.lower(), (
            "Must not claim hospital-grade security"
        )

    def test_honest_limitation_documented(self) -> None:
        """Must use the precise honest ALPHA phrasing."""
        code = _read(DEVICE_KEYS_PATH)
        code_norm = _normalize_ws(code)
        # Exact honest claim required (whitespace-normalized for JSX wrapping)
        assert "P-256 keypair generated client-side" in code_norm, (
            "Must state: P-256 keypair generated client-side"
        )
        assert "private key stored in platform secure storage" in code_norm, (
            "Must state: private key stored in platform secure storage"
        )
        assert "Not yet" in code_norm, (
            "Must state 'Not yet' for the unimplemented capability"
        )
        assert "hardware-backed non-exportable signing key" in code_norm, (
            "Must state the not-yet capability: hardware-backed non-exportable signing key"
        )
        assert "biometric-gated key usage" in code_norm, (
            "Must state the not-yet capability: biometric-gated key usage"
        )

    def test_enroll_params_match_backend(self) -> None:
        """Enrollment params must match DeviceEnrollRequest on the backend."""
        code = _read(DEVICE_KEYS_PATH)
        # Backend expects: device_public_key, device_label, platform
        assert "device_public_key" in code, "Must send device_public_key"
        assert "device_label" in code, "Must send device_label"
        assert "platform" in code, "Must send platform"

    def test_exports_types(self) -> None:
        """Service must export typed interfaces for screens to use."""
        code = _read(DEVICE_KEYS_PATH)
        assert "EnrollDeviceResponse" in code, "Must export EnrollDeviceResponse type"
        assert "DeviceInfo" in code, "Must export DeviceInfo type"
        assert "DevicesListResponse" in code, "Must export DevicesListResponse type"

    def test_has_device_key_check_function(self) -> None:
        """Must provide a way to check if a device key already exists."""
        code = _read(DEVICE_KEYS_PATH)
        assert "hasDeviceKey" in code, "Must have hasDeviceKey function"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SecureDeviceScreen — Enrollment UI
# ═══════════════════════════════════════════════════════════════════════════════


class TestSecureDeviceScreenIntegration:
    """Validate SecureDeviceScreen uses the deviceKeys service correctly."""

    def test_imports_device_keys_service(self) -> None:
        code = _read(SECURE_DEVICE_PATH)
        assert "deviceKeys" in code, (
            "Must import from deviceKeys service"
        )

    def test_uses_generate_and_enroll(self) -> None:
        code = _read(SECURE_DEVICE_PATH)
        assert "generateAndEnrollDevice" in code, (
            "Must use generateAndEnrollDevice for the enrollment flow"
        )

    def test_no_local_keypair_function(self) -> None:
        """The screen should delegate key generation to the service, not define its own."""
        code = _read(SECURE_DEVICE_PATH)
        code_no_comments = _strip_comments(code)
        # Should not define a local generateDeviceKeypairAlpha function
        assert "function generateDeviceKeypairAlpha" not in code_no_comments, (
            "Should not define local keypair generation — delegate to deviceKeys service"
        )

    def test_has_loading_states(self) -> None:
        code = _read(SECURE_DEVICE_PATH)
        assert "generating" in code.lower(), "Must show generating loading state"
        assert "enrolling" in code.lower() or "Registering" in code, (
            "Must show enrollment loading state"
        )

    def test_has_error_handling(self) -> None:
        code = _read(SECURE_DEVICE_PATH)
        assert "error" in code.lower(), "Must handle enrollment errors"
        assert "try" in code, "Must use try/catch for enrollment"

    def test_navigates_to_enrolled_on_success(self) -> None:
        code = _read(SECURE_DEVICE_PATH)
        assert "/patient/enrolled" in code, (
            "Must navigate to DeviceEnrolledScreen on success"
        )

    def test_alpha_labels_present(self) -> None:
        code = _read(SECURE_DEVICE_PATH)
        code_norm = _normalize_ws(code)
        assert "ALPHA" in code_norm, "Must label as ALPHA"
        # Must use precise honest phrasing (whitespace-normalized for JSX wrapping)
        assert "P-256 keypair generated client-side" in code_norm, (
            "Must state: P-256 keypair generated client-side"
        )
        assert "private key stored in platform secure storage" in code_norm, (
            "Must state: private key stored in platform secure storage"
        )
        assert "Not yet" in code_norm, "Must state 'Not yet' for unimplemented capability"
        assert "hardware-backed non-exportable signing key" in code_norm, (
            "Must state not-yet: hardware-backed non-exportable signing key"
        )
        assert "biometric-gated key usage" in code_norm, (
            "Must state not-yet: biometric-gated key usage"
        )

    def test_no_hospital_grade_claims(self) -> None:
        code = _read(SECURE_DEVICE_PATH)
        code_no_comments = _strip_comments(code)
        assert "hospital-grade" not in code_no_comments.lower(), (
            "Must not claim hospital-grade security"
        )

    def test_uses_tamagui_only(self) -> None:
        code = _read(SECURE_DEVICE_PATH)
        assert "from 'tamagui'" in code, "Must use Tamagui components"
        assert "<div" not in code, "Must not use HTML div"
        assert "<button" not in code, "Must not use HTML button"

    def test_uses_shared_apiclient(self) -> None:
        # The screen delegates to deviceKeys service which uses apiClient
        # The screen itself might not import apiClient directly — that's OK
        # as long as the service does
        device_keys_code = _read(DEVICE_KEYS_PATH)
        assert "apiClient" in device_keys_code, (
            "Service must use shared apiClient"
        )

    def test_no_raw_fetch(self) -> None:
        code = _read(SECURE_DEVICE_PATH)
        code_no_comments = _strip_comments(code)
        assert not re.search(r"\bfetch\s*\(", code_no_comments), (
            "Must not use raw fetch()"
        )

    def test_no_axios(self) -> None:
        code = _read(SECURE_DEVICE_PATH)
        assert "axios" not in code.lower(), "Must not use axios"

    def test_no_localhost(self) -> None:
        code = _read(SECURE_DEVICE_PATH)
        code_no_comments = _strip_comments(code)
        assert "localhost" not in code_no_comments.lower(), (
            "Must not contain localhost"
        )

    def test_no_hardcoded_patient_id(self) -> None:
        code = _read(SECURE_DEVICE_PATH)
        code_no_comments = _strip_comments(code)
        assert "patient_id" not in code_no_comments, (
            "Must not hardcode patient_id — comes from JWT via apiClient"
        )

    def test_secure_this_device_button(self) -> None:
        code = _read(SECURE_DEVICE_PATH)
        assert "Secure This Device" in code, "Must have Secure This Device button"

    def test_explains_why_securing_matters(self) -> None:
        code = _read(SECURE_DEVICE_PATH)
        # Must explain the purpose of device security
        assert "health data" in code.lower() or "protect" in code.lower(), (
            "Must explain why securing the device matters"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. DeviceEnrolledScreen — Success + trusted status
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeviceEnrolledScreenIntegration:
    """Validate DeviceEnrolledScreen shows device status from API."""

    def test_file_exists(self) -> None:
        assert DEVICE_ENROLLED_PATH.exists(), "DeviceEnrolledScreen.tsx must exist"

    def test_fetches_device_status(self) -> None:
        code = _read(DEVICE_ENROLLED_PATH)
        # Must use getDevices from the service or apiClient directly
        assert "getDevices" in code or "/api/v2/patient/devices" in code, (
            "Must fetch device trusted status from API"
        )

    def test_imports_device_keys_service(self) -> None:
        code = _read(DEVICE_ENROLLED_PATH)
        assert "deviceKeys" in code, (
            "Must import from deviceKeys service for getDevices"
        )

    def test_shows_device_label(self) -> None:
        code = _read(DEVICE_ENROLLED_PATH)
        assert "device_label" in code or "deviceLabel" in code, (
            "Must display device label"
        )

    def test_shows_enrolled_status(self) -> None:
        code = _read(DEVICE_ENROLLED_PATH)
        # Must show active/trusted status
        assert "active" in code.lower() or "Trusted" in code, (
            "Must display enrollment status (active/trusted)"
        )

    def test_shows_success_checkmark(self) -> None:
        code = _read(DEVICE_ENROLLED_PATH)
        assert "✅" in code or "checkmark" in code.lower() or "Secured" in code, (
            "Must show success confirmation"
        )

    def test_handles_loading_state(self) -> None:
        code = _read(DEVICE_ENROLLED_PATH)
        assert "loading" in code.lower() or "Spinner" in code, (
            "Must handle loading state while fetching device status"
        )

    def test_handles_error_state(self) -> None:
        code = _read(DEVICE_ENROLLED_PATH)
        # Must handle API fetch failure gracefully
        assert "error" in code.lower() or "statusError" in code, (
            "Must handle error when fetching device status"
        )

    def test_uses_tamagui_only(self) -> None:
        code = _read(DEVICE_ENROLLED_PATH)
        assert "from 'tamagui'" in code, "Must use Tamagui components"
        assert "<div" not in code, "Must not use HTML div"

    def test_alpha_labels_present(self) -> None:
        code = _read(DEVICE_ENROLLED_PATH)
        code_norm = _normalize_ws(code)
        assert "ALPHA" in code_norm, "Must label as ALPHA"
        # Must use precise honest phrasing (whitespace-normalized for JSX wrapping)
        assert "P-256 keypair generated client-side" in code_norm, (
            "Must state: P-256 keypair generated client-side"
        )
        assert "private key stored in platform secure storage" in code_norm, (
            "Must state: private key stored in platform secure storage"
        )
        assert "Not yet" in code_norm, "Must state 'Not yet' for unimplemented capability"
        assert "hardware-backed non-exportable signing key" in code_norm, (
            "Must state not-yet: hardware-backed non-exportable signing key"
        )
        assert "biometric-gated key usage" in code_norm, (
            "Must state not-yet: biometric-gated key usage"
        )

    def test_no_hospital_grade_claims(self) -> None:
        code = _read(DEVICE_ENROLLED_PATH)
        code_no_comments = _strip_comments(code)
        assert "hospital-grade" not in code_no_comments.lower(), (
            "Must not claim hospital-grade security"
        )

    def test_no_raw_fetch(self) -> None:
        code = _read(DEVICE_ENROLLED_PATH)
        code_no_comments = _strip_comments(code)
        assert not re.search(r"\bfetch\s*\(", code_no_comments), (
            "Must not use raw fetch()"
        )

    def test_no_axios(self) -> None:
        code = _read(DEVICE_ENROLLED_PATH)
        assert "axios" not in code.lower(), "Must not use axios"

    def test_no_localhost(self) -> None:
        code = _read(DEVICE_ENROLLED_PATH)
        code_no_comments = _strip_comments(code)
        assert "localhost" not in code_no_comments.lower(), (
            "Must not contain localhost"
        )

    def test_no_hardcoded_patient_id(self) -> None:
        code = _read(DEVICE_ENROLLED_PATH)
        code_no_comments = _strip_comments(code)
        assert "patient_id" not in code_no_comments, (
            "Must not hardcode patient_id"
        )

    def test_shows_device_fingerprint(self) -> None:
        code = _read(DEVICE_ENROLLED_PATH)
        assert "fingerprint" in code or "device_id" in code, (
            "Must display device ID fingerprint"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Enrollment success / error flow tests (structural)
# ═══════════════════════════════════════════════════════════════════════════════


class TestEnrollmentFlow:
    """Validate the enrollment flow structure end-to-end."""

    def test_service_generates_keypair_before_enrollment(self) -> None:
        """generateAndEnrollDevice must call generateDeviceKeypair before enrollDevice."""
        code = _read(DEVICE_KEYS_PATH)
        # The generateAndEnrollDevice function should reference
        # generateDeviceKeypair before enrollDevice in source order
        gen_pos = code.find("generateDeviceKeypair", code.find("generateAndEnrollDevice"))
        enroll_pos = code.find("enrollDevice({", code.find("generateAndEnrollDevice"))
        assert gen_pos > 0, "Must call generateDeviceKeypair in generateAndEnrollDevice"
        assert enroll_pos > 0, "Must call enrollDevice in generateAndEnrollDevice"
        assert gen_pos < enroll_pos, (
            "Must generate keypair BEFORE enrolling with backend"
        )

    def test_enroll_sends_only_public_key(self) -> None:
        """The enrollment API call must only include the public key."""
        code = _read(DEVICE_KEYS_PATH)
        code_no_comments = _strip_comments(code)
        # EnrollDeviceParams type must include device_public_key (not private)
        assert "device_public_key" in code_no_comments, "Must define device_public_key in enrollment params"
        # The enrollDevice function passes params as a whole — verify
        # the type interface doesn't include a private key field
        params_section = code_no_comments[
            code_no_comments.find("EnrollDeviceParams"):
            code_no_comments.find("}", code_no_comments.find("EnrollDeviceParams")) + 1
        ]
        assert "private" not in params_section.lower(), (
            "EnrollDeviceParams must NOT include any private key field"
        )
        assert "device_public_key" in params_section, (
            "EnrollDeviceParams must include device_public_key"
        )

    def test_screen_handles_enrollment_error(self) -> None:
        """SecureDeviceScreen must catch errors and show error message."""
        code = _read(SECURE_DEVICE_PATH)
        # Must have try/catch with error state
        assert "catch" in code, "Must have error handling (catch)"
        assert "setError" in code, "Must set error state on failure"
        assert "enrollment failed" in code.lower() or "try again" in code.lower(), (
            "Must show user-friendly error message"
        )

    def test_screen_resets_step_on_error(self) -> None:
        """After an error, the step should reset to 'ready'."""
        code = _read(SECURE_DEVICE_PATH)
        # In the catch block, step should be reset
        catch_match = re.search(r"catch.*?{(.*?)}", code, re.DOTALL)
        if catch_match:
            catch_body = catch_match.group(1)
            assert "ready" in catch_body, "Must reset step to ready on error"

    def test_keypair_reuse_on_existing_key(self) -> None:
        """generateDeviceKeypair should reuse existing key, not regenerate."""
        code = _read(DEVICE_KEYS_PATH)
        # Should check for existing key first
        assert "getItemAsync" in code, "Must check for existing key"
        assert "existing" in code.lower(), "Must handle existing key case"

    def test_delete_key_function_exists(self) -> None:
        """Must provide a way to delete the key (for testing/reset)."""
        code = _read(DEVICE_KEYS_PATH)
        assert "deleteDeviceKey" in code or "deleteItemAsync" in code, (
            "Must have a way to delete device key"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Cross-file consistency
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrossFileConsistency:
    """Ensure service, screens, and API client are consistent."""

    def test_enrollment_endpoint_matches_backend(self) -> None:
        """The enrollment endpoint in deviceKeys.ts must match device_routes.py."""
        device_keys = _read(DEVICE_KEYS_PATH)
        backend_path = ROOT / "app" / "api" / "v2" / "device_routes.py"
        if backend_path.exists():
            backend_code = _read(backend_path)
            # Backend defines: prefix="/api/v2/patient/devices" + "/enroll"
            assert "/api/v2/patient/devices/enroll" in device_keys, (
                "Service enrollment endpoint must match backend route"
            )
            assert "/api/v2/patient/devices" in backend_code, (
                "Backend must have matching route prefix"
            )

    def test_enroll_params_match_backend_schema(self) -> None:
        """Enrollment request fields must match DeviceEnrollRequest on backend."""
        device_keys = _read(DEVICE_KEYS_PATH)
        backend_path = ROOT / "app" / "api" / "v2" / "device_routes.py"
        if backend_path.exists():
            backend_code = _read(backend_path)
            # Backend expects: device_public_key, device_label, platform
            for field in ["device_public_key", "device_label", "platform"]:
                assert field in device_keys, f"Service must include {field} in enrollment"
                assert field in backend_code, f"Backend must accept {field} in request"

    def test_devices_list_endpoint_matches_backend(self) -> None:
        """The devices list endpoint must match backend."""
        device_keys = _read(DEVICE_KEYS_PATH)
        backend_path = ROOT / "app" / "api" / "v2" / "device_routes.py"
        if backend_path.exists():
            assert "/api/v2/patient/devices" in device_keys, (
                "Service must use correct devices list endpoint"
            )

    def test_service_does_not_import_registerDeviceKey(self) -> None:
        """Service must use apiClient, not the old registerDeviceKey import."""
        code = _read(DEVICE_KEYS_PATH)
        assert "registerDeviceKey" not in code, (
            "Must use apiClient, not registerDeviceKey from assurance routes"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Happy-path enrollment payload integration test
# ═══════════════════════════════════════════════════════════════════════════════


class TestEnrollmentHappyPathPayload:
    """Single integration-style test that traces the full enrollment payload
    shape through generateAndEnrollDevice():

        generateDeviceKeypair()
        → produces publicKeyDerBase64
        → calls POST /api/v2/patient/devices/enroll
        → payload contains device_public_key, device_label, platform
        → payload does NOT contain privateKey / private_key / secret
    """

    def test_generate_and_enroll_produces_correct_payload_shape(self) -> None:
        """Trace the full generateAndEnrollDevice flow and validate payload."""
        code = _read(DEVICE_KEYS_PATH)
        code_no_comments = _strip_comments(code)

        # ── Step 1: generateDeviceKeypair produces publicKeyDerBase64 ──────
        # The function must return a DeviceKeyResult with publicKeyDerBase64
        gen_func_start = code.find("async function generateDeviceKeypair")
        assert gen_func_start > 0, "generateDeviceKeypair function must exist"

        # Must produce DER-wrapped base64 public key
        assert "publicKeyDerBase64" in code, (
            "generateDeviceKeypair must produce publicKeyDerBase64"
        )
        assert "wrapEcPublicKeyAsDer" in code, (
            "Must wrap public key in DER (SubjectPublicKeyInfo) before base64"
        )

        # ── Step 2: generateAndEnrollDevice wires the result into enrollDevice ─
        full_flow_start = code.find("async function generateAndEnrollDevice")
        assert full_flow_start > 0, "generateAndEnrollDevice function must exist"

        # Must destructure publicKeyDerBase64 from generateDeviceKeypair result
        full_flow_body = code[
            full_flow_start:code.find("\n}", full_flow_start + 10) + 2
        ]
        assert "publicKeyDerBase64" in full_flow_body, (
            "generateAndEnrollDevice must use publicKeyDerBase64 from keypair result"
        )

        # ── Step 3: enrollDevice sends correct endpoint ────────────────────
        assert "/api/v2/patient/devices/enroll" in full_flow_body or "/api/v2/patient/devices/enroll" in code_no_comments, (
            "Must call POST /api/v2/patient/devices/enroll"
        )

        # ── Step 4: Payload contains required fields ───────────────────────
        # The enrollDevice call receives EnrollDeviceParams which must have:
        # device_public_key, device_label, platform
        params_section = code_no_comments[
            code_no_comments.find("interface EnrollDeviceParams"):
            code_no_comments.find("}", code_no_comments.find("interface EnrollDeviceParams")) + 1
        ]
        assert "device_public_key" in params_section, (
            "EnrollDeviceParams must contain device_public_key"
        )
        assert "device_label" in params_section, (
            "EnrollDeviceParams must contain device_label"
        )
        assert "platform" in params_section, (
            "EnrollDeviceParams must contain platform"
        )

        # ── Step 5: Payload does NOT contain private key fields ────────────
        # None of these should appear in the enrollment params or the
        # enrollDevice function body
        enroll_func_start = code_no_comments.find("async function enrollDevice")
        enroll_func_end = code_no_comments.find(
            "\n}", enroll_func_start + 10,
        ) + 2
        enroll_body = code_no_comments[enroll_func_start:enroll_func_end]

        forbidden_fields = ["privatekey", "private_key", "secret", "secretkey"]
        for forbidden in forbidden_fields:
            assert forbidden not in enroll_body.lower(), (
                f"enrollDevice body must NOT contain '{forbidden}' — "
                f"the private key never leaves the device"
            )
            assert forbidden not in params_section.lower(), (
                f"EnrollDeviceParams must NOT contain '{forbidden}' — "
                f"the private key never leaves the device"
            )

        # Also check the generateAndEnrollDevice function body
        for forbidden in forbidden_fields:
            assert forbidden not in full_flow_body.lower(), (
                f"generateAndEnrollDevice must NOT contain '{forbidden}' — "
                f"the private key never leaves the device"
            )

        # ── Step 6: device_public_key is the ONLY key field sent ───────────
        # In the generateAndEnrollDevice call to enrollDevice, the payload
        # must pass publicKeyDerBase64 as device_public_key
        assert "device_public_key: publicKeyDerBase64" in full_flow_body, (
            "Must pass publicKeyDerBase64 as device_public_key to enrollDevice"
        )


def test_legacy_register_device_key_is_deprecated_not_canonical() -> None:
    """Legacy biometric_registry registration must not be the flagship device-key path."""
    assurance_source = Path("nexa-client/packages/app/api/assurance.ts").read_text()
    consent_source = Path("app/api/v2/consent_routes.py").read_text()
    verifier_source = Path("app/services/signed_approval_verifier.py").read_text()

    assert "@deprecated Device enrollment belongs to services/deviceKeys.ts" in assurance_source
    assert "throw new Error('Use the canonical deviceKeys enrollment service.')" in assurance_source
    assert "PatientDeviceKey" in consent_source
    assert "PatientDeviceKey" in verifier_source
    assert "biometric_registry" not in consent_source
