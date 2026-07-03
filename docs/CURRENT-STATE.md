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

**Updated 2026-07-03:** The Phase 1 consent consolidation is complete.
The old v2 consent systems (`consent_service.py`, `routine.py`,
`break_glass.py`) have been removed and replaced by a single
`ConsentEngine` in `app/services/consent_engine.py`. The legacy v1 system
in `app/core/redis.py` and `app/api/routes.py` remains in place until its
routes are migrated.

| System | Location | Redis Key Prefix | Status | Decision |
|---|---|---|---|---|
| **Legacy v1** | `app/core/redis.py` (`issue_consent_token` / `resolve_consent_token`), consumed by `app/api/routes.py` (`/api/v1/request-consent`, `/api/v1/view-record/*`) | bare UUID keys | **Working, fully self-consistent.** Complete handshake → register → consent → view chain, scope enforcement (clinical vs. full), audit-before-response. | Deprecate — migrate onto `ConsentEngine`, do not extend further |
| **`ConsentEngine`** | `app/services/consent_engine.py`, consumed by `app/api/v2/consent_routes.py`, `app/api/v2/patient_routes.py`, and `app/core/dependencies.py` | `nexa:consent:` | **Working, consolidated.** Issues routine and break-glass tokens, dual-writes to Postgres and Redis, enforces purpose + scope matching, and routes break-glass events to the compliance queue. | Canonical v2 authority |

The v2 production surface now exposes three endpoints:
- `POST /api/v2/consent/grant` — generic provider consent grant with explicit scope
- `POST /api/v2/consent/routine/issue` — scanner-facing routine grant with a default scope
- `POST /api/v2/consent/break-glass/issue` — emergency override with reason and free-text justification

All three delegate to `ConsentEngine.issue()`. The frontend contract now
matches the backend (see Section 6).

---

## 2. Active Bug Triage

Ordered by operational risk, not architectural elegance.

| Issue | Severity | Status | Action |
|---|---|---|---|
| Patient reconstruction endpoint unreachable (`/api/v2/patient/{id}/record` always 403s) | **P0** | **Fixed 2026-07-03** | Migrated `consent_routes.py` and `patient_routes.py` onto `ConsentEngine`. The endpoint now validates and consumes a live `nexa:consent:` token, and the frontend contract exposes `/api/v2/consent/routine/issue` and `/api/v2/consent/break-glass/issue` to mint those tokens. Full suite: 173/173 passing. |
| Audit ledger collision-retry logic is dead code | **P0** | **Fixed 2026-07-03** | Root cause confirmed against the actual pinned dependency: `supabase==2.9.1` → `postgrest>=0.17.0,<0.18.0`; read `postgrest==0.17.2` source directly — `.execute()` raises `APIError` on any non-2xx response, it never returns an object with a populated `.error`. The old `getattr(response, "error", None)` checks were unreachable; a real 23505 collision was always swallowed by the generic `except Exception`, returning `False` with zero retries. Rewrote to catch the raised exception and duck-type on its `.code` attribute (no `postgrest` import, per existing convention). Added two tests that actually exercise the retry path — `test_unique_violation_retries_and_succeeds` and `test_unique_violation_exhausts_retries_returns_false` — neither existed before; the old suite mocked `.execute()` returning `.error` and so could never have caught this. Full suite passing. |
| `scripts/smoke_test.py` will fail on every run | **P1** | **Fixed 2026-07-03** | Root cause confirmed via `app/core/config.py`: `get_clinic_config()`'s own docstring says it's deprecated — *"provider routes now authenticate individual clinicians via `get_provider_context`... retained only for scripts that have not yet migrated."* `CLINIC_API_KEY` is never read by `get_provider_context()`; nothing in `app/` calls `get_clinic_config()` at all. Switched the script to HTTP Basic auth (`PROVIDER_EMAIL`/`PROVIDER_PASSWORD`, defaulting to `seed_test_data.py`'s seeded test provider) against `provider_credential`, matching how `get_provider_context()` actually authenticates. **Not run end-to-end here** — this sandbox has no network access to a live Postgres/Redis/Supabase stack. Verified by reading `authenticate_provider_password` and FastAPI's `HTTPBasic` scheme directly (RFC 7617, confirms the base64(user:pass) construction is correct) plus a compile check. Needs one real run against staging to close out. |
| MFA is a functional dead end | **P0** | **Fixed 2026-07-03** | Implemented TOTP MFA via `pyotp`: `POST /api/v2/auth/login` returns a short-lived `mfa_token` when `mfa_enabled=True`; `POST /api/v2/auth/mfa/verify` completes login with the TOTP code; `POST /api/v2/auth/mfa/setup` enrolls a provider. The inconsistent state `mfa_enabled=True` with no secret now returns `MFA_NOT_CONFIGURED` (500) instead of locking the account permanently. |
| Break-glass consent | **P2** | Folded into ConsentEngine | `POST /api/v2/consent/break-glass/issue` now issues a short-lived, audited break-glass token via `ConsentEngine`. The old standalone `break_glass.py` module is gone. |
| `review_routes.py` audits after mutation, not before | **P2** | **Fixed 2026-07-03** | `approve_review` and `reject_review` now use `append_audit_log_or_503()` before any DB mutation, matching the audit-before-write pattern used everywhere else. |
| `auth_service.py`'s `session_authorizes_patient()` docstring overstates what's enforced | **P3 (doc/hygiene)** | Confirmed | Docstring claims it "centralizes the scope check used by both `GET /api/v1/record/{id}` and `POST /request-consent`." Neither route calls it — `get_scoped_session()` in `dependencies.py` pulls `masked_internal_id` straight off the session dict instead. Not a live vulnerability (those routes never accept a patient ID as external input), but the docstring asserts a guarantee the code doesn't actually provide. Fix the comment or wire it in. |
| Frontend ↔ backend API contract drift | **P0** | **Fixed 2026-07-03** | The frontend expected `/api/v2/consent/routine/issue`, `/api/v2/consent/break-glass/issue`, and `/api/v2/nfc/resolve`; the backend only exposed `/api/v2/consent/grant` and `/api/v2/emergency/read-card`. Added the missing backend endpoints and wired the scanner and emergency screens into the home screen. |
| Legacy v1 consent routes (`/request-consent`, `/view-record/*`) | **P1** | **Fixed 2026-07-03** | Migrated v1 patient self-consent routes onto `ConsentEngine`. V1 self-consent uses a synthetic `patient:self` clinician ID and `patient_self_access` purpose, with scope mapped to ConsentEngine scopes (`"clinical"` → `["clinical.*"]`, `"full"` → `["clinical.*", "pii.*"]`). Updated `tests/test_api.py` to patch the async ConsentEngine Redis client and override the database dependency. |

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

- **`crypto_kms.py` column sign-off** — unrelated to consent, still
  pending from prior sessions. Not re-litigated here; tracked for
  visibility only.
- **Python version drift** — Dockerfile and CI target Python 3.12. A
  `.python-version` file and `pyproject.toml` `requires-python` constraint
  now pin the project to 3.12, but the local test run was performed on
  3.13.13. Align local dev environments to 3.12 before declaring CI parity.

---

## 5. What's Confirmed Clean

Read and verified, no issues found: `crypto_engine.py`,
`biometric_registry.py` / `credential_registry.py`, `card_resolution_service.py`,
`emergency_routes.py`, `document_routes.py`, `fhir_routes.py`,
`nfc_routes.py`, `sharding.py`, `provider_auth_service.py` (aside from the MFA dead end
above), `emergency_snapshot_service.py`, `fhir_converter.py`,
`secure_record.py`, `logging_middleware.py`, `error_catalog.py`. Proper
fail-closed behavior, parameterized SQL, audit-before-read ordering, no
IDOR gaps found in this pass.

---

## 6. Integration Fixes (2026-07-03)

- Added `POST /api/v2/consent/routine/issue` and `POST /api/v2/consent/break-glass/issue` to `app/api/v2/consent_routes.py`, both delegating to `ConsentEngine`.
- Added `POST /api/v2/nfc/resolve` in new `app/api/v2/nfc_routes.py`, gated by `get_provider_context` and delegating to `CardResolutionService`.
- Wired the shared `ScannerScreen` and `SearchScreen` into the Expo and Next home screens, and added the missing Expo/Next `scanner` routes.
- Updated `tests/test_route_registration.py` and `tests/test_architecture.py` to reflect the new routes and the ConsentEngine-only architecture.
- Implemented TOTP MFA: `POST /api/v2/auth/mfa/setup` and `POST /api/v2/auth/mfa/verify`; login now returns an `mfa_token` when MFA is enabled.
- Migrated legacy v1 consent routes (`/request-consent`, `/view-record/*`) onto `ConsentEngine` using a synthetic `patient:self` self-consent model.
- Added `.env.example`, `pyproject.toml`, `.python-version`, improved `.dockerignore`, and rewrote `README.md` with setup instructions.
- Verified frontend build: `yarn install`, `yarn test`, and `yarn build` all succeed in the Tamagui monorepo.
- Full Python test suite: **175/175 passing**, `ruff` clean.