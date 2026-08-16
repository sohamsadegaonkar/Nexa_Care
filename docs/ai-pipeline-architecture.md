# Nexa Care — AI Pipeline Technical Brief

**Day 14 · 2026-07-08**

---

## Current Clinical-Ingestion Authority

Nexa Care is a privacy-first health-record platform. The current safe
source-adjudication path is:

```text
SOURCE_ONLY
-> protected source review
-> immutable human submission
-> accepted-submission clinical commit
```

A SOURCE_ONLY decision is not an AI clinical proposal. The clinician reads the
authorized archived source and manually enters only a supported structured
vital or laboratory result. Runtime AUTO_COMMIT remains force-disabled.
Confidence thresholds, mock extraction, and the legacy extracted-field review
queue are not clinical-ingestion authorities.

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
| Amazon Textract | **Structured adapter implemented; live accuracy qualification pending** | Synchronous single-page `AnalyzeDocument` uses `QUERIES`, `FORMS`, and `TABLES`. Provider-authentic candidates remain untrusted until protected source review and clinician submission. |
| Remote Document AI / VLM | **Compatibility adapter retained** | Existing remote deployments remain supported; absent field evidence stays explicitly unavailable and routes fail closed. |
| Supabase Auth | ✅ Yes | JWT verification, user management |
| Supabase PostgREST | ✅ Yes | Immutable hash-chained audit ledger writes |
| Redis | ✅ Yes | Rate limiting, session store, Celery broker |

---

## Historical Compatibility Flow (Non-Authoritative)

The following diagram describes the older `ExtractedField` compatibility
pipeline. It is retained for historical context only. It does not describe the
current SOURCE_ONLY clinical-ingestion authority, and its mock/threshold paths
must not be used for clinical commit.

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
| **Upload limit** | Textract pilot: 10 MB and one page. The UI accepts PDF/PNG/JPEG; unsupported or multi-page provider input fails closed. |
| **Rate limiting** | Redis-backed per-IP throttling. |
| **MFA** | TOTP-based 2FA for provider accounts. |

---

## Data Model (Pipeline Tables)

```
document_storage          ← uploaded file metadata
  └─ extraction_jobs      ← background job lifecycle
       ├─ extraction_candidates ← patient-DEK encrypted value/source evidence,
       │                          tenant/patient/provider/document/job bound
       └─ extracted_fields ← legacy compatibility observations
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

1. **No local ML** — the real pilot uses Amazon Textract `AnalyzeDocument`
   with nine controlled Queries plus Forms and Tables. Blocking SDK work runs
   outside the async event loop with bounded timeouts and retries.

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

6. **No implicit mock fallback** — `demo` is restricted to explicit
   local/development/test/alpha configuration. Missing AWS credentials fail
   closed with a stable safe code.

7. **Medication 3-component rule** — a medication field must have drug
   name (fuzzy ≥ 0.65), strength (e.g. "500mg"), and frequency (e.g.
   "twice daily"). Missing any component = validation failure.

## Amazon Textract pilot configuration

```dotenv
DOCUMENT_EXTRACTION_PROVIDER=aws_textract
DOCUMENT_AI_AWS_REGION=ap-south-1
DOCUMENT_AI_TIMEOUT_SECONDS=30
DOCUMENT_AI_PROVIDER_MAX_ATTEMPTS=3
DOCUMENT_AI_JOB_MAX_ATTEMPTS=3
```

Use the normal AWS SDK credential chain. Never place access keys in Nexa Care
configuration or logs. The minimum IAM statement is:

```json
{
  "Effect": "Allow",
  "Action": "textract:AnalyzeDocument",
  "Resource": "*"
}
```

AWS synchronous Textract supports JPEG, PNG, PDF, and TIFF, up to 10 MB in
memory; PDF/TIFF are limited to one page, with at most 15 queries per page.
This adapter deliberately accepts only single-page PDF/PNG/JPEG/TIFF and never
silently truncates a multi-page file.

### Provider lifecycle boundary

The extraction lifecycle distinguishes three bounded concepts: a **job
attempt** (a recovery attempt for one persisted job), a **provider
subattempt** (one call inside the configured provider retry budget), and a
**complete extraction result** (the only result allowed to enter identity
assessment or evidence adaptation). `DOCUMENT_AI_PROVIDER_MAX_ATTEMPTS` and
`DOCUMENT_AI_JOB_MAX_ATTEMPTS` are independent integers in the range 1--5; the
maximum possible provider calls is their explicit product. The legacy
`DOCUMENT_AI_MAX_ATTEMPTS` remains a deprecated fallback only and emits a
value-free configuration warning whenever it supplies either budget.

Each job invocation resolves one immutable extraction configuration snapshot.
Provider/network JSON, SDK response content, remote model strings, and
arbitrary Pydantic payload fields are untrusted. A checked-in configured Nexa
adapter returns a frozen `ExtractionProviderResult` only after transport, SDK,
schema, parser, and single-provider/model coherence validation completes. The
orchestrator then independently verifies the envelope against the adapter
instance it actually invoked: its closed adapter identity, server-owned
contract version, complete-success state, attempt trace, and document/model
coherence must all agree before identity processing, decision/routing, or
candidate persistence.

The envelope is ordinary constructible Python data; it is not a secret token
or a module-private capability. Constructing one, or placing adapter/contract
claims in provider JSON, does not authorize clinical interpretation. The trust
boundary is the configured adapter execution path plus orchestrator
validation. Nexa makes no isolation claim against malicious arbitrary code
already executing inside the backend Python process: that code is within the
application trusted computing base and could otherwise monkeypatch factories,
database functions, or orchestration itself.

Provider subattempt provenance is stored in the append-only
`extraction_attempt_events` table before any clinical interpretation. It
contains only lifecycle metadata: deterministic event identity, tenant/patient
and job/document bindings, attempt numbers, controlled adapter/contract/model
versions, closed outcome, stable error code, completion flag, and timestamp.
It contains no OCR payload, source text, clinical value, identifier, or
provider error message. PostgreSQL rejects direct updates and deletes. Its
insert trigger derives and validates the redundant tenant, patient, and source
document bindings against the authoritative job and document rows, so a direct
database write cannot cross-bind lifecycle provenance. Restrictive
job/document foreign keys preserve provenance through ordinary cleanup;
cryptographic erasure remains the governing patient-data control.

Textract remains a one-page synchronous integration. A timed-out worker thread
is not killed, but any late response is barred from parsing, trusted binding,
candidate persistence, routing, or clinical commit. This is a current one-page
timeout safety boundary, not multi-page runtime support or a hospital-pilot
qualification claim.

The block graph is indexed once by Textract block ID. It follows only returned
query/answer, key/value, table/cell, merged-cell, word, line, and selection
relationships. Repeated answers and rows remain independent evidence
occurrences; deduplication occurs only when canonical value and exact evidence
identity are identical. A deterministic semantic boundary groups records only
when value, page, and authentic location or block relationships establish the
same occurrence. Equal values at separate locations remain separate.
Conflicting values are never selected or reconciled automatically.

Each candidate retains exact raw and source text, zero-based page, validated
normalized bounding box, genuine source-block confidence when present, model
version, timestamp, source type and block IDs, and a deterministic evidence
hash. Missing evidence remains missing. Deterministic normalization is a
separate conservative layer: it recognizes only directly written, unambiguous
formats and never guesses units, converts laboratory units, completes
medications, or infers diagnoses. Extracted identity is an OCR fact distinct
from the server-side patient/job/document binding.

Some synchronous Textract responses contain authentic PAGE ancestry without a
numeric `Block.Page` on the PAGE or descendant blocks. The first lineage
implementation reached the PAGE ancestor but still returned `None` because it
required that numeric property. The production provider now passes the already
validated single-page `DocumentMetadata.Pages` value to the parser. A missing
numeric page becomes zero only when the target has authentic ancestry to
exactly one PAGE block and that block is the only PAGE in the graph. Direct
numeric pages remain authoritative; unvalidated callers, unrelated blocks,
multiple or ambiguous PAGE ancestry, and malformed cycles remain unknown.

Milestone status:

- Adapter implemented.
- Production parser fixture tests cover graph interpretation; these are not OCR
  or live Textract accuracy results.
- Live AWS synthetic-document verification pending authorized credentials and
  a synthetic input.
- Runtime AUTO_COMMIT remains disabled.
- Clinician source adjudication and explicit commit remain mandatory.

---

## Historical deterministic-rule coverage (not OCR accuracy)

The historical 85-case suite measures deterministic validation, routing, and
conflict-rule behavior. It does not measure OCR, Textract field extraction,
table reconstruction, or production accuracy and must not be quoted as such.

| Component | Accuracy |
|---|---|
| Validation Engine | 100.0% (46/46) |
| Risk Classifier | 100.0% (15/15) |
| Auto-Approval Engine | 100.0% (12/12) |
| Conflict Detector | 100.0% (5/5) |
| Full Pipeline E2E | 100.0% (7/7) |
| **Overall** | **100.0% (85/85)** |

## Live synthetic accuracy benchmark

`scripts/run_textract_accuracy_benchmark.py` is an explicit opt-in harness that
uses the real `AwsTextractExtractionProvider` and normal AWS SDK credential
chain against a caller-supplied directory of synthetic single-page documents.
Its field-level manifest schema lives under `tests/ai_extraction/benchmark/`.
It prints aggregate metrics only, exits non-zero when configured gates fail,
and must never receive real patient documents. It is excluded from normal unit
tests and deployment automation.

The benchmark reports explicit attempt/success/failure and separate evidence,
semantic-candidate, duplicate-provenance and unmatched counts; sanitized stable
provider-error counts; canonical presence recall; exact occurrence
precision/recall; exact
raw and normalized value accuracy, unit accuracy, repeated-field recall,
table-row and source-text accuracy, page and bounding-box quality, confidence
provenance, identity-mismatch detection, and unexpected
provider-failure rate. Provider/API failure is not clinical `QUARANTINE` and
does not imply a clinical routing decision.

The first valid provider-authorized execution reached Textract for all 15/15
documents without provider errors, but did not pass accuracy qualification. It
reported 53 expected occurrences, 97 evidence records, and 80 inflated matches;
the contemporaneous multiset calculation found 49/53 exact raw occurrences.
Expected-item reuse inflated the match count. Page accuracy was 0, source-text
accuracy 0.275, and identity detection 0.9333333333333333. The corrected
evaluator consumes both sides once, uses source-category-compatible evidence,
resolves pages only from direct Page or PAGE ancestry, and fails identity
conflicts closed. Production staging also produced duplicate review candidates;
it now groups before routing and carries all supporting hashes and block IDs.
No production accuracy claim may be made. A live rerun remains pending separate
authorization.

The committed sanitized replay baseline reached 15/15 provider success with
97 evidence records, 63 semantic candidates, 34 provenance duplicates, and
49/53 exact matches. Live and offline results are equivalent: exact occurrence
precision is `0.7777777777777778`, recall is `0.9245283018867925`, page accuracy
is `1.0` with 97/97 pages present, source-text accuracy is
`0.9183673469387755`, and identity detection is `0.9333333333333333`. The
remaining failed gates are exact occurrence precision and identity
classification; `benchmark_valid` remains false. These fixtures are synthetic
test assets, not clinical records. Future parser and evaluator work must use
offline replay; another live Textract run is not currently authorized. No
production accuracy claim is permitted.

The replay also reports a separate semantic-occurrence diagnostic: 52 matches,
including three additional HbA1c matches beyond the 49 exact raw matches,
giving semantic precision `52/63` and recall `52/53`. Identity fields are
excluded from semantic matching, and exact raw metrics remain the fidelity
measures. A benchmark-only candidate-eligibility projection classifies five
malformed Query-only candidates as ineligible, leaving 58 eligible candidates;
its exact projection is `49/58` and its semantic projection is `52/58` with
recall `52/53`. Authentic evidence is retained, production staging and routing
are unchanged, and these projections do not affect benchmark gates or
`benchmark_valid`.

Production candidate eligibility is separate from the extraction lane. Every
non-identity semantic candidate remains evidence-bound, encrypted and persisted
with `routing_eligible`, a closed eligibility reason, and policy version `v1`.
Malformed Query-only provider format uses
`INELIGIBLE_QUERY_ONLY_INVALID_FORMAT`; an unexpected internal classifier
failure uses `INELIGIBLE_CLASSIFICATION_FAILED`. These reasons are distinct
operational facts: neither is inferred from the other, neither exposes
exception details, and both retain evidence while failing closed.
Clinician candidate responses present eligible candidates only and expose safe
suppressed counts; the protected source-document review path remains available.
Eligibility does not change `SOURCE_ONLY` or `QUARANTINE`, identity handling,
consent, erasure, or clinical commit. No automatic clinical ingestion or
auto-commit is enabled, and benchmark expectations are never consulted by the
production classifier.

The benchmark has an explicit synthetic-only sanitized replay boundary.
Capture is repository-corpus scoped, occurs only after successful provider
parsing, strips request/provider metadata, canonicalizes IDs and relationships,
and atomically requires the complete corpus. Offline replay validates the exact
fixture set and invokes the same parser with zero AWS calls. Replay fixtures
are synthetic test assets, never clinical records, and are not selectable by
production configuration. Unmatched, Query and identity diagnostics contain
only ordinals, canonical fields, source-type signatures, booleans, buckets and
counts; they never expose clinical values or provenance identifiers.

The corrected case diagnostic subsequently reported 63 semantic candidates,
34 duplicate-provenance records, 49 exact matches, 14 unmatched candidates,
four unmatched expectations and 97/97 missing pages. Document 15's deliberate
identity mismatch was correctly detected; one different synthetic case still
failed identity evaluation. Benchmark diagnostics identify cases only by
one-based manifest ordinal and aggregate canonical fields, identity reason
counts and sorted source-type signatures. They never expose values, text,
filenames, coordinates, evidence identifiers or provider request metadata.
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

### Provider fingerprints and Nexa evidence instances

The provider-owned `provider_evidence_hash` is an immutable fingerprint of an
authentic provider observation and provenance. It may legitimately repeat when
the same captured observation is processed in separate extraction workflows;
it is not patient ownership, identity authority, or a persistence key.

`evidence_id` is the Nexa identifier for one adapted evidence instance within a
source-document, job, workflow, and attempt lifecycle. For hashed provider
evidence it is a deterministic UUIDv5 over the internal
`nexa-evidence-instance:v2` namespace, those lifecycle bindings, and the
unchanged provider fingerprint. Therefore the same attempt replay receives the
same evidence ID, while a new attempt receives a distinct evidence ID. Missing
provider hashes retain the existing safe non-deterministic fallback; clinical or
identity values are never substituted into the identity formula.

The candidate `UNIQUE(evidence_id)` constraint remains the final persisted
instance boundary. Exact same-attempt candidate replays are accepted only
after immutable lifecycle and routing metadata are verified; an ID collision
with different authoritative bindings fails closed. Workflow-scoped IDs also
keep candidate value/source encryption contexts distinct across workflows.
Provider evidence remains corroborative only: it never creates patient
ownership, clinical authority, or automatic commit. Runtime `AUTO_COMMIT`
remains disabled and human adjudication remains required.

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
### Durable clinical conflicts and source relationships

Revision `20260814_conflict_supersession` adds append-only conflict sets and
source-document relationship edges. A conflict set is non-authoritative: it
binds every original encrypted candidate/evidence instance to the same tenant,
patient, job and source graph, but never selects a winner or changes a clinical
record. The orchestrator marks incompatible values with
`CLINICAL_VALUE_AMBIGUOUS`, preserves every observation, and runtime
`AUTO_COMMIT` remains disabled.

Conflict identity is deliberately conservative. The value-free
`clinical_fact_key` is SHA-256 over `clinical-fact-key/1.0`, the canonical field
name, and a private Nexa-owned structured-fact identity. Provider JSON cannot
set that identity: the public evidence model rejects extra input and the
Nexa-controlled Textract table parser binds it only at the trusted in-process
boundary when an exact laboratory test and effective date are both present.
Field name, result value, source text, page, row order and confidence do not
establish sameness. Repeated measurements with different dates therefore remain
separate observations. Query and Form evidence without sufficient exact context
gets no fact identity; incompatible same-field observations are preserved and
marked `CLINICAL_VALUE_AMBIGUOUS` as insufficient-context ambiguity, not called
a proven conflict and never granted automatic clinical authority.

PostgreSQL transaction-scoped advisory locks serialize each canonical conflict
graph by tenant, patient, job and fact key. Conflict lookup, creation and member
reconciliation happen under that lock. A composite foreign key proves every
member's candidate and evidence identifiers refer to the same candidate. The
conflict, member and resolution tables are append-only: migration-owned triggers
reject unsupported update or delete, and conflict resolution adds immutable
resolution rows rather than mutating membership or selecting an automatic
winner.

An accepted adjudication submission must explicitly list every applicable
conflict identifier. That list is protected by the immutable submission hash
and materialized as append-only resolution rows. Submission and clinical
commit both fail closed with
`ADJUDICATION_UNRESOLVED_CLINICAL_CONFLICT` when the current accepted
submission lacks complete resolution authority. A superseding submission must
declare the conflicts again; an earlier submission's declaration does not
carry forward.

`document_source_relationships` records one authorized ingestion-originated
`SUPERSEDES` or `ADDENDUM_TO` edge from a newer document to an earlier document.
Both sources must already belong to the same tenant and patient; self-links,
cycles, cross-boundary links, unknown types, and incompatible duplicate edges
are rejected. OCR/provider output cannot create these edges. The edge is
provenance, not truth: the earlier source and all evidence remain immutable and
an addendum never implies deletion.

Relationship mutation is serialized with a PostgreSQL transaction-scoped
advisory lock derived deterministically from the tenant and patient. After the
lock is acquired, the service reloads both documents and the graph, proves the
current provider owns both source workflows, revalidates the current live
document-processing capability, and checks the authoritative erasure registry
immediately before insertion. Revoked consent, active erasure, unavailable
authorization/erasure state, cross-tenant/patient/provider sources, cycles and
depth overflow fail closed with no relationship row. Historical ingestion
consent need not be reactivated; authority comes from the caller's current live
patient-document capability. Database triggers make every persisted edge
append-only.

For a related source, the orchestrator resolves field linkage only when exactly
one earlier candidate shares the explicit clinical-fact key. A resolved
`SUPERSEDES` edge fills `supersedes_evidence_id` and links the new immutable
decision through `earlier_decision_id`; `ADDENDUM_TO` fills
`addendum_to_evidence_id` without deletion semantics. Missing or multiple prior
candidates add `SUPERSESSION_UNRESOLVED` and route to quarantine. A corrected
source whose value conflicts with the earlier same-fact observation remains
conflict-controlled until explicit human adjudication.

Successful conflict creation, member addition, conflict-resolution acceptance,
source-edge creation and source-link processing decisions stage minimal,
value-free events in the same transactional audit outbox. Rejected operations
roll back with their protected mutation; the current architecture does not open
a separate connection to force a rejection event to survive rollback.

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

### Exact clinical commit idempotency and graph binding

An accepted human submission identifies a clinical observation only by the
server-owned tuple of patient, authoritative `source_document_id`, exact vital
type or exact laboratory test name, and the aware effective timestamp. The
clinical value, unit, reference range, reviewer, job, and submission are
protected content, not identity components. No fuzzy, OCR, embedding, name,
case-insensitive, or global content-hash matching is used. A different source
document therefore remains a distinct provenance-bearing observation even when
its content is identical.

PostgreSQL transaction-scoped advisory locks acquire all submission fact locks
in deterministic order. A same-identity row with exactly matching protected
content is reused; its human-adjudicated timeline event is reused as well. A
different value or any other protected-content mismatch fails closed with
`ADJUDICATION_CLINICAL_FACT_COLLISION`. Duplicate identities inside one
submission fail closed with `ADJUDICATION_DUPLICATE_CLINICAL_FACT`. New and
reused fields are reported only as value-free audit counts, and the caller's
transaction remains atomic across all fields.

The `20260815_clinical_commit_guard` migration adds partial unique indexes for
human-adjudicated source facts and timeline references. It fails closed when
pre-existing duplicates or binding mismatches are found; it never selects a
winner, merges rows, or deletes provenance. It also adds authoritative
composite keys and foreign keys linking document, job, candidate, decision, and
routing graphs across tenant and patient boundaries. `DocumentStorage`
deduplication remains scoped to `(tenant_id, patient_id, content_hash)` and is
never global.

These database controls preserve same-job extraction locking, consent and
erasure revalidation, source supersession/conflict integrity, and disabled
`AUTO_COMMIT`. Scenario 6 is not changed or claimed solved.
