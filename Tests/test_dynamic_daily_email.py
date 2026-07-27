from __future__ import annotations

import json
from pathlib import Path

from core.dynamic_daily_email import (
    build_live_pilot_account_payload,
    render_dynamic_email_sections,
)
from paper.build_execution_email import build_execution_email_text


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_registry(root: Path) -> None:
    _write_json(
        root / "config" / "research" / "strategy_registry.json",
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
                    "capabilities": {"produces_holdings": True, "produces_nav": True},
                    "shadow_tracking": {"enabled": True, "source_variant": "baseline_top10_daily"},
                },
                {
                    "strategy_id": "caerus_polaris_alpha",
                    "display_name": "Polaris_Alpha",
                    "short_name": "polaris_alpha",
                    "strategy_type": "security_selection",
                    "family": "core_momentum",
                    "status": "shadow",
                    "role": "challenger",
                    "eligible_for_shadow": True,
                    "eligible_for_promotion": True,
                    "benchmark": "SPY",
                    "execution_impact": "NON_EXECUTIONAL",
                    "display_order": 20,
                    "capabilities": {"produces_holdings": True, "produces_nav": True},
                    "shadow_tracking": {
                        "enabled": True,
                        "source_variant": "polaris_alpha_top4_cap20_daily",
                        "baseline_strategy_id": "caerus_polaris",
                    },
                },
                {
                    "strategy_id": "caerus_future",
                    "display_name": "Caerus Future",
                    "short_name": "future",
                    "strategy_type": "security_selection",
                    "family": "core_momentum",
                    "status": "research",
                    "role": "research_candidate",
                    "eligible_for_shadow": True,
                    "eligible_for_promotion": False,
                    "benchmark": "SPY",
                    "execution_impact": "NON_EXECUTIONAL",
                    "display_order": 30,
                    "capabilities": {"produces_holdings": True, "produces_nav": True},
                    "shadow_tracking": {"enabled": False, "source_variant": None},
                },
            ],
        },
    )


def _write_manifest(root: Path) -> None:
    _write_json(
        root / "research_registry" / "sleeves" / "manifest.json",
        {
            "schema_version": "caerus_sleeve_manifest_v1",
            "sleeves": [
                {
                    "sleeve_id": "polaris",
                    "strategy_id": "caerus_polaris",
                    "display_name": "Caerus Polaris",
                    "lifecycle_stage": "paper_observed",
                },
                {
                    "sleeve_id": "polaris_alpha",
                    "strategy_id": "caerus_polaris_alpha",
                    "display_name": "Polaris_Alpha",
                    "lifecycle_stage": "shadow_observed",
                },
                {
                    "sleeve_id": "manifest_only_sleeve",
                    "display_name": "Manifest Only Sleeve",
                    "lifecycle_stage": "spec_only",
                },
            ],
        },
    )


def _write_shadow(root: Path, trade_date: str) -> None:
    shadow = root / "outputs" / "shadow_candidates" / trade_date
    _write_json(shadow / "caerus_polaris_alpha.json", {"holdings": [{"ticker": "AAPL"}]})
    _write_json(
        shadow / "shadow_evaluation.json",
        {
            "trade_date": trade_date,
            "strategies": {
                "caerus_polaris": {
                    "status": "OK",
                    "data_status": "OK",
                    "daily_return": 0.01,
                    "cumulative_return": 0.10,
                    "avg_turnover": 0.02,
                    "avg_hhi": 0.1,
                    "avg_effective_n": 10,
                    "avg_top_3_concentration": 0.3,
                },
                "caerus_polaris_alpha": {
                    "status": "OK",
                    "data_status": "OK",
                    "daily_return": 0.02,
                    "cumulative_return": 0.20,
                    "avg_turnover": 0.04,
                    "avg_hhi": 0.25,
                    "avg_effective_n": 4,
                    "avg_top_3_concentration": 0.60,
                },
            },
        },
    )
    _write_json(
        shadow / "promotion_readiness.json",
        {
            "strategies": {
                "caerus_polaris_alpha": {
                    "readiness_state": "OBSERVE",
                    "confidence": "LOW",
                }
            }
        },
    )


def _write_live_pilot_run(root: Path) -> None:
    run = root / "outputs" / "live_pilot" / "runs" / "run-1"
    _write_json(
        run / "live_pilot_operator_summary.json",
        {
            "run_id": "run-1",
            "terminal_status": "SUBMITTED",
            "reason_code": "CLEAN",
        },
    )
    _write_json(run / "live_pilot_reconciliation.json", {"status": "CLEAN"})
    _write_json(
        run / "live_pilot_broker_snapshot_post.json",
        {
            "account": {"cash": "75.50", "equity": "1000", "buying_power": "800"},
            "positions": [{"symbol": "AAPL", "qty": "1"}],
            "open_orders": [{"symbol": "MSFT", "status": "new"}],
        },
    )
    _write_json(
        run / "live_pilot_orders_submitted.json",
        {
            "orders": [
                {
                    "symbol": "AAPL",
                    "side": "BUY",
                    "status": "filled",
                    "filled_avg_price": "50",
                    "entry_execution_policy": "live_pilot_buy_market_order_immediate",
                    "submitted_order_type": "market",
                    "is_marketable": True,
                    "is_passive": False,
                    "prior_unfilled_attempts": 2,
                    "escalation_reason": "prior_unfilled_live_buy_attempts_detected",
                }
            ]
        },
    )
    _write_json(
        run / "live_pilot_orders_intended.json",
        {
            "orders": [
                {
                    "symbol": "AAPL",
                    "side": "BUY",
                    "entry_execution_policy": "live_pilot_buy_market_order_immediate",
                    "submitted_order_type": "market",
                    "prior_unfilled_attempts": 2,
                    "escalation_reason": "prior_unfilled_live_buy_attempts_detected",
                }
            ]
        },
    )
    _write_json(
        run / "live_pilot_evidence_metrics.json",
        {
            "submitted_count": 1,
            "accepted_count": 1,
            "filled_count": 1,
            "fill_rate": 1.0,
            "cash_deployment_rate": 0.5,
            "idle_cash_reason": "partial_cap_deployment",
            "approved_buy_count": 1,
            "submitted_buy_count": 1,
            "unfilled_buy_count": 0,
            "escalated_buy_count": 1,
            "entry_execution_policy": "live_pilot_buy_market_order_immediate",
            "submitted_order_type": "market",
            "marketable_order_count": 1,
            "passive_order_count": 0,
            "prior_unfilled_attempts": 2,
        },
    )
    _write_json(
        run / "execution_results.json",
        {
            "approved_buy_count": 1,
            "submitted_buy_count": 1,
            "unfilled_buy_count": 0,
            "escalated_buy_count": 1,
            "entry_execution_policy": "live_pilot_buy_market_order_immediate",
            "submitted_order_type": "market",
            "marketable_order_count": 1,
            "passive_order_count": 0,
            "prior_unfilled_attempts": 2,
        },
    )


def test_dynamic_sections_include_registry_manifest_alpha_and_live_account(tmp_path: Path) -> None:
    trade_date = "2026-06-23"
    _write_registry(tmp_path)
    _write_manifest(tmp_path)
    _write_shadow(tmp_path, trade_date)
    _write_live_pilot_run(tmp_path)

    rendered = render_dynamic_email_sections(tmp_path, trade_date)

    assert "Dynamic Sleeve Inventory" in rendered["text"]
    assert "Caerus Polaris | paper | baseline" in rendered["text"]
    assert "Polaris_Alpha | shadow | alpha" in rendered["text"]
    assert "Manifest Only Sleeve" in rendered["text"]
    assert "Open orders: 1" in rendered["text"]
    assert "Filled pilot orders: 1" in rendered["text"]
    assert "Fill rate: 100.00%" in rendered["text"]
    assert "Approved buys: 1" in rendered["text"]
    assert "Submitted buys: 1" in rendered["text"]
    assert "Unfilled buys: 0" in rendered["text"]
    assert "Escalated buys: 1" in rendered["text"]
    assert "Entry execution policy: live_pilot_buy_market_order_immediate" in rendered["text"]
    assert "Submitted order type: market" in rendered["text"]
    assert "Marketable/passive orders: 1/0" in rendered["text"]
    assert "Prior unfilled attempts: 2" in rendered["text"]
    assert "Polaris_Alpha" in rendered["html"]


def test_execution_email_uses_dynamic_sections_when_repo_root_is_supplied(tmp_path: Path) -> None:
    trade_date = "2026-06-23"
    _write_registry(tmp_path)
    _write_manifest(tmp_path)
    _write_shadow(tmp_path, trade_date)

    _, body = build_execution_email_text(
        {
            "repo_root": str(tmp_path),
            "trade_date": trade_date,
            "mode": "PAPER",
            "execution_status": "READY",
            "trades": [],
        }
    )

    assert "Dynamic Sleeve Inventory" in body
    assert "Polaris_Alpha" in body
    assert "Caerus Future" in body


def test_live_account_payload_uses_explicit_run_and_refreshed_results(tmp_path: Path) -> None:
    scoped = tmp_path / "outputs" / "live_pilot" / "runs" / "scoped-submit"
    wrong = tmp_path / "outputs" / "live_pilot" / "runs" / "newer-dry"
    _write_json(
        scoped / "live_pilot_operator_summary.json",
        {"run_id": "scoped-submit", "terminal_status": "SUBMITTED"},
    )
    _write_json(scoped / "live_pilot_reconciliation.json", {"status": "CLEAN"})
    _write_json(
        scoped / "live_pilot_evidence_metrics.json",
        {"filled_count": 0, "fill_rate": 0.0, "idle_cash_reason": "submitted_not_filled"},
    )
    _write_json(
        scoped / "live_pilot_orders_submitted.json",
        {
            "orders": [
                {
                    "symbol": "LRCX",
                    "side": "SELL",
                    "status": "pending_new",
                    "escalation_reason": "not_applicable_non_buy_order",
                }
            ]
        },
    )
    _write_json(
        scoped / "execution_results.json",
        {
            "run_id": "scoped-submit",
            "status": "SUBMITTED",
            "operator_execution_status": "executed",
            "submitted_count": 1,
            "filled_count": 1,
            "escalation_reason": "prior_unfilled_live_buy_attempts_detected",
            "broker_responses": [
                {
                    "symbol": "LRCX",
                    "side": "SELL",
                    "status": "OrderStatus.FILLED",
                }
            ],
        },
    )
    _write_json(
        scoped / "live_pilot_broker_snapshot_post.json",
        {"account": {"cash": "75", "equity": "500"}, "open_orders": [], "positions": []},
    )
    _write_json(
        wrong / "live_pilot_operator_summary.json",
        {"run_id": "newer-dry", "terminal_status": "DRY_RUN"},
    )

    payload = build_live_pilot_account_payload(tmp_path, run_root=scoped)

    assert payload["latest_run_id"] == "scoped-submit"
    assert payload["status"] == "EXECUTED"
    assert len(payload["filled_orders"]) == 1
    assert payload["evidence_metrics"]["fill_rate"] == 1.0
    assert payload["escalation_reason"] == "prior_unfilled_live_buy_attempts_detected"
