# Nexa Care Migration Graph Fix

> Historical alpha validation evidence; current migration state must be read from Alembic.

## 1. Executive verdict

- Result: APPROVE WITH REQUIRED FOLLOW-UP
- Fresh database upgrade: Passed to head after the migration repairs.
- Existing database compatibility: Revision IDs and mergepoint are preserved; legacy bootstrap tables are detected and retained.
- Final Alembic head: `20260712_tombstone_integrity`
- Remaining blockers: trusted-host HTTP 400 in application checks/tests; the already-upgraded alpha database needs the newly identified `patient_device_keys` timestamp correction applied through an approved Alembic follow-up revision.

## 2. Observed failure

The cleanup root attempted `ALTER TABLE nexa_vault ...` before its assumed schema existed and PostgreSQL raised `UndefinedTableError`. After that defect was repaired, the audit exposed the same missing-ancestry class for `consent_grant_log` and `biometric_registry`.

## 3. Root cause

`20260704_drop_raw_pii_from_vault` and `20260705_nexa_v1` were independent base revisions joined only by `3a7109a78d4e`. Alembic orders revisions by graph metadata, not filename chronology, so the cleanup had no guarantee that core ran first. `DROP COLUMN IF EXISTS` handles an absent column but not an absent table. In addition, the core revision did not create four application tables referenced by later Alembic migrations.

## 4. Migration graph before

```text
base
|-- 20260704_drop_raw_pii_from_vault
`-- 20260705_nexa_v1
     \\ /
  3a7109a78d4e
```

## 5. Migration graph after

```text
base -> 20260705_nexa_v1
          | dependency
          v
       20260704_drop_raw_pii_from_vault
          \\ /
       3a7109a78d4e -> ... -> 20260712_tombstone_integrity
```

The mergepoint and single final head remain unchanged.

## 6. Compatibility analysis

- Empty database: core creates canonical shard, consent-grant, and biometric tables; cleanup then runs defensively.
- Partially migrated database: stable revision IDs remain valid; table/column existence checks tolerate legacy bootstrap state.
- Databases past the mergepoint: revision IDs and merge ancestry are unchanged.
- Revision-ID preservation: all existing identifiers were retained; no new head was introduced.
- Downgrade behavior: cleanup restores nullable JSONB only when `nexa_vault` exists and the column is absent. Legacy bootstrap tables are not dropped because their provenance cannot be inferred safely.

## 7. Files changed

- `alembic/versions/20260704_drop_raw_pii_from_vault.py`: added the core dependency and guarded upgrade/downgrade SQL.
- `alembic/versions/20260705_nexa_v1_core_identity_consent.py`: creates missing canonical tables when absent while retaining legacy tables.
- `alembic/versions/11c1c7e3c464_add_assurance_to_consent_grant_log.py`: makes column additions idempotent.
- `alembic/versions/d2f75cf736b2_add_device_public_key_to_biometric_.py`: makes the device-key column addition idempotent.
- `alembic/versions/20260707_add_patient_device_keys.py`: aligns fresh schema with model timestamp columns.
- `tests/test_migration_graph.py`: adds four focused graph/SQL regression tests.
- `docs/alpha-validation/migration-graph-fix.md`: records this validation.

## 8. Database state

- Alembic state before: no current revision was reported; the failed transaction had rolled back.
- Alembic state after: `20260712_tombstone_integrity (head)`.
- Required tables verified: `nexa_vault`, `nexa_clinical`, `patient_device_keys`.
- `raw_pii`: absent from both shard tables.
- Required indexes/constraints: shard primary/index/unique structures and patient-device primary/unique/index structures verified.
- Follow-up: this database reached head before the timestamp omission was discovered, so its `patient_device_keys` table still lacks `created_at` and `updated_at`; no manual DDL or stamping was used.

## 9. Commands executed

- Alembic `history --verbose`, `branches`, `heads`, `current`, and `upgrade head` via the repository virtual environment.
- Targeted `git grep`/`rg` migration audit.
- Read-only information-schema and index verification.
- `python scripts/check_alpha_environment.py --all`
- `python scripts/consent_preflight.py`
- Requested pytest suites and `tests/test_migration_graph.py`.

## 10. Test results

| Check | Result | Passed | Failed | Notes |
| ----- | ------ | -----: | -----: | ----- |
| history / branches / heads | PASS | 3 | 0 | Single expected head; dependency visible |
| upgrade head / current | PASS | 2 | 0 | Final revision confirmed |
| schema verification | PASS | 1 | 0 | Three required tables; no `raw_pii` |
| environment check | PASS | 3 | 0 | PostgreSQL, Redis, Supabase connectivity |
| consent preflight | FAIL | 2 | 2 | HTTP 400 host validation; missing applied timestamp columns |
| architecture tests | PASS | 8 | 0 | All passed |
| alpha invariant tests | FAIL | 19 | 4 | All failures received HTTP 400 host rejection |
| device consent/security tests | FAIL | 1 | 13 | All failures received HTTP 400 host rejection |
| migration tests | PASS | 4 | 0 | All focused tests passed |

Pytest total: 32 passed, 17 failed. Non-pytest checks are reported separately above.

## 11. Issues discovered

- High — cleanup root metadata: nondeterministic cross-root table access. Resolved with `depends_on`; verified in history and tests.
- High — missing canonical Alembic tables: fresh chain failed at `consent_grant_log` and would later fail at `biometric_registry`. Resolved with compatibility-aware core creation; verified by full upgrade.
- Medium — unguarded historical column additions: schema drift could cause duplicate-column failures. Resolved with inspection guards.
- Medium — `patient_device_keys` timestamps omitted: ORM queries fail against the applied alpha schema. Fresh migration corrected; applied database follow-up still required.
- Medium — trusted host configuration: test client and `/health` return HTTP 400. Kept separate from migration changes.

## 12. Remaining issues

Migration follow-up: add `patient_device_keys.created_at` and `updated_at` to databases that applied the old revision, using a new approved corrective Alembic revision. Runtime follow-up: align `TRUSTED_HOSTS` and the test/API host (`localhost` versus `127.0.0.1`) so health and consent tests reach application routes.

## 13. Final recommendation

APPROVE WITH REQUIRED FOLLOW-UP
