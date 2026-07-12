# Alpha Environment Setup

This runbook prepares Phase 2 infrastructure testing. It does not prove the physical-device consent loop. Never use real patient information.

## Workstation and branch

```powershell
Set-Location C:\Users\DELL\Nexa_Care
.\venv\Scripts\Activate.ps1
git branch --show-current
git rev-parse HEAD
git status --short
```

The branch must be `alpha-loop-testing`. Record the commit in `docs/alpha-validation/environment.md`.

## Required configuration

The backend requires `SUPABASE_URL`, `SUPABASE_KEY` (service-role key), `DATABASE_URL`, `UPSTASH_REDIS_URL`, `HANDSHAKE_PEPPER_SECRET`, `KEK_ROOT_SECRET`, `MFA_ENCRYPTION_KEY`, and `PII_ENCRYPTION_KEY`. `CLINIC_API_KEY` is deprecated and script-only but remains required by its legacy getter. `DEMO_PROVIDER_PASSWORD` is required only by `seed_demo_doctor.py`; the script never prints it.

`ENV` is canonical. The three reviewed legacy seed/enrollment scripts accept `ENVIRONMENT` only as a compatibility fallback when `ENV` is absent. Optional runtime controls are `ENCRYPTION_BACKEND`, `DATABASE_ECHO_SQL`, `MAX_UPLOAD_BYTES`, `PUSH_STATUS_TRANSPORT`, `DOCUMENT_AI_API_KEY`, `DOCUMENT_AI_API_URL`, `CORS_ALLOWED_ORIGINS`, and `TRUSTED_HOSTS`.

For local alpha and in-process tests, use:

```env
TRUSTED_HOSTS=localhost,127.0.0.1,testserver
```

`localhost` supports browser/local API calls, `127.0.0.1` supports direct
loopback calls, and `testserver` is the synthetic hostname used by
Starlette/FastAPI `TestClient`. Physical-phone testing may additionally
require the laptop's current LAN IP in the ignored local environment. Never
commit a workstation LAN IP. Deployed environments must list only their real
deployment domains.

Generate every value independently and never reuse secrets:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Run the Fernet command separately for `MFA_ENCRYPTION_KEY` and `PII_ENCRYPTION_KEY`. Run the random command separately for the pepper, KEK, and legacy script key. Store values only in ignored local files or a secret manager.

## `.env.alpha` and `.env`

Python calls `load_dotenv()` and therefore reads `.env`; `.env.alpha` is the editable alpha source kept separate for safety.

```powershell
.\scripts\setup_alpha_environment.ps1
# Edit .env.alpha locally without displaying it in logs.
.\scripts\setup_alpha_environment.ps1 -ValidateOnly
.\scripts\setup_alpha_environment.ps1 -CopyToDotEnv
python scripts/check_alpha_environment.py --config-only
```

Copying backs up an existing `.env`. Neither file may be committed.

## Infrastructure validation

`DATABASE_URL` must start with `postgresql+asyncpg://`. Hosted Upstash normally uses `rediss://`.

```powershell
python scripts/check_alpha_environment.py --check-postgres
python scripts/check_alpha_environment.py --check-redis
python scripts/check_alpha_environment.py --check-supabase
python scripts/check_alpha_environment.py --all
```

PostgreSQL runs only `SELECT 1`. Redis runs only `PING`. Supabase checks the authentication health endpoint and does not guess or access an application table.

## Backend and health

Bind to all interfaces so a phone on the same trusted LAN can connect:

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
Invoke-RestMethod http://127.0.0.1:8000/healthz
Invoke-RestMethod http://127.0.0.1:8000/health
```

`/healthz` is liveness; `/health` checks PostgreSQL and Redis readiness.

## Test data and consent preflight

These commands mutate only the configured alpha database. The patient seeder is idempotent; the doctor seeder uses upserts/checks but also creates demo identities and records. It intentionally does not fabricate a device key: enroll the authenticated physical device afterward. Confirm `ENV=alpha` and the target before running.

```powershell
python scripts/seed_demo_patient.py
python scripts/seed_demo_doctor.py
python scripts/consent_preflight.py
```

Do not run the local demo-device enrollment script as evidence of physical enrollment; it creates an exportable key file on the workstation.

## Mobile LAN configuration

The shared client reads `NEXT_PUBLIC_API_URL`. Before starting/building Expo, set it to the computer's LAN address—not `localhost`:

```powershell
$env:NEXT_PUBLIC_API_URL='http://192.168.x.x:8000'
ipconfig
Set-Location nexa-client
yarn workspace expo start --dev-client
```

The phone and computer must share a trusted LAN. Permit inbound TCP 8000 in Windows Defender Firewall only for the Private profile. Expo development permits HTTP LAN traffic through its development/native configuration; release builds should use HTTPS and must be rechecked for Android cleartext and iOS App Transport Security policy.

## Physical-device and enrollment readiness

A physical iOS/Android device with enrolled biometrics and an Expo development build is required. The patient UI route `/patient/secure-device` generates P-256 material, keeps the private key in SecureStore, sends only the DER/base64 public key to `/api/v2/patient/devices/enroll`, and reports success/failure. The backend associates enrollment with the authenticated patient and limits active devices to five.

Alpha limitation: SecureStore protects an exportable JS-generated key; this is not yet a hardware-backed, non-exportable Secure Enclave/StrongBox key. Physical behavior has not passed until recorded on actual hardware.

## Phase 2 checklist

- [ ] Configuration validation
- [ ] PostgreSQL `SELECT 1`
- [ ] Redis `PING` and temporary-key cleanup
- [ ] Supabase credentials configured and application access verified
- [ ] Backend `/healthz` and `/health`
- [ ] Alpha patient and provider seeded
- [ ] Phone reaches LAN backend URL
- [ ] Patient login on physical device
- [ ] P-256 device enrollment succeeds
- [ ] Biometric signed consent approval succeeds
- [ ] Scoped provider record read succeeds
- [ ] Patient access history shows the read
- [ ] Audit chain verifies

## Troubleshooting

| Symptom | Safe check |
|---|---|
| Missing variable | Run `python scripts/check_alpha_environment.py --config-only`; values are never printed |
| Async driver error | Use `postgresql+asyncpg://` and install `requirements.txt` |
| PostgreSQL DNS/TLS/auth failure | Recheck provider host, SSL requirements, allow-list, and credentials |
| Redis TLS/auth failure | Use the provider's `rediss://` URL and confirm token rotation/status |
| Phone cannot connect | Use the PC LAN IP, bind Uvicorn to `0.0.0.0`, and check Private-profile firewall rules |
| Mobile URL is empty | Set `NEXT_PUBLIC_API_URL` before starting or rebuilding Expo |
| Enrollment fails | Confirm patient authentication, database migration, device limit, and P-256 DER payload |
| Biometrics unavailable | Enroll device biometrics and use a development build with native modules |
