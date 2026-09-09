# Nexa Care — Current Engineering State

**Last reconciled:** 2026-09-09  
**Source baseline:** `c811ba4abbb752a2ae7227d077d409e1d0738261`  
**Purpose:** repository-attested current state. Historical alpha documents remain useful context but are not authoritative when they conflict with this file or later governance attestations.

## 1. Current authority boundaries

Nexa Care treats these as separate authorities:

```text
account authentication
!= professional/facility verification
!= provider clinical eligibility
!= patient session authority
!= patient device authority
!= patient consent
!= record-access capability
```

Patient consent cannot repair failed provider trust, and a valid account login cannot create fresh device authority once device history exists.

## 2. Provider trust

The internal Provider Trust implementation and PostgreSQL/Redis qualification are merged. Current clinical authorization is built from independently evaluated provider identity/credential, professional verification, facility verification, affiliation state, fixed server-owned clinical capability, and mode-specific session/MFA assurance.

Official live ABDM/NHA HPR/HFR server-to-server qualification is **not** claimed. Phase 5G used synthetic registry behavior. Slice 7F remains **EXTERNALLY BLOCKED** until authoritative NHA/ABDM material supplies the real server-to-server endpoint, authentication, transport, error, retry/rate-limit, and related machine contract. No live production HPR/HFR source should be enabled by inference from synthetic qualification.

## 3. Patient sessions, devices, recovery, and consent

The Slice 6 software authority work through Slice 6H is merged and internally qualified.

Current patient authority includes:

- Redis-backed exact patient-session authority with patient-wide epoch invalidation;
- versioned patient device keys with a stable logical `device_id`, immutable `key_version`, canonical public-key fingerprint, and ACTIVE/REVOKED/REPLACED/COMPROMISED lifecycle;
- proof-of-possession device-key rotation using one-time session/device/version-bound Redis challenges;
- explicit lost-device/account recovery separated from ordinary account authentication;
- trusted-device enrollment/authorization without private-key transfer;
- cross-store failure ordering qualified against real PostgreSQL + Redis, including enrollment finalization versus logout-all and consumed-one-time-authority plus PostgreSQL rollback cases;
- Signed Consent V3 as the current signing domain.

### Signed Consent V3

Current consent signing uses explicit protocol `nexa-consent-v3`; newly created V2 requests cannot mint current access authority and the legacy V2 claim path is retired.

The V3 decision binds request, patient, provider, hospital/facility, nonce, decision, purpose, scope, duration, issuance/expiry, immutable server context, stable logical device ID, exact device-key row, exact key version, and public-key fingerprint. Provider professional/facility/affiliation/capability trust is re-evaluated before protected access is issued.

## 4. Native mobile key custody

Routine patient signing no longer depends on a JavaScript-readable raw P-256 private scalar.

The `NexaDeviceSecurity` native module provides alias-based P-256 signing:

- iOS source requests Secure Enclave-backed P-256 keys and exposes public-key/signature/custody metadata only;
- Android source uses Android Keystore, requests StrongBox when available, reports hardware metadata from platform key information, and exposes no private-key export method;
- the pre-Slice-6H SecureStore raw-key path is isolated to one-time migration only.

GitHub-hosted Android/iOS compilation is implementation evidence, **not physical hardware execution evidence**.

Slice 6I has a qualified evidence harness, validator, blocked manifest, and physical runbook, but its actual handset status remains:

**BLOCKED BY PHYSICAL PLATFORM / NOT_RUN**

No physical Secure Enclave, Android hardware-backed Keystore, StrongBox, biometric, or NFC execution is claimed from CI. No qualified native NFC reader or cross-device NFC/QR key-transfer protocol is currently implemented.

## 5. Persistence and migrations

The current single Alembic head is:

`20260909_device_trust_lifecycle`

`20260909_device_trust_lifecycle` revises `20260906_verification_scheduler`. Pilot/staging/production migration tooling is pinned to the current head and startup must not silently migrate, stamp, or downgrade the database.

## 6. Pilot runtime and cloud-security boundary — Slice 7B

Slice 7B's software evidence harness and validator are merged and internally qualified. They bind a future pilot claim to an exact repository commit, immutable backend image digest, frontend deployment identity, migration head, ECS task-role identity, KMS/S3 properties, PostgreSQL/Redis state, readiness checks, rollback/invalidation evidence, and dependency-failure behavior.

**Slice 7B live pilot runtime: NOT_RUN.**

Repository CI does not prove a deployed ECS/Fargate runtime, live task-role credentials, a specific KMS key or S3 bucket policy, deployed frontend identity, or measured cloud outage behavior. A live PASS requires an authorized synthetic-only pilot deployment and a sanitized measured evidence manifest validated against the merged 7B contract. No real patient PHI is authorized by the software-harness merge.

## 7. FHIR interoperability — Slice 7D

The backend exposes a consent- and provider-trust-gated FHIR R4 export route at `/api/v2/fhir/export/{patient_id}`. It exports current structured patient records first and falls back to the deprecated clinical shard only when structured records are absent. Audit failure aborts export.

Slice 7D merged an internally qualified base-R4 contract, `nexa-fhir-r4-base-v1`, for FHIR `4.0.1`. The declared emitted resource subset is `Condition`, `MedicationRequest`, `Observation`, and `AllergyIntolerance`. The export path now runs a fail-closed internal validator covering the declared structural, reference, terminology, timestamp, Quantity/UCUM, and resource-specific invariants.

**Slice 7D external FHIR validation / partner interoperability: NOT_RUN.**

The local contract is not a complete official FHIR validator, ABDM/India implementation-guide validation, partner-sandbox result, certification, or production exchange qualification. Those require an actual external target/profile/conformance authority.

## 8. Document AI / extraction — Slice 7C

The document pipeline includes provider-authorized AWS Textract integration, durable evidence/routing, clinician adjudication boundaries, failure quarantine, and real PostgreSQL/Redis coverage.

Slice 7C's software/evaluator qualification is merged. Its validator separates provider reachability, extraction metrics, and the actual fail-closed identity decision so a nominal benchmark result cannot hide a false accept or false reject.

The repository records an authorized synthetic benchmark execution that reached Textract for all 15/15 benchmark documents without provider errors, but **did not pass extraction accuracy qualification**. The committed replay left `benchmark_valid=false`, and one true-match synthetic identity was correctly rejected after OCR produced a discrepant name. No fuzzy matching or threshold weakening was introduced to manufacture a pass.

**Slice 7C live extraction accuracy: NOT QUALIFIED.** A future PASS requires a separately authorized live synthetic benchmark against the fixed or explicitly reviewed corpus and a green Slice 7C validator result. Provider reachability alone is insufficient.

## 9. Audit and operational governance — Slice 7E

Slice 7E's software/governance work is merged and internally qualified.

The canonical operator verifier is `scripts/verify_audit_partitions.py`, which verifies the partitioned ledger against durable `audit_chain_heads`. The historical `scripts/verify_audit_chain.py` path is now only a compatibility entry point into the canonical partition-aware verifier. The sanitized evidence wrapper `scripts/verify_audit_integrity_evidence.py` emits value-free classifications rather than raw audit payloads, hashes, event IDs, or database exceptions.

The verifier fails closed for forks/cycles, disconnected components, protocol/scope mismatches, sequence discontinuity, chain-head mismatch, malformed serialized payloads, and an explicitly requested nonexistent partition. A verified integrity failure is evidence to preserve; it is not permission to silently rewrite the ledger.

**Slice 7E operational database snapshot integrity evidence: NOT_RUN.** Repository CI qualifies the verifier and controlled regression cases; it cannot prove a specific authorized pilot/production database snapshot without connecting to that environment.

Pilot retention remains a human-governance boundary. `docs/governance/MILESTONE_6_PILOT_RETENTION_DECISION.md` remains **DRAFT — NOT APPROVED — NOT IN EFFECT**. Security and privacy/legal approval remain **PENDING**, and no proposed retention duration may become an S3 lifecycle rule by engineering inference. Actual lifecycle application and read-back evidence remain **NOT_RUN** until approval exists.

## 10. Slice 7 software closure and remaining gates

The internally executable repository work for **Slice 7A through Slice 7E is merged**. That statement is deliberately narrower than saying every pilot-readiness objective is complete.

The following remaining gates cannot be converted into PASS by repository code or CI alone:

1. **Slice 6I physical handset qualification — BLOCKED BY PHYSICAL PLATFORM / NOT_RUN.**
2. **Slice 7B live pilot runtime — NOT_RUN.** Requires an authorized immutable synthetic-data deployment and measured cloud/runtime evidence.
3. **Slice 7C live extraction accuracy — NOT QUALIFIED.** Requires a separately authorized live synthetic benchmark satisfying the merged evaluator.
4. **Slice 7D external FHIR validation / partner interoperability — NOT_RUN.** Requires a real implementation guide, validator/partner target, or conformance authority.
5. **Slice 7E operational database snapshot integrity evidence — NOT_RUN.** Requires an authorized operational database snapshot and controlled evidence capture.
6. **Pilot retention security/privacy/legal approval — PENDING; lifecycle apply/read-back — NOT_RUN.** Engineering may validate an approved policy but may not create the approval.
7. **Slice 7F official ABDM/NHA HPR/HFR machine-contract qualification — EXTERNALLY BLOCKED.** It requires authoritative NHA/ABDM machine-contract material before implementation or live qualification may proceed.

## 11. Next engineering action

There is no committed `SLICE_8` plan in the repository at this baseline, and this reconciliation does not invent one.

The next safe action is whichever real prerequisite becomes available first:

- execute Slice 6I on genuine supported handset hardware;
- execute the Slice 7B live synthetic pilot qualification in an authorized cloud environment;
- run a separately authorized Slice 7C live synthetic extraction benchmark;
- select and execute a real Slice 7D external FHIR validator/profile/partner target;
- capture authorized Slice 7E operational database snapshot evidence and obtain the required human retention approvals before any lifecycle rollout; or
- begin Slice 7F only after authoritative NHA/ABDM server-to-server contract material is available.

Until one of those prerequisites exists, creating a new software PASS, external contract, physical result, human approval, or Slice 8 scope by assertion would be inaccurate.
