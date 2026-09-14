# Slice 10A — Secure Patient Discovery V2

Status: **OPEN — BASELINE / SECURITY CONTRACT RECONCILIATION**

Baseline `main`: `d138a37956bc7bebca8833ba2cf1fad64cb67593`

Branch: `slice-10a-secure-patient-discovery`

## 1. Why this is the next slice

The repository product order in `docs/context/NEXA_CARE_CODEX_CONTEXT.md` places **real secure patient discovery/search** immediately after registration and before the bounded clinical treatment/access-session work.

Slice 9A backend and UI are closed. The remaining TypeScript `baseUrl` migration is separately recorded in `docs/governance/TYPESCRIPT_UPGRADE_DEBT.md` and is intentionally not part of this slice.

This slice therefore moves product/backend work forward rather than continuing registration-recovery polish.

## 2. Current repository baseline

Patient discovery is **PARTIAL, not missing**.

Already implemented and preserved:

- `POST /api/v2/patient-discovery` for exact `NEXA_PUBLIC_ID` resolution;
- NFC resolution through the same opaque discovery-capability boundary;
- provider authorization through server-owned `ClinicalCapability.PATIENT_DISCOVER`;
- provider/hospital/session-bound Redis discovery handles;
- `PENDING_AUDIT -> ACTIVE` activation only after mandatory success audit;
- short-lived, single-use atomic handle consumption;
- canonical patient resolution through redirect/tombstone handling;
- erasure and deleted-patient fail-closed behavior;
- bounded provider/hospital/type rate limiting;
- no patient UUID or clinical data returned before consent;
- doctor UI support for Nexa public ID and NFC.

Current discovery response remains intentionally minimal:

```text
discovery_handle
expires_at
```

A discovery handle is not consent, clinical access, patient authentication, device authority, or a patient identifier for display.

## 3. Product target

The repository product context targets secure discovery using appropriate identifiers such as:

- Nexa public patient ID;
- phone number;
- name plus another approved identifier;
- NFC;
- QR;
- later, hospital MRN / institution-scoped identifiers.

This target does **not** authorize a broad patient directory, fuzzy patient finder, ranked candidates, or client-selected patient UUIDs.

## 4. Existing security contract that must be deliberately evolved

`docs/governance/SECURITY_NON_REGRESSION.md` currently records the Phase 1B.2 invariant that phone, name, QR, MRN, external-identifier, fuzzy, and broad-directory discovery are not authorized.

That prohibition remains authoritative until this slice explicitly replaces it with a reviewed, narrower V2 contract and tests.

Therefore this slice MUST NOT simply add new values to `identifier_type` and query the database.

Before enabling any new identifier mode, implementation must define and qualify:

1. the authoritative source of that identifier;
2. normalization rules;
3. lookup/index privacy properties;
4. collision and ambiguity behavior;
5. enumeration resistance and rate limits;
6. canonical merge/redirect behavior;
7. erasure/deletion behavior;
8. minimum pre-consent disclosure;
9. audit vocabulary;
10. exact client behavior.

## 5. Non-negotiable authority boundaries

```text
identifier match
!= patient authentication
!= patient session
!= discovery capability
!= patient consent
!= clinical access
!= trusted device
```

and:

```text
provider login
!= provider clinical eligibility
!= PATIENT_DISCOVER capability
!= patient consent
!= clinical access session
```

Discovery remains a provider-initiated, pre-consent identification step only.

The backend remains authoritative for provider identity, hospital, session binding, clinical capability, canonical patient resolution, and the patient bound into the discovery handle.

The client must never submit or select a patient UUID for routine discovery.

## 6. Privacy and enumeration requirements

The V2 discovery surface must preserve these rules:

- no broad patient-directory endpoint;
- no prefix search;
- no fuzzy name matching;
- no ranked candidate list;
- no search suggestions derived from patient data;
- no result counts that disclose directory size;
- no clinical history or document metadata before consent;
- no internal UUID in a success response;
- no use of patient/source-document evidence to search for another patient;
- no client-side canonicalization of merged identities.

Name-based discovery, if introduced, must require another approved identifier and use a conjunctive server-side match. Name alone is not sufficient.

Low-entropy identifiers such as phone numbers require explicit anti-enumeration analysis even for authenticated providers. Existing rate limiting and audit are necessary but must not be assumed sufficient without adversarial qualification.

## 7. Searchable-PII data boundary

Current core `Patient` records contain an opaque `public_patient_id` but no patient name or phone field.

The legacy `nexa_vault` shard contains encrypted `patient_name`, `phone`, and `aadhaar_abha_id` ciphertext. Plaintext PII is prohibited there, and the current schema does not provide a patient search blind index.

Current first-time registration intentionally creates only the account graph required for phone-OTP authentication and does not persist a searchable patient profile.

Consequently, phone/name discovery cannot be implemented safely by:

- decrypting and scanning the entire vault;
- storing plaintext search columns;
- indexing raw phone/name values;
- querying Supabase or another identity provider as an implicit patient directory;
- reusing an unrelated HMAC secret;
- treating the external authentication subject as a phone number without an explicit contract.

## 8. Required searchable-identifier design

If Slice 10A enables phone or other PII-based exact lookup, introduce an explicit server-owned searchable-identifier model or equivalent service boundary.

At minimum it must provide:

- closed identifier-type vocabulary;
- canonical normalized value produced only by server code;
- a keyed one-way exact-match index using an independent, domain-separated secret;
- versioned index/key metadata to permit controlled rotation;
- patient binding through durable relational authority;
- unique/ambiguity rules appropriate to each identifier type;
- no raw normalized PII in logs, audit metadata, URLs, Redis keys, or generic observability;
- migration/backfill only from an authoritative source whose provenance is known;
- fail-closed behavior when index authority or required secret is unavailable.

Do not use an unkeyed hash for low-entropy identifiers such as phone numbers.

A keyed exact-match index is not by itself permission to expose whether arbitrary people have Nexa accounts. Route-level authorization, throttling, auditing and response design still apply.

## 9. Identifier-mode requirements

### 9.1 Nexa public patient ID

Keep the existing opaque `NC-...` exact-match path as the baseline implementation.

Preserve:

- strict normalization/format validation;
- canonical redirect resolution;
- erasure/deletion checks;
- opaque handle response only.

### 9.2 NFC

Keep the existing NFC resolution boundary and reuse the same discovery-handle semantics.

NFC resolution must not expose patient UUID or clinical data.

### 9.3 Phone

Phone discovery is not enabled merely because patient login uses phone OTP.

Before enablement, define:

- authoritative verified phone source;
- E.164/server normalization;
- searchable-index creation/update lifecycle;
- duplicate/merged-account semantics;
- change/reassignment handling;
- enumeration controls.

Do not bind a patient by a client-supplied phone-to-UUID mapping.

### 9.4 Name + another approved identifier

Name-only search remains prohibited.

Any supported combination must:

- be conjunctive;
- use exact or deliberately bounded normalization;
- never return a candidate list;
- fail closed on ambiguity;
- reveal no extra pre-consent demographics beyond an explicitly approved minimum-disclosure contract.

### 9.5 QR

A QR flow should carry an opaque server-issued discovery input or public discovery identifier. It must not encode a raw patient UUID, clinical capability, consent token, access token, device credential, or sensitive profile payload.

### 9.6 Hospital MRN

MRN/institution-scoped discovery is deferred until a hospital-scoped identifier authority and lifecycle are explicitly modeled. Any future MRN lookup must be scoped to the current authorized organization/hospital.

## 10. Discovery handle contract

The current handle model is retained unless qualification demonstrates a reason to change it:

- cryptographically random raw handle;
- only a SHA-256-derived Redis key is used for lookup;
- short TTL;
- staged `PENDING_AUDIT` state;
- activation only after success audit;
- provider-bound;
- hospital-bound;
- provider-session-bound;
- canonical-patient-bound;
- single-use atomic consume;
- no TTL extension during activation;
- invalid/expired/replayed/binding-mismatched handles fail closed.

New identifier modes must converge on this same capability boundary rather than creating parallel consent shortcuts.

## 11. Audit and rate limiting

Every supported discovery mode must retain stable, non-PII audit evidence for at least:

- attempted;
- succeeded;
- no match / safely equivalent disposition;
- rate limited;
- unavailable;
- ambiguous/integrity blocked if that state is distinguishable by server policy.

Audit metadata must contain safe type/policy identifiers, not raw phone, name, QR content, MRN, public ID, or patient UUID unless an existing audit policy explicitly authorizes a durable opaque target.

Rate limits must remain keyed by server-resolved provider/hospital context and identifier type. Low-entropy modes may require stricter budgets or additional abuse controls than public-ID/NFC resolution.

## 12. Error/response design

The route must use stable error codes and must not return internal database state.

The implementation must explicitly review whether new low-entropy identifier modes can safely distinguish `NO_MATCH` from other outcomes to the provider client. A broader identifier mode must not accidentally become an efficient account-enumeration oracle.

Success continues to return only the opaque discovery handle and expiry unless a separately reviewed minimum-necessary pre-consent identity summary is introduced. Such a summary is not authorized by this plan by default.

## 13. Tests required before merge

### Pure/unit

- normalization for every enabled identifier type;
- malformed/oversized input rejection;
- closed enum enforcement;
- no raw PII in audit metadata/logging;
- deterministic keyed-index behavior without revealing raw value;
- key-domain separation and missing-secret fail closed;
- QR payload/parser boundary if QR is included.

### PostgreSQL

- exact searchable-identifier uniqueness/ambiguity behavior;
- canonical merged patient resolution;
- deleted patient denial;
- erased patient denial;
- stale/reassigned identifier lifecycle;
- migration forward path and single Alembic head if schema changes;
- no plaintext PII persisted in search index.

### Redis / concurrency

- staged handle cannot be consumed before audit activation;
- activation does not extend TTL;
- handle is one-use under concurrency;
- wrong provider/hospital/session cannot consume it;
- expired/revoked handle fails closed;
- discovery success-audit failure cannot leak a usable handle;
- rate limit is atomic.

### Adversarial privacy

- broad/fuzzy/name-only lookup rejected;
- candidate enumeration/listing unavailable;
- public ID/phone probes cannot leak internal UUIDs;
- client-supplied patient UUID ignored/rejected;
- ambiguity does not choose a patient arbitrarily;
- cross-hospital scoped identifier cannot resolve outside its authority;
- logs/errors contain no raw search PII;
- discovery cannot mint consent/access authority.

### Frontend

- only enabled identifier modes render;
- discovery handle remains memory-only;
- capability never enters URL/navigation query/local storage/logs;
- no patient clinical data rendered pre-consent;
- expired discovery selection forces fresh discovery;
- stable errors do not expose backend internals.

## 14. Implementation sequence

1. **10A.1 — baseline/security reconciliation**
   - inventory current discovery, NFC, consent-consumption and frontend flows;
   - update security contract deliberately before broadening identifier modes;
   - decide which V2 identifier modes are in scope for this slice.

2. **10A.2 — identifier authority / searchable-index primitive**
   - only if a new PII-based identifier is approved;
   - add migration/model/service/config with independent secret and rotation/version semantics;
   - define authoritative profile/update source.

3. **10A.3 — resolver expansion**
   - add exact server normalization and resolver(s);
   - retain canonical/erasure/tombstone checks;
   - converge on existing discovery handle issuance.

4. **10A.4 — API + audit + abuse controls**
   - closed request vocabulary;
   - stable errors;
   - per-type throttling;
   - non-sensitive audit.

5. **10A.5 — frontend integration**
   - expose only qualified modes;
   - preserve memory-only discovery capability and consent handoff.

6. **10A.6 — qualification**
   - focused pure/PostgreSQL/Redis/adversarial tests;
   - full Backend CI Partitions A/B/C;
   - full Frontend CI including Next, workspace, Android and iOS;
   - Vercel exact-head deployment if frontend changes.

## 15. Definition of done

Slice 10A is complete only when:

- enabled identifier modes have explicit authoritative data sources;
- no broad/fuzzy patient directory exists;
- no plaintext searchable PII is introduced;
- canonical merge/erasure/deletion semantics remain fail closed;
- discovery success returns an opaque single-use bound handle, not patient authority;
- enumeration/privacy risks are explicitly tested;
- security non-regression documentation matches implementation;
- exact-head Backend and Frontend CI are green;
- any migration has one valid Alembic head;
- PR is merged and post-merge `main` is verified.

## 16. Explicitly out of scope

This slice does not implement the next bounded clinical access-session model. Existing consent flows may consume discovery handles as they do today, but Slice 10A does not broaden access authority.

After secure discovery V2 is closed, the next roadmap step is the bounded clinical treatment/access-session slice.