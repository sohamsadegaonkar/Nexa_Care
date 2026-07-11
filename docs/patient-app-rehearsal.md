# Patient App Demo Rehearsal Guide

**Day 14 · 2026-07-10**  
**Sprint:** Alpha Demo — Notification → Tap → Approve with Face ID  
**Audience:** Investors, hospital CIO, incubator panel  
**Duration:** ~4 minutes patient-side (part of a 12-minute full demo)

---

## Pre-Demo Checklist (Do These 30 Minutes Before)

Complete every item. A single miss here will derail the live demo.

- [ ] **Physical device ready** — iPhone with Face ID (iOS 16+) or Android with fingerprint. Simulator will NOT work for biometric.
- [ ] **Phone fully charged** — plug in during rehearsal; unplug for showtime.
- [ ] **Phone unlocked** — Face ID enrolled and working. Test: unlock phone with face once.
- [ ] **App installed** — latest Expo development build (`eas build --profile development`). Not Expo Go — push notifications require a real build.
- [ ] **Environment variable set** — `EXPO_PUBLIC_API_URL=https://demo-api.nexacare.ai` baked into the build.
- [ ] **Demo patient logged in** — phone + OTP verified. JWT active.
- [ ] **Device enrolled** — SecureDeviceScreen completed. DeviceEnrolledScreen confirmed "Trusted & Active". If not enrolled, do it now:
  1. Open app → login → tap "Secure This Device" → wait for "Device Secured!" confirmation.
- [ ] **Push token registered** — app called `POST /api/v2/push/register-token` on login. Verify in Redis: `GET push_token:{patient_id}`.
- [ ] **App in background** — press Home button. Do NOT kill the app. The app must be in background (or foreground) to receive push.
- [ ] **Do Not Disturb OFF** — ensure notifications are not silenced.
- [ ] **Demo request IDs prepared** — two consent requests pre-created by the provider-side presenter:
  - `REQUEST_APPROVE` — for the approval demo
  - `REQUEST_DENY` — for the denial demo
  - Both must be in `pending` status with ≥90 seconds TTL remaining.
- [ ] **Deep-link fallback URLs ready** — copy these to clipboard or Notes:
  - `nexacare://patient/consent-request?requestId=<REQUEST_APPROVE>`
  - `nexacare://patient/consent-request?requestId=<REQUEST_DENY>`

---

## Demo Part 1: Push Notification → Approval with Face ID (~2 min)

### Step 1 — Set the Scene (0:00–0:15)

**Presenter says:**

> "I'm now a patient at City Hospital. My phone has the Nexa Care app
> installed, my device is enrolled with a cryptographic key, and the app
> is sitting in the background. I have NOT shared any password or
> private key with anyone — the key never leaves my phone's secure
> hardware."

**Presenter does:** Hold up phone showing lock screen / home screen with app icon visible. Do NOT open the app.

### Step 2 — Doctor Triggers the Request (0:15–0:30)

**Presenter says:**

> "My doctor, Dr. Mehta, is now requesting access to my lab results.
> She clicks 'Request Access' on the provider dashboard, and within
> seconds I receive a push notification."

**Presenter does:** Nod to the provider-side presenter to trigger `POST /api/v2/consent/request` with `REQUEST_APPROVE`.

### Step 3 — Notification Arrives (0:30–0:45)

**Presenter says:**

> "And here it is — a push notification from Nexa Care. Dr. Mehta is
> requesting access to my lab results for a routine checkup."

**Presenter does:** Tap the notification. The app opens to `ConsentRequestScreen`.

**⚠️ Fallback if push does not arrive:**

1. Open Notes app where you saved the deep link.
2. Tap `nexacare://patient/consent-request?requestId=<REQUEST_APPROVE>`.
3. The app opens to the same screen.
4. **Say:** "In case the push notification doesn't come through — which
   can happen on demo WiFi — I can open the request directly via a
   secure deep link."

### Step 4 — Review the Consent Request (0:45–1:10)

**Presenter says:**

> "I can see exactly who is asking: Dr. Mehta from City Hospital. The
> purpose is 'Routine Checkup'. The data requested is my fasting
> glucose and HbA1c. And I can see this request expires in 2 minutes.
> Everything is transparent — I know what I'm consenting to."

**Presenter does:** Point at each element on screen:
- **Provider name** and **hospital name** at top
- **Purpose** label
- **Data categories** listed
- **Countdown timer** showing remaining time

### Step 5 — Tap Approve → Face ID (1:10–1:35)

**Presenter says:**

> "I'm comfortable with this request. I tap the green Approve button.
> Nexa Care now asks for my Face ID — this proves it's actually me
> holding the phone, not someone who picked it up."

**Presenter does:**
1. Tap the green **Approve** button.
2. The Face ID prompt appears.
3. Look at the phone naturally — Face ID authenticates.
4. The spinner shows: "Signing consent..." then "Submitting approval..."

### Step 6 — Approval Result (1:35–1:55)

**Presenter says:**

> "Access approved. I can see the consent receipt — Dr. Mehta can now
> view my lab results for the next 15 minutes. If I change my mind, I
> can revoke access immediately with the Revoke button. I am always in
> control."

**Presenter does:** Point at:
- ✅ green "Access Granted" banner
- Provider name and scope
- Countdown timer showing "0h 14m 58s"
- **Revoke Access Now** button

**⚠️ Fallback if Face ID fails:**

1. Face ID prompt fails → screen shows "Biometric verification cancelled."
2. Tap **Authenticate** to retry Face ID.
3. If Face ID fails again, tap **Cancel** → go back to consent request screen.
4. **Say:** "Face ID didn't recognize me — which is the correct security
   behavior. Let me try again." Then retry.

### Step 7 — Verify in Access History (1:55–2:00)

**Presenter says:**

> "Every access is audited. I can see it in my Access History —
> Dr. Mehta accessed my records at 2:34 PM."

**Presenter does:** Tap "View Access History" button. Show the new entry with provider name, hospital, and timestamp.

---

## Demo Part 2: Deliberate Denial (~2 min)

### Step 8 — New Request Arrives (2:00–2:15)

**Presenter says:**

> "Now let's see what happens when I don't want to share my data.
> A different provider is requesting access — but this time I'll deny it."

**Presenter does:** Nod to provider-side presenter to trigger `POST /api/v2/consent/request` with `REQUEST_DENY`. Tap the notification (or use deep link).

### Step 9 — Review and Deny (2:15–2:35)

**Presenter says:**

> "I see the request. But I don't recognize this provider, or I'm not
> comfortable sharing this data right now. I tap the red Deny button.
> Notice: denial does NOT require Face ID — I shouldn't need to prove
> my identity just to say no. But my device still signs the denial so
> the hospital knows it came from me and not a glitch."

**Presenter does:**
1. Point at the request details.
2. Tap the red **Deny** button.
3. The app signs the denial (no Face ID prompt) and submits.

**⚠️ Fallback if push doesn't arrive:**

Use the deep link: `nexacare://patient/consent-request?requestId=<REQUEST_DENY>`

### Step 10 — Denial Result (2:35–2:50)

**Presenter says:**

> "Access denied. The doctor has been notified that I declined. No data
> was shared — my records remain private."

**Presenter does:** Point at:
- ❌ red "Access Denied" banner
- "The doctor has been notified" text
- No revoke button (no access was granted to revoke)

### Step 11 — Verify Denial in History (2:50–3:00)

**Presenter says:**

> "And my Access History records the denial — permanently auditable.
> Even my 'no' is on the record, so there's never any confusion about
> whether consent was given."

**Presenter does:** Show Access History with the denial entry visible.

---

## Demo Part 3: Break-Glass Transparency (~1 min, optional)

### Step 12 — Break-Glass Scenario (3:00–3:30)

**Presenter says:**

> "What about emergencies? If a doctor uses break-glass access —
> which bypasses my consent in a life-threatening situation — it's
> flagged with a red BREAK-GLASS badge in my Access History. I always
> know who accessed my data, even in an emergency. Transparency is
> never optional."

**Presenter does:** Scroll Access History to show the break-glass entry with the red ⚠️ BREAK-GLASS warning badge.

---

## Full Sequence Diagram

```
Provider Dashboard                Patient Phone                 Backend
     │                                │                           │
     │  POST /consent/request         │                           │
     │──────────────────────────────────────────────────────────→│
     │                                │   Push notification      │
     │                                │←──────────────────────────│
     │                                │                           │
     │                         [Tap notification]                │
     │                                │                           │
     │                    GET /consent/challenge/{id}             │
     │                                │──────────────────────────→│
     │                                │   Challenge details       │
     │                                │←──────────────────────────│
     │                                │                           │
     │                      ┌─────────┴──────────┐               │
     │                      │ ConsentRequestScreen│               │
     │                      │  Review: who, why,  │               │
     │                      │  what, countdown    │               │
     │                      └────┬─────────┬──────┘               │
     │                           │         │                      │
     │                    [Approve]    [Deny]                     │
     │                           │         │                      │
     │                    Face ID prompt   No biometric           │
     │                           │         │                      │
     │                    Sign with p256   Sign with p256          │
     │                           │         │                      │
     │              POST /consent/approve-signed                  │
     │                           │──────────────────────────→│
     │                           │   Approved / Denied       │
     │                           │←──────────────────────────│
     │                                │                           │
     │                    ApprovalResultScreen                    │
     │                    (receipt + revoke option)               │
```

---

## Troubleshooting Quick Reference

| Problem | Cause | Fix |
|---|---|---|
| No push notification arrives | WiFi blocking Expo push; token not registered; app killed | Use deep-link fallback: `nexacare://patient/consent-request?requestId=...` |
| "Challenge expired or not found" | Request TTL (120 s) expired before you opened it | Provider creates a new request. Happen faster next time. |
| Face ID prompt doesn't appear | Face ID not enrolled on device; app not configured for Face ID | Enroll Face ID in iOS Settings → Face ID & Passcode. Check `app.json` has `NSFaceIDUsageDescription`. |
| Face ID fails repeatedly | Lighting, angle, face not recognized | Hold phone at natural angle. Tap "Try Again". If still fails, cancel and narrate it as a security feature. |
| "No device signing key found" | Device not enrolled | Go back to SecureDeviceScreen, enroll device, then retry. |
| "Device not enrolled" from backend | Backend doesn't have the public key | Re-enroll: SecureDeviceScreen → DeviceEnrolledScreen. |
| App shows blank/white screen | JS bundle error; stale build | Rebuild: `eas build --profile development --platform ios`. |
| "Request already resolved" | You approved/denied this request already in rehearsal | Provider must create a fresh request. |
| Countdown shows "Expired" immediately | Clock skew between device and server | Check device time is set to automatic. |

---

## Presenter Cheat Sheet (One Page, Print This)

```
BEFORE:  ☐ Phone charged    ☐ App installed    ☐ Logged in
         ☐ Device enrolled   ☐ App in background  ☐ DND off
         ☐ REQUEST_APPROVE and REQUEST_DENY IDs ready
         ☐ Deep-link fallbacks in Notes/clipboard

PART 1 — APPROVAL:
  1. Hold up phone (app in background)
  2. Nod to provider presenter → triggers request
  3. Tap notification → ConsentRequestScreen
     Fallback: open deep link from Notes
  4. Narrate: who, purpose, scope, countdown
  5. Tap green APPROVE → Face ID → look at phone
     Fallback if Face ID fails: retry, or cancel and narrate
  6. Show ApprovalResultScreen: receipt, countdown, revoke

PART 2 — DENIAL:
  7. Nod to provider presenter → triggers deny request
  8. Tap notification → ConsentRequestScreen
     Fallback: open deep link from Notes
  9. Narrate: "I don't want to share this time"
  10. Tap red DENY → no Face ID needed → signed denial
  11. Show "Access Denied — doctor notified"

PART 3 — TRANSPARENCY (optional):
  12. Show Access History: break-glass badge, denial entry

CLOSING: "I control my data. Every access is audited,
         every denial is recorded, every emergency is flagged."
```

---

## Key Phrases to Emphasize

1. **"The private key never leaves my phone"** — during device enrollment context.
2. **"Face ID proves it's me"** — before the biometric prompt.
3. **"I can revoke access at any time"** — on the result screen.
4. **"Denial doesn't need Face ID — I shouldn't have to prove who I am just to say no"** — during the denial demo.
5. **"Every access is audited, even emergencies"** — during break-glass discussion.
6. **"Alpha: P-256 keypair generated client-side and private key stored in platform secure storage. Not yet: hardware-backed non-exportable signing key with biometric-gated key usage."** — only if asked about security level.

---

## Alpha Honesty Note

If an audience member asks "Is this hospital-grade security?", respond:

> "This is an alpha demo. We use P-256 keypairs generated client-side
> with the private key stored in the platform secure storage — iOS
> Keychain or Android Keystore. For an academic and incubator demo,
> this is strong. For a hospital pilot, we would need hardware-backed
> non-exportable signing keys with biometric-gated key usage, which
> requires a native module we haven't built yet. The architecture is
> designed for that upgrade path."
