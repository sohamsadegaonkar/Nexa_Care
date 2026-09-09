# Slice 8G — ABDM/NHA HPR/HFR Machine-Contract Readiness

Status: **INTERNAL REGISTRY BOUNDARY READY; OFFICIAL SERVER-TO-SERVER TRANSPORT CONTRACT EXTERNALLY BLOCKED**

## Current authoritative position

Nexa already has an internally qualified provider-registry boundary in
`app/services/provider_verification_registry.py` and durable worker/application
layers around it. That boundary deliberately separates:

```text
registry lookup request
!= registry observation
!= verification evidence record
!= lifecycle decision
!= system automation authority
!= clinical authority
```

The worker also defaults to an empty adapter map and automation disabled. No
external HPR/HFR transport is active by default.

## 2026-09-10 authoritative-source re-check

The current qualification pass re-checked official ABDM/NHA material rather
than relying on old summaries or unofficial API mirrors.

NRCeS publishes the ABDM FHIR R4 implementation guide used by Slice 8D for
health-data interoperability. That FHIR implementation guide is **not** treated
as an HPR/HFR server-to-server registry transport contract.

This qualification pass did not obtain from authoritative NHA/ABDM sources a
complete machine contract that establishes, together and versionably:

- server-to-server authentication/credential lifecycle;
- professional-registry lookup endpoint and HTTP method;
- facility-registry lookup endpoint and HTTP method;
- request and response schemas;
- authoritative identity-binding semantics;
- registry status/error dispositions;
- retry semantics;
- rate-limit semantics;
- an official sandbox/qualification target; and
- change/versioning policy.

Therefore no endpoint, token exchange, response mapping, retry policy, or
rate-limit behavior is inferred from third-party or historical material.

## Machine-checkable blocker

`docs/governance/ABDM_HPR_HFR_MACHINE_CONTRACT_GATE.json` records the current
blocked state and the complete minimum evidence set required before the gate may
be changed to READY.

Regression tests require while the gate is blocked that:

- `external_adapter_enabled=false`;
- provider verification worker defaults remain automation-off with no adapters;
- no HPR/HFR-named concrete transport adapter is committed under
  `app/services`.

Adding an actual HPR/HFR adapter therefore requires an intentional change to the
contract gate and its tests in the same reviewed change, rather than quietly
introducing guessed network behavior.

## What happens when authoritative material becomes available

The concrete implementation should remain thin:

1. validate and record the official contract provenance/version;
2. implement HPR/HFR transport behind the existing `RegistryAdapter` template
   method boundary;
3. map official responses into immutable `RegistryObservation` values without
   carrying raw credentials or response bodies into clinical authority;
4. preserve the closed retry/error distinction, especially that genuine
   transient source unavailability is not interchangeable with auth failures,
   malformed responses, identity mismatch, or not-found outcomes;
5. qualify professional and facility lookups against the official sandbox;
6. persist evidence through the existing verification application layer; and
7. re-run provider-trust PostgreSQL/Redis and adversarial qualification before
   enabling automation.

## Nonclaims

Slice 8G does not claim:

- live HPR lookup;
- live HFR lookup;
- NHA sandbox qualification;
- official registry credentials;
- an inferred endpoint or authentication scheme;
- external Provider Trust qualification.

The remaining HPR/HFR work is an external-contract dependency, not missing core
provider-trust architecture.
