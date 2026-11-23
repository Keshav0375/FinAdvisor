# E2-T10 spike: Cloud Armor cost

Status: demo-mode result (2026-11-22). Prices come from the public pricing page, not from the Billing
Catalog API. The live confirmation waits for K1 (a GCP API key); the exact command is below.

## Summary

| Item | Arch said | Result |
|---|---|---|
| Cloud Armor cost | "~$0.75/1M requests + policy fee: exact cost UNVERIFIED" (04 4.2) | Standard tier: $5.00/policy/month, $1.00/rule/month, $0.75 per 1M requests |
| Idle figure | "~$6-10/month" UNVERIFIED (ADR-012, overview 10) | $9.00/month at 0 requests with the proposed 4 billed rules |
| Verdict | pass if the 0-request figure is <= $10/month | PASS, $1.00 of headroom (see Verdict) |

## Sources and what is verified

- Public price page: https://cloud.google.com/armor/pricing, read on 2026-11-22. Values taken from it:
  - Cloud Armor Standard (pay as you go), global security policies: policy $0.006849315 per hour,
    rule $0.001369863 per hour, requests $0.75 per 1,000,000. Regional policies: requests $0.60 per
    1,000,000 (not used, the external ALB is global).
  - Standard has no data-processing fee. Managed rules (a newer add-on, billed from GA) add $0.75 per
    1,000,000 requests evaluated against a managed rule, metered at most once per request.
  - Cloud Armor Enterprise Paygo is $0.273972603 per hour (about $200/month). It is not used. The page
    warns that creating a hierarchical security policy in a project that is not enrolled auto-enrolls it
    in Enterprise Paygo, so E2-T18 must not create one.
- Hours per month: 730 (knob `--hours`). $0.006849315 x 730 = $5.00 and $0.001369863 x 730 = $1.00.
- Rule expression limit: "up to five subexpressions" per advanced match condition
  (https://cloud.google.com/armor/docs/rules-language-reference). The quotas page lists 10; the lower
  figure is used.
- Fixture: `fixtures/skus.json` has the documented Billing Catalog response shape
  (services.list and services.skus.list). Every service id and SKU id in it is **synthetic**
  (`SYNTH-...`), the descriptions start with `[synthetic]`, and the unit prices are the public-page
  values above. Nothing in it is API-verified. Per the Billing Catalog docs `unitPrice` is per one
  `usageUnit`, so $0.75 per 1M requests is `units 0, nanos 750` per request.
- Not verified until K1: the real SKU ids, their descriptions (the classifier matches on words like
  "policy", "rule", "request", "managed", "regional", "enterprise"), and whether preconfigured WAF rules
  count as "managed rules" for the extra request fee. The worst-case column covers that doubt.

## Run

Offline (demo, synthetic fixture, no key):

    python spikes/e2/armor-cost/armor_cost.py --fixture spikes/e2/armor-cost/fixtures/skus.json

Live, for K1 (enable `cloudbilling.googleapis.com` on a project, create an API key restricted to that
API, then):

    GCP_BILLING_API_KEY=<key> python spikes/e2/armor-cost/armor_cost.py

Run it from the repo root with the backend dev environment (`httpx`, `structlog`). The key is sent in the
`X-Goog-Api-Key` header, never in the URL, and is never printed. Exit codes: 0 done, 1 over budget (only with
`--fail-on-over-budget`), 2 service or a required SKU not found, bad input or API failure. A bogus
`--service-filter` exits 2. Knobs: `--service-filter`, `--key-env`, `--request-tiers`, `--budget`,
`--hours`, `--waf-rules`, `--geo-rules`, `--throttle-rules`, `--no-default-rule`.
After the live run, replace the fixture with the recorded response and edit the Sources section above.

## Rule count proposed for E2-T18

Four billed rules, one policy attached to the ALB backend services:

| Priority | Rule | Billed as |
|---|---|---|
| 1000 | Geo deny (origin.region_code outside the allowed set, D12) | 1 rule |
| 2000 | Preconfigured WAF deny: one rule whose expression joins up to 5 signature sets (SQLi, XSS, LFI, RCE, scanner detection; E2-T18 confirms the stable set names) | 1 rule |
| 3000 | Coarse per-IP throttle (Kong keeps the fine limits) | 1 rule |
| default | Allow | 1 rule (billed here on purpose; whether Google bills the default rule is unverified) |

Joining the WAF signature sets into one expression is what keeps the policy inside the budget. Five
separate WAF rules would bill 8 rules.

## Monthly cost (planned rule set, Standard tier, USD)

| Requests/month | Policy | 4 rules | Request fee | Total | Total if the managed-rule fee also applies to every request |
|---|---|---|---|---|---|
| 0 | 5.00 | 4.00 | 0.00 | 9.00 | 9.00 |
| 1,000,000 | 5.00 | 4.00 | 0.75 | 9.75 | 10.50 |
| 5,000,000 | 5.00 | 4.00 | 3.75 | 12.75 | 16.50 |

Sensitivity (script flags): 5 unmerged WAF rules gives 8 billed rules and $13.00 at 0 requests (FAIL); not
billing the default rule gives $8.00; the most billed rules inside $10 is 5.

Open arch point for the arch batch: R31 says "no geo allow-list" while 04 4.2 and ADR-012 list "geo rules".
The geo deny rule is kept here. The totals for both readings are $9.00 (geo rule kept, `--geo-rules 1`)
and $8.00 (no geo rule, `--geo-rules 0`).

The script prints: `VERDICT: PASS (0-request total 9.00 vs budget 10.00 USD)`. The verdict always uses the
0-request cost, even when `--request-tiers` leaves 0 out. A SKU priced in several tiers, an ambiguous
service match or a malformed catalog response stops the run with exit 2 and a message.

## Verdict

PASS. The 0-request figure is $9.00/month, at or under the $10/month idle budget, and inside the
ADR-012 range of ~$6-10. The margin is thin: one more billed rule leaves $0 of headroom, a sixth fails. The
request charge is small at the planned traffic (about $0.75 to $3.75 a month). The idle budget in ADR-012
covers only this idle figure; the 5M-request row is for information.

The verdict is provisional until the live run: it rests on public-page prices and synthetic SKU ids.

## Fallback

1. If E2-T18 needs another rule or the live run shows higher prices, merge the geo deny into the WAF
   rule's expression (4 signature sets plus the geo test is 5 subexpressions). That leaves 3 billed rules
   and $8.00 at 0 requests.
2. If the idle budget still fails, escalate to the researcher (ADR-012 trigger). This ticket does not
   remove Cloud Armor.

## Later checks

- K1: run the live command, commit the recorded response, drop the "synthetic" labels, re-run the table.
- E10: confirm the real charge against the billing export during the cost month, including whether the
  default rule and preconfigured WAF evaluation are billed as assumed here.
- Out of scope: Confluent cost; the Terraform for the policy (E2-T18).
