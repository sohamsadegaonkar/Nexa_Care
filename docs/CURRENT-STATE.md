# Nexa Care — Current Engineering State

**Last reconciled:** 2026-09-16  
**Reconciliation basis:** Slice 10A merged/qualified baseline plus active Slice 10B bounded clinical-access-session implementation on `main`.  
**Purpose:** repository-attested current state. Historical alpha and earlier Slice-7/8/9 closure documents remain useful context but are not authoritative when they conflict with this file or later governance attestations.

## 1. Current authority boundaries

Nexa Care treats these as separate authorities:

```text
account authentication
!= professional/facility verification
!= provider clinical eligibility
!= patient session authority
!= patient device authority
!= registration-recovery reviewer authority
!= patient discovery identifier match
!= patient discovery capability
!= patient consent
!= approved Redis record-access capability
!= durable ClinicalAccessSession authority
!= encounter/write authority
```

Patient consent cannot repair failed provider trust, a valid account login cannot create fresh device authority once device history exists, registration-recovery review cannot independently mint patient session/device/consent authority, and an identifier match never grants patient authentication, consent, or clinical access. The current Signed Consent V3 treatment bridge is intentionally read-only: it may establish `READ_CLINICAL_HISTORY`, but it does not authorize encounter creation, prescriptions, diagnoses, vitals, notes, investigations, or any other write operation.

## 2. Provider trust and external registry boundary

The internal Provider Trust implementation and PostgreSQL/Redis qualification are merged. Current clinical authorization is built from independently evaluated provider identity/credential, professional verification, facility verification, affiliation state, fixed server-owned clinical capability, and mode-specific session/MFA assurance.

Nexa's external registry integration seam is `app/services/provider_verification_registry.py`. Its permanent invariant remains:

```text
registry lookup request
!= registry observation
!= verification evidence record
!= lifecycle decision
!= system automation authority
!= clinical authority
```

The provider-verification worker defaults to an empty adapter map and external automation disabled.

### Slice 8G — ABDM/NHA HPR/HFR

The internal registry boundary is ready, but official live HPR/HFR transport is **EXTERNALLY BLOCKED**. The 2026-09-10 authoritative-source qualification pass did not obtain a complete, versioned NHA/ABDM server-to-server machine contract that establishes authentication lifecycle, HPR/HFR lookup endpoints and methods, request/response schemas, identity-binding semantics, error/status dispositions, retry/rate-limit semantics, official sandbox target, and version/change policy together.

`docs/governance/ABDM_HPR_HFR_MACHINE_CONTRACT_GATE.json` therefore records `BLOCKED_EXTERNAL_CONTRACT` and `external_adapter_enabled=false`. Published ABDM FHIR interoperability material is not reinterpreted as the HPR/HFR registry transport contract. No HPR/HFR endpoint, token exchange, retry policy, or response mapping is inferred from unofficial or historical material.

## 3. Patient sessions, devices, recovery, consent, and discovery

The Slice 6 software authority work through Slice 6H is merged and internally qualified.

Current patient authority includes:

- Redis-backed exact patient-session authority with patient-wide epoch invalidation;
- versioned patient device keys with a stable logical `device_id`, immutable `key_version`, canonical public-key fingerprint, and ACTIVE/REVOKED/REPLACED/COMPROMISED lifecycle;
- proof-of-possession device-key rotation using one-time session/device/version-bound Redis challenges;
- explicit lost-device/account recovery separated from ordinary account authentication;
- trusted-device enrollment/authorization without private-key transfer;
- cross-store failure ordering qualified against real PostgreSQL + Redis; and
- Signed Consent V3 as the current signing domain.

Current consent signing uses explicit protocol `nexa-consent-v3`; newly created V2 requests cannot mint current access authority and the legacy V2 claim path is retired. Provider professional/facility/affiliation/capability trust is re-evaluated before protected access is issued.

### Slice 9A — registration recovery manual review

The registration-recovery manual-review backend and UI are merged. Fresh patient OTP proof can open an opaque review case only after server-side graph classification. Review authority is independently derived from a live provider session, recent MFA, a current ACTIVE affiliation, and the server-owned `registration_recovery_reviewer` role.

Reviewer claim and terminal resolution are versioned and session-bound. Terminal resolution revalidates the registration graph under the same PostgreSQL advisory-lock domain used by automatic recovery and rejects graph-fingerprint drift. The repair vocabulary remains closed to the already-bounded automatic repair kinds; erasure, revocation, ambiguity and security concerns cannot be silently resurrected. Audit-outbox insertion and any terminal mutation share the database transaction. Reviewer routes never issue patient access sessions, device authority, or consent authority; after a safe repair the patient returns through the normal patient-facing recovery path.

Patient status polling exposes only the opaque case reference, public status, terminal flag, next action and timestamps. Provider subject, graph fingerprint, reviewer identity/session binding and internal authority metadata remain server-side.

The remaining frontend `baseUrl` migration is explicitly deferred in `docs/governance/TYPESCRIPT_UPGRADE_DEBT.md` and is not a Slice-9 blocker.

### Slice 10A — secure patient discovery V2

Slice 10A is **MERGED / QUALIFIED**. PR #46 was merged from its exact qualified head and post-merge `main` backend, frontend/native, and Vercel gates were verified.

Provider-facing discovery supports exactly four bounded transports:

- exact opaque `NEXA_PUBLIC_ID`;
- exact patient-opted-in `PHONE`;
- strict versioned `QR_PUBLIC_ID` carrying only the opaque Nexa public ID; and
- existing NFC card resolution.

Every provider-facing mode requires the server-owned `PATIENT_DISCOVER` clinical capability and converges on the same short-lived, provider/hospital/session-bound, audit-gated, single-use opaque discovery handle. No successful discovery response returns patient UUID, phone, public-ID echo, profile/demographic data, redirect details, clinical data, candidate lists, or result counts.

PHONE is off by default for the patient. Enabling it requires a live patient session plus a fresh Supabase SMS OTP whose authoritative returned subject exactly matches the current patient identity. The durable `patient_search_identifiers` authority stores only versioned, domain-separated keyed HMAC fingerprints plus patient/auth-identity provenance and lifecycle state; it stores no raw or normalized phone. Its dedicated HMAC keyring supports controlled version rotation and fails closed if active rows cannot be covered by configured key material.

A verified phone may not silently move between patient identities. Collision or ambiguity quarantines/revokes implicated search authority instead of selecting a winner. Merge/deletion/erasure/revocation/rebind boundaries revoke or deny stale search authority. Patient opt-out revokes only phone discoverability and does not disable phone login.

PHONE lookup additionally requires the exact live provider session binding and recent provider MFA. It has stricter provider/hospital/type rate budgets plus aggregate cross-type throttling; rate-limit keys contain no searched identifier. Absent, opted-out, stale, or otherwise nonmatching phone authority is exposed only as generic `DISCOVERY_NO_MATCH`, while integrity ambiguity fails closed as unavailable.

`QR_PUBLIC_ID` accepts only `nexa://patient-discovery/v1/NC-...` and is merely a transport for the opaque public ID. Raw UUIDs, access/consent tokens, device credentials, arbitrary URLs and sensitive profile payloads are rejected.

Name-only search, prefix/fuzzy search, ranked candidate lists, broad directory search, MRN and generic external-ID discovery remain prohibited in Slice 10A and remain prohibited after Slice 10A unless a later explicit contract changes that boundary.

The patient frontend exposes Phone Discoverability as a visible privacy control on web and native clients; the provider client exposes only qualified discovery modes. Discovery capabilities remain memory-only and do not travel in URLs or durable client storage.

### Slice 10B — bounded clinical access session

Slice 10B is **IN PROGRESS / BACKEND HARDENING** on `main`; it is not yet a completed release slice.

The current server-owned operation vocabulary includes read and future treatment-write operation names, but current Signed Consent V3 maps only to `READ_CLINICAL_HISTORY`. The existing V3 patient signature does not bind a write-operation set, so it must not be reinterpreted as write consent.

Canonical routine V3 access claims are bound to the exact provider session and issue a short-lived Redis `clinical_access_session` capability containing only server-owned session metadata. The raw provider session binding is not stored; only a one-way binding hash is carried. Routine clinical reads revalidate that exact provider-session binding and the closed current operation set.

Slice 10B.3 adds the durable PostgreSQL `clinical_access_sessions` authority. The raw record-access bearer is never stored in PostgreSQL; only its SHA-256 digest and server-owned patient/provider/hospital/request/session/policy bindings are persisted. The current database contract locks v1 sessions to exactly `READ_CLINICAL_HISTORY`, positive lifetime, active/revoked lifecycle consistency, and a closed revocation vocabulary.

The current read boundary requires Redis capability state and PostgreSQL durable session state to agree. Neither store alone is sufficient authority. V3 claim finalization stages the durable session in the same database transaction as the durable consent grant log; post-commit finalization failure invalidates Redis and compensates the durable authority fail-closed.

Patient-revocation integration and final adversarial/exact-head qualification remain active work. No Slice 10B completion claim is made yet, and no `CREATE_ENCOUNTER` or `WRITE_*` authority is enabled.

## 4. Native mobile key custody

Routine patient signing no longer depends on a JavaScript-readable raw P-256 private scalar.

The `NexaDeviceSecurity` native module provides alias-based P-256 signing. iOS requests Secure Enclave-backed keys and Android uses Android Keystore with StrongBox requested when available. No private-key export method is exposed.

GitHub-hosted Android/iOS compilation is implementation evidence, **not physical hardware execution evidence**.

Slice 6I has a qualified evidence harness, validator, blocked manifest, and physical runbook, but actual supported-handset status remains:

**BLOCKED BY PHYSICAL PLATFORM / NOT_RUN**

## 5. Persistence and migrations

The current single Alembic head on `main` is:

`20260916_clinical_access_sessions`

It descends linearly from `20260914_patient_search_identifiers`, which descends from `20260910_registration_recovery_review`. The Slice 10B migration adds only durable server-owned clinical-session authority; it stores bearer digests rather than raw access tokens.

Pilot/staging/production startup must not silently migrate, stamp, or downgrade the database.

## 6. Backend production hardening and live cloud boundary — Slices 8A/8B

### Slice 8A

Backend production hardening is **MERGED / INTERNALLY QUALIFIED**. Production-like startup uses fail-closed configuration and dependency preflight, including PostgreSQL/schema, TLS Redis, KMS and S3 readiness. Health/operations surfaces and deployment contracts are repository-qualified.

### Slice 8B

Live AWS/ECS qualification plumbing is merged, but the actual pilot deployment/runtime qualification is **BLOCKED BY MISSING PILOT AWS ACCOUNT WIRING / NOT DEPLOYED**.

The live cloud gate requires protected OIDC and explicit ECS/ECR/runtime targets before it can prove a concrete Fargate service, immutable image, task role, KMS/S3 properties, PostgreSQL/Redis state, deployed API health, and protected operations metrics. Repository CI is not a deployment claim.

## 7. FHIR interoperability — Slice 8D

The backend exposes a consent- and provider-trust-gated FHIR R4 export route at `/api/v2/fhir/export/{patient_id}`. It exports current structured records first and uses the deprecated clinical shard only as a backward-compatible fallback. Audit failure aborts export.

The internal base-R4 contract remains `nexa-fhir-r4-base-v1` for FHIR `4.0.1`, covering the supported `Condition`, `MedicationRequest`, `Observation`, and `AllergyIntolerance` subset.

### External ABDM profile validation

Slice 8D is **MERGED / EXTERNALLY PROFILE-VALIDATED** against published ABDM IG package `ndhm.in#6.5.0` using the official HL7 validator CLI. Exact-head qualification generated resources through Nexa's actual converter, passed Nexa's internal validator, loaded the published ABDM package, and returned `ABDM_FHIR_EXTERNAL_VALIDATION=PASS`.

The first external validator run exposed required authority facts for `MedicationRequest.requester` and `AllergyIntolerance.clinicalStatus`. Nexa emits those only when explicit source authority exists; missing legacy facts are not fabricated.

This does **not** claim partner-sandbox exchange, NHA/NRCeS certification, or production interoperability. Those remain **NOT_RUN / EXTERNAL**.

## 8. Document AI / extraction — Slices 7C/8C

The document pipeline includes provider-authorized AWS Textract integration, durable evidence/routing, clinician adjudication boundaries, failure quarantine, and real PostgreSQL/Redis coverage.

The Slice 7C evaluator/software is merged. A historical authorized synthetic run reached Textract for all 15/15 committed benchmark documents but did not qualify: `benchmark_valid=false`. Synthetic case 12 bound `Synthetic Patient Iota` while the recorded OCR evidence contained `Synthetic Patient lota`; Nexa correctly failed closed rather than fuzzy-matching the discrepancy.

### Slice 8C live gate

The protected live Textract workflow is implemented and requires OIDC, all 15 committed synthetic documents to reach the live provider, the existing strict evaluator to pass, sanitized replay capture, and replay reproduction with zero live provider calls.

A real attempt, workflow run `34400385106`, stopped at `Require approved AWS OIDC role` with `TEXTRACT_LIVE_QUALIFICATION=BLOCKED_MISSING_OIDC_ROLE`. AWS credential configuration, AWS identity verification, and Textract calls were not executed.

Therefore current live extraction status is:

**BLOCKED BY PILOT AWS OIDC / LIVE BENCHMARK NOT_RUN**

The committed corpus and identity semantics are intentionally preserved; no fuzzy matching or threshold weakening is introduced to manufacture a PASS.

## 9. Audit and retention — Slice 8E

The canonical operator verifier remains `scripts/verify_audit_partitions.py`. `scripts/verify_audit_integrity_evidence.py` emits the sanitized evidence schema without raw audit payloads, hashes, event IDs, or raw database exceptions.

Slice 8E's protected operational evidence gate is **MERGED / INTERNALLY QUALIFIED**. Backend CI #427 and Frontend CI #376 passed on its exact head.

The exact-head operational attempt, run `34401998829`, failed closed before database access with:

`OPERATIONAL_AUDIT_QUALIFICATION=BLOCKED_MISSING_DATABASE_WIRING`

`DATABASE_URL` was absent. The verifier did not connect and no database, audit-chain, or retention state was mutated.

Therefore operational database integrity evidence remains:

**BLOCKED BY AUTHORIZED DATABASE WIRING / NOT_RUN**

Pilot retention remains a human-governance boundary. `docs/governance/MILESTONE_6_PILOT_RETENTION_DECISION.md` remains **DRAFT — NOT APPROVED — NOT IN EFFECT**. Security and privacy/legal approval remain **PENDING**. Final retention durations are not approved and S3 lifecycle application/read-back remains **NOT_RUN**. Engineering must not create those approvals by inference.

## 10. Production monitoring and rollback — Slice 8F

Slice 8F's repository-side rollback/monitoring gate is **MERGED / INTERNALLY QUALIFIED**.

The gate accepts only an explicitly selected prior task definition and requires a digest-pinned ECR image, Fargate/`awsvpc`, application task role, `awslogs`, no static AWS credentials, and a stable current service. It launches the candidate only as an isolated one-off Fargate task whose command executes the candidate image's own `run_production_startup_preflight()` against the current PostgreSQL/schema, TLS Redis, KMS and S3 environment. Only after a successful candidate preflight does it recheck current `/healthz`, `/health`, protected `/ops/health`, and `/metrics`.

The qualification workflow contains no ECS service traffic switch and no database upgrade/downgrade.

Measured run `34402566056` failed closed before AWS authentication with:

`ROLLBACK_RUNTIME_QUALIFICATION=BLOCKED_MISSING_ACCOUNT_WIRING`

All `7 / 7` required pilot inputs were absent. No one-off ECS task, service mutation, traffic switch, migration, or monitoring probe occurred.

Therefore live rollback/runtime qualification remains **BLOCKED BY PILOT AWS/TARGET WIRING / NOT_RUN**.

## 11. Backend closure state

The previously reconciled backend closure through Slices **8A–8G** remains intact. Slice **9A** backend and UI are merged. Slice **10A** is merged and qualified. Slice **10B** is active backend work and remains incomplete until its durable session lifecycle, revocation paths, adversarial qualification and exact-head release gates are green.

Current matrix:

| Slice | Repository/software state | Remaining live/external state |
| --- | --- | --- |
| 8A | MERGED / INTERNALLY QUALIFIED | none for repository hardening |
| 8B | live-cloud gate MERGED | AWS account/OIDC/targets missing; NOT DEPLOYED |
| 8C | live Textract gate IMPLEMENTED | OIDC missing; live benchmark NOT_RUN |
| 8D | MERGED / ABDM 6.5.0 external profile validation PASS | partner/certification/production exchange NOT_RUN |
| 8E | operational audit gate MERGED / INTERNALLY QUALIFIED | authorized DB wiring missing; retention approvals PENDING |
| 8F | rollback/monitoring gate MERGED / INTERNALLY QUALIFIED | seven pilot inputs missing; live runtime drill NOT_RUN |
| 8G | registry boundary ready / contract gate enforced | official HPR/HFR machine contract + sandbox missing |
| 9A | backend + UI MERGED / QUALIFIED | live deployment not claimed |
| 10A | MERGED / QUALIFIED | no remaining repository closure gate |
| 10B | IN PROGRESS / read-only durable clinical-session hardening | revocation + adversarial/exact-head qualification pending |

The remaining live backend gates still require real prerequisites that repository code cannot manufacture:

1. protected AWS OIDC plus ECS/ECR/KMS/S3/Redis/PostgreSQL/API/operations wiring to execute 8B, 8C and 8F live;
2. an authorized operational PostgreSQL connection to execute 8E evidence capture;
3. genuine named security and privacy/legal retention approvals before any S3 lifecycle apply/read-back;
4. a complete authoritative NHA/ABDM HPR/HFR server-to-server machine contract and official qualification target before a concrete registry transport adapter may be implemented or enabled; and
5. an actual FHIR partner/sandbox if end-to-end exchange qualification is required.

Slice 6I supported-handset execution remains a separate physical-platform gate outside backend software closure.

## 12. Next safe action

Continue Slice 10B backend hardening without broadening write authority:

1. finish patient-revocation propagation into the durable `ClinicalAccessSession` lifecycle;
2. reconcile current migration-head/runtime evidence contracts to `20260916_clinical_access_sessions`;
3. prove Redis/PostgreSQL exact agreement, revocation, expiry, wrong-session/wrong-provider/wrong-hospital/tampered-operation denial, and post-claim failure compensation under adversarial tests;
4. freeze one exact `main` SHA only after the implementation and current governance contracts agree; and
5. require Backend CI Partitions A/B/C with each zero-skip assertion green before treating the durable backend increment as qualified.

Current Signed Consent V3 remains read-only. A future patient-signed treatment context must explicitly bind any write-operation set before `CREATE_ENCOUNTER`, `WRITE_PRESCRIPTION`, `WRITE_DIAGNOSIS`, `WRITE_VITALS`, `WRITE_CLINICAL_NOTES`, or `ORDER_INVESTIGATION` can become valid authority.

The live/external blockers above remain unchanged and must not be misrepresented as pilot deployment, live extraction PASS, operational audit PASS, retention approval, live rollback PASS, HPR/HFR integration, partner interoperability, or physical-device PASS.
