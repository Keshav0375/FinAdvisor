from __future__ import annotations

import importlib.util
import json
import sys
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
