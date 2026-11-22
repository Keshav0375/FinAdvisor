"""E2-T10 spike: Cloud Armor SKU prices and the monthly cost of the planned policy.

Run by hand, never in the PR gate. Offline (demo mode, synthetic fixture, no key):

    python spikes/e2/armor-cost/armor_cost.py --fixture spikes/e2/armor-cost/fixtures/skus.json

Live (needs the Cloud Billing Catalog API enabled and an API key, ticket K1):

    GCP_BILLING_API_KEY=<key> python spikes/e2/armor-cost/armor_cost.py

Prints every Cloud Armor SKU (id, unit price, currency, usage unit), then the monthly cost of the
planned rule set at 0, 1M and 5M requests and a PASS/FAIL verdict against the idle budget.
Exit codes: 0 done, 1 over budget (only with --fail-on-over-budget), 2 service or a required SKU
not found, bad input or the API failed. The write-up is in spikes/e2/armor-cost/README.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

# Spike-local knobs, all overridable on the command line. They sit here, in one place, because the
# script is run by hand; nothing below hardcodes a price, a rule count or a budget.
BASE_URL = "https://cloudbilling.googleapis.com/v1"
KEY_ENV = "GCP_BILLING_API_KEY"
TIMEOUT_S = 30.0
PAGE_SIZE = 5000
DEFAULT_SERVICE_FILTER = "Cloud Armor"
HOURS_PER_MONTH = 730
REQUEST_TIERS: tuple[int, ...] = (0, 1_000_000, 5_000_000)
BUDGET_USD = Decimal("10")
EXIT_OK = 0
EXIT_OVER_BUDGET = 1
EXIT_NOT_FOUND = 2

KIND_POLICY = "policy"
KIND_RULE = "rule"
KIND_REQUEST = "request"
KIND_MANAGED = "managed_request"
KIND_REGIONAL = "regional_request"
KIND_ENTERPRISE = "enterprise"
KIND_OTHER = "other"
REQUIRED_KINDS = (KIND_POLICY, KIND_RULE, KIND_REQUEST)
HOURLY_KINDS = (KIND_POLICY, KIND_RULE)
COUNT_KINDS = (KIND_REQUEST, KIND_MANAGED)
UNIT_HOUR = "h"
UNIT_COUNT = "count"
NANOS = Decimal(1_000_000_000)


class SkuError(Exception):
    """The catalog data cannot be priced (service or SKU missing, unit not understood)."""


@dataclass(frozen=True)
class Sku:
    sku_id: str
    description: str
    kind: str
    usage_unit: str
    unit_price: Decimal
    currency: str


@dataclass(frozen=True)
class RuleSet:
    """Planned policy. WAF signature sets share one rule (up to 5 subexpressions per expression)."""

    waf_rules: int = 1
    geo_rules: int = 1
    throttle_rules: int = 1
    count_default_rule: bool = True

    @property
    def billed_rules(self) -> int:
        return self.waf_rules + self.geo_rules + self.throttle_rules + int(self.count_default_rule)


@dataclass(frozen=True)
class Prices:
    policy_hourly: Decimal
    rule_hourly: Decimal
    request_each: Decimal
    managed_request_each: Decimal | None
    currency: str


@dataclass(frozen=True)
class Cost:
    requests: int
    policy: Decimal
    rules: Decimal
    request_fee: Decimal
    managed_fee: Decimal | None

    @property
    def total(self) -> Decimal:
        return self.policy + self.rules + self.request_fee

    @property
    def total_worst_case(self) -> Decimal | None:
        if self.managed_fee is None:
            return None
        return self.total + self.managed_fee


def classify(description: str) -> str:
    """Map a SKU description to the fee it represents. Order matters: Enterprise and managed
    first, regional before global requests, requests before policy (their text names policies)."""
    text = description.lower()
    if "enterprise" in text:
        return KIND_ENTERPRISE
    if "managed" in text and "request" in text:
        return KIND_MANAGED
    if "request" in text:
        return KIND_REGIONAL if "regional" in text else KIND_REQUEST
    if "polic" in text:
        return KIND_POLICY
    if "rule" in text:
        return KIND_RULE
    return KIND_OTHER


def money(value: Any) -> Decimal:
    """Billing Catalog Money (units as a string, nanos as an int) to Decimal."""
    return (
        Decimal(str(value.get("units", "0") or "0")) + Decimal(int(value.get("nanos", 0))) / NANOS
    )


def parse_sku(raw: dict[str, Any]) -> Sku:
    sku_id = str(raw["skuId"])
    try:
        expression = raw["pricingInfo"][0]["pricingExpression"]
        tiers = expression["tieredRates"]
        first = min(tiers, key=lambda tier: Decimal(str(tier.get("startUsageAmount", 0))))
        price = money(first["unitPrice"])
        currency = str(first["unitPrice"]["currencyCode"])
        unit = str(expression["usageUnit"])
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise SkuError(f"SKU {sku_id} has no readable pricing") from exc
    description = str(raw.get("description", ""))
    return Sku(sku_id, description, classify(description), unit, price, currency)


def pick_service(services: Sequence[dict[str, Any]], service_filter: str) -> str:
    wanted = service_filter.strip().lower()
    matches = [s for s in services if wanted and wanted in str(s.get("displayName", "")).lower()]
    if not matches:
        raise SkuError(f"no billing service matches {service_filter!r}")
    return str(matches[0]["serviceId"])


def build_prices(skus: Sequence[Sku]) -> Prices:
    """Pick one SKU per fee. With several of a kind the dearest wins (the conservative reading)."""
    chosen: dict[str, Sku] = {}
    for sku in skus:
        current = chosen.get(sku.kind)
        if current is not None:
            log.warning(
                "duplicate_sku_kind", kind=sku.kind, kept_max_of=[current.sku_id, sku.sku_id]
            )
        if current is None or sku.unit_price > current.unit_price:
            chosen[sku.kind] = sku
    missing = [kind for kind in REQUIRED_KINDS if kind not in chosen]
    if missing:
        raise SkuError(f"required Cloud Armor SKU not found: {', '.join(missing)}")
    for kind in (*HOURLY_KINDS, *COUNT_KINDS):
        picked = chosen.get(kind)
        want = UNIT_HOUR if kind in HOURLY_KINDS else UNIT_COUNT
        if picked is not None and picked.usage_unit != want:
            raise SkuError(
                f"SKU {picked.sku_id} ({kind}) has usage unit {picked.usage_unit!r}, not {want!r}"
            )
    currencies = {chosen[kind].currency for kind in REQUIRED_KINDS}
    if len(currencies) != 1:
        raise SkuError(f"SKUs are priced in different currencies: {sorted(currencies)}")
    managed = chosen.get(KIND_MANAGED)
    return Prices(
        policy_hourly=chosen[KIND_POLICY].unit_price,
        rule_hourly=chosen[KIND_RULE].unit_price,
        request_each=chosen[KIND_REQUEST].unit_price,
        managed_request_each=managed.unit_price if managed else None,
        currency=currencies.pop(),
    )


def monthly_cost(
    prices: Prices, rules: RuleSet, requests: int, hours: int = HOURS_PER_MONTH
) -> Cost:
    managed = None
    if prices.managed_request_each is not None:
        managed = prices.managed_request_each * requests
    return Cost(
        requests=requests,
        policy=prices.policy_hourly * hours,
        rules=prices.rule_hourly * hours * rules.billed_rules,
        request_fee=prices.request_each * requests,
        managed_fee=managed,
    )


def max_rules_in_budget(prices: Prices, budget: Decimal, hours: int = HOURS_PER_MONTH) -> int:
    """Most billed rules that keep the 0-request cost at or under the budget."""
    room = budget - prices.policy_hourly * hours
    if room < 0:
        return 0
    return int(room // (prices.rule_hourly * hours))


def verdict(idle_total: Decimal, budget: Decimal) -> str:
    return "PASS" if idle_total <= budget else "FAIL"


def cents(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):.2f}"


def parse_tiers(text: str) -> tuple[int, ...]:
    try:
        tiers = tuple(int(part) for part in text.split(",") if part.strip())
    except ValueError as exc:
        raise SkuError(f"bad --request-tiers {text!r}") from exc
    if not tiers or any(t < 0 for t in tiers):
        raise SkuError(f"bad --request-tiers {text!r}")
    return tiers


def load_fixture(path: Path, service_filter: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        services = data["services_response"]["services"]
        skus: list[dict[str, Any]] = data["skus_response"]["skus"]
    except (OSError, ValueError, KeyError) as exc:
        raise SkuError(f"cannot read fixture {path}") from exc
    pick_service(services, service_filter)
    return skus


def fetch_pages(client: httpx.Client, url: str, field: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    token = ""
    while True:
        params = {"pageSize": str(PAGE_SIZE)}
        if token:
            params["pageToken"] = token
        try:
            response = client.get(url, params=params)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # The key travels in a header, so the URL in this error never carries it.
            raise SkuError(f"billing catalog request failed: {type(exc).__name__}") from exc
        items.extend(body.get(field, []))
        token = str(body.get("nextPageToken", ""))
        if not token:
            return items


def fetch_live(client: httpx.Client, service_filter: str) -> list[dict[str, Any]]:
    services = fetch_pages(client, f"{BASE_URL}/services", "services")
    service_id = pick_service(services, service_filter)
    return fetch_pages(client, f"{BASE_URL}/services/{service_id}/skus", "skus")


def render(
    skus: Sequence[Sku],
    prices: Prices,
    rules: RuleSet,
    tiers: Sequence[int],
    budget: Decimal,
    hours: int,
    source: str,
) -> tuple[str, str]:
    lines = [
        f"source: {source}",
        "",
        "SKU id | kind | usage unit | unit price | currency | description",
    ]
    for sku in skus:
        lines.append(
            f"{sku.sku_id} | {sku.kind} | {sku.usage_unit} | {sku.unit_price:f} | {sku.currency}"
            f" | {sku.description}"
        )
    lines += [
        "",
        f"rule set: waf={rules.waf_rules} geo={rules.geo_rules} throttle={rules.throttle_rules}"
        f" default_counted={rules.count_default_rule} -> {rules.billed_rules} billed rules; "
        f"{hours} hours per month",
        "",
        f"requests/month | policy | rules | request fee | total ({prices.currency}) "
        "| total if managed-rule fee applies",
    ]
    idle_total = Decimal(0)
    for requests in tiers:
        cost = monthly_cost(prices, rules, requests, hours)
        if requests == 0:
            idle_total = cost.total
        worst = cost.total_worst_case
        lines.append(
            f"{requests:,} | {cents(cost.policy)} | {cents(cost.rules)} | {cents(cost.request_fee)}"
            f" | {cents(cost.total)} | {cents(worst) if worst is not None else 'n/a'}"
        )
    outcome = verdict(idle_total, budget)
    lines += [
        "",
        f"max billed rules inside the budget: {max_rules_in_budget(prices, budget, hours)}",
        f"VERDICT: {outcome} (0-request total {cents(idle_total)} vs budget {cents(budget)}"
        f" {prices.currency})",
    ]
    return "\n".join(lines) + "\n", outcome


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cloud Armor SKU prices and monthly cost.")
    parser.add_argument("--service-filter", default=DEFAULT_SERVICE_FILTER)
    parser.add_argument("--fixture", type=Path, help="offline: read a recorded catalog response")
    parser.add_argument("--key-env", default=KEY_ENV, help="env var that holds the API key")
    parser.add_argument("--request-tiers", default=",".join(str(t) for t in REQUEST_TIERS))
    parser.add_argument("--budget", type=Decimal, default=BUDGET_USD)
    parser.add_argument("--hours", type=int, default=HOURS_PER_MONTH)
    parser.add_argument("--waf-rules", type=int, default=RuleSet.waf_rules)
    parser.add_argument("--geo-rules", type=int, default=RuleSet.geo_rules)
    parser.add_argument("--throttle-rules", type=int, default=RuleSet.throttle_rules)
    parser.add_argument(
        "--no-default-rule", action="store_true", help="do not bill the default rule"
    )
    parser.add_argument("--fail-on-over-budget", action="store_true")
    return parser


def collect(args: argparse.Namespace) -> tuple[list[dict[str, Any]], str]:
    if args.fixture is not None:
        return load_fixture(
            args.fixture, args.service_filter
        ), f"fixture {args.fixture} (synthetic ids)"
    key = os.environ.get(args.key_env, "")
    if not key:
        raise SkuError(f"no --fixture and no API key in ${args.key_env}")
    with httpx.Client(headers={"X-Goog-Api-Key": key}, timeout=TIMEOUT_S) as client:
        return fetch_live(client, args.service_filter), "live Cloud Billing Catalog API"


def run(args: argparse.Namespace, raw_skus: Sequence[dict[str, Any]], source: str) -> int:
    skus = [parse_sku(raw) for raw in raw_skus]
    if not skus:
        raise SkuError("the service has no SKUs")
    prices = build_prices(skus)
    rules = RuleSet(args.waf_rules, args.geo_rules, args.throttle_rules, not args.no_default_rule)
    tiers = parse_tiers(args.request_tiers)
    text, outcome = render(skus, prices, rules, tiers, args.budget, args.hours, source)
    sys.stdout.write(text)
    if outcome == "FAIL" and args.fail_on_over_budget:
        return EXIT_OVER_BUDGET
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        raw_skus, source = collect(args)
        return run(args, raw_skus, source)
    except SkuError as exc:
        log.error("armor_cost_failed", reason=str(exc))
        sys.stderr.write(f"error: {exc}\n")
        return EXIT_NOT_FOUND


if __name__ == "__main__":
    sys.exit(main())
