# Nexa Care — Sprint Plan Verification & Loose-End Fixes

I read the actual code (not test names or commit messages) against every item in the team sprint plan. Summary: **the team did real, substantive work** — most of it holds up under direct inspection. I found and fixed the loose ends below rather than just flagging them.

## Verified DONE, holds up under inspection

- **Consent engine consolidation** — `nexa_consent_engine.py` is gone. `consent_engine.py` now has `issue_routine`, `issue_break_glass`, `issue`, `validate`, `consume`, `revoke`, all on the Redis-fast-path + Postgres-durable design. Break-glass has a hard-enforced 15-minute TTL and requires a non-empty `reason_code`.
- **Server-side assurance verification** — real, and better than the plan asked for. `assurance_verifier.py` doesn't just check a label; for `PUSH_BIOMETRIC` it looks up the actual Redis record, confirms `status == "approved"`, confirms the patient_id matches, enforces the 90-second window, and atomically deletes the record on read (single-use). A caller cannot forge assurance level by lying in the request body.
- **crypto_kms.py** — exists, with the exact swappable-provider shape recommended (`EncryptionProvider` → `LocalEnvelopeProvider` now, `KMSProvider` stubbed for later). Per-patient DEK generation, rotation, and `destroy_dek` (cryptographic erasure) are all implemented.
- **Biometric signature verification (backend)** — genuinely solid. Real ECDSA-P256 verification, nonce replay protection, timing-attack padding, and it correctly raises `PatientDataErased` if a patient's DEK was destroyed rather than silently failing.
- **Push request/respond/status split** — implemented correctly, plus a websocket option behind a feature flag, plus a `PatientApprovalScreen.tsx` that exists as a genuinely separate screen from the doctor's scanner (real architectural fix, not just a rename).
- **Doctor's scanner screen no longer self-resolves approval** — confirmed; it only calls `request` and polls `status`.
- **Tombstone fields surfaced in `useNfcScanner.ts`** — confirmed; `canonical_patient_id` and `is_redirected` are now read and exposed.
- **MergeAdminScreen fresh MFA challenge** — implemented as Option A (fresh `challenge_token` + TOTP), matching the recommendation.
- **Break-glass revoke endpoint** — exists (`/break-glass/revoke` in `consent_routes.py`).

## Loose ends found and fixed this pass

**1. The flagship gap: mock signature, no real enrollment path.**
`PatientApprovalScreen.tsx` called real Face ID via `expo-local-authentication`, then sent a hardcoded string (`sig_v1_${nonce}_signed`) as the "signature." The backend's real ECDSA verifier would correctly reject this — meaning the flagship real-approval flow could only ever succeed for manually-seeded test data, never a real patient. Tracing further: no route anywhere accepted a `device_public_key` from a patient at all; the only code path that ever wrote one was a batch migration script for pre-existing plaintext keys.

*Fixed:* Added `update_device_public_key()` to `biometric_registry.py` (patient-scoped, update-only — it cannot create a fresh biometric binding, only attach a key to one a clinician already enrolled in person). Added `POST /api/v2/push/register-device-key`. Built `deviceKey.ts` on the client: generates a real P-256 keypair with `@noble/curves`, stores the private key via `expo-secure-store` (OS keystore-backed), and signs the actual challenge (`nonce + request_id + patient_id`, SHA-256, DER-encoded ECDSA) matching the backend's verifier byte-for-byte. Wired `PatientApprovalScreen.tsx`'s approve action to this real signer.

**2. A second, independent bug: wrong URL prefix.**
`assurance.ts` called `/api/v2/assurance/push/...`. The actual router is mounted at `/api/v2/push/...` (confirmed against `main.py`'s `include_router` call, no extra prefix). Every push-approval call from the app would have 404'd, entirely separate from the signature issue. Fixed — all three call sites corrected, with a comment explaining why, so it doesn't silently drift back.

**3. A third bug in the same call site: mismatched field names.**
`scanner/screen.tsx` sent `patient_uuid` / `clinician_name` / `hospital_name`, but the backend's `PushRequestPayload` schema expects `patient_id` / `provider_id` / `purpose` / `scope`. This would have been a 422 even with the URL fixed. Fixed the field names — **but flagging honestly**: `provider_id` is currently a placeholder string, because this screen has no real provider-session identity available to it yet (auth is bearer-token-only; no decoded provider profile is threaded through the app). This isn't a security hole (the backend independently authenticates the provider via the bearer token for the actual authorization decision), but it should be wired to a real value before a demo, since right now it's a well-formed lie that won't fail loudly.

**4. Missing/mismatched dependencies.**
`expo-local-authentication` was imported and used in `PatientApprovalScreen.tsx` but never declared in any `package.json` — would fail at install time. Added it, along with `@noble/curves`, `expo-secure-store`, and `expo-crypto` for the new signing code.

## What I could not verify here

I don't have a way to run a native Expo build or a physical/simulated device with biometric hardware in this environment, and dependency installation for the Python test suite hit version conflicts I didn't chase down given the scope of this pass. Everything above was verified by direct code reading and static syntax checks, not by executing the test suite or an on-device run. **Before the demo, run this on a real device**: enroll a device key, then run a full approve/deny cycle, and confirm the backend actually accepts the signature — that's the one thing that genuinely cannot be confirmed from here.

## Files changed (attached)
- `app/services/biometric_registry.py` — added `update_device_public_key()`
- `app/api/v2/assurance_routes.py` — added `POST /register-device-key`
- `nexa-client/packages/app/api/assurance.ts` — fixed URL prefix, added `registerDeviceKey()`
- `nexa-client/packages/app/utils/deviceKey.ts` — new file, real EC key generation + signing
- `nexa-client/packages/app/features/approval/PatientApprovalScreen.tsx` — real signature, not mock
- `nexa-client/packages/app/features/scanner/screen.tsx` — fixed field names in push request payload
- `nexa-client/packages/app/package.json` — added the four new dependencies

## One thing you'll need to decide, not just implement
`enrollDeviceKey()` exists but isn't called from anywhere yet — I didn't invent a UI location for it (first login? a settings screen?) because guessing wrong and burying it somewhere unintuitive is worse than leaving it visible as a decision. It needs to run once per device, after provider-led NFC enrollment, before the first real approval attempt.