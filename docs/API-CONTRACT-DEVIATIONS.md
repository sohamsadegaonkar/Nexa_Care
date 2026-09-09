# API Contract Reconciliation Report

**Reconciled:** 2026-09-09  
**Current canonical contract:** `docs/API-CONTRACTS.md`  
**Source baseline:** `aa091e14cf38124ca81e32438b49bdad4d79be8b`

## Status of the 2026-07-11 deviation report

The previous contents of this file were an Alpha-era snapshot. They are preserved
in Git history, but they are **not a current defect list** after the Provider
Trust and Slice 6 work.

Several old findings are now either resolved or superseded by a different,
explicit security contract. Reusing the July list as if it were current would
reintroduce stale assumptions, especially Signed Consent V2 and JavaScript/raw
private-key custody.

## Current reconciliation summary

| Historical item | 2026-09-09 status | Current evidence / decision |
| --- | --- | --- |
| Device list omitted `public_key_fingerprint` | **RESOLVED** | `EnrolledDeviceInfo` now returns stable logical device ID, immutable key ID/version, lifecycle state, and canonical fingerprint. |
| Device request field names differed from old alpha names | **ACCEPT CURRENT IMPLEMENTATION** | Current authority contract uses `device_public_key` and `device_label`; the old alpha field names are not the canonical current contract. |
| Signed approval expected a provider `consent_token` in the approval response | **SUPERSEDED FOR SECURITY** | Signed Consent V3 approval is deliberately minimal; the owning provider claims access separately through the one-time V3 claim endpoint. |
| Signed approval response expected scope/expiry | **SUPERSEDED FOR SECURITY** | Scope/duration are already immutable signed/request context. Provider capability disclosure happens only at the claim boundary. |
| V2 signed payload was canonical | **RETIRED** | Current protocol is explicit `nexa-consent-v3`; newly created V2 requests cannot mint current approved-access authority. |
| SecureStore raw P-256 key was the routine client authority | **RETIRED** | Routine client signing uses native key aliases; the raw SecureStore scalar path is migration-only. |
| HIGH/CRITICAL `auto_approved` enforcement missing | **RESOLVED** | Current security tests exercise the canonical auto-approval engine and commit-time rejection for unsafe HIGH/CRITICAL states. |
| Low-confidence fields were not forced to review | **RESOLVED** | Current auto-approval policy/tests reject low-confidence automatic approval. |
| No audit chain verifier existed | **RESOLVED** | `scripts/verify_audit_chain.py` verifies the canonical audit hash chain; tamper-evidence tests also exist. |
| Break-glass accepted arbitrary reason strings | **RESOLVED** | `BreakGlassReasonCode` is a closed enum with reason-specific maximum clinical category sets and validated justification. |
| Pipeline trusted caller-supplied patient IDs | **RESOLVED** | Current pipeline authorization derives patient ownership from server-side job/field records and validates protected operations against that authority. |
| Cross-provider consent binding was globally uncertain | **NO LONGER A VALID BLANKET CLAIM** | Current routine V3 request/claim authority is owner/provider/hospital-bound and provider trust is re-evaluated. Any remaining route-specific concern must be demonstrated against that route rather than carried forward from the alpha snapshot. |

## Current open contract work

The following are real current boundaries, but they are not the same as the old
Alpha deviations:

### 1. External FHIR conformance

The current FHIR export route is internally implemented and tested. There is no
repository basis to claim external FHIR certification or partner-system
interoperability. Slice 7D owns profile/conformance fixtures and any future real
sandbox qualification.

### 2. Document extraction accuracy

A recorded authorized AWS Textract benchmark run reached the provider for all
15/15 synthetic documents without provider errors, but the benchmark did not
pass accuracy gates. Provider reachability is therefore not an extraction
accuracy PASS. Slice 7C owns this gap.

### 3. Live pilot deployment evidence

AWS KMS/S3/ECS/pilot validation tooling exists, but implementation and CI do not
establish a fresh immutable live pilot deployment qualification. Slice 7B owns
that evidence boundary.

### 4. Official ABDM/NHA HPR/HFR machine contract

Internal Provider Trust is qualified, but official live HPR/HFR source
qualification remains externally blocked pending authoritative server-to-server
contract/authentication details. The repository must not invent that contract.

### 5. Physical handset and NFC evidence

Slice 6I evidence machinery is qualified, but physical execution remains
`BLOCKED_BY_PHYSICAL_PLATFORM / NOT_RUN`. No qualified native NFC reader is
currently implemented. CI/simulator evidence cannot be relabeled as physical
hardware evidence.

### 6. Retention approval

Pilot retention remains a human governance decision. The existing Milestone 6
retention document is still pending security and privacy/legal approval and must
not become an S3 lifecycle policy by engineering inference.

## Contract maintenance rule

`docs/API-CONTRACTS.md` is authoritative for the authority-critical surfaces it
covers. This report records deviations or external qualification gaps; it must
not be used as an alternate contract.

When a route contract changes:

1. update the implementation and client contract together;
2. update `docs/API-CONTRACTS.md` in the same reviewed change;
3. add or update a regression test that proves the security-relevant contract;
4. record intentional deviations here only when a mismatch remains;
5. never preserve an obsolete, less-safe contract merely to make an old report
   continue to read as "matching".
