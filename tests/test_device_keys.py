"""Slice 6H patient-device client guardrails.

These tests intentionally reject the pre-6H architecture where routine signing keys were raw
P-256 scalars in expo-secure-store. SecureStore remains appropriate for opaque aliases, session
material, and the one-time legacy migration key, but routine private-key operations must terminate
inside the Nexa native module.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "nexa-client" / "packages" / "app" / "services"
FEATURES = ROOT / "nexa-client" / "packages" / "app" / "features" / "patient"
EXPO = ROOT / "nexa-client" / "apps" / "expo"

DEVICE_KEYS = SERVICES / "deviceKeys.ts"
LEGACY_KEY = SERVICES / "legacyDeviceKey.ts"
NATIVE_SECURITY = SERVICES / "nativeDeviceSecurity.ts"
NATIVE_KEYRING = SERVICES / "nativeDeviceKeyring.ts"
CURRENT_DEVICE = SERVICES / "currentDeviceEnrollment.ts"
ROTATION = SERVICES / "patientDeviceRotation.ts"
NATIVE_RECOVERY = SERVICES / "patientNativeRecovery.ts"
MANAGEMENT = SERVICES / "patientDeviceManagement.ts"
SECURE_DEVICE = FEATURES / "SecureDeviceScreen.tsx"
TRUSTED_DEVICES = FEATURES / "TrustedDevicesScreen.tsx"
RECOVERY_SCREEN = FEATURES / "PatientRecoveryScreen.tsx"


def _read(path: Path) -> str:
    assert path.exists(), f"missing required Slice 6H file: {path}"
    return path.read_text(encoding="utf-8")


def test_canonical_device_service_has_no_routine_raw_private_key_crypto() -> None:
    code = _read(DEVICE_KEYS)
    assert "@noble/curves" not in code
    assert "p256.sign(" not in code
    assert "generateDeviceKeypair" not in code
    assert "signConsentChallenge" not in code
    assert "device_public_key" in code
    assert "/api/v2/patient/devices/enroll" in code
    assert "/api/v2/patient/devices" in code


def test_legacy_scalar_access_is_isolated_to_one_time_rotation_migration() -> None:
    code = _read(LEGACY_KEY)
    assert "expo-secure-store" in code
    assert "DEVICE_PRIVATE_KEY_STORAGE_KEY" in code
    assert "signLegacyDeviceMessage" in code
    assert "deleteLegacyDevicePrivateKey" in code
    assert "Legacy raw-key access is deliberately isolated to one-time 6H rotation migration" in code
    assert "generateDeviceKeypair" not in code


def test_native_security_bridge_exposes_alias_operations_not_private_export() -> None:
    code = _read(NATIVE_SECURITY)
    assert "generateNativeDeviceKey" in code
    assert "getNativeDeviceKey" in code
    assert "signWithNativeDeviceKey" in code
    assert "deleteNativeDeviceKey" in code
    assert "privateKey" not in code
    assert "exportPrivate" not in code


def test_native_keyring_persists_only_aliases() -> None:
    code = _read(NATIVE_KEYRING)
    assert "DEVICE_NATIVE_KEY_ALIAS_STORAGE_KEY" in code
    assert "DEVICE_PENDING_NATIVE_KEY_ALIAS_STORAGE_KEY" in code
    assert "ensurePendingNativeDeviceKey" in code
    assert "commitPendingNativeDeviceKey" in code
    assert "clearCurrentNativeDeviceKey" in code
    assert "publicKeyDerBase64" in code
    assert "privateKey" not in code


def test_bootstrap_and_reconciliation_use_pending_native_authority() -> None:
    code = _read(CURRENT_DEVICE)
    assert "ensurePendingNativeDeviceKey" in code
    assert "commitPendingNativeDeviceKey" in code
    assert "reconcilePendingDevice" in code
    assert "public_key_fingerprint" in code
    assert "legacy-secure-store" in code
    assert "migrateLegacyDeviceToNative" in code
    assert "DEVICE_RECOVERY_REQUIRED" in code


def test_legacy_migration_and_routine_rotation_use_6d_pop_contract() -> None:
    code = _read(ROTATION)
    assert "/rotation/challenge" in code
    assert "/rotate" in code
    assert "current_key_version" in code
    assert "new_public_key_fingerprint" in code
    assert "signLegacyDeviceMessage" in code
    assert "signWithNativeDeviceKey" in code
    assert "DEVICE_ROTATION_CHALLENGE_BINDING_MISMATCH" in code
    assert "DEVICE_ROTATION_RESPONSE_BINDING_MISMATCH" in code


def test_recovery_creates_fresh_native_authority_and_binds_server_response() -> None:
    code = _read(NATIVE_RECOVERY)
    assert "ensurePendingNativeDeviceKey" in code
    assert "new_device_public_key" in code
    assert "public_key_fingerprint" in code
    assert "DEVICE_RECOVERY_RESPONSE_BINDING_MISMATCH" in code
    assert "storePatientAuthSession" in code
    assert "commitPendingNativeDeviceKey" in code
    assert "deleteLegacyDevicePrivateKey" in code


def test_trusted_device_management_is_server_first_for_revocation() -> None:
    code = _read(MANAGEMENT)
    assert code.index("NexaApiClient.revokeDevice") < code.index("clearCurrentNativeDeviceKey")
    assert "rotateCurrentNativeDeviceKey" in code
    assert "isCurrentInstallation" in code


def test_mobile_screens_route_through_authoritative_services() -> None:
    secure = _read(SECURE_DEVICE)
    managed = _read(TRUSTED_DEVICES)
    recovery = _read(RECOVERY_SCREEN)
    assert "ensureCurrentDeviceEnrollment" in secure
    assert "listManagedPatientDevices" in managed
    assert "revokeManagedPatientDevice" in managed
    assert "rotateCurrentManagedDevice" in managed
    assert "completeNativePatientRecovery" in recovery
    assert "new_device_public_key" not in recovery


def test_native_module_sources_exist_for_both_platforms() -> None:
    android = EXPO / "modules" / "nexa-device-security" / "android" / "src" / "main" / "java" / "ai" / "nexacare" / "devicesecurity" / "NexaDeviceSecurityModule.kt"
    ios = EXPO / "modules" / "nexa-device-security" / "ios" / "NexaDeviceSecurityModule.swift"
    assert android.exists()
    assert ios.exists()
    android_code = _read(android)
    ios_code = _read(ios)
    assert "AndroidKeyStore" in android_code
    assert "setIsStrongBoxBacked" in android_code
    assert "isInsideSecureHardware" in android_code
    assert "kSecAttrTokenIDSecureEnclave" in ios_code
    assert "ecdsaSignatureMessageX962SHA256" in ios_code


def test_expo_routes_expose_recovery_and_device_management() -> None:
    assert (EXPO / "app" / "patient" / "devices.tsx").exists()
    assert (EXPO / "app" / "patient" / "recovery.tsx").exists()
