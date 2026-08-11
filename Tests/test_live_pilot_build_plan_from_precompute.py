from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.live_pilot_build_plan_from_precompute import (
    TARGET_PORTFOLIO_SCHEMA,
    build_live_pilot_plan,
)


SECTOR_MAP = {
    "AAPL": "Information Technology",
    "MSFT": "Information Technology",
    "JNJ": "Health Care",
    "PNC": "Financials",
    "SPG": "Real Estate",
}


def _stub_prices(prices: dict[str, float]):
    def _fetch(tickers, run_date):  # noqa: ANN001
        rows = [{"ticker": t, "open": prices[t], "price_date": run_date} for t in tickers if t in prices]
        return pd.DataFrame(rows, columns=["ticker", "open", "price_date"])

    return _fetch


def _bundle(
    tmp_path: Path,
    *,
    signals: list[dict[str, object]],
    payload_trades: list[dict[str, object]] | None = None,
    trade_date: str = "2026-06-22",
    cash_target_weight: float | None = None,
    strategy_identity: dict[str, object] | None = None,
) -> Path:
    bundle = tmp_path / "outputs" / "precompute" / trade_date
    bundle.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "trade_date": trade_date,
        "mode": "PAPER",
        "execution_status": "PLANNED",
        "trades": payload_trades or [],
    }
    if cash_target_weight is not None:
        payload["cash_target_weight"] = cash_target_weight
    (bundle / "planned_execution_payload.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (bundle / "signals.json").write_text(
        json.dumps(
            {
                "snapshot_date": trade_date,
                "signals": signals,
                **(
                    {"strategy_identity": strategy_identity}
                    if strategy_identity is not None
                    else {}
                ),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return bundle / "planned_execution_payload.json"


def _build(tmp_path: Path, payload_path: Path, *, prices: dict[str, float], **kwargs):
    defaults: dict[str, object] = dict(
        approved_sleeve="growth_engine_v4",
        capital_cap=502.0,
        max_orders=50,
        output_dir=tmp_path / "outputs" / "live_pilot" / "plans",
        price_fetcher=_stub_prices(prices),
        sector_map=SECTOR_MAP,
        state_dir=tmp_path / "live_state",
    )
    defaults.update(kwargs)
    return build_live_pilot_plan(payload_path=payload_path, **defaults)


def _orion_shadow(
    tmp_path: Path,
    *,
    trade_date: str = "2026-06-22",
    weights: dict[str, float] | None = None,
) -> Path:
    path = (
        tmp_path
        / "outputs"
        / "shadow_candidates"
        / trade_date
        / "caerus_orion.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "strategy_name": "Caerus Orion",
                "strategy_slug": "caerus_orion",
                "source_variant": "h2_rank_decay_exit_h6_top5",
                "trade_date": trade_date,
                "effective_trade_date": trade_date,
                "target_weights": weights
                or {"AAPL": 0.2, "MSFT": 0.2, "JNJ": 0.2, "PNC": 0.2, "SPG": 0.2},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_paper_lane_uses_exact_governed_orion_snapshot(tmp_path: Path) -> None:
    payload_path = _bundle(
        tmp_path,
        signals=[{"ticker": "JNJ", "target_weight": 1.0, "sleeve": "sleeve_quality"}],
    )
    source_path = _orion_shadow(tmp_path)

    plan = _build(
        tmp_path,
        payload_path,
        prices={"AAPL": 100.0, "MSFT": 200.0, "JNJ": 150.0, "PNC": 170.0, "SPG": 180.0},
        approved_sleeve="caerus_orion",
        lane="paper",
        shadow_root=tmp_path / "outputs" / "shadow_candidates",
    )

    assert plan["status"] == "READY_FOR_MANUAL_APPROVAL"
    assert {row["symbol"] for row in plan["target_portfolio"]} == {"AAPL", "MSFT", "JNJ", "PNC", "SPG"}
    assert plan["decision_source_artifact"]["path"] == str(source_path)
    assert len(plan["decision_source_artifact"]["sha256"]) == 64
    assert plan["cash_target_weight"] == pytest.approx(0.05)
    assert plan["strategy_identity_validation"]["status"] == "PASS"
    assert plan["execution_lane"] == "paper"
    assert "ALPACA_PAPER=1" in plan["required_dry_run_command"]
    assert "ALPACA_BASE_URL=https://paper-api.alpaca.markets" in plan["required_dry_run_command"]
    assert "ALPACA_BASE_URL=https://api.alpaca.markets" not in plan["required_dry_run_command"]
    assert "CAERUS_REQUIRE_APPROVED_EXECUTION_PACKAGE=1" in plan["required_live_command"]
    assert "ALPACA_BASE_URL=https://api.alpaca.markets" not in plan["required_live_command"]
    decision_path = Path(plan["source_signals"])
    decision_input = json.loads(decision_path.read_text(encoding="utf-8"))
    assert decision_input["schema_version"] == "caerus.decision_input.v1"
    assert decision_input["source_strategy_artifact"]["sha256"] == plan["decision_source_artifact"]["sha256"]
    assert decision_input["source_strategy_artifact"]["source_trade_date"] == "2026-06-22"
    assert decision_input["source_strategy_artifact"]["decision_trade_date"] == "2026-06-22"
    assert decision_input["source_strategy_artifact"]["source_trading_session_lag"] == 0
    assert sum(
        row["target_weight"]
        for row in decision_input["signals"]
        if row["ticker"] != "CASH"
    ) == pytest.approx(0.95)


def test_paper_lane_uses_immediately_previous_trading_session_snapshot(
    tmp_path: Path,
) -> None:
    trade_date = "2026-08-10"  # Monday
    payload_path = _bundle(tmp_path, signals=[], trade_date=trade_date)
    source_path = _orion_shadow(tmp_path, trade_date="2026-08-07")

    plan = _build(
        tmp_path,
        payload_path,
        prices={"AAPL": 100.0, "MSFT": 200.0, "JNJ": 150.0, "PNC": 170.0, "SPG": 180.0},
        approved_sleeve="caerus_orion",
        lane="paper",
        shadow_root=tmp_path / "outputs" / "shadow_candidates",
    )

    assert plan["status"] == "READY_FOR_MANUAL_APPROVAL"
    source = plan["decision_source_artifact"]
    assert source["path"] == str(source_path)
    assert source["source_trade_date"] == "2026-08-07"
    assert source["source_effective_trade_date"] == "2026-08-07"
    assert source["decision_trade_date"] == trade_date
    assert source["effective_trade_date"] == trade_date
    assert source["source_trading_session_lag"] == 1
    assert source["source_session_policy"] == "SAME_OR_PREVIOUS_TRADING_SESSION"
    assert len(source["sha256"]) == 64
    identity = json.loads(Path(plan["source_signals"]).read_text())["strategy_identity"]
    assert identity["execution_target_source"] == str(source_path)
    assert identity["shadow_baseline_source"] == str(source_path)
    assert identity["shadow_baseline_source_sha256"] == source["sha256"]
    assert identity["shadow_baseline_source_trade_date"] == "2026-08-07"


def test_paper_lane_skips_current_preclose_reporting_snapshot(
    tmp_path: Path,
) -> None:
    trade_date = "2026-08-11"
    payload_path = _bundle(tmp_path, signals=[], trade_date=trade_date)
    prior_path = _orion_shadow(tmp_path, trade_date="2026-08-10")
    provisional_path = _orion_shadow(tmp_path, trade_date=trade_date)
    provisional = json.loads(provisional_path.read_text())
    provisional.update(
        {
            "effective_trade_date": "2026-08-10",
            "decision_eligible": False,
            "observation_status": "PENDING_SESSION_CLOSE",
        }
    )
    provisional_path.write_text(json.dumps(provisional), encoding="utf-8")

    plan = _build(
        tmp_path,
        payload_path,
        prices={
            "AAPL": 100.0,
            "MSFT": 200.0,
            "JNJ": 150.0,
            "PNC": 170.0,
            "SPG": 180.0,
        },
        approved_sleeve="caerus_orion",
        lane="paper",
        shadow_root=tmp_path / "outputs" / "shadow_candidates",
    )

    assert plan["status"] == "READY_FOR_MANUAL_APPROVAL"
    assert plan["decision_source_artifact"]["path"] == str(prior_path)
    assert plan["decision_source_artifact"]["source_trading_session_lag"] == 1
    policy = plan["target_attainment_policy"]
    assert policy["target_cash_weight"] == 0.05
    assert policy["minimum_cash_weight"] == 0.025
    assert (
        plan["approved_execution_package"]["constraints"][
            "target_attainment_policy"
        ]
        == policy
    )


def test_paper_lane_rejects_provisional_previous_session_snapshot(
    tmp_path: Path,
) -> None:
    trade_date = "2026-08-11"
    payload_path = _bundle(tmp_path, signals=[], trade_date=trade_date)
    prior_path = _orion_shadow(tmp_path, trade_date="2026-08-10")
    prior = json.loads(prior_path.read_text())
    prior.update(
        {
            "decision_eligible": False,
            "observation_status": "PENDING_SESSION_CLOSE",
        }
    )
    prior_path.write_text(json.dumps(prior), encoding="utf-8")

    plan = _build(
        tmp_path,
        payload_path,
        prices={
            "AAPL": 100.0,
            "MSFT": 200.0,
            "JNJ": 150.0,
            "PNC": 170.0,
            "SPG": 180.0,
        },
        approved_sleeve="caerus_orion",
        lane="paper",
        shadow_root=tmp_path / "outputs" / "shadow_candidates",
    )

    assert plan["status"] == "BLOCKED"
    assert plan["reason_code"] == "paper_governed_decision_source_invalid"
    assert "not Decision-eligible" in plan["block_diagnostics"]["error"]


def test_paper_lane_rejects_snapshot_older_than_previous_trading_session(
    tmp_path: Path,
) -> None:
    trade_date = "2026-08-10"  # Monday; Friday is the only permitted prior session.
    payload_path = _bundle(tmp_path, signals=[], trade_date=trade_date)
    _orion_shadow(tmp_path, trade_date="2026-08-06")

    plan = _build(
        tmp_path,
        payload_path,
        prices={},
        approved_sleeve="caerus_orion",
        lane="paper",
        shadow_root=tmp_path / "outputs" / "shadow_candidates",
    )

    assert plan["status"] == "BLOCKED"
    assert plan["reason_code"] == "paper_governed_decision_source_invalid"
    assert "2026-08-07" in plan["block_diagnostics"]["error"]


def test_paper_lane_previous_session_rule_skips_exchange_holiday(
    tmp_path: Path,
) -> None:
    trade_date = "2026-09-08"  # Tuesday after Labor Day.
    payload_path = _bundle(tmp_path, signals=[], trade_date=trade_date)
    source_path = _orion_shadow(tmp_path, trade_date="2026-09-04")

    plan = _build(
        tmp_path,
        payload_path,
        prices={"AAPL": 100.0, "MSFT": 200.0, "JNJ": 150.0, "PNC": 170.0, "SPG": 180.0},
        approved_sleeve="caerus_orion",
        lane="paper",
        shadow_root=tmp_path / "outputs" / "shadow_candidates",
    )

    assert plan["status"] == "READY_FOR_MANUAL_APPROVAL"
    assert plan["decision_source_artifact"]["path"] == str(source_path)
    assert plan["decision_source_artifact"]["source_trade_date"] == "2026-09-04"
    assert plan["decision_source_artifact"]["source_trading_session_lag"] == 1


def test_full_target_all_names_emitted_and_priced(tmp_path: Path) -> None:
    payload_path = _bundle(
        tmp_path,
        signals=[
            {"ticker": "AAPL", "target_weight": 0.5, "sleeve": "sleeve_quality, sleeve_trend"},
            {"ticker": "MSFT", "target_weight": 0.3, "sleeve": "sleeve_trend"},
            {"ticker": "JNJ", "target_weight": 0.2, "sleeve": "sleeve_quality"},
        ],
        # AAPL changed in precompute -> carries entry_price; MSFT/JNJ priced via yfinance.
        payload_trades=[{"ticker": "AAPL", "side": "BUY", "shares": 1, "entry_price": 250.0}],
    )
    plan = _build(tmp_path, payload_path, prices={"MSFT": 400.0, "JNJ": 150.0})

    assert plan["status"] == "READY_FOR_MANUAL_APPROVAL"
    assert plan["target_portfolio_schema"] == TARGET_PORTFOLIO_SCHEMA
    tp = plan["target_portfolio"]
    assert {r["symbol"] for r in tp} == {"AAPL", "MSFT", "JNJ"}
    assert all(r["price"] > 0 for r in tp)
    approved = plan["approved_execution_package"]
    assert approved["schema_version"] == "caerus.execution.v2"
    assert "constraints" in approved
    assert approved["approved_target_rows"] == tp
    assert approved["approved_cash_weight"] == pytest.approx(plan["cash_target_weight"])
    assert all(Path(path).exists() for path in plan["authority_package_paths"].values())
    # The shared temporary pilot cap clips AAPL to 30%; unclassified sector-cap
    # behavior is unchanged and remains outside this guardrail change.
    assert tp[0]["symbol"] == "AAPL"
    weights = {r["symbol"]: float(r["target_weight"]) for r in tp}
    assert weights["AAPL"] == pytest.approx(0.30)
    assert weights["MSFT"] == pytest.approx(0.30)
    assert max(weights.values()) <= 0.30
    # Price provenance: AAPL from payload, others from yfinance.
    by_symbol = {r["symbol"]: r for r in tp}
    assert by_symbol["AAPL"]["price_source"] == "payload_entry_price"
    assert by_symbol["AAPL"]["price"] == pytest.approx(250.0)
    assert by_symbol["MSFT"]["price_source"] == "yfinance_open"


def test_live_lane_blocks_orion_label_when_targets_are_growth_engine(
    tmp_path: Path,
) -> None:
    payload_path = _bundle(
        tmp_path,
        signals=[
            {
                "ticker": "AAPL",
                "target_weight": 0.95,
                "sleeve": "sleeve_trend",
            }
        ],
        strategy_identity={
            "execution_target_strategy_id": "growth_engine_v4",
            "live_pilot_governed_strategy_id": "caerus_orion",
            "live_pilot_tracks_approved_strategy": False,
        },
    )

    plan = _build(
        tmp_path,
        payload_path,
        prices={"AAPL": 100.0},
        approved_sleeve="orion",
        lane="live_pilot",
    )

    assert plan["status"] == "BLOCKED"
    assert plan["reason_code"] == "strategy_identity_mismatch"
    validation = plan["block_diagnostics"]["strategy_identity_validation"]
    assert validation["reason_code"] == "live_pilot_approved_strategy_target_mismatch"


def test_paper_recovery_policy_is_rejected_as_downstream_target_substitution(
    tmp_path: Path,
) -> None:
    identity = {
        "live_strategy_id": "growth_engine_v4",
        "execution_target_strategy_id": "growth_engine_v4",
        "paper_governed_strategy_id": "caerus_polaris",
        "paper_mapping_status": "ENGINE_BASELINE_ALIAS",
    }
    monday_path = _bundle(
        tmp_path,
        trade_date="2026-07-06",
        signals=[
            {
                "ticker": "AAPL",
                "target_weight": 0.95,
                "sleeve": "sleeve_trend",
            },
            {"ticker": "CASH", "target_weight": 0.05},
        ],
        strategy_identity=identity,
    )
    current_path = _bundle(
        tmp_path,
        trade_date="2026-07-08",
        signals=[
            {
                "ticker": "MSFT",
                "target_weight": 0.95,
                "sleeve": "sleeve_trend",
            },
            {"ticker": "CASH", "target_weight": 0.05},
        ],
        strategy_identity=identity,
    )
    assert monday_path.exists()
    config_path = tmp_path / "paper_recovery_policy.json"
    config_path.write_text(
        json.dumps(
            {
                "enabled": True,
                "policy_id": "weekly_rotation_guard_v1",
                "approval_status": "APPROVED_FOR_PAPER_OBSERVATION",
                "paper_only": True,
                "live_eligible": False,
            }
        ),
        encoding="utf-8",
    )
    dates = pd.bdate_range("2026-06-01", periods=25)
    factors = pd.DataFrame(index=dates)
    factors["SPY"] = [100 + index * 0.2 for index in range(len(dates))]
    factors["RSP"] = [100 + index * 0.3 for index in range(len(dates))]
    factors["QQQ"] = [110 - index * 0.3 for index in range(len(dates))]
    factors["SMH"] = [120 - index * 0.8 for index in range(len(dates))]
    factors["MTUM"] = [105 - index * 0.4 for index in range(len(dates))]

    paper_plan = _build(
        tmp_path,
        current_path,
        prices={"AAPL": 100.0},
        approved_sleeve="caerus_polaris",
        lane="paper",
        recovery_policy="weekly_rotation_guard_v1",
        recovery_policy_config=config_path,
        factor_history_fetcher=lambda _start, _end: factors,
        output_dir=tmp_path / "outputs" / "paper_lane" / "plans",
    )
    assert paper_plan["status"] == "BLOCKED"
    assert paper_plan["reason_code"] == "paper_downstream_target_substitution_disabled"

    live_plan = _build(
        tmp_path,
        current_path,
        prices={"MSFT": 100.0},
        approved_sleeve="orion",
        lane="live_pilot",
        recovery_policy="weekly_rotation_guard_v1",
        recovery_policy_config=config_path,
    )
    assert live_plan["status"] == "BLOCKED"
    assert live_plan["reason_code"] == "paper_recovery_policy_wrong_lane"


def test_sleeve_is_stamped_approved_with_provenance(tmp_path: Path) -> None:
    payload_path = _bundle(
        tmp_path,
        signals=[{"ticker": "SPG", "target_weight": 0.5, "sleeve": "sleeve_trend"}],
    )
    plan = _build(tmp_path, payload_path, prices={"SPG": 50.0})
    row = plan["target_portfolio"][0]
    assert row["sleeve"] == "growth_engine_v4"
    assert row["source_signal_sleeve"] == "sleeve_trend"


def test_cash_target_weight_is_top_level_and_carried(tmp_path: Path) -> None:
    payload_path = _bundle(
        tmp_path,
        signals=[
            {"ticker": "CASH", "target_weight": 0.25},
            {"ticker": "AAPL", "target_weight": 0.75, "sleeve": "sleeve_quality"},
        ],
    )
    plan = _build(tmp_path, payload_path, prices={"AAPL": 100.0})
    assert plan["status"] == "READY_FOR_MANUAL_APPROVAL"
    # 0.25 explicit cash; position cap (10%) trims AAPL and adds the excess to cash.
    assert plan["cash_target_weight"] > 0.25
    assert "cash_target_weight" in plan


def test_unpriced_target_blocks_fail_closed(tmp_path: Path) -> None:
    payload_path = _bundle(
        tmp_path,
        signals=[
            {"ticker": "AAPL", "target_weight": 0.5, "sleeve": "sleeve_quality"},
            {"ticker": "MSFT", "target_weight": 0.5, "sleeve": "sleeve_trend"},
        ],
    )
    # Only AAPL is priced; MSFT has no payload entry_price and no yfinance open.
    plan = _build(tmp_path, payload_path, prices={"AAPL": 100.0})
    assert plan["status"] == "BLOCKED"
    assert plan["reason_code"] == "live_pilot_target_unpriced"
    assert "MSFT" in plan["block_diagnostics"]["unpriced_targets"]
    assert plan["target_portfolio"] == []


def test_unknown_layer_metadata_blocks_live(tmp_path: Path) -> None:
    payload_path = _bundle(
        tmp_path,
        signals=[{"ticker": "AAPL", "target_weight": 0.95, "sleeve": "mystery_sleeve"}],
        cash_target_weight=0.05,
    )
    plan = _build(tmp_path, payload_path, prices={"AAPL": 100.0})
    assert plan["status"] == "BLOCKED"
    assert plan["reason_code"] == "live_pilot_layer_unresolved"
    assert plan["block_diagnostics"]["unresolved_layer_labels"] == {
        "AAPL": ["mystery_sleeve"]
    }


def test_paper_and_live_share_identical_targets_and_explicit_cash(tmp_path: Path) -> None:
    signals = [
        {"ticker": "AAPL", "target_weight": 0.25, "sleeve": "sleeve_quality"},
        {"ticker": "MSFT", "target_weight": 0.20, "sleeve": "sleeve_trend"},
        {"ticker": "JNJ", "target_weight": 0.20, "sleeve": "sleeve_quality"},
        {"ticker": "PNC", "target_weight": 0.15, "sleeve": "sleeve_trend"},
        {"ticker": "SPG", "target_weight": 0.15, "sleeve": "sleeve_quality"},
        {"ticker": "CASH", "target_weight": 0.05, "sleeve": "core"},
    ]
    payload_path = _bundle(tmp_path, signals=signals, cash_target_weight=0.05)
    prices = {symbol: 100.0 for symbol in ("AAPL", "MSFT", "JNJ", "PNC", "SPG")}
    plan = _build(tmp_path, payload_path, prices=prices)
    assert plan["status"] == "READY_FOR_MANUAL_APPROVAL"

    from paper.paper_broker import load_targets

    paper_targets, paper_cash, _, _ = load_targets(
        str(payload_path.parent / "signals.json"), cash_target_weight_default=0.05
    )
    paper_weights = {
        str(row["ticker"]): float(row["target_weight"])
        for _, row in paper_targets.iterrows()
    }
    live_weights = {
        str(row["symbol"]): float(row["target_weight"])
        for row in plan["target_portfolio"]
    }
    assert live_weights == pytest.approx(paper_weights)
    assert max(live_weights.values()) <= 0.30
    assert float(plan["cash_target_weight"]) == pytest.approx(float(paper_cash))
    assert sum(live_weights.values()) + float(plan["cash_target_weight"]) == pytest.approx(1.0)


def test_missing_signals_source_blocks(tmp_path: Path) -> None:
    trade_date = "2026-06-22"
    bundle = tmp_path / "outputs" / "precompute" / trade_date
    bundle.mkdir(parents=True, exist_ok=True)
    payload_path = bundle / "planned_execution_payload.json"
    payload_path.write_text(json.dumps({"trade_date": trade_date, "trades": []}) + "\n", encoding="utf-8")
    plan = _build(tmp_path, payload_path, prices={})
    assert plan["status"] == "BLOCKED"
    assert plan["reason_code"] == "live_pilot_signals_source_missing"


def test_nonpositive_cap_and_max_orders_rejected(tmp_path: Path) -> None:
    payload_path = _bundle(
        tmp_path, signals=[{"ticker": "AAPL", "target_weight": 1.0, "sleeve": "sleeve_quality"}]
    )
    with pytest.raises(ValueError, match="capital_cap must be > 0"):
        _build(tmp_path, payload_path, prices={"AAPL": 100.0}, capital_cap=0)
    with pytest.raises(ValueError, match="max_orders must be > 0"):
        _build(tmp_path, payload_path, prices={"AAPL": 100.0}, max_orders=0)


def test_max_orders_above_one_is_accepted(tmp_path: Path) -> None:
    payload_path = _bundle(
        tmp_path,
        signals=[
            {"ticker": "AAPL", "target_weight": 0.5, "sleeve": "sleeve_quality"},
            {"ticker": "MSFT", "target_weight": 0.5, "sleeve": "sleeve_trend"},
        ],
    )
    # The old builder raised for max_orders != 1; the full rebalancer must accept it.
    plan = _build(tmp_path, payload_path, prices={"AAPL": 100.0, "MSFT": 200.0}, max_orders=25)
    assert plan["status"] == "READY_FOR_MANUAL_APPROVAL"
    assert plan["max_orders"] == 25
    assert len(plan["target_portfolio"]) == 2


def test_plan_files_written_and_executor_consumable_shape(tmp_path: Path) -> None:
    payload_path = _bundle(
        tmp_path, signals=[{"ticker": "AAPL", "target_weight": 1.0, "sleeve": "sleeve_quality"}]
    )
    plan = _build(tmp_path, payload_path, prices={"AAPL": 100.0})
    json_path = Path(str(plan["json_path"]))
    md_path = Path(str(plan["markdown_path"]))
    assert json_path.exists() and md_path.exists()
    written = json.loads(json_path.read_text(encoding="utf-8"))
    assert written["approved_sleeve"] == "growth_engine_v4"
    assert written["cash_target_weight"] == plan["cash_target_weight"]
    row = written["target_portfolio"][0]
    # Fields the executor's _target_rows_from_plan / _plan_rows_by_symbol read.
    for key in ("symbol", "target_weight", "price", "sleeve", "order_type"):
        assert key in row
    assert "CAERUS_LIVE_PILOT_DRY_RUN=0" in written["required_live_command"]
    assert written["execution_lane"] == "live_pilot"
    assert "ALPACA_BASE_URL=https://api.alpaca.markets" in written["required_live_command"]
