# Slice 6H — Mobile / Native Patient Authority

Status: **implementation-qualified in CI; no physical-device qualification claimed**.

Baseline: merged Slice 6G `bfde0c0879ef73c4e9fdfd1ccc9f862d482f13e3`.

Qualified implementation head: `fdbb7ed24d69a6d38c79fb92cc1168d238bddd3d`.

## Security objective

Move patient signing authority away from JavaScript-readable raw private-key material and into native platform key handles while preserving the already-qualified P-256 public-key, rotation, recovery, and Signed Consent V3 server contracts.

## Native key module

The Expo app contains a local Expo module named `NexaDeviceSecurity`.

- iOS creates P-256 keys with `kSecAttrTokenIDSecureEnclave`, stores the private key as a permanent Keychain/Secure Enclave object, exports only the public key, and signs with `ecdsaSignatureMessageX962SHA256`.
- Android creates P-256 keys in `AndroidKeyStore`, requests StrongBox when the platform supports it, falls back to Android Keystore when StrongBox allocation is unavailable, rejects an exportable private-key object, and signs with `SHA256withECDSA`.
- Android reports `hardwareBacked` from `KeyInfo.isInsideSecureHardware`; StrongBox is reported only when API-level key metadata says the security level is StrongBox.
- The JavaScript bridge accepts only aliases and signing messages and returns public DER, DER ECDSA signatures, and non-secret custody metadata. It has no method that exports private-key material.

This source-level design and CI compilation do **not** prove that any particular physical Android device used StrongBox/TEE or that any particular iPhone executed a Secure Enclave operation. Physical-platform evidence belongs to Slice 6I.

## Client authority invariants

- routine client signing does not use a JavaScript-readable P-256 private scalar;
- routine enrollment, Signed Consent V3 signing, rotation, and recovery use native key aliases;
- only public-key material and non-secret custody metadata cross the native bridge/server boundary;
- the pre-6H SecureStore raw-key path is isolated to one-time proof-of-possession migration and is deleted after a successful native-key replacement;
- recovery creates fresh pending native authority and promotes it only after the server response binds the returned fingerprint, version, and active status;
- revoking the current installation is server-first: backend revocation succeeds before local native authority is deleted;
- trusted-device authorization signs the existing server challenge with the current native key handle for the prospective public key;
- server-first logout remains preserved.

## Qualification evidence

Exact implementation head: `fdbb7ed24d69a6d38c79fb92cc1168d238bddd3d`.

### Backend CI #362 — run `34324201568`

All three partitions completed successfully and all JUnit zero-skip assertions passed.

- Partition A / Quality & Pure Unit: `3596 passed, 396 deselected`; failures `0`, errors `0`, skipped `0`; Ruff: `All checks passed!`.
- Partition B / PostgreSQL Qualification: `272 passed, 3720 deselected`; failures `0`, errors `0`, skipped `0`; disposable PostgreSQL migrated to `20260909_device_trust_lifecycle`.
- Partition C / PostgreSQL + Redis Qualification: `124 passed, 3868 deselected`; failures `0`, errors `0`, skipped `0`; real PostgreSQL and Redis service containers were used.

Slice 6H adds no Alembic migration. The schema head remains `20260909_device_trust_lifecycle`.

### Frontend CI #311 — run `34324201555`

All three jobs completed successfully.

- JavaScript/build job: root Next tests `6 passed`; application/workspace suite `219 passed`; Next production build succeeded; workspace package build succeeded.
- Android native job: Expo Android prebuild succeeded and `:app:compileDebugKotlin` succeeded on Java 17.
- iOS native job: Expo iOS prebuild succeeded, CocoaPods installation succeeded, and an unsigned Debug iPhone-simulator `xcodebuild` succeeded.

These native jobs are compile/integration evidence only. They are not physical Secure Enclave, Android hardware-backed Keystore, StrongBox, biometric, NFC, or real-phone execution evidence.

## Guardrail repair provenance

A temporary one-use workflow used to migrate legacy source assertions initially failed closed before modifying tests because its expected snippet did not match. A hardened retry then migrated only the named guardrails and self-deleted. A second one-use repair corrected generated indentation, validated Python syntax, and self-deleted in its resulting commit. Both temporary workflow files are absent from the qualified tree.

The final guardrails reject reintroduction of routine raw P-256 scalar generation/signing and assert the native P-256/SHA-256 custody boundary instead of preserving the obsolete SecureStore-scalar architecture.

## NFC and physical-platform boundary

No qualified native NFC reader or cross-device QR/NFC key-transfer protocol is implemented in Slice 6H. The existing scanner abstraction and NFC-resolution contract tests are not evidence that a physical NFC controller was exercised.

Physical qualification therefore remains separate. Slice 6I must record real device/OS/build evidence if such hardware is available; otherwise its physical execution status must remain **BLOCKED BY PHYSICAL PLATFORM**.

## Nonclaims

Slice 6H does not claim that `expo-secure-store` alone is hardware-backed key custody; does not claim physical StrongBox, TEE, Android hardware-backed Keystore, Secure Enclave, biometric, or NFC execution from source inspection or CI compilation; does not claim a user-facing cross-device key-transfer protocol that is not implemented; and does not claim Nexa Care is "fully secure".
