"""Temporary, governed paper-only drawdown recovery policy.

This module derives a weekly paper observation target from stored precompute
signals. It does not alter shared precompute artifacts and is ineligible for the
live-pilot lane.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd


POLICY_ID = "weekly_rotation_guard_v1"
SCHEMA_VERSION = "caerus_paper_recovery_policy_v1"
FACTOR_SYMBOLS = ("SPY", "RSP", "QQQ", "SMH", "MTUM")


FactorFetcher = Callable[[str, str], pd.DataFrame]


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def validate_recovery_config(
    config: Mapping[str, Any],
    *,
    requested_policy: str,
) -> dict[str, Any]:
    reasons: list[str] = []
    if not bool(config.get("enabled")):
        reasons.append("paper_recovery_policy_disabled")
    if str(config.get("policy_id") or "") != str(requested_policy):
        reasons.append("paper_recovery_policy_id_mismatch")
    if str(config.get("approval_status") or "") != "APPROVED_FOR_PAPER_OBSERVATION":
        reasons.append("paper_recovery_policy_not_approved_for_observation")
    if config.get("paper_only") is not True:
        reasons.append("paper_recovery_policy_scope_not_paper_only")
    if config.get("live_eligible") is not False:
        reasons.append("paper_recovery_policy_live_scope_ambiguous")
    return {
        "status": "PASS" if not reasons else "BLOCK",
        "reason_codes": reasons,
        "policy_id": requested_policy,
        "approval_status": config.get("approval_status"),
        "paper_only": config.get("paper_only"),
        "live_eligible": config.get("live_eligible"),
    }


def _first_weekly_signal_path(
    *,
    precompute_root: Path,
    trade_date: str,
) -> Path | None:
    target = pd.Timestamp(trade_date).normalize()
    target_week = target.isocalendar()[:2]
    candidates: list[tuple[pd.Timestamp, Path]] = []
    for path in precompute_root.glob("*/signals.json"):
        try:
            date = pd.Timestamp(path.parent.name).normalize()
        except ValueError:
            continue
        if date <= target and date.isocalendar()[:2] == target_week:
            candidates.append((date, path))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def fetch_factor_closes_yfinance(start: str, end: str) -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(
        list(FACTOR_SYMBOLS),
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    close = raw.get("Close") if isinstance(raw.columns, pd.MultiIndex) else raw
    if close is None or close.empty:
        raise RuntimeError("factor proxy download returned no adjusted closes")
    if isinstance(close, pd.Series):
        close = close.to_frame(name=FACTOR_SYMBOLS[0])
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
    return close


def factor_rotation_scale(
    *,
    decision_date: str,
    closes: pd.DataFrame,
    lookback: int = 10,
    warn_threshold: float = -0.025,
    lock_threshold: float = -0.05,
) -> tuple[float, dict[str, Any]]:
    date = pd.Timestamp(decision_date).normalize()
    prior = closes.loc[closes.index < date].sort_index()
    required = set(FACTOR_SYMBOLS)
    if len(prior) < lookback + 1 or not required.issubset(prior.columns):
        raise ValueError("insufficient strictly-prior factor proxy history")
    latest = pd.to_numeric(prior.iloc[-1], errors="coerce")
    start = pd.to_numeric(prior.iloc[-(lookback + 1)], errors="coerce")
    returns = (latest / start - 1.0).replace([math.inf, -math.inf], pd.NA)
    factor_returns = returns.reindex(["QQQ", "SMH", "MTUM"]).dropna()
    broad_returns = returns.reindex(["SPY", "RSP"]).dropna()
    if len(factor_returns) != 3 or len(broad_returns) != 2:
        raise ValueError("factor proxy history contains non-finite values")
    factor_median = float(factor_returns.median())
    broad_median = float(broad_returns.median())
    relative = factor_median - broad_median
    lagging_count = int(sum(float(value) < broad_median for value in factor_returns))
    scale = 1.0
    reason = "factor_rotation_clear"
    if relative <= lock_threshold and lagging_count >= 2:
        scale = 0.25
        reason = "factor_rotation_lock"
    elif relative <= warn_threshold and lagging_count >= 2:
        scale = 0.50
        reason = "factor_rotation_warn"
    return scale, {
        "decision_date": str(date.date()),
        "lookback_trading_days": lookback,
        "uses_strictly_prior_closes": True,
        "latest_input_date": str(prior.index[-1].date()),
        "factor_returns": {
            key: round(float(value), 10) for key, value in factor_returns.items()
        },
        "broad_returns": {
            key: round(float(value), 10) for key, value in broad_returns.items()
        },
        "factor_median_return": round(factor_median, 10),
        "broad_median_return": round(broad_median, 10),
        "factor_relative_return": round(relative, 10),
        "lagging_factor_count": lagging_count,
        "exposure_scale": scale,
        "reason_code": reason,
    }


def derive_weekly_rotation_guard_payload(
    *,
    precompute_root: Path,
    trade_date: str,
    factor_fetcher: FactorFetcher = fetch_factor_closes_yfinance,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_path = _first_weekly_signal_path(
        precompute_root=precompute_root,
        trade_date=trade_date,
    )
    if source_path is None:
        raise ValueError("no stored signal decision available in current ISO week")
    source = _read_json(source_path)
    decision_date = source_path.parent.name
    start = str((pd.Timestamp(decision_date) - pd.Timedelta(days=45)).date())
    end = decision_date
    closes = factor_fetcher(start, end)
    scale, guard = factor_rotation_scale(
        decision_date=decision_date,
        closes=closes,
    )

    transformed_rows: list[dict[str, Any]] = []
    invested = 0.0
    for raw in source.get("signals") or []:
        if not isinstance(raw, Mapping):
            continue
        ticker = str(raw.get("ticker") or "").strip().upper()
        try:
            original_weight = float(raw.get("target_weight") or 0.0)
        except (TypeError, ValueError):
            continue
        if ticker == "CASH" or original_weight <= 0.0:
            continue
        row = dict(raw)
        row["source_target_weight"] = original_weight
        row["target_weight"] = round(original_weight * scale, 10)
        transformed_rows.append(row)
        invested += float(row["target_weight"])
    transformed_rows.append(
        {
            "ticker": "CASH",
            "target_weight": round(max(0.0, 1.0 - invested), 10),
            "sleeve": "cash",
            "source_target_weight": next(
                (
                    float(row.get("target_weight") or 0.0)
                    for row in source.get("signals") or []
                    if isinstance(row, Mapping)
                    and str(row.get("ticker") or "").strip().upper() == "CASH"
                ),
                max(
                    0.0,
                    1.0
                    - sum(
                        float(row.get("target_weight") or 0.0)
                        for row in source.get("signals") or []
                        if isinstance(row, Mapping)
                        and str(row.get("ticker") or "").strip().upper() != "CASH"
                    ),
                ),
            ),
        }
    )
    meta = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "paper_only": True,
        "live_eligible": False,
        "requested_trade_date": trade_date,
        "weekly_decision_date": decision_date,
        "weekly_source_path": str(source_path),
        "factor_guard": guard,
        "exposure_scale": scale,
        "target_cash_weight": round(max(0.0, 1.0 - invested), 10),
    }
    derived = dict(source)
    derived["snapshot_date"] = trade_date
    derived["signals"] = transformed_rows
    derived["cash_target_weight"] = meta["target_cash_weight"]
    derived["paper_recovery_policy"] = meta
    return derived, meta
