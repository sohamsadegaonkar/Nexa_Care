# Nexa Care India Regulatory Baseline

[Repository agent contract](../../AGENTS.md) · [Security non-regression standard](SECURITY_NON_REGRESSION.md) · [Engineering constitution](NEXA_CARE_ENGINEERING_CONSTITUTION.md)

> This document is an engineering compliance baseline, not legal advice.
> Applicability and final interpretations require qualified Indian legal,
> privacy, cybersecurity and medical-device counsel.

Status: Legal and regulatory review pending  
Owner: Nexa Care leadership  
Security reviewer: Human owner required  
Privacy/legal reviewer: Qualified Indian counsel required  
Clinical reviewer: Qualified clinical owner required  
Last reviewed: 2026-07-27  
Next review: 2026-10-25 or sooner on an official change  
Repository baseline: `a9d542f` on `feature/document-processing-e2e`; Alembic head `20260727_doc_process_bind`

Verified as of: **2026-07-27**. “Verified” means an official source was located and reviewed for engineering relevance; it does not mean counsel approved an interpretation or that Nexa Care is compliant.

## Source policy

Legal and regulatory claims must use primary sources from the Gazette of India, MeitY, MoHFW, NHA/ABDM, CDSCO, CERT-In, NMC, India Code, or another official statutory portal. Blogs, news, law-firm/vendor summaries, and AI-generated text are not legal authority.

Every review records instrument, authority, official source, publication/effective dates where verified, status, applicability, engineering implications, open questions, and last verification. Unknown dates or applicability are marked for verification; they are never invented.

Regulatory status vocabulary is limited to: **binding**, **notified but phased**, **official guidance**, **voluntary standard**, **contractual requirement**, **internal policy**, **legal review pending**, **not applicable**, and **applicability uncertain**. Guidance is not legislation.

## Official source register

| Instrument | Authority and official source | Publication / effective date | Current status | Applicability and engineering implications | Open legal questions | Last verified |
|---|---|---|---|---|---|---|
| Digital Personal Data Protection Act, 2023 | Parliament / Gazette; [MeitY data-protection framework](https://www.meity.gov.in/data-protection-framework) | 2023; section-by-section commencement must be checked against notifications | Binding where commenced; legal review pending | Governs digital personal data roles, purpose/legal basis, notice/consent or legitimate uses, safeguards, rights, breach, deletion, children, and possible SDF duties | Exact commenced provisions, Nexa Care role per workflow, SDF status, cross-border notifications | 2026-07-27 |
| Digital Personal Data Protection Rules, 2025; enforcement timeline; Board notifications; corrigendum | MeitY; [official Rules package](https://www.meity.gov.in/documents/act-and-policies/digital-personal-data-protection-rules-2025-gDOxUjMtQWa?pageTitle=Digit) | Rules/timeline/Board materials 2025-11-14; corrigendum 2025-12-16 | Notified but phased | Notice, safeguards, breach process, contacts, rights/grievance, retention/deletion, children, and Board procedures require a commencement-aware implementation map | Rule-by-rule dates and corrigendum effect require counsel sign-off | 2026-07-27 |
| ABDM Health Data Management Policy | NHA/MoHFW; [official policy PDF](https://abdm.gov.in/static/media/health_management_policy_bac9429a79.80f74bc3e039c00acd4f.pdf) and [ABDM publications](https://abdm.gov.in/publications/) | 2020 policy; 2022 draft revision listed | Official guidance / ecosystem policy; contractual effect may arise through participation | Consent-based, purpose-limited, federated exchange; user control, audit, security, ABHA linkage, grievance controls | Which version governs each planned ABDM integration and which obligations become contractual | 2026-07-27 |
| EHR Standards for India, 2016 | MoHFW; [official EHR Standards PDF](https://www.mohfw.gov.in/sites/default/files/EMR-EHR_Standards_for_India_as_notified_by_MOHFW_2016_0.pdf) | 2016 | Official guidance / voluntary standard unless adopted contractually or otherwise required | Interoperability, coding, provenance, privacy/security, preservation, exchange, and display choices | Required standards/profile versions for each hospital and ABDM interface | 2026-07-27 |
| Information Technology Act, 2000 | Parliament / MeitY or India Code official text | Dates and current amendments require source re-verification | Binding; applicability mapping pending | Security, unauthorized access, intermediary/body-corporate and incident provisions may apply | Interaction with DPDP commencements and healthcare contracts | Verification task open |
| SPDI Rules, 2011 | MeitY / Gazette official text | 2011; continuing applicability requires counsel review | Binding/applicability uncertain | Health data classification, privacy policy, consent, transfer, reasonable security and grievance controls may remain relevant | Displacement/continuity after phased DPDP commencement | Verification task open |
| CERT-In Directions under IT Act section 70B and FAQ | CERT-In; [Directions portal](https://www.cert-in.org.in/Directions70B.jsp), [28 April 2022 Directions](https://www.cert-in.org.in/PDF/CERT-In_Directions_70B_28.04.2022.pdf), [official FAQ](https://www.cert-in.org.in/PDF/FAQs_on_CyberSecurityDirections_May2022.pdf) | 2022-04-28; phased extension 2022-06-27 | Binding directions; entity-specific provisions require applicability review | Clock synchronisation, reportable incident triage, evidence/log availability, response contacts; FAQ describes reporting severe incidents/data breaches within the stated window | Which entity categories and log-retention provisions apply directly to Nexa Care versus vendors | 2026-07-27 |
| Medical Devices Rules, 2017 | CDSCO/MoHFW; [official MDR page](https://cdsco.gov.in/opencms/opencms/en/Acts-and-rules/Medical-Devices-Rules/) | 2017-01-31; amendments listed by CDSCO | Binding if product is medical-device software | Intended use and diagnosis/prevention/monitoring/treatment functions drive classification, licensing, QMS, change and post-market duties | Classification of every current/planned clinical feature | 2026-07-27 |
| Guidance document on Medical Device Software under MDR 2017 | CDSCO; [official Medical Device & Diagnostics page](https://cdsco.gov.in/opencms/opencms/en/Medical-Device-Diagnostics/Medical-Device-Diagnostics/) | 2026-07-21 | Official guidance | Requires immediate feature/intended-use assessment for document extraction, summaries, flags, alerts, CDS, and predictive models | Detailed guidance interpretation and regulatory pathway require specialist review | 2026-07-27 |
| Telemedicine Practice Guidelines and FAQ | NMC/erstwhile MCI and MoHFW; [official archive](https://www.nmc.org.in/old-archive-news/1000/) and [official FAQ](https://nmc.org.in/MCIRest/open/getDocument?path=%2FDocuments%2FPublic%2FPortal%2FLatestNews%2FFinal_FAQ-TELEMEDICINE++6-4-2020..pdf) | 2020-03-25; FAQ 2020-04-06 | Official professional guidance; legal review pending | Applies only if remote consultation/communication/prescribing features exist; requires RMP/patient identity, consent, appropriateness, prescription, records, emergency limits | Current amendments and enforceability after NMC regulatory changes | 2026-07-27 |
| Clinical Establishments legislation/rules | MoHFW plus applicable state/UT official sources | Jurisdiction-specific | Binding where applicable; applicability uncertain | Hospital record retention, registration, and operating requirements may become contractual/product requirements | State-by-state facility analysis | Verification task open |
| Consumer Protection Act/rules and dark-pattern guidance | Department of Consumer Affairs / India Code / official gazette | Current consolidated sources require verification | Binding where applicable | Truthful medical/service claims, price/refund clarity, no dark patterns, no tying legally required record access to unrelated payment | Nexa Care’s contracting/fee role and current guidelines | Verification task open |
| Aadhaar Act/regulations and ABDM ABHA policy | UIDAI plus NHA official sources | Version-specific | Binding / ecosystem policy as applicable | Aadhaar is not mandatory absent approved lawful need; ABHA, Aadhaar, local patient ID, and hospital MRN remain distinct | Whether any planned verification invokes Aadhaar regulation | Verification task open |

## Required regulatory inventory

### REG-001 — Digital Personal Data Protection Act, 2023

For each processing activity, document data fiduciary/processor roles, actors, purpose, legal basis (consent or an applicable legitimate use), notice, minimum necessary data, accuracy responsibility, safeguards, breach handling, deletion trigger, grievance path, Data Principal rights, child-data treatment, records, cross-border position, and possible Significant Data Fiduciary duties. Consent must not be assumed to be the only basis. Penalty and duty interpretations require counsel.

Engineering baseline: maintain a processing inventory; version notices/consents; enforce purpose/scope; support access/correction/erasure/grievance workflows; preserve proof without storing secrets; bind processors contractually; and make retention configurable by approved policy.

### REG-002 — Digital Personal Data Protection Rules, 2025

The final Rules package, official enforcement timeline, Data Protection Board establishment/size materials, and 16 December 2025 corrigendum must be reviewed together. Maintain a rule-by-rule table distinguishing already effective provisions, future commencement, further-notification dependencies, and early best-practice controls.

Engineering mapping must cover clear standalone notice, security safeguards, personal-data breach workflow, published contact, Data Principal requests and grievance handling, retention/erasure, children and verifiable parental consent where applicable, and evidence for Board procedures. Do not turn a phased requirement into an unsupported “currently mandatory” claim.

### REG-003 — DPDP consent and clinical consent

DPDP processing consent, clinical treatment/workflow consent, ABDM health-information exchange consent, and a Nexa Care access capability are related but not interchangeable. Each record must name the authorization type, legal/product purpose, data, actors, scope, duration, revocation semantics, and evidence. Account creation is not blanket clinical-data consent.

### Patient onboarding profile and legal evidence

The patient onboarding profile and a legal-document acceptance are distinct engineering records, not evidence that DPDP consent, clinical consent, ABDM consent, or another legal authorization has been obtained. The service derives identity from authenticated server state; stores profile name/date of birth under patient-envelope encryption; and keeps server-owned document version, digest, timestamp, and transactionally staged audit evidence separate from profile PII. Current onboarding is derived from presently configured documents, so changed or unavailable requirements must not be represented as durable completion.

The applicable notice, legal basis, acceptance wording, retention period, erasure interaction, evidence use, and whether any record can be relied upon for a regulated purpose require qualified counsel and an approved decision record. This baseline records technical controls and makes no compliance or counsel-approval claim.

### REG-004 — ABDM Health Data Management Policy

When Nexa Care participates in ABDM, implement consent-based exchange, purpose limitation, minimum necessary scope, user control and revocation, HIP/HIU responsibilities, consent artefact expectations, security/audit, federated exchange, ABHA linkage rules, and grievance handling. The local architecture must not assume that ABDM participation or every policy provision already applies; record the integration and contract that creates applicability.

### REG-005 — ABDM technical integration

Production ABDM integration requires official sandbox/production onboarding, registered identifiers, current API/profile conformance, consent-manager/HIP/HIU role decisions, encryption and key management, callback authentication, replay/idempotency controls, error mapping, audit evidence, and certification evidence. A local FHIR export is not proof of ABDM conformance. Current official technical specifications and certification criteria remain a verification task before integration.

### REG-006 — Electronic Health Record Standards for India, 2016

Treat the MoHFW standard as an interoperability baseline: preserve semantic codes and units, identity, provenance, authorship, timestamps, corrections/versioning, privacy/security, exchange formats, accessibility, and source evidence. Record which recommendations are adopted, superseded by an approved current profile, or contractually required. Do not call guidance legislation.

### REG-007 — Information Technology Act, 2000

Map unauthorized access, security, confidentiality, electronic records/signatures, body-corporate duties, intermediary questions, and CERT-In authority only from current official consolidated text. DPDP does not justify deleting IT Act incident/security controls without counsel review. Current amendment/interaction analysis is open.

### REG-008 — SPDI Rules, 2011

Health information has historically been treated as sensitive personal data. Maintain privacy notice, purpose limitation, security, transfer/vendor, grievance, and consent controls while counsel determines which SPDI provisions continue for each processing activity during DPDP’s phased commencement. Never claim the Rules are wholly displaced without verified authority.

### REG-009 — CERT-In Directions under section 70B

Maintain accurate time, incident classification, rapid escalation, evidence availability, approved log retention, CERT-In contacts, and reporting decision records. The Directions and FAQ include a six-hour reporting framework for specified severe/reportable incidents, but do not hardcode that as the deadline for every breach or regulator.

Applicability matrix:

| Requirement | Nexa Care | Hosting/cloud/vendor | Status |
|---|---|---|---|
| Clock synchronisation | Treat as applicable security baseline | Contractually require compatible evidence | Legal review pending |
| Reportable cyber-incident triage | Incident team must assess | Vendor must notify/support promptly | Legal review pending |
| Logs/evidence | Preserve approved minimum without health-data leakage | Contract must preserve/return evidence | Applicability uncertain by entity/category |
| Data-centre/VPS/cloud/VPN customer validation duties | Do not assume Nexa Care is the regulated provider | Review vendor category | Applicability uncertain |

### REG-010 — Medical Devices Rules, 2017

Before any release that diagnoses, prevents, monitors, treats, recommends, or materially influences care, create an intended-use statement and assess medical-device status, classification, licensing, QMS, clinical/performance evidence, post-market surveillance, vigilance, and controlled changes. Product naming and marketing claims are part of intended use.

### REG-011 — CDSCO guidance on Medical Device Software

| Feature | Intended use | Diagnoses / recommends treatment / monitors / urgent influence | Potential status | Required boundary | Review |
|---|---|---|---|---|---|
| Document extraction | Transcribe source into structured candidates | No, if it only transcribes for human review | Applicability uncertain | Provenance, confidence, no fabricated output, human adjudication | Regulatory + clinical |
| Summarisation | Condense existing records | Could influence if presented clinically | Applicability uncertain | Source remains visible; label uncertainty; no autonomous conclusion | Regulatory + clinical |
| Emergency summary | Minimum existing facts during emergency | Can influence urgent decisions | High review priority | Separate limited dataset, source links, explicit banner | Regulatory + clinical required |
| Clinician-approved intake | Commit reviewed facts | No autonomous diagnosis | Applicability uncertain | Licensed clinician responsibility and immutable review evidence | Regulatory + clinical |
| Risk flags/alerts | Surface configured risk | May monitor/influence decisions | Potential medical-device software | No hidden inference; validated rule/model; escalation design | Regulatory + clinical required |
| Clinical decision support | Recommend or prioritize care | Yes or potentially | Potential medical-device software | Not in current autonomous product boundary | Approval before design |
| Predictive models | Predict outcomes | Potentially diagnoses/monitors | Potential medical-device software | Prohibited without formal regulatory program | Approval before design |
| Diagnosis/rule-out | Diagnose or exclude disease | Yes | Likely regulated; counsel decides | Outside current product boundary | Mandatory regulatory review |

Current boundary: no autonomous diagnosis, disease rule-out, treatment or dosage recommendation; clinician-approved pathways; AI summaries never replace sources; uncertainty and provenance remain visible.

### REG-012 — Telemedicine Practice Guidelines

Apply only to remote consultation, doctor-patient communication, prescription, or audio/video/chat consultation—not ordinary record access. If introduced, verify registered medical practitioner and patient identity, capture appropriate consent, assess consultation suitability, constrain prescriptions, preserve records, and communicate emergency limitations. Review current NMC amendments/FAQs before release.

### REG-013 — Aadhaar and ABHA

Do not require Aadhaar where it is unnecessary; do not store it absent an approved lawful need; never expose it; never use Aadhaar authentication without authorized integration. Keep explicit, separately governed fields for Nexa Care patient UUID, ABHA address/number, Aadhaar, and hospital MRN. ABHA is not Aadhaar and is not Nexa Care’s canonical internal identifier.

### REG-014 — Clinical establishments and hospital duties

Each participating hospital may face state/UT clinical-establishment law, medical-record retention, accreditation, professional confidentiality, and local policy. Maintain a jurisdiction/facility matrix with hospital counsel; do not assume one national retention rule applies everywhere.

### REG-015 — Consumer protection

Patient-facing claims must be accurate and understandable. Disclose responsible parties, limitations, prices, subscriptions, cancellation/refund rules, and service availability; prohibit dark patterns, hidden fees, misleading medical claims, and making legally required record access conditional on unrelated paid services. Applicability and current official instruments require counsel verification.

### REG-016 — Children and guardians

Design age determination, guardian authority and evidence, verifiable parental consent when required, adolescent confidentiality escalation, emergency access, revocation, and transition to adulthood. Never behaviorally monitor or target advertising to children. Healthcare-consent interactions, age thresholds, and exceptions remain legal/clinical review questions.

### REG-017 — Retention and deletion

No period below is invented. A human-approved schedule must populate authority and duration.

| Data category | Purpose | Minimum / maximum retention | Legal source / hospital policy | Deletion trigger and method | Audit evidence retained | Open question |
|---|---|---|---|---|---|---|
| Account data | Identity/service | To be approved | DPDP + contract | Purpose end/valid request; erase or de-identify | Request/outcome | Required residual identifiers |
| Consent challenges | Single-use authorization | To be approved | DPDP/ABDM/product | Expiry/consumption; purge Redis | Non-secret audit | Forensic window |
| Access grants | Enforce scoped access | To be approved | Product/ABDM/contract | Expiry/revocation; delete capability, retain status | Grant/revoke audit | Ledger retention |
| Audit ledger | Accountability/security | To be approved | Multiple instruments/policy | Immutable retention then approved archive | Chain evidence | Legal maximum and erasure interaction |
| Clinical records | Care continuity | Facility-specific | Hospital/state/professional | Correct/archive/erase only under approved policy | Correction/access evidence | State/facility schedule |
| Source PDFs | Evidence/provenance | To be approved | Hospital/clinical/device assessment | Encrypted archival then approved erasure | Hash/reference | Whether source is medical record |
| Extracted fields | Clinical workflow | Linked to source policy | Same as above | Supersede/correct; approved erasure | Version/provenance | Conflict retention |
| Push tokens | Notification delivery | Shortest operational need | DPDP/product | Logout/revocation/stale token; delete | Minimal event | Staleness window |
| Device keys | Consent verification | Account/device lifecycle | Security policy | Revoke/rotate; retain public-key history as approved | Enrollment/revocation | Historical signature verification |
| Security logs | Detection/response | To be approved | CERT-In/applicable policy | Secure expiry | Incident linkage | Exact applicable period |
| Incident records | Response/evidence | To be approved | CERT-In/DPDP/contracts | Approved archive/destruction | Decision record | Regulator limitation periods |
| Billing records | Payment/tax if introduced | To be approved | Tax/consumer/contracts | Statutory expiry | Transaction evidence | Nexa Care role |
| Erasure tombstones | Prevent resurrection | To be approved | Internal security policy | Only approved terminal process | Tombstone/assurance | Minimal durable content |

### REG-018 — Breach response

Support detection, containment, evidence preservation, affected-data and actor mapping, legal notification decision, Data Principal and Data Protection Board communication where applicable, CERT-In reporting where applicable, hospital/vendor/regulator coordination, recovery, and post-incident review. Maintain multiple clocks and decision owners; never hardcode one deadline for all authorities.

### REG-019 — Processors and vendors

Cloud, Redis, object storage, document AI, FCM/push, SMS/email, monitoring, analytics, and support contracts must restrict purpose, ensure confidentiality/security, disclose subprocessors, support breach response, return/delete data, preserve audit evidence, and permit data-location/cross-border review. No vendor may train on Nexa Care health data without an explicit approved contract and lawful basis. Send only approved minimum data.

### REG-020 — Compliance decision records

Every regulated feature must record:

```text
Feature:
Data involved:
Actors:
Purpose:
Legal basis:
Consent required:
Notice required:
Retention:
Access rules:
Regulatory instruments:
Security controls:
Owner:
Legal reviewer:
Clinical reviewer:
Approval status:
```

Unapproved or unanswered records block controlled pilot/production for that feature.

## Engineering control matrix

| Control ID | Requirement | Code / data / route / UI | Audit and test evidence | Owner | Status |
|---|---|---|---|---|---|
| REG-C01 | Purpose- and scope-bound access | consent models/services/routes; capability UI | consent audit events and adversarial tests | Product + privacy | Partially enforced |
| REG-C02 | Patient transparency/revocation | access-history projection; consent revoke route/screens | provider-read and revoke audits/tests | Product + security | Enforced |
| REG-C03 | Provenance and human review | pipeline/extracted fields/source route/review UI | extraction/review/source-view events and tests | Clinical + ML | Enforced locally |
| REG-C04 | Rights/grievance handling | Workflow design required | Decision/evidence tests required | Privacy + support | Legal review pending |
| REG-C05 | Retention/erasure | erasure registry/tombstones/KMS; schedule required | erasure audits/tests; real KMS pending | Privacy + security | Partially enforced |
| REG-C06 | Breach response | Operational playbook and safe telemetry | Exercise/evidence pending | Security + legal | Validation pending |
| REG-C07 | ABDM exchange | FHIR/export foundations only | Official conformance evidence absent | Interoperability | Applicability uncertain |
| REG-C08 | Medical-device boundary | AI scoring/pipeline/emergency summary | Classification record absent | Regulatory + clinical | Legal review pending |
| REG-C09 | Telemedicine | No canonical consultation workflow identified | Reassess before feature | Product + clinical | Not applicable to current record access |
| REG-C10 | Children/guardians | Dedicated workflow not established | Tests/evidence absent | Privacy + clinical | Legal review pending |

## Open legal and regulatory questions

1. Which DPDP Act/Rules provisions are effective on each release date, and how does the corrigendum alter the timeline?
2. Is Nexa Care a Data Fiduciary, Data Processor, or both for each hospital/patient workflow?
3. What notices, legitimate uses, consent records, rights, and deletion duties apply per workflow?
4. Do any cross-border or Significant Data Fiduciary notifications apply?
5. Which SPDI duties remain during phased DPDP commencement?
6. Which CERT-In entity-specific log/customer-validation provisions apply directly versus to infrastructure vendors?
7. Which ABDM policy/version and contractual requirements apply to planned roles?
8. Are emergency summaries, risk flags, or extraction/summarisation medical-device software under MDR 2017 and the 2026 CDSCO guidance?
9. Which state/facility record-retention and clinical-establishment rules govern each partner?
10. What child/guardian and adolescent-confidentiality rules govern each clinical context?
11. What is the approved record retention and cryptographic-erasure schedule?

## Update procedure

Review at least every 90 days; whenever MeitY, NHA, MoHFW, CDSCO, CERT-In, NMC, UIDAI, or a relevant state authority changes a source; before pilot/production; and before diagnosis, prescriptions, telemedicine, insurance, payments, children’s workflows, or ABDM production integration.

For each update: retrieve the official text, archive its identity/date, distinguish binding/guidance/contract/internal status, record changes and commencement, obtain legal/clinical review as applicable, update the control and decision matrices, add tests/evidence tasks, and never claim compliance from document review alone.

Sources not fully verified in this review: current consolidated IT Act text and amendments; current SPDI applicability; current Consumer Protection/dark-pattern instruments; Aadhaar/ABHA identity requirements; state clinical-establishment and retention laws; current ABDM technical/certification specifications; exact rule-by-rule DPDP commencement interpretation; full substantive interpretation of the 21 July 2026 CDSCO guidance. These are tracked verification tasks, not assumed requirements.

[Repository agent contract](../../AGENTS.md) · [Security non-regression standard](SECURITY_NON_REGRESSION.md) · [Engineering constitution](NEXA_CARE_ENGINEERING_CONSTITUTION.md)
