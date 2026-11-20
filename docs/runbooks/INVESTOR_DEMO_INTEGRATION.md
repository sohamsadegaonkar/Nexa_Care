# Investor demo integration — 20 November 2026

Status: integration candidate; not deployed, not merged, not rehearsed.
Only synthetic data. Do not use a real patient or shared CampusPool resources.

## Provenance and gates

This branch integrates the P0 and P1A clinician viewer/inventory work via
P1B PR #99 (617f019ce226041586d2f2a48c50dd070cf474a9) and Azure PR #100
(423282df34a66ea1b2a1d77f2492e8106efc2d30). Main remained at
d2e961c4b5380b9fa53eeb446fdb7d5a2b7eba8b. The older PR #92 is 59
commits behind main and has overlapping startup, account recovery,
seeding and governance changes. Do not blindly merge it or claim its
launcher is qualified on this combined branch.

Existing CI of PR #99 had three lifecycle test errors and two preview
test errors. Integrate the new erasure transaction and ensure deletion of
document-access decisions is committed even if no source rows remain;
retain the access-block boundary and normalize database failure. Repair
the test harness to supply missing jsdom URL methods; do not bypass
production revocation or preview cleanup. Fresh CI must establish status.

## Rehearsal prerequisites

Use a private/disposable synthetic development environment. Use the exact
repository migration head and existing separate migration runner; no API
startup migration. Configure PostgreSQL, Redis, source malware scanner
and enrolled patient signing device. Prefer local first to avoid costs.
For Azure optional runtime setup consult AZURE_MIGRATION_FOUNDATION.md;
build with INSTALL_AZURE_SDK=true. Account/identity/key, scanner, network,
private ingress, database and Redis TLS, approved secrets, storage
retention, and actual service cost are separately required. The existing
plan is a non-deployable review candidate and must not be applied verbatim.
Key Vault and Layout adapters are opted in only in synthetic development,
not automatically selected by choosing Azure hosting.

Run focused tests, full CI, and physical rehearsal as documented in
../governance/P1B_ORIGINAL_DOCUMENT_ACCESS.md before marking ready.

## Investor sequence

1. Demonstrate patient login and genuine device enrollment. Upload one
   clearly synthetic, single-page report identifying Hospital X in the
   document. If using Azure extraction verify the call and evidence;
   otherwise label the extraction fixture explicitly, not live OCR.
2. Show patient review and finalization, then the longitudinal record.
3. Sign in as a real seeded eligible demo doctor with MFA at Hospital Y.
   Discover the synthetic patient and request routine clinical-history
   consent. Patient signs on the enrolled device.
4. View the actual document-reference inventory and source provenance.
   Request exact original-document access; patient reviews and signs it.
5. Open the stored original and show expiry/revocation and access-history
   evidence. Verify fresh retrieval is denied after revocation.
6. Demonstrate separately authorized emergency access and QR fallback.
   If NFC is simulated, label it; never claim physical qualification.

Record commit SHA, test evidence, exact runtime and device, timestamps,
results and blockers (no secret or patient values). Rehearse twice,
with a local fallback if the cloud is unavailable. This sequence is a
runbook, not executed evidence. The demo uses synthetic data and does
not establish clinical accuracy, regulatory compliance or production
readiness. Do not publish a public development login. Do not provision,
deploy or merge into main without the owner's explicit approval.
