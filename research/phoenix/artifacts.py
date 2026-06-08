from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from research.phoenix.features import build_phoenix_feature_frame
from research.phoenix.strategy import (
    PHOENIX_STRATEGY_ID,
    PhoenixConfig,
    build_phoenix_snapshot,
    run_phoenix_backtest,
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def write_phoenix_research_artifacts(
    *,
    panel: pd.DataFrame,
    trade_date: str,
    start_date: str,
    output_dir: str | Path = "outputs/research/phoenix",
    config: PhoenixConfig | None = None,
) -> dict[str, Any]:
    cfg = config or PhoenixConfig()
    root = Path(output_dir)
    dated_dir = root / str(pd.Timestamp(trade_date).date())
    performance_dir = root / "performance"
    dated_dir.mkdir(parents=True, exist_ok=True)
    performance_dir.mkdir(parents=True, exist_ok=True)

    features = build_phoenix_feature_frame(panel)
    snapshot = build_phoenix_snapshot(panel, trade_date=trade_date, config=cfg)
    backtest = run_phoenix_backtest(panel, start_date=start_date, end_date=trade_date, config=cfg)

    rank_table = pd.DataFrame(snapshot.get("rank_table") or [])
    signal_frame_path = dated_dir / "phoenix_signal_frame.parquet"
    rank_table_path = dated_dir / "phoenix_rank_table.csv"
    holdings_path = dated_dir / "phoenix_holdings.json"
    summary_path = dated_dir / "phoenix_backtest_summary.json"
    decision_trace_path = dated_dir / "phoenix_decision_trace.json"
    attribution_inputs_path = dated_dir / "phoenix_attribution_inputs.json"
    nav_path = performance_dir / "phoenix_nav_series.csv"
    performance_summary_path = performance_dir / "phoenix_summary.json"

    features.to_parquet(signal_frame_path, index=False)
    rank_table.to_csv(rank_table_path, index=False)
    _write_json(holdings_path, snapshot)
    _write_json(summary_path, backtest["summary"])
    _write_json(decision_trace_path, build_decision_trace(snapshot=snapshot))
    _write_json(attribution_inputs_path, build_attribution_inputs(snapshot=snapshot))
    backtest["nav"].to_csv(nav_path, index=False)
    _write_json(
        performance_summary_path,
        {
            "schema_version": "phoenix_performance_summary_v1",
            "strategy_id": PHOENIX_STRATEGY_ID,
            "strategy_slug": PHOENIX_STRATEGY_ID,
            "trade_date": str(pd.Timestamp(trade_date).date()),
            "governance_label": "RESEARCH_ONLY",
            "execution_impact": "NON_EXECUTIONAL",
            "summary": backtest["summary"],
        },
    )
    return {
        "schema_version": "phoenix_artifact_manifest_v1",
        "strategy_id": PHOENIX_STRATEGY_ID,
        "strategy_slug": PHOENIX_STRATEGY_ID,
        "trade_date": str(pd.Timestamp(trade_date).date()),
        "status": snapshot.get("status"),
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "artifacts": {
            "signal_frame": str(signal_frame_path),
            "rank_table": str(rank_table_path),
            "holdings": str(holdings_path),
            "backtest_summary": str(summary_path),
            "decision_trace": str(decision_trace_path),
            "attribution_inputs": str(attribution_inputs_path),
            "nav_series": str(nav_path),
            "performance_summary": str(performance_summary_path),
        },
    }


def build_decision_trace(*, snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "phoenix_decision_trace_v1",
        "strategy_id": PHOENIX_STRATEGY_ID,
        "strategy_slug": PHOENIX_STRATEGY_ID,
        "trade_date": snapshot.get("trade_date"),
        "effective_trade_date": snapshot.get("effective_trade_date"),
        "status": snapshot.get("status"),
        "reason_code": snapshot.get("reason_code"),
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "selected_count": int(snapshot.get("holdings_count") or 0),
        "selected": list(snapshot.get("holdings") or []),
        "signal_diagnostics": dict(snapshot.get("signal_diagnostics") or {}),
        "non_goals": [
            "no broker submission",
            "no paper execution changes",
            "no live execution changes",
            "no Polaris/Orion/Lyra behavior changes",
        ],
    }


def build_attribution_inputs(*, snapshot: dict[str, Any]) -> dict[str, Any]:
    holdings = list(snapshot.get("holdings") or [])
    return {
        "schema_version": "phoenix_attribution_inputs_v1",
        "strategy_id": PHOENIX_STRATEGY_ID,
        "strategy_slug": PHOENIX_STRATEGY_ID,
        "trade_date": snapshot.get("trade_date"),
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "return_convention": "weights_as_of_t",
        "holdings": holdings,
        "weights": dict(snapshot.get("target_weights") or {}),
        "cash_weight": snapshot.get("cash_weight"),
        "signal_components": [
            "return_5d",
            "return_10d",
            "volume_shock_20d",
            "atr_range_shock",
            "rsi_2",
            "rsi_5",
            "phoenix_score",
        ],
        "selected_signal_rows": [
            {
                "ticker": item.get("ticker"),
                "target_weight": item.get("target_weight"),
                "phoenix_score": item.get("phoenix_score"),
                "return_5d": item.get("return_5d"),
                "return_10d": item.get("return_10d"),
                "volume_shock_20d": item.get("volume_shock_20d"),
                "atr_range_shock": item.get("atr_range_shock"),
                "rsi_2": item.get("rsi_2"),
                "rsi_5": item.get("rsi_5"),
            }
            for item in holdings
        ],
    }
