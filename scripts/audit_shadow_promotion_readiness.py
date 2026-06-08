#!/usr/bin/env python3
"""Read-only promotion readiness audit for Caerus shadow strategies.

This command evaluates Shadow candidates against explicit governance criteria.
It never changes active strategy selection, portfolio construction, execution
configuration, broker state, cron, or historical artifacts.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.strategy_registry import load_strategy_registry  # noqa: E402
from scripts.check_shadow_scorecard_health import build_health_payload  # noqa: E402
from scripts.send_shadow_cio_report import build_report  # noqa: E402


_REGISTRY = load_strategy_registry()
BASELINE_SLUG = _REGISTRY.baseline_strategy_id()
BENCHMARK_SLUG = "spy_benchmark"
CHALLENGER_SLUGS = _REGISTRY.promotion_candidate_ids()
MODEL_SLUGS = (BASELINE_SLUG, *CHALLENGER_SLUGS, BENCHMARK_SLUG)
DISPLAY_NAMES = {
    entry.strategy_id: entry.display_name.replace("Caerus ", "")
    for entry in _REGISTRY.active_shadow_security_selection_entries()
} | {
    "spy_benchmark": "SPY",
}
BAD_TOKENS = ("PRICE_CACHE_STALE", "NO_PRIOR", "NO_DATA", "BROKEN_CHAIN")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Shadow promotion readiness audit.")
    parser.add_argument("--repo-root", default=str(_REPO_ROOT))
    parser.add_argument("--baseline-date", default="2026-05-11")
    parser.add_argument("--baseline-valid-days", type=int, default=16)
    parser.add_argument("--recovery-date", default="2026-05-12")
    parser.add_argument("--recovery-data-through", default="2026-05-11")
    parser.add_argument("--expected-date", default=None)
    parser.add_argument("--min-valid-days", type=int, default=30)
    parser.add_argument("--min-forward-clean-days", type=int, default=5)
    parser.add_argument("--drawdown-tolerance", type=float, default=0.02)
    parser.add_argument("--volatility-tolerance", type=float, default=0.05)
    parser.add_argument("--max-top3-concentration", type=float, default=0.60)
    parser.add_argument("--turnover-multiple", type=float, default=1.50)
    parser.add_argument("--turnover-additive-tolerance", type=float, default=0.05)
    parser.add_argument("--anomalous-day-share", type=float, default=0.50)
    parser.add_argument("--diagnostics-dir", default="outputs/diagnostics")
    return parser


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_nav_dates(path: Path) -> list[str]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return [str(row.get("date") or "") for row in csv.DictReader(handle) if row.get("date")]


def _load_performance_history(output_root: Path, *, through_date: str) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for child in sorted(output_root.iterdir() if output_root.exists() else []):
        if not child.is_dir() or child.name > through_date:
            continue
        try:
            dt.date.fromisoformat(child.name)
        except ValueError:
            continue
        payload = _read_json(child / "shadow_performance.json")
        if payload:
            rows.append((child.name, payload))
    return rows


def _forward_clean_days(history: list[tuple[str, dict[str, Any]]], *, after_date: str) -> int:
    count = 0
    for date, payload in history:
        if date <= after_date:
            continue
        if payload.get("status") == "OK" and payload.get("data_status") == "OK":
            count += 1
    return count


def _daily_returns(history: list[tuple[str, dict[str, Any]]], slug: str) -> list[float]:
    returns: list[float] = []
    for _, payload in history:
        if payload.get("status") != "OK" or payload.get("data_status") != "OK":
            continue
        strategy = (payload.get("strategies") or {}).get(slug) or {}
        value = _as_float(strategy.get("daily_return"))
        if value is not None:
            returns.append(value)
    return returns


def _latest_strategy_payload(repo_root: Path, slug: str) -> dict[str, Any]:
    latest = _read_json(repo_root / "outputs" / "shadow_candidates" / "latest" / "shadow_evaluation.json") or {}
    return (latest.get("strategies") or {}).get(slug) or {}


def _passes_metric(value: float | None, comparator: float | None, fn) -> bool:
    if value is None or comparator is None:
        return False
    return bool(fn(value, comparator))


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _classify(*, role: str, checks: list[dict[str, Any]], performance_strong: bool, freshness_ok: bool) -> str:
    if role == "BASELINE":
        return "BASELINE"
    failed = {check["name"] for check in checks if not check["passed"]}
    if not freshness_ok:
        return "NOT_READY"
    if not failed:
        return "PROMOTION_ELIGIBLE"
    if performance_strong and failed <= {"min_valid_days", "forward_clean_days", "not_one_anomalous_day"} | {"turnover_vs_polaris"}:
        return "WATCHLIST"
    if "outperform_polaris" in failed and "excess_vs_spy_beats_polaris" in failed:
        return "REJECT"
    return "NOT_READY" if not performance_strong else "WATCHLIST"


def build_promotion_audit(
    *,
    repo_root: Path,
    baseline_date: str,
    baseline_valid_days: int,
    recovery_date: str,
    recovery_data_through: str,
    expected_date: str | None,
    min_valid_days: int,
    min_forward_clean_days: int,
    drawdown_tolerance: float,
    volatility_tolerance: float,
    max_top3_concentration: float,
    turnover_multiple: float,
    turnover_additive_tolerance: float,
    anomalous_day_share: float,
) -> dict[str, Any]:
    report = build_report(repo_root)
    effective_expected_date = expected_date or report.as_of_date
    health = build_health_payload(
        repo_root=repo_root,
        baseline_date=baseline_date,
        baseline_valid_days=baseline_valid_days,
        expected_date=effective_expected_date,
        strict=False,
    )
    output_root = repo_root / "outputs" / "shadow_candidates"
    history = _load_performance_history(output_root, through_date=report.as_of_date)
    forward_days = _forward_clean_days(history, after_date=recovery_data_through)
    polaris = _latest_strategy_payload(repo_root, BASELINE_SLUG)
    spy = _latest_strategy_payload(repo_root, BENCHMARK_SLUG)
    freshness_ok = (
        report.data_health == "Fresh"
        and health.get("shadow_refresh_status") == "OK"
        and not health.get("shadow_refresh_reason")
    )
    stale_reasons = "; ".join(
        str(value)
        for value in [
            report.data_health_reason,
            health.get("shadow_refresh_reason"),
        ]
        if value
    )
    bad_reason_present = any(token in stale_reasons for token in BAD_TOKENS)

    strategies: dict[str, Any] = {}
    for slug in (BASELINE_SLUG, *CHALLENGER_SLUGS):
        payload = _latest_strategy_payload(repo_root, slug)
        role = "BASELINE" if slug == BASELINE_SLUG else "CHALLENGER"
        valid_days = int(payload.get("rolling_count_of_valid_days") or 0)
        cumulative = _as_float(payload.get("cumulative_return"))
        excess = _as_float(payload.get("excess_return_vs_spy"))
        max_drawdown = _as_float(payload.get("max_drawdown"))
        volatility = _as_float(payload.get("realized_volatility_ann"))
        turnover = _as_float(payload.get("avg_turnover"))
        concentration = _as_float(payload.get("avg_top_3_concentration"))
        polaris_cumulative = _as_float(polaris.get("cumulative_return"))
        polaris_excess = _as_float(polaris.get("excess_return_vs_spy"))
        polaris_drawdown = _as_float(polaris.get("max_drawdown"))
        polaris_volatility = _as_float(polaris.get("realized_volatility_ann"))
        polaris_turnover = _as_float(polaris.get("avg_turnover"))
        returns = _daily_returns(history, slug)
        largest_abs_daily = max((abs(value) for value in returns), default=None)
        anomalous_limit = abs(cumulative or 0.0) * anomalous_day_share

        checks = [
            _check("scorecard_fresh", freshness_ok, f"health={health['status']}; data_health={report.data_health}"),
            _check("no_bad_freshness_reasons", not bad_reason_present, stale_reasons or "none"),
            _check("min_valid_days", valid_days >= min_valid_days, f"{valid_days} >= {min_valid_days}"),
            _check("forward_clean_days", forward_days >= min_forward_clean_days, f"{forward_days} >= {min_forward_clean_days} after {recovery_date}"),
        ]
        if role == "CHALLENGER":
            checks.extend(
                [
                    _check(
                        "outperform_polaris",
                        _passes_metric(cumulative, polaris_cumulative, lambda left, right: left > right),
                        f"{cumulative} > {polaris_cumulative}",
                    ),
                    _check(
                        "excess_vs_spy_beats_polaris",
                        _passes_metric(excess, polaris_excess, lambda left, right: left > right),
                        f"{excess} > {polaris_excess}",
                    ),
                    _check(
                        "drawdown_within_tolerance",
                        _passes_metric(max_drawdown, polaris_drawdown, lambda left, right: left >= right - drawdown_tolerance),
                        f"{max_drawdown} >= {polaris_drawdown} - {drawdown_tolerance}",
                    ),
                    _check(
                        "volatility_within_tolerance",
                        _passes_metric(volatility, polaris_volatility, lambda left, right: left <= right + volatility_tolerance),
                        f"{volatility} <= {polaris_volatility} + {volatility_tolerance}",
                    ),
                    _check(
                        "top3_concentration_limit",
                        concentration is not None and concentration <= max_top3_concentration,
                        f"{concentration} <= {max_top3_concentration}",
                    ),
                    _check(
                        "turnover_vs_polaris",
                        _passes_metric(
                            turnover,
                            polaris_turnover,
                            lambda left, right: left <= right * turnover_multiple + turnover_additive_tolerance,
                        ),
                        f"{turnover} <= {polaris_turnover} * {turnover_multiple} + {turnover_additive_tolerance}",
                    ),
                    _check(
                        "not_one_anomalous_day",
                        largest_abs_daily is not None and (cumulative is None or abs(cumulative) == 0 or largest_abs_daily <= anomalous_limit),
                        f"largest_abs_daily={largest_abs_daily}; limit={anomalous_limit}",
                    ),
                ]
            )
        performance_strong = bool(
            role == "CHALLENGER"
            and cumulative is not None
            and polaris_cumulative is not None
            and excess is not None
            and polaris_excess is not None
            and cumulative > polaris_cumulative
            and excess > polaris_excess
        )
        classification = _classify(role=role, checks=checks, performance_strong=performance_strong, freshness_ok=freshness_ok)
        failed = [check["name"] for check in checks if not check["passed"]]
        passed = [check["name"] for check in checks if check["passed"]]
        if role == "BASELINE":
            recommendation = "Polaris remains the paper baseline."
        elif classification == "PROMOTION_ELIGIBLE":
            recommendation = "Eligible for explicit human promotion review; no automatic promotion is performed."
        elif performance_strong:
            recommendation = "Keep on WATCHLIST; continue forward clean observation window."
        else:
            recommendation = "Not ready for promotion review."
        strategies[slug] = {
            "name": DISPLAY_NAMES[slug],
            "role": role,
            "valid_days": valid_days,
            "forward_clean_days_after_recovery": forward_days,
            "cumulative_return": cumulative,
            "excess_return_vs_spy": excess,
            "cumulative_gap_vs_polaris": None if cumulative is None or polaris_cumulative is None else round(cumulative - polaris_cumulative, 10),
            "excess_gap_vs_polaris": None if excess is None or polaris_excess is None else round(excess - polaris_excess, 10),
            "realized_volatility_ann": volatility,
            "max_drawdown": max_drawdown,
            "avg_turnover": turnover,
            "avg_top_3_concentration": concentration,
            "largest_abs_daily_return": largest_abs_daily,
            "data_status": payload.get("data_status"),
            "chain_status": payload.get("status"),
            "passed_criteria": passed,
            "failed_criteria": failed,
            "promotion_classification": classification,
            "operator_recommendation": recommendation,
        }

    return {
        "schema_version": "shadow_promotion_readiness_v1",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "read_only": True,
        "active_baseline": BASELINE_SLUG,
        "benchmark": "SPY",
        "latest_source_date": report.latest_source_date,
        "data_through_date": report.as_of_date,
        "scorecard_status": report.data_health,
        "scorecard_reason": report.data_health_reason,
        "health_status": health["status"],
        "shadow_refresh_status": health.get("shadow_refresh_status"),
        "shadow_refresh_reason": health.get("shadow_refresh_reason"),
        "recovery_date": recovery_date,
        "recovery_data_through": recovery_data_through,
        "forward_clean_days_after_recovery": forward_days,
        "thresholds": {
            "min_valid_days": min_valid_days,
            "min_forward_clean_days": min_forward_clean_days,
            "drawdown_tolerance": drawdown_tolerance,
            "volatility_tolerance": volatility_tolerance,
            "max_top3_concentration": max_top3_concentration,
            "turnover_multiple": turnover_multiple,
            "turnover_additive_tolerance": turnover_additive_tolerance,
            "anomalous_day_share": anomalous_day_share,
        },
        "strategies": strategies,
        "operator_summary": (
            "Forward clean observation window has not yet been established."
            if forward_days == 0
            else f"Forward clean observation days after recovery: {forward_days}."
        ),
    }


def _fmt_pct(value: Any) -> str:
    number = _as_float(value)
    return "N/A" if number is None else f"{number:+.2%}"


def _write_artifacts(payload: dict[str, Any], diagnostics_dir: Path) -> tuple[Path, Path]:
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    json_path = diagnostics_dir / f"shadow_promotion_readiness_{stamp}.json"
    md_path = diagnostics_dir / f"shadow_promotion_readiness_{stamp}.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        "# Shadow Promotion Readiness",
        "",
        f"- Read-only: `{payload['read_only']}`",
        f"- Data through: `{payload['data_through_date']}`",
        f"- Scorecard: `{payload['scorecard_status']}`",
        f"- Shadow refresh: `{payload['shadow_refresh_status']}`",
        f"- Forward clean days after recovery: `{payload['forward_clean_days_after_recovery']}`",
        f"- Operator summary: {payload['operator_summary']}",
        "",
        "| Strategy | Role | Classification | Valid Days | Forward Clean Days | Cumulative | Excess vs SPY | Failed Criteria |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for slug in (BASELINE_SLUG, *CHALLENGER_SLUGS):
        row = payload["strategies"][slug]
        lines.append(
            "| "
            + " | ".join(
                [
                    row["name"],
                    row["role"],
                    row["promotion_classification"],
                    str(row["valid_days"]),
                    str(row["forward_clean_days_after_recovery"]),
                    _fmt_pct(row["cumulative_return"]),
                    _fmt_pct(row["excess_return_vs_spy"]),
                    ", ".join(row["failed_criteria"]) or "None",
                ]
            )
            + " |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    payload = build_promotion_audit(
        repo_root=repo_root,
        baseline_date=args.baseline_date,
        baseline_valid_days=args.baseline_valid_days,
        recovery_date=args.recovery_date,
        recovery_data_through=args.recovery_data_through,
        expected_date=args.expected_date,
        min_valid_days=args.min_valid_days,
        min_forward_clean_days=args.min_forward_clean_days,
        drawdown_tolerance=args.drawdown_tolerance,
        volatility_tolerance=args.volatility_tolerance,
        max_top3_concentration=args.max_top3_concentration,
        turnover_multiple=args.turnover_multiple,
        turnover_additive_tolerance=args.turnover_additive_tolerance,
        anomalous_day_share=args.anomalous_day_share,
    )
    json_path, md_path = _write_artifacts(payload, repo_root / args.diagnostics_dir)
    print(json.dumps({"json_path": str(json_path), "md_path": str(md_path), "classifications": {k: v["promotion_classification"] for k, v in payload["strategies"].items()}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
