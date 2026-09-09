# Slice 6H — Mobile / Native Patient Authority

Status: **implementation in progress; no physical-device qualification claimed**.

Baseline: merged Slice 6G `bfde0c0879ef73c4e9fdfd1ccc9f862d482f13e3`.

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

The pre-6H client stored a Base64 raw P-256 scalar in `expo-secure-store` and loaded it into JavaScript memory for signing. SecureStore protects a stored value at rest but does not make that value a non-exportable cryptographic key handle. 6H must migrate an already-enrolled legacy key using the existing proof-of-possession rotation contract before deleting that legacy scalar.

## Remaining 6H implementation gates

- route new bootstrap enrollment, recovery, consent signing, and routine rotation through native key aliases;
- migrate a legacy raw key by generating a native replacement and authorizing the already-qualified Slice 6D rotation with the still-current legacy key exactly once;
- add trusted-device management UI and server-first local cleanup after revocation;
- integrate a real native NFC reader only if the Expo/native build can compile and qualify it; NFC application payloads must remain opaque and must not contain patient UUIDs or clinical information;
- preserve server-first logout;
- add JavaScript contract tests plus native prebuild/compile qualification;
- keep browser/JS CI separate from physical-hardware evidence.

## Nonclaims

Slice 6H does not claim that `expo-secure-store` alone is hardware-backed key custody, does not claim physical StrongBox/Secure Enclave execution from source inspection or CI compilation, does not claim physical NFC behavior without a real-device run, and does not claim Nexa Care is "fully secure".
