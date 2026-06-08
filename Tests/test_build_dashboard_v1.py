from __future__ import annotations

import json
from pathlib import Path

from scripts.research.build_dashboard_v1 import DashboardV1Builder


def _write_strategy_registry(root: Path) -> None:
    path = root / "config" / "research" / "strategy_registry.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "caerus_strategy_registry_v1",
                "strategies": [
                    {
                        "strategy_id": "caerus_polaris",
                        "display_name": "Caerus Polaris",
                        "short_name": "polaris",
                        "strategy_type": "security_selection",
                        "family": "core_momentum",
                        "status": "paper",
                        "role": "baseline",
                        "eligible_for_shadow": True,
                        "eligible_for_promotion": False,
                        "benchmark": "SPY",
                        "execution_impact": "NON_EXECUTIONAL",
                        "display_order": 10,
                        "shadow_tracking": {"enabled": True, "source_variant": "baseline"},
                    },
                    {
                        "strategy_id": "caerus_orion",
                        "display_name": "Caerus Orion",
                        "short_name": "orion",
                        "strategy_type": "security_selection",
                        "family": "core_momentum",
                        "status": "shadow",
                        "role": "challenger",
                        "eligible_for_shadow": True,
                        "eligible_for_promotion": True,
                        "benchmark": "SPY",
                        "execution_impact": "NON_EXECUTIONAL",
                        "display_order": 20,
                        "shadow_tracking": {"enabled": True, "source_variant": "orion"},
                    },
                    {
                        "strategy_id": "caerus_lyra",
                        "display_name": "Caerus Lyra",
                        "short_name": "lyra",
                        "strategy_type": "security_selection",
                        "family": "core_momentum",
                        "status": "shadow",
                        "role": "challenger",
                        "eligible_for_shadow": True,
                        "eligible_for_promotion": True,
                        "benchmark": "SPY",
                        "execution_impact": "NON_EXECUTIONAL",
                        "display_order": 30,
                        "shadow_tracking": {"enabled": True, "source_variant": "lyra"},
                    },
                    {
                        "strategy_id": "caerus_phoenix",
                        "display_name": "Caerus Phoenix",
                        "short_name": "phoenix",
                        "strategy_type": "security_selection",
                        "family": "crisis_reversal",
                        "status": "shadow",
                        "role": "challenger",
                        "eligible_for_shadow": True,
                        "eligible_for_promotion": False,
                        "benchmark": "SPY",
                        "execution_impact": "NON_EXECUTIONAL",
                        "display_order": 40,
                        "shadow_tracking": {"enabled": True, "source_variant": "phoenix"},
                    },
                    {
                        "strategy_id": "caerus_argo",
                        "display_name": "Caerus Argo",
                        "short_name": "argo",
                        "strategy_type": "overlay",
                        "family": "regime_overlay",
                        "status": "research",
                        "role": "overlay",
                        "eligible_for_shadow": False,
                        "eligible_for_promotion": False,
                        "benchmark": None,
                        "execution_impact": "NON_EXECUTIONAL",
                        "display_order": 50,
                        "shadow_tracking": {"enabled": False, "source_variant": None},
                    },
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def test_build_dashboard_v1_happy_path(tmp_path: Path) -> None:
    broker_dir = tmp_path / "outputs" / "broker"
    snapshot_dir = tmp_path / "outputs" / "broker_snapshot"
    perf_dir = tmp_path / "outputs" / "perf"
    broker_dir.mkdir(parents=True)
    snapshot_dir.mkdir(parents=True)
    perf_dir.mkdir(parents=True)

    report_date = "2026-04-21"
    captured_at = "2026-04-21T14:05:00+00:00"

    (broker_dir / "broker_snapshot_latest.json").write_text(
        json.dumps(
            {
                "trade_date": report_date,
                "captured_at": captured_at,
                "trust_level": "authoritative",
                "cash": "2500.00",
                "equity": "10000.00",
                "buying_power": "20000.00",
                "last_equity": "9900.00",
                "market_value": 7500.00,
                "account": {
                    "cash": "2500.00",
                    "equity": "10000.00",
                    "buying_power": "20000.00",
                    "last_equity": "9900.00",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (broker_dir / "posttrade_positions.json").write_text(
        json.dumps(
            {
                "trade_date": report_date,
                "captured_at": captured_at,
                "positions_count": 2,
                "positions": [
                    {
                        "symbol": "AAPL",
                        "side": "long",
                        "qty": "10",
                        "market_value": "4000",
                        "current_price": "400",
                        "cost_basis": "3900",
                        "unrealized_pl": "100",
                        "unrealized_plpc": "0.0256",
                    },
                    {
                        "symbol": "MSFT",
                        "side": "long",
                        "qty": "5",
                        "market_value": "3500",
                        "current_price": "700",
                        "cost_basis": "3400",
                        "unrealized_pl": "100",
                        "unrealized_plpc": "0.0294",
                    },
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (snapshot_dir / f"broker_snapshot_{report_date}.json").write_text(
        json.dumps(
            {
                "meta": {
                    "generated_at": captured_at,
                    "report_date": report_date,
                },
                "fills_report_date": [
                    {
                        "id": "fill-1",
                        "symbol": "AAPL",
                        "side": "buy",
                        "qty": "10",
                        "price": "400",
                        "order_id": "order-1",
                        "transaction_time": "2026-04-21T09:35:00-04:00",
                    },
                    {
                        "id": "fill-2",
                        "symbol": "MSFT",
                        "side": "sell",
                        "qty": "2",
                        "price": "700",
                        "order_id": "order-2",
                        "transaction_time": "2026-04-21T09:36:00-04:00",
                    },
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (perf_dir / "live_overlay_nav_series.csv").write_text(
        "date,equity,cash,gross_exposure,net_exposure,return_1d,turnover_dollars,turnover_pct,turnover\n"
        "2026-04-20,9900,2600,0.73,0.73,0.01,0,0,0\n"
        "2026-04-21,10000,2500,0.75,0.75,0.0101010101,0,0,0\n",
        encoding="utf-8",
    )
    (perf_dir / "live_overlay_benchmark_close_history.csv").write_text(
        "date,spy_close,spy_return\n"
        "2026-04-20,500,\n"
        "2026-04-21,505,0.01\n",
        encoding="utf-8",
    )

    payload = DashboardV1Builder(tmp_path, report_date=report_date).build()

    assert payload["status"]["level"] in {"ok", "warning"}
    assert payload["schema_version"] == "dashboard-v2-prototype"
    assert payload["sections"]["nav"]["equity"] == 10000.0
    assert payload["sections"]["positions"]["summary"]["positions_count"] == 2
    assert payload["sections"]["trades_today"]["summary"]["fills_count"] == 2
    assert payload["sections"]["performance_history"]["summary"]["latest_nav"] == 10000.0
    assert "shadow_command_center" in payload["sections"]
    assert "system_health_console" in payload["sections"]
    assert "regime_market_state" in payload["sections"]
    assert "daily_decision_intelligence" in payload["sections"]
    assert "live_readiness" in payload["sections"]
    assert payload["terminal"]["headline"]["nav"] == 10000.0
    assert payload["terminal"]["benchmark"]["rolling_5d_return"] is None
    assert payload["terminal"]["leaders"]["winners"][0]["ticker"] == "AAPL"
    assert payload["sections"]["daily_decision_intelligence"]["summary"]["buy_count"] == 1
    assert payload["sections"]["live_readiness"]["summary"]["deployment_confidence"] in {"HIGH", "WATCH"}


def test_shadow_command_center_surfaces_registry_active_security_strategies(tmp_path: Path) -> None:
    _write_strategy_registry(tmp_path)
    date_dir = tmp_path / "outputs" / "shadow_candidates" / "2026-06-03"
    date_dir.mkdir(parents=True)
    strategies = {
        slug: {
            "strategy_name": name,
            "status": "OK",
            "data_status": "OK",
            "daily_return": 0.01,
            "cumulative_return": 0.05,
            "excess_return_vs_spy": excess,
            "rolling_count_of_valid_days": 40,
        }
        for slug, name, excess in (
            ("caerus_polaris", "Caerus Polaris", 0.02),
            ("caerus_orion", "Caerus Orion", 0.03),
            ("caerus_lyra", "Caerus Lyra", 0.025),
            ("caerus_phoenix", "Caerus Phoenix", 0.035),
        )
    }
    (date_dir / "shadow_evaluation.json").write_text(
        json.dumps({"trade_date": "2026-06-03", "benchmark_symbol": "SPY", "strategies": strategies}, sort_keys=True),
        encoding="utf-8",
    )
    perf_dir = tmp_path / "outputs" / "shadow_candidates" / "performance"
    perf_dir.mkdir(parents=True)
    perf_dir.joinpath("shadow_nav_series.csv").write_text(
        "\n".join(
            ["date,caerus_polaris,caerus_orion,caerus_lyra,caerus_phoenix,spy_benchmark"]
            + [
                f"2026-05-{day:02d},{1.0 + day * 0.001:.6f},{1.0 + day * 0.0015:.6f},{1.0 + day * 0.0012:.6f},{1.0 + day * 0.0018:.6f},{1.0 + day * 0.0008:.6f}"
                for day in range(22, 28)
            ]
        ),
        encoding="utf-8",
    )

    section = DashboardV1Builder(tmp_path)._build_shadow_command_center()

    slugs = [row["slug"] for row in section["strategies"]]
    assert slugs == ["caerus_polaris", "caerus_orion", "caerus_lyra", "caerus_phoenix"]
    assert "caerus_argo" not in slugs
    assert section["summary"]["candidate_count"] == 3
    assert section["summary"]["control"] == "caerus_polaris"
    assert "caerus_phoenix" in section["rolling_excess_series"][-1]
