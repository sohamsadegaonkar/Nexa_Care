# Nexa Care Azure cost and scaling model

Malaysia West (`malaysiawest`) public retail estimates in INR, retrieved
24 November 2026 for synthetic-only Nexa Care staging. The model uses 730 hours
per month and estimates Azure consumption before tax, negotiated discounts,
credits and support. AWS Mumbai, Supabase, Twilio, devices and domain registration
are external costs. Verify the student subscription's eligibility, credit balance,
quotas and effective offer before approving spending. [Azure Retail Prices API](https://learn.microsoft.com/rest/api/cost-management/retail-prices/azure-retail-prices)

## Development and functional staging

| Environment | Assumptions | Estimated Azure spend |
| --- | --- | ---: |
| Minimal development | Run the real app with disposable local Postgres/Redis and synthetic fixtures. Optional cloud adapter benchmark requires its own isolated account, endpoint and budget. | ₹0 local Azure consumption |
| Staging foundation | PostgreSQL 16 B1ms with 32 GB and 14-day same-region PITR, one Redis B0 node, ACR Basic, three standard private endpoints and four private DNS zones. | ₹6,015.01 fixed/month |
| Functional staging, controlled sessions | Foundation allocated all month. Run the API with scanner and the web app for 80 hours at 20% active time. | ₹7,720.90/month |
| Functional staging, always on | Same foundation and monthly volumes, with one API with scanner replica and one web replica allocated for 730 hours. | ₹14,829.03–₹19,332.76/month at 5–35% active time. ₹16,330.27 at 15%. |

The fixed foundation comprises PostgreSQL compute ₹1,626.70, PostgreSQL disk
₹378.48, Redis ₹1,251.31, ACR ₹482.56, private endpoints ₹2,085.51 and private
DNS zones ₹190.46. Subtotals use unrounded meter values. The model assumes
PostgreSQL backups stay within the provisioned storage allowance. [Azure PostgreSQL backup pricing and behavior](https://learn.microsoft.com/azure/postgresql/flexible-server/concepts-backup-restore)

The API and scanner reserve 2 vCPU and 4 GiB. The web app reserves
0.5 vCPU and 1 GiB. Compute equals allocated seconds multiplied by the
weighted active and idle CPU rates and the memory rate. For example, the
controlled session compute is ₹1,152.00. Measure the actual active time because
worker or scanner activity can make replicas bill at the active rate. Stop
the app replicas between approved sessions after audit queues drain. The
stateful services and other foundation components continue to bill. [Container Apps billing](https://learn.microsoft.com/azure/container-apps/billing)

Staging assumes 20 synthetic identities, 100 OTP sends, 40 documents of
2.5 MiB each, 120 Textract pages, 10 peak concurrent requests and 10,000 HTTP
requests. The Azure usage allowance adds ₹553.88 per month for 2 GB of
analytics ingestion at the paid rate, 5 GB of Hot LRS storage with 1,000 writes
and 1,000 reads, 10 GB in each direction through Private Link, 100,000 private
DNS queries, 10,000 Key Vault operations, one active one-hour migration job,
HTTP requests and 10 GB Internet egress within the published first 100 GB
tier. The estimate assumes no ACA or Log Analytics free grants are available.
The staging OCR route uses the isolated AWS Mumbai services. The Malaysia West
catalog returned no Consumption meter for Document Intelligence or Form
Recognizer. Price any future Azure-native OCR benchmark separately.

## Capacity cost sensitivity

These hypothetical Malaysia West subtotals allow comparison across sizes.
They use one WAF v2 gateway, two warm API with scanner replicas and two warm
web replicas (5 vCPU and 10 GiB), multiple database nodes and a two-node Redis
budget. Azure OCR is unpriced, so the subtotals exclude extraction and are
incomplete for a production budget. The Malaysia West staging approval
scope is synthetic testing.

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

| Registered patients | DB size × nodes / GB per node | Redis / log GB / excess backup GB | Azure fixed | Azure variable | Subtotal before OCR |
| ---: | --- | --- | ---: | ---: | ---: |
| 500 | D2ds v5 × 2 / 32 | B1 HA / 6 / 0 | ₹74,116 | ₹24,656 | ₹98,772 |
| 1,000 | D2ds v5 × 2 / 64 | B1 HA / 8 / 0 | ₹74,873 | ₹26,980 | ₹1,01,852 |
| 10,000 | D4ds v5 × 2 / 128 | B5 HA / 20 / 60 | ₹1,26,064 | ₹44,636 | ₹1,70,699 |
| 100,000 | D8ds v5 × 2 / 512 | B10 HA / 70 / 256 | ₹2,20,987 | ₹1,45,855 | ₹3,66,841 |
| 1,000,000 | D16ds v5 × 3 / 4,096 with one read replica | B20 HA / 400 / 2,048 | ₹6,47,141 | ₹8,99,249 | ₹15,46,390 |

Fixed costs include the gateway base charge, ACR Premium and five endpoints
and private DNS zones. In table order, ACA active fractions are 10%, 15%,
28%, 55% and 75%, plus 0, 20, 140, 1,700 and 18,000 active vCPU-hours with
2 GiB per extra vCPU. The corresponding monthly average WAF capacity units
are 5, 5, 10, 25 and 100. Variable costs also include one write and two
reads per new document, one private DNS query per HTTP request, 100,000
Key Vault operations, and Private Link GB in each direction equal to the
Internet egress GB assumption. These usage assumptions require measurement
for a production quote. [Application Gateway usage units](https://learn.microsoft.com/azure/application-gateway/understanding-pricing)

## Model inputs and source rates

| Meter | INR retail rate |
| --- | --- |
| ACA Consumption | ₹0.002666 per active vCPU-second, ₹0.000381 per idle vCPU-second, ₹0.000381 per GiB-second and ₹44.567055 per million requests. |
| PostgreSQL | B1ms ₹2.228353/hour. D2ds/D4ds/D8ds/D16ds v5 ₹20.912234/41.824467/83.648934/167.297868 per node-hour, provisioned storage ₹11.827411 per GB-month and excess LRS backup ₹9.046731 per GB-month. |
| Managed Redis | Balanced B0/B1/B5/B10/B20 ₹1.714118/3.428235/16.541234/33.510997/66.850583 per cache-hour. The staging model counts one node and the HA estimate counts two. Confirm the HA pricing basis with the subscription quote. |
| ACR and Key Vault | ACR Basic ₹15.865110/day and Premium ₹158.708235/day. Key Vault Standard ₹2.856863 per 10,000 operations. |
| Blob | Hot LRS ₹1.714118 per GB-month at these volumes, ₹4.285294 per 10,000 writes and ₹0.342824 per 10,000 reads. |
| Private Link and DNS | Global standard endpoint ₹0.952288/hour and first-tier data processed ₹0.952288/GB in each direction. Global private DNS ₹47.614375 per zone-month at these zone counts and ₹38.091500 per million queries. |
| Azure Monitor and Internet egress | Log Analytics ₹256.260566/GB at the paid ingestion tier. Microsoft-network Internet egress uses the catalog tiers, including ₹0 through 100 GB, ₹11.427450/GB above 100 GB and ₹8.094444/GB above 10,335 GB within the modeled volumes. |
| WAF v2 | ₹40.110350/hour fixed and ₹1.371294 per capacity-unit hour. WAF applies only to the capacity examples. |

To reproduce the lookup, query `https://prices.azure.com/api/retail/prices`
with `api-version=2023-01-01-preview`, `currencyCode='INR'` and
`$filter=armRegionName eq 'malaysiawest'` plus the service or product filter.
Follow `NextPageLink` and select Consumption rows at the relevant tier.
The Private Link and DNS entries use global meters from the same API.

| Source meter | Meter ID |
| --- | --- |
| Malaysia West B1ms | `d0cb18fd-775c-5f2f-84a2-2eb6e23142f4` |
| Malaysia West Redis B0 | `42db9844-7427-549a-a5d9-746a1e1d1e27` |
| Malaysia West ACA active vCPU | `3bcf4424-48a0-5905-bd13-8b047f92911c` |
| Malaysia West ACA idle vCPU | `4493709d-ee9b-5bd2-8710-a3248537f3c3` |
| Malaysia West Log Analytics ingestion | `53de4a8a-4768-5f74-a1f8-6ab32e21ba03` |
| Global standard Private Endpoint | `e6ab7238-e433-4fe0-a2b2-2b2564df2cdb` |
| Global private DNS zones | `0b608a26-f611-4232-8192-ce81b6b57194` |

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
- Obtain a separate OCR quote before budgeting high page volumes. Price
  approved commitment tiers after sustained measured page volume. Detect exact
  duplicate requests
  safely without weakening provenance. Control egress, alerts and log sampling
  without discarding required audit evidence. Obtain external SMS/identity
  quotes for the indicated volume, and include retry/spend controls.
