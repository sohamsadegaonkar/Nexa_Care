# Nexa Care — AI Pipeline Technical Brief

**Day 14 · 2026-07-08**

---

## What We Are

Privacy-first health-record platform. A provider uploads a clinical
document (lab report, prescription, discharge summary). Our pipeline
extracts the medical data, validates it, scores confidence, classifies
risk, detects conflicts, and either auto-approves or sends to a human
steward for review. PII is encrypted with per-patient keys and never
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
| Auto-Approval Engine | threshold decision matrix | Single source of truth, no other module may implement auto-approval logic |

---

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

2. **Fail-closed auto-approval** — if anything is wrong (validation
   failure, review-required unknown reference range, conflict, missing confidence, HIGH/CRITICAL risk, allergy),
   the field goes to human review. False-auto-approve rate is 0%.

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
