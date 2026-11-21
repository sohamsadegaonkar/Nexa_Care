# Non-NFC Android local preflight — 21 November 2026

**Scope:** optional read-only developer prerequisite check on the draft investor-demo branch. **Not:** proof that physical Android signing, clinician-browser-to-real-API, NFC, Azure or independent reviews have passed.

The current owner Android handset lacks NFC. The clinician UI supports an exact `NEXA_PUBLIC_ID` patient lookup and a strict versioned `QR_PUBLIC_ID` fallback. These paths return a bounded opaque discovery handle; **neither discovery nor a passing preflight grants clinical data access**. Routine V3 signed history consent and a separate patient-signed original-document decision are still mandatory. Phone discovery is optional and subject to opt-in/verification/current MFA; it is not used here.

## Prerequisites

- Use the **exact current [PR #101](https://github.com/sohamsadegaonkar/Nexa_Care/pull/101) head** and a *private, disposable, synthetic-only* environment. No real patient data, no public development endpoint or shared production services.
- Backend dependencies: isolated PostgreSQL and Redis; separate Alembic migration runner using the exact single repository head; real malware scanner with fresh definitions, patient-owned encrypted document storage, synthetic Hospital X report and a demo Hospital Y provider with proper identity/affiliation and MFA.
- Physical Android: development build configured for the private backend and a truly enrolled test key, not a software fixture passed off as hardware proof. For Windows USB/ADB, an authorized connected Android device and `adb reverse tcp:8000 tcp:8000` / `adb reverse tcp:8081 tcp:8081` may enable loopback development. Otherwise configure a **trusted private LAN address**, never a public tunnel. Expo client `EXPO_PUBLIC_API_URL` must match the device's actual network path.

## Read-only checks

From repository root in an active Python environment with project dependencies installed, set up the ignored local environment using the repository's normal development process, then run:

```bash
python -m scripts.demo_preflight --discovery public-id
```

The preflight rejects non-demo environments; inspects the active Alembic revision, provider tables, Redis, clinical eligibility, both synthetic patients' exact Public IDs and erasure state. It **does not** require the NFC demo-card row for the public-ID route. It makes **no migrations, data writes, cloud calls or device-signature requests**.

Expected, if these prerequisites truly pass:
```text
discovery_mode=public-id
nfc_registry_check=NOT_REQUIRED_FOR_PUBLIC_ID_DISCOVERY
physical_nfc_gate=NOT_RUN_NO_NFC_HARDWARE
patient_device_signature_gate=NOT_RUN
clinician_browser_real_api_gate=NOT_RUN
release_gate=HOLD
preflight=NON_NFC_PREREQUISITES_READY
```

Failure emits `preflight=NO-GO` and exits nonzero. Never print the private database credentials or synthetic signing keys in logs. The preflight may print the **synthetic public patient IDs** for use in a manual, exact-ID clinician lookup, but not device tokens or patient medical contents.

For the existing Windows development launcher, use:
```powershell
.\scripts\start_nexa_dev.ps1 -Discovery PublicId -NoBrowser
```
Do not pass `-Restart` unless it is acceptable to stop the already listening processes on the development machine. The launcher is designed for a locally configured Windows Android development workstation; it neither enrolls a device nor guarantees each child service successfully reaches its ready state. Its non-NFC status is **prerequisites only**, not a demo release GO.

The legacy `--discovery nfc` (default) retains the demo NFC registry requirement for NFC-path prereqs, but even that is **not evidence of a physical tap**.

## Next manual execution — NOT_RUN

1. Check `adb devices -l` shows the authorized physical handset and verify the exact app build SHA/device key assurance category. ADB connection alone is not device-key or biometric evidence.
2. Sign in to the patient app with a synthetic patient and an actually enrolled phone signing key; upload, review and save the repo's bundled `docs/demo/synthetic-hospital-x-report.pdf`. Do not use a stale dummy `s3://` document reference as an actual original.
3. In a **real browser connected to the actual local backend**, authenticate the synthetic Hospital Y clinician with MFA; select **Public ID** in Patient Discovery and enter the exact synthetic Nexa ID. Confirm only opaque handle is returned pending a separate signed V3 clinical-history grant.
4. Verify denial of original bytes before separate P1B patient signature; after signed approval, require byte-exact single opening, second attempt denied and one opening audit event.
5. Revoke/expire/disconnect while the browser preview is displayed and record closure within the eight-second lease. Never claim a screenshot of a mock API proves this session.
6. Record exact SHA, hardware/key attestation, Android OS, Chrome version, private runtime and scanner freshness, times, redacted captures and pass/fail/blocked per [investor QA matrix](INVESTOR_DEMO_QUALIFICATION_20261121.md).

**Still blocked until independently observed:** actual phone P-256 signing/biometric, NFC physical test (requires other NFC-capable device), connected browser-to-API/revocation, approved live Azure, independent clinical/security/privacy review. Do not merge PRs #99/#101 or call the investor demo ready on the strength of preflight/CI alone.

## Security and regulatory impact

References [SEC-001/002/003/011/013/016/020/023](../governance/SECURITY_NON_REGRESSION.md), [REG-003](../governance/INDIA_REGULATORY_BASELINE.md), [discovery security](../governance/SLICE_10A_DISCOVERY_SECURITY_NON_REGRESSION.md), [P1B](../governance/P1B_ORIGINAL_DOCUMENT_ACCESS.md), and [engineering constitution](../governance/NEXA_CARE_ENGINEERING_CONSTITUTION.md). Only read-only prerequisite selection, local launcher flag, CI unit test and documentation change. No changes to clinical data, consent/access/identity authority, audit/event semantics, extraction, storage, erasure implementation, emergency scope or regulatory policy. No migration is added or run.
