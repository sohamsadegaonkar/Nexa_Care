# Slice 6H — Mobile / Native Patient Authority

Status: **implementation complete; exact-head qualification pending; no physical-device qualification claimed**.

Baseline: merged Slice 6G `bfde0c0879ef73c4e9fdfd1ccc9f862d482f13e3`.

Implementation checkpoint before this qualification-trigger commit: `63cff86f65b919f9d20ff03d9453c67c219b902a`.

## Security objective

Move patient signing authority away from JavaScript-readable raw private-key material and into native platform key handles while preserving the already-qualified P-256 public-key, rotation, recovery, and Signed Consent V3 server contracts.

## Native key module

The Expo app contains a local Expo module named `NexaDeviceSecurity`.

- iOS creates P-256 keys with `kSecAttrTokenIDSecureEnclave`, stores the private key as a permanent Keychain/Secure Enclave object, exports only the public key, and signs with `ecdsaSignatureMessageX962SHA256`.
- Android creates P-256 keys in `AndroidKeyStore`, requests StrongBox when the platform supports it, falls back to Android Keystore when StrongBox allocation is unavailable, rejects an exportable private-key object, and signs with `SHA256withECDSA`.
- Android reports `hardwareBacked` from `KeyInfo.isInsideSecureHardware`; StrongBox is reported only when API-level key metadata says the security level is StrongBox.
- The JavaScript bridge accepts only aliases and signing messages and returns public DER, DER ECDSA signatures, and non-secret custody metadata. It has no method that exports private-key material.

This source-level design does **not** by itself prove that any particular physical Android device used StrongBox/TEE or that any particular iPhone executed a Secure Enclave operation. That requires physical-platform evidence in Slice 6I.

## Existing legacy state

The pre-6H client stored a Base64 raw P-256 scalar in `expo-secure-store` and loaded it into JavaScript memory for signing. SecureStore protects a stored value at rest but does not make that value a non-exportable cryptographic key handle. Slice 6H migrates an already-enrolled legacy key using the existing proof-of-possession rotation contract before deleting that legacy scalar; routine signing no longer uses that raw-key path.

## Implemented 6H gates

- new bootstrap enrollment, recovery, Signed Consent V3 signing, and routine rotation route through native key aliases;
- a legacy raw key can authorize exactly one migration rotation to a fresh native replacement before legacy local-key cleanup;
- trusted-device management lists device authority and performs server-first revocation before current-install local key cleanup;
- trusted-device authorization signs the existing server challenge with the current native key handle for a prospective public key;
- account recovery creates fresh pending native authority and promotes it only after the recovery response binds the returned public-key fingerprint/version/status;
- server-first logout remains preserved;
- JavaScript/native contract guardrails reject reintroduction of routine raw P-256 scalar signing;
- Frontend CI includes JavaScript/build qualification plus Android prebuild/Kotlin compilation and iOS prebuild/CocoaPods/simulator compilation.

## Qualification boundary

Exact-head Backend CI and Frontend/native CI are intentionally pending at this checkpoint. The final attestation will record only completed runs attached to the exact qualified implementation head, then will itself receive a separate exact-doc-head qualification run.

Native compilation proves the source integrates and compiles in CI. It does not prove execution on a particular physical Secure Enclave, Android hardware-backed Keystore, or StrongBox device.

No qualified native NFC reader or cross-device QR/NFC key-transfer protocol is implemented in this slice. The existing scanner abstraction is not physical NFC evidence.

## Nonclaims

Slice 6H does not claim that `expo-secure-store` alone is hardware-backed key custody, does not claim physical StrongBox/Secure Enclave execution from source inspection or CI compilation, does not claim physical NFC behavior without a real-device run, does not claim a user-facing cross-device key-transfer protocol that is not implemented, and does not claim Nexa Care is "fully secure".
