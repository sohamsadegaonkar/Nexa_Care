# Slice 9A — Verified Manual-Review Case Creation Plan

Status: **IMPLEMENTED — historical design contract; current qualification tracked in SLICE_9A_CLOSURE_QUALIFICATION.md and PR #43**

Base: `54351f9a55ba94665420961cfe766bdcc84a5398`

## Entry authority

A durable review case may be created only inside the existing patient registration-recovery OTP verification path **after** all of the following have succeeded:

1. server-side recovery attempt claim;
2. Supabase OTP verification;
3. authoritative verified phone match;
4. provider subject extraction from the verified Supabase result;
5. server-side registration graph inspection;
6. inspection disposition exactly `manual_review`.

Possession of a phone number, recovery reference, patient UUID, graph fingerprint, case reference, or caller-supplied provider subject is not independent case-creation authority.

## Durable identity and privacy binding

Case creation resolves the exact `PatientAuthIdentity` row from the already-verified provider subject and stores:

- `identity_id` as the stable non-secret foreign-key graph anchor;
- provider literal `supabase`;
- domain-separated SHA-256 provider-subject hash, never the raw provider subject;
- candidate source patient UUID only when the referenced patient row currently exists;
- graph fingerprint produced by the verified server-side classifier;
- one normalized durable reason code from the closed review vocabulary;
- contract/policy versions and deterministic operation/idempotency hashes.

No phone, OTP, Supabase access token, raw provider subject, patient access token, device authority, consent authority, or clinical content is persisted in the review case.

## Reason normalization

The server-owned mapping recorded in Finding 005 is the only accepted normalization boundary. Unknown future internal manual-review reasons normalize to `SECURITY_CONCERN`; they are never persisted as arbitrary database strings.

## Idempotency and concurrency

Case uniqueness is bound to `(provider, provider_subject_hash, graph_fingerprint)`.

Creation uses a deterministic idempotency key derived from the verified recovery attempt ID and a deterministic operation hash derived from the durable case inputs. PostgreSQL conflict handling must return the existing exact graph-bound case rather than creating a duplicate. A conflict whose existing row does not match the expected provider-subject hash, identity anchor, graph fingerprint, and normalized reason is a fail-closed `REGISTRATION_RECOVERY_REVIEW_CASE_CONFLICT`.

Concurrent verified attempts for the same identity and graph must converge on one case reference.

## Transaction and audit

For a manual-review classification, `PATIENT_REGISTRATION_RECOVERY_REQUIRED` and `PATIENT_REGISTRATION_RECOVERY_REVIEW_OPENED` are staged in the same database transaction as case creation and committed once.

If case creation or either audit enqueue fails, the database transaction rolls back and the recovery attempt claim is released when safe. A patient is not told a durable case exists until the case and audit outbox entries commit.

After commit, the exact recovery attempt is consumed. If consumption fails, retrying verification may return the same durable case because creation is idempotent; it must not create another case.

## Patient response

The verified manual-review branch returns only:

- stable error code `REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED`;
- opaque durable `case_reference`.

It does not return provider subject/hash, identity ID, patient ID, graph fingerprint, normalized/internal reason code, reviewer identity, reviewer session binding, or repair authority.

## Step 2 qualification

Focused tests must cover reason normalization, raw-subject non-persistence, exact identity anchoring, duplicate/idempotent creation, conflicting existing-row fail-closed behavior, audit staging, and the verified route returning only the durable case reference. Real PostgreSQL duplicate-race qualification is completed again in the later adversarial qualification step.
