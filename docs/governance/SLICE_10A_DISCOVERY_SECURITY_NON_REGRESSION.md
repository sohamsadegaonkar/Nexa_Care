# Slice 10A Discovery Security Non-Regression Contract

Status: **ENFORCED BY SLICE 10A RELEASE GATE**  
Scope: patient discovery/search only  
Precedence: on merge of PR #46, this file supersedes the earlier Phase 1B.2
mode-list statement in `SECURITY_NON_REGRESSION.md` while preserving all of that
standard's underlying authority, privacy, audit and fail-closed principles.

## 1. Permanent authority separation

```text
identifier match
!= patient authentication
!= patient session
!= patient device authority
!= discovery capability
!= patient consent
!= record-access authority
```

```text
provider login
!= provider clinical eligibility
!= PATIENT_DISCOVER capability
!= patient consent
!= record-access authority
```

No client role string, account session, patient consent, raw identifier or
search-index match independently grants clinical access.

## 2. Closed provider-facing discovery vocabulary

Qualified Slice 10A transports are exactly:

- `NEXA_PUBLIC_ID`
- `PHONE`
- `QR_PUBLIC_ID`
- NFC through `/api/v2/nfc/resolve`

The following are prohibited regressions unless a later reviewed security
contract explicitly replaces this one:

- patient UUID as a routine discovery input;
- name-only search;
- prefix/fuzzy search;
- candidate/ranked result lists;
- broad patient directories;
- MRN lookup without an independently modeled hospital-scoped authority;
- generic external-ID lookup;
- query suggestions derived from patient data.

## 3. Minimum disclosure

Before consent, successful discovery may disclose only:

```text
discovery_handle
expires_at
```

It must not disclose patient UUID, searched phone, public-ID echo, name,
demographics, clinical data, document metadata, redirect chain, match count or
candidate list.

## 4. Discovery-handle integrity

A discovery handle must remain:

- cryptographically random;
- short-lived;
- staged inert before mandatory success audit;
- activated atomically only after that audit;
- provider-bound;
- hospital-bound;
- exact provider-session-bound;
- canonical-patient-bound;
- single-use and atomically consumed;
- non-renewing during activation.

Audit failure, Redis failure, expiry, replay, wrong provider/hospital/session,
deleted patient, erasure state or canonical-resolution integrity failure must
fail closed without disclosing a usable handle.

## 5. PHONE privacy authority

Phone discovery is patient-opt-in and exact-only.

The discovery index must never persist raw or normalized phone values. It may
persist only versioned domain-separated keyed fingerprints plus relational
provenance and lifecycle state.

The HMAC keyring must be dedicated to discovery indexing. Reusing OTP,
registration, provider-contact, consent or unrelated application secrets is a
prohibited regression. Missing/invalid key configuration fails closed.

Patient opt-in requires fresh upstream phone verification tied to the exact
current external identity subject. A client-supplied patient mapping is never an
authority source.

A verified-phone collision across patient identities must not choose a winner.
Implicated search authority is quarantined/revoked and the operation fails
closed.

Patient opt-out revokes phone-search authority without revoking ordinary patient
login authority.

## 6. PHONE provider assurance

PHONE lookup requires all normal `PATIENT_DISCOVER` trust plus current live
provider-session binding and recent provider MFA. An old session, stale MFA,
frontend role or patient consent cannot bypass that gate.

## 7. Anti-enumeration requirements

Rate-limit keys must never contain raw or normalized patient identifiers.
Provider/hospital/type limits and an aggregate cross-type budget are mandatory.
PHONE retains stricter limits than opaque public-ID/QR modes.

For PHONE, absent, opted-out, revoked/stale and normal no-match states remain
publicly equivalent as `DISCOVERY_NO_MATCH`. Integrity ambiguity is unavailable,
not a candidate list.

No result count, near-match or directory-size signal may be added.

## 8. QR boundary

QR may carry only the strict versioned Nexa public-ID transport:

```text
nexa://patient-discovery/v1/NC-...
```

Raw UUIDs, access tokens, consent tokens, device credentials, arbitrary URLs,
query/fragment payloads and profile data are prohibited.

## 9. Lifecycle coupling

Search identifiers are patient-bound authority and must be revoked or denied
when their authentication source is revoked, rebound or otherwise invalidated.
Merge reconciliation may not leave an active stale identifier bound to a
pre-merge patient. Erasure and deletion remain fail-closed boundaries.

## 10. Audit non-regression

The Slice 10A audit vocabulary includes:

- `PATIENT_DISCOVERY_ATTEMPTED`
- `PATIENT_DISCOVERY_SUCCEEDED`
- `PATIENT_DISCOVERY_NO_MATCH`
- `PATIENT_DISCOVERY_RATE_LIMITED`
- `PATIENT_DISCOVERY_UNAVAILABLE`
- `PATIENT_DISCOVERY_AUTHORITY_REJECTED`
- `PATIENT_SEARCH_IDENTIFIER_BOUND`
- `PATIENT_SEARCH_IDENTIFIER_SUPERSEDED`
- `PATIENT_SEARCH_IDENTIFIER_REVOKED`
- `PATIENT_PHONE_DISCOVERABILITY_DISABLED`

Audit/observability must remain value-free for searched phone/public-ID/QR
content. Required audit failure must not be converted into a successful
discovery response.

## 11. Client non-regression

Discovery handles remain memory-only. They must never enter URL paths, query
strings, fragments, browser/native persistent storage or analytics.

Patient phone discoverability must remain visibly controllable on supported web
and native patient clients. The status API returns only a boolean and never the
phone.

## 12. Evidence required for any release

A release containing these controls requires the same exact commit to pass:

- Backend CI Partitions A, B and C;
- each partition's zero-skip assertion;
- full frontend tests;
- Next production build;
- workspace build;
- Android generation/compile;
- iOS generation/CocoaPods/compile; and
- Vercel exact-head deployment.

A passing older commit does not qualify a newer head. Any fix after freeze
invalidates the previous qualification and requires the full gate again.

## 13. Nonclaims

This contract is an internal engineering security boundary. It is not a claim of
statutory compliance, production certification, physical-device qualification,
HPR/HFR integration or complete security.
