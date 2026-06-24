#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd

from core.feedback_loop_artifacts import write_feedback_loop_artifacts
from core.strategy_registry import load_strategy_registry
from research.alpha_lab_v1.signals import build_alpha_lab_signal_frame
from research.alpha_lab_v2.engine import build_target_snapshot
from research.flow_detection.data import ensure_price_panel, load_universe
from research.shadow_tracking import run as shadow
from research.shadow_tracking.strategies import build_shadow_definitions, shadow_tracking_observation_start


BENCHMARK_SYMBOL = "SPY"
_REGISTRY = load_strategy_registry()
OPTIONAL_PRE_INCEPTION_SLUGS = tuple(
    entry.strategy_id
    for entry in _REGISTRY.active_shadow_security_selection_entries()
    if (entry.shadow_tracking or {}).get("baseline_strategy_id")
)
MODEL_SLUGS = (*_REGISTRY.active_shadow_security_selection_ids(), "spy_benchmark")
OBSERVATION_START_BY_SLUG = {
    entry.strategy_id: shadow_tracking_observation_start(entry)
    for entry in _REGISTRY.active_shadow_security_selection_entries()
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Regenerate artifact-only daily shadow scorecard inputs without full historical backtests."
    )
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--start-date", default="2014-01-01")
    parser.add_argument("--output-dir", default="outputs/shadow_candidates")
    parser.add_argument("--price-cache-path", default="outputs/research/flow_detection_v1/price_panel.parquet")
    return parser


def _strategy_payload(*, definition: Any, snapshot: dict[str, Any], trade_date: str) -> dict[str, Any]:
    weights = snapshot["weights"]
    weights = weights[weights > 0].sort_values(ascending=False)
    rank_table = snapshot["rank_table"]
    holdings = []
    for ticker, weight in weights.items():
        row = rank_table[rank_table["ticker"] == ticker]
        momentum_rank = float(row["momentum_rank"].iloc[0]) if not row.empty else None
        momentum_score = float(row["momentum_score"].iloc[0]) if not row.empty else None
        holdings.append(
            {
                "ticker": ticker,
                "target_weight": round(float(weight), 6),
                "momentum_rank": momentum_rank,
                "momentum_score": round(momentum_score, 6) if momentum_score is not None else None,
                "estimated_holding_period_days": snapshot["holding_period_by_ticker"].get(str(ticker)),
            }
        )
    concentration = {
        "holdings_count": int(len(weights)),
        "max_weight": round(float(weights.max()), 6) if not weights.empty else 0.0,
        "top3_concentration": round(float(weights.head(3).sum()), 6) if not weights.empty else 0.0,
        "top5_concentration": round(float(weights.head(5).sum()), 6) if not weights.empty else 0.0,
        "gross_exposure": round(float(weights.sum()), 6) if not weights.empty else 0.0,
        "cash_weight": round(float(max(0.0, 1.0 - weights.sum())), 6) if not weights.empty else 1.0,
        "hhi": _weight_hhi(weights),
        "effective_n": _effective_n(weights),
    }
    return {
        "strategy_name": definition.strategy_name,
        "strategy_slug": definition.strategy_slug,
        "source_variant": definition.source_variant,
        "trade_date": trade_date,
        "effective_trade_date": snapshot["effective_trade_date"],
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "holdings": holdings,
        "target_weights": {str(ticker): round(float(weight), 6) for ticker, weight in weights.items()},
        "rank_table": rank_table.head(15).to_dict(orient="records"),
        "expected_turnover": snapshot["expected_turnover"],
        "estimated_holding_period_days": snapshot["estimated_holding_period_days"],
        "weight_concentration": concentration,
        "alpha_per_dollar_deployed_proxy": None,
        "performance_summary": None,
        "scorecard_refresh_mode": "incremental_no_full_backtest",
    }


def _weight_hhi(weights: pd.Series) -> float:
    gross = float(weights.sum()) if not weights.empty else 0.0
    if gross <= 0.0:
        return 0.0
    deployed_weights = weights.astype(float) / gross
    return round(float((deployed_weights ** 2).sum()), 6)


def _effective_n(weights: pd.Series) -> float:
    hhi = _weight_hhi(weights)
    return round(float(1.0 / hhi), 6) if hhi > 0.0 else 0.0


def _is_before_observation_start(slug: str, trade_date: str) -> bool:
    start_date = OBSERVATION_START_BY_SLUG.get(slug)
    if not start_date or not trade_date:
        return False
    return pd.Timestamp(trade_date).strftime("%Y-%m-%d") < start_date


def _append_nav_series(*, output_root: Path, shadow_performance: dict[str, Any]) -> dict[str, Any]:
    trade_date = str(shadow_performance.get("trade_date") or "")
    if shadow_performance.get("data_status") != "OK":
        return _remove_nav_series_date(
            output_root=output_root,
            trade_date=trade_date,
            reason=f"data_status={shadow_performance.get('data_status')}",
        )
    if shadow_performance.get("status") != "OK":
        return _remove_nav_series_date(
            output_root=output_root,
            trade_date=trade_date,
            reason=f"performance_status={shadow_performance.get('status')}",
        )
    strategies = shadow_performance.get("strategies") or {}
    row = {"date": trade_date}
    for slug in MODEL_SLUGS:
        if _is_before_observation_start(slug, trade_date):
            row[slug] = ""
            continue
        nav = (strategies.get(slug) or {}).get("nav")
        if nav is None:
            return {"status": "SKIPPED", "reason": f"missing_nav:{slug}"}
        row[slug] = str(nav)

    path = output_root / "performance" / "shadow_nav_series.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: dict[str, dict[str, str]] = {}
    if path.exists():
        with path.open(newline="", encoding="utf-8") as handle:
            for existing in csv.DictReader(handle):
                date = str(existing.get("date") or "")
                if date:
                    rows[date] = dict(existing)
    if trade_date in rows:
        return {
            "status": "REJECTED",
            "reason_code": "SHADOW_EXISTING_DATE_RESTATEMENT_BLOCKED",
            "reason": f"shadow_nav_series already contains date {trade_date}; use explicit recovery/restatement workflow",
            "path": str(path),
            "latest_date": max(rows) if rows else None,
        }
    continuity_error = _validate_nav_append_continuity(rows=rows, row=row, shadow_performance=shadow_performance)
    if continuity_error:
        return {
            "status": "REJECTED",
            "reason_code": continuity_error["reason_code"],
            "reason": continuity_error["reason"],
            "path": str(path),
            "latest_date": max(rows) if rows else None,
            "offending_date": trade_date,
        }
    rows[trade_date] = row
    fieldnames = ["date", *MODEL_SLUGS]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for date in sorted(rows):
            writer.writerow({field: rows[date].get(field, "") for field in fieldnames})
    return {"status": "OK", "path": str(path), "rows": len(rows), "latest_date": trade_date}


def _validate_nav_append_continuity(
    *,
    rows: dict[str, dict[str, str]],
    row: dict[str, str],
    shadow_performance: dict[str, Any],
) -> dict[str, str] | None:
    if not rows:
        return None
    trade_date = str(row.get("date") or "")
    prior_dates = sorted(date for date in rows if date < trade_date)
    if not prior_dates:
        return {
            "reason_code": "SHADOW_NAV_CONTINUITY_MISMATCH",
            "reason": f"established NAV history exists but no prior row is available before {trade_date}",
        }
    previous = rows[prior_dates[-1]]
    strategies = shadow_performance.get("strategies") or {}
    for slug in MODEL_SLUGS:
        if _is_before_observation_start(slug, trade_date) and not str(row.get(slug) or ""):
            continue
        try:
            candidate_nav = float(row[slug])
            reported_daily_return = float((strategies.get(slug) or {}).get("daily_return"))
            reported_previous_nav = float((strategies.get(slug) or {}).get("previous_nav"))
        except (KeyError, TypeError, ValueError):
            return {
                "reason_code": "SHADOW_NAV_SCHEMA_MISMATCH",
                "reason": f"missing or non-numeric continuity fields for {slug}",
            }
        try:
            prior_csv_nav = float(previous[slug])
        except (KeyError, TypeError, ValueError):
            if slug in OPTIONAL_PRE_INCEPTION_SLUGS and abs(reported_previous_nav - 1.0) <= 1e-8:
                continue
            return {
                "reason_code": "SHADOW_NAV_SCHEMA_MISMATCH",
                "reason": f"missing or non-numeric continuity fields for {slug}",
            }
        expected_nav = prior_csv_nav * (1.0 + reported_daily_return)
        if abs(reported_previous_nav - prior_csv_nav) > max(1e-8, abs(prior_csv_nav) * 1e-8):
            return {
                "reason_code": "SHADOW_NAV_CONTINUITY_MISMATCH",
                "reason": (
                    f"{slug} previous_nav={reported_previous_nav} does not match "
                    f"prior CSV NAV={prior_csv_nav} from {prior_dates[-1]}"
                ),
            }
        if abs(candidate_nav - expected_nav) > max(1e-8, abs(expected_nav) * 1e-8):
            return {
                "reason_code": "SHADOW_NAV_CONTINUITY_MISMATCH",
                "reason": (
                    f"{slug} candidate NAV={candidate_nav} does not equal prior "
                    f"NAV compounded by daily_return ({expected_nav})"
                ),
            }
    return None


def _remove_nav_series_date(*, output_root: Path, trade_date: str, reason: str) -> dict[str, Any]:
    path = output_root / "performance" / "shadow_nav_series.csv"
    if not trade_date or not path.exists():
        return {"status": "SKIPPED", "reason": reason}

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or ["date", *MODEL_SLUGS]
        rows = [dict(row) for row in reader if str(row.get("date") or "") != trade_date]

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    latest_date = rows[-1].get("date") if rows else None
    return {
        "status": "SKIPPED",
        "path": str(path),
        "rows": len(rows),
        "latest_date": latest_date,
        "reason": reason,
    }


def _publish_latest(output_root: Path, trade_date: str) -> dict[str, Any]:
    dated_dir = output_root / trade_date
    latest_dir = output_root / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    missing: list[str] = []
    published: list[str] = []
    for artifact in ("comparison.md", "comparison.json", "delta.json", "shadow_evaluation.json", "feedback_loop_summary.json"):
        source = dated_dir / artifact
        if not source.exists():
            missing.append(artifact)
            continue
        (latest_dir / artifact).write_bytes(source.read_bytes())
        published.append(artifact)
    return {
        "status": "OK" if not missing else "PARTIAL",
        "latest_dir": str(latest_dir),
        "published_artifacts": published,
        "missing_artifacts": missing,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    trade_date = pd.Timestamp(args.trade_date).strftime("%Y-%m-%d")
    output_root = Path(args.output_dir)
    dated_dir = output_root / trade_date
    dated_dir.mkdir(parents=True, exist_ok=True)

    universe = load_universe("data/universe.csv")
    panel, panel_meta = ensure_price_panel(
        symbols=sorted(set(universe + [BENCHMARK_SYMBOL])),
        start_date=args.start_date,
        end_date=trade_date,
        cache_path=args.price_cache_path,
        prefer_local=True,
        allow_download=False,
    )
    signals = build_alpha_lab_signal_frame(panel)
    if not shadow.trade_date_has_data(signals, trade_date=trade_date):
        reason = shadow.classify_no_data_reason(signals, trade_date=trade_date, allow_download=False)
        raise RuntimeError(f"cannot refresh scorecard artifacts for {trade_date}: {reason}")

    strategy_payloads: dict[str, dict[str, Any]] = {}
    for definition in build_shadow_definitions(trade_date=trade_date):
        snapshot = build_target_snapshot(signals, definition.spec, trade_date=trade_date, start_date=args.start_date)
        payload = _strategy_payload(definition=definition, snapshot=snapshot, trade_date=trade_date)
        strategy_payloads[definition.strategy_slug] = payload
        (dated_dir / f"{definition.strategy_slug}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    previous_trade_date = shadow.find_previous_trading_date(signals, trade_date=trade_date)
    delta_payload = shadow.build_delta_payload(
        output_root=output_root,
        trade_date=trade_date,
        previous_trade_date=previous_trade_date,
        strategy_payloads=strategy_payloads,
    )
    comparison_payload = shadow.build_comparison_payload(strategy_payloads, trade_date=trade_date, delta_payload=delta_payload)
    shadow_performance = shadow.build_shadow_performance_payload(
        panel=panel,
        output_root=output_root,
        trade_date=trade_date,
        previous_trade_date=previous_trade_date,
        strategy_payloads=strategy_payloads,
        data_status="OK",
    )
    (dated_dir / "delta.json").write_text(json.dumps(delta_payload, indent=2), encoding="utf-8")
    (dated_dir / "summary.json").write_text(json.dumps(comparison_payload, indent=2), encoding="utf-8")
    (dated_dir / "comparison.json").write_text(json.dumps(comparison_payload, indent=2), encoding="utf-8")
    (dated_dir / "shadow_performance.json").write_text(json.dumps(shadow_performance, indent=2), encoding="utf-8")
    evaluation = shadow.build_shadow_evaluation_payload(output_root=output_root, trade_date=trade_date)
    (dated_dir / "shadow_evaluation.json").write_text(json.dumps(evaluation, indent=2), encoding="utf-8")
    (dated_dir / "comparison.md").write_text(shadow.build_comparison_markdown(comparison_payload, dated_dir=dated_dir), encoding="utf-8")

    nav_status = _append_nav_series(output_root=output_root, shadow_performance=shadow_performance)
    feedback_summary = write_feedback_loop_artifacts(output_root=output_root, trade_date=trade_date, panel=panel)
    publish_status = _publish_latest(output_root, trade_date)
    result = {
        "trade_date": trade_date,
        "status": "OK" if nav_status.get("status") == "OK" and publish_status.get("status") == "OK" else "PARTIAL",
        "panel_meta": panel_meta,
        "nav_series": nav_status,
        "feedback_loop": {
            "status": feedback_summary.get("status"),
            "path": str(dated_dir / "feedback_loop_summary.json"),
        },
        "latest_publish": publish_status,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
