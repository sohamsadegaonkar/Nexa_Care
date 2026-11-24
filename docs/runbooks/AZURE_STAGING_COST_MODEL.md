# Nexa Care Azure cost and scaling model

Central India public retail estimates in INR, retrieved 24 November 2026.
Amounts are monthly planning estimates before tax, discounts, credits, support,
AWS, Supabase, Twilio, mobile device costs and DNS domain registration. The
model assumes 730 hours and 2,628,000 seconds per month. Verify the effective
rate, quota and budget in the intended subscription before approval.

## Development and functional staging

| Environment | Assumptions | Estimated Azure spend |
| --- | --- | ---: |
| Minimal development | Run the real app with disposable local Postgres/Redis and synthetic fixtures. Optional cloud adapter benchmark requires its own isolated account, endpoint and budget. | ₹0 local Azure consumption |
| Staging foundation | B1ms PostgreSQL 16, 32 GB storage, 14-day same-region PITR, one Redis B0 node, ACR Basic, three Private Endpoints, four private DNS zones. Foundation stays allocated a full month. | About ₹6,043 fixed |
| Functional staging, controlled sessions | Foundation stays allocated. Two app groups active for 80 hours in the month at 20% active time. Budget 20 synthetic identities, 100 OTPs, 40 new 2.5 MiB documents, 120 pages for extraction, 10 concurrent requests, 10 GB egress, up to 5 GB encrypted recovery artifacts and 2 GB analytics logs. | About ₹7,610 |
| Functional staging, always on | Same volumes, with one API+scanner replica and one web replica allocated for 730 hours. Active resource fraction 5–35%, 15% midpoint. | ₹13,244–₹17,186, midpoint ₹14,558 |

The API group reserves 2 vCPU/4 GiB, including the scanner. The web group
reserves 0.5 vCPU/1 GiB. Actual ACA active time depends on HTTP requests and
background activity, so measure an isolated run before relying on the low
end. Stop the app groups deliberately between approved test windows only
after audit queues drain. The stateful services, endpoints, registry, logs
and backup retention can continue to bill. Managed Azure PostgreSQL backup
storage up to provisioned server storage is included at this price basis.
The initial hybrid qualification sends real synthetic pages to isolated
Textract. Its usage and cross-cloud transfer are external costs. An approved
separate Azure prebuilt benchmark of 120 pages is about ₹114 at list price
before any F0 allowance. [Azure PostgreSQL backup pricing and behavior](https://learn.microsoft.com/azure/postgresql/flexible-server/concepts-backup-restore) · [ACA pricing](https://azure.microsoft.com/en-in/pricing/details/container-apps/)

## Production sizing scenarios

The scenarios assume approved Azure-native extraction and storage, a
Central India regional WAF, two warm API/scanner and two warm web replicas
(5 vCPU/10 GiB total), zone-redundant database and two Redis nodes. They are
capacity estimates for a future deployment. Sizing depends on measured
latency, burst, data isolation and extraction quality.

40% of registered patients are active monthly. Each active patient uploads
two documents averaging three pages and 2.5 MiB, triggers 2.5 SMS sends,
issues 500 HTTP requests and transfers 0.08 GB out. The object footprint is
12 months of steady uploads plus 20% for versions and updates, before any
longer retention. PostgreSQL PITR and isolated restore budgets must cover the
actual approved retention period. These volumes include app and clinical
workload estimates and exclude real historical records.

| Registered patients | Monthly active | New docs / OCR pages | Retained Blob GiB | SMS sends | Peak concurrent requests | Egress GB/month |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 500 early production | 200 | 400 / 1,200 | 14 | 500 | 8 | 16 |
| 1,000 | 400 | 800 / 2,400 | 28 | 1,000 | 15 | 32 |
| 10,000 | 4,000 | 8,000 / 24,000 | 281 | 10,000 | 75 | 320 |
| 100,000 | 40,000 | 80,000 / 240,000 | 2,813 | 100,000 | 500 | 3,200 |
| 1,000,000 | 400,000 | 800,000 / 2,400,000 | 28,125 | 1,000,000 | 4,000 | 32,000 |

| Registered patients | Primary size × total DB nodes; storage/node | Redis / logs and DB backup GiB over allowance | Azure fixed | Azure variable | Model total |
| ---: | --- | --- | ---: | ---: | ---: |
| 500 early production | D2ds v5 × 2; 32 GB | B1 HA / 6 and 0 | ₹98,651 | ₹5,671 | ≈ ₹1.04 lakh |
| 1,000 | D2ds v5 × 2; 64 GB | B1 HA / 8 and 0 | ₹1,00,450 | ₹8,982 | ≈ ₹1.09 lakh |
| 10,000 | D4ds v5 × 2; 128 GB | B5 HA / 20 and 60 | ₹1,58,880 | ₹41,529 | ≈ ₹2.00 lakh |
| 100,000 | D8ds v5 × 2; 512 GB | B10 HA / 70 and 256 | ₹2,73,335 | ₹3,26,344 | ≈ ₹6.00 lakh |
| 1,000,000 | D16ds v5 × 3 incl. one read replica; 4,096 GB | B20 HA / 400 and 2,048 | ₹8,23,359 | ₹30,62,454 | ≈ ₹38.86 lakh |

## Model inputs and source rates

| Meter | INR retail rate or explicit assumption |
| --- | --- |
| ACA | ₹0.0023 per active vCPU-second, ₹0.0003 per idle vCPU-second, ₹0.0003/GiB-second, ₹38.0915/million requests. Production active fractions are 10%, 15%, 28%, 55%, 75% with 0, 20, 140, 1,700, 18,000 incremental active vCPU-hours respectively. Extra active compute has 2 GiB per vCPU. |
| PostgreSQL | B1ms ₹2.3331/hour. D2ds/D4ds/D8ds/D16ds v5 ₹23.9024/47.8048/95.6097/191.2193 per node-hour. Provisioned disk ₹12.475/GB-month. Incremental LRS backup ₹9.0467/GB-month. Count HA nodes separately as a conservative planning basis. |
| Managed Redis | Balanced B0/B1/B5/B10/B20 ₹1.6189/3.1425/15.4271/31.2350/62.2796 per cache-hour meter. Model one billable node for staging and two for HA. Confirm the SKU-specific HA pricing basis against the current subscription quote. |
| Object storage and OCR | Hot LRS ₹1.9046/GB-month, with a ₹0.003 per-document transaction allowance. Document Intelligence S0 layout/prebuilt benchmark ₹952.2875/1,000 pages, without commitment discounts. Production redundancy choice is a further sensitivity. |
| Registry, Vault, networking | ACR Basic/Premium ₹15.8651/₹158.7082 per day. Key Vault standard ₹2.8569/10,000 operations, with ₹29/month for production estimates. Private Endpoint planning rate ₹0.9523/hour each plus metered data where applicable, private DNS ₹47.6144/zone-month. Production plans use five links/zones. |
| Logging and outbound | Analytics ingestion ₹306.6366/GB. To be conservative this model does not deduct the 5 GB ingestion allowance. Internet outbound ₹11.4275/GB after 100 GB/month, using the first paid tier for all usage. |
| Production regional edge | ₹38,000/39,000/43,000/55,000/1,40,000 per month as explicit WAF, capacity and network allowances. Replace with the selected WAF v2 fixed and measured capacity-unit rates and bandwidth before proposing spend. |

Public meter data comes from the Azure Retail Prices API with
`currencyCode='INR'`, Consumption pricing and `armRegionName='centralindia'`
where regional meters apply. For example the B1ms meter is
`27c18f86-1cc2-552e-b6b9-657d170d563d`, B0 is
`bf063fd6-9db7-5e24-bdef-9ac0bde98ed3`, and the prebuilt OCR meter is
`ddaa022c-eaf7-542d-8766-d86879934f9a`. Service pricing and allocation
should be rechecked for redundancy, backups, approved data retention, ACR
storage above quota, Private Link data, regional edge capacity and cross-region DR.
[Retail API](https://learn.microsoft.com/rest/api/cost-management/retail-prices/azure-retail-prices) · [Managed Redis](https://azure.microsoft.com/en-in/pricing/details/managed-redis/) · [Private Link](https://azure.microsoft.com/en-in/pricing/details/private-link/) · [Application Gateway cost units](https://learn.microsoft.com/azure/application-gateway/understanding-pricing)

## Cost controls and scale triggers

- Use local engineering by default. Share the ACA consumption free grant
  across an eligible subscription only after confirming no other projects
  consume it. Investigate OCR F0 only for isolated synthetic compatibility.
  Neither allowance eliminates registry, database, endpoint or recovery costs.
- Azure for Students advertises USD 100 of credit for 12 months subject to
  eligibility and offer limits. Verify the intended account's actual
  subscription offer, balance, expiry, spending limit, allowed region, and
  quotas. A generic offer does not demonstrate a credit or capacity allocation.
  No benefit or spend is assumed in the model. [Azure for Students](https://azure.microsoft.com/en-in/free/students/) · [Azure products by region](https://azure.microsoft.com/explore/global-infrastructure/products-by-region/) · [ACA quotas](https://learn.microsoft.com/azure/container-apps/quotas)
- Measure PostgreSQL CPU, IOPS, locks, connections and growth before A/B
  sizing. Revisit B1ms when burst credits decline persistently, p95 response
  increases, 70% of CPU or connections holds under load, or restore time
  approaches the recovery target. Size pools and worker replicas together.
- Keep Redis below 65% of usable memory and alerts on eviction, latency,
  and CPU. Verify compatibility before any cluster or persistence mode
  change. Increase ACA replica counts only after worker and signed-consent
  concurrency tests. Use query/index and outbox evidence before adding read
  models or dedicated compute.
- OCR pages dominate the large-scale scenarios. Reprice approved commitment
  tiers after sustained measured page volume. Detect exact duplicate requests
  safely without weakening provenance. Control egress, alerts and log sampling
  without discarding required audit evidence. Obtain external SMS/identity
  quotes for the indicated volume, and include retry/spend controls.
