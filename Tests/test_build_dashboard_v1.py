from __future__ import annotations

import json
from pathlib import Path

from scripts.research.build_dashboard_v1 import DashboardV1Builder, parse_args, write_dashboard_v1_payload


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


def _write_alpha_strategy_registry(root: Path) -> None:
    path = root / "config" / "research" / "strategy_registry.json"
    path.parent.mkdir(parents=True)
    base = {
        "strategy_type": "security_selection",
        "family": "core_momentum",
        "eligible_for_shadow": True,
        "eligible_for_promotion": True,
        "benchmark": "SPY",
        "execution_impact": "NON_EXECUTIONAL",
        "capabilities": {
            "produces_holdings": True,
            "produces_nav": True,
            "produces_attribution": True,
            "produces_promotion_metrics": True,
            "produces_regime_overlay": False,
        },
    }
    strategies = [
        {
            **base,
            "strategy_id": "caerus_polaris",
            "display_name": "Caerus Polaris",
            "short_name": "polaris",
            "status": "paper",
            "role": "baseline",
            "eligible_for_promotion": False,
            "display_order": 10,
            "shadow_tracking": {"enabled": True, "source_variant": "baseline_top10_daily"},
        },
        {
            **base,
            "strategy_id": "caerus_polaris_alpha",
            "display_name": "Polaris_Alpha",
            "short_name": "polaris_alpha",
            "status": "shadow",
            "role": "challenger",
            "display_order": 15,
            "shadow_tracking": {
                "enabled": True,
                "source_variant": "polaris_alpha_top4_cap20_daily",
                "baseline_strategy_id": "caerus_polaris",
                "construction": {"top_n": 4, "max_position_weight": 0.2, "weighting": "equal"},
                "review_checkpoints_trading_days": [20, 60],
            },
        },
        {
            **base,
            "strategy_id": "caerus_orion",
            "display_name": "Caerus Orion",
            "short_name": "orion",
            "status": "shadow",
            "role": "challenger",
            "display_order": 20,
            "shadow_tracking": {"enabled": True, "source_variant": "orion_top5"},
        },
        {
            **base,
            "strategy_id": "caerus_orion_alpha",
            "display_name": "Orion_Alpha",
            "short_name": "orion_alpha",
            "status": "shadow",
            "role": "challenger",
            "display_order": 25,
            "shadow_tracking": {
                "enabled": True,
                "source_variant": "orion_alpha_top3_cap25",
                "baseline_strategy_id": "caerus_orion",
                "construction": {"top_n": 3, "max_position_weight": 0.25, "weighting": "equal"},
                "review_checkpoints_trading_days": [20, 60],
            },
        },
        {
            **base,
            "strategy_id": "caerus_lyra",
            "display_name": "Caerus Lyra",
            "short_name": "lyra",
            "status": "shadow",
            "role": "challenger",
            "display_order": 30,
            "shadow_tracking": {"enabled": True, "source_variant": "lyra"},
        },
    ]
    path.write_text(
        json.dumps({"schema_version": "caerus_strategy_registry_v1", "strategies": strategies}, sort_keys=True),
        encoding="utf-8",
    )


def _write_alpha_manifest(root: Path) -> None:
    path = root / "research_registry" / "sleeves" / "manifest.json"
    path.parent.mkdir(parents=True)
    sleeves = [
        {"sleeve_id": "polaris", "strategy_id": "caerus_polaris", "display_name": "Caerus Polaris", "lifecycle_stage": "paper_observed", "status": "current_paper_baseline"},
        {"sleeve_id": "polaris_alpha", "strategy_id": "caerus_polaris_alpha", "display_name": "Polaris_Alpha", "lifecycle_stage": "shadow_observed", "status": "current_shadow_challenger"},
        {"sleeve_id": "orion", "strategy_id": "caerus_orion", "display_name": "Caerus Orion", "lifecycle_stage": "shadow_observed", "status": "current_shadow_challenger"},
        {"sleeve_id": "orion_alpha", "strategy_id": "caerus_orion_alpha", "display_name": "Orion_Alpha", "lifecycle_stage": "shadow_observed", "status": "current_shadow_challenger"},
        {"sleeve_id": "lyra", "strategy_id": "caerus_lyra", "display_name": "Caerus Lyra", "lifecycle_stage": "shadow_observed", "status": "current_shadow_challenger"},
    ]
    path.write_text(
        json.dumps({"schema_version": "caerus_sleeve_manifest_v1", "manifest_version": "test", "sleeves": sleeves}, sort_keys=True),
        encoding="utf-8",
    )


def _write_shadow_alpha_artifacts(root: Path, report_date: str) -> None:
    dated = root / "outputs" / "shadow_candidates" / report_date
    dated.mkdir(parents=True)
    strategies = {}
    for slug, name, valid_days, cumulative, drawdown, turnover, top3, effective_n, alpha_proxy in (
        ("caerus_polaris", "Caerus Polaris", 28, 0.12, -0.05, 0.08, 0.30, 10.0, 0.10),
        ("caerus_polaris_alpha", "Polaris_Alpha", 1, 0.02, 0.0, 0.0, 0.60, 4.0, 0.02),
        ("caerus_orion", "Caerus Orion", 28, 0.16, -0.07, 0.02, 0.60, 5.0, 0.14),
        ("caerus_orion_alpha", "Orion_Alpha", 1, 0.03, 0.0, 0.0, 0.75, 3.0, 0.03),
        ("caerus_lyra", "Caerus Lyra", 28, 0.08, -0.04, 0.03, 0.50, 5.0, 0.06),
    ):
        strategies[slug] = {
            "strategy_name": name,
            "status": "OK",
            "data_status": "OK",
            "daily_return": 0.01,
            "cumulative_return": cumulative,
            "excess_return_vs_spy": cumulative - 0.01,
            "rolling_count_of_valid_days": valid_days,
            "max_drawdown": drawdown,
            "avg_turnover": turnover,
            "avg_top_3_concentration": top3,
            "avg_effective_n": effective_n,
            "alpha_per_dollar_deployed_proxy": alpha_proxy,
        }
    (dated / "shadow_evaluation.json").write_text(
        json.dumps({"trade_date": report_date, "benchmark_symbol": "SPY", "strategies": strategies}, sort_keys=True),
        encoding="utf-8",
    )
    perf = root / "outputs" / "shadow_candidates" / "performance"
    perf.mkdir(parents=True)
    perf.joinpath("shadow_nav_series.csv").write_text(
        "date,caerus_polaris,caerus_polaris_alpha,caerus_orion,caerus_orion_alpha,caerus_lyra,spy_benchmark\n"
        f"{report_date},1.12,1.02,1.16,1.03,1.08,1.01\n",
        encoding="utf-8",
    )


def test_build_dashboard_v1_accepts_date_alias() -> None:
    args = parse_args(["--date", "2026-06-08"])

    assert args.report_date == "2026-06-08"


def test_dashboard_v1_payload_aliases_are_consistent(tmp_path: Path) -> None:
    payload = {
        "schema_version": "dashboard-v2-prototype",
        "sections": {"operator_control_tower": {"summary": {"live_pilot_state": "ACTIVE"}}},
    }

    write_dashboard_v1_payload(payload, tmp_path)

    canonical = (tmp_path / "dashboard_data.json").read_text(encoding="utf-8")
    assert (tmp_path / "dashboard-data.json").read_text(encoding="utf-8") == canonical
    wrapper = (tmp_path / "dashboard-data.js").read_text(encoding="utf-8")
    assert wrapper.startswith("window.DASHBOARD_V1 = ")
    assert '"operator_control_tower"' in wrapper


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
    assert "decision_grade" in payload["sections"]
    assert "live_pilot" in payload["sections"]
    assert "sleeve_inventory" in payload["sections"]
    assert "baseline_alpha_comparison" in payload["sections"]
    assert "account_layers" in payload["sections"]
    assert "governance_state" in payload["sections"]
    assert "operator_control_tower" in payload["sections"]
    assert payload["sections"]["decision_grade"]["status"] == "PARTIAL"
    assert isinstance(payload["sections"]["operator_control_tower"]["summary"]["operator_action_required"], bool)
    assert len(payload["sections"]["operator_control_tower"]["cards"]) == 6
    assert payload["terminal"]["headline"]["nav"] == 10000.0
    assert payload["terminal"]["benchmark"]["rolling_5d_return"] is None
    assert payload["terminal"]["leaders"]["winners"][0]["ticker"] == "AAPL"
    assert payload["sections"]["daily_decision_intelligence"]["summary"]["buy_count"] == 1
    assert payload["sections"]["live_readiness"]["summary"]["deployment_confidence"] in {"HIGH", "WATCH"}


def test_dashboard_v1_surfaces_alpha_sleeves_and_live_pilot_evidence(tmp_path: Path) -> None:
    report_date = "2026-06-08"
    _write_alpha_strategy_registry(tmp_path)
    _write_alpha_manifest(tmp_path)
    _write_shadow_alpha_artifacts(tmp_path, report_date)

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-live"
    run_root.mkdir(parents=True)
    (tmp_path / "outputs" / "live_pilot" / "plans").mkdir(parents=True)
    (tmp_path / "outputs" / "live_pilot" / "plans" / f"live_pilot_plan_{report_date}.json").write_text(
        json.dumps(
            {
                "trade_date": report_date,
                "status": "READY_FOR_MANUAL_APPROVAL",
                "capital_cap": 100.0,
                "order_policy": {"scope": "FR-104 LIVE_PILOT only", "order_type": "market", "paper_or_production_impact": "none"},
                "selected_order": {"ticker": "AAPL", "qty": 1, "expected_price": 50, "order_type": "market"},
            }
        ),
        encoding="utf-8",
    )
    (run_root / "live_pilot_operator_summary.json").write_text(
        json.dumps({"run_id": "run-live", "terminal_status": "SUBMITTED", "submitted_count": 1, "filled_count": 1, "fill_rate": 1.0, "generated_at": "2026-06-08T14:00:00+00:00"}),
        encoding="utf-8",
    )
    (run_root / "live_pilot_evidence_metrics.json").write_text(
        json.dumps({"submitted_count": 1, "accepted_count": 1, "filled_count": 1, "fill_rate": 1.0, "rejected_count": 0, "reconciliation_clean_rate": 1.0, "cash_deployment_rate": 0.5, "capital_cap_usd": 100.0, "idle_cash_reason": "partial_cap_deployment"}),
        encoding="utf-8",
    )
    (run_root / "live_pilot_reconciliation.json").write_text(json.dumps({"status": "CLEAN", "state": "CLEAN", "open_count": 0}), encoding="utf-8")
    (run_root / "live_pilot_orders_submitted.json").write_text(
        json.dumps({"orders": [{"symbol": "AAPL", "status": "filled", "qty": 1, "expected_price": 50, "fill_price": 50.1, "submitted_order_type": "market"}]}),
        encoding="utf-8",
    )
    (run_root / "live_pilot_open_order_check.json").write_text(json.dumps({"blocking_open_orders": [], "block_submission": False}), encoding="utf-8")
    (run_root / "live_pilot_broker_snapshot_post.json").write_text(
        json.dumps({"account": {"cash": "450", "equity": "500", "buying_power": "450"}, "positions": [{"symbol": "AAPL", "qty": "1", "market_value": "50"}], "open_orders": []}),
        encoding="utf-8",
    )

    payload = DashboardV1Builder(tmp_path, report_date=report_date).build()

    sleeve_names = [row["display_name"] for row in payload["sections"]["sleeve_inventory"]["rows"]]
    assert "Polaris_Alpha" in sleeve_names
    assert "Orion_Alpha" in sleeve_names
    assert "Caerus Lyra" in sleeve_names
    rows_by_name = {row["display_name"]: row for row in payload["sections"]["sleeve_inventory"]["rows"]}
    assert rows_by_name["Polaris_Alpha"]["registry_eligible_for_promotion"] is True
    assert rows_by_name["Polaris_Alpha"]["eligible_for_promotion"] is False
    assert rows_by_name["Polaris_Alpha"]["evidence_eligible_for_promotion"] is False
    assert rows_by_name["Orion_Alpha"]["registry_eligible_for_promotion"] is True
    assert rows_by_name["Orion_Alpha"]["eligible_for_promotion"] is False
    assert rows_by_name["Orion_Alpha"]["evidence_eligible_for_promotion"] is False
    assert payload["sections"]["baseline_alpha_comparison"]["summary"]["pair_count"] == 2
    assert payload["sections"]["live_pilot"]["status"] == "SUBMITTED"
    assert payload["sections"]["live_pilot"]["metrics"]["fill_rate"] == 1.0
    assert payload["sections"]["operator_control_tower"]["summary"]["live_pilot_state"] == "ACTIVE"
    assert payload["sections"]["operator_control_tower"]["summary"]["alpha_pair_count"] == 2
    assert payload["sections"]["operator_control_tower"]["latest_order"]["ticker"] == "AAPL"
    assert payload["sections"]["account_layers"]["rows"][1]["layer"] == "Live pilot account"
    assert payload["sections"]["governance_state"]["summary"]["fr068_pilot_blocking"] is False


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
