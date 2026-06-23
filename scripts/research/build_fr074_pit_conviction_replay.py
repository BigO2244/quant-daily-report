"""Build FR-074 PIT conviction-allocation replay artifacts.

Research-only. This script replays allocation policies from stored decision-time
shadow-candidate artifacts. It does not import production allocation, broker,
execution, scheduler, or order-routing code.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from alpha_stack.research.metrics import (  # noqa: E402
    annualised_vol,
    max_drawdown,
    sharpe_ratio,
    sortino_ratio,
)

SCHEMA_VERSION = "caerus_fr074_pit_conviction_replay_v1"

STRATEGIES = ("caerus_polaris", "caerus_orion", "caerus_lyra")
CONVICTION_METHODS = ("rank_weighted", "score_weighted", "score2_weighted")
MAX_POSITION_WEIGHTS = (0.10, 0.20, 0.30, 0.40)
MIN_POSITION_WEIGHTS = (0.00, 0.01, 0.02, 0.03)
FORWARD_HORIZONS = (1, 5, 20)
TRADING_DAYS_PER_YEAR = 252

CURRENT_POLICY_ID = "current_artifact_target_proxy"
DEFAULT_SHADOW_ROOT = Path("outputs/shadow_candidates")
DEFAULT_PRICE_CACHE_PATH = Path("outputs/research/flow_detection_v1/price_panel.parquet")
DEFAULT_OUTPUT_ROOT = Path("outputs/research/fr074_pit_conviction_replay")
DEFAULT_TRANSACTION_COST_BPS = 10.0
DEFAULT_CURRENT_MAX_POSITION = 0.10
DEFAULT_CURRENT_MIN_GROSS_EXPOSURE = 0.90
MIN_DECISION_GRADE_OBSERVATIONS = 60

INSPECTED_FILES = [
    "outputs/shadow_candidates/<date>/caerus_polaris.json",
    "outputs/shadow_candidates/<date>/caerus_orion.json",
    "outputs/shadow_candidates/<date>/caerus_lyra.json",
    "outputs/research/pit_rebaseline/orion_lyra_matched_2026-06-17.json",
    "outputs/research/pit_rebaseline/polaris_2026-06-10.json",
    "outputs/research/pit_rebaseline/polaris_priced_2026-06-10.json",
    "outputs/research/pit_rebaseline/caerus_large_cap_family.json",
    "outputs/precompute/2026-03-24/daily_snapshot.json",
    "outputs/precompute/2026-03-24/signals.json",
    "outputs/precompute/2026-03-24/planned_execution_payload.json",
    "outputs/portfolio_history/2026-04-30/weights_snapshot.json",
    "outputs/portfolio_history/2026-04-30/holdings_snapshot.json",
    "outputs/target_attainment/2026-06-09/target_attainment_2026-06-09.json",
    "research/alpha_lab_v2/engine.py",
    "research/shadow_tracking/run.py",
    "scripts/refresh_shadow_scorecard_artifacts.py",
]


@dataclass(frozen=True)
class DecisionSnapshot:
    trade_date: str
    strategy_payloads: dict[str, dict[str, Any]]
    source_paths: dict[str, str]


@dataclass(frozen=True)
class PolicySpec:
    policy_id: str
    policy_family: str
    method: str
    max_position_weight: float
    min_position_weight: float


def _round(value: Any, digits: int = 10) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(out):
        return default
    return out


def _sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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


def _repo_path(repo: Path, path: Path) -> Path:
    return path if path.is_absolute() else repo / path


def _repo_relative(repo: Path, path: Path) -> str:
    return str(path.resolve().relative_to(repo.resolve()))


def _normalise_ticker(value: Any) -> str:
    return str(value or "").strip().upper()


def _is_date_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        pd.Timestamp(path.name)
    except ValueError:
        return False
    return len(path.name) == 10


def discover_shadow_snapshots(
    repo: Path,
    *,
    shadow_root: Path = DEFAULT_SHADOW_ROOT,
) -> tuple[list[DecisionSnapshot], list[dict[str, Any]]]:
    root = repo / shadow_root
    snapshots: list[DecisionSnapshot] = []
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return snapshots, rows

    for dated_dir in sorted(path for path in root.iterdir() if _is_date_dir(path)):
        payloads: dict[str, dict[str, Any]] = {}
        source_paths: dict[str, str] = {}
        row: dict[str, Any] = {
            "trade_date": dated_dir.name,
            "artifact_dir": str(dated_dir.relative_to(repo)),
            "expected_strategy_count": len(STRATEGIES),
            "available_strategy_count": 0,
            "complete_for_replay": False,
        }
        for strategy in STRATEGIES:
            path = dated_dir / f"{strategy}.json"
            exists = path.exists()
            row[f"{strategy}_exists"] = exists
            if not exists:
                continue
            try:
                payload = _read_json(path)
            except Exception as exc:  # pragma: no cover - defensive artifact inventory
                row[f"{strategy}_read_error"] = str(exc)
                continue
            payloads[strategy] = payload
            source_paths[strategy] = str(path.relative_to(repo))
            row["available_strategy_count"] += 1
            row[f"{strategy}_target_count"] = len(payload.get("target_weights") or {})
            row[f"{strategy}_rank_table_count"] = len(payload.get("rank_table") or [])
            row[f"{strategy}_trade_date"] = payload.get("trade_date")
            row[f"{strategy}_effective_trade_date"] = payload.get("effective_trade_date")
            row[f"{strategy}_sha256"] = _sha256(path)

        complete = all(strategy in payloads for strategy in STRATEGIES)
        has_rank_and_weights = all(
            bool((payloads.get(strategy) or {}).get("target_weights"))
            and bool((payloads.get(strategy) or {}).get("rank_table"))
            for strategy in STRATEGIES
        )
        row["complete_for_replay"] = bool(complete and has_rank_and_weights)
        rows.append(row)
        if row["complete_for_replay"]:
            snapshots.append(
                DecisionSnapshot(
                    trade_date=dated_dir.name,
                    strategy_payloads=payloads,
                    source_paths=source_paths,
                )
            )
    return snapshots, rows


def load_close_matrix(repo: Path, *, price_cache_path: Path = DEFAULT_PRICE_CACHE_PATH) -> tuple[pd.DataFrame, dict[str, Any]]:
    path = repo / price_cache_path
    if not path.exists():
        return pd.DataFrame(), {
            "price_cache_path": str(price_cache_path),
            "price_cache_exists": False,
            "reason": "price_cache_missing",
        }

    panel = pd.read_parquet(path)
    if not {"date", "ticker", "close"}.issubset(panel.columns):
        return pd.DataFrame(), {
            "price_cache_path": str(price_cache_path),
            "price_cache_exists": True,
            "reason": "price_cache_missing_required_columns",
        }
    panel = panel[["date", "ticker", "close"]].copy()
    panel["date"] = pd.to_datetime(panel["date"]).dt.normalize()
    panel["ticker"] = panel["ticker"].astype(str).str.upper()
    panel["close"] = pd.to_numeric(panel["close"], errors="coerce")
    close = panel.pivot_table(index="date", columns="ticker", values="close", aggfunc="last").sort_index()
    meta = {
        "price_cache_path": str(price_cache_path),
        "price_cache_exists": True,
        "price_cache_sha256": _sha256(path),
        "price_date_start": str(close.index.min().date()) if not close.empty else None,
        "price_date_end": str(close.index.max().date()) if not close.empty else None,
        "price_ticker_count": int(len(close.columns)),
        "price_observation_days": int(len(close.index)),
        "price_role": "forward_return_measurement_only_not_construction_input",
    }
    return close, meta


def _cap_and_distribute(scores: pd.Series, max_position_weight: float) -> pd.Series:
    scores = pd.to_numeric(scores, errors="coerce").fillna(0.0)
    scores = scores[scores > 0.0].astype(float)
    if scores.empty:
        return pd.Series(dtype=float)

    cap = float(max_position_weight)
    if cap <= 0:
        return pd.Series(dtype=float)

    capped = pd.Series(0.0, index=scores.index, dtype=float)
    remaining_scores = scores.copy()
    remaining_capital = 1.0
    for _ in range(len(scores) + 1):
        if remaining_scores.empty or remaining_capital <= 1e-12:
            break
        proposal = remaining_scores / float(remaining_scores.sum()) * remaining_capital
        over_cap = proposal[proposal > cap]
        if over_cap.empty:
            capped.loc[proposal.index] = proposal
            break
        capped.loc[over_cap.index] = cap
        remaining_capital -= cap * len(over_cap)
        remaining_scores = remaining_scores.drop(over_cap.index)
    return capped[capped > 1e-12].sort_values(ascending=False)


def allocate_from_scores(
    scores: pd.Series,
    *,
    max_position_weight: float,
    min_position_weight: float,
) -> pd.Series:
    eligible = pd.to_numeric(scores, errors="coerce").fillna(0.0)
    eligible = eligible[eligible > 0.0].astype(float)
    if eligible.empty:
        return pd.Series(dtype=float)

    while not eligible.empty:
        weights = _cap_and_distribute(eligible, max_position_weight=max_position_weight)
        if min_position_weight <= 0 or weights.empty:
            return weights
        keep = weights[weights >= min_position_weight].index
        if len(keep) == len(weights):
            return weights
        eligible = eligible.reindex(keep).dropna()
    return pd.Series(dtype=float)


def _current_artifact_weights(snapshot: DecisionSnapshot) -> pd.Series:
    budget = 1.0 / len(STRATEGIES)
    combined = pd.Series(dtype=float)
    for strategy in STRATEGIES:
        raw = (snapshot.strategy_payloads.get(strategy) or {}).get("target_weights") or {}
        sleeve = pd.Series(
            {_normalise_ticker(ticker): _as_float(weight, 0.0) or 0.0 for ticker, weight in raw.items()},
            dtype=float,
        )
        sleeve = sleeve[sleeve > 1e-12]
        combined = combined.add(sleeve * budget, fill_value=0.0)

    combined = combined[combined > 1e-12].sort_values(ascending=False)
    if combined.empty:
        return combined

    combined = combined.clip(upper=DEFAULT_CURRENT_MAX_POSITION)
    gross = float(combined.sum())
    if 0.0 < gross < DEFAULT_CURRENT_MIN_GROSS_EXPOSURE:
        headroom = (DEFAULT_CURRENT_MAX_POSITION - combined).clip(lower=0.0)
        total_headroom = float(headroom.sum())
        if total_headroom > 1e-12:
            needed = min(DEFAULT_CURRENT_MIN_GROSS_EXPOSURE - gross, total_headroom)
            combined = combined + headroom / total_headroom * needed
    return combined[combined > 1e-12].sort_values(ascending=False)


def candidate_table(snapshot: DecisionSnapshot) -> pd.DataFrame:
    by_ticker: dict[str, dict[str, Any]] = {}
    for strategy in STRATEGIES:
        payload = snapshot.strategy_payloads.get(strategy) or {}
        selected = {_normalise_ticker(ticker) for ticker in (payload.get("target_weights") or {})}
        for row in payload.get("rank_table") or []:
            ticker = _normalise_ticker(row.get("ticker"))
            if not ticker:
                continue
            rank = _as_float(row.get("momentum_rank"))
            score = _as_float(row.get("momentum_score"))
            item = by_ticker.setdefault(
                ticker,
                {
                    "trade_date": snapshot.trade_date,
                    "ticker": ticker,
                    "best_rank": None,
                    "max_score": None,
                    "mean_score_numerator": 0.0,
                    "mean_score_denominator": 0,
                    "source_count": 0,
                    "selected_source_count": 0,
                    "source_strategies": [],
                },
            )
            item["source_count"] += 1
            item["source_strategies"].append(strategy)
            if ticker in selected or bool(row.get("is_selected")):
                item["selected_source_count"] += 1
            if rank is not None:
                item["best_rank"] = rank if item["best_rank"] is None else min(float(item["best_rank"]), rank)
            if score is not None:
                item["max_score"] = score if item["max_score"] is None else max(float(item["max_score"]), score)
                item["mean_score_numerator"] += score
                item["mean_score_denominator"] += 1

    rows: list[dict[str, Any]] = []
    for item in by_ticker.values():
        denominator = int(item.pop("mean_score_denominator"))
        numerator = float(item.pop("mean_score_numerator"))
        item["mean_score"] = numerator / denominator if denominator else None
        item["source_strategies"] = ",".join(sorted(set(item["source_strategies"])))
        item["rank_bucket"] = _exclusive_rank_bucket(item.get("best_rank"))
        rows.append(item)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["best_rank"] = pd.to_numeric(out["best_rank"], errors="coerce")
    out["max_score"] = pd.to_numeric(out["max_score"], errors="coerce")
    out["mean_score"] = pd.to_numeric(out["mean_score"], errors="coerce")
    out["source_count"] = pd.to_numeric(out["source_count"], errors="coerce").fillna(0).astype(int)
    out["selected_source_count"] = pd.to_numeric(out["selected_source_count"], errors="coerce").fillna(0).astype(int)
    return out.sort_values(["best_rank", "max_score", "ticker"], ascending=[True, False, True]).reset_index(drop=True)


def _positive_conviction_scores(candidates: pd.DataFrame, method: str) -> pd.Series:
    if candidates.empty:
        return pd.Series(dtype=float)

    tickers = candidates["ticker"].astype(str)
    ranks = pd.to_numeric(candidates["best_rank"], errors="coerce").fillna(float(len(candidates))).clip(lower=1.0)
    scores = pd.to_numeric(candidates["max_score"], errors="coerce").fillna(0.0)

    if method == "rank_weighted":
        values = 1.0 / ranks
    elif method in {"score_weighted", "score2_weighted"}:
        shifted = scores - min(float(scores.min()), 0.0)
        if float(shifted.sum()) <= 1e-12 or float(shifted.max() - shifted.min()) <= 1e-12:
            shifted = 1.0 / ranks
        else:
            shifted = shifted + 1e-12
        values = shifted.pow(2.0) if method == "score2_weighted" else shifted
    else:
        raise ValueError(f"unsupported conviction method: {method}")

    values.index = pd.Index(tickers, dtype=str)
    return values.astype(float)


def conviction_weights(snapshot: DecisionSnapshot, spec: PolicySpec) -> pd.Series:
    candidates = candidate_table(snapshot)
    scores = _positive_conviction_scores(candidates, spec.method)
    return allocate_from_scores(
        scores,
        max_position_weight=spec.max_position_weight,
        min_position_weight=spec.min_position_weight,
    )


def policy_specs() -> list[PolicySpec]:
    specs: list[PolicySpec] = []
    for method in CONVICTION_METHODS:
        for cap in MAX_POSITION_WEIGHTS:
            for floor in MIN_POSITION_WEIGHTS:
                specs.append(
                    PolicySpec(
                        policy_id=f"conviction_{method}_cap{int(cap * 100)}_min{int(floor * 100)}",
                        policy_family="conviction",
                        method=method,
                        max_position_weight=cap,
                        min_position_weight=floor,
                    )
                )
    return specs


def _forward_returns(close: pd.DataFrame, trade_date: str, horizon: int) -> pd.Series:
    if close.empty:
        return pd.Series(dtype=float)
    dt = pd.Timestamp(trade_date).normalize()
    if dt not in close.index:
        return pd.Series(dtype=float)
    loc = close.index.get_loc(dt)
    if isinstance(loc, slice):
        loc = loc.start
    if not isinstance(loc, int) or loc + horizon >= len(close.index):
        return pd.Series(dtype=float)
    start = pd.to_numeric(close.iloc[loc], errors="coerce")
    end = pd.to_numeric(close.iloc[loc + horizon], errors="coerce")
    out = end / start - 1.0
    return out.replace([math.inf, -math.inf], pd.NA).dropna().astype(float)


def _exclusive_rank_bucket(rank: Any) -> str:
    value = _as_float(rank)
    if value is None:
        return "rank_missing"
    if value <= 1:
        return "top_1"
    if value <= 3:
        return "rank_2_3"
    if value <= 5:
        return "rank_4_5"
    if value <= 10:
        return "rank_6_10"
    return "lower_11_15"


def _rank_top_set(snapshot: DecisionSnapshot, cutoff: int) -> set[str]:
    candidates = candidate_table(snapshot)
    if candidates.empty:
        return set()
    ranks = pd.to_numeric(candidates["best_rank"], errors="coerce")
    return set(candidates.loc[ranks <= cutoff, "ticker"].astype(str))


def _policy_weight_for_snapshot(snapshot: DecisionSnapshot, spec: PolicySpec | None) -> pd.Series:
    if spec is None:
        return _current_artifact_weights(snapshot)
    return conviction_weights(snapshot, spec)


def replay_policy(
    snapshots: list[DecisionSnapshot],
    close: pd.DataFrame,
    *,
    spec: PolicySpec | None,
    transaction_cost_bps: float = DEFAULT_TRANSACTION_COST_BPS,
) -> dict[str, Any]:
    policy_id = CURRENT_POLICY_ID if spec is None else spec.policy_id
    valid_snapshots = [snapshot for snapshot in snapshots if pd.Timestamp(snapshot.trade_date).normalize() in close.index]
    valid_snapshots = sorted(valid_snapshots, key=lambda item: item.trade_date)
    if not valid_snapshots or close.empty:
        return {
            "policy_id": policy_id,
            "returns": pd.Series(dtype=float),
            "weights": pd.DataFrame(),
            "cash": pd.Series(dtype=float),
            "turnover": pd.Series(dtype=float),
            "top_ranked_deployment": pd.Series(dtype=float),
            "decision_weights": {},
        }

    decision_dates = [pd.Timestamp(snapshot.trade_date).normalize() for snapshot in valid_snapshots]
    decision_weights = {
        snapshot.trade_date: _policy_weight_for_snapshot(snapshot, spec).sort_values(ascending=False)
        for snapshot in valid_snapshots
    }
    snapshot_by_date = {
        pd.Timestamp(snapshot.trade_date).normalize(): snapshot
        for snapshot in valid_snapshots
    }
    start = decision_dates[0]
    end = min(decision_dates[-1], close.index[-2] if len(close.index) > 1 else close.index[-1])
    replay_dates = close.index[(close.index >= start) & (close.index <= end)]

    returns_rows: list[tuple[pd.Timestamp, float]] = []
    weight_rows: list[pd.Series] = []
    cash_rows: list[tuple[pd.Timestamp, float]] = []
    turnover_rows: list[tuple[pd.Timestamp, float]] = []
    top_ranked_rows: list[tuple[pd.Timestamp, float]] = []
    previous_decision_weight = pd.Series(dtype=float)
    active_snapshot = valid_snapshots[0]
    active_weight = decision_weights[active_snapshot.trade_date]
    decision_index = 0

    for dt in replay_dates:
        while decision_index + 1 < len(decision_dates) and decision_dates[decision_index + 1] <= dt:
            decision_index += 1
            active_snapshot = snapshot_by_date[decision_dates[decision_index]]
            active_weight = decision_weights[active_snapshot.trade_date]

        is_rebalance_date = dt in set(decision_dates)
        if is_rebalance_date:
            aligned_prev = previous_decision_weight.reindex(active_weight.index.union(previous_decision_weight.index)).fillna(0.0)
            aligned_new = active_weight.reindex(aligned_prev.index).fillna(0.0)
            turnover = float((aligned_new - aligned_prev).abs().sum())
            previous_decision_weight = active_weight.copy()
        else:
            turnover = 0.0

        forward = _forward_returns(close, str(dt.date()), 1)
        if forward.empty:
            continue
        aligned_returns = forward.reindex(active_weight.index).fillna(0.0)
        gross_return = float((active_weight * aligned_returns).sum())
        net_return = gross_return - turnover * (transaction_cost_bps / 10000.0)
        cash = max(0.0, 1.0 - float(active_weight.sum()))
        top10 = _rank_top_set(active_snapshot, 10)
        top_deployment = float(active_weight.reindex(sorted(top10)).fillna(0.0).sum()) if top10 else 0.0

        returns_rows.append((dt, net_return))
        row = active_weight.rename(dt)
        weight_rows.append(row)
        cash_rows.append((dt, cash))
        turnover_rows.append((dt, turnover))
        top_ranked_rows.append((dt, top_deployment))

    returns = pd.Series({dt: value for dt, value in returns_rows}, name="net_return", dtype=float).sort_index()
    weights = pd.DataFrame(weight_rows).fillna(0.0).sort_index() if weight_rows else pd.DataFrame()
    weights.columns = [str(col) for col in weights.columns]
    cash_series = pd.Series({dt: value for dt, value in cash_rows}, name="cash_weight", dtype=float).sort_index()
    turnover_series = pd.Series({dt: value for dt, value in turnover_rows}, name="turnover", dtype=float).sort_index()
    top_ranked = pd.Series({dt: value for dt, value in top_ranked_rows}, name="top10_deployment", dtype=float).sort_index()
    return {
        "policy_id": policy_id,
        "returns": returns,
        "weights": weights,
        "cash": cash_series,
        "turnover": turnover_series,
        "top_ranked_deployment": top_ranked,
        "decision_weights": decision_weights,
    }


def _policy_metrics(
    policy_id: str,
    returns: pd.Series,
    weights: pd.DataFrame,
    cash: pd.Series,
    turnover: pd.Series,
    top_ranked_deployment: pd.Series,
) -> dict[str, Any]:
    returns = returns.dropna()
    if returns.empty:
        return {
            "policy_id": policy_id,
            "observation_count": 0,
            "total_return": None,
            "cagr": None,
            "sharpe": None,
            "sortino": None,
            "max_drawdown": None,
            "volatility": None,
            "turnover": None,
            "average_holdings_count": None,
            "average_gross_exposure": None,
            "cash_drag": None,
            "average_hhi": None,
            "average_top1_weight": None,
            "average_top3_weight": None,
            "average_max_position_weight": None,
            "capital_deployed_top_ranked_ideas": None,
            "capital_deployment_efficiency": None,
        }

    nav = (1.0 + returns.fillna(0.0)).cumprod()
    nav_for_drawdown = pd.concat(
        [
            pd.Series([1.0], index=[returns.index[0] - pd.Timedelta(days=1)]),
            nav,
        ]
    )
    aligned_weights = weights.reindex(returns.index).fillna(0.0) if not weights.empty else pd.DataFrame(index=returns.index)
    holdings_count = (aligned_weights > 1e-12).sum(axis=1)
    gross = aligned_weights.sum(axis=1)
    sorted_weights = pd.DataFrame(
        [row[row > 1e-12].sort_values(ascending=False).reset_index(drop=True) for _, row in aligned_weights.iterrows()],
        index=aligned_weights.index,
    ).fillna(0.0)
    top1 = sorted_weights.iloc[:, :1].sum(axis=1) if not sorted_weights.empty else pd.Series(dtype=float)
    top3 = sorted_weights.iloc[:, :3].sum(axis=1) if not sorted_weights.empty else pd.Series(dtype=float)
    max_weight = sorted_weights.iloc[:, 0] if not sorted_weights.empty and 0 in sorted_weights.columns else pd.Series(dtype=float)
    hhi = aligned_weights.pow(2).sum(axis=1)
    total_return = float(nav.iloc[-1] - 1.0)
    avg_gross = float(gross.mean()) if not gross.empty else 0.0

    return {
        "policy_id": policy_id,
        "observation_count": int(len(returns)),
        "total_return": _round(total_return),
        "cagr": _round(_cagr_from_returns(returns)),
        "sharpe": _round(sharpe_ratio(returns)),
        "sortino": _round(sortino_ratio(returns)),
        "max_drawdown": _round(max_drawdown(nav_for_drawdown)),
        "volatility": _round(annualised_vol(returns)),
        "turnover": _round(float(turnover.reindex(returns.index).fillna(0.0).mean())) if not turnover.empty else None,
        "average_holdings_count": _round(float(holdings_count.mean())) if not holdings_count.empty else None,
        "average_gross_exposure": _round(avg_gross),
        "cash_drag": _round(float(cash.reindex(returns.index).fillna(0.0).mean())) if not cash.empty else None,
        "average_hhi": _round(float(hhi.mean())) if not hhi.empty else None,
        "average_top1_weight": _round(float(top1.mean())) if not top1.empty else None,
        "average_top3_weight": _round(float(top3.mean())) if not top3.empty else None,
        "average_max_position_weight": _round(float(max_weight.mean())) if not max_weight.empty else None,
        "capital_deployed_top_ranked_ideas": _round(float(top_ranked_deployment.reindex(returns.index).fillna(0.0).mean()))
        if not top_ranked_deployment.empty
        else None,
        "capital_deployment_efficiency": _round(total_return / avg_gross) if avg_gross > 1e-12 else None,
    }


def _cagr_from_returns(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    total = float((1.0 + returns.fillna(0.0)).prod())
    n_years = len(returns) / TRADING_DAYS_PER_YEAR
    if total <= 0.0 or n_years <= 0.0:
        return 0.0
    return float(total ** (1.0 / n_years) - 1.0)


def rank_bucket_forward_returns(
    snapshots: list[DecisionSnapshot],
    close: pd.DataFrame,
    *,
    horizons: tuple[int, ...] = FORWARD_HORIZONS,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    bucket_order = ["top_1", "rank_2_3", "rank_4_5", "rank_6_10", "lower_11_15"]
    for horizon in horizons:
        daily_bucket_returns: dict[str, list[float]] = {bucket: [] for bucket in bucket_order}
        daily_bucket_counts: dict[str, int] = {bucket: 0 for bucket in bucket_order}
        for snapshot in snapshots:
            candidates = candidate_table(snapshot)
            if candidates.empty:
                continue
            forward = _forward_returns(close, snapshot.trade_date, horizon)
            if forward.empty:
                continue
            candidates = candidates.copy()
            candidates["forward_return"] = forward.reindex(candidates["ticker"].astype(str)).to_numpy()
            for bucket, group in candidates.dropna(subset=["forward_return"]).groupby("rank_bucket"):
                if bucket not in daily_bucket_returns:
                    continue
                daily_bucket_returns[bucket].append(float(group["forward_return"].mean()))
                daily_bucket_counts[bucket] += int(len(group))
        for bucket in bucket_order:
            series = pd.Series(daily_bucket_returns[bucket], dtype=float)
            rows.append(
                {
                    "horizon_days": horizon,
                    "rank_bucket": bucket,
                    "bucket_definition": _bucket_definition(bucket),
                    "decision_date_count": int(len(series)),
                    "security_observation_count": int(daily_bucket_counts[bucket]),
                    "mean_forward_return": _round(series.mean()) if not series.empty else None,
                    "median_forward_return": _round(series.median()) if not series.empty else None,
                    "hit_rate": _round(float((series > 0).mean())) if not series.empty else None,
                    "annualized_mean_return_proxy": _round(float(series.mean()) * (TRADING_DAYS_PER_YEAR / horizon)) if not series.empty else None,
                }
            )
    return rows


def cutoff_forward_returns(
    snapshots: list[DecisionSnapshot],
    close: pd.DataFrame,
    *,
    horizons: tuple[int, ...] = FORWARD_HORIZONS,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for horizon in horizons:
        for cutoff in (1, 3, 5, 10, 15):
            daily_returns: list[float] = []
            security_count = 0
            for snapshot in snapshots:
                candidates = candidate_table(snapshot)
                if candidates.empty:
                    continue
                forward = _forward_returns(close, snapshot.trade_date, horizon)
                if forward.empty:
                    continue
                ranks = pd.to_numeric(candidates["best_rank"], errors="coerce")
                tickers = candidates.loc[ranks <= cutoff, "ticker"].astype(str)
                values = forward.reindex(tickers).dropna()
                if values.empty:
                    continue
                daily_returns.append(float(values.mean()))
                security_count += int(len(values))
            series = pd.Series(daily_returns, dtype=float)
            rows.append(
                {
                    "horizon_days": horizon,
                    "cutoff": f"top_{cutoff}",
                    "decision_date_count": int(len(series)),
                    "security_observation_count": int(security_count),
                    "mean_forward_return": _round(series.mean()) if not series.empty else None,
                    "median_forward_return": _round(series.median()) if not series.empty else None,
                    "hit_rate": _round(float((series > 0).mean())) if not series.empty else None,
                }
            )
    return rows


def _bucket_definition(bucket: str) -> str:
    return {
        "top_1": "best sleeve rank <= 1",
        "rank_2_3": "1 < best sleeve rank <= 3",
        "rank_4_5": "3 < best sleeve rank <= 5",
        "rank_6_10": "5 < best sleeve rank <= 10",
        "lower_11_15": "10 < best sleeve rank <= 15; rank tables are truncated at 15",
    }.get(bucket, "unknown")


def position_size_correlation_rows(
    snapshots: list[DecisionSnapshot],
    close: pd.DataFrame,
) -> list[dict[str, Any]]:
    policy_records: dict[str, list[dict[str, Any]]] = {CURRENT_POLICY_ID: []}
    for strategy in STRATEGIES:
        policy_records[f"{strategy}_artifact_targets"] = []

    for snapshot in snapshots:
        forward = _forward_returns(close, snapshot.trade_date, 1)
        if forward.empty:
            continue
        current = _current_artifact_weights(snapshot)
        ranks = candidate_table(snapshot).set_index("ticker") if not candidate_table(snapshot).empty else pd.DataFrame()
        for ticker, weight in current.items():
            policy_records[CURRENT_POLICY_ID].append(
                {
                    "ticker": ticker,
                    "weight": float(weight),
                    "next_day_return": _as_float(forward.get(ticker)),
                    "best_rank": _as_float(ranks.get("best_rank", pd.Series(dtype=float)).get(ticker)) if not ranks.empty else None,
                }
            )
        for strategy in STRATEGIES:
            raw = (snapshot.strategy_payloads.get(strategy) or {}).get("target_weights") or {}
            for ticker, weight in raw.items():
                ticker = _normalise_ticker(ticker)
                policy_records[f"{strategy}_artifact_targets"].append(
                    {
                        "ticker": ticker,
                        "weight": _as_float(weight, 0.0) or 0.0,
                        "next_day_return": _as_float(forward.get(ticker)),
                        "best_rank": _as_float(ranks.get("best_rank", pd.Series(dtype=float)).get(ticker)) if not ranks.empty else None,
                    }
                )

    rows: list[dict[str, Any]] = []
    for policy_id, records in sorted(policy_records.items()):
        frame = pd.DataFrame(records).dropna(subset=["weight", "next_day_return"])
        pearson = _safe_corr(frame, "weight", "next_day_return", method="pearson")
        spearman = _safe_corr(frame, "weight", "next_day_return", method="spearman")
        rows.append(
            {
                "policy_id": policy_id,
                "observation_count": int(len(frame)),
                "weight_return_pearson": _round(pearson),
                "weight_return_spearman": _round(spearman),
                "average_weight": _round(frame["weight"].mean()) if not frame.empty else None,
                "average_next_day_return": _round(frame["next_day_return"].mean()) if not frame.empty else None,
            }
        )
    return rows


def _safe_corr(frame: pd.DataFrame, left: str, right: str, *, method: str) -> float | None:
    if len(frame) < 5:
        return None
    if frame[left].nunique(dropna=True) < 2 or frame[right].nunique(dropna=True) < 2:
        return None
    return _as_float(frame[left].corr(frame[right], method=method))


def small_position_rows(policy_replays: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for policy_id, replay in sorted(policy_replays.items()):
        weights = replay.get("weights")
        cash = replay.get("cash")
        if not isinstance(weights, pd.DataFrame) or weights.empty:
            continue
        for threshold in (0.01, 0.02, 0.03):
            mask = (weights > 1e-12) & (weights < threshold)
            below = weights.where(mask, 0.0)
            counts = mask.sum(axis=1)
            rows.append(
                {
                    "policy_id": policy_id,
                    "threshold": threshold,
                    "average_capital_below_threshold": _round(float(below.sum(axis=1).mean())),
                    "max_capital_below_threshold": _round(float(below.sum(axis=1).max())),
                    "average_positions_below_threshold": _round(float(counts.mean())),
                    "max_positions_below_threshold": int(counts.max()) if not counts.empty else 0,
                    "average_cash_weight": _round(float(cash.mean())) if isinstance(cash, pd.Series) and not cash.empty else None,
                }
            )
    return rows


def reconstruction_assessment(
    repo: Path,
    inventory_rows: list[dict[str, Any]],
    snapshots: list[DecisionSnapshot],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    complete_dates = [snapshot.trade_date for snapshot in snapshots]
    all_dates = [str(row["trade_date"]) for row in inventory_rows]
    complete_count = len(complete_dates)
    rows = [
        {
            "source": "shadow_candidate_artifacts",
            "path_pattern": "outputs/shadow_candidates/<date>/<strategy>.json",
            "available_date_start": min(all_dates) if all_dates else None,
            "available_date_end": max(all_dates) if all_dates else None,
            "available_snapshot_count": len(all_dates),
            "replayable_snapshot_count": complete_count,
            "sleeves": ",".join(STRATEGIES),
            "construction_fields": "target_weights,holdings,rank_table,momentum_score,momentum_rank",
            "confidence": "HIGH_CONSTRUCTION_SHORT_WINDOW",
            "limitation": "actual decision-time artifacts but short 2026-04-21 to 2026-06-08 window and rank_table truncated at 15",
        },
        _supporting_artifact_row(
            repo,
            "orion_lyra_pit_rebaseline",
            Path("outputs/research/pit_rebaseline/orion_lyra_matched_2026-06-17.json"),
            "longer matched return series but no daily construction rank/weight table for allocator replay",
        ),
        _supporting_artifact_row(
            repo,
            "polaris_pit_rebaseline",
            Path("outputs/research/pit_rebaseline/polaris_priced_2026-06-10.json"),
            "priced PIT summary/attribution surface, not a daily allocation replay tape",
        ),
        _supporting_artifact_row(
            repo,
            "precompute_snapshot",
            Path("outputs/precompute/2026-03-24/daily_snapshot.json"),
            "single precompute bundle with targets/allocations but no sleeve rank table",
        ),
        _supporting_artifact_row(
            repo,
            "portfolio_history_snapshot",
            Path("outputs/portfolio_history/2026-04-30/weights_snapshot.json"),
            "single portfolio-history weights surface for Polaris/Orion/Lyra",
        ),
        _supporting_artifact_row(
            repo,
            "target_attainment_observability",
            Path("outputs/target_attainment/2026-06-09/target_attainment_2026-06-09.json"),
            "single observability artifact, explicitly non-executional",
        ),
    ]

    sleeve_rows = [
        {
            "sleeve": "Orion",
            "reconstruction_status": "REPLAYABLE_FROM_SHADOW_CANDIDATES",
            "available_date_start": min(complete_dates) if complete_dates else None,
            "available_date_end": max(complete_dates) if complete_dates else None,
            "replayable_snapshot_count": complete_count,
            "confidence": "MEDIUM_HIGH_FOR_CONSTRUCTION_LOW_FOR_GENERALIZATION",
        },
        {
            "sleeve": "Lyra",
            "reconstruction_status": "REPLAYABLE_FROM_SHADOW_CANDIDATES",
            "available_date_start": min(complete_dates) if complete_dates else None,
            "available_date_end": max(complete_dates) if complete_dates else None,
            "replayable_snapshot_count": complete_count,
            "confidence": "MEDIUM_HIGH_FOR_CONSTRUCTION_LOW_FOR_GENERALIZATION",
        },
        {
            "sleeve": "Polaris",
            "reconstruction_status": "REPLAYABLE_FROM_SHADOW_CANDIDATES",
            "available_date_start": min(complete_dates) if complete_dates else None,
            "available_date_end": max(complete_dates) if complete_dates else None,
            "replayable_snapshot_count": complete_count,
            "confidence": "MEDIUM_HIGH_FOR_CONSTRUCTION_LOW_FOR_GENERALIZATION",
        },
        {
            "sleeve": "Active production sleeves",
            "reconstruction_status": "PARTIAL_TARGET_ONLY",
            "available_date_start": "2026-03-24",
            "available_date_end": "2026-03-24",
            "replayable_snapshot_count": 0,
            "confidence": "LOW_FOR_CONVICTION_REPLAY",
        },
    ]
    return rows, sleeve_rows


def _supporting_artifact_row(repo: Path, source: str, path: Path, limitation: str) -> dict[str, Any]:
    full = repo / path
    exists = full.exists()
    payload: dict[str, Any] = {}
    if exists:
        try:
            payload = _read_json(full)
        except Exception:
            payload = {}
    date_range = payload.get("matched_pit_date_range") or payload.get("window") or {}
    return {
        "source": source,
        "path_pattern": str(path),
        "exists": exists,
        "sha256": _sha256(full),
        "available_date_start": date_range.get("start") if isinstance(date_range, dict) else None,
        "available_date_end": date_range.get("end") if isinstance(date_range, dict) else None,
        "available_snapshot_count": None,
        "replayable_snapshot_count": 0,
        "sleeves": None,
        "construction_fields": ",".join(sorted(payload.keys())[:12]) if payload else None,
        "confidence": "SUPPORTING_ONLY",
        "limitation": limitation,
    }


def _best_policy_row(policy_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        row
        for row in policy_rows
        if str(row.get("policy_id", "")).startswith("conviction_") and row.get("total_return") is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda row: float(row.get("total_return") or -999.0))


def _current_policy_row(policy_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in policy_rows:
        if row.get("policy_id") == CURRENT_POLICY_ID:
            return row
    return None


def falsification_findings(
    policy_rows: list[dict[str, Any]],
    rank_rows: list[dict[str, Any]],
    correlation_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    current = _current_policy_row(policy_rows) or {}
    best = _best_policy_row(policy_rows) or {}
    current_return = _as_float(current.get("total_return"), 0.0) or 0.0
    best_return = _as_float(best.get("total_return"), 0.0) or 0.0
    obs = int(current.get("observation_count") or 0)
    one_day = {row["rank_bucket"]: row for row in rank_rows if row.get("horizon_days") == 1}
    top_1 = _as_float((one_day.get("top_1") or {}).get("mean_forward_return"))
    lower = _as_float((one_day.get("lower_11_15") or {}).get("mean_forward_return"))
    rank_alpha = "INCONCLUSIVE"
    if top_1 is not None and lower is not None:
        rank_alpha = "SUPPORTED" if top_1 > lower else "NOT_SUPPORTED"

    current_corr = next((row for row in correlation_rows if row.get("policy_id") == CURRENT_POLICY_ID), {})
    spearman = _as_float(current_corr.get("weight_return_spearman"))
    sizing_alpha = "INCONCLUSIVE"
    if spearman is not None:
        sizing_alpha = "NOT_DETECTED" if abs(spearman) < 0.10 else ("POSITIVE" if spearman > 0 else "NEGATIVE")

    if obs < MIN_DECISION_GRADE_OBSERVATIONS:
        recommendation = "CONTINUE RESEARCH"
        recommendation_reason = "artifact-backed replay has fewer than 60 realized daily observations"
    elif best_return <= current_return:
        recommendation = "REJECT FR-074"
        recommendation_reason = "best conviction replay did not outperform the current artifact target proxy"
    else:
        recommendation = "READY FOR SHADOW EXPERIMENT"
        recommendation_reason = "conviction replay outperformed with enough PIT observations"

    return {
        "current_policy_id": CURRENT_POLICY_ID,
        "best_conviction_policy_id": best.get("policy_id"),
        "observation_count": obs,
        "conviction_total_return_delta": _round(best_return - current_return),
        "conviction_sharpe_delta": _round((_as_float(best.get("sharpe"), 0.0) or 0.0) - (_as_float(current.get("sharpe"), 0.0) or 0.0)),
        "conviction_max_drawdown_delta": _round(
            (_as_float(best.get("max_drawdown"), 0.0) or 0.0) - (_as_float(current.get("max_drawdown"), 0.0) or 0.0)
        ),
        "stock_selection_alpha": rank_alpha,
        "position_sizing_alpha": sizing_alpha,
        "concentration_benefit": "SUPPORTED_IN_SHORT_REPLAY" if best_return > current_return else "NOT_SUPPORTED_IN_SHORT_REPLAY",
        "concentration_risk": "HIGHER_DRAWDOWN" if (_as_float(best.get("max_drawdown"), 0.0) or 0.0) < (_as_float(current.get("max_drawdown"), 0.0) or 0.0) else "NOT_HIGHER_IN_SHORT_REPLAY",
        "final_recommendation": recommendation,
        "recommendation_reason": recommendation_reason,
        "fr_numbering_note": "Memory indicates FR-074 was previously used for execution reliability governance; confirm governance numbering before opening implementation work.",
    }


def _methodology_payload() -> dict[str, Any]:
    return {
        "research_only": True,
        "production_behavior_changed": False,
        "construction_inputs": "stored outputs/shadow_candidates dated JSON artifacts only",
        "forward_return_inputs": "local close price panel after each artifact date",
        "current_allocator_proxy": (
            "equal budget across Polaris/Orion/Lyra artifact target weights, aggregate duplicate tickers, "
            "10% single-name cap, and 90% minimum gross exposure if cap headroom permits"
        ),
        "conviction_methods": {
            "rank_weighted": "candidate weight score = 1 / best observed sleeve rank",
            "score_weighted": "candidate weight score = shifted positive max observed momentum_score",
            "score2_weighted": "candidate weight score = shifted positive max observed momentum_score squared",
        },
        "max_position_variants": list(MAX_POSITION_WEIGHTS),
        "min_position_variants": list(MIN_POSITION_WEIGHTS),
        "transaction_cost_bps": DEFAULT_TRANSACTION_COST_BPS,
        "no_lookahead_controls": [
            "rank and target weights come from dated persisted artifacts",
            "future prices are used only for realized return measurement after the artifact date",
            "missing construction dates carry the prior artifact weights rather than reconstructing unseen decisions",
        ],
        "known_limitations": [
            "short artifact replay window",
            "rank tables are truncated at 15 candidates",
            "price panel is a local research cache and not itself a construction artifact",
            "current allocator is an artifact-backed proxy, not live broker execution",
        ],
    }


def build_artifact(
    *,
    repo: Path,
    artifact_date: str,
    shadow_root: Path = DEFAULT_SHADOW_ROOT,
    price_cache_path: Path = DEFAULT_PRICE_CACHE_PATH,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    snapshots, inventory_rows = discover_shadow_snapshots(repo, shadow_root=shadow_root)
    close, price_meta = load_close_matrix(repo, price_cache_path=price_cache_path)
    reconstruction_rows, sleeve_rows = reconstruction_assessment(repo, inventory_rows, snapshots)

    policy_replays: dict[str, dict[str, Any]] = {}
    current_replay = replay_policy(snapshots, close, spec=None)
    policy_replays[CURRENT_POLICY_ID] = current_replay
    for spec in policy_specs():
        policy_replays[spec.policy_id] = replay_policy(snapshots, close, spec=spec)

    policy_rows: list[dict[str, Any]] = []
    for policy_id, replay in sorted(policy_replays.items()):
        metrics = _policy_metrics(
            policy_id,
            replay.get("returns", pd.Series(dtype=float)),
            replay.get("weights", pd.DataFrame()),
            replay.get("cash", pd.Series(dtype=float)),
            replay.get("turnover", pd.Series(dtype=float)),
            replay.get("top_ranked_deployment", pd.Series(dtype=float)),
        )
        spec = _spec_for_policy_id(policy_id)
        metrics.update(spec)
        policy_rows.append(metrics)

    rank_rows = rank_bucket_forward_returns(snapshots, close)
    cutoff_rows = cutoff_forward_returns(snapshots, close)
    corr_rows = position_size_correlation_rows(snapshots, close)
    small_rows = small_position_rows(policy_replays)
    findings = falsification_findings(policy_rows, rank_rows, corr_rows)

    artifact_dir = _repo_path(repo, output_root) / artifact_date
    tables = {
        "artifact_inventory": inventory_rows,
        "reconstruction_assessment": reconstruction_rows,
        "sleeve_reconstruction": sleeve_rows,
        "policy_metrics": policy_rows,
        "rank_bucket_forward_returns": rank_rows,
        "rank_cutoff_forward_returns": cutoff_rows,
        "position_size_correlation": corr_rows,
        "small_position_exposure": small_rows,
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "artifact_date": artifact_date,
        "governance_label": "RESEARCH_ONLY_NO_RUNTIME_CHANGE",
        "production_impact": {
            "allocator_changed": False,
            "execution_changed": False,
            "broker_changed": False,
            "scheduler_changed": False,
            "live_or_paper_behavior_changed": False,
        },
        "inputs": {
            "shadow_root": str(shadow_root),
            "price_cache_path": str(price_cache_path),
            "inspected_files": INSPECTED_FILES,
            "price_meta": price_meta,
        },
        "methodology": _methodology_payload(),
        "pit_reconstruction_assessment": {
            "sources": reconstruction_rows,
            "sleeves": sleeve_rows,
            "artifact_inventory_summary": {
                "dated_dirs": len(inventory_rows),
                "complete_replay_snapshots": len(snapshots),
                "first_complete_date": min([snapshot.trade_date for snapshot in snapshots]) if snapshots else None,
                "last_complete_date": max([snapshot.trade_date for snapshot in snapshots]) if snapshots else None,
            },
        },
        "results": {
            "policy_metrics": policy_rows,
            "rank_bucket_forward_returns": rank_rows,
            "rank_cutoff_forward_returns": cutoff_rows,
            "position_size_correlation": corr_rows,
            "small_position_exposure": small_rows,
        },
        "falsification_findings": findings,
        "risks_and_bias": [
            "Replay window is too short for decision-grade annualized metrics.",
            "Shadow rank tables expose top 15 only, limiting residual/lower-ranked inference.",
            "Current allocator result is a target-attainment proxy derived from stored target weights, not a broker execution replay.",
            "Local price cache may include current data revisions; it is used only for realized forward returns, not construction.",
            "FR-074 numbering appears to collide with prior execution reliability governance memory.",
        ],
        "recommendation": findings["final_recommendation"],
    }

    _write_json(artifact_dir / "fr074_pit_conviction_replay.json", payload)
    for name, rows in tables.items():
        _write_csv(artifact_dir / f"{name}.csv", rows)
    _write_json(
        artifact_dir / "manifest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "artifact_date": artifact_date,
            "primary_artifact": _repo_relative(repo, artifact_dir / "fr074_pit_conviction_replay.json"),
            "tables": {name: _repo_relative(repo, artifact_dir / f"{name}.csv") for name in tables},
            "source_shadow_hashes": [
                {
                    "trade_date": row.get("trade_date"),
                    "strategy": strategy,
                    "path": row.get(f"{strategy}_exists") and f"outputs/shadow_candidates/{row.get('trade_date')}/{strategy}.json",
                    "sha256": row.get(f"{strategy}_sha256"),
                }
                for row in inventory_rows
                for strategy in STRATEGIES
                if row.get(f"{strategy}_sha256")
            ],
        },
    )
    (artifact_dir / "fr074_pit_conviction_replay.md").write_text(
        _markdown_report(payload),
        encoding="utf-8",
    )
    return payload, tables


def _spec_for_policy_id(policy_id: str) -> dict[str, Any]:
    if policy_id == CURRENT_POLICY_ID:
        return {
            "policy_family": "current_artifact_target_proxy",
            "method": "target_weight_attainment_proxy",
            "max_position_weight": DEFAULT_CURRENT_MAX_POSITION,
            "min_position_weight": None,
        }
    for spec in policy_specs():
        if spec.policy_id == policy_id:
            return {
                "policy_family": spec.policy_family,
                "method": spec.method,
                "max_position_weight": spec.max_position_weight,
                "min_position_weight": spec.min_position_weight,
            }
    return {"policy_family": None, "method": None, "max_position_weight": None, "min_position_weight": None}


def _markdown_report(payload: dict[str, Any]) -> str:
    findings = payload["falsification_findings"]
    policy_rows = payload["results"]["policy_metrics"]
    rank_rows = payload["results"]["rank_bucket_forward_returns"]
    current = _current_policy_row(policy_rows) or {}
    best = _best_policy_row(policy_rows) or {}
    reconstruction = payload["pit_reconstruction_assessment"]["artifact_inventory_summary"]

    lines = [
        "# FR-074 PIT Conviction Allocation Replay",
        "",
        "## Executive Summary",
        "",
        f"Final recommendation: **{findings['final_recommendation']}**.",
        "",
        (
            "The artifact-backed replay is directionally useful but not decision-grade. "
            f"It found {reconstruction['complete_replay_snapshots']} complete construction snapshots from "
            f"{reconstruction['first_complete_date']} through {reconstruction['last_complete_date']}, "
            f"but the current-policy replay has only {findings['observation_count']} realized daily observations."
        ),
        "",
        (
            f"Best conviction policy: `{best.get('policy_id')}` with total return "
            f"{_pct(best.get('total_return'))}, Sharpe {_fmt(best.get('sharpe'))}, max drawdown "
            f"{_pct(best.get('max_drawdown'))}. Current artifact target proxy: total return "
            f"{_pct(current.get('total_return'))}, Sharpe {_fmt(current.get('sharpe'))}, max drawdown "
            f"{_pct(current.get('max_drawdown'))}."
        ),
        "",
        "## PIT Reconstruction Assessment",
        "",
        _markdown_table(payload["pit_reconstruction_assessment"]["sleeves"], [
            "sleeve",
            "reconstruction_status",
            "available_date_start",
            "available_date_end",
            "replayable_snapshot_count",
            "confidence",
        ]),
        "",
        "## Methodology",
        "",
        "- Construction inputs are stored dated shadow-candidate artifacts only.",
        "- Missing construction dates carry the latest available artifact weights.",
        "- Forward prices are used only after the decision date for realized-return measurement.",
        "- Current allocator is represented by an artifact-backed target-attainment proxy.",
        "- Conviction variants sweep rank, score, and score-squared weighting across 10%, 20%, 30%, and 40% caps with 0%, 1%, 2%, and 3% minimum-position floors.",
        "",
        "## Results Tables",
        "",
        "### Policy Metrics",
        "",
        _markdown_table(
            [current, best],
            [
                "policy_id",
                "total_return",
                "cagr",
                "sharpe",
                "sortino",
                "max_drawdown",
                "volatility",
                "turnover",
                "average_holdings_count",
                "cash_drag",
                "average_hhi",
            ],
        ),
        "",
        "### Rank-Bucket Forward Returns",
        "",
        _markdown_table(
            [row for row in rank_rows if row.get("horizon_days") == 1],
            [
                "rank_bucket",
                "bucket_definition",
                "decision_date_count",
                "security_observation_count",
                "mean_forward_return",
                "hit_rate",
            ],
        ),
        "",
        "## Falsification Findings",
        "",
        f"- Stock-selection alpha: `{findings['stock_selection_alpha']}`.",
        f"- Position-sizing alpha: `{findings['position_sizing_alpha']}`.",
        f"- Concentration benefit: `{findings['concentration_benefit']}`.",
        f"- Concentration risk: `{findings['concentration_risk']}`.",
        f"- Reason recommendation is not stronger: {findings['recommendation_reason']}.",
        "",
        "## Risks And Bias Assessment",
        "",
    ]
    lines.extend(f"- {item}" for item in payload["risks_and_bias"])
    lines.extend(
        [
            "",
            "## Files Inspected",
            "",
        ]
    )
    lines.extend(f"- `{path}`" for path in payload["inputs"]["inspected_files"])
    lines.extend(
        [
            "",
            "## Recommendation",
            "",
            f"**{findings['final_recommendation']}**. Do not implement allocator changes from this evidence alone. The next FR should extend persisted decision-time construction artifacts across a longer window before paper or shadow promotion.",
            "",
        ]
    )
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    rounded = _round(value, 4)
    return "n/a" if rounded is None else str(rounded)


def _pct(value: Any) -> str:
    rounded = _round(value)
    return "n/a" if rounded is None else f"{rounded * 100:.2f}%"


def _markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._"
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in rows:
        values = []
        for column in columns:
            value = row.get(column)
            if isinstance(value, float):
                value = _round(value, 6)
            values.append("" if value is None else str(value))
        body.append("| " + " | ".join(values) + " |")
    return "\n".join([header, sep, *body])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--artifact-date", default="2026-06-22")
    parser.add_argument("--shadow-root", type=Path, default=DEFAULT_SHADOW_ROOT)
    parser.add_argument("--price-cache-path", type=Path, default=DEFAULT_PRICE_CACHE_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload, _ = build_artifact(
        repo=args.repo.resolve(),
        artifact_date=args.artifact_date,
        shadow_root=args.shadow_root,
        price_cache_path=args.price_cache_path,
        output_root=args.output_root,
    )
    out = _repo_path(args.repo.resolve(), args.output_root) / args.artifact_date / "fr074_pit_conviction_replay.json"
    print(json.dumps({"artifact": str(out), "recommendation": payload["recommendation"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
