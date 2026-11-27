# Nexa Care Azure staging qualification

**24 November 2026 · Malaysia West synthetic staging · Ready URL pending**

An independent tester records the exact source revision, API/web/scanner
digests, test environment, timestamp and sanitized evidence. Existing
GitHub checks establish disposable CI behavior on their listed commits.
The Azure qualification statuses below apply only to a deployed environment.
The owner reported a successful preflight for the region-adjusted foundation
in `rg-nexacare-staging`. Provider validation and what-if for the reviewed
commit, explicit spending approval and live deployment evidence are required
before qualification.

| Scope | Live Azure status | Acceptance evidence |
| --- | --- | --- |
| Foundation and probes | NOT RUN | Provider validation and deployment readback, private DB/cache/vault, approved ingress, canonical runtime startup, exact migrated head, API liveness/readiness and Next dependency probe. |
| Patient phone login and genuine SMS OTP | NOT RUN | Authorized handset receives a time-bound code on the actual login screen. Verify accepted code and denied wrong, expired and replayed code. Unlisted phone is rejected before sending. |
| Patient identity lifecycle | NOT RUN | Approved synthetic identity binds to a new staged database row. Onboarding/profile and terms/privacy evidence remain distinct from treatment authorization. |
| Clinician credentials, MFA and dashboard | NOT RUN | Eligible staged account signs in, completes real TOTP MFA and sees the normal dashboard. Unauthenticated, wrong-role and expired sessions fail. |
| Medical document upload and scanner | NOT RUN | Genuine upload reaches the actual API. Clean approved synthetic file succeeds. Standard scanner test fixture, unsupported shape and unavailable scanner are blocked. Check definition age and effective limits. |
| Extraction and patient review | NOT RUN | Actual approved provider emits evidence from the synthetic source. The original UI supports review/correction and traceable commit. Record failures without fabricating field values. |
| Encryption and medical history | NOT RUN | Stored bytes are ciphertext. Authorized retrieve of the intended document gives the expected checksum and correct patient history. Wrong role or patient scope cannot read bytes. |
| Signed consent and clinician access | NOT RUN | The enrolled real device signs bound consent. Verify medical-history access and the separate original document grant from the original clinician browser. Audit original byte access and prove no-consent denial. |
| Expiry, revocation and authorization rejection | NOT RUN | Exercise revocation/expiry using authorized synthetic accounts, two hospitals and separate patients. Observe protected browser preview closure and rejection of subsequent access, including replay and disabled clinician. |
| NFC, push and physical behavior | NOT RUN | Platform-specific device evidence on approved handset(s). NFC uses a capable device and scoped consent behavior. Hardware-backed signing and physical push/network conditions obtain their own results. |
| Audit and observations | NOT RUN | Value-free evidence for allow, deny, revoke, scanner failures and outbox processing. Verify partition integrity, metrics, alerting and telemetry redaction. |
| Backup and recovery | NOT RUN | Independent isolated restore of database, objects and key/version dependencies with integrity, erasure, audit and latency evidence. |
| Independent human gate | PENDING | Security, privacy and clinical reviewers sign the scope they evaluated. Separate approval is necessary for real users, production traffic or clinical care. |

Useful prior evidence includes [PR #101 doctor browser CI](https://github.com/sohamsadegaonkar/Nexa_Care/actions/runs/44902704947),
[PR #101 real scanner qualification](https://github.com/sohamsadegaonkar/Nexa_Care/actions/runs/44902704853)
and [PR #106 readiness failure](https://github.com/sohamsadegaonkar/Nexa_Care/actions/runs/45333936370).
The staging activation gate requires evidence from the new Azure deployment
and the selected reproducible build.
