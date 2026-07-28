"""Decision-time target-book diagnostics.

The metrics in this module describe desired portfolio changes. They are not
executed turnover and must never be presented as broker fills.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "caerus_target_book_metrics_v1"


def _to_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def target_weights(payload: Mapping[str, Any] | None) -> dict[str, float]:
    payload = payload or {}
    source = payload.get("signals")
    if not isinstance(source, list):
        source = payload.get("weights")
    rows: dict[str, float] = {}
    for item in source if isinstance(source, list) else []:
        if not isinstance(item, Mapping):
            continue
        ticker = str(item.get("ticker") or item.get("symbol") or "").strip().upper()
        weight = _to_float(item.get("target_weight"))
        if ticker and weight is not None and weight > 0.0:
            rows[ticker] = rows.get(ticker, 0.0) + weight
    if "CASH" not in rows:
        rows["CASH"] = max(0.0, 1.0 - sum(rows.values()))
    total = sum(rows.values())
    if total <= 0.0:
        return {"CASH": 1.0}
    return {ticker: weight / total for ticker, weight in sorted(rows.items())}


def _book_stats(weights: Mapping[str, float]) -> dict[str, Any]:
    equity = [
        float(weight)
        for ticker, weight in weights.items()
        if ticker != "CASH" and float(weight) > 0.0
    ]
    equity.sort(reverse=True)
    hhi = sum(weight * weight for weight in equity)
    return {
        "equity_name_count": len(equity),
        "cash_weight": round(float(weights.get("CASH", 0.0)), 10),
        "gross_equity_weight": round(sum(equity), 10),
        "max_equity_weight": round(equity[0], 10) if equity else 0.0,
        "top3_equity_weight": round(sum(equity[:3]), 10),
        "hhi": round(hhi, 10),
        "effective_n": round(1.0 / hhi, 10) if hhi > 0.0 else None,
    }


def build_target_book_metrics(
    *,
    current_payload: Mapping[str, Any],
    current_source: str,
    previous_payload: Mapping[str, Any] | None = None,
    previous_source: str | None = None,
) -> dict[str, Any]:
    current = target_weights(current_payload)
    previous = target_weights(previous_payload) if previous_payload is not None else None
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "metric_scope": "desired_target_book_not_executed_fills",
        "current_source": current_source,
        "previous_source": previous_source,
        "current": _book_stats(current),
        "previous": _book_stats(previous) if previous is not None else None,
        "desired_one_way_turnover_pct": None,
        "desired_gross_l1_turnover_pct": None,
        "name_overlap_count": None,
        "status": "NO_PRIOR_TARGET",
    }
    if previous is None:
        return payload
    names = sorted(set(current).union(previous))
    gross_l1 = sum(abs(current.get(name, 0.0) - previous.get(name, 0.0)) for name in names)
    current_equity = {name for name, value in current.items() if name != "CASH" and value > 0.0}
    previous_equity = {name for name, value in previous.items() if name != "CASH" and value > 0.0}
    payload.update(
        {
            "desired_one_way_turnover_pct": round(0.5 * gross_l1, 10),
            "desired_gross_l1_turnover_pct": round(gross_l1, 10),
            "name_overlap_count": len(current_equity.intersection(previous_equity)),
            "status": "AVAILABLE",
        }
    )
    return payload


def latest_prior_signals(
    *,
    root: Path,
    trade_date: str,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        cutoff = str(Path(str(trade_date)).name)
    except Exception:
        cutoff = str(trade_date)
    candidates: list[Path] = []
    if root.exists():
        for path in root.glob("*/signals.json"):
            if path.parent.name < cutoff:
                candidates.append(path)
    for path in sorted(candidates, key=lambda item: item.parent.name, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload, str(path)
    return None, None
