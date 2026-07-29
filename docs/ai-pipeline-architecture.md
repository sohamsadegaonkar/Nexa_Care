# Nexa Care — AI Pipeline Technical Brief

**Day 14 · 2026-07-08**

---

## What We Are

Privacy-first health-record platform. A provider uploads a clinical
document (lab report, prescription, discharge summary). Our pipeline
extracts the medical data, validates it, scores confidence, classifies
risk, and detects conflicts. Runtime auto-commit is disabled; extracted
clinical candidates require human adjudication. PII is encrypted with
leaves the vault unredacted.

---

## Stack

| What | What We Use |
|---|---|
| API server | FastAPI 0.105.0 (Python 3.12) |
| Database | PostgreSQL via SQLAlchemy 2.0 async + asyncpg |
| Auth & audit ledger | Supabase 2.9.1 (JWT, Row-Level Security, hash-chained audit log) |
| Cache & rate limiting | Redis 4.5.1 |
| Background jobs | Celery 5.3.0 + Flower |
| Encryption at rest | cryptography 44.0.0 — AES-256-GCM per-patient DEKs, KEK-wrapped |
| Password hashing | passlib with argon2 + bcrypt |
| MFA | pyotp (TOTP) |
| Schema migrations | Alembic 1.15.0 |
| Monitoring | prometheus-client 0.25.0 |
| Linting | ruff 0.8.4 (zero violations enforced) |

---

## External APIs

| API | Wired? | What It Does |
|---|---|---|
| Document AI / VLM | **Placeholder** — mock when `DOCUMENT_AI_API_KEY` absent | Hosted Vision-Language Model for OCR + structured extraction from clinical documents. Intentionally no local PyTorch or Transformers — the API server runs GPU-free. |
| Supabase Auth | ✅ Yes | JWT verification, user management |
| Supabase PostgREST | ✅ Yes | Immutable hash-chained audit ledger writes |
| Redis | ✅ Yes | Rate limiting, session store, Celery broker |

---

## Pipeline Flow

```
  Provider uploads document (PDF / image)
                │
                ▼
  ┌─────────────────────────────┐
  │  1. EXTRACTION              │  MedicalDocumentExtractor
  │  Calls hosted VLM API      │  (mock fallback if no key)
  │  → ExtractedMedicalDocument │  {patient_name, diagnoses,
  └──────────────┬──────────────┘   lab_results, prescriptions,
                 │                  extraction_confidence}
                 ▼
  ┌─────────────────────────────┐
  │  2. VALIDATION              │  validate_field()
  │  BP format, lab ranges,     │  Medication 3-component rule,
  │  drug formulary match,      │  date plausibility, sugar
  │  abnormal flagging          │  reference range 70–100 mg/dL
  └──────────────┬──────────────┘
                 │
                 ▼
  ┌─────────────────────────────┐
  │  3. CONFIDENCE SCORING      │  score_field()
  │  Extractor confidence +     │  +0.04 valid BP format
  │  heuristic adjustments      │  +0.05 complete medication
  │  → float [0.0, 1.0]        │  −0.25 malformed BP etc.
  └──────────────┬──────────────┘
                 │
                 ▼
  ┌─────────────────────────────┐
  │  4. RISK CLASSIFICATION     │  classify_risk()
  │  bp/sugar → MEDIUM_RISK     │  medication → HIGH_RISK
  │  allergy → HIGH_RISK always │  anaphylaxis → CRITICAL_RISK
  │  Escalate on: abnormal lab, │  validation failure, conflict
  └──────────────┬──────────────┘
                 │
                 ▼
  ┌─────────────────────────────┐
  │  5. CONFLICT DETECTION      │  detect_conflicts()
  │  Same sugar >15 mg/dL apart │  → VALUE_DISCREPANCY
  │  BP "120/80" vs "120/80     │  mmHg" → no conflict (normalized)
  │  Penicillin allergy +       │  Amoxicillin → CONTRAINDICATION
  │  (beta-lactam cross-react)  │
  └──────────────┬──────────────┘
                 │
                 ▼
  ┌─────────────────────────────┐
  │  6. AUTO-APPROVAL DECISION  │  should_auto_approve()
  │  CRITICAL/HIGH → never      │  Allergy → forced HIGH, never
  │  MEDIUM_RISK → conf ≥ 0.97  │  LOW_RISK → conf ≥ 0.95
  │  validation fail → never    │  conflict → never
  └──────────────┬──────────────┘
                 │
          ┌──────┴──────┐
          │             │
     auto_approve   needs review
          │             │
          ▼             ▼
   PII → encrypted   Human steward
   vault shard       review queue
   Clinical →        (WS8 cockpit)
   de-identified
   shard
```

---

## Intelligence Layer (WS5) — No ML Runtime

Every WS5 component is **deterministic Python** — no PyTorch, no
Transformers, no GPU, no randomness. Same input always gives same
output. The "AI" part is the hosted VLM that does document
understanding; WS5 is the safety net.

| Component | Method | What It Checks |
|---|---|---|
| Medical Validator | regex + `difflib` fuzzy match + configured reference ranges | BP format, lab value + unit, medication 3-component (drug + strength + frequency), drug formulary ≥ 0.65 ratio, sugar 70–100 normal / >100 abnormal, HbA1c 4.0–5.6 %, future-date rejection, unknown generic lab ranges marked review-required |
| Confidence Scorer | heuristic ± on extractor confidence | Format conformance bonus / malus |
| Risk Classifier | field-type catalog + escalation rules | Base tier by category, +1 on abnormal / validation failure / conflict, allergy never below HIGH |
| Conflict Detector | value comparison + cross-reactivity | Sugar Δ >15, BP mismatch (after stripping "mmHg"), HbA1c / heart rate / SpO2 / temperature / weight thresholds, same-unit generic lab discrepancies, incompatible generic lab units, allergy↔medication contraindication, penicillin + "-cillin" |
| Legacy Auto-Approval Engine | compatibility-only threshold matrix for the older `ExtractedField` contract | Not used by the runtime orchestrator and not authoritative for canonical field evidence |

---

The two-branch auto-approval diagram above documents the legacy
`ExtractedField` compatibility design. It is not the current canonical
field-evidence routing contract and is not wired into the runtime orchestrator.
The authoritative pure three-lane evaluator is documented below.

## Security

| Layer | Detail |
|---|---|
| **Encryption at rest** | Per-patient DEK (AES-256-GCM), wrapped by system KEK, versioned. PII fields (patient_name, phone, aadhaar_abha_id) encrypted before write. |
| **PII redaction** | Correction logger redacts 9 PII field types to `[REDACTED]` before storage. Audit ledger never logs raw document bytes or extracted PII. Review queue only sees clinical fields. |
| **Consent gate** | Providers must present live `X-Consent-Token` for the specific patient + purpose. No consent = no access. |
| **Audit trail** | Hash-chained immutable ledger. Every pipeline transition audited before the data write (fail-closed). |
| **Upload limit** | 20 MB `ContentSizeLimitMiddleware`. |
| **Rate limiting** | Redis-backed per-IP throttling. |
| **MFA** | TOTP-based 2FA for provider accounts. |

---

## Data Model (Pipeline Tables)

```
document_storage          ← uploaded file metadata
  └─ extraction_jobs      ← background job lifecycle
       └─ extracted_fields ← each atomic observation
            ├─ validation_result (JSONB)
            ├─ confidence, risk_level, status
            └─ review_queue   ← fields needing human eyes
                 └─ field_corrections ← PII-redacted human edits

nexa_vault (PII shard)   ← encrypted patient_name, phone, aadhaar
nexa_clinical (clinical)  ← de-identified diagnoses, labs, prescriptions
```

---

## API Routes (Pipeline)

| Method | Route | What |
|---|---|---|
| POST | `/api/v2/pipeline/documents/upload` | Upload → create ExtractionJob |
| GET | `/api/v2/pipeline/jobs/{job_id}` | Job status + extracted fields |
| GET | `/api/v2/pipeline/review-queue` | Fields needing review |
| POST | `/api/v2/pipeline/fields/{field_id}/review` | Steward approve / reject / edit |
| POST | `/api/v2/pipeline/jobs/{job_id}/commit` | Atomic commit to patient record |

Plus 17 other route modules: auth, consent, FHIR R4, NFC, emergency,
MFA, assurance, merge, contracts, devices, dashboard, transparency,
policies, roles, documents, patient records, review.

---

## Key Design Decisions

1. **No local ML** — extraction is a hosted VLM call. Everything else
   is deterministic rules. API server runs GPU-free.

2. **Runtime auto-commit disabled** — the current runtime does not
   automatically commit extracted clinical candidates. Missing or incomplete
   evidence fails closed and cannot be promoted by a confidence threshold.

3. **Allergy invariant** — allergy fields are forced to HIGH_RISK and
   never auto-approved, regardless of confidence. Hard-coded clinical
   safety rule.

4. **PII/Clinical sharding** — patient identity is encrypted in a
   separate vault with per-patient DEKs. Clinical data is de-identified.
   Linked only by `masked_internal_id`.

5. **Audit-before-write** — every transition writes to the immutable
   hash-chained ledger *before* the data write. Audit failure = operation
   abort.

6. **Mock fallback** — entire pipeline works without a VLM API key.
   Deterministic mock extractor for CI, demos, and local development.

7. **Medication 3-component rule** — a medication field must have drug
   name (fuzzy ≥ 0.65), strength (e.g. "500mg"), and frequency (e.g.
   "twice daily"). Missing any component = validation failure.

---

## Ground-Truth Accuracy

Measured by running actual WS5 engine code against 85 test cases.
Not synthetic — every number comes from the real engine.

| Component | Accuracy |
|---|---|
| Validation Engine | 100.0% (46/46) |
| Risk Classifier | 100.0% (15/15) |
| Auto-Approval Engine | 100.0% (12/12) |
| Conflict Detector | 100.0% (5/5) |
| Full Pipeline E2E | 100.0% (7/7) |
| **Overall** | **100.0% (85/85)** |
## Adversarial evidence catalog

Milestone 0 defines a canonical 24-scenario adversarial catalog under
`tests/ai_extraction/adversarial/`. Each scenario is mapped to one or more of
six typed evidence-contract groups: `IDENTITY`, `CLINICAL_VALUE`,
`VISUAL_EVIDENCE`, `MODEL_EVIDENCE`, `POLICY_EVIDENCE`, and `LIFECYCLE`.

Catalog coverage is specification coverage: it records the threats and minimum
fail-safe outcomes that the extraction design must address. It is distinct from
executable runtime regression coverage. Catalog inclusion does not mean a
scenario is implemented or passing. Scenario 17 currently has an executable
runtime regression for atomic clinical persistence and audit-outbox rollback;
the remaining catalog scenarios must not be described as runtime-tested until
real tests exercise their production paths.

Runtime auto-commit remains disabled and is neither enabled nor approved by the
catalog.

## Canonical field-evidence contract

Milestone 1 defines the immutable contract in
`app/models/field_evidence.py` and the current-output adapter in
`app/services/extraction_evidence_adapter.py`. The contract groups evidence
into the same six areas as the adversarial catalog:

- **Identity:** patient, tenant/organization, source document and hash,
  ingestion/encounter identifiers, and an explicit binding status.
- **Clinical value:** canonical field name, raw and normalized values/units,
  reference range, effective date, clinical risk, validation results, and
  unresolved ambiguity.
- **Visual evidence:** zero-based page number, normalized `0.0-1.0` bounding
  box, exact source text/span, and coverage completeness.
- **Model evidence:** provider/model/version, extraction timestamp, separate
  document and field confidence, confidence provenance, verifier outcome, and
  safe evidence hashes.
- **Policy evidence:** immutable evaluation identifier/version/timestamp when
  evaluation eventually occurs and the disabled auto-commit flag. It does not
  contain a lane decision.
- **Lifecycle:** job/workflow/request and attempt bindings, timestamps, partial
  response state, retry/supersession/addendum relationships, and consent and
  erasure snapshots.

Document confidence and field confidence are different facts. The current
provider payload supplies document-level confidence only; the adapter retains
that value as `document_confidence` while recording `field_confidence=None`
with `UNAVAILABLE` provenance. Current output also lacks genuine page,
bounding-box, and exact source-text evidence, so those fields remain `None`
rather than being fabricated.

The existing staging column for field confidence is non-null. Current
document-only-confidence output therefore fails with
`FIELD_EVIDENCE_INCOMPLETE` before staging persistence instead of substituting
zero or copying document confidence. No migration is introduced by this
milestone.

Completeness helpers report structural evidence facts and machine-readable
issues only. They do not approve clinical truth or return an auto-commit,
source-only, or quarantine lane. Incomplete evidence is representable but
cannot be silently promoted. Runtime auto-commit remains disabled.

Catalog coverage remains a threat specification. Executable coverage is
declared only when a test exercises the production contract or adapter;
Scenario 17 continues to exercise the production clinical/audit transaction.

## Immutable three-lane decision contract

Milestone 2 adds the immutable decision representation in
`app/models/extraction_decision.py` and the pure evaluator in
`app/services/extraction_decision_engine.py`. The evaluator is the canonical
authority for decisions over `ExtractedFieldEvidence`; the older
`should_auto_approve()` helper is retained only for compatibility tests over
the pre-evidence `ExtractedField` type and is not called by the runtime
orchestrator.

The decision contract pins one evidence-contract version, one policy version,
the expected patient/tenant/organization/source/job/attempt bindings, stable
reason codes, canonical policy and evidence SHA-256 digests, evaluator version,
and optional linkage to an earlier immutable decision. Re-evaluation creates a
new decision ID and links to the earlier decision; it never mutates or mixes
the earlier policy snapshot.

Lane precedence is deterministic and fail closed:

1. **QUARANTINE** takes precedence for invalid inputs, identity or tenant
   mismatch, missing source binding/hash, tampering, conflicting evidence,
   partial provider output, verifier disagreement, inactive consent, erasure
   in progress, unsupported versions, or unresolved supersession. It is a
   decision result, not a persisted quarantine record.
2. **SOURCE_ONLY** represents authentic, correctly bound source evidence that
   cannot qualify for automatic clinical commitment because field confidence,
   visual/model/verifier evidence, normalization, permitted clinical risk, or
   the feature flag is insufficient. It does not mean approved, clinically
   verified, persisted, or committed.
3. **AUTO_COMMIT** is structurally reachable only when a caller supplies an
   explicit enabled policy and every approved requirement passes. The
   production/default policy remains `auto_commit_enabled=False`, and this
   milestone neither installs nor invokes an enabled runtime policy.

Evidence representation, decision evaluation, and persistence remain separate
layers. The evaluator performs no database, network, audit-delivery, logging,
or persistence work.

## Durable safe-lane routing

Milestone 3 adds two pipeline tables through
`20260729_extract_lane_route`: append-only `extraction_decisions` and separate
operational `extraction_routing`. A decision row stores only contract and
evaluator versions, opaque bindings, ordered reason codes, safe hashes,
timestamps, lane, and optional earlier-decision linkage. It never stores raw or
normalized clinical values, source text, filenames, provider exceptions, or
document bytes. The routing row references the existing encrypted
`DocumentStorage` artifact instead of duplicating it.

`SOURCE_ONLY` creates `SOURCE_RETAINED` routing metadata. It means the
authentic, patient/tenant-bound source remains retained under its existing
storage policy; it is not approved, clinically verified, placed in the legacy
review queue, or written to clinical truth. Current provider output supplies
document confidence but no genuine field confidence, page, bounding box,
source text, model version, or verifier agreement. After live consent and
erasure rechecks, that honest output therefore aggregates to a `source_only`
job without creating `ExtractedFieldRecord`, `ReviewQueueItem`, or
`PipelineCommit` rows.

`QUARANTINE` creates `QUARANTINE_PENDING` metadata with a review deadline.
The deterministic escalation service locks the route and can transition only
an expired pending item to `QUARANTINE_ESCALATED`; it never changes the
immutable decision or creates clinical data. No scheduler or adjudication UI is
introduced.

The orchestrator serializes attempts by locking the extraction job. For each
candidate it revalidates live consent through the existing approved-access
store, checks the authoritative erasure registry, adapts canonical evidence,
evaluates the three-lane policy, and stages the decision, route, job status,
and tenant-bound audit-outbox events in one caller-owned transaction. Any
write or outbox failure rolls back that entire routing transaction. Any
quarantine field makes the job `quarantined`; otherwise current candidates
aggregate to `source_only`. A complete response with no supported candidates
uses the explicit `source_only` terminal outcome and cannot become
`ready_for_commit`.

The runtime persistence boundary rejects `AUTO_COMMIT` even if a synthetic
enabled policy can produce it in a unit test. Database constraints additionally
allow only `SOURCE_ONLY` and `QUARANTINE` decision/routing lanes and require the
stored feature flag to remain false. The manual commit route explicitly rejects
source-only and quarantined jobs, while preserving existing explicitly
approved/edited legacy-field compatibility. Human adjudication UI remains a
later milestone.

Catalog membership remains specification coverage. Runtime-tested flags are
updated only when executable tests exercise the production control; this
milestone preserves the existing declared runtime scenario set without
claiming real PostgreSQL, Redis, object-storage, provider, KMS, or device
evidence.
### Human source adjudication

An extraction decision records the evidence-policy result; routing records its
operational lane; neither is a clinical approval. Only a `SOURCE_ONLY` route in
`SOURCE_RETAINED` state may open an ordinary adjudication case. `QUARANTINE` and
runtime `AUTO_COMMIT` remain excluded.

The reviewer must have an authorized clinical review role and live
document-processing consent. Source bytes are retrieved through the encrypted
document store and returned only by the case-scoped, no-store endpoint; storage
references and permanent URLs are never exposed. Consent, tenant/patient
bindings, and the erasure registry are checked again at submission and commit.

Reviewer submissions are immutable, strictly typed vital or laboratory
artifacts with canonical hashes. Human verification is provenance, not an AI
confidence score. Corrections supersede an earlier submission by adding a new
row. An accepted current submission is the sole authority for the locked,
single-transaction clinical/timeline/audit-outbox commit, and records use
`human_adjudicated` provenance with null AI confidence.

Milestone 4.1 makes the stored case review-session identifier authoritative for
submission, supersession, source access, and commit. Idempotency hashes bind to
that session. Only explicit `clinician` and `clinical_reviewer` roles may enter
or commit clinical values; generic administration remains limited to
non-clinical visibility. Audit-safe reason codes come from a bounded,
outcome-specific enum and never accept reviewer prose or clinical values.
Before submission or commit, the service reconstructs the case, job, document,
route and decision graph instead of trusting duplicated columns. PostgreSQL
constraints additionally enforce positive versions/attempts, SHA-256 lengths,
paired nullable source bindings, restrictive resource references, and
same-case accepted-submission ownership.

Jobs with no supported clinical candidates have no fabricated field decision
or routing row. They may receive an explicit document-level case whose routing
and decision bindings are null.

### Clinician source-adjudication workspace

Milestone 5 provides the provider-web routes under
`/doctor/pipeline/adjudication`. The queue reads adjudication cases, and the
review workspace retrieves the protected case source directly into a temporary
browser object URL that is revoked on replacement or unmount. PDF and image
rendering stays local to the browser; no third-party viewer receives document
bytes.

Review-session identifiers and mutation idempotency keys are generated with
browser cryptographic randomness and live only in process memory. They are
never placed in URLs or browser storage. Refresh/session loss fails closed and
does not invent a replacement session for an existing case. The UI supports
only the backend vital and laboratory unions, closed outcome-specific reason
codes, explicit human-verification confirmation, and accepted-submission
commit with `human_adjudicated` provenance.

The legacy `ReviewQueueItem`/`FieldCard` workflow remains only a historical
compatibility surface. `source_only` and `quarantined` jobs are redirected away
from its review and commit screens; the current adjudication workspace never
calls the legacy job commit endpoint. Supersession is intentionally unavailable
because the safe case-detail response does not expose the accepted structured
submission required for full reconfirmation.
