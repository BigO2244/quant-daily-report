"""Rebuild positions and cash deterministically from the trade ledger."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def _resolve_starting_cash(default_cash: float = 100000.0) -> float:
    cfg_path = Path("paper/config_paper.json")
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        for key in ("initial_equity", "starting_equity", "starting_cash"):
            if key in cfg:
                return float(cfg[key])
    out = Path("outputs/ledger/cash_start.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        logger.warning("[POSITIONS] using fallback cash_start=%s", default_cash)
        pd.DataFrame([{"date": "", "starting_cash": default_cash}]).to_csv(out, index=False)
    return float(pd.read_csv(out)["starting_cash"].iloc[0])


def rebuild_positions_from_ledger(ledger: pd.DataFrame, asof_date: str) -> dict[str, Any]:
    """Apply average-cost accounting through asof_date trades."""
    if ledger.empty:
        return {"positions": pd.DataFrame(columns=["ticker", "shares", "avg_cost", "realized_pnl", "sleeve"]), "cash": _resolve_starting_cash()}

    led = ledger.copy()
    led["trade_date"] = pd.to_datetime(led["trade_date"])
    led = led[led["trade_date"] <= pd.to_datetime(asof_date)].copy()
    led = led.sort_values(["trade_date", "timestamp_et", "order_id"])

    pos: dict[str, dict[str, float | str]] = {}
    cash = _resolve_starting_cash()

    for _, row in led.iterrows():
        t = str(row["ticker"]).upper()
        side = str(row["side"]).upper()
        qty = float(row["quantity"])
        px = float(row["fill_price"])
        fee = float(row.get("fees", 0.0) or 0.0)
        sleeve = str(row.get("sleeve", ""))
        item = pos.setdefault(t, {"ticker": t, "shares": 0.0, "avg_cost": 0.0, "realized_pnl": 0.0, "sleeve": sleeve})
        item["sleeve"] = sleeve or item.get("sleeve", "")

        if side == "BUY":
            new_shares = float(item["shares"]) + qty
            if new_shares > 0:
                item["avg_cost"] = ((float(item["shares"]) * float(item["avg_cost"])) + qty * px) / new_shares
            item["shares"] = new_shares
            cash -= (qty * px) + fee
        elif side == "SELL":
            sell_qty = min(qty, float(item["shares"]))
            item["realized_pnl"] = float(item["realized_pnl"]) + sell_qty * (px - float(item["avg_cost"]))
            item["shares"] = float(item["shares"]) - sell_qty
            if abs(float(item["shares"])) <= 1e-12:
                item["shares"] = 0.0
                item["avg_cost"] = 0.0
            cash += (sell_qty * px) - fee

    positions = pd.DataFrame(pos.values())
    if positions.empty:
        positions = pd.DataFrame(columns=["ticker", "shares", "avg_cost", "realized_pnl", "sleeve"])
    if not positions.empty:
        positions = positions.sort_values("ticker").reset_index(drop=True)
    return {"positions": positions, "cash": float(cash)}


def write_position_outputs(positions: pd.DataFrame, cash: float, asof_date: str) -> dict[str, str]:
    out_dir = Path("outputs/ledger")
    out_dir.mkdir(parents=True, exist_ok=True)
    pos_path = out_dir / f"positions_{asof_date}.csv"
    holdings_path = out_dir / f"holdings_{asof_date}.csv"
    cash_path = out_dir / f"cash_{asof_date}.json"

    positions.to_csv(pos_path, index=False)
    holdings = positions[["ticker", "shares", "avg_cost", "sleeve"]].copy() if not positions.empty else pd.DataFrame(columns=["ticker", "shares", "avg_cost", "sleeve"])
    holdings = holdings[holdings["shares"].abs() > 1e-12]
    holdings.to_csv(holdings_path, index=False)
    cash_path.write_text(json.dumps({"date": asof_date, "cash_end": float(cash), "num_positions": int(len(holdings))}, indent=2) + "\n", encoding="utf-8")

    return {"positions": str(pos_path), "holdings": str(holdings_path), "cash": str(cash_path)}
