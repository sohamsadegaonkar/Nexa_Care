# Slice 7 — Pilot Readiness, Interoperability, and Evidence Closure

Status: **STARTED — reconciliation/design branch**

Baseline: `aa091e14cf38124ca81e32438b49bdad4d79be8b`

Branch: `slice-7-reconciliation`

## Goal

Slice 7 turns the internally qualified trust, consent, device, encryption, audit, and pipeline primitives into a current pilot-readiness program with explicit interoperability and evidence gates.

It does **not** convert external/manual blockers into PASS by assertion.

Core rule:

```text
IMPLEMENTED != INTERNALLY QUALIFIED != DEPLOYED != EXTERNALLY QUALIFIED != PHYSICALLY QUALIFIED
```

Each 7.x sub-slice must state exactly which level it proves.

## Verified starting point

The repository enters Slice 7 with these relevant facts:

- internal Provider Trust qualification is merged, while official live ABDM/NHA HPR/HFR machine-contract qualification remains externally blocked;
- patient session/device/recovery/rotation authority and Signed Consent V3 are merged and internally qualified;
- cross-boundary PostgreSQL/Redis failure ordering is qualified;
- native Android/iOS signing-key custody is implemented and compile-qualified, but physical hardware execution remains unproven;
- Slice 6I evidence machinery is qualified while physical execution remains `BLOCKED_BY_PHYSICAL_PLATFORM / NOT_RUN`;
- current Alembic head is `20260909_device_trust_lifecycle`;
- AWS KMS/S3 and pilot runtime tooling exist, but implementation/tooling alone is not a fresh live deployment qualification;
- the FHIR R4 export route is internally implemented/tested but not externally conformance-qualified;
- AWS Textract was reached successfully for all 15/15 synthetic benchmark documents in the recorded authorized run, but extraction accuracy qualification did not pass;
- pilot retention security/privacy/legal approval remains pending.

## Slice 7 sequencing

### 7A — Baseline and contract reconciliation

**Objective:** make current repository documentation and machine-readable contracts describe the software that actually exists after Slices 5–6.

Required work:

- reconcile `docs/CURRENT-STATE.md` against current governance attestations and code;
- correct stale migration/deployment references to `20260909_device_trust_lifecycle`;
- identify historical alpha documents that must not override current runtime contracts;
- audit `docs/API-CONTRACTS.md` and `docs/API-CONTRACT-DEVIATIONS.md` against current V3 consent/device/FHIR/pipeline routes;
- remove or mark stale security-gap statements that are already resolved, without deleting historical evidence;
- add tests for any contract artifact whose drift can create an unsafe client/server assumption.

Exit gate:

- exact-head Backend A/B/C zero-skip CI;
- Frontend JS/build + Android/iOS compile CI when client contracts are touched;
- no unresolved current-state contradiction for authority-critical routes.

### 7B — Pilot runtime and cloud-security qualification

**Objective:** qualify the current code in an immutable, synthetic-data pilot runtime rather than inferring deployment safety from local/CI tests.

Scope:

- exact backend image digest and frontend deployment identity;
- ECS/Fargate task-role credential path; no static AWS access keys;
- `ENCRYPTION_BACKEND=kms` with the intended AWS KMS key and encryption context;
- encrypted S3 document storage with expected KMS metadata and public-access protections;
- dedicated PostgreSQL and TLS Redis/Upstash;
- database migrated to exact repository head before traffic;
- `/healthz` and `/health` readiness behavior;
- audit-outbox worker/backlog health;
- trusted host/CORS/proxy boundaries;
- rollback and session/consent invalidation sequence;
- Redis/KMS/S3/database unavailability fail-closed cases where authority depends on them.

No real patient PHI is allowed by default. A green 7B means **pilot-runtime qualification for the tested immutable deployment**, not generic production certification.

### 7C — Document extraction accuracy qualification

**Objective:** close the gap between provider reachability and clinically useful extraction accuracy.

Starting evidence:

- a recorded authorized synthetic run reached AWS Textract for 15/15 documents without provider errors;
- that run failed benchmark accuracy gates and left `benchmark_valid=false`.

Required work:

- preserve a fixed synthetic benchmark and expected-occurrence corpus;
- separate provider transport failures from parser/classification/occurrence failures;
- eliminate duplicate/inflated evidence matches;
- qualify identity classification independently from field extraction;
- require stable precision/recall/coverage thresholds before setting benchmark PASS;
- keep high/critical-risk and uncertain clinical data on the explicit human-review path;
- preserve immutable source/evidence provenance and quarantine semantics.

No benchmark PASS may be inferred from "15/15 provider calls succeeded".

### 7D — FHIR R4 conformance and interoperability

**Objective:** move from an internally tested export shape to a declared, validated interoperability contract.

Current implementation boundary:

- `/api/v2/fhir/export/{patient_id}` is provider-trust and active-consent gated;
- structured patient records are preferred with a legacy shard fallback;
- audit failure aborts export.

Required work:

- freeze the exact resource/profile set Nexa claims to emit;
- validate generated bundles/resources against the chosen FHIR R4 rules and implementation guide/profile set;
- test identifiers, references, terminology, timestamps, units, provenance, empty/partial records, and malformed internal data;
- preserve patient/provider/facility authorization boundaries during export;
- add deterministic conformance fixtures and machine-readable reports;
- add an external target/sandbox qualification only when a real target exists.

Internal schema validation is not external certification.

### 7E — Operational evidence, audit, and retention closure

**Objective:** make operational evidence reproducible and remove stale governance ambiguity without allowing engineering code to self-approve legal/privacy decisions.

Required work:

- reconcile audit-ledger integrity tooling and runbook with the canonical ledger implementation;
- qualify chain verification against controlled tamper scenarios and operational database snapshots;
- define sanitized evidence-manifest conventions for deployment/runtime qualification;
- reconcile rollback, incident preservation, and evidence retention runbooks;
- keep the Milestone 6 retention decision PENDING until named security and privacy/legal reviewers actually approve it;
- prevent pending/proposed retention values from becoming cloud lifecycle configuration accidentally.

Engineering may validate configuration **after** approval; it may not manufacture the approval.

### 7F — Official ABDM/NHA HPR/HFR machine-contract qualification

Status at Slice 7 start: **EXTERNALLY BLOCKED**.

The repository must not invent an official contract. Work may proceed only when authoritative NHA/ABDM material supplies the actual server-to-server endpoint/authentication/transport/error/rate-limit contract required for live qualification.

When available, qualification must include:

- exact source runtime configuration;
- auth/token lifecycle;
- TLS/host allow-list/SSRF controls;
- schema and disposition mapping;
- timeout/retry/backoff/rate limits;
- adverse/ambiguous source results;
- synthetic/non-production qualification identities unless explicitly authorized;
- current Provider Trust re-evaluation after imported observations;
- kill switch and fail-closed behavior.

Until then: no live production HPR/HFR source is registered or claimed.

## Parallel external/manual gate — Slice 6I

Slice 6I remains the physical handset boundary and is intentionally not redefined as a Slice 7 software test.

Status remains:

**BLOCKED BY PHYSICAL PLATFORM / NOT_RUN**

When a genuine device is available, its seven physical scenarios can be run from a fresh branch based on the then-current reviewed `main`. A physical PASS must satisfy the existing machine-checkable evidence validator.

## Native NFC boundary

No qualified native NFC reader currently exists. If native NFC becomes a product requirement for the next pilot, it must receive its own explicit 7.x sub-slice or a separately approved extension with:

- actual iOS/Android native reader implementation;
- opaque payload only;
- no patient UUID or clinical data on the tag;
- replay/expiry/session/provider/facility binding;
- physical controller evidence.

The existing scanner abstraction is not a physical NFC implementation.

## Merge discipline

Each executable 7.x sub-slice follows the same discipline established in Slice 6:

1. branch from current verified `main`;
2. implement only the scoped authority contract;
3. open a draft PR;
4. run exact-head CI and real PostgreSQL/Redis/cloud/interop qualification where relevant;
5. fix failures without weakening authority;
6. record measured evidence and explicit nonclaims;
7. requalify the final documentation head;
8. merge with an expected-head guard;
9. verify `main`;
10. delete obsolete branches only after their work is proven merged.

## Immediate next action after this reconciliation

Complete **7A** by auditing the current API contracts/deviation report against the merged V3 consent, device lifecycle/recovery, FHIR export, and pipeline contracts. Only after 7A is qualified should implementation begin on 7B/7C/7D in parallel or priority order.
