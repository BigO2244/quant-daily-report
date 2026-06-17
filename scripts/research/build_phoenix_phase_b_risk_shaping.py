"""Build Phoenix Phase B risk-shaping evidence.

Research-only. Evaluates whether Phoenix-style crisis/recovery exposure can
retain recovery upside while reducing event drawdown. This script consumes the
same PIT large-cap/SEP close cache used by the Phoenix crisis evidence builder
and does not alter production signals, allocation, execution, broker, cron, or
risk-control behavior.
"""
from __future__ import annotations

import argparse
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

from scripts.research.build_phoenix_crisis_recovery_evidence import (
    CRISIS_DRAWDOWN_THRESHOLD,
    CRISIS_MIN_SEPARATION_DAYS,
    DEFAULT_END,
    DEFAULT_START,
    DEFAULT_WARMUP_START,
    _close_matrix,
    _cumulative_return,
    _max_drawdown,
    _portfolio_overlap,
    _round,
    _strategy_specs,
    _volatility,
    build_price_panel,
    detect_crisis_windows,
)
from research.alpha_lab_v1.signals import build_alpha_lab_signal_frame
from research.alpha_lab_v2.engine import run_backtest

SCHEMA_VERSION = "caerus_phoenix_phase_b_risk_shaping_v1"
DEFAULT_COST_BPS = 25.0


@dataclass(frozen=True)
class RiskVariant:
    variant_id: str
    description: str
    top_n: int = 10
    max_gross: float = 0.80
    max_name_weight: float = 0.10
    min_return_20d: float = 0.0
    min_return_5d: float = 0.0
    max_vol_20d_ann: float | None = None
    entry_lag_days: int = 0
    confirmation_window_days: int = 0
    min_spy_confirmation_return: float | None = None
    min_candidate_confirmation_return: float | None = None
    stage_lags_and_weights: tuple[tuple[int, float], ...] = ((0, 1.0),)
    stop_loss: float | None = None
    requires_liquidity_source: bool = False


def default_variants() -> list[RiskVariant]:
    return [
        RiskVariant(
            variant_id="baseline_close_only",
            description="Close-only baseline from the first Phoenix crisis evidence artifact.",
        ),
        RiskVariant(
            variant_id="stricter_crisis_entry",
            description="Require deeper 20D and 5D local dislocation before entry.",
            min_return_20d=-0.12,
            min_return_5d=-0.04,
        ),
        RiskVariant(
            variant_id="volatility_cap_70",
            description="Exclude names whose 20D realized annualized volatility exceeds 70%.",
            max_vol_20d_ann=0.70,
        ),
        RiskVariant(
            variant_id="staged_entry_0_5_10",
            description="Enter 35% at trough, 35% after 5 trading days, and 30% after 10 trading days.",
            stage_lags_and_weights=((0, 0.35), (5, 0.35), (10, 0.30)),
        ),
        RiskVariant(
            variant_id="recovery_confirmation_5d",
            description="Wait up to 5 trading days for SPY and candidate confirmation before entering.",
            confirmation_window_days=5,
            min_spy_confirmation_return=0.02,
            min_candidate_confirmation_return=0.00,
        ),
        RiskVariant(
            variant_id="stop_loss_10pct",
            description="Exit a crisis basket to cash if event cumulative loss reaches -10%.",
            stop_loss=-0.10,
        ),
        RiskVariant(
            variant_id="concentrated_top5_50gross",
            description="Hold only top 5 dislocation names at 50% max gross and 10% max name weight.",
            top_n=5,
            max_gross=0.50,
            max_name_weight=0.10,
        ),
        RiskVariant(
            variant_id="broader_top15_75gross",
            description="Hold up to 15 names at 75% max gross and 5% max name weight.",
            top_n=15,
            max_gross=0.75,
            max_name_weight=0.05,
        ),
        RiskVariant(
            variant_id="liquidity_capacity_filter",
            description="Require PIT volume/dollar-volume capacity filter when source data exists.",
            requires_liquidity_source=True,
        ),
    ]


def _date_at_lag(index: pd.DatetimeIndex, start: pd.Timestamp, lag: int) -> pd.Timestamp:
    if start not in index:
        pos = max(index.searchsorted(start) - 1, 0)
    else:
        pos = index.get_loc(start)
    if isinstance(pos, slice):
        pos = pos.start or 0
    return pd.Timestamp(index[min(int(pos) + int(lag), len(index) - 1)])


def _returns(matrix: pd.DataFrame, days: int, at: pd.Timestamp) -> pd.Series:
    idx = matrix.index.get_loc(at)
    if isinstance(idx, slice):
        idx = idx.start or 0
    prior = max(int(idx) - int(days), 0)
    return matrix.iloc[int(idx)].div(matrix.iloc[prior]).sub(1.0)


def _vol_20d(matrix: pd.DataFrame, at: pd.Timestamp) -> pd.Series:
    idx = matrix.index.get_loc(at)
    if isinstance(idx, slice):
        idx = idx.start or 0
    start = max(int(idx) - 20, 0)
    daily = matrix.iloc[start : int(idx) + 1].pct_change()
    return daily.std(ddof=1).mul(math.sqrt(252))


def _candidate_frame(matrix: pd.DataFrame, entry_date: pd.Timestamp, variant: RiskVariant) -> pd.DataFrame:
    if entry_date not in matrix.index:
        entry_date = _date_at_lag(matrix.index, entry_date, 0)
    idx = matrix.index.get_loc(entry_date)
    if isinstance(idx, slice):
        idx = idx.start or 0
    if int(idx) < 252:
        return pd.DataFrame()
    current = matrix.iloc[int(idx)].dropna()
    ret_5d = _returns(matrix, 5, entry_date).reindex(current.index)
    ret_20d = _returns(matrix, 20, entry_date).reindex(current.index)
    ret_252d = _returns(matrix, 252, entry_date).reindex(current.index)
    vol_20d = _vol_20d(matrix, entry_date).reindex(current.index)
    frame = pd.DataFrame(
        {
            "close": current,
            "return_5d": ret_5d,
            "return_20d": ret_20d,
            "return_252d": ret_252d,
            "vol_20d_ann": vol_20d,
        }
    ).dropna(subset=["close", "return_5d", "return_20d", "return_252d"])
    frame = frame[frame.index != "SPY"]
    frame = frame[frame["close"] >= 5.0]
    frame = frame[(frame["return_20d"] < float(variant.min_return_20d)) & (frame["return_5d"] < float(variant.min_return_5d))]
    if variant.max_vol_20d_ann is not None:
        frame = frame[frame["vol_20d_ann"] <= float(variant.max_vol_20d_ann)]
    if frame.empty:
        return frame
    frame["phoenix_dislocation_score"] = (
        (-frame["return_20d"]).clip(lower=0.0) * 0.60
        + (-frame["return_5d"]).clip(lower=0.0) * 0.30
        + (-frame["return_252d"]).clip(lower=0.0) * 0.10
    )
    return frame.sort_values(["phoenix_dislocation_score", "return_20d"], ascending=[False, True]).head(int(variant.top_n))


def _confirmed_entry_date(matrix: pd.DataFrame, trough: pd.Timestamp, variant: RiskVariant) -> pd.Timestamp | None:
    index = matrix.index
    if variant.confirmation_window_days <= 0:
        return _date_at_lag(index, trough, variant.entry_lag_days)
    spy = matrix["SPY"].dropna()
    for lag in range(1, int(variant.confirmation_window_days) + 1):
        candidate_date = _date_at_lag(index, trough, lag)
        spy_return = float(spy.loc[candidate_date] / spy.loc[trough] - 1.0)
        if variant.min_spy_confirmation_return is not None and spy_return < float(variant.min_spy_confirmation_return):
            continue
        candidates = _candidate_frame(matrix, candidate_date, variant)
        if candidates.empty:
            continue
        if variant.min_candidate_confirmation_return is not None:
            confirmed = candidates[candidates["return_5d"] >= float(variant.min_candidate_confirmation_return)]
            if confirmed.empty:
                continue
        return candidate_date
    return None


def _weights_for_candidates(candidates: pd.DataFrame, variant: RiskVariant) -> pd.Series:
    if candidates.empty:
        return pd.Series(dtype=float)
    gross = min(float(variant.max_gross), float(variant.max_name_weight) * len(candidates))
    return pd.Series(gross / len(candidates), index=pd.Index(candidates.index.astype(str), dtype=str), dtype=float).round(10)


def _candidate_records(candidates: pd.DataFrame, weights: pd.Series) -> list[dict[str, Any]]:
    rows = []
    for ticker, row in candidates.iterrows():
        rows.append(
            {
                "ticker": str(ticker),
                "target_weight": _round(weights.get(str(ticker), 0.0)),
                "phoenix_dislocation_score": _round(row.get("phoenix_dislocation_score")),
                "return_5d_at_entry": _round(row.get("return_5d")),
                "return_20d_at_entry": _round(row.get("return_20d")),
                "return_252d_at_entry": _round(row.get("return_252d")),
                "vol_20d_ann_at_entry": _round(row.get("vol_20d_ann")),
            }
        )
    return rows


def _staged_exposure(index: pd.DatetimeIndex, entry: pd.Timestamp, variant: RiskVariant) -> pd.Series:
    exposure = pd.Series(0.0, index=index, dtype=float)
    cumulative = 0.0
    for lag, fraction in sorted(variant.stage_lags_and_weights):
        stage_date = _date_at_lag(index, entry, int(lag))
        cumulative += float(fraction)
        exposure.loc[stage_date:] = min(cumulative, 1.0)
    return exposure.clip(lower=0.0, upper=1.0)


def _apply_stop_loss(returns: pd.Series, stop_loss: float | None) -> tuple[pd.Series, str | None]:
    if stop_loss is None or returns.empty:
        return returns, None
    nav = (1.0 + returns.fillna(0.0)).cumprod()
    below = nav <= (1.0 + float(stop_loss))
    if not bool(below.any()):
        return returns, None
    stop_date = pd.Timestamp(below[below].index[0])
    stopped = returns.copy()
    stopped.loc[stop_date:] = 0.0
    return stopped, stop_date.strftime("%Y-%m-%d")


def _event_returns_for_variant(
    matrix: pd.DataFrame,
    window: dict[str, Any],
    variant: RiskVariant,
) -> tuple[pd.Series, pd.Series, dict[str, Any]]:
    if variant.requires_liquidity_source:
        return pd.Series(dtype=float), pd.Series(dtype=float), {
            "status": "NOT_RUN",
            "reason_codes": ["pit_liquidity_source_missing"],
            "detail": "Repo-local PIT SEP cache has closeadj/close only; no volume/dollar-volume field is available.",
        }
    trough = pd.Timestamp(window["trough_date"])
    entry = _confirmed_entry_date(matrix, trough, variant)
    if entry is None:
        return pd.Series(dtype=float), pd.Series(dtype=float), {
            "status": "SKIPPED",
            "reason_codes": ["confirmation_not_met"],
            "trough_date": trough.strftime("%Y-%m-%d"),
        }
    candidates = _candidate_frame(matrix, entry, variant)
    weights = _weights_for_candidates(candidates, variant)
    if weights.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float), {
            "status": "SKIPPED",
            "reason_codes": ["no_eligible_candidates"],
            "trough_date": trough.strftime("%Y-%m-%d"),
            "entry_date": entry.strftime("%Y-%m-%d"),
        }
    end = pd.Timestamp(window["recovery_20d_end"])
    event_index = matrix.loc[entry:end].index
    raw_returns = matrix.reindex(columns=weights.index).pct_change().loc[event_index].fillna(0.0).mul(weights, axis=1).sum(axis=1)
    exposure = _staged_exposure(event_index, entry, variant)
    gross_returns = raw_returns.mul(exposure, fill_value=0.0)
    stopped_returns, stop_date = _apply_stop_loss(gross_returns, variant.stop_loss)
    turnover = pd.Series(0.0, index=event_index, dtype=float)
    previous_exposure = 0.0
    for date, exposure_value in exposure.items():
        delta = max(float(exposure_value) - previous_exposure, 0.0)
        if delta > 0:
            turnover.loc[date] += float(weights.abs().sum()) * delta
        previous_exposure = float(exposure_value)
    exit_date = pd.Timestamp(stop_date) if stop_date else event_index[-1]
    turnover.loc[exit_date] += float(weights.abs().sum()) * float(exposure.loc[exit_date])
    diagnostics = {
        "status": "OK",
        "reason_codes": ["ok"],
        "trough_date": trough.strftime("%Y-%m-%d"),
        "entry_date": entry.strftime("%Y-%m-%d"),
        "recovery_20d_end": end.strftime("%Y-%m-%d"),
        "selected_count": int(len(weights)),
        "gross_exposure": _round(float(weights.sum())),
        "selected": _candidate_records(candidates, weights),
        "event_return_20d": _cumulative_return(stopped_returns.sub(turnover.mul(DEFAULT_COST_BPS / 10000.0), fill_value=0.0)),
        "event_max_drawdown_20d": _max_drawdown(stopped_returns.sub(turnover.mul(DEFAULT_COST_BPS / 10000.0), fill_value=0.0)),
        "stop_date": stop_date,
        "turnover": _round(float(turnover.sum())),
    }
    return stopped_returns, turnover, diagnostics


def _daily_returns(result: dict[str, Any]) -> pd.Series:
    daily = result.get("daily")
    if not isinstance(daily, pd.DataFrame) or daily.empty:
        return pd.Series(dtype=float)
    return pd.Series(daily["net_return"].values, index=pd.to_datetime(daily["date"]), dtype=float)


def _weights(result: dict[str, Any]) -> pd.DataFrame:
    weights = result.get("weights")
    if not isinstance(weights, pd.DataFrame) or weights.empty:
        return pd.DataFrame()
    out = weights.copy()
    out.index = pd.to_datetime(out.index)
    return out.sort_index()


def _variant_summary(
    *,
    variant: RiskVariant,
    returns_gross: pd.Series,
    turnover: pd.Series,
    event_rows: list[dict[str, Any]],
    baseline_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    net = returns_gross.sub(turnover.mul(DEFAULT_COST_BPS / 10000.0), fill_value=0.0)
    event_returns = [row.get("event_return_20d") for row in event_rows if row.get("event_return_20d") is not None]
    skipped = [row for row in event_rows if row.get("status") != "OK"]
    max_dd = _max_drawdown(net)
    avg_event = sum(float(v) for v in event_returns) / len(event_returns) if event_returns else None
    drawdown_improvement = None
    upside_retention = None
    if baseline_summary:
        base_dd = baseline_summary.get("max_drawdown_25bps")
        base_avg = baseline_summary.get("average_event_return_20d")
        if base_dd is not None and max_dd is not None:
            drawdown_improvement = _round(float(max_dd) - float(base_dd))
        if base_avg not in (None, 0) and avg_event is not None:
            upside_retention = _round(float(avg_event) / float(base_avg))
    classification = "RESEARCH_ONLY_NOT_SHADOW_READY"
    blockers: list[str] = []
    warnings: list[str] = []
    if variant.requires_liquidity_source:
        blockers.append("pit_liquidity_source_missing")
    if len(event_returns) < 3:
        blockers.append("insufficient_triggered_crisis_events")
    if avg_event is None or avg_event <= 0:
        blockers.append("recovery_upside_not_positive")
    if max_dd is not None and max_dd <= -0.35:
        blockers.append("event_drawdown_too_deep")
    if upside_retention is not None and upside_retention < 0.70:
        warnings.append("upside_retention_below_70pct")
    if not blockers:
        classification = "SHADOW_SPEC_CANDIDATE_RESEARCH_ONLY" if not warnings else "RESEARCH_READY_WITH_WARNINGS"
    return {
        "variant_id": variant.variant_id,
        "description": variant.description,
        "status": "NOT_RUN" if variant.requires_liquidity_source else "OK",
        "classification": classification,
        "average_event_return_20d": _round(avg_event),
        "cumulative_return_25bps": _cumulative_return(net),
        "max_drawdown_25bps": max_dd,
        "volatility_25bps": _volatility(net),
        "average_turnover": _round(turnover[turnover > 0].mean()),
        "triggered_event_count": int(len(event_returns)),
        "skipped_event_count": int(len(skipped)),
        "drawdown_improvement_vs_baseline": drawdown_improvement,
        "upside_retention_vs_baseline": upside_retention,
        "blockers": blockers,
        "warnings": warnings,
        "reason_codes": blockers + warnings or ["ok"],
        "config": {
            "top_n": variant.top_n,
            "max_gross": variant.max_gross,
            "max_name_weight": variant.max_name_weight,
            "min_return_20d": variant.min_return_20d,
            "min_return_5d": variant.min_return_5d,
            "max_vol_20d_ann": variant.max_vol_20d_ann,
            "entry_lag_days": variant.entry_lag_days,
            "confirmation_window_days": variant.confirmation_window_days,
            "min_spy_confirmation_return": variant.min_spy_confirmation_return,
            "min_candidate_confirmation_return": variant.min_candidate_confirmation_return,
            "stage_lags_and_weights": list(variant.stage_lags_and_weights),
            "stop_loss": variant.stop_loss,
            "requires_liquidity_source": variant.requires_liquidity_source,
        },
    }


def build_artifact(*, repo: Path, output_date: str, start_date: str, end_date: str) -> dict[str, Any]:
    panel, input_meta = build_price_panel(repo=repo, end_date=end_date)
    matrix = _close_matrix(panel)
    windows = detect_crisis_windows(matrix["SPY"], start_date=start_date, end_date=end_date)
    full_index = matrix.loc[start_date:end_date].index
    signals = build_alpha_lab_signal_frame(panel.assign(date=panel["date"].dt.strftime("%Y-%m-%d")))
    existing_results = {
        slug: run_backtest(signals, spec, start_date=start_date, end_date=end_date)
        for slug, spec in _strategy_specs().items()
    }
    existing_returns = {slug: _daily_returns(result).reindex(full_index).dropna() for slug, result in existing_results.items()}
    existing_weights = {slug: _weights(result) for slug, result in existing_results.items()}
    variant_rows: list[dict[str, Any]] = []
    variant_events: dict[str, list[dict[str, Any]]] = {}
    variant_returns: dict[str, pd.Series] = {}
    baseline_summary: dict[str, Any] | None = None
    for variant in default_variants():
        gross = pd.Series(0.0, index=full_index, dtype=float)
        turnover = pd.Series(0.0, index=full_index, dtype=float)
        event_rows: list[dict[str, Any]] = []
        for window in windows:
            event_returns, event_turnover, diagnostics = _event_returns_for_variant(matrix, window, variant)
            if not event_returns.empty:
                gross.loc[event_returns.index] = gross.loc[event_returns.index].add(event_returns, fill_value=0.0)
                turnover.loc[event_turnover.index] = turnover.loc[event_turnover.index].add(event_turnover, fill_value=0.0)
            event_rows.append({"window_id": window["window_id"], **diagnostics})
        summary = _variant_summary(
            variant=variant,
            returns_gross=gross,
            turnover=turnover,
            event_rows=event_rows,
            baseline_summary=baseline_summary,
        )
        if variant.variant_id == "baseline_close_only":
            baseline_summary = summary
        variant_rows.append(summary)
        variant_events[variant.variant_id] = event_rows
        variant_returns[variant.variant_id] = gross.sub(turnover.mul(DEFAULT_COST_BPS / 10000.0), fill_value=0.0)

    best = sorted(
        [row for row in variant_rows if row["classification"] != "RESEARCH_ONLY_NOT_SHADOW_READY"],
        key=lambda row: (
            row.get("max_drawdown_25bps") if row.get("max_drawdown_25bps") is not None else -99,
            row.get("average_event_return_20d") if row.get("average_event_return_20d") is not None else -99,
        ),
        reverse=True,
    )
    overlap_correlation: dict[str, Any] = {}
    for variant_id, series in variant_returns.items():
        row: dict[str, Any] = {}
        for slug, existing in existing_returns.items():
            data = pd.concat([series.rename("phoenix"), existing.rename(slug)], axis=1).dropna()
            row[slug] = _round(data["phoenix"].corr(data[slug])) if len(data) > 2 else None
        overlap_correlation[variant_id] = row

    liquidity_blocked = True
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "artifact_date": output_date,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "production_impact": "none",
        "decision_state": "risk_shaping_evidence_generated",
        "matched_pit_date_range": {
            "warmup_start": DEFAULT_WARMUP_START,
            "start": start_date,
            "end": end_date,
            "holdout_2025_forward": "excluded",
            "matched_return_observations": int(len(full_index)),
        },
        "inputs": input_meta,
        "crisis_window_definitions": {
            "threshold": CRISIS_DRAWDOWN_THRESHOLD,
            "min_separation_days": CRISIS_MIN_SEPARATION_DAYS,
            "window_count": len(windows),
            "windows": windows,
        },
        "tested_dimensions": [
            "stricter_crisis_entry_filters",
            "liquidity_capacity_filters",
            "volatility_risk_caps",
            "staged_entry_after_panic",
            "recovery_confirmation_trigger",
            "max_loss_stop_out_variants",
            "position_count_and_concentration_variants",
        ],
        "variant_results": variant_rows,
        "event_diagnostics_by_variant": variant_events,
        "overlap_correlation_vs_existing_sleeves": {
            "daily_return_correlation": overlap_correlation,
            "note": "Correlation is computed on sparse Phoenix event-return series versus matched PIT existing-sleeve daily returns.",
        },
        "liquidity_capacity": {
            "tested": True,
            "decision_grade": False,
            "reason_codes": ["pit_liquidity_source_missing"],
            "source_gap": "Sharadar SEP repo-local cache has closeadj/close only; volume/dollar-volume is required before capacity filters can be decision-grade.",
        },
        "best_research_candidate": best[0] if best else None,
        "readiness_conclusion": {
            "is_shadow_eligible": False,
            "shadow_readiness_work_justified": bool(best),
            "classification": (
                "PHOENIX_RISK_SHAPING_CANDIDATE_PENDING_LIQUIDITY"
                if best and liquidity_blocked
                else "PHOENIX_RISK_SHAPING_SHADOW_SPEC_CANDIDATE"
                if best
                else "PHOENIX_RISK_SHAPING_NOT_SHADOW_READY"
            ),
            "reason_codes": (
                ["candidate_variant_found", "pit_liquidity_source_missing"]
                if best and liquidity_blocked
                else ["candidate_variant_found"]
                if best
                else ["no_variant_met_shadow_spec_gate"]
            ),
        },
        "non_goals": [
            "no Phoenix activation",
            "no live signals",
            "no allocation changes",
            "no execution changes",
            "no broker behavior changes",
            "no promotion threshold changes",
        ],
    }
    return payload


def write_artifact(repo: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    out_dir = repo / "outputs" / "research" / "phoenix_evidence"
    date = payload["artifact_date"]
    json_path = out_dir / f"phoenix_phase_b_risk_shaping_{date}.json"
    md_path = out_dir / f"phoenix_phase_b_risk_shaping_{date}.md"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    conclusion = payload.get("readiness_conclusion") or {}
    best = payload.get("best_research_candidate") or {}
    lines = [
        "# Phoenix Phase B Risk-Shaping Evidence",
        "",
        f"Date: `{date}`",
        "",
        "RESEARCH_ONLY / NO_RUNTIME_CHANGE",
        "",
        f"Classification: `{conclusion.get('classification')}`",
        f"Shadow eligible: `{conclusion.get('is_shadow_eligible')}`",
        f"Shadow readiness work justified: `{conclusion.get('shadow_readiness_work_justified')}`",
        f"Best candidate: `{best.get('variant_id')}`",
        f"Best candidate max drawdown: `{best.get('max_drawdown_25bps')}`",
        f"Best candidate avg 20D event return: `{best.get('average_event_return_20d')}`",
        "",
        "## Variant Summary",
        "",
        "| Variant | Classification | Avg 20D event return | Max DD | Upside retention | DD improvement |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in payload.get("variant_results") or []:
        lines.append(
            f"| `{row.get('variant_id')}` | `{row.get('classification')}` | {row.get('average_event_return_20d')} | "
            f"{row.get('max_drawdown_25bps')} | {row.get('upside_retention_vs_baseline')} | "
            f"{row.get('drawdown_improvement_vs_baseline')} |"
        )
    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--date", default=pd.Timestamp.now("UTC").strftime("%Y-%m-%d"))
    parser.add_argument("--start-date", default=DEFAULT_START)
    parser.add_argument("--end-date", default=DEFAULT_END)
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    payload = build_artifact(repo=repo, output_date=args.date, start_date=args.start_date, end_date=args.end_date)
    json_path, md_path = write_artifact(repo, payload)
    print(json.dumps({
        "status": "OK",
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "classification": (payload.get("readiness_conclusion") or {}).get("classification"),
        "best_variant": (payload.get("best_research_candidate") or {}).get("variant_id"),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
