# Nexa Care staging fidelity incident and non-regression rule
**Date:** 24 Nov 2026
**Status:** Remediation in progress; full hosted end-to-end staging remains NO-GO
**Evidence:** [draft PR #106](https://github.com/sohamsadegaonkar/Nexa_Care/pull/106), [full app tracking #107](https://github.com/sohamsadegaonkar/Nexa_Care/issues/107), [original device/browser qualification PR #101](https://github.com/sohamsadegaonkar/Nexa_Care/pull/101)
**Authority:** [AGENTS.md](../../AGENTS.md), [security non-regression](SECURITY_NON_REGRESSION.md), [engineering constitution](NEXA_CARE_ENGINEERING_CONSTITUTION.md).

## Requested contract — an exact application clone, not a demonstration

For the owner, a **staging website** is the actual Nexa Care product checked out at the intended repository revision with the same patient/provider screens, routes, auth/session logic, consent and document processing behavior, deployed against *isolated* infrastructure and test-only identities. The only acceptable differences are service origins, credentials, environment-specific release banners/nonfunctional metadata, isolation and synthetic data. A frontend-only page, local-only walkthrough, sample login code, or mocked consent does **not** satisfy a working staging request.

## Root cause and impact

1. The original Next frontend was published without the required build-time `NEXT_PUBLIC_API_URL`, and its same-origin `/api/**` rewrite pointed to a reject-all temporary maintenance gateway. Consequently real patient phone OTP did not work and the page displayed a missing API origin error.
2. Instead of provisioning the real FastAPI backend and required data/security services, follow-on patches replaced the real patient login with an inert form, then hid it, then supplied `/demo/patient` with fictional patient access and consent and `DEMO-*` entry code.
3. Those sample interactions could work in a browser but were **not the original Nexa Care application** and did not exercise the original phone OTP, patient records, uploads, clinical API, identity, real signed consent or audit. Reporting these as fixing the staging workflow was misleading.
4. The owner explicitly rejected this substitution. It must not recur.

## Correction and controls

- **Source restore:** `nexa-client/apps/next/app/patient/login/page.tsx` and `nexa-client/apps/next/app/page.tsx` are restored **exactly from `main`**, preserving the genuine phone/OTP patient route and provider login. The added `nexa-client/apps/next/app/demo/patient/page.tsx` is deleted. All other genuine product pages remain from the repository.
- **CI fidelity:** The staging qualification workflow fetches `main` and requires a zero-diff comparison of these two original entry screens; it asserts the fabricated `/demo/patient` page does not exist. Staging source tests verify original OTP calls and provider entry without fictionalized replacements.
- **Infrastructure:** The hosted frontend's `NEXT_PUBLIC_API_URL` uses its own staging HTTPS origin, but `API_PROXY_TARGET` still points to an intentionally rejecting maintenance gateway. **No real FastAPI staging API is deployed yet.** Until that changes, users cannot perform authenticated patient/clinician end-to-end flows. Do not redirect to production or declare success based on frontend liveness.
- **Remaining real-service gates:** independently provisioned TLS PostgreSQL/Redis, tenant/identity isolation and database migrations; Supabase `alpha_testing` project Auth with Twilio and signup disabled (but older Auth/clinical records exist, not verified synthetic), least-privilege secret injection, scanner, remote document extraction, S3/KMS encryption, request/session/audit safeguards and browser/physical-device signed-consent/bytes tests. Production-like startup checks in `app/core/production_runtime.py` are mandatory: do not bypass them by setting `ENVIRONMENT=test` on a public service.
- **Release discipline:** PR #106 remains **draft/unmerged**, staging readiness expected to fail until real E2E passes. PRs #99/#101 involve separate original report/rehearsal changes still subject to approval. No production or clinical data copied/modified.

## Definition of done

1. Exact application sources and relevant feature branches/approved revisions are built (including required real patient and clinician flows), with justified, source-diff-reviewed staging configuration only.
2. Public isolated HTTPS Next ↔ FastAPI ↔ Auth/Redis/Postgres/Document/Scanner/KMS paths are live; no production services, credentials or data are reachable.
3. A permitted test phone receives real short-lived OTP; wrong/expired/replayed OTP denied, signup restrictions and backend allowlist enforced; synthetic clinician credentials complete real session/MFA as applicable.
4. A synthetic document is imported and reviewed, original bytes encrypted, signed consent independently issued and revoked, cross-hospital report denied without consent, one-time opening audited, concurrent/replay access denied; real browser and physical-device evidence recorded.
5. Exact revision CI, security dependencies, readiness, privacy/security/clinical human reviewers and explicit production-promotion gates all pass.

**Do not claim any of these unverified gates passed.**
