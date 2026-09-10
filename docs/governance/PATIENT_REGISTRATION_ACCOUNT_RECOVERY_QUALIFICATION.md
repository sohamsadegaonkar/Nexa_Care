# Patient Registration Account Recovery — Qualification Record

Status: **PRE-ATTESTATION HEAD QUALIFIED / ATTESTATION HEAD REQUIRES REQUALIFICATION**

Base: `main` `859bde2aeaefe1c172e0e224746e4cbfffe4dac4`

Measured implementation head: `b3108dc3bd3915fceb9e83d5531ccd16751a9aa4`

## Exact-head evidence

GitHub Actions executed the full repository qualification matrix on the measured implementation head.

Backend CI run `#475` / run id `34512471283`:

- Quality & Pure Unit (Partition A): **PASS**.
- Ruff lint: **PASS**.
- Partition A zero-skip assertion: **PASS**.
- PostgreSQL Qualification (Partition B): **PASS**.
- Partition B zero-skip assertion: **PASS**.
- PostgreSQL + Redis Qualification (Partition C): **PASS**.
- Partition C zero-skip assertion: **PASS**.

Frontend CI run `#424` / run id `34512471316`:

- frontend tests: **PASS**.
- Next production build: **PASS**.
- workspace package build: **PASS**.
- Android native project generation and source compilation: **PASS**.
- iOS native project generation, CocoaPods installation, and source compilation: **PASS**.

## Review state at measured head

- PR `#40` remained draft while reviewed.
- Base SHA remained `859bde2aeaefe1c172e0e224746e4cbfffe4dac4`.
- No submitted GitHub reviews were present.
- No inline review comments were present.
- The security review retained the authority separation between OTP proof, recovery attempt state, one-time repair capability, patient session authority, device authority, and consent authority.
- Revocation, erasure, unexplained deletion, identity ambiguity, merge ambiguity, and canonical conflicts remain outside automatic repair.

## Attestation-head rule

This file itself changes the branch head. Therefore the measured PASS above qualifies `b3108dc3bd3915fceb9e83d5531ccd16751a9aa4`, not this documentation commit by assertion.

The resulting attestation head must pass the same Backend CI and Frontend CI workflows before PR #40 may be marked ready or merged. Immediately before merge, `main`, the PR head, changed-file scope, and review state must be reverified; merge must use an exact expected-head guard.
