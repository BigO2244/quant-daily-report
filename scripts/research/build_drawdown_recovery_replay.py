"""Build a point-in-time-honest drawdown recovery replay.

Research-only. The script consumes stored precompute signals and a stored close
panel. It never imports broker, execution, scheduler, or production allocation
code.

The archive does not retain the post-July-7 daily pre-concentration allocator
books. Therefore the broad counterfactual is deliberately limited to the last
stored broad target book. The artifact marks that limitation explicitly instead
of presenting a reconstructed dynamic broad book as decision-grade evidence.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd


SCHEMA_VERSION = "caerus_drawdown_recovery_replay_v1"
DEFAULT_SIGNALS_ROOT = Path("outputs/precompute")
DEFAULT_PRICE_PANEL = Path("outputs/research/flow_detection_v1/price_panel.parquet")
DEFAULT_FACTOR_PANEL = Path("outputs/research/drawdown_recovery/factor_proxy_panel.parquet")
DEFAULT_OUTPUT_ROOT = Path("outputs/research/drawdown_recovery")
DEFAULT_START = "2026-05-12"
DEFAULT_END = "2026-07-27"
DEFAULT_COST_BPS = 10.0
FACTOR_PROXIES = ("SPY", "RSP", "QQQ", "SMH", "MTUM")


@dataclass(frozen=True)
class Decision:
    trade_date: pd.Timestamp
    weights: pd.Series
    source_path: str
    source_sha256: str


@dataclass(frozen=True)
class ReplayPolicy:
    policy_id: str
    description: str
    decision_fn: Callable[[pd.Timestamp, dict[pd.Timestamp, Decision], pd.DataFrame], pd.Series | None]
    evidence_class: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object: {path}")
    return payload


def _normalise_weights(payload: dict[str, Any]) -> pd.Series:
    rows: dict[str, float] = {}
    for row in payload.get("signals") or []:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        try:
            weight = float(row.get("target_weight") or 0.0)
        except (TypeError, ValueError):
            continue
        if not ticker or not math.isfinite(weight) or weight <= 0.0:
            continue
        rows[ticker] = rows.get(ticker, 0.0) + weight
    weights = pd.Series(rows, dtype=float)
    if "CASH" not in weights.index:
        weights.loc["CASH"] = max(0.0, 1.0 - float(weights.sum()))
    total = float(weights.sum())
    if total <= 0.0:
        return pd.Series({"CASH": 1.0}, dtype=float)
    return (weights / total).sort_values(ascending=False)


def discover_decisions(
    repo: Path,
    *,
    signals_root: Path = DEFAULT_SIGNALS_ROOT,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
) -> tuple[dict[pd.Timestamp, Decision], list[dict[str, Any]]]:
    root = repo / signals_root
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    decisions: dict[pd.Timestamp, Decision] = {}
    inventory: list[dict[str, Any]] = []
    if not root.exists():
        return decisions, inventory

    for dated_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        try:
            trade_date = pd.Timestamp(dated_dir.name).normalize()
        except ValueError:
            continue
        if trade_date < start_ts or trade_date > end_ts:
            continue
        path = dated_dir / "signals.json"
        row: dict[str, Any] = {
            "trade_date": str(trade_date.date()),
            "source_path": str(path.relative_to(repo)),
            "exists": path.exists(),
        }
        if not path.exists():
            inventory.append(row)
            continue
        try:
            payload = _read_json(path)
            weights = _normalise_weights(payload)
        except Exception as exc:
            row["read_error"] = str(exc)
            inventory.append(row)
            continue
        equity_weights = weights.drop(labels=["CASH"], errors="ignore")
        identity = payload.get("strategy_identity") or {}
        row.update(
            {
                "sha256": _sha256(path),
                "equity_name_count": int((equity_weights > 1e-12).sum()),
                "cash_weight": round(float(weights.get("CASH", 0.0)), 10),
                "max_equity_weight": round(float(equity_weights.max()), 10)
                if not equity_weights.empty
                else 0.0,
                "live_strategy_id": identity.get("live_strategy_id"),
                "live_tracks_shadow_baseline": identity.get("live_tracks_shadow_baseline"),
            }
        )
        decisions[trade_date] = Decision(
            trade_date=trade_date,
            weights=weights,
            source_path=row["source_path"],
            source_sha256=row["sha256"],
        )
        inventory.append(row)
    return decisions, inventory


def _load_panel(path: Path) -> pd.DataFrame:
    panel = pd.read_parquet(path)
    required = {"date", "ticker", "close"}
    if not required.issubset(panel.columns):
        raise ValueError(f"price panel missing columns: {sorted(required - set(panel.columns))}")
    panel = panel[["date", "ticker", "close"]].copy()
    panel["date"] = pd.to_datetime(panel["date"]).dt.normalize()
    panel["ticker"] = panel["ticker"].astype(str).str.upper().str.strip()
    panel["close"] = pd.to_numeric(panel["close"], errors="coerce")
    return panel.dropna(subset=["date", "ticker", "close"])


def hydrate_factor_panel(
    path: Path,
    *,
    start: str,
    end: str,
) -> dict[str, Any]:
    """Hydrate adjusted-close factor proxies for research replay only."""
    import yfinance as yf

    raw = yf.download(
        list(FACTOR_PROXIES),
        start=str(pd.Timestamp(start).date()),
        end=str((pd.Timestamp(end) + pd.Timedelta(days=5)).date()),
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    close = raw.get("Close") if isinstance(raw.columns, pd.MultiIndex) else raw
    if close is None or close.empty:
        raise RuntimeError("factor proxy hydration returned no adjusted closes")
    if isinstance(close, pd.Series):
        close = close.to_frame(name=FACTOR_PROXIES[0])
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
    panel = (
        close.rename_axis(index="date", columns="ticker")
        .stack()
        .rename("close")
        .reset_index()
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(path, index=False)
    return {
        "path": str(path),
        "source": "yfinance_adjusted_close",
        "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "symbols": list(FACTOR_PROXIES),
        "date_start": str(panel["date"].min().date()),
        "date_end": str(panel["date"].max().date()),
        "sha256": _sha256(path),
    }


def load_close_matrix(
    repo: Path,
    price_panel: Path,
    *,
    factor_panel: Path | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    path = price_panel if price_panel.is_absolute() else repo / price_panel
    if not path.exists():
        return pd.DataFrame(), {
            "path": str(price_panel),
            "exists": False,
            "status": "MISSING",
        }
    try:
        panel = _load_panel(path)
    except ValueError as exc:
        return pd.DataFrame(), {
            "path": str(price_panel),
            "exists": True,
            "status": "INVALID",
            "reason": str(exc),
        }
    primary_date_end = panel["date"].max()
    factor_meta: dict[str, Any] = {"status": "NOT_PROVIDED"}
    if factor_panel is not None:
        factor_path = factor_panel if factor_panel.is_absolute() else repo / factor_panel
        if factor_path.exists():
            factor_rows = _load_panel(factor_path)
            factor_rows = factor_rows[factor_rows["date"] <= primary_date_end]
            panel = pd.concat(
                [
                    panel[~panel["ticker"].isin(set(FACTOR_PROXIES))],
                    factor_rows,
                ],
                ignore_index=True,
            )
            factor_meta = {
                "status": "AVAILABLE",
                "path": str(factor_path.relative_to(repo))
                if factor_path.is_relative_to(repo)
                else str(factor_path),
                "sha256": _sha256(factor_path),
                "symbols": sorted(
                    set(factor_rows["ticker"]).intersection(FACTOR_PROXIES)
                ),
            }
        else:
            factor_meta = {"status": "MISSING", "path": str(factor_path)}
    close = panel.pivot_table(
        index="date",
        columns="ticker",
        values="close",
        aggfunc="last",
    ).sort_index()
    return close, {
        "path": str(path.relative_to(repo)) if path.is_relative_to(repo) else str(path),
        "exists": True,
        "status": "AVAILABLE",
        "sha256": _sha256(path),
        "date_start": str(close.index.min().date()) if not close.empty else None,
        "date_end": str(close.index.max().date()) if not close.empty else None,
        "primary_date_end": str(primary_date_end.date()),
        "ticker_count": int(len(close.columns)),
        "role": "forward_return_and_strict_prior_close_guard_inputs",
        "factor_panel": factor_meta,
    }


def _normalise_target(weights: pd.Series | None) -> pd.Series | None:
    if weights is None:
        return None
    out = pd.to_numeric(weights, errors="coerce").fillna(0.0)
    out.index = pd.Index([str(value).strip().upper() for value in out.index])
    out = out.groupby(level=0).sum()
    out = out[out > 1e-12]
    total = float(out.sum())
    if total <= 0.0:
        return pd.Series({"CASH": 1.0}, dtype=float)
    if total > 1.0 + 1e-9:
        out = out / total
    if "CASH" not in out.index:
        out.loc["CASH"] = max(0.0, 1.0 - float(out.sum()))
    return out / float(out.sum())


def _observed_daily(
    date: pd.Timestamp,
    decisions: dict[pd.Timestamp, Decision],
    _close: pd.DataFrame,
) -> pd.Series | None:
    decision = decisions.get(date)
    return decision.weights.copy() if decision else None


def _observed_weekly(
    date: pd.Timestamp,
    decisions: dict[pd.Timestamp, Decision],
    _close: pd.DataFrame,
) -> pd.Series | None:
    decision = decisions.get(date)
    if decision is None:
        return None
    earlier_same_week = [
        dt
        for dt in decisions
        if dt < date and dt.isocalendar()[:2] == date.isocalendar()[:2]
    ]
    return None if earlier_same_week else decision.weights.copy()


def _last_broad_anchor(decisions: dict[pd.Timestamp, Decision], minimum_names: int = 15) -> Decision | None:
    eligible = [
        decision
        for decision in decisions.values()
        if int((decision.weights.drop(labels=["CASH"], errors="ignore") > 1e-12).sum())
        >= minimum_names
    ]
    return max(eligible, key=lambda item: item.trade_date) if eligible else None


def _rotation_guard_scale(
    *,
    date: pd.Timestamp,
    target: pd.Series,
    close: pd.DataFrame,
    lookback: int = 10,
    relative_warn: float = -0.025,
    relative_lock: float = -0.05,
    breadth_warn: float = 0.60,
) -> tuple[float, dict[str, Any]]:
    """Return exposure scale using only closes strictly before ``date``."""
    prior = close.loc[close.index < date]
    names = [
        name for name in target.index if name != "CASH" and name in close.columns
    ]
    meta: dict[str, Any] = {
        "date": str(date.date()),
        "lookback": lookback,
        "available_names": len(names),
        "scale": 1.0,
        "reason": "guard_clear",
    }
    factor_names = [
        name for name in ("QQQ", "SMH", "MTUM") if name in prior.columns
    ]
    broad_names = [name for name in ("SPY", "RSP") if name in prior.columns]
    if len(prior) >= lookback + 1 and len(factor_names) == 3 and broad_names:
        latest = prior.iloc[-1]
        start = prior.iloc[-(lookback + 1)]
        factor_returns = (
            latest.reindex(factor_names) / start.reindex(factor_names) - 1.0
        ).dropna()
        broad_returns = (
            latest.reindex(broad_names) / start.reindex(broad_names) - 1.0
        ).dropna()
        if len(factor_returns) == 3 and not broad_returns.empty:
            factor_median = float(factor_returns.median())
            broad_median = float(broad_returns.median())
            relative = factor_median - broad_median
            lagging_count = int(
                sum(float(value) < broad_median for value in factor_returns)
            )
            scale = 1.0
            reason = "guard_clear"
            if relative <= relative_lock and lagging_count >= 2:
                scale = 0.25
                reason = "factor_rotation_lock"
            elif relative <= relative_warn and lagging_count >= 2:
                scale = 0.50
                reason = "factor_rotation_warn"
            meta.update(
                {
                    "signal_source": "factor_proxies",
                    "factor_proxy_returns": {
                        key: round(float(value), 10)
                        for key, value in factor_returns.items()
                    },
                    "broad_proxy_returns": {
                        key: round(float(value), 10)
                        for key, value in broad_returns.items()
                    },
                    "factor_median_return": round(factor_median, 10),
                    "broad_median_return": round(broad_median, 10),
                    "factor_relative_return": round(relative, 10),
                    "lagging_factor_count": lagging_count,
                    "scale": scale,
                    "reason": reason,
                }
            )
            return scale, meta

    if (
        len(prior) < max(21, lookback + 1)
        or len(names) < 3
        or "SPY" not in prior.columns
    ):
        meta["reason"] = "insufficient_prior_data"
        return 1.0, meta

    latest = prior.iloc[-1]
    start = prior.iloc[-(lookback + 1)]
    cohort_returns = latest.reindex(names) / start.reindex(names) - 1.0
    cohort_returns = cohort_returns.replace([math.inf, -math.inf], pd.NA).dropna()
    spy_return = float(latest["SPY"] / start["SPY"] - 1.0)
    sma20 = prior[names].tail(20).mean()
    below_sma = (latest.reindex(names) < sma20).dropna()
    if cohort_returns.empty or below_sma.empty:
        meta["reason"] = "insufficient_prior_data"
        return 1.0, meta

    cohort_median = float(cohort_returns.median())
    relative = cohort_median - spy_return
    below_fraction = float(below_sma.mean())
    scale = 1.0
    reason = "guard_clear"
    if relative <= relative_lock and below_fraction >= breadth_warn:
        scale = 0.25
        reason = "cohort_rotation_lock"
    elif relative <= relative_warn and below_fraction >= breadth_warn:
        scale = 0.50
        reason = "cohort_rotation_warn"
    meta.update(
        {
            "cohort_median_return": round(cohort_median, 10),
            "spy_return": round(spy_return, 10),
            "cohort_relative_return": round(relative, 10),
            "below_sma20_fraction": round(below_fraction, 10),
            "scale": scale,
            "reason": reason,
            "signal_source": "target_cohort_fallback",
        }
    )
    return scale, meta


def _guarded_target(
    date: pd.Timestamp,
    decisions: dict[pd.Timestamp, Decision],
    close: pd.DataFrame,
) -> pd.Series | None:
    decision = decisions.get(date)
    if decision is None:
        return None
    target = decision.weights.copy()
    scale, _ = _rotation_guard_scale(date=date, target=target, close=close)
    equity = target.drop(labels=["CASH"], errors="ignore") * scale
    equity.loc["CASH"] = max(0.0, 1.0 - float(equity.sum()))
    return equity


def _guarded_weekly(
    date: pd.Timestamp,
    decisions: dict[pd.Timestamp, Decision],
    close: pd.DataFrame,
) -> pd.Series | None:
    target = _observed_weekly(date, decisions, close)
    if target is None:
        return None
    scale, _ = _rotation_guard_scale(date=date, target=target, close=close)
    equity = target.drop(labels=["CASH"], errors="ignore") * scale
    equity.loc["CASH"] = max(0.0, 1.0 - float(equity.sum()))
    return equity


def _replay_policy(
    *,
    policy: ReplayPolicy,
    decisions: dict[pd.Timestamp, Decision],
    close: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    cost_bps: float,
    initial_target: pd.Series | None = None,
    initial_decision_date: pd.Timestamp | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    dates = close.index[(close.index >= start) & (close.index <= end)]
    current = pd.Series({"CASH": 1.0}, dtype=float)
    rows: list[dict[str, Any]] = []
    nav = 1.0
    active = False
    for date in dates:
        next_dates = close.index[close.index > date]
        if len(next_dates) == 0:
            break
        next_date = next_dates[0]
        target = policy.decision_fn(date, decisions, close)
        if (
            initial_target is not None
            and initial_decision_date is not None
            and date == initial_decision_date
        ):
            target = initial_target
        target = _normalise_target(target)
        turnover = 0.0
        if target is not None:
            names = current.index.union(target.index)
            turnover = 0.5 * float(
                (
                    current.reindex(names).fillna(0.0)
                    - target.reindex(names).fillna(0.0)
                )
                .abs()
                .sum()
            )
            current = target
            active = True
        if not active:
            continue

        equity_names = [name for name in current.index if name != "CASH" and name in close.columns]
        start_prices = pd.to_numeric(close.loc[date, equity_names], errors="coerce")
        end_prices = pd.to_numeric(close.loc[next_date, equity_names], errors="coerce")
        asset_returns = (end_prices / start_prices - 1.0).replace(
            [math.inf, -math.inf], pd.NA
        )
        valid = asset_returns.dropna()
        missing_weight = float(
            current.reindex([name for name in current.index if name != "CASH"]).fillna(0.0).sum()
            - current.reindex(valid.index).fillna(0.0).sum()
        )
        gross_return = float((current.reindex(valid.index).fillna(0.0) * valid).sum())
        cost = turnover * (float(cost_bps) / 10000.0)
        net_return = gross_return - cost
        nav *= 1.0 + net_return

        post_values = current.reindex(valid.index).fillna(0.0) * (1.0 + valid)
        cash_value = float(current.get("CASH", 0.0)) + max(0.0, missing_weight)
        denominator = float(post_values.sum()) + cash_value
        current = (
            pd.concat([post_values, pd.Series({"CASH": cash_value})])
            / denominator
            if denominator > 0.0
            else pd.Series({"CASH": 1.0}, dtype=float)
        )
        equity_weights = current.drop(labels=["CASH"], errors="ignore")
        rows.append(
            {
                "policy_id": policy.policy_id,
                "date": str(date.date()),
                "next_date": str(next_date.date()),
                "gross_return": gross_return,
                "cost": cost,
                "net_return": net_return,
                "nav": nav,
                "one_way_turnover": turnover,
                "cash_weight_end": float(current.get("CASH", 0.0)),
                "holdings_count_end": int((equity_weights > 1e-12).sum()),
                "max_weight_end": float(equity_weights.max()) if not equity_weights.empty else 0.0,
                "missing_price_weight": max(0.0, missing_weight),
            }
        )

    frame = pd.DataFrame(rows)
    returns = pd.to_numeric(frame.get("net_return"), errors="coerce").dropna()
    metrics: dict[str, Any] = {
        "policy_id": policy.policy_id,
        "description": policy.description,
        "evidence_class": policy.evidence_class,
        "observation_count": int(len(frame)),
        "total_return": None,
        "max_drawdown": None,
        "average_one_way_turnover": None,
        "cumulative_one_way_turnover": None,
        "win_rate": None,
        "average_cash_weight": None,
        "average_holdings_count": None,
        "max_missing_price_weight": None,
    }
    if not frame.empty:
        nav_series = pd.to_numeric(frame["nav"], errors="coerce").dropna()
        peaks = nav_series.cummax()
        metrics.update(
            {
                "date_start": frame.iloc[0]["date"],
                "date_end": frame.iloc[-1]["next_date"],
                "total_return": float(nav_series.iloc[-1] - 1.0),
                "max_drawdown": float((nav_series / peaks - 1.0).min()),
                "average_one_way_turnover": float(frame["one_way_turnover"].mean()),
                "cumulative_one_way_turnover": float(frame["one_way_turnover"].sum()),
                "win_rate": float((returns > 0.0).mean()) if not returns.empty else None,
                "average_cash_weight": float(frame["cash_weight_end"].mean()),
                "average_holdings_count": float(frame["holdings_count_end"].mean()),
                "max_missing_price_weight": float(frame["missing_price_weight"].max()),
            }
        )
    return metrics, rows


def build_artifact(
    *,
    repo: Path,
    artifact_date: str,
    start: str,
    end: str,
    signals_root: Path,
    price_panel: Path,
    factor_panel: Path | None,
    output_root: Path,
    cost_bps: float,
) -> tuple[dict[str, Any], Path]:
    decisions, inventory = discover_decisions(
        repo,
        signals_root=signals_root,
        start=start,
        end=end,
    )
    close, price_meta = load_close_matrix(
        repo,
        price_panel,
        factor_panel=factor_panel,
    )
    if not decisions:
        raise RuntimeError("no stored precompute signal decisions available")
    if close.empty:
        raise RuntimeError(f"close panel unavailable: {price_meta}")

    broad_anchor = _last_broad_anchor(decisions)
    if broad_anchor is None:
        raise RuntimeError("no stored broad target book with at least 15 equity names")

    policies = [
        ReplayPolicy(
            "observed_daily_targets",
            "Exact stored production target sequence, rebalanced on every stored decision date.",
            _observed_daily,
            "STRICT_PIT_STORED_DECISIONS",
        ),
        ReplayPolicy(
            "observed_weekly_targets",
            "First exact stored production target each ISO week; held until the next week.",
            _observed_weekly,
            "STRICT_PIT_REBALANCE_COUNTERFACTUAL",
        ),
        ReplayPolicy(
            "observed_daily_rotation_guard",
            "Exact stored daily targets scaled to 50%/25% gross when their cohort reverses versus SPY using only prior closes.",
            _guarded_target,
            "STRICT_PIT_RESEARCH_COUNTERFACTUAL",
        ),
        ReplayPolicy(
            "observed_weekly_rotation_guard",
            "Weekly stored targets plus the strictly prior-close cohort rotation guard.",
            _guarded_weekly,
            "STRICT_PIT_RESEARCH_COUNTERFACTUAL",
        ),
    ]

    start_ts = max(pd.Timestamp(start).normalize(), min(decisions))
    end_ts = min(pd.Timestamp(end).normalize(), close.index.max())
    all_metrics: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    for policy in policies:
        metrics, rows = _replay_policy(
            policy=policy,
            decisions=decisions,
            close=close,
            start=start_ts,
            end=end_ts,
            cost_bps=cost_bps,
        )
        all_metrics.append(metrics)
        all_rows.extend(rows)

    anchor_start = max(broad_anchor.trade_date, start_ts)
    for policy_id, daily_rebalance in (
        ("last_exact_broad_book_buy_hold", False),
        ("last_exact_broad_book_daily_rebalance", True),
    ):
        used = False

        def broad_fn(
            date: pd.Timestamp,
            _decisions: dict[pd.Timestamp, Decision],
            _close: pd.DataFrame,
            *,
            _daily_rebalance: bool = daily_rebalance,
        ) -> pd.Series | None:
            nonlocal used
            if date < anchor_start:
                return None
            if _daily_rebalance or not used:
                used = True
                return broad_anchor.weights.copy()
            return None

        policy = ReplayPolicy(
            policy_id,
            (
                f"Last exact broad target book from {broad_anchor.trade_date.date()} "
                + ("rebalanced daily." if daily_rebalance else "bought once and allowed to drift.")
            ),
            broad_fn,
            "STRICT_PIT_STATIC_ANCHOR_COUNTERFACTUAL",
        )
        metrics, rows = _replay_policy(
            policy=policy,
            decisions=decisions,
            close=close,
            start=anchor_start,
            end=end_ts,
            cost_bps=cost_bps,
        )
        all_metrics.append(metrics)
        all_rows.extend(rows)

    guard_rows: list[dict[str, Any]] = []
    for date, decision in sorted(decisions.items()):
        if date < start_ts or date > end_ts:
            continue
        _, guard = _rotation_guard_scale(
            date=date,
            target=decision.weights,
            close=close,
        )
        guard_rows.append(guard)

    observed = next(
        (row for row in all_metrics if row["policy_id"] == "observed_daily_targets"),
        {},
    )
    candidates = [
        row
        for row in all_metrics
        if row["policy_id"]
        in {
            "observed_weekly_targets",
            "observed_daily_rotation_guard",
            "observed_weekly_rotation_guard",
        }
    ]
    recommended = None
    eligible = [
        row
        for row in candidates
        if row.get("observation_count") == observed.get("observation_count")
        and float(row.get("max_missing_price_weight") or 0.0) <= 0.01
    ]
    if eligible:
        recommended = max(
            eligible,
            key=lambda row: (
                float(row.get("total_return") or -999.0),
                float(row.get("max_drawdown") or -999.0),
                -float(row.get("average_one_way_turnover") or 999.0),
            ),
        )["policy_id"]

    payload = {
        "schema_version": SCHEMA_VERSION,
        "artifact_date": artifact_date,
        "research_only": True,
        "execution_impact": "NONE",
        "window": {
            "requested_start": start,
            "requested_end": end,
            "replay_start": str(start_ts.date()),
            "replay_end": str(end_ts.date()),
        },
        "transaction_cost_bps_per_one_way_turnover": float(cost_bps),
        "decision_inventory": inventory,
        "price_evidence": price_meta,
        "broad_anchor": {
            "trade_date": str(broad_anchor.trade_date.date()),
            "source_path": broad_anchor.source_path,
            "source_sha256": broad_anchor.source_sha256,
            "equity_name_count": int(
                (
                    broad_anchor.weights.drop(labels=["CASH"], errors="ignore")
                    > 1e-12
                ).sum()
            ),
        },
        "metrics": all_metrics,
        "guard_observations": guard_rows,
        "recommendation": {
            "research_candidate": recommended,
            "selection_rule": "highest net return, then shallower drawdown, then lower turnover among complete strict-PIT candidate replays",
            "promotion_status": "NOT_PROMOTED",
            "paper_status": "NOT_ENABLED",
        },
        "evidence_assessment": {
            "decision_grade": False,
            "strong_claims": [
                "The exact stored production target sequence can be replayed.",
                "The last exact broad pre-concentration book can be replayed as a static anchor.",
                "Weekly and rotation-guard counterfactuals use only decisions and closes available by each decision date.",
            ],
            "prohibited_claims": [
                "The dynamic daily 18-name post-July-7 book was exactly reconstructed.",
                "A short single-regime replay proves durable alpha or promotion readiness.",
                "The selected research candidate is authorized for paper or live trading.",
            ],
            "missing_evidence": [
                "Daily pre-concentration allocator weights after the last stored broad target book.",
                "A long multi-regime, PIT-valid replay window for the production growth_engine_v4 decision stream.",
                "Forward paper observation for any recovery candidate.",
            ],
        },
    }

    output_dir = (output_root if output_root.is_absolute() else repo / output_root) / artifact_date
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "drawdown_recovery_replay.json"
    csv_path = output_dir / "drawdown_recovery_daily.csv"
    metrics_path = output_dir / "drawdown_recovery_metrics.csv"
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    pd.DataFrame(all_rows).to_csv(csv_path, index=False)
    pd.DataFrame(all_metrics).to_csv(metrics_path, index=False)
    return payload, json_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    default_repo = (
        Path(__file__).resolve().parents[2]
        if len(Path(__file__).resolve().parents) > 2
        else Path.cwd()
    )
    parser.add_argument("--repo", type=Path, default=default_repo)
    parser.add_argument("--artifact-date", default="2026-07-28")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--signals-root", type=Path, default=DEFAULT_SIGNALS_ROOT)
    parser.add_argument("--price-panel", type=Path, default=DEFAULT_PRICE_PANEL)
    parser.add_argument("--factor-panel", type=Path, default=DEFAULT_FACTOR_PANEL)
    parser.add_argument("--hydrate-factor-panel", action="store_true")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--transaction-cost-bps", type=float, default=DEFAULT_COST_BPS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    factor_path = (
        args.factor_panel
        if args.factor_panel.is_absolute()
        else args.repo.resolve() / args.factor_panel
    )
    if args.hydrate_factor_panel:
        hydrate_factor_panel(
            factor_path,
            start=args.start,
            end=args.end,
        )
    payload, path = build_artifact(
        repo=args.repo.resolve(),
        artifact_date=args.artifact_date,
        start=args.start,
        end=args.end,
        signals_root=args.signals_root,
        price_panel=args.price_panel,
        factor_panel=args.factor_panel,
        output_root=args.output_root,
        cost_bps=args.transaction_cost_bps,
    )
    print(
        json.dumps(
            {
                "artifact": str(path),
                "research_candidate": payload["recommendation"]["research_candidate"],
                "decision_grade": payload["evidence_assessment"]["decision_grade"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
