# Nexa Care Security Non-Regression Standard

[Repository agent contract](../../AGENTS.md) · [India regulatory baseline](INDIA_REGULATORY_BASELINE.md) · [Engineering constitution](NEXA_CARE_ENGINEERING_CONSTITUTION.md)

Status: Enforced repository standard  
Owner: Nexa Care engineering leadership  
Security reviewer: Human owner required  
Privacy/legal reviewer: Human owner required  
Clinical reviewer: Human owner required  
Last reviewed: 2026-07-27  
Next review: 2026-10-25 or sooner after a material security change  
Repository baseline: `a9d542f` on `feature/document-processing-e2e`; Alembic head `20260727_doc_process_bind`

## 1. Status and ownership

This standard records defects already found in Nexa Care, their corrected invariants, prohibited regression patterns, enforcement points, and evidence. The technical owner and named reviewers must be assigned before a controlled pilot.

Status vocabulary:

- **Enforced:** implementation and repository tests currently encode the invariant.
- **Partially enforced:** some enforcement or evidence exists; identified work remains.
- **Validation pending:** implementation exists but required real infrastructure or device evidence is absent.
- **Retired:** old behavior is intentionally rejected and must not return.
- **Prohibited:** the pattern must never be introduced.

Review every 90 days, after a security incident, before pilot/production, and whenever an invariant or enforcement point changes.

## 2. Security design principles

Nexa Care defaults to deny, grants least privilege for a declared purpose, derives trust server-side, and binds every sensitive action to explicit tenant or hospital context. Secrets and patient data do not belong in URLs. Cryptographic evidence is verified before authorization. Sensitive actions require immutable, partitioned audit evidence and fail closed when a critical control is unavailable.

The system minimises data and observability, uses durable idempotency, distinguishes secure deletion from mere logical blocking, never fabricates clinical data, preserves source provenance, and distinguishes mock coverage from PostgreSQL, Redis, KMS, object-storage, and physical-device evidence.

## 3. Security findings register

| ID | Area | Original defect and threat | Corrected invariant / prohibited regression | Authoritative implementation | Required tests | Status | Last verified |
|---|---|---|---|---|---|---|---|
| SEC-001 | Consent capability transport | Bearer capabilities could reach URLs/navigation, leaking via history, logs, referrers, or analytics. | Navigation carries only non-secret `workflow_id`; capabilities remain memory-only and travel through the canonical header. Query-token use returns `410 CONSENT_TOKEN_IN_URL_RETIRED`. Never persist capabilities or put them in route/query/fragment/state. | `app/core/consent_gate.py`; `nexa-client/packages/app/services/capabilityStore.ts`; `apiClient.ts` | `test_consent_gated_crypto.py`; `test_frontend_integration_guardrails.py`; `capabilityUrlContract.test.ts`; patient/scanner/routine/emergency/pipeline screens; missing, expiry, logout, isolation, static search | Enforced | 2026-07-27 |
| SEC-002 | Server-side scope | Client-controlled tenant, hospital, patient, purpose, scope, category, or partition enables cross-tenant access. | Authenticated server state is authoritative; client assertions are inputs to validate, never trust anchors. | `app/core/dependencies.py`; `consent_gate.py`; `document_processing_gate.py`; v2 routes | `test_dependencies.py`; `test_pipeline_consent_server_side.py`; unauthorized/cross-doctor security tests | Enforced | 2026-07-27 |
| SEC-003 | Signed consent | Divergent or incomplete signature verification permits tampering or wrong-device approval. | `SignedApprovalVerifier` is authoritative; verify an enrolled, non-revoked P-256 key and bind request, patient, hospital, decision, scope, purpose, duration, expiry, session, and nonce. No duplicate verifier. | `app/services/signed_approval_verifier.py`; `patient_device_keys.py` | `test_signed_approval.py`; `test_signed_approval_contract.py`; forged-signature and device tests | Enforced | 2026-07-27 |
| SEC-004 | Challenge replay | Reusable or non-atomic challenges permit repeat authorization. | Challenge consumption is atomic and single-use in Redis; expiry and revocation fail closed. | `app/services/consent_engine.py`; `app/core/redis.py` | consent concurrency/replay tests plus `tests/integration/test_runtime_redis.py` | Validation pending | 2026-07-27 |
| SEC-005 | Routine/break-glass | Mixing emergency and routine paths can silently broaden access. | Separate paths; approved reason code and justification; clinical category is not emergency reason; emergency summary is minimum scope, visibly flagged, expiring/revocable, and audited. | `break_glass_policy.py`; `emergency_routes.py`; `emergency_summary_service.py`; `clinical_categories.py` | `test_emergency_summary.py`; `test_break_glass_revoke.py`; `test_record_viewer_and_emergency.py` | Enforced | 2026-07-27 |
| SEC-006 | Audit context | Implicit/global context or client-selected partitions can cross tenant chains. | Public audit APIs require keyword-only `audit_context`; no implicit fallback or public raw `chain_partition`; trusted dependencies bind tenant/hospital; platform context is explicit. | `app/security/audit_context.py`; `app/observability/audit_ledger.py`; `app/core/dependencies.py` | `test_audit_context_architecture.py`; `test_dependencies.py`; `test_seed_demo_doctor.py` | Enforced | 2026-07-27 |
| SEC-007 | Audit integrity/scale | Global scans, weak heads, or unverified links break integrity and scalability. | O(1) partition-head locking; monotonic partition sequence; hash continuity; partition idempotency; unhealthy partitions deny append; verifier detects disconnected components/head mismatch; UUID FK head; bounded partition columns. | `audit_ledger.py`; audit models; `20260720_final_runtime_fix.py`; `20260721_policy_audit_types.py` | audit ledger/guardrail/verifier/migration tests; `scripts.verify_audit_partitions --dry-run` | Enforced; live partition verification pending per environment | 2026-07-27 |
| SEC-008 | Transactional outbox | Mutation and audit could diverge; crashed `processing` rows could wedge forever. | Policy mutation and outbox insert share a transaction; lifespan starts/awaits worker; `SKIP LOCKED` claims use leases; expired work is reclaimable; append is idempotent; dead/stalled work degrades readiness. | `policy_service.py`; `audit_outbox_processor.py`; `app/main.py`; `20260719_security_runtime.py` | `test_policy_service_atomic.py`; `test_audit_outbox_processor.py`; runtime PostgreSQL tests | Enforced; real PostgreSQL evidence environment-dependent | 2026-07-27 |
| SEC-009 | Mutation idempotency | A “last key” cache loses historical safety and allows duplicate retries. | Durable unique `(tenant, operation, key)` records and canonical request hashes; same payload returns original result, changed payload conflicts, concurrency cannot duplicate. | mutation idempotency schema/service paths in `20260720_final_runtime_fix.py` | integration concurrency and migration tests | Partially enforced | 2026-07-27 |
| SEC-010 | Policy CAS | Last-write-wins loses concurrent policy updates. | Require `expected_version`; exactly one writer wins; increment once; matching outbox event; timezone-aware timestamps. | `policy_service.py`; policy models; `20260721_policy_audit_types.py` | `test_policy_service_atomic.py` and PostgreSQL marker tests | Enforced; real locking evidence environment-dependent | 2026-07-27 |
| SEC-011 | Cryptographic erasure | Cache/fallback decryption could bypass deletion; assurance could overclaim destruction. | Erasure registry is authoritative; registry failure denies; tombstone blocks cache; assurance distinguishes blocked/scheduled/destroyed; recoverability reflects cryptographic reality; reconcile inconsistent state. | `app/security/erasure_registry.py`; `erasure_tombstone.py`; `crypto_kms.py`; patient erasure routes | erasure, tombstone, envelope-encryption, runtime integration tests | Partially enforced; KMS destruction evidence pending | 2026-07-27 |
| SEC-012 | Key isolation | Shared wrapping-key fallback can weaken patient isolation. | Persist patient-bound key metadata and wrapping-key identifier; verify encryption context; no silent shared-key fallback; label mock/local KMS evidence honestly. | `crypto_engine.py`; `crypto_kms.py`; `dek_store.py` | `test_crypto_kms.py`; `test_vault_encryption.py`; `test_envelope_encryption.py` | Validation pending for real KMS | 2026-07-27 |
| SEC-013 | Logging/errors | Raw exceptions, tokens, signed URLs, names, reports, or clinical values can leak. | Log safe identifiers, trace IDs, and stable codes only; redact credentials/capabilities/payloads; client errors expose no internals. | safe logging in pipeline/API/client; `test_pipeline_orchestrator_safe_logging.py` | logging tests and static searches for tokens, signed URLs, `logger.exception`, `exc_info=True` | Partially enforced | 2026-07-27 |
| SEC-014 | Document authenticity | Unavailable/malformed extraction could fabricate clinical values or lose evidence. | No fabricated fallback or production-like mock; malformed or incomplete output fails closed; identity conflict blocks commit; document confidence never substitutes for field confidence; missing page, bounding box, source text, provider/model, and verifier evidence remain explicitly unavailable. | `field_evidence.py`; `extraction_evidence_adapter.py`; `document_storage.py`; `pipeline_orchestrator.py`; `document_processing_gate.py`; `20260727_document_processing_bindings.py` | adversarial field-evidence contract/adapter tests; document extractor/pipeline/safety/medical validation/server-side consent tests | Enforced locally; external provider/object storage evidence pending | 2026-07-29 |
| SEC-015 | Human review/risk | Confidence-only automation can promote unsafe fields to clinical truth. | High-risk or uncertain fields require adjudication; explicit risk rules; no AI diagnosis, rule-out, invented history, or silent replacement of source. | `app/ai/auto_approval.py`; `scoring_engine.py`; review routes | AI scoring, auto-approval, tampered payload, medical validation tests | Enforced | 2026-07-27 |
| SEC-016 | Device lifecycle | Wrong-patient, excess, or revoked device keys could approve. | Key belongs to authenticated patient; device maximum enforced; revoked keys cannot approve; rotation/revocation audited; push registration is not cryptographic enrollment. | `biometric_registry.py`; device routes/models | device key, biometric registry, consent QA tests | Enforced | 2026-07-27 |
| SEC-017 | Auth/session binding | Client identity/hospital or ambiguous credentials can elevate access; MFA can be replayed. | Provider and hospital resolve server-side; one canonical credential source; privileged MFA binds action/session; lifetime and revocation enforced. | auth dependencies/routes; `provider_auth_service.py`; MFA routes | auth, provider schema/password, MFA replay/integration tests | Enforced | 2026-07-27 |
| SEC-018 | Migration safety | Multiple heads, unsafe stamping, type drift, and manual schema fixes create unreproducible production state. | One head; forward-only fixes; explicit schemas; ORM/migration agreement; fresh and previous-head gates; never stamp past failure or manually create required tables. | `alembic/versions`; current head `20260731_adjudication_harden` | migration graph and migration-specific tests; disposable PostgreSQL upgrade gates | Enforced locally; disposable PostgreSQL gates pending per environment | 2026-07-31 |
| SEC-019 | Health/readiness | Conflated liveness/readiness can restart healthy processes or admit unsafe traffic. | Liveness and readiness differ; dependency loss does not falsify liveness; readiness includes DB, Redis, outbox worker, dead/stalled work, and audit health without sensitive details. | `app/main.py`; health routes/services | trusted-host, health, audit-outbox/runtime tests | Partially enforced | 2026-07-27 |
| SEC-020 | Physical device | Unit/API mocks were mistaken for end-to-end consent evidence. | FCM delivery, biometric approval, device P-256 signing, expiry, emergency, and revocation need real-device evidence before pilot. | Expo app, push service, physical preflight scripts | physical-device checklist/report and live evidence | Validation pending for each release candidate | 2026-07-27 |
| SEC-021 | Patient transparency | Self-access and multi-stage audit events produced blank/duplicate patient cards. | Projection permits only final successful provider access, excludes self access/actor patient, deduplicates operations, resolves immutable IDs with explicit fallbacks, paginates filtered events. | `patient_record_routes.py`; `AccessHistoryScreen.tsx` | `test_access_history.py`; patient screen tests; physical refresh evidence | Enforced | 2026-07-27 |
| SEC-022 | Consent revocation | UI called a nonexistent route and silently swallowed failure. | Patient-owned idempotent server revocation removes Redis capability, revokes grant/request, audits, and causes subsequent provider validation to deny. | `consent_routes.py`; `approved_access_capability.py`; API client/result screen | `test_patient_consent_revoke.py`; integration revoke tests; frontend tests | Enforced | 2026-07-27 |
| SEC-023 | Document source access | A processing job/source could be fetched without durable provider/consent binding. | Document-processing authorization derives patient/provider/hospital and live consent server-side; deny/allow and source-view events are audited. | `document_processing_gate.py`; `pipeline_routes.py`; `20260727_document_processing_bindings.py` | pipeline authorization, route, audit coverage, migration tests | Enforced locally | 2026-07-27 |
| SEC-024 | Clinical commit audit atomicity | Clinical persistence could succeed before required audit durability, so an API error could leave unaudited clinical data committed. | Clinical mutations, timeline and job state changes, and durable audit-outbox insertion share one transaction; any outbox failure rolls the entire commit back. Final ledger delivery remains asynchronous. | `pipeline_routes.py`; `record_ingestion.py`; `audit_outbox.py`; `audit_outbox_processor.py` | Scenario 17 in `tests/ai_extraction/adversarial/test_lifecycle.py`; pipeline, ingestion, and outbox tests | Enforced locally; real PostgreSQL rollback evidence environment-dependent | 2026-07-29 |
| SEC-025 | Extraction lane decision | Unvalidated, mismatched, incomplete, or lifecycle-blocked evidence could be routed by scattered confidence/status logic or a mutable policy snapshot. | Revalidate canonical evidence at the decision boundary; pin one immutable supported policy; quarantine integrity, identity, consent, erasure, and unresolved-supersession blockers before considering source-only limitations; runtime auto-commit stays force-disabled. Decisions contain stable codes and safe digests, never raw clinical/source values. | `extraction_decision.py`; `extraction_decision_engine.py` | `test_extraction_decision_engine.py`; adversarial catalog and auto-commit invariant tests | Enforced locally; lane persistence intentionally absent | 2026-07-29 |
| SEC-026 | Extraction routing durability | Incomplete source evidence or quarantine state could be lost, duplicated, mixed across attempts, or persisted without matching audit evidence. | Persist immutable safe decision projections separately from mutable routing state; reject runtime auto-commit; bind job/patient/tenant/document/attempt; serialize idempotency with database locks and constraints; commit decision, route, job state, and audit outbox together. Raw values, source text, filenames, provider errors, and document bytes never enter routing metadata. | `pipeline.py`; `extraction_routing.py`; `pipeline_orchestrator.py`; `20260729_extract_lane_route.py` | routing, orchestrator, migration, commit-guard, audit-outbox, consent, and erasure tests | Enforced locally; real PostgreSQL concurrency and migration evidence environment-dependent | 2026-07-29 |
| SEC-027 | Human source adjudication boundary | A routing decision or reviewer action without source access could become clinical truth, cross tenancy, bypass revoked consent or erasure, fabricate confidence, or leak clinical values through audit metadata. | Only explicit clinician/clinical-reviewer roles may mutate SOURCE_ONLY/SOURCE_RETAINED adjudication; generic administration is visibility-only. QUARANTINE and AUTO_COMMIT are excluded. The stored review session is authoritative; reason codes are closed and outcome-scoped; submissions are immutable, typed, hashed, and superseded by new rows. Source access, submission and commit reconstruct all resource bindings and recheck live consent and erasure. Clinical commit is locked, idempotent, audit-outbox atomic, and uses `human_adjudicated` provenance with no fabricated confidence. Zero-candidate jobs retain paired null decision/routing bindings. | `adjudication.py`; `pipeline_routes.py`; `20260730_source_adjudicate.py`; `20260731_adjudication_harden.py` | contract, session, role, authorization, source-access, idempotency, atomicity, audit-safety, migration, consent and erasure tests | Enforced locally; disposable PostgreSQL locking and migration gates environment-dependent | 2026-07-31 |

## 4. Prohibited patterns and safe alternatives

```python
# PROHIBITED: secret in URL
consent_token = request.query_params["consent_token"]
# SAFE: resolve workflow_id to a memory-only grant, then use the canonical header.
```

```python
# PROHIBITED: client-selected audit partition
chain_partition = payload.chain_partition
# SAFE: AuditContext.for_tenant(...) or for_hospital(...) from authenticated state.
```

```python
# PROHIBITED: missing trusted context
await append_audit_log(audit_context=None, ...)
# SAFE: pass an explicit trusted AuditContext; fail if it cannot be derived.
```

```python
# PROHIBITED: sensitive exception serialization
except Exception as exc:
    logger.exception(exc)
# SAFE: log a stable error code and safe trace identifier; preserve details only in an approved secure sink.
```

```python
# PROHIBITED: fabricated medicine
if document_ai_unavailable:
    return fake_clinical_values()
# SAFE: fail terminally or route the source to manual review.
```

```python
# PROHIBITED: unrecoverable worker claim
status = "processing"  # without lease/reclaim
# SAFE: atomically claim with worker_id and lease_expires_at; reclaim expired leases.
```

```python
# PROHIBITED: non-durable idempotency
last_idempotency_key = key
# SAFE: durable unique scope plus canonical request hash and stored result.
```

Also prohibited: client-selected tenant/patient scope, unbounded break-glass access, localStorage capabilities, raw patient/clinical analytics, production mocks, migration stamping around failures, and tests altered to hide a runtime defect.

## 5. Security test gates

Run from the repository root:

```powershell
.\venv\Scripts\python.exe -m ruff check app tests
.\venv\Scripts\python.exe -m ruff format --check app tests
.\venv\Scripts\python.exe -m compileall -q app scripts tests alembic
.\venv\Scripts\python.exe -m pytest -q
.\venv\Scripts\python.exe -m pytest -m postgres -q  # real disposable PostgreSQL required
.\venv\Scripts\python.exe -m pytest -m redis -q     # real disposable Redis required
.\venv\Scripts\python.exe -m alembic heads
cmd /c yarn --cwd nexa-client test:app
cmd /c yarn --cwd nexa-client verify-next-build
git diff --check
```

Fresh and previous-head migration tests require explicitly disposable PostgreSQL databases and must validate both `upgrade head` from empty and upgrade from the prior deployed head. Never run destructive migration tests against an ambiguous URL.

Static review:

```powershell
rg -n "consent_token|consentToken|access_token|signed_url" app nexa-client tests
rg -n "query_params.*token|chain_partition\s*=.*payload|audit_context=None" app tests
rg -n "logger\.exception|exc_info=True" app tests
```

Search hits require review; their existence is not automatically a defect. A green mock suite is not PostgreSQL, Redis, KMS, object-storage, or device proof.

## 6. How to add or update a security finding

1. Assign the next `SEC-###`.
2. Record the exploit or failure mode and affected assets.
3. Define a testable corrected invariant and prohibited regression.
4. Add an adversarial regression test.
5. Identify the production enforcement point.
6. Add an architecture/static guard where practical.
7. Record real-infrastructure/device validation separately.
8. Close only when enforcement and required evidence exist; mock coverage alone cannot close an infrastructure finding.
9. Update ownership, status, last-verified date, cross-references, and the repository completion report.

## Update procedure

Review this register every 90 days and whenever a security defect, invariant, enforcement point, dependency, migration, or validation requirement changes. Reinspect implementation and adversarial tests, update status only from current evidence, record infrastructure/device evidence separately, obtain the named security owner’s review, and update related regulatory or constitutional controls in the same patch. Never retire a finding merely because its original test was removed.

[Repository agent contract](../../AGENTS.md) · [India regulatory baseline](INDIA_REGULATORY_BASELINE.md) · [Engineering constitution](NEXA_CARE_ENGINEERING_CONSTITUTION.md)
