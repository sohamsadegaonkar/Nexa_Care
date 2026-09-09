# Nexa Care — Current Engineering State

**Last reconciled:** 2026-09-09  
**Source baseline:** `aa091e14cf38124ca81e32438b49bdad4d79be8b`  
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

Official live ABDM/NHA HPR/HFR server-to-server qualification is **not** claimed. Phase 5G used synthetic registry behavior; the repository still records the official HPR/HFR machine contract as externally blocked pending authoritative NHA contract/authentication details. No live production HPR/HFR source should be enabled by inference from synthetic qualification.

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

No physical Secure Enclave, Android hardware-backed Keystore, StrongBox, biometric, or NFC execution is claimed from CI.

No qualified native NFC reader or cross-device NFC/QR key-transfer protocol is currently implemented.

## 5. Persistence and migrations

The current single Alembic head is:

`20260909_device_trust_lifecycle`

`20260909_device_trust_lifecycle` revises `20260906_verification_scheduler`. Pilot/staging/production migration tooling is pinned to the current head and startup must not silently migrate, stamp, or downgrade the database.

## 6. Encryption and deployment boundary

The repository contains both local envelope-encryption support and an `AWSKMSProvider`, plus S3 encrypted document storage and pilot environment/deployment validation tooling.

For pilot/staging/production, repository policy requires cloud KMS-backed envelope encryption, managed AWS task-role credentials, dedicated PostgreSQL/Redis, restricted host/proxy/CORS configuration, and audit/readiness checks. The presence of this implementation and tooling does **not** by itself establish a fresh live production deployment qualification.

Any new pilot/production claim must bind exact immutable backend/frontend versions, the deployed runtime configuration, PostgreSQL migration state, Redis state, KMS/S3 metadata, readiness checks, rollback evidence, and synthetic-only qualification data unless separately approved for real clinical data.

## 7. FHIR interoperability

The backend exposes a consent- and provider-trust-gated FHIR R4 export route at `/api/v2/fhir/export/{patient_id}`. It exports current structured patient records first and falls back to the deprecated clinical shard only when structured records are absent. Audit failure aborts export.

Internal route/unit coverage does not establish external FHIR conformance certification, partner-system interoperability, profile validation against an external implementation guide, or production exchange qualification. Those remain separate work.

## 8. Document AI / extraction

The document pipeline includes provider-authorized AWS Textract integration, durable evidence/routing, clinician adjudication boundaries, failure quarantine, and real PostgreSQL/Redis coverage.

The repository records a real authorized synthetic benchmark execution that reached Textract for all 15/15 benchmark documents without provider errors, but **did not pass extraction accuracy qualification**. Exact-occurrence precision and identity classification remained failing gates and `benchmark_valid` remained false.

Therefore provider reachability must not be described as extraction-accuracy qualification.

## 9. Audit and operational governance

The repository contains a canonical audit ledger, tamper-evidence tests, and `scripts/verify_audit_chain.py`; older documentation claiming that no audit-chain verifier exists is stale.

Pilot retention remains a human-governance boundary. `docs/governance/MILESTONE_6_PILOT_RETENTION_DECISION.md` is still DRAFT/PENDING for security and privacy/legal approval and must not be converted into an S3 lifecycle rule while those approvals are absent.

## 10. Current external/manual blockers

The following cannot be converted into PASS by repository code or CI alone:

1. Slice 6I physical handset qualification — **BLOCKED BY PHYSICAL PLATFORM / NOT_RUN**.
2. Official ABDM/NHA HPR/HFR live server-to-server qualification — externally blocked pending authoritative machine contract/auth details.
3. Pilot retention security/privacy/legal approval — human approval remains pending.
4. Any external FHIR partner/certification claim — requires an actual target/conformance authority.

## 11. Next engineering program

The next software program is **Slice 7 — Pilot Readiness, Interoperability, and Evidence Closure**.

Its scope and sequencing are defined in:

`docs/governance/SLICE_7_PILOT_READINESS_PLAN.md`

Slice 7 may proceed on internally executable work while the external/manual blockers above remain truthfully recorded. Progress in Slice 7 does not relabel Slice 6I physical execution as complete.
