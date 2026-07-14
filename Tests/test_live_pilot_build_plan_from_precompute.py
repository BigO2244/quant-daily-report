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
        json.dumps({"snapshot_date": trade_date, "signals": signals}, indent=2, sort_keys=True) + "\n",
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
