# Patient workflow non-regression case — 24 November 2026

Related: [Agent contract](../../AGENTS.md), [security non-regression](SECURITY_NON_REGRESSION.md), [engineering constitution](NEXA_CARE_ENGINEERING_CONSTITUTION.md), [staging PR #106](https://github.com/sohamsadegaonkar/Nexa_Care/pull/106).

## Incident and impact

A user encountered `API base URL is not configured. Set NEXT_PUBLIC_API_URL.` while trying to send a patient OTP from the hosted staging Next.js frontend. The deployment had omitted a required public **same-origin** setting. The first attempted "fix" disabled patient OTP while the backend was unqualified; a later patch removed the phone field, verification action and any usable patient entry altogether. The error disappeared, but the user could no longer enter the patient experience. A safety fix inadvertently became a product navigation regression.

**Root causes:** (1) no qualified hosted FastAPI staging API or secrets yet; (2) initial frontend deployment missing the build-time public `NEXT_PUBLIC_API_URL`; (3) failure to distinguish **blocking an unsafe network operation** from **removing a user journey**; (4) absent staging browser assertions that the patient route remains useful when authentication is unavailable.

## Mandatory engineering rule from this incident

> Do not fix an availability, security or configuration defect by silently deleting or obscuring a separate essential workflow. Preserve the entry point, display honest unavailable states, and add a tested alternative only where it cannot bypass authorization.

For future changes to patient, clinician, document or consent flows:

1. Identify the user's desired journey and its entry point before editing any controls.
2. Define separately the invariants for **navigation**, **functional UI**, **backend readiness**, **authentication/authorization** and **clinical access**.
3. Keep original account creation/login controls in code and keep them present with accurate disabled/unavailable state when the environment is offline; do not turn a failed request into success or fabricate authenticated sessions.
4. When real backend use is impossible, offer a clearly labeled synthetic journey using only literal fictional UI state. Isolate it from genuine `/patient/**` clinical pages, sensitive auth, browser storage, real API calls, SMS, device signing, document bytes and durable consent.
5. Protect BOTH paths with tests: the synthetic walkthrough stays navigable, while the real login and all clinical APIs remain fail-closed. A staging health check does **not** assert application readiness.
6. Verify the **exact deployment SHA** in a real browser, on the patient entry page and across the synthetic walkthrough. Report the untested genuine OTP and clinical workflows explicitly.
7. Do not promote synthetic UI evidence as a backend, consent, audit, SMS delivery, security or clinical compliance proof. Physical-device, synthetic signed-consent E2E, privacy, clinical and security signoff remain separate gates.

## Correction on staging PR #106

- `NEXT_PUBLIC_API_URL` points to the HTTPS **web origin**, as required for first-party browser API requests. The real `API_PROXY_TARGET` remains the reject-all maintenance gateway until a qualified isolated FastAPI backend is deployed.
- `NEXT_PUBLIC_STAGING_READ_ONLY=1` keeps real patient OTP send/verify handlers blocked before any network request while the backend is unavailable.
- `/patient/login` keeps the original phone/OTP form in source and renders its inactive state rather than erasing patient controls. A prominent "Explore synthetic patient workflow" button links to the separated `/demo/patient` route.
- `/demo/patient` is accessible only in the **isolated staging preview build** and uses fictional, ephemeral in-memory UI state for dashboard, records, timeline, and simulated approval/denial/revocation. No real account, API, SMS, patient/clinical record, document bytes, session cookie, cryptographic signature, audit or authorization capability is created. It must never be called a live clinical workflow.
- `scripts/staging/patient_workflow_non_regression.test.mjs` checks presence of original sign-in, accessible synthetic entry, scope guard and forbidden API/session behavior. The staging qualification workflow executes this check before its synthetic and deployed-smoke gates.
- Live browser acceptance must still be independently recorded for this exact commit; a successful static test is insufficient.

## Release and review status

This is a **product-availability non-regression correction**, not permission to relax identity, consent, data access, or clinical safeguards. The staging UI is not a functional patient login. The real web frontend is still blocked from FastAPI/Redis/PostgreSQL/S3/KMS/ClamAV and real OTP delivery. Keep PR #106 **draft/unmerged** and clinical/production release **NO-GO** until actual authenticated integration and human reviews pass.

**Required validation:** Next production build; staging synthetic-flow non-regression; browser navigation from patient login into every demo step, simulated consent state changes and return to login; 503 readiness; no real SMS or clinical API calls. If any of these fail, repair without weakening the test or removing another workflow.

## Follow-up: accessibility of disabled controls

The first browser acceptance inspection found that the login form's custom `FormField` and `ActionButton` looked disabled under `disabled={STAGING_READ_ONLY}` in source, but the **rendered HTML input/button still lacked the native `disabled` attribute and remained keyboard-focusable**. Do not interpret a component prop or a visual style as evidence of actual interaction blocking.

The correction retains the original login form layout but wraps only the OTP controls in an HTML `<fieldset disabled={STAGING_READ_ONLY}>` with an ancestor `inert={STAGING_READ_ONLY ? true : undefined}`. The synthetic walkthrough CTA remains **outside** that ancestor. The OTP handlers continue to return before network activity when staging preview is active. The CI contract checks the disabled/inert ancestry and clickable alternate navigation; an independent browser test must check actual focus and interactivity, not appearance alone.
