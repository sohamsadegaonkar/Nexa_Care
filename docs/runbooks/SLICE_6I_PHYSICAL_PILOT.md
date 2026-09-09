# Slice 6I — Physical Pilot Runbook

This runbook is for a fresh physical qualification of the Slice 6H native patient-authority boundary. Until an actual handset run produces reproducible evidence, the governance status remains **BLOCKED BY PHYSICAL PLATFORM**.

## 1. Immutable inputs

1. Start from the reviewed Slice 6I branch/commit and record its full 40-character Git SHA.
2. Record the exact backend deployment SHA and the exact mobile build SHA; do not use `latest` or an unpinned branch name as evidence identity.
3. Use only synthetic patient/provider/facility data. Never use real patient PHI/PII for qualification.
4. Current repository Alembic head is `20260909_device_trust_lifecycle`. Stop if the deployed database is not at that exact expected head unless a later reviewed migration has explicitly advanced it.
5. Do not commit deployment domains, account identifiers, role ARNs, database/Redis URLs, tokens, cookies, credentials, secrets, raw public keys, signatures, or private-key material.

## 2. Build the physical client

From the repository root:

```text
cd nexa-client
corepack enable
yarn install --immutable
```

For Android, connect an approved physical device and run the actual workspace script:

```text
yarn workspace expo-app android --device
```

For iOS, connect an approved physical device to a macOS development host and run:

```text
yarn workspace expo-app ios --device
```

Use a development or production native build that contains `NexaDeviceSecurity`; Expo Go is not sufficient for the native key module. Record the build identity and full Git SHA actually installed.

## 3. Pre-run evidence record

Create a working copy of `docs/qualification/SLICE_6I_PHYSICAL_EVIDENCE.json` outside the repository evidence branch while the run is in progress. Record only:

- physical device model and OS version;
- platform (`ios` or `android`);
- exact installed build Git SHA and a non-secret build identifier;
- UTC execution timestamp;
- native custody metadata returned by the running client;
- SHA-256 fingerprint of the public key DER, never the raw DER;
- sanitized evidence artifact SHA-256 hashes;
- non-PII audit event identifiers.

Do not record a patient ID, provider ID, email, phone number, token, cookie, Authorization header, URL containing credentials, signature, raw public key, or private key.

## 4. Native custody check — 6I-PHY-001

On the physical client:

1. Generate or obtain the current native patient signing key through the normal product flow.
2. Confirm the JavaScript-visible structure contains only the alias-mediated public metadata expected by `NativeDeviceKeyInfo`.
3. Perform a normal signing operation through the native key handle.
4. Record the sanitized custody values and public-key SHA-256 fingerprint.

Interpret the running-device metadata literally:

- iOS may claim `ios-secure-enclave` only when the runtime reports hardware-backed Secure Enclave custody.
- Android may claim `android-strongbox` only when both hardware-backed and StrongBox-backed metadata are true.
- Android `android-keystore-hardware` requires hardware-backed metadata.
- Android `android-keystore` must not be promoted to StrongBox/TEE evidence.

A source file or CI compiler result is not a substitute for this physical observation.

## 5. Signed Consent V3 — 6I-PHY-002

Using synthetic identities only:

1. Establish a current authoritative patient session on the physical client.
2. Create a real server-side consent request through the normal provider flow.
3. Approve it on the physical handset using Signed Consent V3 and the current native key handle.
4. Confirm the server accepts the signature, binds it to the expected device/key version, and grants only the requested scope/purpose/duration.
5. Capture only redacted/synthetic screenshots or logs and hash them before adding evidence references.

## 6. Key rotation — 6I-PHY-003

1. Start from an active physical-device key version.
2. Execute normal proof-of-possession rotation to a fresh native key.
3. Confirm the new version becomes current.
4. Confirm the replaced key cannot authorize a new rotation/consent as current authority.
5. Record sanitized audit identifiers and evidence hashes.

## 7. Trusted-device revocation — 6I-PHY-004

1. List trusted devices using the authenticated patient flow.
2. Revoke the physical device server-first.
3. Confirm local cleanup happens only after the server mutation succeeds.
4. Confirm subsequent authority from the revoked device is denied by the server.

## 8. Account recovery — 6I-PHY-005

1. Use the reviewed recovery path grounded in the configured identity provider.
2. Obtain a fresh recovery capability and fresh native key authority on the physical device.
3. Confirm old sessions/keys remain invalid according to recovery policy.
4. Do not reconstruct or export an old private key.

## 9. Logout/session invalidation — 6I-PHY-006

1. Begin with a current authoritative patient session.
2. Perform normal logout from the physical client.
3. Confirm server-side session invalidation occurs before local credential cleanup is treated as success.
4. Confirm the old session cannot call an authority-protected endpoint.

## 10. Fail-closed network/authority check — 6I-PHY-007

Under a controlled synthetic test environment, make the required authority backend unavailable or otherwise prevent authoritative confirmation without modifying production security code. Verify that the physical client does not create, rotate, recover, consent, or preserve server authority merely because a local bearer/key exists. Restore service and verify the normal flow separately.

Do not create a broad outage in a shared environment. The test must be isolated and reversible.

## 11. Evidence finalization

Only after all required scenarios have actually run:

1. Populate the manifest with `execution_status: "EXECUTED"`.
2. Set `qualification_result` to `PASS` only if every required test is `PASS` and all physical evidence prerequisites are present. Otherwise use `FAIL`.
3. Run:

```text
python scripts/validate_slice6i_physical_evidence.py docs/qualification/SLICE_6I_PHYSICAL_EVIDENCE.json
```

4. Review the manifest manually for secrets/PII even after validator success.
5. Commit only the sanitized manifest/evidence hashes and any reviewed redacted artifacts.

A PASS manifest is evidence for the exact recorded device/build/run only. It is not a universal guarantee about every Android/iOS device or every hardware configuration.

## 12. Current blocker

No genuine current-device evidence is available in the repository at Slice 6I start. Do not execute the finalization steps as PASS until an approved physical Android or iOS device is actually available.
