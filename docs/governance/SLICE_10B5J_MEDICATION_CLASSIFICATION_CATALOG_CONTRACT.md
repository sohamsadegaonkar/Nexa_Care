# Slice 10B.5j — Medication Classification Catalog Contract

**Status:** GOVERNANCE / DESIGN CONTRACT ONLY  
**Regulatory cutoff:** 2026-09-19 (India)  
**Repository baseline:** `9294e750ca08e6e9beb336feaf2c5383fb38374e` on `main`  
**Inherited Alembic head:** `20260919_prescriber_eligibility`  
**Upstream verdict:** `WRITE_PRESCRIPTION PERSISTENCE BLOCKED`  
**Smallest prerequisite being closed by this slice:** `MEDICATION CLASSIFICATION CATALOG REQUIRED`

This slice defines the first server-owned medication classification catalog contract. It creates no catalog tables, release artifacts, loaders, medication entries, migrations, Prescription persistence, prescribing route, UI, formulary engine, or AI classifier.

The binding question is:

> May this exact medication catalog entry be used in Nexa WRITE_PRESCRIPTION v1 under the universal CARE_MODE_UNKNOWN boundary?

The answer must come only from a qualified, versioned, integrity-bound server release.

---

# 1. Frozen upstream boundaries

The following Slice 10B.5i decisions remain frozen:

```text
CARE MODE UNKNOWN

arbitrary free-text medication identity = BLOCKED

future medication identity =
server-controlled medication_code
+
server-resolved immutable medication_display

initial v1 medication scope =
only catalog entries explicitly qualified for the universal / ANY-mode boundary

unknown or unclassified medication = DENY
```

Initial v1 excludes:

- Schedule G;
- Schedule H;
- Schedule H1;
- Schedule X;
- NDPS narcotic/psychotropic substances;
- specialist/oncology/high-risk therapies;
- medicines requiring special prescribing/recordkeeping semantics not represented by v1;
- unknown/unclassified medication concepts.

The catalog is not a formulary recommendation engine. It answers only whether a medication concept is eligible for the narrow Nexa v1 persistence boundary.

---

# 2. Authoritative source hierarchy

## 2.1 Regulatory cutoff

This contract re-checked official sources through **2026-09-19**.

Draft notifications are evidence of a possible future change only. They never activate an allow decision.

A later final Gazette notification overrides an earlier consolidated compilation from its legally effective date.

## 2.2 Source hierarchy

### Tier 1 — binding national drug-control / statutory sources

| Authority/source | Date/version checked | Fact it proves for catalog governance | National applicability / precedence | Official URL |
| --- | --- | --- | --- | --- |
| CDSCO Drugs Rules, 1945 compilation | compilation dated 2024-07-01; CDSCO page release 2024-09-19 | Rule 65 prescription/dispensing constraints; Rule 97 labeling; Schedules G/H/H1/X and related rules | National rules baseline, subject to later final Gazette amendments | https://www.cdsco.gov.in/opencms/opencms/en/Acts-and-rules/Drugs-Rules/ |
| India Code Drugs and Cosmetics Rules, 1945 | official compilation checked 2026-09-19 | Rule 97 distinguishes G/H/H1/X labels; Rule 65 includes prescription/dispensing requirements | National central-rule text; later amendments must be layered on | https://www.indiacode.nic.in/bitstream/123456789/19158/1/2016drugsandcosmeticsact1940rules1945.pdf |
| CDSCO Gazette Notifications index | live index checked 2026-09-19 | Current final and draft amendments after consolidated Rules | Later final Gazette amendments override older compilation from their effective date | https://www.cdsco.gov.in/opencms/opencms/en/Notifications/Gazette-Notifications |
| G.S.R. 95(E) | 2024-02-05 final | Oseltamivir and Zanamivir inclusion in Schedule H1 | National final amendment | CDSCO Gazette index |
| G.S.R. 757(E) | 2025-10-16 final | Additional Schedule H2 categories | National final amendment; H2 is traceability/product-identity evidence, not a v1 allow grant | CDSCO Gazette index |
| G.S.R. 377(E) | 2026-05-13 final | Pregabalin and its formulations included in Schedule H1 | National final amendment | CDSCO Gazette index |
| G.S.R. 506(E) | 2026-06-22 final | Further Schedule H2 amendment | National final amendment; identity/traceability relevance only | CDSCO Gazette index |
| G.S.R. 745(E) | 2026-08-19 **draft** | Proposed H1 additions (Flupentixol, Zopiclone, Gabapentin, Carisoprodol) | **Not effective final law at this cutoff**; cannot be used as an allow source | https://www.cdsco.gov.in/opencms/resources/UploadCDSCOWeb/2018/UploadGazette_NotificationsFiles/2026.08.19%20G.S.R%20745%28E%29_Inclusion%20of%20additional%20four%20drugs%20in%20Schedule%20H1.pdf |
| NDPS Act, 1985 | current India Code text checked 2026-09-19 | Section 8 medical/scientific-purpose control subject to Act/rules/licence/permit conditions | National statute | https://www.indiacode.nic.in/handle/123456789/21511 |
| NDPS Rules, 1985 | current India Code rules checked 2026-09-19 | Rules 52F/52G impose prescription conditions for essential narcotic drugs, including identity, quantity, daily dose and period of consumption | National rules | https://upload.indiacode.nic.in/showfile?actid=AC_CG_61_1073_00014_00014_1563259383370&filename=the_narcotic_drugs_and_psychotropic_substances_rules%2C_1985_date_14.11.1985.pdf&type=rule |

### Tier 2 — operative professional / telemedicine medication-scope sources

| Authority/source | Date/version | Fact it proves | Use in catalog |
| --- | --- | --- | --- |
| Telemedicine Practice Guidelines, Appendix 5 to the 2002 professional-conduct regulations | 2020-03-25 | List O/A/B/prohibited medicine framework and dependence on consultation mode/type; base prohibited group includes Schedule X and NDPS narcotic/psychotropic substances | Primary mode-policy source for the CARE_MODE_UNKNOWN product boundary |
| Official modification to Telemedicine medicine lists | 2020-04-11 | Specific additions to List A including Phenobarbitone, Clobazam and Clonazepam | Demonstrates that category decisions require versioned amendments, not substring rules |
| NMC Rules & Regulations page | checked 2026-09-19 | 2023 RMP Professional Conduct Regulations are listed together with the 2023-08-23 amendment keeping them in abeyance | Prevents silently replacing the operative telemedicine basis with held-in-abeyance 2023 text |
| Indian Medical Council Professional Conduct, Etiquette and Ethics Regulations, 2002 | published 2002-04-06; current NMC publication | Current professional-conduct baseline referenced by the 2020 Telemedicine Practice Guidelines | Professional baseline only; not a medication identity vocabulary |

Official URLs:

- https://www.nrces.in/download/files/pdf/Telemedicine.pdf
- https://www.nmc.org.in/MCIRest/open/getDocument?path=%2FDocuments%2FPublic%2FPortal%2FLatestNews%2FModification+in+Medicine+lists+in+Telemedicine+Practice+Guidelin.pdf
- https://www.nmc.org.in/rules-regulations-nmc/
- https://www.nmc.org.in/rules-regulations/code-of-medical-ethics-regulations-2002/

### Tier 3 — official India medication terminology / identity sources

#### Common Drug Codes for India (CDCI)

NRCeS, a MoHFW national resource centre operated by C-DAC, publishes **Common Drug Codes for India** for clinical documentation, ePrescription, dispensing, stock and supply-chain interoperability.

The current NRCeS News surface records a CDCI/DISB release on **2026-08-31**. NRCeS distributes:

- a SNOMED CT India Drug Extension / terminology-integrated package;
- a CDCI flat-files package;
- a Drug Information Service Bundle (DISB).

CDCI includes generic clinical-drug concepts and branded medicine concepts. It is suitable as a medication **identity/interoperability source**, but it is not itself the legal authority that decides current Schedule G/H/H1/X, NDPS, or Nexa universal-policy eligibility.

Official URLs:

- https://www.nrces.in/services/national-releases
- https://www.nrces.in/resources
- https://www.nrces.in/news
- https://www.nrces.in/faqs

#### Licensing

SNOMED CT use in India requires an Affiliate Licence. NRCeS states that MoHFW has made SNOMED CT free for use within India, but an Affiliate Licence is still required. Web/mobile software used outside India may require additional international licensing.

Therefore:

```text
CDCI/SNOMED identity use =
permitted only under a valid applicable licence
```

A license failure or unavailable release cannot be bypassed by accepting arbitrary medication text.

### Tier 4 — external terminology mappings

RxNorm is not adopted as Nexa's v1 medication authority.

The U.S. National Library of Medicine states that RxNorm is U.S.-centric and primarily covers drugs available in the United States. Its normalized names/codes can be useful for interoperability, but they do not establish Indian regulatory schedule or telemedicine status.

External terminologies may later be mappings, never v1 allow authority unless separately governed.

---

# 3. Catalog ownership

## 3.1 Organizational owner

Medication catalog releases are owned by a server-side organizational authority:

```text
NEXA MEDICATION CATALOG RELEASE AUTHORITY
```

The operational function belongs to medication-safety / clinical-regulatory governance, not ordinary clinical users.

The catalog must never be:

- provider-maintained;
- patient-maintained;
- editable from normal provider/patient frontend workflows;
- generated or activated by AI;
- silently synchronized from an unreviewed external API;
- activated by a legacy affiliation role;
- activated merely because a source terminology published a new version.

## 3.2 Existing trust-permission infrastructure

The repository already has a strong model for GLOBAL organizational permissions:

```text
ProviderTrustPermissionGrant
TrustManagementPermission
TrustPermissionScope.GLOBAL
append-only grant / revoke semantics
fresh privileged authorization
structural audit
```

This infrastructure is a suitable **mechanical substrate** for future medication-catalog governance.

However, the current closed permission vocabulary contains:

```text
PROFESSIONAL_REVIEW
PRESCRIBING_ELIGIBILITY_REVIEW
FACILITY_REVIEW
AFFILIATION_MANAGE
TRUST_PERMISSION_MANAGE
```

None semantically authorizes pharmaceutical catalog review/activation.

Therefore:

```text
DO NOT REUSE PRESCRIBING_ELIGIBILITY_REVIEW
DO NOT REUSE TRUST_PERMISSION_MANAGE
```

A later implementation slice requires a dedicated GLOBAL medication-catalog governance permission.

Conceptual future permission:

```text
MEDICATION_CATALOG_RELEASE_REVIEW
```

Activation additionally requires a release transition that proves the approver is not the preparer. A second permission may be introduced if implementation review concludes activation and classification review must be independently delegated.

No permission enum or migration is added by this slice.

## 3.3 Human separation of duties

Every release requires:

1. one **preparer** responsible for source collection/classification;
2. one independent **approver** with medication-catalog release authority;
3. preparer != approver;
4. the approver reviews every ALLOW decision and the release-level digest;
5. activation is server-side and auditable.

For the first release, a second independent clinical/regulatory reviewer is required for every `v1_universal_allowed=true` entry. Thus the positive allow decision has two-person review even if one reviewer also performs release activation.

---

# 4. Medication-code decision

## 4.1 Decision

For v1, Nexa should use an **official India generic clinical-drug concept identifier from CDCI / the India Drug Extension for SNOMED CT** as the preferred medication identity code when the exact concept is available and licensed.

```text
medication_code =
string representation of the approved CDCI/SNOMED clinical-drug concept identifier
```

The code is imported into a Nexa-owned catalog release. The **catalog release and policy decision are server-owned** even though the underlying terminology identifier is externally governed.

## 4.2 Why CDCI/SNOMED is preferred over a new arbitrary Nexa code

NRCeS explicitly publishes common drug codes for India for clinical and ePrescription use. SNOMED concept identifiers are designed to uniquely identify concepts and follow concept-permanence rules; inactive concepts remain available for historical interpretation and are not to be reused for different meanings.

This gives:

- deterministic identity;
- stable historical semantics;
- versioned source traceability;
- India relevance;
- generic and branded concept coverage;
- compatibility with Indian digital-health interoperability.

## 4.3 Why the code does not grant policy

```text
CDCI/SNOMED code exists
!=
Nexa v1 may prescribe it
```

The CDCI concept identifies the medicine. Nexa's evidence-bound release separately proves:

- current schedule status;
- NDPS status;
- telemedicine class;
- special-recordkeeping status;
- specialist/high-risk product-policy status;
- universal ANY-mode eligibility.

## 4.4 Fallback when an official concept is absent

No local free-text concept may be created inside a production release.

If the exact medication concept required for a proposed allow entry is unavailable or ambiguous in the qualified terminology source:

```text
DO NOT ADD THE ENTRY
```

A future Nexa-owned code namespace may be governed separately, but it is not needed for the first catalog implementation.

---

# 5. Generic / brand scope

## 5.1 First-release decision

```text
V1 CATALOG = GENERIC CLINICAL-DRUG CONCEPTS ONLY
```

No brand/product-specific medication entries are permitted in the first governed release.

Reasons:

- the first release is a safety allowlist, not a commercial formulary;
- brand identity adds manufacturer/licence/product lifecycle complexity;
- arbitrary brand text must not bypass classification;
- CDCI already permits later mapping between generic and branded concepts if required;
- generic-only keeps the initial regulatory review surface narrow.

The provider-facing future UI must select the generic catalog concept. It must not accept a typed brand as authorization identity.

## 5.2 Strength and form granularity

The phrase "generic" does **not** mean molecule-only.

Official restrictions may apply to:

- substance;
- salt;
- preparation;
- formulation;
- concentration;
- strength;
- dose form.

Therefore the v1 code must resolve to a generic clinical-drug concept granular enough to distinguish the formulation/strength/form facts required by the governing source.

Catalog fields may include:

```text
canonical_generic_name
dose_form
identity_strength_descriptor
ingredient_set
```

where these are identity facts supplied by the terminology source.

This is separate from the future PrescriptionItem:

```text
strength: bounded clinician-authored display string
```

The clinician-authored string is not the medication identity and cannot override the catalog concept.

Future persistence must deny a submitted strength that materially contradicts the selected catalog identity.

---

# 6. Catalog-entry identity contract

Conceptual immutable entry:

```text
MedicationCatalogEntry
- release_id
- medication_code
- code_system
- code_system_version
- canonical_generic_name
- medication_display
- ingredient_identity
- dose_form
- identity_strength_descriptor
- classification fields
- v1_universal_allowed
- classification_rationale_code
- entry_integrity_digest
```

Rules:

- `medication_code` is a string; never parse it as a JavaScript floating number;
- code + code-system version identify the source terminology concept;
- the same source code is never repurposed to mean a different concept;
- inactive/retired source concepts cannot enter a new release as allowed;
- `medication_display` is server-resolved for that release;
- release history preserves old displays for historical rendering;
- source terminology updates never mutate an already QUALIFIED/ACTIVE release entry.

Uniqueness:

```text
UNIQUE(release_id, medication_code)
```

---

# 7. Closed classification vocabulary

One ambiguous boolean such as `is_controlled` is prohibited.

Independent dimensions are required.

## 7.1 Drug-schedule class

```text
DrugScheduleClass =
NONE_CONFIRMED
G
H
H1
X
MULTIPLE_RESTRICTED
UNKNOWN
```

`NONE_CONFIRMED` is an evidence-bearing negative finding. It is not a default.

## 7.2 NDPS class

```text
NdpsClass =
NOT_CONTROLLED_CONFIRMED
CONTROLLED
ESSENTIAL_NARCOTIC_DRUG
UNKNOWN
```

The more specific essential-narcotic value does not make the medicine allowable; it records why special statutory prescription semantics apply.

## 7.3 Telemedicine class

```text
TelemedicineClass =
LIST_O_ANY_MODE
LIST_A_VIDEO_FIRST_OR_FOLLOWUP
LIST_B_FOLLOWUP_ONLY
PROHIBITED
NOT_ESTABLISHED
UNKNOWN
```

`NOT_ESTABLISHED` means no positive official telemedicine classification was proven for the exact concept.

For `CARE_MODE_UNKNOWN`, only `LIST_O_ANY_MODE` can satisfy the first-release telemedicine criterion.

## 7.4 Special-recordkeeping class

```text
SpecialRecordkeepingClass =
NONE_CONFIRMED
REQUIRED
UNKNOWN
```

## 7.5 Nexa specialist/high-risk class

This is a Nexa product-safety classification, not a claim that India has one statutory "specialist drug schedule."

```text
NexaHighRiskClass =
NONE_CONFIRMED
ONCOLOGY_HIGH_RISK
SPECIALIST_RESTRICTED
OTHER_HIGH_RISK
UNKNOWN
```

Any non-NONE value denies v1 universal use.

## 7.6 Regulatory product-status class

```text
RegulatoryProductStatus =
CURRENT
RESTRICTED
PROHIBITED
DRAFT_CHANGE_PENDING
UNKNOWN
```

A draft Gazette proposal may set `DRAFT_CHANGE_PENDING`, but cannot be used to turn a denied entry into allowed.

## 7.7 Derived policy result

```text
v1_universal_allowed: boolean
```

This is server-derived at release build time. It is never supplied by a client and cannot be hand-edited independently of the evidence/classification record.

---

# 8. V1 universal allow criteria

An entry receives:

```text
v1_universal_allowed = true
```

only when **every** criterion below is proven:

1. exact active CDCI/SNOMED generic clinical-drug identity exists under the qualified source version;
2. identity granularity is sufficient for all relevant schedule/formulation distinctions;
3. `DrugScheduleClass = NONE_CONFIRMED`;
4. `NdpsClass = NOT_CONTROLLED_CONFIRMED`;
5. `SpecialRecordkeepingClass = NONE_CONFIRMED`;
6. `NexaHighRiskClass = NONE_CONFIRMED`;
7. `TelemedicineClass = LIST_O_ANY_MODE`;
8. `RegulatoryProductStatus = CURRENT`;
9. no final CDSCO prohibition/restriction makes the proposed v1 use incompatible;
10. every required negative classification has evidence; absence of a positive hit is not enough without a reviewed source check;
11. source documents are official and current through the release `source_cutoff_at`;
12. draft notifications are recorded but never treated as final allow authority;
13. preparer review is complete;
14. independent clinical/regulatory reviewers approve the positive classification;
15. entry digest matches the canonical entry/evidence representation;
16. release digest is valid;
17. release status is `ACTIVE`;
18. no effective emergency deny applies to the code.

If any condition cannot be proven:

```text
v1_universal_allowed = false
```

There is no "probably allowed" runtime state.

---

# 9. First-release content strategy

## 9.1 Strategy

Choose:

```text
A. VERY SMALL REVIEWED ALLOWLIST
```

The first release is not an attempt to classify the Indian medicines market.

The release exists to prove:

- source ingestion discipline;
- exact identity mapping;
- negative schedule/NDPS checks;
- telemedicine ANY-mode proof;
- independent human review;
- deterministic digest;
- activation/rollback behavior;
- future fail-closed runtime lookup.

## 9.2 First-release cap

```text
MAXIMUM 20 v1_universal_allowed entries
```

This is an operational safety cap, not a legal threshold.

Justification:

- every positive entry requires source collection plus explicit G/H/H1/X, NDPS, telemedicine, recordkeeping, product-status, and high-risk review;
- every positive entry requires independent human review;
- the full positive set must be re-reviewable as one qualification packet at each source cutoff;
- a twenty-entry maximum creates a deliberately bounded blast radius while still being large enough to exercise multiple ordinary medication forms and the entire catalog lifecycle.

Denied sample/test concepts may be included in qualification fixtures, but they are not part of the production allow count.

No later increase is automatic. Expanding the cap is a separately reviewed catalog-policy change.

---

# 10. Source evidence per entry

Every entry requires evidence sufficient to justify every classification dimension.

Conceptual evidence record:

```text
MedicationCatalogEvidence
- evidence_id
- release_id
- medication_code
- source_authority
- source_document_title
- source_document_version_or_notification
- publication_date
- effective_date
- source_url
- source_locator
- checked_at
- finding_type
- finding_value
- classification_rationale_code
- evidence_digest
- prepared_by
- reviewed_by
```

Allowed `source_authority` values are closed, conceptually:

```text
CDSCO
INDIA_CODE
MOHFW
NMC
NRCES
SNOMED_INTERNATIONAL_IDENTITY_ONLY
```

SNOMED/CDCI evidence can establish identity/version facts but not substitute for CDSCO/India Code statutory classification.

Evidence requirements for an ALLOW entry include explicit findings for:

- source terminology identity active/current;
- G/H/H1/X schedule check;
- NDPS check;
- telemedicine List O ANY-mode proof;
- special-recordkeeping check;
- current final prohibition/restriction check;
- Nexa specialist/high-risk review.

## Evidence storage boundary

Do not store full source PDFs or pharmaceutical documents in generic audit metadata.

The catalog evidence layer stores:

- normalized finding;
- stable source locator;
- publication/effective dates;
- source document digest where legally/operationally permitted;
- bounded excerpt locator or page/rule/schedule reference.

Large official documents remain external governed evidence.

---

# 11. Release model

Conceptual object:

```text
MedicationCatalogRelease
- release_id
- version
- status
- effective_at
- source_cutoff_at
- policy_version
- source_terminology_version
- integrity_digest
- artifact_signature
- artifact_key_id
- prepared_by
- qualified_by
- activated_by
- created_at
- qualified_at
- activated_at
- superseded_at
- revoked_at
- previous_release_id
```

## 11.1 Closed status vocabulary

```text
DRAFT
QUALIFIED
ACTIVE
SUPERSEDED
REVOKED
```

Operational meanings:

### DRAFT

- not runtime authority;
- mutable while preparation is in progress;
- may be deleted/rebuilt before qualification because it has never authorized clinical action.

### QUALIFIED

- content/review/digest frozen;
- not yet runtime authority;
- immutable.

### ACTIVE

- one and only one release used for new v1 medication validation;
- immutable.

### SUPERSEDED

- historical release replaced by a newer ACTIVE release;
- immutable;
- remains valid historical evidence for Prescriptions issued under it.

### REVOKED

- release must not authorize new issuance because of an integrity/governance/regulatory incident;
- immutable historical artifact;
- does not rewrite Prescriptions already issued under it.

## 11.2 Single-active invariant

At most one release may be ACTIVE.

Future relational enforcement requires a partial unique constraint/index over the ACTIVE status or a transactional singleton active pointer.

No compatibility model permits multiple simultaneous ACTIVE v1 releases.

---

# 12. Release immutability

Published authority is immutable.

```text
DRAFT -> QUALIFIED -> ACTIVE -> SUPERSEDED
                       \-> REVOKED
QUALIFIED -> REVOKED
```

No ACTIVE or QUALIFIED entry may be UPDATEd to change:

- medication identity;
- display;
- classification;
- source reference;
- evidence digest;
- allow decision.

New evidence requires:

```text
NEW RELEASE
```

Historical Prescriptions continue to bind the exact release that authorized them.

---

# 13. Integrity strategy

## 13.1 Canonical release artifact

The canonical release is a deterministic UTF-8 JSON manifest.

Canonicalization must use a specified stable serialization; preferred implementation contract:

```text
RFC 8785 JSON Canonicalization Scheme
```

The manifest orders entries by `medication_code` and evidence references by deterministic key.

## 13.2 SHA-256 release digest

```text
integrity_digest =
SHA-256(canonical_release_manifest_bytes)
```

The digest binds at minimum:

- manifest schema version;
- catalog release version;
- policy version;
- source cutoff;
- source terminology version;
- ordered medication entries;
- code-system identities;
- canonical displays;
- every classification field;
- v1 universal decision;
- classification rationale codes;
- evidence reference metadata;
- evidence digests.

Do not include volatile relational surrogate IDs, insertion timestamps, database sequence values, or storage URLs in the digest if they prevent independent reproduction.

## 13.3 Signature

The future release artifact should additionally carry a detached asymmetric signature produced by a platform-held, non-exported signing key.

The implementation slice must qualify the exact algorithm/key-management boundary. AWS KMS may be used if that slice preserves existing crypto/KMS governance.

Digest equality is mandatory even when signature verification succeeds.

---

# 14. Storage / AWS deployment contract

Future runtime uses **both**:

1. immutable signed release artifact as the independently verifiable release source;
2. relational database projection for transactional medication lookup.

The database projection is not allowed to silently diverge from the artifact digest.

Activation must verify that:

```text
artifact digest
==
release.integrity_digest
==
digest of relational projected release
```

Future private artifact storage may use versioned/object-immutable AWS storage, but this slice performs no upload, deployment, bucket change, task-definition change, or AWS mutation.

Task-1 live-deployment state remains unchanged.

---

# 15. Release workflow

Frozen conceptual workflow:

```text
official source collection
-> terminology identity resolution
-> classification preparation
-> first human review
-> second review for every positive allow entry
-> canonical artifact build
-> integrity digest/signature build
-> release qualification
-> activation
```

A source API or terminology update may create a DRAFT candidate only.

It must never auto-activate.

## 15.1 Qualification requirements

A release can become QUALIFIED only when:

- source cutoff is explicit;
- all positive entries satisfy the universal criteria;
- every entry's identity resolves in the qualified terminology version;
- all evidence references are reachable or have a qualified evidence digest/locator;
- all positive reviews are complete;
- no preparer self-approves;
- canonical manifest reproduces the stored digest;
- denied/unknown fixture cases fail closed.

---

# 16. Activation semantics

## 16.1 Server-owned current release

New Prescription issuance must resolve the current ACTIVE release **server-side at mutation time**.

The request must not accept a caller-selected:

```text
catalog_version
policy_version
release_id
v1_universal_allowed
```

The provider client supplies only the selected current `medication_code` and bounded PrescriptionItem semantics.

## 16.2 Old/superseded codes

If a medication code was present in a previous release:

- if the same code exists and is allowed in the current ACTIVE release, current-release validation governs the new issuance;
- if absent, inactive, denied, or emergency-denied in the ACTIVE release, new issuance is denied;
- the server never falls back to an older release merely because the client remembered an old code.

A frontend catalog cache is advisory UX only.

---

# 17. Catalog-change semantics

When new official evidence changes a medication classification:

```text
new evidence
-> new catalog release
-> new Prescriptions use new release
```

Do not rewrite historical Prescription issuance.

A historical Prescription remains evidence of:

> what a clinician issued under the authority and catalog version current at that time.

Never automatically:

- delete the historical Prescription;
- mutate the item;
- change it to invalid;
- create/discontinue an active medication record;
- rewrite patient history.

Retrospective safety action is a separate clinical-safety workflow.

---

# 18. Emergency-deny strategy

An immutable release may need an urgent fail-closed restriction before a complete replacement release is qualified.

A separate append-only deny overlay is justified:

```text
MedicationCatalogEmergencyDeny
```

Conceptual fields:

```text
emergency_deny_id
medication_code
applies_to_release_id (nullable for global-current/future deny)
reason_code
source_authority
source_reference
source_effective_at
effective_at
expires_at
created_by
created_at
supersedes_deny_id
evidence_digest
```

Closed conceptual reason codes include:

```text
REGULATORY_PROHIBITION
REGULATORY_RECLASSIFICATION
SOURCE_INTEGRITY_FAILURE
CATALOG_CLASSIFICATION_ERROR
PATIENT_SAFETY_HOLD
```

Rules:

- deny only; never emergency allow;
- append-only;
- structurally audited;
- server-owned;
- immediately fails closed for new issuance;
- original catalog release remains unchanged;
- lifting a deny requires an explicit later governance record/release transition, never row deletion.

---

# 19. Unknown / source-failure policy

Future WRITE_PRESCRIPTION validation must deny when any of these are true:

- medication code absent;
- code ambiguous;
- source terminology concept inactive or unresolved;
- classification incomplete;
- any required classification dimension is UNKNOWN;
- source evidence unavailable where revalidation is required;
- active release missing;
- multiple ACTIVE releases;
- release status not ACTIVE;
- release digest mismatch;
- artifact signature invalid/unknown;
- relational projection digest mismatch;
- policy version unsupported;
- source terminology licence/required package unavailable;
- effective emergency deny exists.

Frozen behavior:

```text
FAIL CLOSED
NO FREE-TEXT FALLBACK
NO "UNKNOWN = ORDINARY"
```

---

# 20. Future Prescription binding

A future canonical Prescription must persist the exact server-derived:

```text
medication_catalog_release_id
medication_catalog_version
medication_policy_version
```

Each PrescriptionItem persists:

```text
medication_code
medication_display
strength
frequency
```

`medication_display` is copied from the ACTIVE release at issuance time.

Historical rendering must not perform a live lookup and replace the old display with a newer catalog term.

The persisted display snapshot and medication code must correspond to the same release entry.

---

# 21. Idempotency consequences

Future operation remains:

```text
treatment.write_prescription.v1
```

The committed canonical request hash must bind:

- ordered medication codes;
- server-resolved display snapshots;
- active catalog release ID/version;
- medication policy version;
- clinician-authored strength;
- clinician-authored frequency;
- item order;
- all other frozen Prescription authority bindings.

## 21.1 Lost-response replay across a release change

A completed idempotent replay is **not a new medication authorization decision**.

Required order:

1. look up the existing idempotency key in the same actor/tenant/operation scope;
2. if a completed result exists, recover the originally bound release/version and resolved aggregate;
3. recompute/compare the original canonical request semantics using that stored release binding;
4. identical request -> return original logical result;
5. changed request -> conflict;
6. only when no prior reservation/result exists may the server resolve the currently ACTIVE catalog release for a new mutation.

Therefore an ACTIVE release change after a successful commit does not transform a genuine replay into a second clinical issuance.

---

# 22. Concurrent release change / TOCTOU contract

The future Prescription transaction must not validate against release A and commit after release B becomes authoritative without preserving the original atomic decision.

Required database locking order:

```text
begin transaction
-> resolve exactly one ACTIVE MedicationCatalogRelease
-> lock ACTIVE release row FOR SHARE (or stronger)
-> verify release status/version/digest
-> resolve/lock requested release entries
-> apply emergency-deny check
-> reserve idempotency
-> perform Prescription transaction
-> commit
```

Release activation/supersession must:

```text
lock current ACTIVE release FOR UPDATE
-> qualify new release state
-> supersede old release
-> activate new release
-> commit atomically
```

The shared prescription lock conflicts with the release status update, so activation waits for an in-flight transaction that already bound the old ACTIVE release.

The implementation slice may use an equivalent serializable/version-pointer design, but it must prove the same no-TOCTOU invariant.

---

# 23. Audit contract

Catalog governance audit is structural.

Allowed metadata:

- release ID/version;
- status transition;
- entry count;
- allowed-entry count;
- policy version;
- source cutoff;
- source terminology version;
- integrity digest;
- signature key identifier;
- preparer/reviewer/activator internal IDs;
- activation/supersession/revocation event;
- emergency-deny ID/reason code;
- medication_code when needed as a global policy-reference identifier.

Do not put in generic audit metadata:

- entire pharmaceutical source documents;
- large source excerpts;
- API credentials;
- licence credentials;
- KMS private material;
- patient information;
- Prescription clinical text;
- Treatment Session identifiers.

---

# 24. Patient / provider data separation

Medication catalog data is global reference/policy data.

It must contain no:

- patient ID;
- clinical Encounter ID;
- Treatment Session ID;
- ConsentGrant;
- patient clinical history;
- provider prescribing history.

Reviewer/approver identity is organizational governance provenance only and must not be confused with a patient's prescribing clinician.

---

# 25. Representative design-only classification samples

These rows validate that the model can express the required decisions. They are **not production catalog entries** and are not authority for prescribing.

| Representative concept | Official evidence used for design validation | Classification shape | v1 universal result |
| --- | --- | --- | --- |
| Paracetamol tablet / exact future CDCI generic clinical-drug concept | Telemedicine Practice Guidelines List O explicitly names Paracetamol; Drugs Rules Schedule K household-remedy text includes Paracetamol Tablets under conditions excluding Schedule G/H/X substances; release review must still explicitly complete H1/NDPS/current-prohibition negative checks for the exact concept | schedule: NONE_CONFIRMED after full release review; NDPS: NOT_CONTROLLED_CONFIRMED; telemedicine: LIST_O_ANY_MODE; special recordkeeping: NONE_CONFIRMED; high-risk: NONE_CONFIRMED | **POTENTIAL TRUE** for a production release only after exact CDCI identity + all required negative findings are dual-reviewed |
| Pregabalin / exact formulation concept | G.S.R. 377(E), 2026-05-13, final inclusion of Pregabalin and its drug formulations in Schedule H1 | schedule: H1 | **FALSE / DENY** |
| Methylphenidate / exact formulation concept | Schedule X in the Drugs Rules, 1945 | schedule: X; telemedicine base policy: prohibited category | **FALSE / DENY** |
| Unmapped or ambiguous brand text, e.g. `ExampleMed-ZX` | no qualified catalog identity/evidence | schedule: UNKNOWN; NDPS: UNKNOWN; telemedicine: UNKNOWN; identity unresolved | **FALSE / DENY** |

The paracetamol row is deliberately phrased as a **candidate positive design example**, not a pre-approved production entry. The first implementation slice must create the actual evidence packet and exact code before setting the release flag true.

---

# 26. Conceptual relational schema

No migration is created now.

## 26.1 MedicationCatalogRelease

Conceptual key:

```text
release_id UUID PRIMARY KEY
version VARCHAR UNIQUE NOT NULL
```

Important constraints/indexes:

- closed status check;
- unique version;
- partial unique index ensuring at most one ACTIVE release;
- unique integrity digest;
- `source_cutoff_at <= qualified_at <= activated_at` where applicable;
- preparer cannot equal final approver/activator under the separation policy;
- immutable-state mutation guard for QUALIFIED/ACTIVE/SUPERSEDED/REVOKED.

Indexes:

- status/effective_at;
- source_cutoff_at;
- policy_version.

## 26.2 MedicationCatalogEntry

Conceptual key:

```text
PRIMARY KEY (release_id, medication_code)
```

Foreign key:

```text
release_id -> MedicationCatalogRelease.release_id ON DELETE RESTRICT
```

Important fields:

- code system/version;
- generic name/display;
- ingredient identity;
- form;
- identity strength descriptor;
- all closed classification dimensions;
- v1 universal boolean;
- rationale code;
- entry digest.

Indexes:

- medication_code;
- release_id + v1_universal_allowed;
- release_id + schedule class;
- release_id + telemedicine class.

No entry may exist outside one exact release.

## 26.3 MedicationCatalogEvidence

Conceptual key:

```text
evidence_id UUID PRIMARY KEY
```

Foreign key:

```text
(release_id, medication_code)
-> MedicationCatalogEntry
ON DELETE RESTRICT
```

Uniqueness should prevent duplicate evidence insertion for the same release/code/finding/evidence digest.

Indexes:

- release_id/medication_code;
- source authority;
- publication/effective date;
- evidence digest.

## 26.4 MedicationCatalogEmergencyDeny

Conceptual key:

```text
emergency_deny_id UUID PRIMARY KEY
```

Important constraints:

- code required;
- reason closed;
- effective_at required;
- expires_at > effective_at when present;
- append-only;
- no row deletion as a revoke mechanism.

Index active denies by:

```text
medication_code, effective_at, expires_at
```

---

# 27. Runtime invariant for future implementation

A future new Prescription item is acceptable only when:

```text
exact code exists in locked ACTIVE release
+
release integrity is valid
+
entry identity is current
+
entry.v1_universal_allowed == true
+
no effective emergency deny
+
all existing Treatment Session / Encounter / prescriber authority gates pass
```

The catalog never replaces professional prescribing eligibility.

The prescribing authority graph remains conjunctive.

---

# 28. Implementation-readiness verdict

```text
MEDICATION CATALOG CONTRACT READY FOR IMPLEMENTATION
```

The source hierarchy, India-relevant terminology identity, licensing boundary, generic-only first scope, classification dimensions, universal allow predicate, two-person governance, release lifecycle, deterministic integrity model, emergency deny overlay, failure policy, Prescription binding, idempotency behavior, concurrency locking, audit boundary, storage model, and conceptual schema are defined sufficiently for a bounded **catalog-infrastructure implementation slice**.

This verdict does **not** authorize canonical Prescription persistence.

---

# 29. Exact next Task-0 action

Implement only the medication catalog infrastructure defined here.

The next slice may create:

- catalog release/entry/evidence/emergency-deny persistence;
- a qualified release artifact format;
- digest/signature verification;
- governed release preparation/review/activation services;
- dedicated medication-catalog governance permission if required by the implementation;
- runtime catalog lookup service;
- test-only sample fixtures.

The next slice must still **not** create:

- canonical Prescription / PrescriptionItem tables;
- `WRITE_PRESCRIPTION` persistence route;
- provider prescription UI;
- patient prescription UI;
- formulary recommendations;
- AI medication classification.

Before any subsequent Prescription persistence slice, at least one small production catalog release must itself be populated from official evidence, independently reviewed, integrity-qualified, activated, and fail-closed under adversarial tests.

Core invariant:

```text
versioned identity
+
official evidence
+
closed classification
+
independent review
+
integrity-bound release
+
fail-closed runtime authority
before
canonical medication Prescription persistence
```
