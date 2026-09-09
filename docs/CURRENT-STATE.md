# Nexa Care — Current Engineering State

**Last reconciled:** 2026-09-10  
**Reconciliation base:** `7d55cfa47991b8e577b09e30ddcd5a0074a839b3`  
**Purpose:** repository-attested current state. Historical alpha and earlier Slice-7 closure documents remain useful context but are not authoritative when they conflict with this file or later governance attestations.

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

## 3. Patient sessions, devices, recovery, and consent

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

## 4. Native mobile key custody

Routine patient signing no longer depends on a JavaScript-readable raw P-256 private scalar.

The `NexaDeviceSecurity` native module provides alias-based P-256 signing. iOS requests Secure Enclave-backed keys and Android uses Android Keystore with StrongBox requested when available. No private-key export method is exposed.

GitHub-hosted Android/iOS compilation is implementation evidence, **not physical hardware execution evidence**.

Slice 6I has a qualified evidence harness, validator, blocked manifest, and physical runbook, but actual supported-handset status remains:

**BLOCKED BY PHYSICAL PLATFORM / NOT_RUN**

## 5. Persistence and migrations

The current single Alembic head is:

`20260909_device_trust_lifecycle`

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

The internally executable repository work defined for the current backend closure through Slices **8A–8G** is complete once the reconciliation containing this file is merged. This statement is deliberately narrower than saying the backend is deployed or every external qualification is complete.

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

The remaining backend gates require real prerequisites that repository code cannot manufacture:

1. protected AWS OIDC plus ECS/ECR/KMS/S3/Redis/PostgreSQL/API/operations wiring to execute 8B, 8C and 8F live;
2. an authorized operational PostgreSQL connection to execute 8E evidence capture;
3. genuine named security and privacy/legal retention approvals before any S3 lifecycle apply/read-back;
4. a complete authoritative NHA/ABDM HPR/HFR server-to-server machine contract and official qualification target before a concrete registry transport adapter may be implemented or enabled; and
5. an actual FHIR partner/sandbox if end-to-end exchange qualification is required.

Slice 6I supported-handset execution remains a separate physical-platform gate outside backend software closure.

## 12. Next safe action

There is no repository-defined Slice 9 in this reconciliation, and none is invented here.

For now, backend repository/software closure is complete. The next action is whichever genuine prerequisite becomes available first: wire the protected pilot AWS environment, wire the authorized operational database, obtain retention approvals, obtain the authoritative HPR/HFR machine contract/sandbox, execute a partner FHIR exchange target, or perform the separate Slice 6I physical-device qualification.

Until one of those prerequisites exists, claiming deployment, live extraction PASS, operational audit PASS, retention approval, live rollback PASS, HPR/HFR integration, partner interoperability, or physical-device PASS would be inaccurate.
