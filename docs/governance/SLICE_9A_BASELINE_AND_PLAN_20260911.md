# Slice 9A — Registration Recovery Review Baseline and Plan

Status: **ACTIVE / DRAFT / NOT MERGE-ELIGIBLE**

Established: 2026-09-11

Authoritative base: `main` `54351f9a55ba94665420961cfe766bdcc84a5398`

Parent dependency: patient registration account recovery PR #40 merged from exact reviewed head `309d85b7a79474cc3ee62cc63d4f707d7d2ae59c` after fully green Backend CI #487 and Frontend CI #436.

This branch was created directly from the verified post-merge `main`. It intentionally does **not** merge the stale Slice 9A branch into this lineage. Reviewed Slice 9A files are transplanted deliberately and re-reviewed against this new parent baseline.

## Authority boundaries retained

- verified Supabase OTP proves control of the external patient identity only;
- registration recovery attempt state is not login, device, consent, or reviewer authority;
- automatic recovery remains limited to its already-qualified closed repair allowlist;
- manual-review classification may create durable review state but may not itself grant repair authority;
- reviewer authority is independent from clinical capability, patient consent, patient authentication, and device authority;
- reviewer repair never issues patient login, device, or consent authority;
- erasure and revocation remain independent fail-closed authorities.

## Slice 9A implementation sequence

1. Re-establish and re-review the dedicated reviewer authorization gate on this base.
2. Connect verified patient manual-review classification to idempotent case creation.
3. Add patient-visible case-status API.
4. Add reviewer claim/recovery semantics.
5. Add terminal disposition/repair service.
6. Lock and recompute the registration graph before every repair.
7. Make repair plus `PATIENT_REGISTRATION_RECOVERY_REVIEW_RESOLVED` audit transactional.
8. Add PostgreSQL race/adversarial qualification.
9. Update migration-head contracts and route registry.
10. Freeze one exact head, run Backend + Frontend CI, review the final diff, and merge only if genuinely green.

## Review discipline

Before moving from any numbered step to the next, review the implementation and its tests. Any material authority, privacy, schema, concurrency, migration, or qualification finding is written to the rolling Slice 9A review markdown before dependent work proceeds.
