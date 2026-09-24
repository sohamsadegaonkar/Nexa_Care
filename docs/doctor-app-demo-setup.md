# Doctor App Demo Setup Guide

> Disposable-development only. This is not production, pilot, preview, or
> alpha onboarding. Use synthetic patients only and keep credentials in ignored
> environment files.

**Last updated:** 2026-09-22

This guide walks you through running the Nexa Care Doctor Web App against a
demo backend with seeded test data.

---

## Recommended disposable local stack (Windows)

Use this path for a visible local demo. It creates an ignored
`.env.demo.local`, a loopback-only disposable PostgreSQL database and Redis
container, and never targets the historical `.env` database.

First time only:

```powershell
.\scripts\start_demo_dev.ps1 -InitializeInfrastructure
.\scripts\start_demo_dev.ps1 -Migrate
.\scripts\start_demo_dev.ps1 -Seed
```

Start the visible stack:

```powershell
.\scripts\start_demo_dev.ps1 -BackendPort 8010 -WebPort 3010 -MetroPort 8081 -StartExpo
```

Open `http://127.0.0.1:3010/doctor/login`. Sign in with
`demo.doctor@nexacare.in`, the ignored `DEMO_PROVIDER_PASSWORD`, and a current
TOTP code from the ignored `DEMO_PROVIDER_TOTP_SECRET` in `.env.demo.local`.
Do not print either secret. The seed deliberately requires MFA.

For a physical Android development device, use the workstation's private LAN
IPv4 address and explicitly restrict the allowed phone subnet. This is the
only launcher mode that binds the API beyond loopback; it binds only the exact
literal LAN address and the route separately enforces the supplied RFC1918
CIDR. PostgreSQL and Redis remain loopback-only:

```powershell
.\scripts\start_demo_dev.ps1 -BackendPort 8010 -WebPort 3010 -MetroPort 8081 -StartExpo `
  -MobileApiUrl http://<LAN-IP>:8010 -MobileClientCidr <LAN-CIDR>
```

The closed synthetic-patient login appears only in this development stack and
still performs ordinary Redis session issuance and native P-256 device
enrollment. A development client on a real Android device or emulator is
required to create the active device key; the seed never fabricates one.

With that device attached, install or refresh the native development client
from `nexa-client`:

```powershell
corepack yarn workspace expo-app android
```

---

## Prerequisites

- Python 3.12.x with the project's virtual environment active
- Docker Desktop for the launcher-managed loopback PostgreSQL and Redis
- Node.js 18+ and Yarn 4+ (for the frontend)

---

## 1. Launcher-managed configuration

Do not set a cloud/Supabase target, edit `.env`, or run Uvicorn manually for
this walkthrough. The launcher owns the ignored `.env.demo.local`, verifies a
loopback `nexa_qual_demo_*` target before every mutation, and starts the doctor
proxy with the matching local API target.

---

## 2. Seed the Demo Doctor

Use the three launcher commands shown above for first-time infrastructure,
migration, and seed setup. The normal `-Seed` operation preserves a complete
current synthetic fixture; it fails closed if an existing fixture has missing,
expired, revoked, or changed clinical-trust state. It never targets `.env`, a
cloud service, or a non-loopback database.

This creates:

| Resource | Value |
|----------|-------|
| **Doctor** | Dr. Meera Joshi |
| **Email** | `demo.doctor@nexacare.in` |
| **Password** | Value of the ignored local `DEMO_PROVIDER_PASSWORD` variable |
| **Hospital** | Nexa Demo Hospital (Mumbai) |
| **MFA** | Required; use the ignored local TOTP secret |

And two demo patients:

| Patient | ID | Notes |
|---------|----|----|
| Aarav Sharma | deterministic synthetic UUID (shown by the seed) | Has NFC card `04:B3:C1:DE:55:01` |
| Priya Patel | deterministic synthetic UUID (shown by the seed) | Manual search only |

---

## 3. Start the backend and doctor web app

Run the launcher command from the top of this guide. It starts FastAPI on
`127.0.0.1:8010`, Next on `127.0.0.1:3010`, and Metro on `127.0.0.1:8081`.
The ordinary emulator/browser path is loopback-only; no wildcard Uvicorn bind
is part of this guide.

---

## 4. Open the doctor experience

Open `http://127.0.0.1:3010/doctor/login` in a browser.

---

## 5. Step-by-Step Demo Flow

### 5.1 Login

1. Open `http://127.0.0.1:3010/doctor/login`
2. Enter **Email:** `demo.doctor@nexacare.in`
3. Enter **Password:** the value of your ignored local `DEMO_PROVIDER_PASSWORD`
4. Click **Sign In**
5. You are redirected to the Dashboard

> The disposable demo account requires TOTP MFA as well. Use the current code
> generated from the ignored local `DEMO_PROVIDER_TOTP_SECRET`.

### 5.2 Dashboard

After login, you see:
- **Provider name:** Dr. Meera Joshi
- **Hospital:** Nexa Demo Hospital
- **Role:** clinician
- **Provider Identity** card with the real provider UUID
- **Quick actions:** Search Patient, Scan NFC Card, Emergency Access
- **Pending Consent Requests:** (shows "No pending requests" initially)

### 5.3 Search Patient (Manual)

1. Click **🔍 Search Patient**
2. Mode is "Manual Search" by default
3. Enter the patient ID shown by `seed_demo_doctor.py` (e.g., the UUID for Priya Patel)
4. Click **Search**
5. You see "Patient Found" with the patient ID
6. Click **Request Access**

### 5.4 Search Patient (NFC)

1. From the Dashboard, click **📱 Scan NFC Card**
   (or navigate to `/doctor/patient-search?mode=nfc`)
2. Mode switches to "NFC Scan"
3. Enter the NFC Card UID: `04:B3:C1:DE:55:01`
4. Click **Resolve**
5. You see "Patient Found" with the resolved patient ID

> **ALPHA:** NFC card UID is entered manually. Production will use native
> NFC tap on a mobile device.

### 5.5 Request Consent

1. From the Patient Found screen, click **Request Access**
2. The Request Consent screen shows:
   - **Patient ID** (from the search)
   - **Provider ID** (from your session — never hardcoded)
   - **Purpose** (controlled selector): Treatment, Emergency Care, Diagnostic Review, Follow-up, Referral
   - **Purpose Note** (optional): e.g., "Diabetes follow-up consultation"
   - **Requested Scope** (controlled selector): Patient Summary, Vitals, Medications, Allergies, Lab Results, Clinical Record
   - **Access Duration** (preset selector): 5 min, 15 min, 30 min, 60 min
3. Select your options and click **Request Access**
4. You are redirected to the Waiting for Approval screen

### 5.6 Waiting for Patient Approval

The waiting screen shows:
- **Adaptive polling:** 2s → 5s → 10s as time passes
- **Elapsed timer**
- **Cancel Request** button (real server-side cancellation)

The patient must approve on their mobile app.

Approval must be completed in the authenticated physical-device app. Synthetic
workstation keys and direct unsigned approval calls are not enrollment or
consent evidence. The challenge may be inspected without resolving it:

```bash
# Get the challenge details
curl -H "Authorization: Bearer <PATIENT_TOKEN>" \
  http://127.0.0.1:8010/api/v2/consent/challenge/<REQUEST_ID>

# Approval is submitted only by the mobile app after biometric-gated signing.
```

Once approved, the waiting screen shows ✅ "Access Approved" and auto-redirects.

### 5.7 View Patient Record

After approval, you see the Patient Record Viewer with:
- **Consent countdown bar** showing remaining time
- **Allergies banner** (always visible when in scope — safety critical)
- **Tab navigation:** Summary, Vitals, Prescriptions, Lab Reports, Allergies, Documents, Timeline, Access Status
- **Provenance badges:** "Clinician verified" (green) or "AI extracted · 95% model confidence · Not yet verified" (yellow)
- **Lab abnormal flags** with red "ABNORMAL" badge

**Access Status tab** shows:
- Authorization: Active (green badge)
- Authorization Reference: non-secret consent request reference
- Scope, Purpose, Provider, Time Remaining

When consent expires, the viewer **locks immediately** with 🔒 and
"Consent expired. Request access again."

### 5.8 Emergency Break-Glass

1. From the Dashboard, click **🚨 Emergency Access**
2. You see the emergency form with:
   - **Patient ID** input
   - **Reason Code** selector (12 controlled options):
     - Immediate Threat to Life
     - Patient Incapacitated
     - Emergency Diagnostic Decision
     - Emergency Medication Safety
     - Unidentified Patient
     - Surgical Emergency
     - Severe Bleeding
     - Cardiac Arrest
     - Anaphylaxis
     - Respiratory Failure
     - System / Consent Service Down
     - Other Clinically Justified Emergency (requires 50+ char justification)
   - **Clinical Justification** (min 20 characters, 50 for "Other")
3. Fill in the form and click **Issue Break-Glass Access**
4. You see "Emergency Access Granted" with:
   - Masked authorization reference (never the raw token)
   - "This access will be recorded and may trigger patient and compliance notifications."
   - Warning about "Other" triggering mandatory review
5. Click **View Patient Record** to access the patient data

---

## 6. Security Invariants (Verified by Tests)

| Invariant | Status |
|-----------|--------|
| Zero `provider_id` placeholders | ✅ All from `useProviderAuth()` |
| Zero `localhost` in source | ✅ API URL from env var |
| Consent token in `X-Consent-Token` header | ✅ Passed on every data call |
| Consent tokens never displayed | ✅ Only masked references |
| Break-glass reason codes controlled | ✅ 12 codes, no free-text |
| Justification minimum length | ✅ 20 chars (50 for "Other") |
| Patient notification honest | ✅ "may trigger notifications" |
| Doctor never calls approval endpoint | ✅ Only patient can approve |
| Scope-restricted tabs | ✅ Unauthorized tabs hidden + data not fetched |
| AI provenance with verification | ✅ "Clinician verified" / "AI extracted · Not yet verified" |
| Consent revalidation every 10s | ✅ Backend validates every request |
| Session guards on all screens | ✅ Unauthenticated → login redirect |

---

## 7. Troubleshooting

| Problem | Solution |
|---------|----------|
| Login fails with 401 | Verify the configured database and account status. Normal seeding does not reset an existing password. Use the explicit rotation command below when required. |
| "Patient device not enrolled" error | Sign in on a real Android development client and complete native P-256 device enrollment; the seed intentionally does not create a device key |
| Consent challenge not found | Redis must be running; challenges are stored in Redis with 120s TTL |
| Frontend shows blank page | Check `NEXT_PUBLIC_API_URL` is set correctly |
| 401 on data requests | Session token may have expired; log in again |
| Build fails with OOM | Set `NODE_OPTIONS="--max-old-space-size=4096"` |

---

## 8. Demo Password Rotation and Seed Reruns

Re-run the normal launcher seed only when the synthetic fixture remains
complete and current. It preserves passwords, lockouts, device state, and
clinical-trust decisions; it fails closed instead of recreating a missing,
expired, revoked, or changed authority row:

```powershell
.\scripts\start_demo_dev.ps1 -Seed
```

To rotate the synthetic provider password, update only the ignored
`.env.demo.local`, then run the explicit confirmation path against that same
file. The command never prints the password, TOTP secret, or issued token:

```powershell
$env:NEXA_DEMO_ENV_FILE = (Resolve-Path .\.env.demo.local)
.\venv\Scripts\python.exe scripts\seed_demo_doctor.py `
  --reset-password `
  --confirm-demo-provider-reset
Remove-Item Env:NEXA_DEMO_ENV_FILE
```

Rotation writes only the canonical `password_hash`, clears password lockout and
failed attempts, updates `password_changed_at`, revokes existing provider and
pending-MFA sessions, and writes an audit event. It does not reactivate a
disabled identity, credential, affiliation, or verification. Recovering a
changed synthetic trust fixture is deliberately broader and requires the same
confirmed reset plus both reactivation flags; this re-establishes only the
canonical synthetic provider/facility/affiliation verification fixture:

```powershell
$env:NEXA_DEMO_ENV_FILE = (Resolve-Path .\.env.demo.local)
.\venv\Scripts\python.exe scripts\seed_demo_doctor.py `
  --reset-password `
  --confirm-demo-provider-reset `
  --reactivate-provider `
  --reactivate-credential
Remove-Item Env:NEXA_DEMO_ENV_FILE
```

Restarting Uvicorn is not required after a database-only seed operation.
