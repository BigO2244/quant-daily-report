"""Tier 3 Caerus regime attribution.

Classifies historical trading days into seven market regimes using only
the SPY benchmark series available through the target date (no
look-ahead, no paid external data) and reports per-strategy performance
within each regime.

Regimes (priority order; first matching wins for a given day):

  panic        — 5-day cumulative SPY return <= -5%
  recovery     — 5-day cumulative SPY return >= +5% AND prior 20-day < 0
  bear_trend   — 20-day SPY return <= -2%
  bull_trend   — 20-day SPY return >= +2%
  high_vol     — 20-day realized vol (annualized) > 25%
  low_vol      — 20-day realized vol (annualized) < 10%
  neutral      — otherwise

The classifier uses only price history strictly prior to (and including)
the target trade_date. Days within the first 20 observations are
classified as "warming" and excluded from regime metrics so the rolling
windows have full coverage.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from core.strategy_registry import active_shadow_security_selection_ids

SCHEMA_VERSION = "caerus_regime_attribution_v1"

STRATEGIES = active_shadow_security_selection_ids()
SPY_COLUMN = "spy_benchmark"

REGIME_LABELS = (
    "bull_trend",
    "bear_trend",
    "high_vol",
    "low_vol",
    "panic",
    "recovery",
    "neutral",
)

# Classifier thresholds.
PANIC_5D_RETURN_THRESHOLD = -0.05
RECOVERY_5D_RETURN_THRESHOLD = 0.05
BEAR_20D_RETURN_THRESHOLD = -0.02
BULL_20D_RETURN_THRESHOLD = 0.02
HIGH_VOL_ANNUALIZED = 0.25
LOW_VOL_ANNUALIZED = 0.10

WARMUP_DAYS = 20
TRADING_DAYS_PER_YEAR = 252

MIN_OBS_HIGH_CONFIDENCE = 30
MIN_OBS_MEDIUM_CONFIDENCE = 10


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except Exception:
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _round(value: Any, digits: int = 10) -> float | None:
    f = _safe_float(value)
    return round(f, digits) if f is not None else None


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_nav_series(repo: Path) -> pd.DataFrame | None:
    path = repo / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv"
    if not path.exists():
        return None
    try:
        frame = pd.read_csv(path)
    except Exception:
        return None
    if "date" not in frame.columns:
        return None
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date", kind="mergesort").reset_index(drop=True)
    return frame


def _filter_to_target_date(frame: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    cutoff = pd.Timestamp(trade_date)
    return frame.loc[frame["date"] <= cutoff].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Regime classification (no look-ahead)
# ---------------------------------------------------------------------------

def _classify_regimes(frame: pd.DataFrame) -> pd.DataFrame:
    """Classify each row's regime using only data through that row."""
    if SPY_COLUMN not in frame.columns:
        return frame.assign(regime=None, spy_return=None, vol_20d=None)
    spy = frame[SPY_COLUMN].astype(float)
    spy_return = spy.pct_change()
    # Rolling windows are inclusive of the current day but use only past
    # data (pandas .rolling() is left-aligned with the trailing window).
    return_20d = spy.pct_change(periods=20)
    return_5d = spy.pct_change(periods=5)
    vol_20d = spy_return.rolling(window=20, min_periods=20).std() * math.sqrt(TRADING_DAYS_PER_YEAR)

    regimes: list[str | None] = []
    for i in range(len(frame)):
        if i < WARMUP_DAYS:
            regimes.append(None)
            continue
        r5 = return_5d.iloc[i]
        r20 = return_20d.iloc[i]
        v20 = vol_20d.iloc[i]
        prior_r20 = return_20d.iloc[i - 5] if i - 5 >= 0 else None
        regime = "neutral"
        if pd.notna(r5) and r5 <= PANIC_5D_RETURN_THRESHOLD:
            regime = "panic"
        elif (
            pd.notna(r5)
            and r5 >= RECOVERY_5D_RETURN_THRESHOLD
            and prior_r20 is not None
            and pd.notna(prior_r20)
            and prior_r20 < 0.0
        ):
            regime = "recovery"
        elif pd.notna(r20) and r20 <= BEAR_20D_RETURN_THRESHOLD:
            regime = "bear_trend"
        elif pd.notna(r20) and r20 >= BULL_20D_RETURN_THRESHOLD:
            regime = "bull_trend"
        elif pd.notna(v20) and v20 > HIGH_VOL_ANNUALIZED:
            regime = "high_vol"
        elif pd.notna(v20) and v20 < LOW_VOL_ANNUALIZED:
            regime = "low_vol"
        regimes.append(regime)
    out = frame.copy()
    out["regime"] = regimes
    out["spy_return"] = spy_return
    out["vol_20d"] = vol_20d
    return out


# ---------------------------------------------------------------------------
# Per-strategy regime metrics
# ---------------------------------------------------------------------------

def _max_drawdown(returns: pd.Series) -> float | None:
    if returns.empty:
        return None
    cumulative = (1.0 + returns.fillna(0.0)).cumprod()
    peak = cumulative.cummax()
    drawdown = cumulative / peak - 1.0
    return float(drawdown.min())


def _strategy_regime_metrics(strategy: str, frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if strategy not in frame.columns:
        return {
            regime: {
                "regime": regime,
                "observation_count": 0,
                "total_return": None,
                "average_return": None,
                "hit_rate": None,
                "realized_volatility": None,
                "max_drawdown": None,
                "average_contribution": None,
                "top_contributors": [],
                "top_detractors": [],
                "confidence": "LOW",
                "reason_codes": ["strategy_returns_missing"],
            }
            for regime in REGIME_LABELS
        }
    strategy_return = frame[strategy].astype(float).pct_change()
    eligible = frame.dropna(subset=["regime"]).copy()
    eligible["strategy_return"] = strategy_return.loc[eligible.index]
    eligible = eligible.dropna(subset=["strategy_return"])
    out: dict[str, dict[str, Any]] = {}
    for regime in REGIME_LABELS:
        slice_df = eligible.loc[eligible["regime"] == regime]
        observation_count = int(len(slice_df))
        reasons: list[str] = []
        if observation_count == 0:
            out[regime] = {
                "regime": regime,
                "observation_count": 0,
                "total_return": None,
                "average_return": None,
                "hit_rate": None,
                "realized_volatility": None,
                "max_drawdown": None,
                "average_contribution": None,
                "top_contributors": [],
                "top_detractors": [],
                "confidence": "LOW",
                "reason_codes": ["no_observations_in_regime"],
            }
            continue
        returns = slice_df["strategy_return"]
        total_return = float((1.0 + returns.fillna(0.0)).prod() - 1.0)
        average_return = float(returns.mean())
        hit_rate = float((returns > 0).sum() / observation_count)
        realized_vol = float(returns.std() * math.sqrt(TRADING_DAYS_PER_YEAR)) if observation_count > 1 else None
        mdd = _max_drawdown(returns)
        if observation_count < MIN_OBS_MEDIUM_CONFIDENCE:
            confidence = "LOW"
            reasons.append("insufficient_observations")
        elif observation_count < MIN_OBS_HIGH_CONFIDENCE:
            confidence = "MEDIUM"
        else:
            confidence = "HIGH"
        out[regime] = {
            "regime": regime,
            "observation_count": observation_count,
            "total_return": _round(total_return),
            "average_return": _round(average_return),
            "hit_rate": _round(hit_rate),
            "realized_volatility": _round(realized_vol),
            "max_drawdown": _round(mdd),
            "average_contribution": None,
            "top_contributors": [],
            "top_detractors": [],
            "confidence": confidence,
            "reason_codes": sorted(set(reasons)) or ["ok"],
        }
    return out


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------

def build_regime_attribution(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root)

    nav_path = repo / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv"
    frame = _load_nav_series(repo)

    if frame is None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "date": trade_date,
            "available": False,
            "confidence": "LOW",
            "regime_labels": list(REGIME_LABELS),
            "regime_distribution": {},
            "history_window": {
                "first_date": None,
                "last_date": None,
                "total_days": 0,
                "classified_days": 0,
            },
            "strategies": {},
            "reason_codes": ["missing_shadow_nav_series"],
            "source_artifacts": [],
        }
        out_dir = (Path(output_root) if output_root is not None else repo / "outputs" / "research" / "regime_attribution") / trade_date
        _write_json(out_dir / "regime_attribution.json", payload)
        _write_text(out_dir / "regime_attribution.md", render_markdown(payload))
        return payload

    frame = _filter_to_target_date(frame, trade_date)
    reason_codes: list[str] = []
    if frame.empty:
        reason_codes.append("no_history_at_or_before_target_date")
    if SPY_COLUMN not in frame.columns:
        reason_codes.append("missing_spy_benchmark_column")
    classified = _classify_regimes(frame)
    classified_only = classified.dropna(subset=["regime"]) if "regime" in classified.columns else classified.iloc[0:0]

    regime_distribution: dict[str, int] = {regime: 0 for regime in REGIME_LABELS}
    for regime in classified_only["regime"].astype(str):
        if regime in regime_distribution:
            regime_distribution[regime] += 1

    if not reason_codes and len(classified_only) < WARMUP_DAYS:
        reason_codes.append("history_below_warmup_window")

    strategies_payload: dict[str, dict[str, Any]] = {}
    for strategy in STRATEGIES:
        strategies_payload[strategy] = {
            "strategy": strategy,
            "regimes": _strategy_regime_metrics(strategy, classified),
        }

    confidences: list[str] = []
    for s in STRATEGIES:
        for r in REGIME_LABELS:
            row = strategies_payload[s]["regimes"][r]
            if row.get("observation_count"):
                confidences.append(str(row.get("confidence") or "LOW"))
    rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    aggregate_confidence = (
        "HIGH"
        if confidences and all(rank[c] >= 2 for c in confidences)
        else (
            "MEDIUM"
            if confidences and all(rank[c] >= 1 for c in confidences)
            else "LOW"
        )
    )

    available = bool(
        SPY_COLUMN in (frame.columns if frame is not None else [])
        and len(classified_only) >= WARMUP_DAYS
        and not reason_codes
    )

    first_date = (
        classified["date"].iloc[0].date().isoformat() if not classified.empty else None
    )
    last_date = (
        classified["date"].iloc[-1].date().isoformat() if not classified.empty else None
    )

    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "available": available,
        "confidence": aggregate_confidence if available else "LOW",
        "regime_labels": list(REGIME_LABELS),
        "regime_distribution": regime_distribution,
        "history_window": {
            "first_date": first_date,
            "last_date": last_date,
            "total_days": int(len(classified)),
            "classified_days": int(len(classified_only)),
        },
        "strategies": strategies_payload,
        "reason_codes": sorted(set(reason_codes)) or ["ok"],
        "source_artifacts": [str(nav_path)],
    }

    out_dir = (Path(output_root) if output_root is not None else repo / "outputs" / "research" / "regime_attribution") / trade_date
    _write_json(out_dir / "regime_attribution.json", payload)
    _write_text(out_dir / "regime_attribution.md", render_markdown(payload))
    return payload


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Regime Attribution - {payload.get('date')}",
        "",
        f"- Available: {payload.get('available')}",
        f"- Confidence: {payload.get('confidence')}",
        f"- History window: {payload.get('history_window', {}).get('first_date')} -> {payload.get('history_window', {}).get('last_date')}",
        f"- Classified days: {payload.get('history_window', {}).get('classified_days')}",
        f"- Reason codes: {', '.join(payload.get('reason_codes') or [])}",
        "",
        "## Regime Distribution",
        "",
        "| Regime | Days |",
        "|---|---:|",
    ]
    distribution = payload.get("regime_distribution") or {}
    for regime in REGIME_LABELS:
        lines.append(f"| {regime} | {distribution.get(regime, 0)} |")
    lines.append("")
    lines.append("## Per-Strategy Regime Performance")
    lines.append("")
    strategies = payload.get("strategies") or {}
    for strategy in sorted(strategies):
        regimes = (strategies[strategy] or {}).get("regimes") or {}
        lines += [
            f"### {strategy}",
            "",
            "| Regime | Obs | Total Ret | Avg Ret | Hit Rate | Vol | Max DD | Confidence | Reasons |",
            "|---|---:|---:|---:|---:|---:|---:|---|---|",
        ]
        for regime in REGIME_LABELS:
            row = regimes.get(regime) or {}
            lines.append(
                f"| {regime} | {row.get('observation_count')} | {row.get('total_return')} | {row.get('average_return')} | {row.get('hit_rate')} | {row.get('realized_volatility')} | {row.get('max_drawdown')} | {row.get('confidence')} | {', '.join(row.get('reason_codes') or [])} |"
            )
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Tier 3 regime attribution artifacts (research-only).")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_regime_attribution(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(
        json.dumps(
            {
                "date": args.date,
                "available": payload["available"],
                "confidence": payload["confidence"],
                "regime_distribution": payload["regime_distribution"],
                "reason_codes": payload["reason_codes"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
