# Pipeline UI Demo Setup & Rehearsal Guide

**Last updated:** 2026-07-11

> **ALPHA**: This demo guide covers an alpha implementation. AI extraction
> results are simulated/placeholder data until the live extraction engine is
> wired. All screens are consent-gated and require authenticated sessions.

---

## Prerequisites

- Running Nexa Care backend (API server on `NEXT_PUBLIC_API_URL`)
- Running Next.js frontend (`cd nexa-client/apps/next && yarn dev`)
- Demo doctor seeded (`python scripts/seed_demo_doctor.py`)
- Demo patient with at least one consent grant
- A sample clinical document (PDF) for upload — see **Demo Document** below

---

## Demo Document

For the demo, prepare a **single-page PDF** containing a patient's medication
list and allergy record. The AI extraction should produce the following fields:

| Field Name           | Raw Value (AI Extracted)        | Confidence | Risk Level  | Expected Demo Action |
|----------------------|---------------------------------|------------|-------------|----------------------|
| `patient_name`       | Rajesh Kumar                    | 0.97       | LOW_RISK    | Auto-approved        |
| `date_of_birth`      | 1985-03-15                      | 0.95       | LOW_RISK    | Auto-approved        |
| `medication_name`    | Metformin 500mg                 | 0.92       | MEDIUM_RISK | Needs review         |
| `medication_frequency` | (missing / blank)             | 0.41       | HIGH_RISK   | **Edit** — add "Twice daily" |
| `allergy`            | Penicillin                      | 0.88       | MEDIUM_RISK | **Approve** after review |
| `diagnosis`          | Type 2 Diabetes Mellitus        | 0.94       | LOW_RISK    | Auto-approved        |

The **medication_frequency** field has a missing value (confidence 41%) — this
is the key demo moment: the AI failed to extract the frequency, so the doctor
must **edit** the field and enter the corrected value.

The **allergy** field is flagged for review at 88% confidence — the doctor
must **approve** it after verifying against the source document.

---

## Step-by-Step Demo Flow

### Step 1: Login

1. Navigate to `/doctor/login`
2. Enter demo doctor credentials (see `scripts/seed_demo_doctor.py`)
3. If MFA is enabled, enter the TOTP code
4. After login, you land on the **Doctor Dashboard**

> **What to say:** "I'm logging in as Dr. Sharma. The session uses JWT
> authentication with MFA support."

### Step 2: Request Consent

1. From the dashboard, navigate to **Patient Search**
2. Search for the demo patient (e.g., by NFC tap or ID)
3. On the patient profile, click **Request Consent**
4. Select scope: `medications, allergies, diagnoses`
5. Submit the consent request
6. The **Waiting For Approval** screen shows polling status

> **What to say:** "I'm requesting consent for medications, allergies, and
> diagnoses. The patient must approve on their phone — we never auto-grant."

### Step 3: Consent Approved

1. On the patient device (or simulated), approve the consent request
2. The waiting screen detects the approval automatically (2s polling)
3. You are redirected to the **Patient Record Viewer**

> **What to say:** "Consent granted. The token is passed as a header on every
> subsequent request — never in URLs."

### Step 4: Upload Document

1. From the dashboard, navigate to **Pipeline → Upload**
   (`/doctor/pipeline/upload?patient_id=...&consent_token=...`)
2. Drag-and-drop the demo PDF (or click **Browse Files**)
3. Confirm patient ID is pre-filled from consent context
4. Click **Upload & Extract**

> **What to say:** "I'm uploading a medication list. The browser sets the
> multipart boundary automatically — we never set Content-Type manually."

### Step 5: Watch Extraction

1. After upload, you are redirected to **Job Status**
   (`/doctor/pipeline/jobs/{jobId}`)
2. The progress bar shows: Queued → Extracting → Scored → Review Pending
3. The spinner and status label update every 2 seconds
4. When extraction completes, the field summary appears:
   - **4 Auto-Approved** (patient_name, date_of_birth, diagnosis + 1 more)
   - **2 Need Review** (medication_frequency, allergy)

> **What to say:** "The AI pipeline extracted 6 fields. Four were
> auto-approved at high confidence, but two need my review — the medication
> frequency is missing and the allergy needs verification."

5. Click **Go to Review Queue →**

### Step 6: Review Queue

1. The **Review Queue** shows all items flagged for human review
2. Each item displays: document title, flagged field count, highest risk level
3. Click the demo document entry to open the **Review Cockpit**

> **What to say:** "The review queue shows all documents with flagged fields.
> I'll open this one — it has 2 fields needing review, with HIGH as the
> highest risk level."

### Step 7: Review Cockpit — Edit the Medication

1. The **Review Cockpit** opens with split layout:
   - **Left:** Document preview with bounding-box overlays
   - **Right:** Field cards (needs_review first, then auto-approved)
2. The **progress bar** shows "0/2 reviewed"
3. The first needs_review field is **medication_frequency**:
   - Status badge: orange "NEEDS REVIEW"
   - Provenance badge: "AI extracted · 41% model confidence · Not yet verified"
   - Risk badge: "⛔ HIGH RISK"
   - Value is blank/missing
4. Click **Edit** on the medication_frequency field
5. The edit panel appears with:
   - Original AI extraction shown with strikethrough
   - Input field pre-filled with the raw value
   - Type the corrected value: **"Twice daily"**
6. Click **Save Edit**
7. A brief **✎ Edited** confirmation animation appears
8. The field transitions to read-only with status "edited"
9. The progress bar updates to "1/2 reviewed"

> **What to say:** "The AI missed the medication frequency — only 41%
> confidence. I can see the original extraction is blank. I'll enter 'Twice
> daily' as the corrected value. The edit is tracked in the audit trail."

### Step 8: Review Cockpit — Approve the Allergy

1. The second needs_review field is **allergy**:
   - Status badge: orange "NEEDS REVIEW"
   - Provenance badge: "AI extracted · 88% model confidence · Not yet verified"
   - Risk badge: "⚠ MEDIUM_RISK"
   - Value: "Penicillin"
2. Verify against the document preview on the left — the bounding box
   highlights the source region
3. Click **Approve** on the allergy field
4. A brief **✓ Approved** confirmation animation appears
5. The field transitions to read-only with status "approved"
6. The progress bar updates to "2/2 reviewed" and turns green
7. A **✓ All Reviewed** badge appears

> **What to say:** "The AI extracted 'Penicillin' as the allergy at 88%
% confidence. I can see the source region highlighted on the document — it
matches. I'll approve it."

### Step 9: Commit to Record

1. With all fields reviewed, the **Commit →** button in the footer activates
   (green theme)
2. Click **Commit →** to navigate to the **Commit Screen**
   (`/doctor/pipeline/commit/{jobId}`)
3. The commit screen shows:
   - Field summary grouped by status:
     - **Auto-Approved (3):** patient_name, date_of_birth, diagnosis
     - **Clinician Verified (1):** allergy — blue "Verified ✓" badge
     - **Edited (1):** medication_frequency — yellow "Edited ✎" badge
   - CommitSafetyBadge per field showing provenance
   - HIGH/CRITICAL risk warning banner (medication_frequency was HIGH_RISK)
   - Optional **Encounter Summary** text field
4. Click **Commit 5 Fields to Record**
5. The commit is processed; success state shows:
   - `timeline_event_id`
   - `ledger_tx_hash` (audit trail)
   - `committed_at` timestamp
   - Field count committed

> **What to say:** "The commit screen shows exactly what will be written to
> the patient record — auto-approved fields, the clinician-verified allergy,
> and the edited medication frequency. The HIGH risk warning reminds me to
> double-check. Once I commit, this is immutable in the audit ledger."

### Step 10: Show in Timeline

1. Navigate to the **Patient Record Viewer** for the same patient
2. The timeline now shows a new event from the pipeline commit
3. The event includes:
   - Source: "AI extracted" with confidence scores
   - Provenance badges for each field
   - The edited medication_frequency shows "Twice daily" with the edit marker

> **What to say:** "The committed data now appears in the patient's health
> timeline. Each field carries its provenance — you can see which fields were
> AI-extracted, which were clinician-verified, and which were edited."

---

## Demo Failure Recovery

| Scenario | Recovery |
|----------|----------|
| Upload fails (network error) | Click **Retry** on the error banner |
| Consent denied | Return to patient profile, re-request |
| Job stuck in "processing" | Click **Retry** on the loading state |
| Commit blocked (unresolved fields) | Return to Review Cockpit via the **Go to Review Cockpit** button |
| 409 Conflict on commit | Error message shows "unresolved fields remain"; return to cockpit |
| Session expired (401) | Automatic redirect to login; re-authenticate |

---

## Screen State Summary

Every pipeline screen handles three states:

| Screen | Loading | Error | Empty |
|--------|---------|-------|-------|
| **PipelineUpload** | Spinner during upload | Red error card with Retry | N/A (input form) |
| **JobStatus** | Spinner + "Loading job status…" | Red text + Retry button | N/A (job always has data) |
| **ReviewQueue** | Spinner + "Loading review queue…" | Red card with Retry | "No items pending review" + description |
| **ReviewCockpit** | Spinner + "Loading job details…" + Retry | Red text + Retry button | "No extracted fields found" + Refresh |
| **Commit** | Spinner + "Loading job details…" | Red text + Retry | N/A (navigated from cockpit) |

---

## Architecture Notes for Demo Q&A

### Q: How is consent enforced?
**A:** Every API call passes the consent token as `X-Consent-Token` header. The
backend validates the token on every request. If consent expires mid-review, the
next API call returns 403 and the frontend locks the viewer.

### Q: What happens if the doctor tries to commit with unresolved fields?
**A:** The Commit button is disabled with exact count ("2 fields still need
review"). Even if bypassed client-side, the backend returns HTTP 409.

### Q: How are edits tracked?
**A:** Every edit is recorded with `adjudicated_by`, `adjudicated_at`, and the
original vs corrected value. The CommitSafetyBadge shows "Edited ✎" in yellow.

### Q: Why is there no Approve/Deny button on the doctor screen for consent?
**A:** Only the patient can approve or deny consent requests. Doctors can only
request and wait. This is a deliberate architectural constraint.

### Q: What about emergency access?
**A:** Break-glass access is available via the Emergency Access screen. It
requires selecting a reason code from a controlled list (not free-text), and
grants a 15-minute consent window. Rate-limited to 3/hour/provider.

### Q: Is the document preview real?
**A:** ALPHA: Currently a placeholder SVG with bounding-box overlays. When S3
presigned URLs or a document rendering service is available, the placeholder
will be replaced with actual page images.

---

## Seed Script

To seed the demo data:

```bash
cd /path/to/Nexa_Care
python scripts/seed_demo_doctor.py
```

This creates:
- Demo doctor account with known credentials
- Demo patient with NFC card linkage
- Pre-approved consent grant for the demo flow
