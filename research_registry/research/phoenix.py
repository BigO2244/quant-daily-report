from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from research.phoenix.strategy import (
    PHOENIX_STRATEGY_ID,
    PhoenixConfig,
    build_phoenix_snapshot,
    run_phoenix_backtest,
)
from research_registry.research.model_quality_common import (
    md_join,
    model_quality_dir,
    normalize_date,
    write_json,
    write_text,
)

SCHEMA_VERSION = "caerus_phoenix_model_quality_research_v1"


def build_phoenix_model_quality_research(
    *,
    panel: pd.DataFrame,
    trade_date: str,
    start_date: str = "2014-01-01",
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
    config: PhoenixConfig | None = None,
    write: bool = True,
) -> dict[str, Any]:
    target = normalize_date(trade_date)
    cfg = config or PhoenixConfig()
    snapshot = build_phoenix_snapshot(panel, trade_date=target, config=cfg)
    backtest = run_phoenix_backtest(panel, start_date=start_date, end_date=target, config=cfg)
    used_panel = _panel_used_through_date(panel, target)
    summary = dict(backtest.get("summary") or {})
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": target,
        "strategy_id": PHOENIX_STRATEGY_ID,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "status": snapshot.get("status"),
        "active": bool(snapshot.get("holdings")),
        "reason_codes": list(snapshot.get("reason_codes") or ([snapshot.get("reason_code")] if snapshot.get("reason_code") else ["ok"])),
        "target_candidates": list(snapshot.get("holdings") or []),
        "target_weights": dict(snapshot.get("target_weights") or {}),
        "cash_weight": snapshot.get("cash_weight"),
        "rank_table": list(snapshot.get("rank_table") or []),
        "signal_diagnostics": dict(snapshot.get("signal_diagnostics") or {}),
        "data_coverage": {
            **dict(snapshot.get("data_coverage") or {}),
            "panel_rows_through_trade_date": int(len(used_panel)),
            "panel_symbols_through_trade_date": int(used_panel["ticker"].nunique()) if not used_panel.empty and "ticker" in used_panel.columns else 0,
            "panel_min_date_through_trade_date": str(pd.to_datetime(used_panel["date"]).min().date()) if not used_panel.empty and "date" in used_panel.columns else None,
            "panel_max_date_through_trade_date": str(pd.to_datetime(used_panel["date"]).max().date()) if not used_panel.empty and "date" in used_panel.columns else None,
        },
        "backtest_summary": summary,
        "research_limits": [
            "research_only_no_broker_submission",
            "selection_uses_rows_on_or_before_trade_date",
            "forward_returns_used_only_for_backtest_evaluation",
        ],
    }
    if write:
        out_dir = model_quality_dir(repo_root, target, output_root)
        write_json(out_dir / "phoenix_research.json", payload)
        write_text(out_dir / "phoenix_research.md", render_markdown(payload))
    return payload


def _panel_used_through_date(panel: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    if panel is None or panel.empty or "date" not in panel.columns:
        return pd.DataFrame()
    frame = panel.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"])
    return frame[frame["date"] <= pd.Timestamp(trade_date)].copy()


def render_markdown(payload: dict[str, Any]) -> str:
    candidates = payload.get("target_candidates") or []
    summary = payload.get("backtest_summary") or {}
    lines = [
        f"# Phoenix Research - {payload.get('date')}",
        "",
        f"- Status: {payload.get('status')}",
        f"- Active: {payload.get('active')}",
        f"- Reason codes: {md_join(payload.get('reason_codes') or [])}",
        f"- Governance: {payload.get('governance_label')} / {payload.get('execution_impact')}",
        "",
        "## Backtest Context",
        "",
        f"- CAGR: {summary.get('cagr')}",
        f"- Sharpe: {summary.get('sharpe')}",
        f"- Max drawdown: {summary.get('max_drawdown')}",
        f"- Average turnover: {summary.get('avg_turnover')}",
        "",
        "## Target Candidates",
        "",
        "| Ticker | Weight | Score | Return 5d | Volume shock | Reasons |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in candidates:
        lines.append(
            f"| {row.get('ticker')} | {row.get('target_weight')} | {row.get('phoenix_score')} | "
            f"{row.get('return_5d')} | {row.get('volume_shock_20d')} | {md_join(row.get('reason_codes') or [])} |"
        )
    if not candidates:
        lines.append("| none | 0 | n/a | n/a | n/a | no active crisis-reversal candidates |")
    lines.extend(["", "## Research Limits", ""])
    for item in payload.get("research_limits") or []:
        lines.append(f"- {item}")
    return "\n".join(lines)


def print_summary(payload: dict[str, Any]) -> str:
    return json.dumps(
        {
            "date": payload.get("date"),
            "strategy_id": payload.get("strategy_id"),
            "status": payload.get("status"),
            "active": payload.get("active"),
            "reason_codes": payload.get("reason_codes"),
        },
        sort_keys=True,
    )
