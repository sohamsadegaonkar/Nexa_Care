# Orchestrator gateway implementation record

Scope: implement the 13 typed operations in the supplied ACTION_CATALOG.md for
the single server-owned repository `sohamsadegaonkar/Nexa_Care`.

Applicable invariants: least privilege, server-derived scope, expected-SHA
concurrency, durable mutation idempotency, metadata-only audit, bounded input,
safe errors, no credential disclosure, and truthful qualification reporting.
The gateway runs separately from the clinical API. It imports no clinical
modules and receives no patient DB, Redis, storage, extraction or consent keys.
Patient data, consent, identity evidence, clinical audit, extraction, erasure,
storage and emergency access are unchanged. No clinical migration or legal
policy changes are proposed. Regulatory relevance is data minimisation and
vendor/credential containment; this is not a compliance determination.

Architecture: strict HTTP models -> repository service -> fixed GitHub client;
separate policy module and local durable operation journal. SQLite is used only
for this single-instance engineering gateway's operation journal, not as a
replacement for Nexa's authoritative clinical PostgreSQL database. Mount a
durable private disk. Do not horizontally scale this version.

Writes accept only `orchestrator/` branches. File replacements/deletions bind
both blob SHA and branch head. Canonical path and UTF-8 checks apply to reads,
search and writes. Search scans bounded files at a resolved immutable commit;
it does not accept GitHub query syntax or redirect to another repository.

Merge uses only merge-commit semantics. GitHub's computed test merge commit
must have exactly the submitted base/head parents. All configured check names
must pass from their pinned GitHub App IDs on the head and test merge commit.
Review must approve the exact head; unresolved threads and missing/truncated
evidence deny. An atomic updateRefs mutation compares both main and head.
GitHub native protections remain authoritative and are never bypassed. This
path can be rejected by protection rules that require GitHub's ordinary PR
merge path; rejection is intentional, not a reason to weaken rules. Merge is
disabled by default until the repository owner qualifies this combination on
a disposable repository and approves activation. Squash/rebase are unsupported.

Durable journal intent precedes any remote mutation. Success is recorded before
return. A crash/timeout leaves an unresolved operation which fails closed on
replay; the operator must reconcile it against GitHub. No automatic retries or
claims of cross-system atomicity. Payload fingerprints use a dedicated HMAC
secret; journal records never store source content, tokens, bodies or queries.

Validation required: missing/bad auth, canonical path/branch bypasses, symlinks,
stale/missing blob/head/base, checks provenance and completeness, stale review,
unresolved threads, journal failure/replay/collision, upstream errors/timeouts,
response redaction, schema parity, and a disposable local HTTP smoke test.
Live GitHub App, protected-branch merge, hosting and GPT editor validation remain
separate gates; synthetic HTTP mocks prove no live provider behavior.
