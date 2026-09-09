# Slice 6I — Physical Pilot Qualification

Status: **BLOCKED BY PHYSICAL PLATFORM**.

Baseline: merged Slice 6H `9782d208f9330d9dcc4ba63af31d9a5bee7877db`.

## Objective

Slice 6I is the physical-platform qualification boundary for the native patient authority implemented in Slice 6H. It exists to collect reproducible evidence from an actual Android or iOS device without weakening, extrapolating, or re-labeling CI evidence as physical evidence.

## Current state

No fresh, reproducible physical-device qualification artifact is present in the repository for the Slice 6H native key boundary. The historical `docs/real-phone-test-report.md` explicitly states that its prior real-phone claims are not supported by reproducible repository evidence and require a new manual run. It is therefore not accepted as Slice 6I PASS evidence.

The committed evidence manifest starts in `BLOCKED_BY_PHYSICAL_PLATFORM` / `NOT_RUN` state. This blocker may be changed only after a genuine physical run records the exact build SHA, physical device/OS, native custody metadata, required scenario results, sanitized evidence hashes, and audit identifiers.

## Evidence boundary

The following are useful qualification prerequisites but are **not** physical proof:

- GitHub-hosted Android Gradle/Kotlin compilation;
- GitHub-hosted iOS prebuild, CocoaPods, or simulator compilation;
- JavaScript/unit/integration tests;
- source inspection of Secure Enclave, Android Keystore, or StrongBox code paths;
- a simulator/emulator run;
- a rehearsal checklist, screenshots without reproducible provenance, or a stale demo report.

Physical PASS requires an actual device and a manifest accepted by `scripts/validate_slice6i_physical_evidence.py`.

## Required physical scenarios

The physical run must execute all of these stable test IDs:

- `6I-PHY-001-native-key-custody` — generate/read/sign using the native key handle and record only sanitized custody metadata plus the SHA-256 public-key fingerprint;
- `6I-PHY-002-signed-consent-v3` — complete a Signed Consent V3 approval using the physical device key;
- `6I-PHY-003-key-rotation` — rotate authority and prove the replaced key is no longer current/usable for new authority;
- `6I-PHY-004-trusted-device-revocation` — server-first revoke a trusted device and verify subsequent authority is denied;
- `6I-PHY-005-account-recovery` — recover to fresh authority without reconstructing an old private key and verify old authority remains invalid;
- `6I-PHY-006-session-logout` — verify server-first logout/session invalidation on the physical client;
- `6I-PHY-007-fail-closed-network` — verify the physical client does not invent authority when required server/Redis-backed authority cannot be confirmed.

## Native custody claims

The native bridge exposes custody metadata but does not itself prove what a particular device executed. Slice 6I may record only what the running platform reports:

- `ios-secure-enclave` requires `hardware_backed=true`;
- `android-strongbox` requires `hardware_backed=true` and `strongbox_backed=true`;
- `android-keystore-hardware` requires `hardware_backed=true`;
- `android-keystore` is not to be relabeled as StrongBox or hardware-backed evidence.

No private key, raw public DER, signature, bearer token, cookie, patient/provider identifier, phone/email, database URL, Redis URL, credential, or secret belongs in repository evidence.

## Qualification mechanics

The machine-checkable record is `docs/qualification/SLICE_6I_PHYSICAL_EVIDENCE.json`. The validator intentionally accepts the committed blocked state, but it will reject a PASS claim unless all physical prerequisites and evidence fields are present and internally consistent.

Harness CI passing means only: **the 6I evidence contract and guardrails are qualified**. It does not change the physical status while the manifest remains blocked.

## Nonclaims

Slice 6I currently does not claim physical Secure Enclave execution, physical Android hardware-backed Keystore execution, StrongBox execution, physical NFC behavior, end-to-end push delivery on a real handset, or a completed physical pilot. It does not claim Nexa Care is "fully secure".
