"""Polaris/Orion holdings-count and concentration frontier research.

Research-only. This module uses the canonical security_id replay panel and
does not import execution, broker, scheduler, allocator, or production trading
paths.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from alpha_stack.research.metrics import annualised_vol, max_drawdown, sharpe_ratio, sortino_ratio
from research.alpha_lab_v1.signals import build_alpha_lab_signal_frame
from research.alpha_lab_v2.engine import prepare_backtest_inputs
from research.shadow_tracking.strategies import build_strategy_lookup

SCHEMA_VERSION = "caerus_holdings_concentration_frontier_v1"
DEFAULT_START_DATE = "2014-01-02"
DEFAULT_END_DATE = "2024-12-31"
DEFAULT_PANEL_PATH = Path("outputs/research/canonical_pit_replay/2026-06-23/price_panel.parquet")
DEFAULT_MANIFEST_PATH = Path("outputs/research/canonical_pit_replay/2026-06-23/manifest.json")
DEFAULT_OUTPUT_ROOT = Path("outputs/research/holdings_concentration_frontier")
DEFAULT_TOP_N = (3, 4, 5, 6, 7, 8, 9, 10, 12, 15)
DEFAULT_WEIGHT_METHODS = ("equal", "rank", "score", "score2")
DEFAULT_MAX_POSITION_WEIGHTS = (0.20, 0.25, 0.30, 0.35, 0.40)
DEFAULT_MIN_POSITION_WEIGHTS = (0.0, 0.02, 0.03, 0.05)
TARGET_SLEEVES = ("caerus_polaris", "caerus_orion")
TRADING_DAYS_PER_YEAR = 252
EPSILON = 1e-12
Candidate = tuple[str, float, float]
CandidatePack = dict[str, Any]


@dataclass(frozen=True)
class VariantSpec:
    sleeve: str
    top_n: int
    weighting_method: str
    max_position_weight: float
    min_position_weight: float

    @property
    def variant_id(self) -> str:
        cap = int(round(self.max_position_weight * 100))
        floor = "none" if self.min_position_weight <= 0 else str(int(round(self.min_position_weight * 100)))
        return f"{self.sleeve}_top{self.top_n}_{self.weighting_method}_cap{cap}_min{floor}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _round(value: Any, digits: int = 10) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _current_strategy_specs() -> dict[str, dict[str, Any]]:
    lookup = build_strategy_lookup()
    out: dict[str, dict[str, Any]] = {}
    for sleeve in TARGET_SLEEVES:
        spec = lookup[sleeve].spec
        out[sleeve] = {
            "name": spec.name,
            "top_n": int(spec.top_n),
            "rebalance_mode": spec.rebalance_mode,
            "transaction_cost_bps": float(spec.transaction_cost_bps),
            "use_rank_decay_exit": bool(spec.use_rank_decay_exit),
            "exit_rank_multiple": float(spec.exit_rank_multiple),
            "description": spec.description,
        }
    return out


def load_canonical_signal_inputs(
    *,
    panel_path: Path,
    start_date: str,
    end_date: str,
    include_trailing_vol: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, list[pd.Timestamp], pd.DataFrame]:
    panel = pd.read_parquet(
        panel_path,
        columns=["date", "security_id", "display_ticker", "closeadj"],
    )
    alpha_panel = panel[["date", "security_id", "closeadj"]].rename(
        columns={"security_id": "ticker", "closeadj": "close"}
    )
    signals = build_alpha_lab_signal_frame(alpha_panel)
    frame, returns_matrix, trading_dates = prepare_backtest_inputs(
        signals,
        start_date=start_date,
        end_date=end_date,
    )
    if include_trailing_vol:
        close_matrix = frame.pivot(index="date", columns="ticker", values="close").sort_index()
        close_returns = close_matrix.pct_change()
        trailing_vol = close_returns.rolling(63, min_periods=20).std()
    else:
        trailing_vol = pd.DataFrame()
    return frame, returns_matrix, trading_dates, trailing_vol


def _cap_and_redistribute(raw: pd.Series, max_position_weight: float) -> pd.Series:
    raw = pd.to_numeric(raw, errors="coerce").dropna()
    raw = raw[raw > EPSILON].astype(float)
    if raw.empty:
        return pd.Series(dtype=float)
    cap = float(max_position_weight)
    if cap <= 0:
        return pd.Series(dtype=float)
    remaining = raw.copy()
    output = pd.Series(0.0, index=raw.index, dtype=float)
    remaining_capital = 1.0
    for _ in range(len(raw) + 1):
        if remaining.empty or remaining_capital <= EPSILON:
            break
        proposal = remaining / float(remaining.sum()) * remaining_capital
        over_cap = proposal[proposal > cap + EPSILON]
        if over_cap.empty:
            output.loc[proposal.index] = proposal
            break
        output.loc[over_cap.index] = cap
        remaining_capital -= cap * len(over_cap)
        remaining = remaining.drop(over_cap.index)
    return output[output > EPSILON].sort_values(ascending=False)


def _cap_and_redistribute_map(raw: dict[str, float], max_position_weight: float) -> dict[str, float]:
    active = {str(k): float(v) for k, v in raw.items() if float(v) > EPSILON}
    if not active:
        return {}
    cap = float(max_position_weight)
    if cap <= 0:
        return {}
    remaining = dict(active)
    output = {ticker: 0.0 for ticker in active}
    remaining_capital = 1.0
    for _ in range(len(active) + 1):
        if not remaining or remaining_capital <= EPSILON:
            break
        total = sum(remaining.values())
        if total <= EPSILON:
            break
        proposal = {ticker: value / total * remaining_capital for ticker, value in remaining.items()}
        over = {ticker: value for ticker, value in proposal.items() if value > cap + EPSILON}
        if not over:
            output.update(proposal)
            break
        for ticker in over:
            output[ticker] = cap
            remaining.pop(ticker, None)
        remaining_capital -= cap * len(over)
    return {
        ticker: weight
        for ticker, weight in sorted(output.items(), key=lambda item: item[1], reverse=True)
        if weight > EPSILON
    }


def apply_position_constraints_map(
    raw: dict[str, float],
    *,
    max_position_weight: float,
    min_position_weight: float,
) -> dict[str, float]:
    active = {str(k): float(v) for k, v in raw.items() if float(v) > EPSILON}
    if not active:
        return {}
    floor = float(min_position_weight)
    for _ in range(len(active) + 1):
        constrained = _cap_and_redistribute_map(active, max_position_weight=max_position_weight)
        if not constrained or floor <= 0:
            return constrained
        keep = {ticker: weight for ticker, weight in constrained.items() if weight >= floor - EPSILON}
        if len(keep) == len(constrained):
            return constrained
        active = {ticker: active[ticker] for ticker in keep if ticker in active}
        if not active:
            return {}
    return _cap_and_redistribute_map(active, max_position_weight=max_position_weight)


def apply_position_constraints(
    raw: pd.Series,
    *,
    max_position_weight: float,
    min_position_weight: float,
) -> pd.Series:
    constrained = apply_position_constraints_map(
        {str(k): float(v) for k, v in pd.to_numeric(raw, errors="coerce").dropna().items()},
        max_position_weight=max_position_weight,
        min_position_weight=min_position_weight,
    )
    if not constrained:
        return pd.Series(dtype=float)
    return pd.Series(constrained, dtype=float)


def _positive_scores(selected: pd.DataFrame, *, squared: bool) -> pd.Series:
    scores = pd.to_numeric(selected["momentum_score"], errors="coerce").fillna(0.0)
    ranks = pd.to_numeric(selected["momentum_rank"], errors="coerce").fillna(float(len(selected)))
    shifted = scores - min(float(scores.min()), 0.0)
    if float(shifted.max() - shifted.min()) <= EPSILON or float(shifted.sum()) <= EPSILON:
        raw = 1.0 / ranks.clip(lower=1.0)
    else:
        raw = shifted + EPSILON
    if squared:
        raw = raw.pow(2)
    raw.index = pd.Index(selected["ticker"].astype(str), dtype=str)
    return raw.astype(float)


def raw_weights_for_selection(
    selected: pd.DataFrame,
    *,
    weighting_method: str,
    trailing_vol_for_date: pd.Series | None = None,
) -> pd.Series:
    if selected.empty:
        return pd.Series(dtype=float)
    tickers = pd.Index(selected["ticker"].astype(str), dtype=str)
    if weighting_method == "equal":
        return pd.Series(1.0, index=tickers, dtype=float)
    if weighting_method == "rank":
        ranks = pd.to_numeric(selected["momentum_rank"], errors="coerce").fillna(float(len(selected)))
        ranks.index = tickers
        return (1.0 / ranks.clip(lower=1.0)).astype(float)
    if weighting_method == "score":
        return _positive_scores(selected, squared=False)
    if weighting_method == "score2":
        return _positive_scores(selected, squared=True)
    if weighting_method == "inverse_vol":
        if trailing_vol_for_date is None:
            return pd.Series(dtype=float)
        vols = pd.to_numeric(trailing_vol_for_date.reindex(tickers), errors="coerce")
        vols = vols.where(vols > EPSILON)
        if vols.dropna().empty:
            return pd.Series(1.0, index=tickers, dtype=float)
        return (1.0 / vols.fillna(float(vols.median()))).astype(float)
    raise ValueError(f"unknown weighting method: {weighting_method}")


def _candidate_pack_from_frame(daily: pd.DataFrame, *, pre_sorted: bool = False) -> CandidatePack:
    if daily.empty:
        return {"ordered": [], "by_ticker": {}}
    candidates = daily
    if "signal_ready" in candidates.columns and not bool(candidates["signal_ready"].all()):
        candidates = candidates[candidates["signal_ready"]]
    if candidates.empty:
        return {"ordered": [], "by_ticker": {}}
    if not pre_sorted:
        candidates = candidates.sort_values(["momentum_score", "ticker"], ascending=[False, True])
    ordered: list[Candidate] = [
        (str(row.ticker), float(row.momentum_score), float(row.momentum_rank))
        for row in candidates[["ticker", "momentum_score", "momentum_rank"]].itertuples(index=False)
    ]
    return {"ordered": ordered, "by_ticker": {ticker: (ticker, score, rank) for ticker, score, rank in ordered}}


def _select_from_pack(
    pack: CandidatePack,
    *,
    sleeve: str,
    top_n: int,
    previous_weights: dict[str, float],
    exit_rank_multiple: float,
) -> list[str]:
    ordered: list[Candidate] = pack.get("ordered", [])
    if not ordered:
        return []
    if sleeve != "caerus_orion":
        return [ticker for ticker, _, _ in ordered[:top_n]]
    by_ticker: dict[str, Candidate] = pack.get("by_ticker", {})
    exit_cutoff = int(top_n * float(exit_rank_multiple))
    keep: list[str] = []
    for ticker, weight in previous_weights.items():
        if float(weight) <= EPSILON or ticker not in by_ticker:
            continue
        _, _, rank = by_ticker[ticker]
        if float(rank) <= exit_cutoff:
            keep.append(ticker)
    keep_set = set(keep)
    fill = [ticker for ticker, _, _ in ordered if ticker not in keep_set]
    return (keep + fill)[:top_n]


def _raw_weights_for_records(
    selected: list[Candidate],
    *,
    weighting_method: str,
) -> dict[str, float]:
    if not selected:
        return {}
    if weighting_method == "equal":
        return {ticker: 1.0 for ticker, _, _ in selected}
    if weighting_method == "rank":
        return {ticker: 1.0 / max(float(rank), 1.0) for ticker, _, rank in selected}
    if weighting_method in {"score", "score2"}:
        scores = [float(score) for _, score, _ in selected]
        ranks = [float(rank) for _, _, rank in selected]
        min_score = min(scores)
        shifted = [score - min(min_score, 0.0) for score in scores]
        if max(shifted) - min(shifted) <= EPSILON or sum(shifted) <= EPSILON:
            raw = [1.0 / max(rank, 1.0) for rank in ranks]
        else:
            raw = [value + EPSILON for value in shifted]
        if weighting_method == "score2":
            raw = [value * value for value in raw]
        return {ticker: float(value) for (ticker, _, _), value in zip(selected, raw)}
    raise ValueError(f"unknown weighting method for optimized path: {weighting_method}")


def select_tickers_for_date(
    daily: pd.DataFrame,
    *,
    sleeve: str,
    top_n: int,
    previous_weights: pd.Series | dict[str, float],
    exit_rank_multiple: float = 2.0,
    pre_sorted: bool = False,
) -> list[str]:
    if daily.empty:
        return []
    if "signal_ready" in daily.columns and not bool(daily["signal_ready"].all()):
        candidates = daily[daily["signal_ready"]].copy()
    else:
        candidates = daily
    if candidates.empty:
        return []
    if not pre_sorted:
        candidates = candidates.sort_values(["momentum_score", "ticker"], ascending=[False, True])
    if sleeve != "caerus_orion":
        return candidates.head(top_n)["ticker"].astype(str).tolist()

    exit_cutoff = int(top_n * float(exit_rank_multiple))
    keep: list[str] = []
    if isinstance(previous_weights, dict):
        previous = [str(ticker) for ticker, weight in previous_weights.items() if float(weight) > EPSILON]
    else:
        previous = [str(ticker) for ticker in previous_weights[previous_weights > EPSILON].index]
    by_ticker = candidates if candidates.index.name == "ticker" else candidates.set_index(candidates["ticker"].astype(str), drop=False)
    for ticker in previous:
        if ticker not in by_ticker.index:
            continue
        row = by_ticker.loc[ticker]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        if float(row["momentum_rank"]) <= exit_cutoff:
            keep.append(ticker)
    fill = [ticker for ticker in candidates["ticker"].astype(str).tolist() if ticker not in set(keep)]
    return (keep + fill)[:top_n]


def _turnover(previous_weights: dict[str, float], weights: dict[str, float]) -> float:
    names = set(previous_weights) | set(weights)
    return float(sum(abs(float(weights.get(name, 0.0)) - float(previous_weights.get(name, 0.0))) for name in names))


def _drawdown_duration(nav: pd.Series) -> dict[str, Any]:
    if nav.empty:
        return {
            "max_drawdown_duration_trading_days": 0,
            "max_drawdown_duration_start": None,
            "max_drawdown_duration_end": None,
        }
    drawdown = nav / nav.cummax() - 1.0
    current_start: pd.Timestamp | None = None
    current_len = 0
    best_len = 0
    best_start: pd.Timestamp | None = None
    best_end: pd.Timestamp | None = None
    for dt, value in drawdown.items():
        if float(value) < -EPSILON:
            if current_start is None:
                current_start = pd.Timestamp(dt)
                current_len = 0
            current_len += 1
            if current_len > best_len:
                best_len = current_len
                best_start = current_start
                best_end = pd.Timestamp(dt)
        else:
            current_start = None
            current_len = 0
    return {
        "max_drawdown_duration_trading_days": int(best_len),
        "max_drawdown_duration_start": best_start.strftime("%Y-%m-%d") if best_start is not None else None,
        "max_drawdown_duration_end": best_end.strftime("%Y-%m-%d") if best_end is not None else None,
    }


def _variant_metrics(
    returns: pd.Series,
    *,
    gross_exposure: pd.Series,
    holdings_count: pd.Series,
    turnover: pd.Series,
    spec: VariantSpec,
) -> dict[str, Any]:
    returns = pd.to_numeric(returns, errors="coerce").dropna()
    if returns.empty:
        base = {
            "observation_count": 0,
            "total_return": None,
            "cagr": None,
            "sharpe": None,
            "sortino": None,
            "max_drawdown": None,
            "volatility": None,
            "turnover": None,
            "average_holdings_count": None,
            "cash_drag": None,
            "best_month": None,
            "worst_month": None,
            "best_month_date": None,
            "worst_month_date": None,
            "alpha_per_dollar_deployed": None,
            "calmar": None,
        }
        return {**_variant_identity(spec), **base}
    nav = (1.0 + returns).cumprod()
    total_return = float(nav.iloc[-1] - 1.0)
    years = len(returns) / TRADING_DAYS_PER_YEAR
    cagr_value = (1.0 + total_return) ** (1.0 / years) - 1.0 if years > 0 and total_return > -1.0 else None
    monthly = (1.0 + returns).resample("ME").prod() - 1.0
    avg_gross = float(gross_exposure.reindex(returns.index).fillna(0.0).mean())
    mdd = float(max_drawdown(nav))
    calmar = (float(cagr_value) / abs(mdd)) if cagr_value is not None and abs(mdd) > EPSILON else None
    best_month_date = monthly.idxmax().strftime("%Y-%m") if not monthly.empty else None
    worst_month_date = monthly.idxmin().strftime("%Y-%m") if not monthly.empty else None
    metrics = {
        "observation_count": int(len(returns)),
        "total_return": _round(total_return),
        "cagr": _round(cagr_value),
        "sharpe": _round(sharpe_ratio(returns)),
        "sortino": _round(sortino_ratio(returns)),
        "max_drawdown": _round(mdd),
        "volatility": _round(annualised_vol(returns)),
        "turnover": _round(float(turnover.reindex(returns.index).fillna(0.0).mean())),
        "average_holdings_count": _round(float(holdings_count.reindex(returns.index).fillna(0.0).mean())),
        "average_gross_exposure": _round(avg_gross),
        "cash_drag": _round(float((1.0 - gross_exposure.reindex(returns.index).fillna(0.0)).clip(lower=0.0).mean())),
        "best_month": _round(float(monthly.max())) if not monthly.empty else None,
        "worst_month": _round(float(monthly.min())) if not monthly.empty else None,
        "best_month_date": best_month_date,
        "worst_month_date": worst_month_date,
        "alpha_per_dollar_deployed": _round(float(cagr_value) / avg_gross) if cagr_value is not None and avg_gross > EPSILON else None,
        "calmar": _round(calmar),
    }
    metrics.update(_drawdown_duration(nav))
    return {**_variant_identity(spec), **metrics}


def _variant_identity(spec: VariantSpec) -> dict[str, Any]:
    return {
        "variant_id": spec.variant_id,
        "sleeve": spec.sleeve,
        "top_n": int(spec.top_n),
        "weighting_method": spec.weighting_method,
        "max_position_weight": float(spec.max_position_weight),
        "min_position_weight": float(spec.min_position_weight),
    }


def run_variant(
    *,
    spec: VariantSpec,
    daily_frames: dict[pd.Timestamp, CandidatePack | pd.DataFrame],
    returns_by_date: dict[pd.Timestamp, dict[str, float]],
    trading_dates: list[pd.Timestamp],
    trailing_vol: pd.DataFrame,
    transaction_cost_bps: float,
    exit_rank_multiple: float = 2.0,
) -> dict[str, Any]:
    previous_weights: dict[str, float] = {}
    returns: list[tuple[pd.Timestamp, float]] = []
    gross_rows: list[tuple[pd.Timestamp, float]] = []
    holdings_rows: list[tuple[pd.Timestamp, int]] = []
    turnover_rows: list[tuple[pd.Timestamp, float]] = []

    for dt in trading_dates:
        daily = daily_frames.get(dt, {"ordered": [], "by_ticker": {}})
        pack = daily if isinstance(daily, dict) and "ordered" in daily else _candidate_pack_from_frame(daily)
        selected_tickers = _select_from_pack(
            pack,
            sleeve=spec.sleeve,
            top_n=spec.top_n,
            previous_weights=previous_weights,
            exit_rank_multiple=exit_rank_multiple,
        )
        if selected_tickers:
            by_ticker: dict[str, Candidate] = pack.get("by_ticker", {})
            selected_records = [by_ticker[ticker] for ticker in selected_tickers if ticker in by_ticker]
            if spec.weighting_method == "inverse_vol":
                selected = pd.DataFrame(
                    [
                        {"ticker": ticker, "momentum_score": score, "momentum_rank": rank}
                        for ticker, score, rank in selected_records
                    ]
                )
                vol_row = trailing_vol.loc[dt] if dt in trailing_vol.index else None
                raw_series = raw_weights_for_selection(
                    selected,
                    weighting_method=spec.weighting_method,
                    trailing_vol_for_date=vol_row,
                )
                raw = {str(k): float(v) for k, v in raw_series.items()}
            else:
                raw = _raw_weights_for_records(selected_records, weighting_method=spec.weighting_method)
            weight_dict = apply_position_constraints_map(
                raw,
                max_position_weight=spec.max_position_weight,
                min_position_weight=spec.min_position_weight,
            )
        else:
            weight_dict = {}

        turnover_value = _turnover(previous_weights, weight_dict)
        return_map = returns_by_date.get(dt, {})
        gross_return = float(sum(float(weight) * float(return_map.get(ticker, 0.0)) for ticker, weight in weight_dict.items()))
        net_return = gross_return - turnover_value * (transaction_cost_bps / 10000.0)
        returns.append((dt, net_return))
        gross_rows.append((dt, float(sum(weight_dict.values())) if weight_dict else 0.0))
        holdings_rows.append((dt, len(weight_dict)))
        turnover_rows.append((dt, turnover_value))
        previous_weights = weight_dict

    index = pd.DatetimeIndex([dt for dt, _ in returns])
    return _variant_metrics(
        pd.Series([value for _, value in returns], index=index, name="net_return"),
        gross_exposure=pd.Series([value for _, value in gross_rows], index=index, name="gross_exposure"),
        holdings_count=pd.Series([value for _, value in holdings_rows], index=index, name="holdings_count"),
        turnover=pd.Series([value for _, value in turnover_rows], index=index, name="turnover"),
        spec=spec,
    )


def _best_by(rows: list[dict[str, Any]], field: str, *, higher_is_better: bool = True) -> dict[str, Any] | None:
    valid = [row for row in rows if row.get(field) is not None]
    if not valid:
        return None
    return sorted(valid, key=lambda row: float(row[field]), reverse=higher_is_better)[0]


def _practical_score_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return []
    score = pd.Series(0.0, index=frame.index)
    for field in ("cagr", "sharpe", "sortino", "calmar", "alpha_per_dollar_deployed"):
        values = pd.to_numeric(frame[field], errors="coerce")
        score += values.rank(pct=True).fillna(0.0)
    drawdown_penalty = pd.to_numeric(frame["max_drawdown"], errors="coerce").abs().rank(pct=True).fillna(1.0)
    turnover_penalty = pd.to_numeric(frame["turnover"], errors="coerce").rank(pct=True).fillna(1.0)
    cash_penalty = pd.to_numeric(frame["cash_drag"], errors="coerce").fillna(1.0)
    score = score - drawdown_penalty - 0.25 * turnover_penalty - 0.50 * cash_penalty
    frame["practical_score"] = score
    return frame.sort_values(["sleeve", "practical_score"], ascending=[True, False]).to_dict("records")


def _pareto_frontier(rows: list[dict[str, Any]], *, risk_field: str) -> list[dict[str, Any]]:
    valid = [
        row for row in rows
        if row.get("cagr") is not None and row.get(risk_field) is not None
    ]
    if risk_field == "max_drawdown":
        valid = [{**row, "risk_value": abs(float(row[risk_field]))} for row in valid]
    else:
        valid = [{**row, "risk_value": float(row[risk_field])} for row in valid]
    frontier: list[dict[str, Any]] = []
    best_return = -math.inf
    for row in sorted(valid, key=lambda item: (item["risk_value"], -float(item["cagr"]))):
        if float(row["cagr"]) > best_return + EPSILON:
            frontier.append({
                "sleeve": row["sleeve"],
                "risk_axis": risk_field,
                "risk_value": _round(row["risk_value"]),
                "cagr": row["cagr"],
                "variant_id": row["variant_id"],
                "top_n": row["top_n"],
                "weighting_method": row["weighting_method"],
                "max_position_weight": row["max_position_weight"],
                "min_position_weight": row["min_position_weight"],
                "label": _variant_label(row),
            })
            best_return = float(row["cagr"])
    return frontier


def _variant_label(row: dict[str, Any]) -> str:
    cap = int(round(float(row["max_position_weight"]) * 100))
    floor = "none" if float(row["min_position_weight"]) <= 0 else f"{int(round(float(row['min_position_weight']) * 100))}%"
    return f"Top {row['top_n']} {row['weighting_method']} cap {cap}% min {floor}"


def _topn_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return []
    summaries: list[dict[str, Any]] = []
    for (sleeve, top_n), group in frame.groupby(["sleeve", "top_n"]):
        summaries.append({
            "sleeve": sleeve,
            "top_n": int(top_n),
            "variant_count": int(len(group)),
            "median_cagr": _round(pd.to_numeric(group["cagr"], errors="coerce").median()),
            "best_cagr": _round(pd.to_numeric(group["cagr"], errors="coerce").max()),
            "best_sharpe": _round(pd.to_numeric(group["sharpe"], errors="coerce").max()),
            "best_sortino": _round(pd.to_numeric(group["sortino"], errors="coerce").max()),
            "least_bad_max_drawdown": _round(pd.to_numeric(group["max_drawdown"], errors="coerce").max()),
            "median_cash_drag": _round(pd.to_numeric(group["cash_drag"], errors="coerce").median()),
        })
    return sorted(summaries, key=lambda row: (row["sleeve"], row["top_n"]))


def _method_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return []
    summaries: list[dict[str, Any]] = []
    for (sleeve, method), group in frame.groupby(["sleeve", "weighting_method"]):
        summaries.append({
            "sleeve": sleeve,
            "weighting_method": method,
            "variant_count": int(len(group)),
            "median_cagr": _round(pd.to_numeric(group["cagr"], errors="coerce").median()),
            "best_cagr": _round(pd.to_numeric(group["cagr"], errors="coerce").max()),
            "median_max_drawdown": _round(pd.to_numeric(group["max_drawdown"], errors="coerce").median()),
            "best_sharpe": _round(pd.to_numeric(group["sharpe"], errors="coerce").max()),
        })
    return sorted(summaries, key=lambda row: (row["sleeve"], row["weighting_method"]))


def _cap_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return []
    summaries: list[dict[str, Any]] = []
    for (sleeve, cap), group in frame.groupby(["sleeve", "max_position_weight"]):
        summaries.append({
            "sleeve": sleeve,
            "max_position_weight": float(cap),
            "variant_count": int(len(group)),
            "median_cagr": _round(pd.to_numeric(group["cagr"], errors="coerce").median()),
            "best_cagr": _round(pd.to_numeric(group["cagr"], errors="coerce").max()),
            "median_max_drawdown": _round(pd.to_numeric(group["max_drawdown"], errors="coerce").median()),
            "best_sharpe": _round(pd.to_numeric(group["sharpe"], errors="coerce").max()),
        })
    return sorted(summaries, key=lambda row: (row["sleeve"], row["max_position_weight"]))


def _sleeve_answers(rows: list[dict[str, Any]], practical_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    answers: dict[str, dict[str, Any]] = {}
    frame = pd.DataFrame(rows)
    practical_frame = pd.DataFrame(practical_rows)
    for sleeve in TARGET_SLEEVES:
        subset = [row for row in rows if row["sleeve"] == sleeve]
        best_return = _best_by(subset, "cagr")
        best_sharpe = _best_by(subset, "sharpe")
        best_sortino = _best_by(subset, "sortino")
        best_drawdown_adjusted = _best_by(subset, "calmar")
        practical = None
        if not practical_frame.empty:
            p = practical_frame[practical_frame["sleeve"] == sleeve]
            practical = p.iloc[0].to_dict() if not p.empty else None

        sleeve_frame = frame[frame["sleeve"] == sleeve].copy()
        topn = (
            sleeve_frame.groupby("top_n")["cagr"].median().sort_index()
            if not sleeve_frame.empty else pd.Series(dtype=float)
        )
        cap_mdd = (
            sleeve_frame.groupby("max_position_weight")["max_drawdown"].median().sort_index()
            if not sleeve_frame.empty else pd.Series(dtype=float)
        )
        score = sleeve_frame[sleeve_frame["weighting_method"] == "score"]
        score2 = sleeve_frame[sleeve_frame["weighting_method"] == "score2"]
        score2_aggressive = None
        if not score.empty and not score2.empty:
            score2_aggressive = bool(
                pd.to_numeric(score2["max_drawdown"], errors="coerce").median()
                < pd.to_numeric(score["max_drawdown"], errors="coerce").median()
                and pd.to_numeric(score2["sharpe"], errors="coerce").median()
                <= pd.to_numeric(score["sharpe"], errors="coerce").median()
            )
        optimal_top_n = int(practical["top_n"]) if practical else None
        current_top_n = 10 if sleeve == "caerus_polaris" else 5
        if practical is None:
            final = "INSUFFICIENT EVIDENCE"
        elif optimal_top_n < current_top_n:
            final = "TEST MORE CONCENTRATED SHADOW VARIANT"
        elif optimal_top_n > current_top_n:
            final = "TEST LESS CONCENTRATED SHADOW VARIANT"
        else:
            final = "KEEP CURRENT HOLDINGS STRUCTURE"
        answers[sleeve] = {
            "best_return_variant": best_return,
            "best_sharpe_variant": best_sharpe,
            "best_sortino_variant": best_sortino,
            "best_drawdown_adjusted_variant": best_drawdown_adjusted,
            "practical_pilot_variant": practical,
            "holdings_count_median_cagr_by_top_n": {str(int(k)): _round(v) for k, v in topn.items()},
            "cap_median_max_drawdown_by_cap": {str(float(k)): _round(v) for k, v in cap_mdd.items()},
            "performance_improves_as_holdings_expand": _trend_label(topn),
            "score2_too_aggressive": score2_aggressive,
            "optimal_top_n": optimal_top_n,
            "optimal_max_position_weight": practical.get("max_position_weight") if practical else None,
            "final_recommendation": final,
        }
    return answers


def _trend_label(series: pd.Series) -> str:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if len(clean) < 2:
        return "INSUFFICIENT_DATA"
    first = float(clean.iloc[0])
    last = float(clean.iloc[-1])
    best_idx = int(clean.idxmax())
    if last > first and best_idx == int(clean.index[-1]):
        return "IMPROVES_THROUGH_LARGEST_TESTED_N"
    if last < first and best_idx == int(clean.index[0]):
        return "DEGRADES_AS_HOLDINGS_EXPAND"
    return f"NON_MONOTONIC_PEAK_TOP_{best_idx}"


def build_frontier_artifact(
    *,
    repo_root: Path,
    artifact_date: str,
    start_date: str = DEFAULT_START_DATE,
    end_date: str = DEFAULT_END_DATE,
    panel_path: Path = DEFAULT_PANEL_PATH,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    top_n_values: tuple[int, ...] = DEFAULT_TOP_N,
    weighting_methods: tuple[str, ...] = DEFAULT_WEIGHT_METHODS,
    max_position_weights: tuple[float, ...] = DEFAULT_MAX_POSITION_WEIGHTS,
    min_position_weights: tuple[float, ...] = DEFAULT_MIN_POSITION_WEIGHTS,
) -> dict[str, Any]:
    resolved_output_root = output_root if output_root.is_absolute() else repo_root / output_root
    output_dir = resolved_output_root / artifact_date
    resolved_panel = repo_root / panel_path
    resolved_manifest = repo_root / manifest_path
    frame, returns_matrix, trading_dates, trailing_vol = load_canonical_signal_inputs(
        panel_path=resolved_panel,
        start_date=start_date,
        end_date=end_date,
        include_trailing_vol="inverse_vol" in set(weighting_methods),
    )
    daily_frames: dict[pd.Timestamp, CandidatePack] = {}
    candidate_columns = ["ticker", "momentum_score", "momentum_rank", "signal_ready"]
    for dt, group in frame.groupby("date", sort=False):
        candidates = group[group["signal_ready"]].copy()
        if candidates.empty:
            daily_frames[dt] = {"ordered": [], "by_ticker": {}}
            continue
        candidates["ticker"] = candidates["ticker"].astype(str)
        candidates = candidates[candidate_columns].sort_values(["momentum_score", "ticker"], ascending=[False, True])
        daily_frames[dt] = _candidate_pack_from_frame(candidates, pre_sorted=True)
    returns_by_date: dict[pd.Timestamp, dict[str, float]] = {
        dt: {
            str(ticker): float(value)
            for ticker, value in returns_matrix.loc[dt].dropna().items()
        }
        for dt in trading_dates
    }
    current_specs = _current_strategy_specs()
    rows: list[dict[str, Any]] = []
    total = len(TARGET_SLEEVES) * len(top_n_values) * len(weighting_methods) * len(max_position_weights) * len(min_position_weights)
    completed = 0
    for sleeve in TARGET_SLEEVES:
        transaction_cost_bps = float(current_specs[sleeve]["transaction_cost_bps"])
        exit_rank_multiple = float(current_specs[sleeve]["exit_rank_multiple"])
        for top_n in top_n_values:
            for method in weighting_methods:
                for cap in max_position_weights:
                    for floor in min_position_weights:
                        spec = VariantSpec(
                            sleeve=sleeve,
                            top_n=int(top_n),
                            weighting_method=method,
                            max_position_weight=float(cap),
                            min_position_weight=float(floor),
                        )
                        rows.append(
                            run_variant(
                                spec=spec,
                                daily_frames=daily_frames,
                                returns_by_date=returns_by_date,
                                trading_dates=trading_dates,
                                trailing_vol=trailing_vol,
                                transaction_cost_bps=transaction_cost_bps,
                                exit_rank_multiple=exit_rank_multiple,
                            )
                        )
                        completed += 1
                        if completed % 100 == 0:
                            print(f"[FRONTIER] completed {completed}/{total}", flush=True)

    practical_rows = _practical_score_rows(rows)
    practical_score_by_id = {
        row["variant_id"]: row.get("practical_score")
        for row in practical_rows
        if row.get("variant_id")
    }
    for row in rows:
        row["practical_score"] = practical_score_by_id.get(row["variant_id"])
    topn_rows = _topn_summary(rows)
    method_rows = _method_summary(rows)
    cap_rows = _cap_summary(rows)
    frontier_rows = []
    for sleeve in TARGET_SLEEVES:
        sleeve_rows = [row for row in rows if row["sleeve"] == sleeve]
        frontier_rows.extend(_pareto_frontier(sleeve_rows, risk_field="max_drawdown"))
        frontier_rows.extend(_pareto_frontier(sleeve_rows, risk_field="volatility"))
    sleeve_answers = _sleeve_answers(rows, practical_rows)

    manifest = json.loads(resolved_manifest.read_text(encoding="utf-8")) if resolved_manifest.exists() else {}
    payload = {
        "schema_version": SCHEMA_VERSION,
        "artifact_date": artifact_date,
        "generated_at": datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat(),
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "production_impact": "none",
        "runtime_behavior_changed": False,
        "scope": {
            "included_sleeves": list(TARGET_SLEEVES),
            "excluded_sleeves": ["caerus_lyra", "caerus_phoenix", "caerus_cygnus", "caerus_cassiopeia"],
        },
        "date_window": {"start_date": start_date, "end_date": end_date},
        "inputs": {
            "panel_path": str(panel_path),
            "panel_sha256": sha256_file(resolved_panel),
            "manifest_path": str(manifest_path),
            "manifest_sha256": sha256_file(resolved_manifest) if resolved_manifest.exists() else None,
            "manifest_membership_certification_status": manifest.get("membership_certification_status"),
            "manifest_membership_certification_methods": manifest.get("membership_certification_methods"),
            "manifest_decision_grade_blockers": manifest.get("decision_grade_blockers"),
            "manifest_lineage_digest": manifest.get("lineage_digest"),
            "identity_key": manifest.get("identity_key"),
            "ticker_role": manifest.get("ticker_role"),
            "source_paths": manifest.get("source_paths"),
            "source_hashes": manifest.get("source_hashes"),
            "data_limitation_status": "NOT_DECISION_GRADE_MEMBERSHIP",
        },
        "methodology": {
            "signal_source": "research.alpha_lab_v1.signals.build_alpha_lab_signal_frame",
            "score_formula": "momentum_score = 0.5*r12_1 + 0.3*r6_1 + 0.2*r3",
            "decision_timing": (
                "Weights are fixed from signals available as of each decision date close; "
                "next close-to-close returns are applied after the decision date."
            ),
            "polaris_rule_preserved": "Daily rebalance; no rank-decay exit.",
            "orion_rule_preserved": "Daily rebalance; existing holdings retained while rank <= top_n*2, then filled by current rank.",
            "weighting_methods": {
                "equal": "Equal raw weight across selected names.",
                "rank": "Raw weight = 1 / current cross-sectional momentum rank.",
                "score": "Raw weight = positive shifted momentum_score; inverse-rank fallback when scores are flat.",
                "score2": "Raw weight = squared positive shifted momentum_score; inverse-rank fallback when scores are flat.",
                "inverse_vol": "Implemented but not included by default because Polaris/Orion do not currently use inverse-vol sizing.",
            },
            "constraint_handling": (
                "Raw weights are normalized, capped with redistribution to uncapped names, "
                "then positions below the minimum floor are dropped and the process repeats. "
                "Unallocated capital remains cash with zero return."
            ),
            "transaction_cost_bps": {
                sleeve: current_specs[sleeve]["transaction_cost_bps"]
                for sleeve in TARGET_SLEEVES
            },
            "tested_top_n": list(top_n_values),
            "tested_weighting_methods": list(weighting_methods),
            "tested_max_position_weights": list(max_position_weights),
            "tested_min_position_weights": list(min_position_weights),
        },
        "known_limitations": [
            "FR-068 date-effective large-cap membership remains blocked; canonical membership currently uses current scalemarketcap.",
            "The test avoids data/universe.csv and uses security_id-keyed canonical replay artifacts, but membership is not decision-grade.",
            "Sharadar SEP closeadj prices are available for the current large-cap family; omitted historical large-cap names remain possible.",
            "No live/paper fills, residual lots, tax lots, borrow, liquidity, slippage beyond turnover cost, or sector/factor constraints are modeled.",
            "SPY benchmark alpha is not claimed because SPY is not part of the common-stock canonical replay family; alpha_per_dollar_deployed is a return-per-gross-exposure proxy.",
        ],
        "variant_count": len(rows),
        "current_strategy_specs": current_specs,
        "best_variants": sleeve_answers,
        "digest": None,
    }
    payload["digest"] = stable_digest({k: v for k, v in payload.items() if k != "digest"})

    _write_csv(output_dir / "variant_metrics.csv", rows)
    _write_csv(output_dir / "topn_summary.csv", topn_rows)
    _write_csv(output_dir / "weighting_method_summary.csv", method_rows)
    _write_csv(output_dir / "max_position_summary.csv", cap_rows)
    _write_csv(output_dir / "frontier_points.csv", frontier_rows)
    _write_csv(output_dir / "practical_score_rankings.csv", practical_rows)

    payload_path = output_dir / "holdings_concentration_frontier.json"
    _write_json(payload_path, payload)
    report_path = output_dir / "holdings_concentration_frontier.md"
    report_path.write_text(_render_markdown(payload, rows, topn_rows, frontier_rows), encoding="utf-8")
    plot_paths = _write_plots(output_dir, rows)
    payload["artifact_paths"] = {
        "json": str(payload_path),
        "report": str(report_path),
        "variant_metrics": str(output_dir / "variant_metrics.csv"),
        "topn_summary": str(output_dir / "topn_summary.csv"),
        "frontier_points": str(output_dir / "frontier_points.csv"),
        "practical_score_rankings": str(output_dir / "practical_score_rankings.csv"),
        **plot_paths,
    }
    _write_json(payload_path, payload)
    report_path.write_text(_render_markdown(payload, rows, topn_rows, frontier_rows), encoding="utf-8")
    return payload


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    number = _round(value, 6)
    if number is None:
        return "n/a"
    return f"{number * 100:.2f}%"


def _fmt_num(value: Any) -> str:
    number = _round(value, 4)
    return "n/a" if number is None else f"{number:.4f}"


def _short_variant(row: dict[str, Any] | None) -> str:
    if not row:
        return "n/a"
    return (
        f"{row.get('variant_id')} "
        f"(CAGR {_fmt_pct(row.get('cagr'))}, Sharpe {_fmt_num(row.get('sharpe'))}, "
        f"MDD {_fmt_pct(row.get('max_drawdown'))})"
    )


def _render_markdown(
    payload: dict[str, Any],
    rows: list[dict[str, Any]],
    topn_rows: list[dict[str, Any]],
    frontier_rows: list[dict[str, Any]],
) -> str:
    lines = [
        "# Polaris / Orion Holdings Concentration Frontier",
        "",
        f"Date: {payload['artifact_date']}",
        "Scope: Research-only; Polaris and Orion only.",
        "Runtime impact: none.",
        "",
        "## Executive Summary",
        "",
    ]
    for sleeve in TARGET_SLEEVES:
        answer = payload["best_variants"][sleeve]
        practical = answer.get("practical_pilot_variant")
        lines.extend([
            f"### {sleeve}",
            "",
            f"- Final recommendation: `{answer['final_recommendation']}`",
            f"- Practical pilot variant: {_short_variant(practical)}",
            f"- Best return variant: {_short_variant(answer.get('best_return_variant'))}",
            f"- Best Sharpe variant: {_short_variant(answer.get('best_sharpe_variant'))}",
            f"- Best Sortino variant: {_short_variant(answer.get('best_sortino_variant'))}",
            f"- Best drawdown-adjusted variant: {_short_variant(answer.get('best_drawdown_adjusted_variant'))}",
            "",
        ])
    lines.extend([
        "## Data Lineage",
        "",
        f"- Panel: `{payload['inputs']['panel_path']}`",
        f"- Panel SHA256: `{payload['inputs']['panel_sha256']}`",
        f"- Manifest: `{payload['inputs']['manifest_path']}`",
        f"- Manifest membership status: `{payload['inputs']['manifest_membership_certification_status']}`",
        f"- Manifest membership methods: `{payload['inputs']['manifest_membership_certification_methods']}`",
        f"- Decision-grade blockers inherited: `{payload['inputs']['manifest_decision_grade_blockers']}`",
        f"- Lineage digest: `{payload['inputs']['manifest_lineage_digest']}`",
        "",
        "## Methodology",
        "",
        payload["methodology"]["decision_timing"],
        "",
        "- Top-N tested: " + ", ".join(str(x) for x in payload["methodology"]["tested_top_n"]),
        "- Weighting methods tested: " + ", ".join(payload["methodology"]["tested_weighting_methods"]),
        "- Max position caps tested: " + ", ".join(_fmt_pct(x) for x in payload["methodology"]["tested_max_position_weights"]),
        "- Minimum position floors tested: " + ", ".join("none" if x == 0 else _fmt_pct(x) for x in payload["methodology"]["tested_min_position_weights"]),
        "",
        "## Required Answers",
        "",
    ])
    for sleeve in TARGET_SLEEVES:
        answer = payload["best_variants"][sleeve]
        practical = answer.get("practical_pilot_variant") or {}
        lines.extend([
            f"### {sleeve}",
            "",
            f"1. Holdings expansion pattern: `{answer['performance_improves_as_holdings_expand']}`.",
            f"2. Diminishing returns point: practical optimum top N = `{answer['optimal_top_n']}`.",
            "3. Top 3 too concentrated: inspect top-3 rows in `variant_metrics.csv`; "
            "the practical ranking penalizes drawdown, turnover, and cash.",
            "4. Top 10 too diluted: compare `topn_summary.csv` against the practical optimum.",
            f"5. Optimal holdings count: `{answer['optimal_top_n']}`.",
            f"6. Optimal max-position cap: `{_fmt_pct(answer['optimal_max_position_weight'])}`.",
            f"7. Score2 too aggressive: `{answer['score2_too_aggressive']}`.",
            "8. Concentration/drawdown: see `max_position_summary.csv` and frontier plots.",
            f"9. Shadow-test candidate: `{practical.get('variant_id')}`.",
            "",
        ])
    lines.extend([
        "## Holdings Count Summary",
        "",
        "| Sleeve | Top N | Median CAGR | Best CAGR | Best Sharpe | Least-bad MDD |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for row in topn_rows:
        lines.append(
            f"| {row['sleeve']} | {row['top_n']} | {_fmt_pct(row['median_cagr'])} | "
            f"{_fmt_pct(row['best_cagr'])} | {_fmt_num(row['best_sharpe'])} | "
            f"{_fmt_pct(row['least_bad_max_drawdown'])} |"
        )
    lines.extend([
        "",
        "## Frontier Artifacts",
        "",
        "- Full variant metrics: `variant_metrics.csv`",
        "- Pareto frontier points: `frontier_points.csv`",
        "- Practical rankings: `practical_score_rankings.csv`",
        "- Plots: `frontier_max_drawdown_cagr.png`, `frontier_volatility_cagr.png` when matplotlib is available.",
        "",
        "## Risks / Limitations",
        "",
    ])
    for item in payload["known_limitations"]:
        lines.append(f"- {item}")
    lines.extend([
        "",
        "## Validation Commands",
        "",
        "```bash",
        "PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 -m py_compile \\",
        "  research/holdings_concentration_frontier.py \\",
        "  scripts/research/build_holdings_concentration_frontier.py",
        "",
        "PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 -m pytest -p no:cacheprovider \\",
        "  Tests/test_holdings_concentration_frontier.py",
        "",
        "PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 scripts/research/build_holdings_concentration_frontier.py \\",
        "  --artifact-date " + payload["artifact_date"],
        "",
        "git diff --check",
        "```",
        "",
    ])
    return "\n".join(lines)


def _write_plots(output_dir: Path, rows: list[dict[str, Any]]) -> dict[str, str]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return {}
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {}
    paths: dict[str, str] = {}
    for risk_field, filename, xlabel in (
        ("max_drawdown", "frontier_max_drawdown_cagr.png", "Max drawdown (absolute)"),
        ("volatility", "frontier_volatility_cagr.png", "Volatility"),
    ):
        fig, ax = plt.subplots(figsize=(10, 6))
        for sleeve, group in frame.groupby("sleeve"):
            x = pd.to_numeric(group[risk_field], errors="coerce")
            if risk_field == "max_drawdown":
                x = x.abs()
            y = pd.to_numeric(group["cagr"], errors="coerce")
            ax.scatter(x, y, s=22, alpha=0.45, label=sleeve)
            practical = group.sort_values("practical_score", ascending=False).head(1) if "practical_score" in group else pd.DataFrame()
            if not practical.empty:
                prow = practical.iloc[0]
                px = abs(float(prow[risk_field])) if risk_field == "max_drawdown" else float(prow[risk_field])
                py = float(prow["cagr"])
                ax.annotate(
                    f"{sleeve} Top {int(prow['top_n'])} {prow['weighting_method']} cap {int(round(float(prow['max_position_weight']) * 100))}%",
                    (px, py),
                    fontsize=8,
                    xytext=(5, 5),
                    textcoords="offset points",
                )
        ax.set_xlabel(xlabel)
        ax.set_ylabel("CAGR")
        ax.set_title("Holdings Concentration Frontier")
        ax.grid(True, alpha=0.25)
        ax.legend()
        path = output_dir / filename
        fig.tight_layout()
        fig.savefig(path, dpi=160)
        plt.close(fig)
        paths[filename.removesuffix(".png")] = str(path)
    return paths
