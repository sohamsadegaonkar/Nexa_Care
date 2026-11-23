# Nexa Care — isolated staging and gated promotion (23 Nov 2026)

**State: source-only STAGING FOUNDATION — NOT DEPLOYED. NO STAGING URL EXISTS YET. NO PRODUCTION PROMOTION IS AUTHORIZED.**

## What is implemented

- Separate branch `staging/nexa-care-qualification-20261123` starts from draft PR #102 `e92870152bffb5bb2ae5c3371cf1fadf9f28e59c`, not `main`. This includes dependency upgrades and its existing tests, **not** unmerged original-document P1B PR #99/#101 changes.
- On every push to the staging branch, `.github/workflows/staging-qualification.yml` runs **synthetic** patient OTP, PyJWT, ASGI request-boundary, malicious PDF/image, signed-consent/replay, capability isolation and erasure tests. The OTP test stubs Supabase; no SMS is sent, no live patient account exists, and **no backend OTP bypass is enabled**.
- The staging HTTPS check is fail-closed until GitHub repository variable `NEXA_STAGING_BASE_URL` points to an isolated, deployed HTTPS site. `scripts/staging/check_target.py` rejects the known current production host and non-HTTPS/non-allowlisted or local URLs.
- This branch cannot trigger a production deployment: the new workflow has read-only `contents` permission and no deployment credentials or production jobs.

## Runtime and hosting preparation before a real URL

Existing Render workspace has `Nexa_Care` service at `https://nexa-care.onrender.com`, on `main` with auto-deploy **enabled**. It is **not a staging target**; never repoint or change it in this exercise. No Nexa Care-specific Render PostgreSQL or Key Value instance currently exists in the connected workspace. `deploy/legacy/render.remote-pilot.yaml` is explicitly deprecated and must not be reused.

Provision a new *isolated* staging deployment only after reviewing the deployment architecture and separate provider resources. Use an approved staging host such as `https://nexa-care-staging.onrender.com` **only if the provider actually assigns that URL** (example, not provisioned). The backend currently requires production-like validation for `ENVIRONMENT=staging`, including independent secrets, TLS database and Redis authority, properly configured data storage, malware scanner and auth service, non-production domain/allowed origins and own seed-only patient/provider/hospital identities. Do not connect production Supabase/PostgreSQL/Redis/S3/Azure resources or share encryption/session keys.

Staging OTP strategy: **synthetic fixture identities** only. For CI use fake Supabase / SMS callbacks and controlled deterministic code via test mocks. For a live hosted staging website prefer an isolated test identity provider / sandbox OTP interception service with explicit allowlist + auth and audit, never a global `123456` or bypassing normal identity/session/CSRF/consent checks. A hosted OTP implementation has **not** been deployed or enabled.

## Promotion pipeline (future policy, not an enabled production deploy)

1. Staging candidate starts on its own versioned SHA. Auto-run backend pure/PostgreSQL/Redis, dependency audit, CodeQL, scanner, frontend/browser and Android/iOS builds **on that SHA**.
2. Deploy to staging **only after** the safety configuration check and isolated infrastructure have passed; do not auto-deploy the branch to an unconfigured public site. Probe HTTPS `/healthz` and test synthetic OTP in a sandbox IDP.
3. Exercise bad tokens/expired/replayed OTP, PDF/image corruption, encryption & erasure, signed consent, missing approval, cross-patient/cross-hospital denial, revocation before/after access, concurrent original-source open and real Chromium browser paths; record environment, test identities, SHA and artifacts. Include P1B original-source path only after separately integrating / qualifying PR #99 and PR #101.
4. If **any** gate fails, mark this candidate **NO GO**. Fix in an isolated branch and **repeat all gates on a new SHA**. Never automatically declare a failed candidate ready due to a stale older check.
5. Once all automated and observed physical-device/browser checks pass, independent security, privacy and clinical reviewers must supply **actual** names, dates, reviewed SHA, scope and explicit approval. Release owner must perform a final **manual** production GO decision.
6. After signed approval, production promotion should be via a protected release PR / GitHub Environment with required reviewers, no bypass/self-review, immutable commit/digest, backup/rollback plan and post-deploy smoke. **No automatic unattended promotion, no direct push to `main`**, and no production data in staging.

## Outstanding approvals and blockers

- [ ] Choose/approve staging hosting and paid/free resource footprint and region; provision independent DB, Redis, auth/SMS sandbox and storage; protect preview with access restrictions.
- [ ] Decide real staging URL; set repository variable `NEXA_STAGING_BASE_URL`, stage-only secrets in protected GitHub staging Environment.
- [ ] Integrate PR #99/#101 original document workflow safely into the staging candidate without altering draft production PRs; resolve conflicts and rerun independent tests.
- [ ] Staging browser/P1B test fixture with scripted OTP through sandbox provider and registered synthetic patient/provider/hospital accounts.
- [ ] Android/iOS, actual phone biometric signing, human clinical/privacy/security sign-offs (tracking [#105](https://github.com/sohamsadegaonkar/Nexa_Care/issues/105)).
- [ ] Set up protected production GitHub Environment and release-owner approval. **Do not enable production auto deploy based on this staging job.**

Related work: [draft PR #102](https://github.com/sohamsadegaonkar/Nexa_Care/pull/102), [draft PR #101](https://github.com/sohamsadegaonkar/Nexa_Care/pull/101), [draft PR #99](https://github.com/sohamsadegaonkar/Nexa_Care/pull/99), [MFA #103](https://github.com/sohamsadegaonkar/Nexa_Care/issues/103), [break-glass #104](https://github.com/sohamsadegaonkar/Nexa_Care/issues/104), [review gate #105](https://github.com/sohamsadegaonkar/Nexa_Care/issues/105).


## 23 Nov 2026 — Approved isolated Render resources (actual provisioning)

The owner approved proceeding with isolated staging hosting infrastructure. **Two free-plan, Singapore-region resources were created in the connected Render workspace `tea-d8sftp7avr4c73fo8pbg`:**
- PostgreSQL `nexa-care-staging-db-20261123`, id `dpg-dc1vb3hksfps73a22vpg-a`, PostgreSQL 16, [Render dashboard](https://dashboard.render.com/d/dpg-dc1vb3hksfps73a22vpg-a); created 2026-11-23 08:13 UTC, **free instance expiry 2026-12-23 08:13 UTC**. Was `creating` at first post-create verification. This database is **not production ready** and expiry cannot be treated as a durable release host.
- Redis-compatible Render Key Value `nexa-care-staging-redis-20261123`, id `red-dc1vb4jl550s73flueug`, version 8.1.10, free plan, `noeviction`, **persistence off**. First post-create verification: `available`. This instance is for **disposable non-PHI synthetic credentials only**, and must not be treated as durable consent/session storage during outage/restore qualifications.
- No existing Nexa Care Render service was modified. Existing `Nexa_Care` stays on `main` at `https://nexa-care.onrender.com` with its previous auto-deploy behavior unchanged.

**Outstanding before an actual public staging URL:**
1. Provision a *new* Supabase sandbox project after the owner explicitly selects the Supabase organization; never reuse the existing `alpha_testing` or other Supabase projects. Supabase hosted test phone numbers and fixed codes expire and require a configured phone SMS provider even when test-number SMS is not sent. Never publish test numbers/codes. Disable signups outside allowlisted synthetic fixtures.
2. Confirm independent hosted storage/scanner, TLS-only application DB and Redis connection strings, strict trusted hosts and CORS, unique secret values and isolation startup tests. Do not copy production secrets or production data. These resources have no connection credentials in the repository.
3. Only then create an isolated staging backend/frontend deployment **with autoDeploy initially off**, protect access, and verify it passes production-like boot checks plus `/healthz`. Register its actual assigned HTTPS URL as `NEXA_STAGING_BASE_URL` and rerun staging/CI.
4. Since the PostgreSQL free tier expires December 23 and Redis persistence is off, runtime failure/recovery, consent atomicity and production-like durability tests require separate ephemeral test containers or an approved durable plan. **Never claim full staging release qualification from these two disposable resources alone.**
