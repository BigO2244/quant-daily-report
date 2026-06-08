from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from core.strategy_registry import load_strategy_registry

SCHEMA_VERSION = "caerus_promotion_readiness_windows_v1"
_REGISTRY = load_strategy_registry()
STRATEGIES = _REGISTRY.active_shadow_security_selection_ids()
CONTROL_STRATEGY = _REGISTRY.baseline_strategy_id()
PROMOTION_CANDIDATES = _REGISTRY.promotion_candidate_ids()
WINDOWS = (20, 40, 60)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _round(value: Any, digits: int = 10) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except Exception:
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, digits)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _load_nav_series(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        frame = pd.read_csv(path)
    except Exception:
        return None
    if "date" not in frame.columns:
        return None
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date", kind="mergesort")
    return frame


def _max_drawdown(nav: pd.Series) -> float | None:
    if nav.empty:
        return None
    peaks = nav.cummax()
    drawdowns = (nav / peaks) - 1.0
    return _round(drawdowns.min())


def _collect_shadow_metrics(repo: Path, target_date: str) -> dict[str, dict[str, dict[str, float]]]:
    root = repo / "outputs" / "shadow_candidates"
    out: dict[str, dict[str, dict[str, float]]] = {}
    if not root.exists():
        return out
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            pd.Timestamp(child.name)
        except Exception:
            continue
        if child.name > target_date:
            continue
        payload = _read_json(child / "comparison.json")
        strategies = payload.get("strategies") if isinstance(payload, dict) else None
        if not isinstance(strategies, dict):
            continue
        for strategy, row in sorted(strategies.items()):
            if not isinstance(row, dict):
                continue
            holdings = row.get("holdings") if isinstance(row.get("holdings"), list) else []
            weights = sorted(
                [
                    float(h.get("target_weight") or h.get("weight") or 0.0)
                    for h in holdings
                    if isinstance(h, dict)
                ],
                reverse=True,
            )
            concentration = row.get("weight_concentration") if isinstance(row.get("weight_concentration"), dict) else {}
            out.setdefault(child.name, {})[strategy] = {
                "turnover": _round(row.get("expected_turnover")) or 0.0,
                "position_count": float(concentration.get("holdings_count") or len(weights) or 0),
                "top3_concentration": _round(concentration.get("top3_concentration")) or _round(sum(weights[:3])) or 0.0,
                "top5_concentration": _round(sum(weights[:5])) or 0.0,
            }
    return out


def _average_metric(metrics_by_date: dict[str, dict[str, dict[str, float]]], dates: set[str], strategy: str, key: str) -> float | None:
    values = [
        metrics_by_date[date][strategy][key]
        for date in sorted(dates)
        if date in metrics_by_date and strategy in metrics_by_date[date] and key in metrics_by_date[date][strategy]
    ]
    return _round(sum(values) / len(values)) if values else None


def _classify_readiness(
    *,
    strategy: str,
    window: int,
    observation_count: int,
    total_return: float | None,
    excess_vs_polaris: float | None,
    excess_vs_spy: float | None,
    hit_rate: float | None,
    max_drawdown: float | None,
    correlation_vs_polaris: float | None,
    missing_spy: bool,
) -> tuple[str, str, list[str]]:
    reasons: list[str] = []
    if observation_count < window:
        reasons.append("insufficient_observation_count")
    if strategy == CONTROL_STRATEGY:
        reasons.append("baseline_strategy_not_promotion_candidate")
    if strategy != CONTROL_STRATEGY and excess_vs_polaris is None:
        reasons.append("missing_polaris_benchmark")
    if missing_spy:
        reasons.append("missing_spy_benchmark")
    if max_drawdown is not None and max_drawdown <= -0.15:
        reasons.append("drawdown_block")
    if strategy != CONTROL_STRATEGY and correlation_vs_polaris is not None and correlation_vs_polaris >= 0.95:
        reasons.append("low_differentiation_vs_polaris")
    if observation_count < window or "missing_polaris_benchmark" in reasons or "drawdown_block" in reasons:
        return "NOT_READY", "LOW", sorted(set(reasons))
    if strategy == CONTROL_STRATEGY:
        return "WATCH", "MEDIUM", sorted(set(reasons)) or ["ok"]
    if "low_differentiation_vs_polaris" in reasons:
        return "WATCH", "LOW" if missing_spy else "MEDIUM", sorted(set(reasons))
    if total_return is not None and total_return > 0 and (excess_vs_polaris or 0) > 0 and (missing_spy or (excess_vs_spy or 0) > 0) and (hit_rate or 0) >= 0.5:
        confidence = "LOW" if missing_spy else ("HIGH" if window >= 60 else "MEDIUM")
        state = "PROMOTE" if window >= 60 and not missing_spy else "PROMOTION_CANDIDATE"
        return state, confidence, sorted(set(reasons)) or ["ok"]
    if total_return is not None and total_return > 0:
        return "WATCH", "LOW" if missing_spy else "MEDIUM", sorted(set(reasons + ["positive_return_but_promotion_thresholds_not_met"]))
    return "NOT_READY", "LOW", sorted(set(reasons + ["non_positive_window_return"]))


def build_promotion_readiness_windows(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root)
    out_dir = Path(output_root) if output_root is not None else repo / "outputs" / "research" / "promotion_readiness" / trade_date
    nav_path = repo / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv"
    nav = _load_nav_series(nav_path)
    metrics_by_date = _collect_shadow_metrics(repo, trade_date)
    reason_codes: list[str] = []
    if nav is None:
        reason_codes.append("shadow_nav_series_missing")
        payload = {
            "schema_version": SCHEMA_VERSION,
            "date": trade_date,
            "available": False,
            "confidence": "LOW",
            "promotion_recommendation": "NO_PROMOTION_RECOMMENDED",
            "strategies": {},
            "reason_codes": reason_codes,
            "source_artifacts": [str(nav_path)],
        }
        _write_outputs(out_dir, payload)
        return payload
    nav = nav[nav["date"] <= pd.Timestamp(trade_date)].copy()
    if nav.empty:
        reason_codes.append("no_nav_rows_on_or_before_date")
    strategies_payload: dict[str, Any] = {}
    for strategy in STRATEGIES:
        strategy_windows: dict[str, Any] = {}
        for window in WINDOWS:
            rows = nav.tail(window + 1).copy()
            window_dates = {d.date().isoformat() for d in rows["date"]}
            daily = rows[strategy].pct_change().dropna() if strategy in rows.columns else pd.Series(dtype=float)
            spy_daily = rows["spy_benchmark"].pct_change().dropna() if "spy_benchmark" in rows.columns else pd.Series(dtype=float)
            polaris_daily = rows[CONTROL_STRATEGY].pct_change().dropna() if CONTROL_STRATEGY in rows.columns else pd.Series(dtype=float)
            observation_count = int(daily.dropna().shape[0])
            missing_days = max(window - observation_count, 0)
            total_return = None
            if strategy in rows.columns and rows[strategy].dropna().shape[0] >= 2:
                total_return = _round((rows[strategy].dropna().iloc[-1] / rows[strategy].dropna().iloc[0]) - 1.0)
            polaris_return = None
            if CONTROL_STRATEGY in rows.columns and rows[CONTROL_STRATEGY].dropna().shape[0] >= 2:
                polaris_return = _round((rows[CONTROL_STRATEGY].dropna().iloc[-1] / rows[CONTROL_STRATEGY].dropna().iloc[0]) - 1.0)
            spy_return = None
            if "spy_benchmark" in rows.columns and rows["spy_benchmark"].dropna().shape[0] >= 2:
                spy_return = _round((rows["spy_benchmark"].dropna().iloc[-1] / rows["spy_benchmark"].dropna().iloc[0]) - 1.0)
            excess_vs_polaris = _round((total_return or 0.0) - polaris_return) if total_return is not None and polaris_return is not None else None
            excess_vs_spy = _round((total_return or 0.0) - spy_return) if total_return is not None and spy_return is not None else None
            aligned = pd.concat([daily.rename("s"), polaris_daily.rename("p")], axis=1).dropna()
            corr = None
            if strategy != CONTROL_STRATEGY and aligned.shape[0] >= 2 and aligned["s"].std(ddof=0) > 0 and aligned["p"].std(ddof=0) > 0:
                corr = _round(aligned["s"].corr(aligned["p"]))
            missing_spy = spy_return is None
            state, confidence, reasons = _classify_readiness(
                strategy=strategy,
                window=window,
                observation_count=observation_count,
                total_return=total_return,
                excess_vs_polaris=excess_vs_polaris,
                excess_vs_spy=excess_vs_spy,
                hit_rate=_round((daily > 0).mean()) if observation_count else None,
                max_drawdown=_max_drawdown(rows[strategy].dropna()) if strategy in rows.columns else None,
                correlation_vs_polaris=corr,
                missing_spy=missing_spy,
            )
            if _average_metric(metrics_by_date, window_dates, strategy, "turnover") is None:
                reasons = sorted(set(reasons + ["holdings_metrics_missing"]))
                if confidence == "HIGH":
                    confidence = "MEDIUM"
            strategy_windows[str(window)] = {
                "window_trading_days": window,
                "total_return": total_return,
                "excess_return_vs_polaris": 0.0 if strategy == CONTROL_STRATEGY and total_return is not None else excess_vs_polaris,
                "excess_return_vs_spy": excess_vs_spy,
                "hit_rate": _round((daily > 0).mean()) if observation_count else None,
                "average_daily_contribution": _round(daily.mean()) if observation_count else None,
                "realized_volatility": _round(daily.std(ddof=0) * math.sqrt(252)) if observation_count else None,
                "max_drawdown": _max_drawdown(rows[strategy].dropna()) if strategy in rows.columns else None,
                "turnover": _average_metric(metrics_by_date, window_dates, strategy, "turnover"),
                "average_position_count": _average_metric(metrics_by_date, window_dates, strategy, "position_count"),
                "average_top3_concentration": _average_metric(metrics_by_date, window_dates, strategy, "top3_concentration"),
                "average_top5_concentration": _average_metric(metrics_by_date, window_dates, strategy, "top5_concentration"),
                "daily_return_correlation_vs_polaris": corr,
                "observation_count": observation_count,
                "missing_days": missing_days,
                "confidence": confidence,
                "readiness_state": state,
                "reason_codes": reasons,
            }
        strategies_payload[strategy] = {"windows": strategy_windows}
    recommendation, blockers = _promotion_recommendation(strategies_payload)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "available": nav is not None and not nav.empty,
        "confidence": _aggregate_confidence(strategies_payload),
        "promotion_recommendation": recommendation,
        "strategies": strategies_payload,
        "windows": [str(w) for w in WINDOWS],
        "blockers": blockers,
        "reason_codes": sorted(set(reason_codes + blockers)) or ["ok"],
        "source_artifacts": [str(nav_path)] + sorted({str(repo / "outputs" / "shadow_candidates" / d / "comparison.json") for d in metrics_by_date}),
    }
    _write_outputs(out_dir, payload)
    return payload


def _promotion_recommendation(strategies_payload: dict[str, Any]) -> tuple[str, list[str]]:
    blockers: list[str] = []
    for strategy in PROMOTION_CANDIDATES:
        windows = (strategies_payload.get(strategy) or {}).get("windows") or {}
        states = [str((windows.get(str(w)) or {}).get("readiness_state")) for w in WINDOWS]
        reasons = [code for w in WINDOWS for code in ((windows.get(str(w)) or {}).get("reason_codes") or [])]
        if states == ["PROMOTION_CANDIDATE", "PROMOTION_CANDIDATE", "PROMOTE"] and not any(code in {"low_differentiation_vs_polaris", "drawdown_block", "insufficient_observation_count"} for code in reasons):
            return f"PROMOTION_REVIEW_READY:{strategy}", []
        if "insufficient_observation_count" in reasons:
            blockers.append(f"{strategy}:insufficient_observations")
        if "low_differentiation_vs_polaris" in reasons:
            blockers.append(f"{strategy}:weak_differentiation")
    return "NO_PROMOTION_RECOMMENDED", sorted(set(blockers)) or ["no_strategy_satisfies_20_40_60_readiness"]


def _aggregate_confidence(strategies_payload: dict[str, Any]) -> str:
    confidences = [
        str(row.get("confidence"))
        for strategy in strategies_payload.values()
        for row in ((strategy.get("windows") or {}).values())
    ]
    if not confidences or "LOW" in confidences:
        return "LOW"
    if "MEDIUM" in confidences:
        return "MEDIUM"
    return "HIGH"


def _write_outputs(out_dir: Path, payload: dict[str, Any]) -> None:
    _write_json(out_dir / "promotion_readiness_windows.json", payload)
    _write_text(out_dir / "promotion_readiness_windows.md", render_markdown(payload))


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Promotion Readiness Windows - {payload.get('date')}",
        "",
        f"- Available: {payload.get('available')}",
        f"- Confidence: {payload.get('confidence')}",
        f"- Recommendation: {payload.get('promotion_recommendation')}",
        f"- Reason codes: {', '.join(payload.get('reason_codes') or [])}",
        "",
        "| Strategy | Window | State | Obs | Missing | Return | Excess vs Polaris | Excess vs SPY | Hit Rate | Max DD | Confidence | Reasons |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for strategy, payload_strategy in sorted((payload.get("strategies") or {}).items()):
        for window, row in sorted((payload_strategy.get("windows") or {}).items(), key=lambda item: int(item[0])):
            lines.append(
                "| {strategy} | {window} | {state} | {obs} | {missing} | {ret} | {pol} | {spy} | {hit} | {dd} | {conf} | {reasons} |".format(
                    strategy=strategy,
                    window=window,
                    state=row.get("readiness_state"),
                    obs=row.get("observation_count"),
                    missing=row.get("missing_days"),
                    ret=row.get("total_return"),
                    pol=row.get("excess_return_vs_polaris"),
                    spy=row.get("excess_return_vs_spy"),
                    hit=row.get("hit_rate"),
                    dd=row.get("max_drawdown"),
                    conf=row.get("confidence"),
                    reasons=", ".join(row.get("reason_codes") or []),
                )
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build read-only 20/40/60 day promotion readiness artifacts.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_promotion_readiness_windows(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(json.dumps({"date": args.date, "available": payload["available"], "promotion_recommendation": payload["promotion_recommendation"], "reason_codes": payload["reason_codes"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
