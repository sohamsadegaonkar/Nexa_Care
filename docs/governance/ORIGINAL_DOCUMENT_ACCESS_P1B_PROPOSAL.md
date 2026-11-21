# Original medical document access — historical P1B foundation proposal

> Historical pre-implementation proposal dated 20 November 2026. Current P1B authorization is described in [P1B_ORIGINAL_DOCUMENT_ACCESS.md](P1B_ORIGINAL_DOCUMENT_ACCESS.md). The proposal's 'not implemented' label applies to the earlier isolated foundation branch, not the current implementation. This archive must not be treated as a second permission model.

**Date:** 20 November 2026. **Status:** INTERNAL ELIGIBILITY IMPLEMENTATION ONLY;
authorization and file disclosure NOT IMPLEMENTED / NOT APPROVED.
Investor-demo readiness is the near-term goal, not a waiver of clinical,
security, or regulatory controls. Stacked on draft PR #98 (document inventory).

## Problem and bounded scope

An authorized clinician who sees a reference to a historical report needs a
patient-approved way to inspect the original. A single report is the proposed
minimum disclosure unit. Initially support only finalized, non-quarantined
patient-self external imports linked to a canonical DocumentReference and
patient-owned storage row. Provider pipeline sources and identity-review
evidence are out of scope. A missing or ambiguous graph is ineligible.
Do not present eligibility as clinical verification or permission.

The new internal service
`app/services/provider_original_document_eligibility.py` resolves that graph
using a patient-bound query. It validates canonical ownership, completion,
source kind, MIME, nonempty size, matching storage references and recorded
SHA-256 metadata, rejects any error-bearing import and ambiguous completion
graph, and invokes the existing erasure/lifecycle guard before and after the
query. Returns internal identity references only. It does not fetch or inspect
bytes, grant consent, create a route, or certify content integrity or safety.
A future caller must resolve from trusted authorization, never merely accept
untrusted client identity assertions.

## Proposed separate protocol (not an active policy)

- Version the signed request independently of Signed Consent V3 and
  Treatment Session V1. Use exactly the closed `READ_DOCUMENTS` operation for
  one document, with no history, pipeline, write, identity-review, or
  emergency inheritance. The current V3 mapping remains unchanged.
- Request after current verified provider eligibility and authorized
  patient discovery/history context. Server resolves the canonical patient
  and exact selected source graph; no storage URL or arbitrary blob key.
  Evaluate role, affiliation, facility, current MFA/session, purpose and
  applicable patient policy. Request initiation is not consent.
- Before a patient signs, present accurate provider and hospital identity,
  the specific document type and date, purpose, duration and disclosure
  implications. Clearly disclose that previously viewed information cannot
  be remotely recalled. No bundled approval or implied clinical verification.
- Signed context must bind protocol/policy version, exact operation, request,
  patient, provider, hospital, originating provider-session binding,
  canonical document/import/source identifiers, source-integrity binding,
  purpose, decision, patient-device identity, nonce, issued/expiry times and
  bounded duration. Specify interoperable canonical serialization and test
  vectors *before* implementation; reuse existing enrolled P-256
  SignedApprovalVerifier boundaries rather than a second verifier.
- Single-use challenge consumption and one-time claim; live Redis state plus
  durable PostgreSQL approval/grant state, with exact operation and resource
  binding and patient revocation. No new bearer persisted by the client.
  No mint, view or transport authorization from a V3 clinical/full,
  processing, treatment-write, or break-glass capability.
- Revalidate consent, exact resource association, lifecycle and eligibility
  before storage access and again after any asynchronous storage I/O and
  before disclosure. Verify actual bytes SHA-256 and size against the
  authoritative metadata, current malware/type/decoder policy, safe bounded
  content. No source access to corrupt, ambiguous, erased, quarantined or
  identity-conflicting evidence. Require a defined concurrency/revocation
  boundary (including in-flight fetches) and fail-closed dependency behavior.
- Use existing patient-bound encrypted storage adapter; no public storage
  links, pre-signed URLs, filenames or document bytes in telemetry. Keep
  patient/document identifiers and memory-only access capability out of URLs.
  Design authenticated web blob viewer and native handling with no-store,
  safe MIME, CSP/sandbox isolation, object URL cleanup, no durable browser
  cache and explicit unavailable/expired/revoked states. File contents may
  contain third-party information; review the actual disclosure and safe
  rendering model. No arbitrary external upload, download or public sharing.
- Durable allow/deny and actual source-disclosure audit through existing
  trusted AuditContext, with patient-visible source access history and
  no clinical values, tokens, raw source, filename or storage reference in
  logs. Do not reuse a patient-self audit actor for provider reads. Review
  event registry and visibility policy before introducing any event.
- Patient-self source access remains separate and unchanged. No identity
  quarantine release or E1–E6 disclosure, no storage/retention change, no
  clinical commit, no cross-patient reassignment, no real patient demo data.

## Required approval and evidence

Before introducing runtime issuance or original-file disclosure, obtain named
security, privacy/legal and clinical review of purpose, resource-level signed
authority, minimum scope, retention/erasure, patient notice, audit/history,
device binding and browser/native handling. This is a proposed engineering
contract, not approval or legal advice. Resolve the review decision against
SEC-001/002/003/004/005/006/011/013/014/016/017/020/021/022/023/034, the India
Regulatory Baseline (REG-003 and lifecycle/retention controls), Identity
Evidence Disclosure Policy, Engineering Constitution sections 2, 4, 6, 8, 11–14.

Qualification must include cross-patient/provider/hospital/session,
wrong-source/digest, expired/revoked/replayed challenge and claim, denied
approval, malformed signatures, erased/registry-down, corrupt/malware source,
audit failure, old permission types denied, concurrent revocation, database
and Redis failures, physical device signing, browser/native viewer disposal,
and repeatable synthetic Hospital X-to-Hospital Y rehearsal. Unit eligibility
tests and successful CI alone cannot qualify file release.

**Current change impact:** Internal metadata resolver, tests, proposed design
only. No routes, storage reads, consent mapping, patient data changes, schema
migration, clinical-write authority, identity-evidence policy or emergency
access changes. No real cloud/production or physical-device validation
performed. Preserve draft PR hold pending explicit owner merge command.
