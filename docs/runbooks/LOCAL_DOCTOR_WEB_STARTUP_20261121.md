# Local backend + doctor website (web only) — 21 November 2026

**Scope:** private, disposable, synthetic-only Windows development rehearsal. The Android test phone is occupied by another application. **No phone, Metro, ADB, NFC, cloud deployment, real patient data, production database, signing-key ceremony or investor-demo approval is used.** This script is a development startup convenience, not a live verification report.

## Fixes versus the older all-device launcher

The older `scripts/start_nexa_dev.ps1` starts Uvicorn and Next with `NEXT_PUBLIC_API_URL=http://localhost:8000`, and requires Android SDK/JDK/ADB even for doctor-web-only usage. Nexa's browser provider authentication uses first-party, HttpOnly cookies with CSRF enforcement; `next.config.js` already supports forwarding `/api/:path*` through `API_PROXY_TARGET`. The new web-only launcher sets **`NEXT_PUBLIC_API_URL=http://localhost:3000` and `API_PROXY_TARGET=http://127.0.0.1:8000`**, so browser requests and provider cookies remain on the doctor origin while Next forwards API traffic to the local backend. This does not loosen production same-origin or CSRF rules.

## Required local setup

- Checkout exact [draft PR #101](https://github.com/sohamsadegaonkar/Nexa_Care/pull/101) revision, using a **dedicated private development machine** with PowerShell, Python virtual environment and installed backend dependencies, Node 22, Corepack/Yarn 4.5; no Android tooling is needed.
- Use an isolated, disposable synthetic PostgreSQL DB and Redis with a repository-compatible migration applied using the **existing dedicated migration runner**. Never run automatic migrations on API startup or point these steps to an existing live database.
- Seed the synthetic demo doctor, hospital and two exact-ID patients using the repo's approved development-only tools, with dummy MFA secret stored in the ignored local environment. Do not write secrets, medical documents or patient identifiers to committed files or logs.
- Ensure both localhost ports **8000 and 3000 are free**. This launcher refuses busy ports; it never kills existing applications.
- For actual PDF import/original access, configure fresh ClamAV scanner and patient source storage separately; **backend health alone does not qualify scanner freshness, original access, a clinical session or a physical phone**.

From the repo root, run:

```powershell
.\scripts\start_nexa_web.ps1
```

Or, to avoid opening a browser automatically:

```powershell
.\scripts\start_nexa_web.ps1 -NoBrowser
```

The launcher first performs read-only `python scripts/demo_preflight.py --discovery public-id`. On success it starts the backend **bound to 127.0.0.1:8000** and the doctor Next development server at **localhost:3000**, without Metro or ADB. It then probes the real local services:

| Probe | Expected result | Limit |
| --- | --- | --- |
| `GET http://127.0.0.1:8000/healthz` | 200 JSON `status=ok` | Process liveness only |
| `GET http://127.0.0.1:8000/health` | 200 JSON `status=ok`; PostgreSQL/Redis/audit outbox/workers all `ok` | Backend readiness, not patient consent |
| `GET http://localhost:3000/doctor/login` | 200 HTML | Login shell renders; not proof of login |
| `GET http://localhost:3000/api/v2/auth/web/session` without credentials | **401 JSON** from proxied FastAPI endpoint | Proves unauthenticated denial and same-origin proxy path; not a valid-session E2E |

A wrong endpoint, missing proxy, backend 503 or unavailable page emits `local_web_stack=NO_GO`, and the launcher will not report readiness. The checker sends no credentials and logs no headers or response bodies. No real login attempt is made. Passing output is **`local_web_stack=PREREQUISITES_READY`** with separate `device_signing=NOT_RUN`, `physical_nfc=NOT_RUN`, `clinical_release=HOLD`.

## Remaining work

- **NOT_RUN:** execute this new launcher on the owner’s configured Windows development machine (no connected machine is available to this repository-only change).
- **NOT_RUN:** legitimate seeded doctor login and MFA in an actual browser, cookie/CSRF session, real clinical grant, signed P1B original bytes/revocation, or observed phone key. Do not conflate the intentional unauthenticated 401 proxy probe with a real login.
- **NOT_RUN:** Azure live read/write/Key Vault/Layout or any production migration/deployment. The pre-existing Render service is not changed.
- CI: GitHub backend pure tests provide synthetic probe and static launcher regression; CI cannot establish the owner’s Windows/Next local runtime from those unit tests.

[Security non-regression](../governance/SECURITY_NON_REGRESSION.md) (SEC-001/002/003/013/016/018/020), [India regulatory baseline](../governance/INDIA_REGULATORY_BASELINE.md) (REG-003), [P1B access](../governance/P1B_ORIGINAL_DOCUMENT_ACCESS.md), [non-NFC setup](NON_NFC_ANDROID_LOCAL_PREFLIGHT_20261121.md). No change to patient/clinician permissions, clinical data, consent, audit, identity, AI extraction, object storage, erasure or emergency access. No migration added. Physical and independent review gates remain HOLD.


## Live CI doctor login defect and correction — 21 November 2026

The first CI-hosted real browser test (not owner Windows) proved FastAPI readiness and the first-party unauthenticated session denial. It then received **HTTP 200 with `status=mfa_required`** from the real password endpoint, but the UI stayed on the password page. The backend `ProviderWebLoginState` serializes `expires_at: datetime | None = None` as JSON **`null`**, while the original frontend Zod `z.string().optional()` rejects null; the client treated a genuine success as a validation error. The revised frontend discriminator accepts `{status:'mfa_required', expires_at:null}`, while still requiring a nonempty expiry for a completed authenticated session and denying malformed states. A dedicated Vitest contract test now freezes both. The new [real-backend Chromium workflow](../../.github/workflows/doctor-web-real-backend.yml) must be rerun on the corrected PR head to verify actual MFA, session cookie scoping and frontend navigation. An earlier HTTP 200 alone is NOT proof of successful MFA or authorized access.

This is a **client response-contract correction**, not any relaxation of FastAPI authentication, MFA requirements, Secure/HttpOnly cookies, CSRF checks, patient consent, identity trust or clinical scope. It does not authenticate an unsanctioned user. The CI fixture is synthetic/disposable; physical Android signing/NFC, owner's Windows runtime and live provider-with-patient consent remain NOT_RUN/BLOCKED. Do not merge or deploy based on the synthetic browser test.
