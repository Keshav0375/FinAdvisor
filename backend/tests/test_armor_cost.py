from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SPIKE_DIR = REPO_ROOT / "spikes" / "e2" / "armor-cost"
FIXTURE = SPIKE_DIR / "fixtures" / "skus.json"
README = SPIKE_DIR / "README.md"
# Kept before any test patches httpx.Client, so the mock catalog can still build a real client.
REAL_CLIENT = httpx.Client

# Public list prices read from https://cloud.google.com/armor/pricing on 2026-11-22; the totals are
# worked out by hand here (policy 5.00, rule 1.00 each, 0.75 per 1M requests), not by the script.
ADR_012_IDLE_RANGE = (Decimal("6"), Decimal("10"))
EXPECTED_BASE = {0: "9.00", 1_000_000: "9.75", 5_000_000: "12.75"}
EXPECTED_WORST = {0: "9.00", 1_000_000: "10.50", 5_000_000: "16.50"}


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("armor_cost", SPIKE_DIR / "armor_cost.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["armor_cost"] = module
    spec.loader.exec_module(module)
    return module


armor = load_module()


def fixture_data() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def fixture_prices() -> Any:
    skus = [armor.parse_sku(raw) for raw in fixture_data()["skus_response"]["skus"]]
    return armor.build_prices(skus)


def test_script_prints_each_sku_id_price_and_currency(capsys: pytest.CaptureFixture[str]) -> None:
    code = armor.main(["--fixture", str(FIXTURE)])

    out = capsys.readouterr().out
    assert code == 0
    for raw in fixture_data()["skus_response"]["skus"]:
        assert raw["skuId"] in out
    assert "SYNTH-ARMOR-POLICY-HR | policy | h | 0.006849315 | USD" in out
    assert "SYNTH-ARMOR-REQ-GLOBAL | request | count | 0.00000075 | USD" in out
    assert "synthetic ids" in out


def test_bogus_service_filter_exits_non_zero_with_fixture(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = armor.main(["--fixture", str(FIXTURE), "--service-filter", "No Such Service"])

    assert code == armor.EXIT_NOT_FOUND
    assert "no billing service matches" in capsys.readouterr().err


def test_empty_service_filter_exits_non_zero() -> None:
    assert armor.main(["--fixture", str(FIXTURE), "--service-filter", " "]) == armor.EXIT_NOT_FOUND


def test_missing_required_sku_kind_exits_non_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = fixture_data()
    data["skus_response"]["skus"] = [
        s for s in data["skus_response"]["skus"] if s["skuId"] != "SYNTH-ARMOR-RULE-HR"
    ]
    path = tmp_path / "no_rule.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    assert armor.main(["--fixture", str(path)]) == armor.EXIT_NOT_FOUND
    assert "required Cloud Armor SKU not found: rule" in capsys.readouterr().err


def test_service_without_skus_exits_non_zero(tmp_path: Path) -> None:
    data = fixture_data()
    data["skus_response"]["skus"] = []
    path = tmp_path / "empty.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    assert armor.main(["--fixture", str(path)]) == armor.EXIT_NOT_FOUND


def test_unreadable_fixture_exits_non_zero(tmp_path: Path) -> None:
    assert armor.main(["--fixture", str(tmp_path / "missing.json")]) == armor.EXIT_NOT_FOUND
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert armor.main(["--fixture", str(bad)]) == armor.EXIT_NOT_FOUND


def test_live_mode_without_key_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(armor.KEY_ENV, raising=False)

    assert armor.main([]) == armor.EXIT_NOT_FOUND
    assert armor.KEY_ENV in capsys.readouterr().err


def test_unsupported_usage_unit_is_rejected() -> None:
    raw = fixture_data()["skus_response"]["skus"][0]
    raw["pricingInfo"][0]["pricingExpression"]["usageUnit"] = "GiBy"
    skus = [armor.parse_sku(r) for r in fixture_data()["skus_response"]["skus"][1:]]
    skus.append(armor.parse_sku(raw))

    with pytest.raises(armor.SkuError, match="usage unit"):
        armor.build_prices(skus)


def test_sku_without_pricing_is_rejected() -> None:
    with pytest.raises(armor.SkuError, match="no readable pricing"):
        armor.parse_sku({"skuId": "SYNTH-X", "description": "Cloud Armor rule"})


def test_mixed_currencies_are_rejected() -> None:
    data = fixture_data()["skus_response"]["skus"]
    data[1]["pricingInfo"][0]["pricingExpression"]["tieredRates"][0]["unitPrice"][
        "currencyCode"
    ] = "EUR"
    skus = [armor.parse_sku(r) for r in data]

    with pytest.raises(armor.SkuError, match="currencies"):
        armor.build_prices(skus)


def test_duplicate_kind_keeps_dearest_price() -> None:
    data = fixture_data()["skus_response"]["skus"]
    extra = json.loads(json.dumps(data[1]))
    extra["skuId"] = "SYNTH-ARMOR-RULE-DEAR"
    extra["pricingInfo"][0]["pricingExpression"]["tieredRates"][0]["unitPrice"]["nanos"] = 2000000
    skus = [armor.parse_sku(r) for r in [*data, extra]]

    assert armor.build_prices(skus).rule_hourly == Decimal("0.002")


def test_money_combines_units_and_nanos() -> None:
    assert armor.money({"units": "2", "nanos": 500_000_000}) == Decimal("2.5")
    assert armor.money({"nanos": 750}) == Decimal("0.00000075")
    assert armor.money({"units": "", "nanos": 0}) == Decimal(0)


def test_lowest_tier_price_is_used() -> None:
    raw = fixture_data()["skus_response"]["skus"][1]
    tiers = raw["pricingInfo"][0]["pricingExpression"]["tieredRates"]
    tiers.insert(
        0,
        {"startUsageAmount": 100, "unitPrice": {"currencyCode": "USD", "units": "0", "nanos": 1}},
    )

    assert armor.parse_sku(raw).unit_price == Decimal("0.001369863")


@pytest.mark.parametrize(
    ("description", "kind"),
    [
        ("Cloud Armor Enterprise Paygo subscription", "enterprise"),
        ("Cloud Armor managed rules request fee", "managed_request"),
        ("Standard requests, regionally scoped security policies", "regional_request"),
        ("Standard requests, globally scoped security policies", "request"),
        ("Standard security policy, per hour", "policy"),
        ("Standard rule, per hour", "rule"),
        ("Something else", "other"),
    ],
)
def test_classify(description: str, kind: str) -> None:
    assert armor.classify(description) == kind


def test_cost_table_matches_hand_computed_totals() -> None:
    prices = fixture_prices()
    rules = armor.RuleSet()

    assert rules.billed_rules == 4
    for requests, total in EXPECTED_BASE.items():
        cost = armor.monthly_cost(prices, rules, requests)
        assert armor.cents(cost.total) == total
    for requests, total in EXPECTED_WORST.items():
        worst = armor.monthly_cost(prices, rules, requests).total_worst_case
        assert worst is not None and armor.cents(worst) == total


def test_worst_case_is_none_without_managed_sku() -> None:
    data = [
        r
        for r in fixture_data()["skus_response"]["skus"]
        if r["skuId"] != "SYNTH-ARMOR-MANAGED-REQ"
    ]
    prices = armor.build_prices([armor.parse_sku(r) for r in data])

    assert armor.monthly_cost(prices, armor.RuleSet(), 1_000_000).total_worst_case is None


def test_idle_budget_verdict_passes_at_zero_requests(capsys: pytest.CaptureFixture[str]) -> None:
    cost = armor.monthly_cost(fixture_prices(), armor.RuleSet(), 0)

    assert cost.total <= Decimal("10")
    assert armor.verdict(cost.total, Decimal("10")) == "PASS"
    armor.main(["--fixture", str(FIXTURE)])
    assert "VERDICT: PASS (0-request total 9.00 vs budget 10.00 USD)" in capsys.readouterr().out


def test_unmerged_waf_rules_fail_the_budget(capsys: pytest.CaptureFixture[str]) -> None:
    args = ["--fixture", str(FIXTURE), "--waf-rules", "5"]

    assert armor.main(args) == 0
    assert "VERDICT: FAIL (0-request total 13.00" in capsys.readouterr().out
    assert armor.main([*args, "--fail-on-over-budget"]) == armor.EXIT_OVER_BUDGET


def test_no_default_rule_flag_lowers_the_idle_cost(capsys: pytest.CaptureFixture[str]) -> None:
    armor.main(["--fixture", str(FIXTURE), "--no-default-rule"])

    assert "VERDICT: PASS (0-request total 8.00" in capsys.readouterr().out


def test_max_rules_inside_budget() -> None:
    prices = fixture_prices()

    assert armor.max_rules_in_budget(prices, Decimal("10")) == 5
    assert armor.max_rules_in_budget(prices, Decimal("4")) == 0


def test_idle_cost_sits_inside_or_below_the_adr_012_estimate() -> None:
    idle = armor.monthly_cost(fixture_prices(), armor.RuleSet(), 0).total

    assert idle <= ADR_012_IDLE_RANGE[1]


def test_request_tiers_parse_and_reject_bad_input() -> None:
    assert armor.parse_tiers("0, 1000") == (0, 1000)
    for bad in ("", "x", "-5"):
        with pytest.raises(armor.SkuError):
            armor.parse_tiers(bad)


def make_catalog_client(
    skus: list[dict[str, Any]], seen: list[httpx.Request], service_name: str = "Cloud Armor"
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        token = request.url.params.get("pageToken", "")
        if path == "/v1/services":
            return httpx.Response(
                200,
                json={"services": [{"serviceId": "S1", "displayName": service_name}]},
            )
        if path == "/v1/services/S1/skus":
            if not token:
                return httpx.Response(200, json={"skus": skus[:2], "nextPageToken": "p2"})
            return httpx.Response(200, json={"skus": skus[2:]})
        return httpx.Response(404)

    return REAL_CLIENT(transport=httpx.MockTransport(handler), headers={"X-Goog-Api-Key": "k"})


def test_live_fetch_pages_and_sends_key_in_header_not_url() -> None:
    seen: list[httpx.Request] = []
    skus = fixture_data()["skus_response"]["skus"]
    with make_catalog_client(skus, seen) as client:
        fetched = armor.fetch_live(client, "Cloud Armor")

    assert [s["skuId"] for s in fetched] == [s["skuId"] for s in skus]
    assert all("key" not in r.url.params for r in seen)
    assert all(r.headers["X-Goog-Api-Key"] == "k" for r in seen)


def test_live_fetch_with_bogus_service_filter_raises() -> None:
    with (
        make_catalog_client([], []) as client,
        pytest.raises(armor.SkuError, match="no billing service"),
    ):
        armor.fetch_live(client, "Bogus")


def test_live_http_error_is_a_sku_error_without_the_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "denied"})

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(armor.SkuError) as info,
    ):
        armor.fetch_live(client, "Cloud Armor")

    assert "googleapis" not in str(info.value)


def test_main_live_path_reads_key_from_env(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    skus = fixture_data()["skus_response"]["skus"]
    seen: list[httpx.Request] = []

    def fake_client(**kwargs: Any) -> httpx.Client:
        client = make_catalog_client(skus, seen)
        client.headers.update(kwargs["headers"])
        return client

    monkeypatch.setenv(armor.KEY_ENV, "env-key")
    monkeypatch.setattr(armor.httpx, "Client", fake_client)

    assert armor.main([]) == 0
    assert "live Cloud Billing Catalog API" in capsys.readouterr().out
    assert seen[0].headers["X-Goog-Api-Key"] == "env-key"


def write_variant(tmp_path: Path, mutate: Callable[[dict[str, Any]], None]) -> Path:
    data = fixture_data()
    mutate(data)
    path = tmp_path / "variant.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def sku_by_id(data: dict[str, Any], sku_id: str) -> dict[str, Any]:
    found: dict[str, Any] = next(s for s in data["skus_response"]["skus"] if s["skuId"] == sku_id)
    return found


def tiers_of(data: dict[str, Any], sku_id: str) -> list[dict[str, Any]]:
    tiers: list[dict[str, Any]] = sku_by_id(data, sku_id)["pricingInfo"][0]["pricingExpression"][
        "tieredRates"
    ]
    return tiers


def run_fixture(path: Path, *extra: str) -> int:
    code: int = armor.main(["--fixture", str(path), *extra])
    return code


# Finding 1: the verdict is about the idle cost, whatever tiers are printed.


def test_verdict_is_based_on_idle_cost_without_a_zero_request_tier(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = ["--fixture", str(FIXTURE), "--request-tiers", "1000000,5000000", "--budget", "5"]

    assert armor.main(args) == 0
    out = capsys.readouterr().out
    assert "VERDICT: FAIL (0-request total 9.00 vs budget 5.00 USD)" in out
    assert "VERDICT: PASS" not in out
    assert armor.main([*args, "--fail-on-over-budget"]) == armor.EXIT_OVER_BUDGET


def test_verdict_passes_without_a_zero_tier_when_idle_is_inside_budget(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert armor.main(["--fixture", str(FIXTURE), "--request-tiers", "5000000"]) == 0

    assert "VERDICT: PASS (0-request total 9.00 vs budget 10.00 USD)" in capsys.readouterr().out


# Finding 2: a SKU with several tier prices cannot be costed from one unit price.


def test_sku_with_a_free_first_tier_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def free_first_tier(data: dict[str, Any]) -> None:
        tiers = tiers_of(data, "SYNTH-ARMOR-RULE-HR")
        tiers[0]["unitPrice"] = {"currencyCode": "USD", "units": "0", "nanos": 0}
        tiers.append(
            {
                "startUsageAmount": 10,
                "unitPrice": {"currencyCode": "USD", "units": "0", "nanos": 1369863},
            }
        )

    code = run_fixture(write_variant(tmp_path, free_first_tier))

    assert code == armor.EXIT_NOT_FOUND
    err = capsys.readouterr().err
    assert "SYNTH-ARMOR-RULE-HR" in err
    assert "2 different tier prices" in err


def test_parse_sku_counts_distinct_price_points_only() -> None:
    data = fixture_data()
    sku = sku_by_id(data, "SYNTH-ARMOR-RULE-HR")
    tiers = tiers_of(data, "SYNTH-ARMOR-RULE-HR")
    tiers.append({**tiers[0], "startUsageAmount": 10})

    assert armor.parse_sku(sku).price_points == 1

    tiers.append({"startUsageAmount": 20, "unitPrice": {"currencyCode": "USD", "nanos": 5}})

    assert armor.parse_sku(sku).price_points == 2


def test_tiered_sku_that_is_not_used_only_warns(tmp_path: Path) -> None:
    def tier_the_enterprise_sku(data: dict[str, Any]) -> None:
        tiers = tiers_of(data, "SYNTH-ARMOR-ENTERPRISE-PAYGO")
        tiers.append(
            {
                "startUsageAmount": 10,
                "unitPrice": {"currencyCode": "USD", "units": "0", "nanos": 1},
            }
        )

    assert run_fixture(write_variant(tmp_path, tier_the_enterprise_sku)) == 0


def test_tiered_managed_sku_is_rejected(tmp_path: Path) -> None:
    def tier_the_managed_sku(data: dict[str, Any]) -> None:
        tiers_of(data, "SYNTH-ARMOR-MANAGED-REQ").append(
            {
                "startUsageAmount": 10,
                "unitPrice": {"currencyCode": "USD", "units": "0", "nanos": 1},
            }
        )

    assert run_fixture(write_variant(tmp_path, tier_the_managed_sku)) == armor.EXIT_NOT_FOUND


# Finding 3: malformed input and bad numbers exit 2 with a message, never a traceback or exit 1.


def test_service_without_service_id_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def drop_service_id(data: dict[str, Any]) -> None:
        del data["services_response"]["services"][1]["serviceId"]

    assert run_fixture(write_variant(tmp_path, drop_service_id)) == armor.EXIT_NOT_FOUND
    assert "has no serviceId" in capsys.readouterr().err


def test_live_service_without_service_id_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"services": [{"displayName": "Cloud Armor"}]})

    with (
        REAL_CLIENT(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(armor.SkuError, match="no serviceId"),
    ):
        armor.fetch_live(client, "Cloud Armor")


@pytest.mark.parametrize(
    "body",
    [["not", "an", "object"], {"services": "oops"}, {"services": None}],
)
def test_live_response_with_the_wrong_shape_raises(body: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with (
        REAL_CLIENT(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(armor.SkuError, match="no usable 'services' list"),
    ):
        armor.fetch_live(client, "Cloud Armor")


@pytest.mark.parametrize("bad_units", ["abc", "1e999999999999", "NaN"])
def test_non_numeric_price_exits_two_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], bad_units: str
) -> None:
    def break_price(data: dict[str, Any]) -> None:
        tiers_of(data, "SYNTH-ARMOR-POLICY-HR")[0]["unitPrice"]["units"] = bad_units

    code = run_fixture(write_variant(tmp_path, break_price))

    assert code == armor.EXIT_NOT_FOUND
    assert "error:" in capsys.readouterr().err


def test_non_integer_nanos_exits_two(tmp_path: Path) -> None:
    def break_nanos(data: dict[str, Any]) -> None:
        tiers_of(data, "SYNTH-ARMOR-POLICY-HR")[0]["unitPrice"]["nanos"] = "many"

    assert run_fixture(write_variant(tmp_path, break_nanos)) == armor.EXIT_NOT_FOUND


def test_sku_without_sku_id_or_not_an_object_exits_two(tmp_path: Path) -> None:
    def drop_sku_id(data: dict[str, Any]) -> None:
        del data["skus_response"]["skus"][0]["skuId"]

    def add_junk_entry(data: dict[str, Any]) -> None:
        data["skus_response"]["skus"].append("junk")

    assert run_fixture(write_variant(tmp_path, drop_sku_id)) == armor.EXIT_NOT_FOUND
    assert run_fixture(write_variant(tmp_path, add_junk_entry)) == armor.EXIT_NOT_FOUND


def test_zero_rule_price_exits_two_not_zero_division(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def free_rules(data: dict[str, Any]) -> None:
        tiers_of(data, "SYNTH-ARMOR-RULE-HR")[0]["unitPrice"]["nanos"] = 0

    code = run_fixture(write_variant(tmp_path, free_rules))

    assert code == armor.EXIT_NOT_FOUND
    captured = capsys.readouterr()
    assert "rule fee is zero" in captured.err
    assert "VERDICT" not in captured.out


def test_zero_hours_exits_two_not_zero_division(capsys: pytest.CaptureFixture[str]) -> None:
    assert armor.main(["--fixture", str(FIXTURE), "--hours", "0"]) == armor.EXIT_NOT_FOUND
    assert "--hours must be greater than 0" in capsys.readouterr().err
    assert armor.main(["--fixture", str(FIXTURE), "--hours", "-3"]) == armor.EXIT_NOT_FOUND


def test_max_rules_in_budget_rejects_a_zero_rule_fee() -> None:
    prices = fixture_prices()
    free = armor.Prices(
        prices.policy_hourly,
        Decimal(0),
        prices.request_each,
        prices.managed_request_each,
        prices.currency,
    )

    with pytest.raises(armor.SkuError, match="rule fee is zero"):
        armor.max_rules_in_budget(free, Decimal("10"))


@pytest.mark.parametrize("budget", ["abc", "NaN", "Infinity"])
def test_bad_budget_is_a_usage_error(budget: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        armor.main(["--fixture", str(FIXTURE), "--budget", budget])

    assert info.value.code == 2
    assert "--budget" in capsys.readouterr().err


def test_negative_budget_or_rule_counts_exit_two() -> None:
    assert armor.main(["--fixture", str(FIXTURE), "--budget", "-1"]) == armor.EXIT_NOT_FOUND
    assert armor.main(["--fixture", str(FIXTURE), "--geo-rules", "-1"]) == armor.EXIT_NOT_FOUND


def test_arithmetic_error_is_exit_two_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*args: Any, **kwargs: Any) -> int:
        raise ZeroDivisionError

    monkeypatch.setattr(armor, "run", boom)

    assert armor.main(["--fixture", str(FIXTURE)]) == armor.EXIT_NOT_FOUND
    assert "ZeroDivisionError" in capsys.readouterr().err


# Finding 4: an ambiguous service match is an error that lists the candidates.


def test_two_services_matching_the_filter_exit_two_and_are_listed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def two_matches(data: dict[str, Any]) -> None:
        services = data["services_response"]["services"]
        services[0] = {"serviceId": "SYNTHETIC-A", "displayName": "Cloud Armor Edge"}
        services[1] = {"serviceId": "SYNTHETIC-B", "displayName": "Cloud Armor Core"}

    code = run_fixture(write_variant(tmp_path, two_matches))

    assert code == armor.EXIT_NOT_FOUND
    err = capsys.readouterr().err
    assert "2 billing services match" in err
    assert "Cloud Armor Edge (SYNTHETIC-A)" in err
    assert "Cloud Armor Core (SYNTHETIC-B)" in err


def test_unique_exact_display_name_wins_over_longer_names() -> None:
    services = [
        {"serviceId": "S-ENT", "displayName": "Cloud Armor Enterprise"},
        {"serviceId": "S-STD", "displayName": "cloud armor"},
    ]

    assert armor.pick_service(services, "Cloud Armor") == "S-STD"
    with pytest.raises(armor.SkuError, match="2 billing services match"):
        armor.pick_service(services, "armor")


def test_pick_service_ignores_entries_without_a_display_name() -> None:
    services: list[Any] = ["junk", {"serviceId": "X"}, {"serviceId": "S1", "displayName": "Armor"}]

    assert armor.pick_service(services, "armor") == "S1"


def test_readme_records_verdict_table_fallback_and_live_command() -> None:
    text = README.read_text(encoding="utf-8")

    assert "synthetic" in text
    assert "https://cloud.google.com/armor/pricing" in text
    assert "2026-11-22" in text
    assert "VERDICT: PASS" in text
    for total in (*EXPECTED_BASE.values(), *EXPECTED_WORST.values()):
        assert total in text
    assert "Fallback" in text
    assert "researcher" in text
    assert "GCP_BILLING_API_KEY" in text
    assert "K1" in text
    assert "E10" in text
    assert "E2-T18" in text
