# Doctor App Demo Setup Guide

**Last updated:** 2026-07-10

This guide walks you through running the Nexa Care Doctor Web App against a
demo backend with seeded test data.

---

## Prerequisites

- Python 3.11+ with the project's virtual environment active
- PostgreSQL or Supabase database (local or cloud)
- Redis (for consent request state and session tokens)
- Node.js 18+ and Yarn 4+ (for the frontend)

---

## 1. Set Environment Variables

```bash
# Backend
export DATABASE_URL="postgresql://user:pass@localhost:5432/nexacare"
export REDIS_URL="redis://localhost:6379/0"
export DEMO_PROVIDER_PASSWORD="<GENERATE_A_STRONG_LOCAL_DEMO_PASSWORD>"

# Frontend — point at your running backend
export NEXT_PUBLIC_API_URL="http://localhost:8000"
```

> **No localhost in source code.** The frontend reads `NEXT_PUBLIC_API_URL` from
> the environment at build time. The default fallback is `https://api.nexacare.in`.

---

## 2. Seed the Demo Doctor

```powershell
Set-Location C:\Users\DELL\Nexa_Care
$password = .\venv\Scripts\python.exe -c "import secrets; print('Aa1!' + secrets.token_urlsafe(32))"
# Copy $password into the ignored .env as DEMO_PROVIDER_PASSWORD, then:
Remove-Variable password
.\venv\Scripts\python.exe scripts\seed_demo_doctor.py
```

The normal seed command creates a missing credential but never overwrites an
existing password. Never commit `.env` or place the generated password directly
in a command argument.

This creates:

| Resource | Value |
|----------|-------|
| **Doctor** | Dr. Meera Joshi |
| **Email** | `demo.doctor@nexacare.in` |
| **Password** | Value of the ignored local `DEMO_PROVIDER_PASSWORD` variable |
| **Hospital** | Nexa Demo Hospital (Mumbai) |
| **MFA** | Disabled (for demo simplicity) |

And two demo patients:

| Patient | ID | Notes |
|---------|----|----|
| Aarav Sharma | (auto-generated UUID) | Has NFC card `04:B3:C1:DE:55:01` |
| Priya Patel | (auto-generated UUID) | Manual search only |

---

## 3. Start the Backend

```bash
cd /path/to/Nexa_Care
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Verify the backend is running:

```bash
curl http://localhost:8000/health
```

---

## 4. Start the Frontend

```bash
cd /path/to/Nexa_Care/nexa-client

# Install dependencies (first time only)
node .yarn/releases/yarn-4.5.0.cjs install

# Build and start the Next.js app
cd nexa-client/apps/next
node ../../node_modules/next/dist/bin/next dev --port 3000
```

Open http://localhost:3000/doctor/login in your browser.

---

## 5. Step-by-Step Demo Flow

### 5.1 Login

1. Open http://localhost:3000/doctor/login
2. Enter **Email:** `demo.doctor@nexacare.in`
3. Enter **Password:** the value of your ignored local `DEMO_PROVIDER_PASSWORD`
4. Click **Sign In**
5. You are redirected to the Dashboard

> MFA is disabled on the demo account. In production, login would require
> a TOTP code from the authenticator app.

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

The patient must approve on their mobile app. For demo purposes, you can:

**Option A: Use the consent approval script**

```bash
python scripts/demo_push_approval.py --request-id <REQUEST_ID>
```

**Option B: Call the approval endpoint directly**

```bash
# Get the challenge details
curl -H "Authorization: Bearer <PATIENT_TOKEN>" \
  http://localhost:8000/api/v2/consent/challenge/<REQUEST_ID>

# Approve (requires device signing in production)
curl -X POST http://localhost:8000/api/v2/consent/approve-signed \
  -H "Authorization: Bearer <PATIENT_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"request_id":"<REQUEST_ID>","patient_id":"<PATIENT_ID>","decision":"approved","challenge_nonce":"<NONCE>","signature":"<SIG>","device_id":"<DEVICE_ID>"}'
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
- Authorization Reference: masked token (e.g., `nexa:co••••3f2a`)
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
| "Patient device not enrolled" error | The patient needs a device key; the seed script creates one |
| Consent challenge not found | Redis must be running; challenges are stored in Redis with 120s TTL |
| Frontend shows blank page | Check `NEXT_PUBLIC_API_URL` is set correctly |
| 401 on data requests | Session token may have expired; log in again |
| Build fails with OOM | Set `NODE_OPTIONS="--max-old-space-size=4096"` |

---

## 8. Demo Password Rotation and Seed Reruns

Re-running the normal seed command is non-destructive and leaves the password,
lockout state, and active-state decisions unchanged:

```powershell
.\venv\Scripts\python.exe scripts\seed_demo_doctor.py
```

To intentionally rotate only `demo.doctor@nexacare.in`, first generate a new
strong value and place it in the ignored `.env`, then run both confirmation
flags:

```powershell
.\venv\Scripts\python.exe scripts\seed_demo_doctor.py `
  --reset-password `
  --confirm-demo-provider-reset
```

Rotation writes only the canonical `password_hash`, clears password lockout and
failed attempts, updates `password_changed_at`, revokes existing provider and
pending-MFA sessions, and writes an audit event. It does not reactivate a
disabled identity or credential unless the corresponding explicit flag is also
provided. Restarting Uvicorn is not required after a database-only rotation.

Verify login without displaying the returned token:

```powershell
$body = @{
  login_identifier = "demo.doctor@nexacare.in"
  password = (Get-Content .env | Where-Object { $_ -match '^DEMO_PROVIDER_PASSWORD=' } | Select-Object -First 1).Split('=', 2)[1].Trim('"')
} | ConvertTo-Json
$response = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v2/auth/login" -Method POST -ContentType "application/json" -Body $body
$json = $response.Content | ConvertFrom-Json
[pscustomobject]@{
  http_status = $response.StatusCode
  token_present = [bool]$json.access_token
  provider_uid_present = [bool]$json.provider_uid
  hospital_id_present = [bool]$json.hospital_id
  mfa_required = [bool]$json.mfa_token
}
Remove-Variable body, response, json
```
