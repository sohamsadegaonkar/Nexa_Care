# Nexa Care

Nexa Care is a privacy-first health-record platform that helps track doctor productivity, patient revisit patterns, and appointment durations through consent-scoped dashboards.

- **Backend**: FastAPI (Python) with vertical PII/clinical data sharding, Redis-backed consent tokens, audit-before-write, and provider authentication.
- **Frontend**: Tamagui monorepo with Next.js web and Expo mobile apps.

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/sohamsadegaonkar/Nexa_Care.git
cd Nexa_Care
cp .env.example .env
# Edit .env with your Supabase, Postgres, Redis, and pepper-secret values.
```

### 2. Backend

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# The project pins Python 3.12 (see .python-version and pyproject.toml).
# Ensure your environment is on 3.12 before installing.
pip install -r requirements.txt -r requirements_dev.txt

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Liveness check (no dependencies): `GET http://localhost:8000/healthz`  
Readiness check (verifies Redis + Postgres): `GET http://localhost:8000/health`  
API docs: `http://localhost:8000/docs`

### 3. Tests

```bash
ruff check .
python -m pytest tests/ -v
```

### 4. Frontend (optional)

The frontend is in `nexa-client/`. See its README for the full monorepo setup.

```bash
cd nexa-client
yarn install
yarn web        # Next.js dev server
yarn native     # Expo dev server
```

## Deployment on Render

Render's own platform health check should point at the **liveness** probe,
not the readiness probe:

- **Health Check Path: `/healthz`**

`/healthz` returns `200 {"status": "ok"}` as soon as the FastAPI app has
finished starting up, and it never touches Redis, Postgres, Supabase, or
any other external dependency. That's what Render needs to decide "is the
process alive" during and after a deploy.

`GET /health` is a separate **readiness/dependency** check. It actively
pings Redis and runs `SELECT 1` against Postgres, and returns `503` if
either is unavailable. That's the correct behavior for `/health` (it's
useful for your own monitoring/alerting), but it means `/health` can
legitimately return `503` for reasons that have nothing to do with the
app itself being up -- e.g. Redis briefly unreachable, a Postgres
connection-pool blip. Pointing Render's health check at `/health` will
cause Render to kill/restart an otherwise-healthy deploy whenever a
dependency has a transient hiccup. Use `/healthz` for Render; keep using
`/health` for dependency monitoring.

## Key flows

- **Provider login** → `POST /api/v2/auth/login`. If MFA is enabled, the response includes an `mfa_token`; complete login with `POST /api/v2/auth/mfa/verify`.
- **NFC scan** → `POST /api/v2/nfc/resolve` returns a masked patient ID.
- **Routine consent** → `POST /api/v2/consent/routine/issue` returns a time-bound consent token.
- **Patient record** → `GET /api/v2/patient/{patient_id}/record` with `X-Consent-Token` and `X-Consent-Purpose` returns a scoped record.
- **Emergency break-glass** → `POST /api/v2/consent/break-glass/issue` issues a short-lived, audited emergency token.
- **Patient self-consent** (v1) → `POST /request-consent` and `GET /view-record/*` now also run through `ConsentEngine` with a synthetic `patient:self` actor.

## Project structure

```
app/               # FastAPI backend
  api/v1/          # Legacy routes (handshake, register, consent view)
  api/v2/          # Provider-centric v2 routes (auth, consent, patient, emergency, nfc, reviews, fhir)
  services/        # ConsentEngine, auth, biometric/card resolution, sharding, etc.
  observability/   # Audit ledger, redactor, error catalog
  core/            # Config, database, Redis, dependencies
docs/              # Architecture and current-state notes
migrations/        # Postgres/Supabase SQL migrations
scripts/           # Seed data, smoke test, NFC simulator
nexa-client/       # Tamagui monorepo (Next.js + Expo)
```

## Notes

- `CLINIC_API_KEY` is deprecated and only used by legacy scripts. Provider routes authenticate via `provider_credential` (HTTP Basic or Bearer session token).
- MFA is implemented via TOTP (`POST /api/v2/auth/mfa/setup` and `POST /api/v2/auth/mfa/verify`).
- Provider sessions are bound to User-Agent (hard check) and client IP (soft check). UA mismatch returns `401`; IP mismatch is allowed but logs `SESSION_IP_ROTATION_DETECTED`.
- `POST /api/v2/auth/refresh` rebinds the new session token to the current request's UA/IP.
- MFA brute-force lockout uses the composite Redis key `mfa_fails:{provider_id}:{ip_hash}`.
- The legacy combined `raw_pii` JSONB blob has been removed from `nexa_vault`; PII is encrypted at rest into the `patient_name`, `phone`, and `aadhaar_abha_id` columns. Alembic migration `20260704_drop_raw_pii_from_vault` drops the column.
