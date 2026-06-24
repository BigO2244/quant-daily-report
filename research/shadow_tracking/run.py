from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path

import pandas as pd

from alpha_stack.research.metrics import summarise_performance
from core.strategy_registry import load_strategy_registry
from research.alpha_lab_v1.signals import build_alpha_lab_signal_frame
from research.alpha_lab_v2.engine import build_target_snapshot, run_backtest
from research.flow_detection.data import ensure_price_panel, load_universe

from core.feedback_loop_artifacts import write_feedback_loop_artifacts

from .strategies import build_shadow_definitions, shadow_tracking_active_on


BENCHMARK_SYMBOL = "SPY"
BENCHMARK_SLUG = "spy_benchmark"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DEV-only shadow tracking for Caerus momentum candidates")
    parser.add_argument("--trade-date", default=None)
    parser.add_argument("--start-date", default="2014-01-01")
    parser.add_argument("--end-date", default=pd.Timestamp.today().normalize().strftime("%Y-%m-%d"))
    parser.add_argument("--output-dir", default="outputs/shadow_candidates")
    parser.add_argument("--price-cache-path", default="outputs/research/flow_detection_v1/price_panel.parquet")
    parser.add_argument("--allow-download", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_root = Path(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    trade_date = str(pd.Timestamp(args.trade_date or args.end_date).strftime("%Y-%m-%d"))
    dated_dir = output_root / trade_date
    dated_dir.mkdir(parents=True, exist_ok=True)
    print(f"[SHADOW] created folder for trade_date={trade_date}")

    universe = load_universe("data/universe.csv")
    panel, panel_meta = ensure_price_panel(
        symbols=sorted(set(universe + [BENCHMARK_SYMBOL])),
        start_date=args.start_date,
        end_date=args.end_date,
        cache_path=args.price_cache_path,
        allow_download=bool(args.allow_download),
    )
    signals = build_alpha_lab_signal_frame(panel)
    print(resolve_trade_date(signals, requested_trade_date=args.trade_date, end_date=args.end_date))
    previous_trade_date = find_previous_trading_date(signals, trade_date=trade_date)

    if not trade_date_has_data(signals, trade_date=trade_date):
        no_data_reason = classify_no_data_reason(signals, trade_date=trade_date, allow_download=bool(args.allow_download))
        print(f"[SHADOW] no data for trade_date={trade_date}")
        print(f"[SHADOW] no data reason: {no_data_reason}")
        delta_payload = {
            "trade_date": trade_date,
            "previous_date": previous_trade_date,
            "status": "NO_DATA",
            "reason_code": no_data_reason,
            "strategies": {},
        }
        comparison_payload = build_no_data_comparison_payload(
            trade_date=trade_date,
            delta_payload=delta_payload,
            panel_meta=panel_meta,
            reason_code=no_data_reason,
        )
        (dated_dir / "delta.json").write_text(json.dumps(delta_payload, indent=2))
        (dated_dir / "summary.json").write_text(json.dumps(comparison_payload, indent=2))
        (dated_dir / "comparison.json").write_text(json.dumps(comparison_payload, indent=2))
        shadow_performance = build_shadow_performance_payload(
            panel=panel,
            output_root=output_root,
            trade_date=trade_date,
            previous_trade_date=previous_trade_date,
            strategy_payloads={},
            data_status="NO_DATA",
            data_reason=no_data_reason,
        )
        (dated_dir / "shadow_performance.json").write_text(json.dumps(shadow_performance, indent=2))
        print(f"[SHADOW] performance computed for trade_date={trade_date}")
        print("[SHADOW] NAV updated")
        shadow_evaluation = build_shadow_evaluation_payload(output_root=output_root, trade_date=trade_date)
        (dated_dir / "shadow_evaluation.json").write_text(json.dumps(shadow_evaluation, indent=2))
        write_phase_c_promotion_artifacts(output_root=output_root, trade_date=trade_date, shadow_evaluation=shadow_evaluation)
        (dated_dir / "comparison.md").write_text(build_comparison_markdown(comparison_payload, dated_dir=dated_dir))
        try:
            write_feedback_loop_artifacts(output_root=output_root, trade_date=trade_date, panel=panel)
            print(f"[SHADOW] feedback loop artifacts written for trade_date={trade_date}")
        except Exception as exc:
            print(f"[SHADOW] feedback loop artifacts skipped: {exc}")
        print(f"[SHADOW] evaluation summary written for trade_date={trade_date}")
        print("[SHADOW] delta status: NO_PRIOR")
        print(f"[SHADOW] wrote {dated_dir}/...")
        return 0

    definitions = build_shadow_definitions(trade_date=trade_date)
    strategy_payloads = {}
    backtest_results = {}
    for definition in definitions:
        snapshot = build_target_snapshot(signals, definition.spec, trade_date=trade_date, start_date=args.start_date)
        backtest = run_backtest(signals, definition.spec, start_date=args.start_date, end_date=trade_date)
        backtest_results[definition.strategy_slug] = backtest
        payload = build_strategy_payload(
            definition=definition,
            snapshot=snapshot,
            backtest=backtest,
            trade_date=trade_date,
        )
        strategy_payloads[definition.strategy_slug] = payload
        (dated_dir / f"{definition.strategy_slug}.json").write_text(json.dumps(payload, indent=2))

    delta_payload = build_delta_payload(
        output_root=output_root,
        trade_date=trade_date,
        previous_trade_date=previous_trade_date,
        strategy_payloads=strategy_payloads,
    )
    if delta_payload.get("status") == "OK":
        print(f"[SHADOW] delta comparison vs {delta_payload.get('previous_date')}")
        print("[SHADOW] delta status: OK")
    else:
        print("[SHADOW] delta status: NO_PRIOR")
    (dated_dir / "delta.json").write_text(json.dumps(delta_payload, indent=2))

    comparison_payload = build_comparison_payload(strategy_payloads, trade_date=trade_date, delta_payload=delta_payload)
    (dated_dir / "summary.json").write_text(json.dumps(comparison_payload, indent=2))
    (dated_dir / "comparison.json").write_text(json.dumps(comparison_payload, indent=2))
    shadow_performance = build_shadow_performance_payload(
        panel=panel,
        output_root=output_root,
        trade_date=trade_date,
        previous_trade_date=previous_trade_date,
        strategy_payloads=strategy_payloads,
        data_status="OK",
    )
    (dated_dir / "shadow_performance.json").write_text(json.dumps(shadow_performance, indent=2))
    print(f"[SHADOW] performance computed for trade_date={trade_date}")
    print("[SHADOW] NAV updated")
    shadow_evaluation = build_shadow_evaluation_payload(output_root=output_root, trade_date=trade_date)
    (dated_dir / "shadow_evaluation.json").write_text(json.dumps(shadow_evaluation, indent=2))
    write_phase_c_promotion_artifacts(output_root=output_root, trade_date=trade_date, shadow_evaluation=shadow_evaluation)
    (dated_dir / "comparison.md").write_text(build_comparison_markdown(comparison_payload, dated_dir=dated_dir))
    try:
        write_feedback_loop_artifacts(output_root=output_root, trade_date=trade_date, panel=panel)
        print(f"[SHADOW] feedback loop artifacts written for trade_date={trade_date}")
    except Exception as exc:
        print(f"[SHADOW] feedback loop artifacts skipped: {exc}")
    print(f"[SHADOW] evaluation summary written for trade_date={trade_date}")
    print(f"[SHADOW] wrote {dated_dir}/...")

    nav_series, summary = build_performance_artifacts(
        panel=panel,
        trade_date=trade_date,
        start_date=args.start_date,
        output_root=output_root,
        strategy_payloads=strategy_payloads,
        backtest_results=backtest_results,
        panel_meta=panel_meta,
    )
    (output_root / "performance").mkdir(parents=True, exist_ok=True)
    nav_series.to_csv(output_root / "performance" / "shadow_nav_series.csv", index=False)
    (output_root / "performance" / "shadow_summary.json").write_text(json.dumps(summary, indent=2))
    return 0


def resolve_trade_date(signals: pd.DataFrame, *, requested_trade_date: str | None, end_date: str) -> str:
    requested = str(pd.Timestamp(requested_trade_date or end_date).strftime("%Y-%m-%d"))
    if trade_date_has_data(signals, trade_date=requested):
        return f"[SHADOW] using requested trade_date={requested}"
    nearest = find_latest_available_trade_date(signals, trade_date=requested)
    if nearest:
        return f"[SHADOW] requested trade_date unavailable in data; nearest available date is {nearest}"
    return f"[SHADOW] requested trade_date unavailable in data; no earlier market date exists in panel"


def trade_date_has_data(signals: pd.DataFrame, *, trade_date: str) -> bool:
    if signals.empty or "date" not in signals.columns:
        return False
    dates = pd.DatetimeIndex(pd.to_datetime(signals["date"])).strftime("%Y-%m-%d")
    return trade_date in set(dates)


def find_latest_available_trade_date(signals: pd.DataFrame, *, trade_date: str) -> str | None:
    if signals.empty or "date" not in signals.columns:
        return None
    dates = pd.DatetimeIndex(pd.to_datetime(signals["date"])).sort_values().unique()
    eligible = dates[dates <= pd.Timestamp(trade_date)]
    if len(eligible) == 0:
        return None
    return str(eligible[-1].date())


def find_previous_trading_date(signals: pd.DataFrame, *, trade_date: str) -> str | None:
    if signals.empty or "date" not in signals.columns:
        return None
    dates = pd.DatetimeIndex(pd.to_datetime(signals["date"])).sort_values().unique()
    earlier = dates[dates < pd.Timestamp(trade_date)]
    if len(earlier) == 0:
        return None
    return str(earlier[-1].date())


def build_strategy_payload(*, definition, snapshot: dict, backtest: dict, trade_date: str) -> dict:
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
    performance_summary = dict(backtest["summary"])
    performance_summary["alpha_per_dollar_deployed_proxy"] = _alpha_per_dollar_deployed_proxy(
        performance_summary.get("excess_return_vs_spy"),
        performance_summary.get("avg_cash_weight"),
        concentration.get("gross_exposure"),
    )
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
        "alpha_per_dollar_deployed_proxy": performance_summary["alpha_per_dollar_deployed_proxy"],
        "performance_summary": performance_summary,
    }


def classify_no_data_reason(signals: pd.DataFrame, *, trade_date: str, allow_download: bool = False) -> str:
    if signals.empty or "date" not in signals.columns:
        return "NO_SIGNAL_DATA"
    max_date = pd.to_datetime(signals["date"], errors="coerce").max()
    if pd.notna(max_date) and max_date.normalize() < pd.Timestamp(trade_date).normalize():
        return "PRICE_CACHE_STALE" if not allow_download else "PRICE_DATA_UNAVAILABLE_AFTER_HYDRATION"
    return "NO_DATA_FOR_TRADE_DATE"


def build_no_data_comparison_payload(*, trade_date: str, delta_payload: dict, panel_meta: dict, reason_code: str = "NO_DATA_FOR_TRADE_DATE") -> dict:
    return {
        "trade_date": trade_date,
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "shadow_methodology": "model_portfolio",
        "status": "NO_DATA",
        "reason_code": reason_code,
        "message": "No shadow portfolio generated because the requested trade date is unavailable in the data panel.",
        "delta": delta_payload,
        "strategies": {},
        "pairwise_overlap": [],
        "differences_vs_polaris": {},
        "broker_context": {},
        "data": panel_meta,
    }


def build_comparison_payload(strategy_payloads: dict[str, dict], *, trade_date: str, delta_payload: dict | None = None) -> dict:
    pairwise = []
    for left_slug, right_slug in combinations(strategy_payloads.keys(), 2):
        left = strategy_payloads[left_slug]
        right = strategy_payloads[right_slug]
        pairwise.append(compare_two_strategies(left, right))

    baseline_slug = _baseline_strategy_slug()
    baseline_payload = strategy_payloads.get(baseline_slug)
    return {
        "trade_date": trade_date,
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "shadow_methodology": "model_portfolio",
        "delta": delta_payload,
        "strategies": {
            slug: {
                "strategy_name": payload["strategy_name"],
                "expected_turnover": payload["expected_turnover"],
                "estimated_holding_period_days": payload["estimated_holding_period_days"],
                "weight_concentration": payload["weight_concentration"],
                "alpha_per_dollar_deployed_proxy": payload.get("alpha_per_dollar_deployed_proxy"),
                "holdings": payload["holdings"],
            }
            for slug, payload in strategy_payloads.items()
        },
        "pairwise_overlap": pairwise,
        "differences_vs_polaris": {
            slug: compare_two_strategies(baseline_payload, payload)
            for slug, payload in strategy_payloads.items()
            if baseline_payload is not None and slug != baseline_slug
        },
        "broker_context": _load_broker_context(strategy_payloads),
    }


def compare_two_strategies(left: dict, right: dict) -> dict:
    left_weights = pd.Series(left["target_weights"], dtype=float)
    right_weights = pd.Series(right["target_weights"], dtype=float)
    common = left_weights.index.intersection(right_weights.index)
    overlap_pct = float(pd.concat([left_weights.reindex(common), right_weights.reindex(common)], axis=1).min(axis=1).sum()) if len(common) else 0.0
    left_only = sorted(set(left_weights.index) - set(right_weights.index))
    right_only = sorted(set(right_weights.index) - set(left_weights.index))
    return {
        "left_strategy": left["strategy_name"],
        "left_slug": left["strategy_slug"],
        "right_strategy": right["strategy_name"],
        "right_slug": right["strategy_slug"],
        "overlap_weight_pct": round(overlap_pct, 6),
        "shared_names": sorted(common.tolist()),
        "left_unique_names": left_only,
        "right_unique_names": right_only,
    }


def build_comparison_markdown(comparison: dict, *, dated_dir: Path | None = None) -> str:
    strategies = comparison["strategies"]
    delta = _load_markdown_sidecar(dated_dir, "delta.json") or comparison.get("delta") or {}
    evaluation = _load_markdown_sidecar(dated_dir, "shadow_evaluation.json")
    delta_status = delta.get("status") or "NO_PRIOR"
    lines = [
        "# Shadow Candidates Comparison",
        "",
        f"## Trade Date",
        f"- {comparison['trade_date']}",
        "",
    ]
    lines.extend(_executive_summary_lines(evaluation=evaluation, delta=delta))
    lines.extend(["", "## Performance Scoreboard"])
    lines.extend(_performance_scoreboard_lines(evaluation))
    lines.extend(["", "## Relative Performance"])
    lines.extend(_relative_performance_lines(evaluation))
    lines.extend(["", "## Chain Health"])
    lines.extend(_chain_health_lines(evaluation=evaluation, delta=delta))
    lines.extend([
        "",
        "## Delta Artifact",
        "- File: delta.json",
        f"- Status: {delta_status}",
        "",
        "## Day-over-Day Changes",
    ])
    lines.extend(_delta_markdown_lines(delta))
    if comparison.get("status") == "NO_DATA":
        lines.extend(
            [
                "",
                "## Status",
                f"- {comparison.get('message')}",
                "",
                "## Benchmark Note",
                f"- Benchmark symbol: {comparison['benchmark_symbol']}",
            ]
        )
        return "\n".join(lines) + "\n"
    rendered_slugs = _comparison_strategy_slugs(comparison)
    for left_slug, right_slug in combinations(rendered_slugs, 2):
        lines.extend(["", f"## {_heading_label(left_slug)} vs {_heading_label(right_slug)}"])
        lines.extend(_pairwise_lines(comparison, left_slug, right_slug))
    lines.extend(["", "## Current Top Holdings"])
    for slug in rendered_slugs:
        payload = strategies[slug]
        top = ", ".join(f"{item['ticker']} ({item['target_weight']:.2%})" for item in payload["holdings"][:5])
        lines.append(f"- {payload['strategy_name']}: {top}")
    lines.extend(["", "## Turnover / Concentration Summary"])
    for slug in rendered_slugs:
        payload = strategies[slug]
        conc = payload["weight_concentration"]
        lines.append(
            f"- {payload['strategy_name']}: turnover {payload['expected_turnover']}, max weight {conc['max_weight']}, top-3 concentration {conc['top3_concentration']}, cash {conc.get('cash_weight', 0.0)}, alpha/deployed-dollar proxy {payload.get('alpha_per_dollar_deployed_proxy')}, est. holding period {payload['estimated_holding_period_days']}"
        )
    broker_context = comparison.get("broker_context") or {}
    if broker_context:
        lines.extend(["", "## Broker Context Appendix"])
        lines.append("- Informational only. Shadow portfolios remain model-portfolio based, not broker-authoritative.")
        lines.append(
            f"- Snapshot as of {broker_context.get('as_of') or 'unknown'} with {broker_context.get('positions_count') or 0} broker positions."
        )
        for slug in rendered_slugs:
            overlap = (broker_context.get("strategy_overlap") or {}).get(slug) or {}
            lines.append(
                f"- {comparison['strategies'][slug]['strategy_name']} overlap with broker: {overlap.get('overlap_names_count', 0)} names; broker-only names: {', '.join(overlap.get('broker_only_names') or []) or 'None'}"
            )
    lines.extend(["", "## Benchmark Note", f"- Benchmark symbol: {comparison['benchmark_symbol']}"])
    return "\n".join(lines) + "\n"


def _load_markdown_sidecar(dated_dir: Path | None, filename: str) -> dict | None:
    if dated_dir is None:
        return None
    return safe_read_json(dated_dir / filename)


def _heading_label(slug: str) -> str:
    return _strategy_label(slug).replace("Caerus ", "")


def _model_strategy_slugs(*, trade_date: str | None = None) -> tuple[str, ...]:
    registry = load_strategy_registry()
    return tuple(
        entry.strategy_id
        for entry in registry.active_shadow_security_selection_entries()
        if shadow_tracking_active_on(entry, trade_date=trade_date)
    )


def _comparison_strategy_slugs(comparison: dict) -> tuple[str, ...]:
    strategies = comparison.get("strategies") if isinstance(comparison.get("strategies"), dict) else {}
    if not strategies:
        return ()
    ordered = [slug for slug in _model_strategy_slugs() if slug in strategies]
    extras = [slug for slug in strategies if slug not in set(ordered)]
    return tuple(ordered + sorted(extras))


def _baseline_strategy_slug() -> str:
    return load_strategy_registry().baseline_strategy_id()


def _promotion_candidate_slugs() -> tuple[str, ...]:
    return load_strategy_registry().promotion_candidate_ids()


def _scoreboard_slugs(evaluation: dict | None) -> list[str]:
    strategies = (evaluation or {}).get("strategies") or {}
    slugs = [slug for slug in _model_strategy_slugs() if slug in strategies]
    if BENCHMARK_SLUG in strategies:
        slugs.append(BENCHMARK_SLUG)
    return slugs


def _strategy_label(slug: str, payload: dict | None = None) -> str:
    if payload and payload.get("strategy_name"):
        return str(payload["strategy_name"])
    labels = load_strategy_registry().strategy_labels()
    return labels.get(slug, slug)


def _strategy_role(slug: str) -> str:
    if slug == BENCHMARK_SLUG:
        return "BENCHMARK"
    baseline = _baseline_strategy_slug()
    return "BASELINE" if slug == baseline else "CHALLENGER"


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _weight_hhi(weights: pd.Series) -> float:
    gross = float(weights.sum()) if not weights.empty else 0.0
    if gross <= 0.0:
        return 0.0
    deployed_weights = weights.astype(float) / gross
    return round(float((deployed_weights ** 2).sum()), 6)


def _effective_n(weights: pd.Series) -> float:
    hhi = _weight_hhi(weights)
    return round(float(1.0 / hhi), 6) if hhi > 0.0 else 0.0


def _alpha_per_dollar_deployed_proxy(
    excess_return_vs_spy: object,
    avg_cash_weight: object,
    current_gross_exposure: object,
) -> float | None:
    excess = _as_float(excess_return_vs_spy)
    if excess is None:
        return None
    avg_cash = _as_float(avg_cash_weight)
    deployed = 1.0 - avg_cash if avg_cash is not None else _as_float(current_gross_exposure)
    if deployed is None or deployed <= 0.0:
        return None
    return round(float(excess / deployed), 6)


def _fmt_pct(value: object) -> str:
    number = _as_float(value)
    return "N/A" if number is None else f"{number:.2%}"


def _fmt_signed_pct(value: object) -> str:
    number = _as_float(value)
    return "N/A" if number is None else f"{number:+.2%}"


def _fmt_decimal(value: object) -> str:
    number = _as_float(value)
    return "N/A" if number is None else f"{number:.2f}"


def _fmt_int(value: object) -> str:
    if value is None:
        return "N/A"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return "N/A"


def _evaluation_strategies(evaluation: dict | None) -> dict:
    return (evaluation or {}).get("strategies") or {}


def _model_metrics(evaluation: dict | None) -> dict[str, dict]:
    strategies = _evaluation_strategies(evaluation)
    return {slug: strategies.get(slug) or {} for slug in _model_strategy_slugs()}


def _min_valid_days(evaluation: dict | None) -> int | None:
    metrics = _model_metrics(evaluation)
    counts = [
        int(payload.get("rolling_count_of_valid_days") or 0)
        for payload in metrics.values()
        if payload
    ]
    return min(counts) if counts else None


def _best_strategy(evaluation: dict | None, field: str) -> tuple[str, dict] | None:
    candidates = []
    for slug, payload in _model_metrics(evaluation).items():
        value = _as_float(payload.get(field))
        if value is not None:
            candidates.append((value, slug, payload))
    if not candidates:
        return None
    _, slug, payload = max(candidates, key=lambda item: item[0])
    return slug, payload


def _cum_diff(evaluation: dict | None, slug: str, baseline_slug: str | None = None) -> float | None:
    baseline_slug = baseline_slug or _baseline_strategy_slug()
    strategies = _evaluation_strategies(evaluation)
    left = _as_float((strategies.get(slug) or {}).get("cumulative_return"))
    baseline = _as_float((strategies.get(baseline_slug) or {}).get("cumulative_return"))
    if left is None or baseline is None:
        return None
    return left - baseline


def _excess_vs_spy(evaluation: dict | None, slug: str) -> float | None:
    return _as_float((_evaluation_strategies(evaluation).get(slug) or {}).get("excess_return_vs_spy"))


def _executive_summary_lines(*, evaluation: dict | None, delta: dict) -> list[str]:
    lines = ["## Executive Summary"]
    if evaluation is None:
        missing_lines = [
            "- Best performer today: N/A - performance scoreboard unavailable: shadow_evaluation.json missing",
            "- Best cumulative performer: N/A - performance scoreboard unavailable: shadow_evaluation.json missing",
        ]
        baseline = _baseline_strategy_slug()
        missing_lines.append(f"- {_heading_label(baseline)} vs SPY: N/A - shadow_evaluation.json missing")
        for slug in _promotion_candidate_slugs():
            missing_lines.append(f"- {_heading_label(slug)} vs {_heading_label(baseline)}: N/A - shadow_evaluation.json missing")
        missing_lines.extend(
            [
                f"- Chain health: UNKNOWN; delta status is {delta.get('status') or 'NO_PRIOR'}",
                "- Operator conclusion: Do not use shadow performance for decisions until shadow_evaluation.json is generated.",
            ]
        )
        return lines + missing_lines

    if BENCHMARK_SLUG not in _evaluation_strategies(evaluation):
        lines.append("- Warning: SPY benchmark unavailable in evaluation artifact")

    min_valid = _min_valid_days(evaluation)
    best_today = _best_strategy(evaluation, "daily_return")
    best_cumulative = _best_strategy(evaluation, "cumulative_return")
    initializing = min_valid is None or min_valid < 2
    if best_today:
        _, payload = best_today
        lines.append(f"- Best performer today: {_strategy_label('', payload)} ({_fmt_pct(payload.get('daily_return'))})")
    else:
        lines.append("- Best performer today: N/A")
    if best_cumulative:
        _, payload = best_cumulative
        suffix = " - INITIALIZING; performance comparison is not yet meaningful" if initializing else ""
        lines.append(f"- Best cumulative performer: {_strategy_label('', payload)} ({_fmt_pct(payload.get('cumulative_return'))}){suffix}")
    else:
        lines.append("- Best cumulative performer: N/A")
    baseline = _baseline_strategy_slug()
    lines.append(f"- {_heading_label(baseline)} vs SPY: {_fmt_signed_pct(_excess_vs_spy(evaluation, baseline))} excess return")
    for slug in _promotion_candidate_slugs():
        lines.append(f"- {_heading_label(slug)} vs {_heading_label(baseline)}: {_fmt_signed_pct(_cum_diff(evaluation, slug, baseline))} cumulative return difference")
    lines.append(f"- Chain health: {_chain_health_summary(evaluation=evaluation, delta=delta)}")
    if initializing:
        lines.append(
            f"- Operator conclusion: INITIALIZING; minimum valid days is {_fmt_int(min_valid)}. One valid day cannot establish a performance trend."
        )
    else:
        leader = _strategy_label("", best_cumulative[1]) if best_cumulative else "N/A"
        lines.append(f"- Operator conclusion: Decision-useful chain; {leader} leads by cumulative return.")
    return lines


def _performance_scoreboard_lines(evaluation: dict | None) -> list[str]:
    if evaluation is None:
        return ["Performance scoreboard unavailable: shadow_evaluation.json missing"]
    strategies = _evaluation_strategies(evaluation)
    lines = [
        "| Strategy | Data Status | Chain Status | Valid Days | Daily Return | Cumulative Return | Excess vs SPY | Alpha/Deployed Proxy | Vol Ann | Max Drawdown | Avg Turnover | Top-3 Conc. | Constituent Changes |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for slug in _scoreboard_slugs(evaluation):
        payload = strategies.get(slug) or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    _strategy_label(slug, payload),
                    str(payload.get("data_status") or "N/A"),
                    str(payload.get("status") or "N/A"),
                    _fmt_int(payload.get("rolling_count_of_valid_days")),
                    _fmt_pct(payload.get("daily_return")),
                    _fmt_pct(payload.get("cumulative_return")),
                    _fmt_pct(payload.get("excess_return_vs_spy")),
                    _fmt_pct(payload.get("alpha_per_dollar_deployed_proxy")),
                    _fmt_pct(payload.get("realized_volatility_ann")),
                    _fmt_pct(payload.get("max_drawdown")),
                    _fmt_decimal(payload.get("avg_turnover")),
                    _fmt_pct(payload.get("avg_top_3_concentration")),
                    _fmt_int(payload.get("constituent_change_count")),
                ]
            )
            + " |"
        )
    if BENCHMARK_SLUG not in strategies:
        lines.append("| SPY | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |")
        lines.append("")
        lines.append("SPY benchmark unavailable in evaluation artifact")
    return lines


def _relative_performance_lines(evaluation: dict | None) -> list[str]:
    if evaluation is None:
        return ["- Status: UNKNOWN - shadow_evaluation.json missing"]
    min_valid = _min_valid_days(evaluation)
    status = "INITIALIZING" if min_valid is None or min_valid < 2 else "DECISION_USEFUL"
    baseline = _baseline_strategy_slug()
    lines = [
        f"- Status: {status}",
        *[
            f"- {_heading_label(slug)} minus {_heading_label(baseline)} cumulative return: {_fmt_signed_pct(_cum_diff(evaluation, slug, baseline))}"
            for slug in _promotion_candidate_slugs()
        ],
        *[
            f"- {_heading_label(slug)} excess vs SPY: {_fmt_signed_pct(_excess_vs_spy(evaluation, slug))}"
            for slug in _model_strategy_slugs()
        ],
    ]
    if status == "INITIALIZING":
        lines.append("- Diagnosis: INITIALIZING; one valid day cannot establish performance trend.")
    return lines


def _chain_health_lines(*, evaluation: dict | None, delta: dict) -> list[str]:
    if evaluation is None:
        return [
            "- Minimum valid days: N/A",
            "- Any NO_DATA: UNKNOWN",
            "- Any BROKEN_CHAIN: UNKNOWN",
            f"- Delta status: {delta.get('status') or 'NO_PRIOR'}",
            "- Diagnosis: shadow_evaluation.json is missing; chain health cannot be trusted.",
        ]
    strategies = _model_metrics(evaluation)
    min_valid = _min_valid_days(evaluation)
    any_no_data = any(payload.get("data_status") == "NO_DATA" for payload in strategies.values())
    any_broken = any(payload.get("status") == "BROKEN_CHAIN" for payload in strategies.values())
    delta_status = delta.get("status") or "NO_PRIOR"
    return [
        f"- Minimum valid days: {_fmt_int(min_valid)}",
        f"- Any NO_DATA: {'YES' if any_no_data else 'NO'}",
        f"- Any BROKEN_CHAIN: {'YES' if any_broken else 'NO'}",
        f"- Delta status: {delta_status}",
        f"- Diagnosis: {_chain_health_diagnosis(evaluation=evaluation, delta=delta)}",
    ]


def _chain_health_summary(*, evaluation: dict | None, delta: dict) -> str:
    if evaluation is None:
        return f"UNKNOWN; delta status is {delta.get('status') or 'NO_PRIOR'}"
    min_valid = _min_valid_days(evaluation)
    strategies = _model_metrics(evaluation)
    any_no_data = any(payload.get("data_status") == "NO_DATA" for payload in strategies.values())
    any_broken = any(payload.get("status") == "BROKEN_CHAIN" for payload in strategies.values())
    delta_status = delta.get("status") or "NO_PRIOR"
    state = "BROKEN" if any_broken else "NO_DATA" if any_no_data else "OK"
    return f"{state}; minimum valid days={_fmt_int(min_valid)}; delta status={delta_status}"


def _chain_health_diagnosis(*, evaluation: dict, delta: dict) -> str:
    strategies = _model_metrics(evaluation)
    min_valid = _min_valid_days(evaluation)
    any_no_data = any(payload.get("data_status") == "NO_DATA" for payload in strategies.values())
    any_broken = any(payload.get("status") == "BROKEN_CHAIN" for payload in strategies.values())
    delta_status = delta.get("status") or "NO_PRIOR"
    if any_broken:
        return "BROKEN_CHAIN detected; do not trust cumulative shadow performance until prior artifacts are repaired."
    if any_no_data:
        return "NO_DATA detected; the price/signal panel did not support at least one model strategy for this date."
    if min_valid is None or min_valid < 2:
        if delta_status == "NO_PRIOR":
            return "INITIALIZING; current artifacts are valid, but prior-day snapshots are missing so trend and delta comparisons are not decision-useful yet."
        return "INITIALIZING; current artifacts are valid, but fewer than two valid days are available."
    if delta_status == "NO_PRIOR":
        return "Performance chain has multiple valid days, but day-over-day holdings delta lacks prior snapshots."
    if delta_status == "NO_DATA":
        return "Performance chain has data, but delta artifact reports NO_DATA for holdings comparison."
    return "Healthy; cumulative performance and day-over-day delta are available."


def _pairwise_lines(comparison: dict, left_slug: str, right_slug: str) -> list[str]:
    item = next(
        pair
        for pair in comparison["pairwise_overlap"]
        if pair["left_slug"] == left_slug and pair["right_slug"] == right_slug
        or pair["left_slug"] == right_slug and pair["right_slug"] == left_slug
    )
    left_unique = item["left_unique_names"] if item["left_slug"] == left_slug else item["right_unique_names"]
    right_unique = item["right_unique_names"] if item["right_slug"] == right_slug else item["left_unique_names"]
    return [
        f"- Overlap weight: {item['overlap_weight_pct']:.2%}",
        f"- {comparison['strategies'][left_slug]['strategy_name']} unique: {', '.join(left_unique) or 'None'}",
        f"- {comparison['strategies'][right_slug]['strategy_name']} unique: {', '.join(right_unique) or 'None'}",
    ]


def build_performance_artifacts(
    *,
    panel: pd.DataFrame,
    trade_date: str,
    start_date: str,
    output_root: Path,
    strategy_payloads: dict[str, dict],
    backtest_results: dict[str, dict],
    panel_meta: dict,
) -> tuple[pd.DataFrame, dict]:
    nav_frame = pd.DataFrame()
    summary = {
        "schema_version": "shadow_candidates_v1",
        "trade_date": trade_date,
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "data": panel_meta,
        "strategies": {},
    }
    for slug, result in backtest_results.items():
        nav = result["nav"].copy()
        nav["date"] = pd.to_datetime(nav["date"])
        nav = nav.rename(columns={"nav": slug})
        nav_frame = nav if nav_frame.empty else nav_frame.merge(nav, on="date", how="outer")
        summary["strategies"][slug] = {
            "strategy_name": strategy_payloads[slug]["strategy_name"],
            "summary": result["summary"],
        }

    spy = panel[panel["ticker"] == BENCHMARK_SYMBOL].copy()
    spy["date"] = pd.to_datetime(spy["date"])
    spy = spy[(spy["date"] >= pd.Timestamp(start_date)) & (spy["date"] <= pd.Timestamp(trade_date))].sort_values("date")
    spy["return"] = spy["close"].pct_change().shift(-1)
    spy = spy.iloc[:-1].copy()
    spy["spy_benchmark"] = (1.0 + spy["return"].fillna(0.0)).cumprod()
    spy_series = spy[["date", "spy_benchmark"]]
    nav_frame = spy_series if nav_frame.empty else nav_frame.merge(spy_series, on="date", how="outer")
    nav_frame = nav_frame.sort_values("date").reset_index(drop=True)
    nav_frame["date"] = nav_frame["date"].dt.strftime("%Y-%m-%d")

    if not spy.empty:
        returns = pd.Series(spy["return"].values, index=pd.to_datetime(spy["date"]), name="return")
        nav = pd.Series(spy["spy_benchmark"].values, index=pd.to_datetime(spy["date"]), name="nav")
        summary["strategies"][BENCHMARK_SLUG] = {
            "strategy_name": "SPY",
            "summary": summarise_performance(nav, returns=returns, benchmark_returns=None, label="SPY"),
        }
    return nav_frame, summary


def build_shadow_performance_payload(
    *,
    panel: pd.DataFrame,
    output_root: Path,
    trade_date: str,
    previous_trade_date: str | None,
    strategy_payloads: dict[str, dict],
    data_status: str,
    data_reason: str | None = None,
) -> dict:
    prior_chain_state, prior_payload = load_prior_shadow_performance(
        output_root=output_root,
        previous_trade_date=previous_trade_date,
    )
    prior_navs = (prior_payload or {}).get("strategies") or {}
    prior_reason_code = (prior_payload or {}).get("reason_code")
    returns_by_ticker = compute_returns_for_trade_date(panel=panel, trade_date=trade_date)
    chain_state = prior_chain_state
    if chain_state == "BROKEN_CHAIN":
        print(f"[SHADOW] broken performance chain at prior date={previous_trade_date}")

    strategies = {}
    for slug in _model_strategy_slugs(trade_date=trade_date):
        prior_strategy = prior_navs.get(slug) or {}
        prev_nav_raw = prior_strategy.get("nav")
        if prev_nav_raw is None and chain_state in {"NO_PRIOR", "OK"}:
            prev_nav_raw = 1.0
        prev_nav = float(prev_nav_raw) if prev_nav_raw is not None else None
        if chain_state == "BROKEN_CHAIN":
            daily_return = 0.0 if data_status == "NO_DATA" else round(
                float(pd.Series((strategy_payloads.get(slug) or {}).get("target_weights") or {}, dtype=float).mul(pd.Series(returns_by_ticker), fill_value=0.0).sum()),
                10,
            )
            nav = None
            weight_count = int(len((strategy_payloads.get(slug) or {}).get("target_weights") or {})) if data_status != "NO_DATA" else 0
        elif data_status == "NO_DATA":
            daily_return = 0.0
            nav = prev_nav
            weight_count = 0
        else:
            weights = pd.Series((strategy_payloads.get(slug) or {}).get("target_weights") or {}, dtype=float)
            daily_return = round(float(weights.mul(pd.Series(returns_by_ticker), fill_value=0.0).sum()), 10)
            nav = round(float(prev_nav * (1.0 + daily_return)), 10)
            weight_count = int(len(weights))
        strategies[slug] = {
            "strategy_name": (strategy_payloads.get(slug) or {}).get(
                "strategy_name",
                _strategy_label(slug),
            ),
            "daily_return": daily_return,
            "nav": nav,
            "previous_nav": prev_nav,
            "weights_count": weight_count,
        }

    if chain_state == "BROKEN_CHAIN":
        spy_prev_nav = None
        spy_return = round(float(returns_by_ticker.get(BENCHMARK_SYMBOL, 0.0)), 10) if data_status != "NO_DATA" else 0.0
        spy_nav = None
    else:
        spy_prev_nav = float((prior_navs.get(BENCHMARK_SLUG) or {}).get("nav", 1.0))
        spy_return = round(float(returns_by_ticker.get(BENCHMARK_SYMBOL, 0.0)), 10) if data_status != "NO_DATA" else 0.0
        spy_nav = round(float(spy_prev_nav * (1.0 + spy_return)), 10) if data_status != "NO_DATA" else spy_prev_nav
    strategies[BENCHMARK_SLUG] = {
        "strategy_name": "SPY",
        "daily_return": spy_return,
        "nav": spy_nav,
        "previous_nav": spy_prev_nav,
        "weights_count": 1,
    }

    return {
        "trade_date": trade_date,
        "previous_trade_date": previous_trade_date,
        "status": chain_state,
        "reason_code": prior_reason_code,
        "data_status": data_status,
        "data_reason": data_reason,
        "return_convention": "weights_as_of_t",
        "strategies": strategies,
    }


def load_prior_shadow_performance(*, output_root: Path, previous_trade_date: str | None) -> tuple[str, dict | None]:
    if not previous_trade_date:
        return "NO_PRIOR", None
    previous_dir = output_root / previous_trade_date
    if not previous_dir.exists():
        if _nav_series_has_established_history(output_root):
            return "BROKEN_CHAIN", {
                "reason_code": "SHADOW_PRIOR_ARTIFACT_MISSING",
                "missing_prior_date": previous_trade_date,
            }
        return "NO_PRIOR", None
    path = previous_dir / "shadow_performance.json"
    if not path.exists():
        return "BROKEN_CHAIN", {
            "reason_code": "SHADOW_PRIOR_ARTIFACT_MISSING",
            "missing_prior_date": previous_trade_date,
            "missing_prior_path": str(path),
        }
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return "BROKEN_CHAIN", None
    if payload.get("status") == "BROKEN_CHAIN":
        return "BROKEN_CHAIN", None
    strategies = (payload.get("strategies") or {})
    if any(v.get("nav") is None for v in strategies.values() if isinstance(v, dict)):
        return "BROKEN_CHAIN", None
    return "OK", payload


def _nav_series_has_established_history(output_root: Path) -> bool:
    path = output_root / "performance" / "shadow_nav_series.csv"
    if not path.exists():
        return False
    try:
        import csv

        with path.open(newline="", encoding="utf-8") as handle:
            return any(bool(row.get("date")) for row in csv.DictReader(handle))
    except Exception:
        return False


def build_shadow_evaluation_payload(*, output_root: Path, trade_date: str) -> dict:
    dated_dir = output_root / trade_date
    current_performance = safe_read_json(dated_dir / "shadow_performance.json") or {}
    history_dates = list_shadow_date_dirs(output_root=output_root, trade_date=trade_date)
    performance_history = [
        (date, safe_read_json(output_root / date / "shadow_performance.json"))
        for date in history_dates
    ]

    evaluation = {
        "trade_date": trade_date,
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "strategies": {},
    }
    strategy_names = {
        slug: _strategy_label(slug)
        for slug in (*_model_strategy_slugs(trade_date=trade_date), BENCHMARK_SLUG)
    }
    spy_current = ((current_performance.get("strategies") or {}).get(BENCHMARK_SLUG) or {})
    spy_cumulative_return = (
        round(float(spy_current["nav"]) - 1.0, 10)
        if spy_current.get("nav") is not None
        else None
    )
    for slug, strategy_name in strategy_names.items():
        current = ((current_performance.get("strategies") or {}).get(slug) or {})
        chain_state = current_performance.get("status")
        data_status = current_performance.get("data_status")
        data_reason = current_performance.get("data_reason")
        return_convention = current_performance.get("return_convention")
        nav = current.get("nav")
        cumulative_return = round(float(nav) - 1.0, 10) if nav is not None else None
        excess_return_vs_spy = (
            round(cumulative_return - spy_cumulative_return, 10)
            if slug != BENCHMARK_SLUG and cumulative_return is not None and spy_cumulative_return is not None
            else 0.0
            if slug == BENCHMARK_SLUG and spy_cumulative_return is not None
            else None
        )
        valid_daily_returns = extract_valid_daily_returns(performance_history=performance_history, strategy_slug=slug)
        nav_history = extract_chain_nav_history(performance_history=performance_history, strategy_slug=slug)
        turnover_history = extract_strategy_metric_history(output_root=output_root, history_dates=history_dates, strategy_slug=slug, field="expected_turnover")
        concentration_history = extract_strategy_top3_concentration_history(output_root=output_root, history_dates=history_dates, strategy_slug=slug)
        cash_history = extract_strategy_concentration_field_history(output_root=output_root, history_dates=history_dates, strategy_slug=slug, field="cash_weight")
        hhi_history = extract_strategy_concentration_field_history(output_root=output_root, history_dates=history_dates, strategy_slug=slug, field="hhi")
        effective_n_history = extract_strategy_concentration_field_history(output_root=output_root, history_dates=history_dates, strategy_slug=slug, field="effective_n")
        constituent_change_count = extract_constituent_change_count(output_root=output_root, history_dates=history_dates, strategy_slug=slug)
        avg_cash_weight = round(sum(cash_history) / len(cash_history), 10) if cash_history else None
        evaluation["strategies"][slug] = {
            "strategy_name": strategy_name,
            "status": chain_state,
            "data_status": data_status,
            "data_reason": data_reason,
            "return_convention": return_convention,
            "daily_return": current.get("daily_return"),
            "nav": nav,
            "cumulative_return": cumulative_return,
            "excess_return_vs_spy": excess_return_vs_spy,
            "rolling_count_of_valid_days": len(valid_daily_returns),
            "realized_volatility_ann": compute_realized_volatility_ann(valid_daily_returns),
            "max_drawdown": compute_max_drawdown(nav_history),
            "avg_turnover": round(sum(turnover_history) / len(turnover_history), 10) if turnover_history else None,
            "avg_top_3_concentration": round(sum(concentration_history) / len(concentration_history), 10) if concentration_history else None,
            "avg_cash_weight": avg_cash_weight,
            "avg_hhi": round(sum(hhi_history) / len(hhi_history), 10) if hhi_history else None,
            "avg_effective_n": round(sum(effective_n_history) / len(effective_n_history), 10) if effective_n_history else None,
            "alpha_per_dollar_deployed_proxy": _alpha_per_dollar_deployed_proxy(excess_return_vs_spy, avg_cash_weight, None),
            "constituent_change_count": constituent_change_count,
        }
    return evaluation


def write_phase_c_promotion_artifacts(*, output_root: Path, trade_date: str, shadow_evaluation: dict) -> dict:
    dated_dir = output_root / trade_date
    longitudinal = build_longitudinal_metrics_payload(output_root=output_root, trade_date=trade_date, shadow_evaluation=shadow_evaluation)
    stability = build_stability_surface_payload(longitudinal)
    readiness = build_phase_c_promotion_readiness_payload(longitudinal=longitudinal, stability=stability)
    dated_dir.mkdir(parents=True, exist_ok=True)
    (dated_dir / "longitudinal_metrics.json").write_text(json.dumps(longitudinal, indent=2, sort_keys=True))
    (dated_dir / "stability_surface.json").write_text(json.dumps(stability, indent=2, sort_keys=True))
    (dated_dir / "promotion_readiness.json").write_text(json.dumps(readiness, indent=2, sort_keys=True))
    (dated_dir / "promotion_readiness.md").write_text(build_phase_c_promotion_markdown(readiness))
    return readiness


def build_longitudinal_metrics_payload(*, output_root: Path, trade_date: str, shadow_evaluation: dict) -> dict:
    history_dates = list_shadow_date_dirs(output_root=output_root, trade_date=trade_date)
    performance_history = [
        (date, safe_read_json(output_root / date / "shadow_performance.json"))
        for date in history_dates
    ]
    strategies = {}
    for slug in (*_model_strategy_slugs(trade_date=trade_date), BENCHMARK_SLUG):
        evaluation = (shadow_evaluation.get("strategies") or {}).get(slug) or {}
        daily_returns = extract_valid_daily_returns(performance_history=performance_history, strategy_slug=slug)
        nav_history = extract_chain_nav_history(performance_history=performance_history, strategy_slug=slug)
        turnover_history = extract_strategy_metric_history(output_root=output_root, history_dates=history_dates, strategy_slug=slug, field="expected_turnover")
        top3_history = extract_strategy_top3_concentration_history(output_root=output_root, history_dates=history_dates, strategy_slug=slug)
        top5_history = extract_strategy_top_concentration_history(output_root=output_root, history_dates=history_dates, strategy_slug=slug, top_n=5)
        avg_position_history = extract_strategy_average_position_size_history(output_root=output_root, history_dates=history_dates, strategy_slug=slug)
        cash_history = extract_strategy_concentration_field_history(output_root=output_root, history_dates=history_dates, strategy_slug=slug, field="cash_weight")
        hhi_history = extract_strategy_concentration_field_history(output_root=output_root, history_dates=history_dates, strategy_slug=slug, field="hhi")
        effective_n_history = extract_strategy_concentration_field_history(output_root=output_root, history_dates=history_dates, strategy_slug=slug, field="effective_n")
        constituent_change_count = extract_constituent_change_count(output_root=output_root, history_dates=history_dates, strategy_slug=slug)
        strategy_returns = {f"{window}D": _rolling_sum(daily_returns, window) for window in (5, 10, 20)}
        strategy_returns["cumulative"] = evaluation.get("cumulative_return")
        strategies[slug] = {
            "strategy_name": evaluation.get("strategy_name") or _strategy_label(slug),
            "role": _strategy_role(slug),
            "data_status": evaluation.get("data_status"),
            "chain_status": evaluation.get("status"),
            "rolling_returns": strategy_returns,
            "rolling_excess_return": {
                "vs_polaris": {},
                "vs_spy": {},
            },
            "risk_metrics": {
                "realized_volatility_ann": evaluation.get("realized_volatility_ann"),
                "max_drawdown": evaluation.get("max_drawdown"),
                "downside_volatility_proxy": compute_downside_volatility(daily_returns),
                "drawdown_recovery_speed_days": compute_drawdown_recovery_speed(nav_history),
            },
            "operational_metrics": {
                "avg_turnover": _mean_or_none(turnover_history),
                "constituent_change_count": constituent_change_count,
                "avg_top_3_concentration": _mean_or_none(top3_history),
                "avg_top_5_concentration": _mean_or_none(top5_history),
                "avg_position_size": _mean_or_none(avg_position_history),
                "avg_cash_weight": _mean_or_none(cash_history),
                "avg_hhi": _mean_or_none(hhi_history),
                "avg_effective_n": _mean_or_none(effective_n_history),
                "alpha_per_dollar_deployed_proxy": evaluation.get("alpha_per_dollar_deployed_proxy"),
            },
            "valid_observation_windows": len(daily_returns),
            "available_observation_windows": len(history_dates),
        }
    for slug, payload in strategies.items():
        for window in ("5", "10", "20", "cumulative"):
            key = window if window == "cumulative" else f"{window}D"
            left = payload["rolling_returns"].get(key)
            baseline = strategies.get(_baseline_strategy_slug(), {}).get("rolling_returns", {}).get(key)
            spy = strategies.get(BENCHMARK_SLUG, {}).get("rolling_returns", {}).get(key)
            payload["rolling_excess_return"]["vs_polaris"][f"{window}D" if window != "cumulative" else "cumulative"] = _diff_or_none(left, baseline)
            payload["rolling_excess_return"]["vs_spy"][f"{window}D" if window != "cumulative" else "cumulative"] = _diff_or_none(left, spy)
    return {
        "schema_version": "fr_028_phase_c_longitudinal_metrics_v1",
        "trade_date": trade_date,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "return_convention": "weights_as_of_t",
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "history_dates": history_dates,
        "strategies": strategies,
    }


def build_stability_surface_payload(longitudinal: dict) -> dict:
    expected = max(1, len(longitudinal.get("history_dates") or []))
    strategies = {}
    for slug, payload in (longitudinal.get("strategies") or {}).items():
        valid = int(payload.get("valid_observation_windows") or 0)
        continuity = round(valid / expected, 6)
        missing_penalty = round(1.0 - continuity, 6)
        turnover = _as_float((payload.get("operational_metrics") or {}).get("avg_turnover"))
        concentration = _as_float((payload.get("operational_metrics") or {}).get("avg_top_3_concentration"))
        drawdown = _as_float((payload.get("risk_metrics") or {}).get("max_drawdown"))
        penalties = missing_penalty * 45.0
        if turnover is not None and turnover > 0.50:
            penalties += min(20.0, (turnover - 0.50) * 40.0)
        if concentration is not None and concentration > 0.60:
            penalties += min(20.0, (concentration - 0.60) * 50.0)
        if drawdown is not None and drawdown < -0.10:
            penalties += min(25.0, abs(drawdown + 0.10) * 100.0)
        stability_score = round(max(0.0, 100.0 - penalties), 2)
        strategies[slug] = {
            "strategy_name": payload.get("strategy_name"),
            "role": payload.get("role"),
            "valid_observation_windows": valid,
            "expected_observation_windows": expected,
            "continuity_score": continuity,
            "missing_data_penalty": missing_penalty,
            "stability_score": stability_score,
            "reason_codes": stability_reason_codes(valid=valid, expected=expected, turnover=turnover, concentration=concentration, drawdown=drawdown, stability_score=stability_score),
        }
    return {
        "schema_version": "fr_028_phase_c_stability_surface_v1",
        "trade_date": longitudinal.get("trade_date"),
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "strategies": strategies,
    }


def build_phase_c_promotion_readiness_payload(*, longitudinal: dict, stability: dict) -> dict:
    strategies = {}
    current_leader = None
    leader_score = None
    for slug, payload in (longitudinal.get("strategies") or {}).items():
        if payload.get("role") != "CHALLENGER":
            continue
        stable = ((stability.get("strategies") or {}).get(slug) or {})
        readiness = classify_phase_c_readiness(strategy=payload, stability=stable)
        strategies[slug] = readiness
        score = _as_float((payload.get("rolling_excess_return") or {}).get("vs_polaris", {}).get("cumulative"))
        if score is not None and (leader_score is None or score > leader_score):
            leader_score = score
            current_leader = slug
    return {
        "schema_version": "fr_028_phase_c_promotion_readiness_v1",
        "trade_date": longitudinal.get("trade_date"),
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "active_baseline": _baseline_strategy_slug(),
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "current_leader": current_leader if leader_score is not None and leader_score > 0 else None,
        "leader_evidence": {
            "cumulative_excess_vs_polaris": leader_score,
            "threshold": "> 0 with sufficient stability/history for readiness",
        },
        "readiness_state_order": ["NOT_READY", "OBSERVE", "CONTINUE_SHADOW", "EMERGING_CANDIDATE", "CANDIDATE_FOR_CAPITAL"],
        "strategies": strategies,
        "non_goals": [
            "no automatic promotion",
            "no strategy selection changes",
            "no capital allocation changes",
            "no broker or execution behavior changes",
        ],
    }


def classify_phase_c_readiness(*, strategy: dict, stability: dict) -> dict:
    valid = int(strategy.get("valid_observation_windows") or 0)
    stability_score = _as_float(stability.get("stability_score")) or 0.0
    cumulative_vs_polaris = _as_float((strategy.get("rolling_excess_return") or {}).get("vs_polaris", {}).get("cumulative"))
    cumulative_vs_spy = _as_float((strategy.get("rolling_excess_return") or {}).get("vs_spy", {}).get("cumulative"))
    turnover = _as_float((strategy.get("operational_metrics") or {}).get("avg_turnover"))
    concentration = _as_float((strategy.get("operational_metrics") or {}).get("avg_top_3_concentration"))
    drawdown = _as_float((strategy.get("risk_metrics") or {}).get("max_drawdown"))
    reason_codes = list(stability.get("reason_codes") or [])
    if valid < 5:
        reason_codes.append("insufficient_history")
        return _readiness_record(strategy, stability, "OBSERVE", "LOW", reason_codes)
    if valid < 20:
        reason_codes.append("insufficient_history")
        return _readiness_record(strategy, stability, "CONTINUE_SHADOW", "LOW", reason_codes)
    if cumulative_vs_polaris is None or cumulative_vs_polaris <= 0 or cumulative_vs_spy is None or cumulative_vs_spy <= 0:
        reason_codes.append("insufficient_excess_return")
    if turnover is not None and turnover > 0.50:
        reason_codes.append("excessive_turnover")
    if concentration is not None and concentration > 0.60:
        reason_codes.append("concentration_risk")
    if drawdown is not None and drawdown < -0.10:
        reason_codes.append("drawdown_risk")
    if stability_score < 70:
        reason_codes.append("unstable_performance")
    reason_codes = sorted(set(reason_codes))
    blocking = [code for code in reason_codes if code not in {"healthy_progression"}]
    if not blocking and valid >= 60 and stability_score >= 85:
        return _readiness_record(strategy, stability, "CANDIDATE_FOR_CAPITAL", "HIGH", ["healthy_progression"])
    if not blocking:
        return _readiness_record(strategy, stability, "EMERGING_CANDIDATE", "MODERATE", ["healthy_progression"])
    return _readiness_record(strategy, stability, "CONTINUE_SHADOW", "MODERATE" if valid >= 20 else "LOW", reason_codes)


def _readiness_record(strategy: dict, stability: dict, state: str, confidence: str, reason_codes: list[str]) -> dict:
    return {
        "strategy_name": strategy.get("strategy_name"),
        "readiness_state": state,
        "confidence": confidence,
        "reason_codes": sorted(set(reason_codes)),
        "valid_observation_windows": strategy.get("valid_observation_windows"),
        "stability_score": stability.get("stability_score"),
        "cumulative_excess_vs_polaris": (strategy.get("rolling_excess_return") or {}).get("vs_polaris", {}).get("cumulative"),
        "cumulative_excess_vs_spy": (strategy.get("rolling_excess_return") or {}).get("vs_spy", {}).get("cumulative"),
        "max_drawdown": (strategy.get("risk_metrics") or {}).get("max_drawdown"),
        "avg_turnover": (strategy.get("operational_metrics") or {}).get("avg_turnover"),
        "avg_top_3_concentration": (strategy.get("operational_metrics") or {}).get("avg_top_3_concentration"),
    }


def build_phase_c_promotion_markdown(payload: dict) -> str:
    lines = [
        "# FR-028 Phase C Promotion Readiness",
        "",
        f"- Trade date: `{payload.get('trade_date')}`",
        f"- Governance label: `{payload.get('governance_label')}`",
        f"- Execution impact: `{payload.get('execution_impact')}`",
        f"- Current leader: `{payload.get('current_leader') or 'insufficient evidence'}`",
        "",
        "## Challenger Readiness",
        "",
        "| Strategy | State | Confidence | Valid Windows | Stability | Excess vs Polaris | Excess vs SPY | Reason Codes |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for slug, strategy in sorted((payload.get("strategies") or {}).items()):
        lines.append(
            "| "
            + " | ".join(
                [
                    strategy.get("strategy_name") or slug,
                    str(strategy.get("readiness_state")),
                    str(strategy.get("confidence")),
                    str(strategy.get("valid_observation_windows")),
                    str(strategy.get("stability_score")),
                    _fmt_pct(strategy.get("cumulative_excess_vs_polaris")),
                    _fmt_pct(strategy.get("cumulative_excess_vs_spy")),
                    ", ".join(strategy.get("reason_codes") or []),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Non-Goals"])
    lines.extend(f"- {item}" for item in payload.get("non_goals") or [])
    return "\n".join(lines) + "\n"


def stability_reason_codes(*, valid: int, expected: int, turnover: float | None, concentration: float | None, drawdown: float | None, stability_score: float) -> list[str]:
    reason_codes = []
    if valid < expected:
        reason_codes.append("missing_data_penalty")
    if turnover is not None and turnover > 0.50:
        reason_codes.append("excessive_turnover")
    if concentration is not None and concentration > 0.60:
        reason_codes.append("concentration_risk")
    if drawdown is not None and drawdown < -0.10:
        reason_codes.append("drawdown_risk")
    if stability_score < 70:
        reason_codes.append("unstable_performance")
    if not reason_codes:
        reason_codes.append("healthy_progression")
    return reason_codes


def list_shadow_date_dirs(*, output_root: Path, trade_date: str) -> list[str]:
    candidates = []
    for child in output_root.iterdir() if output_root.exists() else []:
        if not child.is_dir():
            continue
        try:
            normalized = pd.Timestamp(child.name).strftime("%Y-%m-%d")
        except Exception:
            continue
        if normalized == child.name and child.name <= trade_date:
            candidates.append(child.name)
    return sorted(candidates)


def safe_read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def extract_valid_daily_returns(*, performance_history: list[tuple[str, dict | None]], strategy_slug: str) -> list[float]:
    returns: list[float] = []
    seen_strategy = False
    for _, payload in performance_history:
        if not payload:
            if seen_strategy:
                break
            continue
        if payload.get("status") == "BROKEN_CHAIN":
            return []
        if payload.get("data_status") != "OK":
            continue
        strategy = ((payload.get("strategies") or {}).get(strategy_slug) or {})
        if not strategy:
            if seen_strategy:
                return []
            continue
        if strategy.get("nav") is None:
            if seen_strategy:
                return []
            continue
        seen_strategy = True
        returns.append(float(strategy.get("daily_return") or 0.0))
    return returns


def extract_chain_nav_history(*, performance_history: list[tuple[str, dict | None]], strategy_slug: str) -> list[float] | None:
    navs: list[float] = []
    seen_strategy = False
    for _, payload in performance_history:
        if not payload:
            if seen_strategy:
                return None
            continue
        if payload.get("status") == "BROKEN_CHAIN":
            return None
        strategy = ((payload.get("strategies") or {}).get(strategy_slug) or {})
        if not strategy:
            if seen_strategy:
                return None
            continue
        nav = strategy.get("nav")
        if nav is None:
            if seen_strategy:
                return None
            continue
        seen_strategy = True
        navs.append(float(nav))
    return navs


def extract_strategy_metric_history(*, output_root: Path, history_dates: list[str], strategy_slug: str, field: str) -> list[float]:
    values = []
    for date in history_dates:
        payload = safe_read_json(output_root / date / f"{strategy_slug}.json")
        if not payload:
            continue
        value = payload.get(field)
        if value is not None:
            values.append(float(value))
    return values


def extract_strategy_top3_concentration_history(*, output_root: Path, history_dates: list[str], strategy_slug: str) -> list[float]:
    values = []
    for date in history_dates:
        payload = safe_read_json(output_root / date / f"{strategy_slug}.json")
        if not payload:
            continue
        value = ((payload.get("weight_concentration") or {}).get("top3_concentration"))
        if value is not None:
            values.append(float(value))
    return values


def extract_strategy_concentration_field_history(*, output_root: Path, history_dates: list[str], strategy_slug: str, field: str) -> list[float]:
    values = []
    for date in history_dates:
        payload = safe_read_json(output_root / date / f"{strategy_slug}.json")
        if not payload:
            continue
        value = ((payload.get("weight_concentration") or {}).get(field))
        if value is not None:
            values.append(float(value))
    return values


def extract_strategy_top_concentration_history(*, output_root: Path, history_dates: list[str], strategy_slug: str, top_n: int) -> list[float]:
    values = []
    for date in history_dates:
        payload = safe_read_json(output_root / date / f"{strategy_slug}.json")
        if not payload:
            continue
        holdings = payload.get("holdings") or []
        weights = sorted(
            [
                float(item.get("target_weight"))
                for item in holdings
                if isinstance(item, dict) and item.get("target_weight") is not None
            ],
            reverse=True,
        )
        if weights:
            values.append(round(float(sum(weights[:top_n])), 10))
    return values


def extract_strategy_average_position_size_history(*, output_root: Path, history_dates: list[str], strategy_slug: str) -> list[float]:
    values = []
    for date in history_dates:
        payload = safe_read_json(output_root / date / f"{strategy_slug}.json")
        if not payload:
            continue
        holdings = payload.get("holdings") or []
        weights = [
            float(item.get("target_weight"))
            for item in holdings
            if isinstance(item, dict) and item.get("target_weight") is not None
        ]
        if weights:
            values.append(round(float(sum(weights) / len(weights)), 10))
    return values


def extract_constituent_change_count(*, output_root: Path, history_dates: list[str], strategy_slug: str) -> int | None:
    total = 0
    found = False
    for date in history_dates:
        delta_payload = safe_read_json(output_root / date / "delta.json")
        if not delta_payload or delta_payload.get("status") != "OK":
            continue
        strategy = ((delta_payload.get("strategies") or {}).get(strategy_slug) or {})
        if not strategy:
            continue
        total += len(strategy.get("adds") or []) + len(strategy.get("removes") or [])
        found = True
    return total if found else None


def compute_realized_volatility_ann(daily_returns: list[float]) -> float | None:
    if len(daily_returns) < 2:
        return None
    return round(float(pd.Series(daily_returns, dtype=float).std(ddof=1) * (252.0 ** 0.5)), 10)


def compute_downside_volatility(daily_returns: list[float]) -> float | None:
    downside = [value for value in daily_returns if value < 0]
    if len(downside) < 2:
        return None
    return round(float(pd.Series(downside, dtype=float).std(ddof=1) * (252.0 ** 0.5)), 10)


def compute_max_drawdown(nav_history: list[float] | None) -> float | None:
    if not nav_history:
        return None
    nav = pd.Series(nav_history, dtype=float)
    drawdown = nav / nav.cummax() - 1.0
    return round(float(drawdown.min()), 10)


def compute_drawdown_recovery_speed(nav_history: list[float] | None) -> int | None:
    if not nav_history:
        return None
    peak = nav_history[0]
    days_since_drawdown = 0
    max_recovery = 0
    in_drawdown = False
    for nav in nav_history:
        if nav >= peak:
            peak = nav
            if in_drawdown:
                max_recovery = max(max_recovery, days_since_drawdown)
            in_drawdown = False
            days_since_drawdown = 0
        else:
            in_drawdown = True
            days_since_drawdown += 1
    if in_drawdown:
        max_recovery = max(max_recovery, days_since_drawdown)
    return max_recovery


def _rolling_sum(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return round(float(sum(values[-window:])), 10)


def _mean_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return round(float(sum(values) / len(values)), 10)


def _diff_or_none(left: object, right: object) -> float | None:
    left_number = _as_float(left)
    right_number = _as_float(right)
    if left_number is None or right_number is None:
        return None
    return round(left_number - right_number, 10)


def compute_returns_for_trade_date(*, panel: pd.DataFrame, trade_date: str) -> dict[str, float]:
    frame = panel.copy()
    if frame.empty or "date" not in frame.columns or "ticker" not in frame.columns or "close" not in frame.columns:
        return {}
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values(["ticker", "date"])
    frame["daily_return"] = frame.groupby("ticker")["close"].pct_change()
    current = frame[frame["date"] == pd.Timestamp(trade_date)].copy()
    current["ticker"] = current["ticker"].astype(str)
    return {
        str(ticker): round(float(daily_return), 10)
        for ticker, daily_return in zip(current["ticker"], current["daily_return"].fillna(0.0))
    }


def build_delta_payload(
    *,
    output_root: Path,
    trade_date: str,
    previous_trade_date: str | None,
    strategy_payloads: dict[str, dict],
) -> dict:
    if previous_trade_date is None:
        return {
            "trade_date": trade_date,
            "previous_date": None,
            "status": "NO_PRIOR",
            "strategies": {},
        }

    previous_dir = output_root / previous_trade_date
    strategies = {}
    for slug in _model_strategy_slugs(trade_date=trade_date):
        previous_path = previous_dir / f"{slug}.json"
        if not previous_path.exists():
            print(f"[SHADOW] warning: prior snapshot missing for {slug} on {previous_trade_date}")
            return {
                "trade_date": trade_date,
                "previous_date": previous_trade_date,
                "status": "NO_PRIOR",
                "strategies": {},
            }
        try:
            previous_payload = json.loads(previous_path.read_text())
        except Exception as exc:
            print(f"[SHADOW] warning: prior snapshot unreadable for {slug} on {previous_trade_date}: {exc}")
            return {
                "trade_date": trade_date,
                "previous_date": previous_trade_date,
                "status": "NO_PRIOR",
                "strategies": {},
            }
        strategies[slug] = compute_strategy_delta(previous_payload, strategy_payloads[slug])

    return {
        "trade_date": trade_date,
        "previous_date": previous_trade_date,
        "status": "OK",
        "strategies": strategies,
    }


def find_previous_shadow_date(output_root: Path, *, trade_date: str) -> str | None:
    candidates = []
    for child in output_root.iterdir() if output_root.exists() else []:
        if not child.is_dir():
            continue
        try:
            normalized = pd.Timestamp(child.name).strftime("%Y-%m-%d")
        except Exception:
            continue
        if normalized == child.name and child.name < trade_date:
            candidates.append(child.name)
    return max(candidates) if candidates else None


def compute_strategy_delta(previous_payload: dict, current_payload: dict) -> dict:
    prev_weights = pd.Series(previous_payload.get("target_weights") or {}, dtype=float)
    curr_weights = pd.Series(current_payload.get("target_weights") or {}, dtype=float)
    prev_set = set(prev_weights.index)
    curr_set = set(curr_weights.index)
    added = sorted(curr_set - prev_set)
    removed = sorted(prev_set - curr_set)
    unchanged = sorted(prev_set & curr_set)

    weight_changes = {}
    increases = []
    decreases = []
    for ticker in unchanged:
        delta = round(float(curr_weights[ticker] - prev_weights[ticker]), 6)
        weight_changes[str(ticker)] = delta
        if delta > 0:
            increases.append({"ticker": str(ticker), "delta_weight": delta})
        elif delta < 0:
            decreases.append({"ticker": str(ticker), "delta_weight": delta})

    increases.sort(key=lambda item: item["delta_weight"], reverse=True)
    decreases.sort(key=lambda item: item["delta_weight"])
    all_names = prev_weights.index.union(curr_weights.index)
    turnover_proxy = round(
        float(
            (
                curr_weights.reindex(all_names, fill_value=0.0)
                - prev_weights.reindex(all_names, fill_value=0.0)
            ).abs().sum()
        ),
        6,
    )
    rotated_names = len(added) + len(removed)
    summary_name = _delta_summary_label(current_payload)
    summary = (
        f"{summary_name} stable"
        if rotated_names == 0 and not increases and not decreases
        else f"{summary_name} rotated {rotated_names} names"
    )
    return {
        "strategy_name": current_payload.get("strategy_name"),
        "strategy_slug": current_payload.get("strategy_slug"),
        "adds": added,
        "removes": removed,
        "unchanged": unchanged,
        "weight_changes": weight_changes,
        "increases": increases,
        "decreases": decreases,
        "summary_metrics": {
            "num_adds": len(added),
            "num_removals": len(removed),
            "num_unchanged": len(unchanged),
            "turnover_proxy": turnover_proxy,
        },
        "summary": summary,
    }


def _delta_summary_label(current_payload: dict) -> str:
    slug = str(current_payload.get("strategy_slug") or "")
    entry = load_strategy_registry().get(slug)
    raw = entry.raw if entry else {}
    return str((raw or {}).get("delta_summary_name") or current_payload.get("strategy_name") or _strategy_label(slug))


def _delta_markdown_lines(delta_payload: dict | None) -> list[str]:
    if not delta_payload:
        return ["No prior day available for comparison."]
    if delta_payload.get("status") == "NO_DATA":
        previous = delta_payload.get("previous_date")
        return [
            f"- Previous trading day: {previous}" if previous else "- Previous trading day: None",
            "- No market data available for this trade date.",
            "- Current trade date has no market data, so no holdings delta was computed.",
        ]
    if delta_payload.get("status") == "NO_PRIOR":
        previous = delta_payload.get("previous_date")
        return [
            f"- Previous trading day: {previous}" if previous else "- Previous trading day: None",
            "- No prior day available for comparison.",
        ]
    lines = [f"- Previous available day: {delta_payload.get('previous_date')}"]
    for slug in _model_strategy_slugs():
        item = (delta_payload.get("strategies") or {}).get(slug) or {}
        lines.extend(
            [
                "",
                f"### {item.get('strategy_name') or slug}",
                f"- Adds: {', '.join(item.get('adds') or []) or 'None'}",
                f"- Removes: {', '.join(item.get('removes') or []) or 'None'}",
                f"- Increases: {_format_weight_moves(item.get('increases') or [], top_n=3)}",
                f"- Decreases: {_format_weight_moves(item.get('decreases') or [], top_n=3)}",
                f"- Summary: {item.get('summary') or 'No change summary available'}",
            ]
        )
    return lines


def _format_weight_moves(items: list[dict], *, top_n: int) -> str:
    if not items:
        return "None"
    sliced = items[:top_n]
    return ", ".join(f"{item['ticker']} ({item['delta_weight']:+.2%})" for item in sliced)


def _load_broker_context(strategy_payloads: dict[str, dict]) -> dict:
    positions_path = Path("outputs/broker/posttrade_positions.json")
    snapshot_path = Path("outputs/broker/broker_snapshot_latest.json")
    if not positions_path.exists():
        return {}
    try:
        positions_payload = json.loads(positions_path.read_text())
        snapshot_payload = json.loads(snapshot_path.read_text()) if snapshot_path.exists() else {}
    except Exception:
        return {}

    broker_names = sorted(
        str(item.get("symbol") or "").upper()
        for item in positions_payload.get("positions") or []
        if str(item.get("symbol") or "").strip()
    )
    broker_set = set(broker_names)
    strategy_overlap = {}
    for slug, payload in strategy_payloads.items():
        model_set = {str(item["ticker"]).upper() for item in payload.get("holdings") or []}
        strategy_overlap[slug] = {
            "overlap_names_count": int(len(model_set & broker_set)),
            "shared_names": sorted(model_set & broker_set),
            "broker_only_names": sorted(broker_set - model_set),
            "model_only_names": sorted(model_set - broker_set),
        }
    return {
        "informational_only": True,
        "source": "outputs/broker/posttrade_positions.json",
        "as_of": snapshot_payload.get("as_of") or positions_payload.get("captured_at"),
        "trade_date": snapshot_payload.get("trade_date"),
        "positions_count": int(len(broker_names)),
        "broker_holdings": broker_names,
        "strategy_overlap": strategy_overlap,
    }


if __name__ == "__main__":
    raise SystemExit(main())
