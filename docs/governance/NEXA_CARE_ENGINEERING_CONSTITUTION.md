# Nexa Care Engineering Constitution

[Repository agent contract](../../AGENTS.md) · [Security non-regression standard](SECURITY_NON_REGRESSION.md) · [India regulatory baseline](INDIA_REGULATORY_BASELINE.md)

Status: Mandatory engineering policy  
Owner: Nexa Care engineering leadership  
Security reviewer: Human owner required  
Privacy/legal reviewer: Human owner required  
Clinical reviewer: Human owner required  
Last reviewed: 2026-07-27  
Next review: 2026-10-25 or sooner after a constitutional change  
Repository baseline: `a9d542f` on `feature/document-processing-e2e`; Alembic head `20260727_doc_process_bind`

## 1. Product mission

Nexa Care is a consent-first healthcare interoperability and clinical-workflow platform designed to let an authorized clinician access the right patient information quickly, with transparent consent, minimum necessary scope, trustworthy provenance and complete auditability.

It is not an unrestricted medical-record database, advertising data platform, autonomous diagnostic system, replacement for clinical judgment, system that hides sources behind AI output, reason to make basic patient access deliberately difficult, or platform for monetizing unauthorized/unnecessary health-data use.

## 2. Core product promises

Every feature preserves:

1. Patient control
2. Clinician usability
3. Minimum necessary access
4. Source traceability
5. Clear trust labels
6. Fast but safe workflows
7. Senior-friendly accessibility
8. Explicit emergency boundaries
9. No fabricated clinical data
10. Verifiable audit evidence
11. Affordable storage architecture
12. Interoperability over lock-in

Trade-offs must be recorded. Speed cannot silently defeat authorization; cost cannot remove evidence; AI convenience cannot misstate clinical truth.

## 3. Clinical safety boundary

Code must not autonomously diagnose, rule out disease, prescribe, recommend dosage/treatment, hide uncertainty, remove source evidence, promote low-confidence extraction to truth, or present patient-reported information as clinician-verified.

Use explicit, evidence-backed labels where applicable:

```text
Clinician verified
Patient reported
Document extracted
AI generated
Human reviewed
Unverified
Conflicting
Superseded
```

The label is part of the data contract, not decorative UI. A transformation must retain input provenance, model/rule version, reviewer, corrections, and source reference.

## 4. Required architecture boundaries

Canonical layers:

```text
API/routes
Application services
Domain policies
Persistence/repositories
Security/authorization
Audit/observability
External integrations
Frontend capability/navigation
Tests
```

- Routes validate HTTP concerns and orchestrate; they do not duplicate domain policy or cryptography.
- Services own workflows and explicit transaction boundaries.
- Domain policies make reusable business/clinical authorization decisions.
- Repositories/database layers own persistence and locking semantics.
- Security context comes from trusted dependencies, never client-selected tenant/hospital/patient values.
- External providers sit behind testable interfaces.
- UI presents and requests authorization; it cannot create it.
- Sensitive actions require audit evidence at the correct success/failure point.
- Circular security dependencies and route-local verifier implementations are prohibited.

Current examples include `SignedApprovalVerifier`, `AuditContext`, the consent/document-processing gates, audit outbox processor, erasure registry, pipeline orchestrator, and memory-only frontend capability store.

## 5. Data architecture

- Use a canonical internal patient UUID; keep ABHA, Aadhaar, hospital MRN, and external identifiers distinct.
- Bind tenant/hospital explicitly at every sensitive boundary.
- Separate structured clinical facts from encrypted archived source documents.
- Preserve source page, bounding box, confidence, risk, extraction/model version, reviewer, and immutable source reference for extracted facts.
- Retain version/correction history and distinguish clinician-verified, patient-reported, extracted, and AI-generated data.
- Do not delete legally/policy-required audit evidence; minimise its content.
- Archive safely instead of keeping data permanently hot when approved policy permits.
- Every duplicate source of truth needs authority and reconciliation rules.
- Retention and erasure follow an approved schedule; absence of a schedule does not authorize indefinite retention or unsafe deletion.

## 6. Consent architecture

- Account creation and feature authorization are separate.
- Sensitive device enrollment gates the sensitive feature, not unnecessarily the whole account.
- Routine access is explicitly patient-approved; emergency access is clinician-initiated and justified.
- Bind purpose, scope, patient, provider, hospital, session, duration/expiry, and nonce.
- Capabilities are bearer secrets: memory-only, never URL/localStorage/log/analytics.
- Revocation takes effect server-side and invalidates live capability state.
- Denied, expired, revoked, or malformed access cannot fall through another route.
- UI names exactly what is approved and provides clear terminal/expired/error states.
- DPDP processing consent, clinical consent, ABDM exchange consent, and Nexa Care capability authorization remain distinct records.

## 7. Emergency architecture

Emergency access requires a separate endpoint, policy, response model, and minimum dataset; controlled reason codes; justification when required; unmistakable banner; patient-visible history; expiry and revocation; and complete audit evidence. Clinical categories are not emergency reason codes. Full-record access requires separate authorization and cannot be smuggled through the summary path.

## 8. AI and document pipeline

Canonical flow:

```text
Upload
→ malware/type/size validation
→ encrypted storage
→ extraction
→ provenance capture
→ confidence and risk gate
→ human adjudication when required
→ explicit commit
→ source archival
```

Rules:

- No fabricated fallback and no production-like mock extraction.
- Model output is untrusted input.
- Preserve source page, bounding box, confidence, risk, prompt/model/version, and correction history.
- Identity mismatch blocks commit; duplicates and conflicting facts require explicit handling.
- AI output and summaries never replace sources.
- High-risk/uncertain fields require human adjudication; confidence alone does not override clinical risk.
- External providers receive only approved minimum data.
- No provider trains on Nexa Care health data without approved lawful basis and contract.
- Document-processing authorization must bind patient/provider/hospital/live consent before source access and audit allow/deny/view.

## 9. Backend coding standard

Require Python type hints, explicit domain errors, safe exception boundaries, UTC timezone-aware datetimes, UUIDs for identities where appropriate, bounded strings, database constraints matching assumptions, documented transactions, durable idempotency for retryable mutation, compare-and-swap for concurrency, dependency injection, and explicit shutdown/cancellation.

Prohibit bare `except`, sensitive exception logging, import-time network/workers, hidden module-import mutation, synchronous network I/O in async workflows, unbounded retries, implicit tenant context, and direct production-provider calls without an interface.

## 10. Database and migration standard

PostgreSQL is authoritative. ORM and Alembic must agree. Maintain one head and use forward-only corrections for possibly applied migrations. Backfill safely before a non-null constraint, use explicit schema names when required, design indexes for real query/locking patterns, and never mutate required schema manually or stamp around a failed migration.

Fresh-empty and previous-deployed-head upgrades are separate gates on explicitly disposable PostgreSQL. SQLite/mocks cannot prove PostgreSQL locking/types. Destructive tests require a resolved disposable target.

Every migration documents:

```text
Purpose:
Preconditions:
Existing-data behavior:
Locking risk:
Rollback position:
Validation query:
Forward-fix strategy:
```

Current head is `20260830_provider_trust`; changing it requires updating architecture contracts and validating ancestry, identifier length, and a single head.

## 11. Frontend standard

- Never put bearer tokens, capabilities, patient data, or clinical values in URLs or analytics.
- Capabilities remain memory-only; navigation uses non-secret workflow identifiers.
- Provide accessible loading, empty, error, expired, refresh, pagination, and retry states without fabricated success.
- Preserve existing data during refresh and make terminal authorization failure clear.
- Do not hide sources or blur clinician/patient permissions.
- Use understandable consent language, senior-friendly type and targets, safe-area-aware native layouts, and unmistakable emergency visuals.
- A visual fallback may say identity unavailable; it must not invent a clinician/facility.
- Physical-device behavior requires physical-device evidence.

## 12. API standard

Use versioned routes and explicit strict request/response models. Reject unknown fields for sensitive mutations. Return stable canonical error codes without raw exceptions. Authorize before data fetch and append required audit before sensitive success. Bound pagination/size, require durable idempotency for retryable mutation, keep secrets out of URLs, minimise response data, and keep OpenAPI consistent with runtime behavior.

Authentication success alone is not authorization. Patient, provider, hospital, purpose, live consent, category, and resource ownership must be validated at the correct boundary.

## 13. Audit standard

Every sensitive event defines:

```text
Actor
Patient/resource
Tenant/hospital
Purpose
Domain
Action
Outcome
Reason
Request/workflow ID
Timestamp
Sequence/hash information
```

Never audit access tokens, raw capabilities, full clinical payloads, source contents, encryption keys, or credentials. Use explicit trusted `AuditContext`; no global fallback or raw public partition override. New event types must enter the audit-event registry, visibility projection decision, and tests in the same patch.

## 14. Testing standard

Use the appropriate layers:

```text
Unit
Service
Route
Architecture
Migration
PostgreSQL
Redis
External-provider contract
Frontend
Production build
Physical device
```

Tests validate behavior and durable contracts, not brittle formatting trivia. Security tests are adversarial. PostgreSQL proves real locking/types; Redis proves atomicity/expiry; KMS mocks prove only the interface; physical push/biometric needs a device. Skips name missing infrastructure and are never counted as passes. Release-critical `xfail` is prohibited. Every fixed defect receives a regression test and, where practical, an architecture guard.

## 15. Observability standard

Use trace IDs, stable error codes, safe structured logs, and metrics for worker state, outbox backlog/dead letters/stalled leases, audit-chain health, extraction failure, push failure, consent timing, and emergency access. Never put health data, patient/provider names, tokens, capabilities, source contents, or clinical values in logs or metric labels. Public health responses disclose no sensitive topology/details.

## 16. Performance and reliability

Define, measure, and assign owners for:

| Concern | Target | Owner |
|---|---|---|
| Consent request latency | SLO pending measurement and product approval | Owner required |
| Record access latency | SLO pending | Owner required |
| Emergency summary latency | Safety-critical SLO pending | Owner required |
| Notification delivery | SLO pending physical evidence | Owner required |
| Document throughput | Capacity target pending | Owner required |

Use bounded retries with jitter, circuit breaking, backpressure, idempotent recovery, explicit timeouts/cancellation, poor-network states, and graceful dependency degradation that never bypasses security. Do not invent numerical SLOs.

## 17. Feature decision checklist

Before implementation, answer:

```text
What user problem does it solve?
Who is the user?
Who pays?
What patient data is involved?
Is the data necessary?
What authorization is required?
What is the minimum scope?
Could this become medical-device software?
Could this create diagnosis/treatment behavior?
What is the failure mode?
What audit event is required?
What retention applies?
What happens when offline?
What happens when the dependency fails?
How is the feature tested?
What does success measure?
```

Unclear answers block implementation or require an explicit discovery/decision task.

## 18. Definition of done

A change is done only when requirements are met; security invariants and regulatory impact are reviewed; migrations are valid; required tests/builds pass; skips and unrun infrastructure/device validation are explicit; logs are safe; docs are current; rollback/recovery is considered; unrelated scope is absent; and the final report is truthful.

Completion reports list changed files, applicable invariants, exact tests/pass/skip/fail counts, unrun validation, risks, migration status, and governance-rule changes.

## 19. Prohibited shortcuts

- Fake clinical fallback or production mock extraction
- Hardcoded patient, tenant, hospital, provider, or authorization scope
- Global/optional audit context
- Tokens/capabilities in URLs or localStorage
- Client-selected tenant/partition
- Broad emergency scope or emergency-to-full-record fallback
- Raw exception/payload logging
- Mock-only infrastructure claims
- Lint/test suppression instead of structural correction
- Unsafe migration stamping/manual schema mutation
- Direct production testing or production data in tests
- Silent last-write-wins or non-durable idempotency
- Hidden fees, dark patterns, misleading medical claims
- AI diagnosis/treatment by implication
- Source destruction without approved retention policy
- Tests changed to conceal a production defect

## 20. Change governance

Any constitutional change requires a written reason, affected invariant, security review, regulatory/clinical review when applicable, tests, migration impact, approval record, and update date. Agents may not weaken these rules silently.

Update procedure:

1. Open a focused governance change with the proposed wording and concrete use case.
2. Identify conflicts with the Security Standard and Regulatory Baseline.
3. Choose the safer interpretation until human reviewers decide.
4. Add/update enforcement tests without encoding full prose.
5. Obtain named engineering/security/legal/clinical approval as applicable.
6. Update metadata, cross-links, feature decision records, and downstream documentation.
7. Report any validation not run and never describe an unapproved proposal as policy.

[Repository agent contract](../../AGENTS.md) · [Security non-regression standard](SECURITY_NON_REGRESSION.md) · [India regulatory baseline](INDIA_REGULATORY_BASELINE.md)
