# Nexa Care — Current State

**Purpose:** Single ground-truth tracking document for Phase 0 stabilization.
Not an architecture doc. Not a design doc. This describes what the code
*actually does today*, verified against the repository, so the team can
align before Phase 1 (Consent Consolidation) begins.

**Verification method:** Every row below was confirmed by reading the
source directly (imports, call graphs, Redis key prefixes, payload
schemas) — not inferred from comments, docstrings, or prior audit
write-ups. Where a docstring or comment made a claim the code didn't
back up, that's called out explicitly.

**Last updated:** 2026-07-02

---

## 1. Consent Systems

There are **four** independent consent/token mechanisms in the codebase
today, not one, and not two.

| System | Location | Redis Key Prefix | Status | Decision |
|---|---|---|---|---|
| **Legacy v1** | `app/core/redis.py` (`issue_consent_token` / `resolve_consent_token`), consumed by `app/api/routes.py` (`/api/v1/request-consent`, `/api/v1/view-record/*`) | bare UUID keys | **Working, fully self-consistent.** Complete handshake → register → consent → view chain, scope enforcement (clinical vs. full), audit-before-response. | Deprecate — migrate onto `ConsentEngine` in Phase 1, do not extend further |
| **`consent_service.py`** | `app/services/consent_service.py`, issuer for `POST /api/v2/consent/grant`; also backs `require_active_consent` used by `fhir_routes.py` | `nexa_cons_` | **Working, internally consistent.** FHIR export path functions correctly end-to-end. | Deprecate — migrate onto `ConsentEngine` in Phase 1 |
| **`routine.py`** | `app/services/consent/routine.py`, the *only* validator `GET /api/v2/patient/{id}/record` (patient reconstruction) accepts | `nexa:routine_consent:` | **Broken — structurally unreachable.** `routine.issue()`, the only function that can mint a token this endpoint accepts, has **zero production callers**. Only exercised by `tests/test_consent_state_machines.py`, which mocks `routine.validate`/`routine.consume` directly rather than driving a real grant → access flow. Every real request to `/api/v2/patient/{id}/record` returns `403`, permanently. | Deprecate — migrate onto `ConsentEngine` in Phase 1 |
| **`break_glass.py`** | `app/services/consent/break_glass.py` | `nexa:break_glass:` | **Dead code.** Fully implemented (own Redis prefix, own compliance-queue key), but no route (`consent_routes.py`, `emergency_routes.py`, `patient_routes.py`) calls `issue()` or `validate()`. Only reachable from its own unit test. Emergency access today is actually handled by a separate, unrelated mechanism in `emergency_routes.py` (NFC-card/snapshot). | Fold into `ConsentEngine` as the emergency-access path, or explicitly retire if `emergency_routes.py`'s mechanism is the intended long-term design — **open decision, not yet made** |

**Decision (2026-07-02):** None of the three v2 systems becomes canonical
as-is. A fresh **`ConsentEngine`** will be built to replace `consent_service.py`
and `routine.py`. The legacy v1 system stays running unmodified until
routes are migrated. `break_glass.py`'s fate is still open — see Section 4.

**Naming hazard worth fixing regardless of outcome:** `consent_service.py`
defines a function called `verify_routine_consent`, which checks the
`nexa_cons_` namespace — *not* `routine.py`'s `nexa:routine_consent:`
namespace. The name is close enough to `routine.py`'s real functions
that it's a plausible contributor to why the `/patient/{id}/record`
mismatch went unnoticed. Rename or remove during the `ConsentEngine`
migration regardless of which module wins.

---

## 2. Active Bug Triage

Ordered by operational risk, not architectural elegance.

| Issue | Severity | Status | Action |
|---|---|---|---|
| Patient reconstruction endpoint unreachable (`/api/v2/patient/{id}/record` always 403s) | **P0** | Confirmed, root cause understood | **Blocked on `ConsentEngine`.** Do not patch `routine.py` — that repairs a module being deprecated. Fix lands as part of Phase 1 migration. |
| Audit ledger collision-retry logic is dead code | **P0** | **Fixed 2026-07-03** | Root cause confirmed against the actual pinned dependency: `supabase==2.9.1` → `postgrest>=0.17.0,<0.18.0`; read `postgrest==0.17.2` source directly — `.execute()` raises `APIError` on any non-2xx response, it never returns an object with a populated `.error`. The old `getattr(response, "error", None)` checks were unreachable; a real 23505 collision was always swallowed by the generic `except Exception`, returning `False` with zero retries. Rewrote to catch the raised exception and duck-type on its `.code` attribute (no `postgrest` import, per existing convention). Added two tests that actually exercise the retry path — `test_unique_violation_retries_and_succeeds` and `test_unique_violation_exhausts_retries_returns_false` — neither existed before; the old suite mocked `.execute()` returning `.error` and so could never have caught this. Full suite: 129/129 passing. |
| `scripts/smoke_test.py` will fail on every run | **P1** | **Fixed 2026-07-03** | Root cause confirmed via `app/core/config.py`: `get_clinic_config()`'s own docstring says it's deprecated — *"provider routes now authenticate individual clinicians via `get_provider_context`... retained only for scripts that have not yet migrated."* `CLINIC_API_KEY` is never read by `get_provider_context()`; nothing in `app/` calls `get_clinic_config()` at all. Switched the script to HTTP Basic auth (`PROVIDER_EMAIL`/`PROVIDER_PASSWORD`, defaulting to `seed_test_data.py`'s seeded test provider) against `provider_credential`, matching how `get_provider_context()` actually authenticates. **Not run end-to-end here** — this sandbox has no network access to a live Postgres/Redis/Supabase stack. Verified by reading `authenticate_provider_password` and FastAPI's `HTTPBasic` scheme directly (RFC 7617, confirms the base64(user:pass) construction is correct) plus a compile check. Needs one real run against staging to close out. |
| MFA is a functional dead end | **P2** | Confirmed | `authenticate_provider_password()` returns `MFA_REQUIRED` for any provider with `mfa_enabled=True`, but no `/mfa/verify` route exists anywhere. Not a security hole (fails closed) — but any provider row with MFA flipped on in prod is permanently locked out. Decide: implement MFA verification, or remove the dead branch until it's built. |
| `break_glass.py` unused | **P2** | Confirmed | Leave until consent migration decision (Section 4) resolves its fate. |
| `review_routes.py` audits after mutation, not before | **P2** | Confirmed | Every other write path (biometric enrollment, card-lost reporting, consent grant) audits the attempt *before* mutating and hard-fails if that audit write fails. `approve_review`/`reject_review` do it backwards: DB write commits and `review.status` flips first, `append_audit_log_or_503` is called after. A 503 there leaves a durably committed, unaudited change with no safe retry path (status is no longer `PENDING`). Bring in line with the audit-before-write pattern used everywhere else. |
| `auth_service.py`'s `session_authorizes_patient()` docstring overstates what's enforced | **P3 (doc/hygiene)** | Confirmed | Docstring claims it "centralizes the scope check used by both `GET /api/v1/record/{id}` and `POST /request-consent`." Neither route calls it — `get_scoped_session()` in `dependencies.py` pulls `masked_internal_id` straight off the session dict instead. Not a live vulnerability (those routes never accept a patient ID as external input), but the docstring asserts a guarantee the code doesn't actually provide. Fix the comment or wire it in. |

**Correction to prior tracking:** `python-multipart` ReDoS CVE, previously
flagged as an open critical finding, is **resolved** —
`requirements.txt` pins `python-multipart>=0.0.9`. Worth a one-time
`pip freeze` check against the actual deploy environment to confirm the
installed version matches the lockfile, but the source of truth is clean.
No further action tracked here.

---

## 3. Architecture CI Guardrail (Phase 0)

Per the roadmap, add a static check before any further consent work:
fail CI if a production route file imports more than one of
`consent_service`, `app.services.consent.routine`, or
`app.core.redis`'s consent functions. This is intentionally cheap — it
would have caught the `patient_routes.py` / `consent_routes.py` mismatch
immediately instead of it surviving unnoticed.

Status: **Implemented 2026-07-03** — `tests/test_architecture.py`. AST-based, no server/DB needed. Two tests: one is an intentional `@expectedFailure` that documents the exact known violation (`consent_service` + `routine` both live in v2 production routes) and will flip to "unexpected success" — the signal to remove the decorator — once Phase 1 lands; the other is a real, always-enforced tripwire that fails immediately if a third consent family (e.g. `break_glass`) creeps into production routes before the migration. Verified against a synthetic third-family import to confirm it actually fires, not just that it imports cleanly. Legacy v1 (`app/api/routes.py`) is intentionally out of scope — tracked as a whole-system deprecation, not intra-v2 drift.

---

## 4. Open Decisions

- **`break_glass.py` fate** — fold into `ConsentEngine` as the emergency
  path, or retire it in favor of `emergency_routes.py`'s existing
  NFC/snapshot mechanism. Needs a decision before Phase 1 migration
  touches emergency access.
- **MFA** — build the verify endpoint, or strip the dead
  `MFA_REQUIRED` branch until it's ready to be built. Low urgency until
  someone flips `mfa_enabled=True` on a real provider row, at which
  point it becomes a P0 lockout.
- **`crypto_kms.py` column sign-off** — unrelated to consent, still
  pending from prior sessions. Not re-litigated here; tracked for
  visibility only.

---

## 5. What's Confirmed Clean

Read and verified, no issues found: `crypto_engine.py`,
`biometric_registry.py` / `credential_registry.py`, `card_resolution_service.py`,
`emergency_routes.py`, `document_routes.py`, `fhir_routes.py`,
`sharding.py`, `provider_auth_service.py` (aside from the MFA dead end
above), `emergency_snapshot_service.py`, `fhir_converter.py`,
`secure_record.py`, `logging_middleware.py`, `error_catalog.py`. Proper
fail-closed behavior, parameterized SQL, audit-before-read ordering, no
IDOR gaps found in this pass.