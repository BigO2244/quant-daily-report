from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

from core.research.timing_fill_model import signed_shares
from scripts.research.execution_timing_replay import replay_day
from scripts.research.intraday_research_cache import CACHE_KEY_VERSION


SCHEMA_VERSION = "caerus_execution_timing_counterfactual_v1"
OFFSETS_MINUTES = (0, 1, 2, 5, 10)
BASELINE_OFFSET_MINUTES = 5
COVERAGE_THRESHOLD = 0.8


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _round(value: Any, digits: int = 6) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except Exception:
        return None


def _offset_label(minutes: int) -> str:
    return f"T+{minutes}m"


def _offset_time(minutes: int) -> str:
    base = dt.datetime(2000, 1, 1, 9, 30)
    return (base + dt.timedelta(minutes=minutes)).strftime("%H:%M")


def _reason_codes(day_status: str, total_trades: int, baseline_fillable: int, coverage_ratio: float) -> list[str]:
    reasons: list[str] = []
    if day_status == "no_plan":
        reasons.append("plan_payload_missing")
    elif day_status == "empty_plan":
        reasons.append("empty_planned_payload")
    elif day_status == "no_cache":
        reasons.append("minute_bar_cache_missing")
    if total_trades == 0:
        reasons.append("no_planned_orders")
    if total_trades > 0 and baseline_fillable == 0:
        reasons.append("baseline_bars_missing")
    if total_trades > 0 and coverage_ratio < COVERAGE_THRESHOLD:
        reasons.append("coverage_below_threshold")
    return sorted(set(reasons)) or ["ok"]


def build_execution_timing_counterfactual(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root)
    out_dir = Path(output_root) if output_root is not None else repo / "outputs" / "research" / "execution_timing" / trade_date
    day = replay_day(
        plan_date=trade_date,
        plan_root=repo / "outputs" / "precompute",
        cache_root=repo / "data" / "research_cache" / "intraday",
        cache_key_version=CACHE_KEY_VERSION,
        offsets_minutes=OFFSETS_MINUTES,
        baseline_offset_minutes=BASELINE_OFFSET_MINUTES,
    )
    baseline_label = _offset_label(BASELINE_OFFSET_MINUTES)
    total_trades = int(day.plan_trade_count or 0)
    baseline_cost = day.day_costs.get(baseline_label, {}).get("cost_usd") if day.day_costs else None
    baseline_gross = day.day_costs.get(baseline_label, {}).get("gross_notional_usd") if day.day_costs else None
    baseline_fillable = int(day.day_costs.get(baseline_label, {}).get("fillable_trades") or 0) if day.day_costs else 0
    coverage_ratio = (baseline_fillable / total_trades) if total_trades else 0.0
    reasons = _reason_codes(day.status, total_trades, baseline_fillable, coverage_ratio)
    available = day.status == "ok" and total_trades > 0 and baseline_cost is not None and coverage_ratio >= COVERAGE_THRESHOLD

    offset_rows: list[dict[str, Any]] = []
    for minutes in OFFSETS_MINUTES:
        label = _offset_label(minutes)
        cost_row = day.day_costs.get(label, {}) if day.day_costs else {}
        opportunity = day.opportunities.get(label, {}) if day.opportunities else {}
        cost = cost_row.get("cost_usd")
        gross = cost_row.get("gross_notional_usd")
        delta = (float(cost) - float(baseline_cost)) if cost is not None and baseline_cost is not None else None
        bps = (delta / float(baseline_gross) * 10_000.0) if delta is not None and baseline_gross else None
        missing_symbols = sorted(
            {
                trade.get("ticker")
                for trade, fills in zip(day.trades, day.per_trade_fills)
                if (fills.get(label) is None or fills.get(label).status != "ok")
            }
        )
        buy_notional = 0.0
        sell_notional = 0.0
        weighted_px_num = 0.0
        weighted_qty = 0.0
        for trade, fills in zip(day.trades, day.per_trade_fills):
            fill = fills.get(label)
            if fill is None or fill.status != "ok" or fill.modeled_fill is None:
                continue
            shares = abs(float(trade.get("shares") or 0.0))
            notional = shares * float(fill.modeled_fill)
            weighted_px_num += shares * float(fill.modeled_fill)
            weighted_qty += shares
            if str(trade.get("side")) == "BUY":
                buy_notional += notional
            elif str(trade.get("side")) == "SELL":
                sell_notional += notional
        offset_reasons = []
        if missing_symbols:
            offset_reasons.append("missing_minute_bars")
        if opportunity.get("reason"):
            offset_reasons.append(str(opportunity["reason"]))
        offset_rows.append(
            {
                "offset_label": label,
                "execution_time_et": _offset_time(minutes),
                "is_baseline": minutes == BASELINE_OFFSET_MINUTES,
                "symbols_evaluated": int(cost_row.get("fillable_trades") or 0),
                "symbols_missing_bars": missing_symbols,
                "buy_notional_evaluated": _round(buy_notional),
                "sell_notional_evaluated": _round(sell_notional),
                "estimated_execution_price": _round(weighted_px_num / weighted_qty if weighted_qty else None),
                "signed_cost_usd": _round(cost),
                "gross_notional_usd": _round(gross),
                "estimated_slippage_vs_baseline_usd": 0.0 if minutes == BASELINE_OFFSET_MINUTES and baseline_cost is not None else _round(delta),
                "total_estimated_bps_impact_vs_baseline": 0.0 if minutes == BASELINE_OFFSET_MINUTES and baseline_cost is not None else _round(bps),
                "reason_codes": sorted(set(offset_reasons)) or ["ok"],
            }
        )

    symbol_rows: list[dict[str, Any]] = []
    for trade, fills in zip(day.trades, day.per_trade_fills):
        baseline_fill = fills.get(baseline_label)
        baseline_price = baseline_fill.modeled_fill if baseline_fill and baseline_fill.status == "ok" else None
        contribution_by_offset: dict[str, float | None] = {}
        prices_by_offset: dict[str, float | None] = {}
        for minutes in OFFSETS_MINUTES:
            label = _offset_label(minutes)
            fill = fills.get(label)
            price = fill.modeled_fill if fill and fill.status == "ok" else None
            prices_by_offset[label] = _round(price)
            if price is not None and baseline_price is not None:
                contribution = signed_shares(str(trade.get("side")), trade.get("shares", 0)) * (float(price) - float(baseline_price))
                contribution_by_offset[label] = 0.0 if minutes == BASELINE_OFFSET_MINUTES else _round(contribution)
            else:
                contribution_by_offset[label] = None
        symbol_rows.append(
            {
                "symbol": trade.get("ticker"),
                "side": trade.get("side"),
                "qty": trade.get("shares"),
                "estimated_execution_price_by_offset": prices_by_offset,
                "contribution_to_timing_difference_usd": contribution_by_offset,
                "reason_codes": ["ok"] if baseline_price is not None else ["baseline_bar_missing"],
            }
        )
    symbol_rows.sort(key=lambda row: (str(row.get("symbol")), str(row.get("side"))))

    numeric_offsets = [
        row for row in offset_rows
        if not row["is_baseline"] and row.get("total_estimated_bps_impact_vs_baseline") is not None
    ]
    best = min(numeric_offsets, key=lambda row: (row["total_estimated_bps_impact_vs_baseline"], row["offset_label"]), default=None)
    worst = max(numeric_offsets, key=lambda row: (row["total_estimated_bps_impact_vs_baseline"], row["offset_label"]), default=None)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "trade_date": trade_date,
        "available": available,
        "confidence": "MEDIUM" if available else "LOW",
        "baseline_offset": baseline_label,
        "baseline_time_et": "09:35",
        "offsets_evaluated": [_offset_label(minutes) for minutes in OFFSETS_MINUTES],
        "symbols_evaluated": baseline_fillable,
        "symbols_missing_bars": sorted({
            symbol for row in offset_rows for symbol in row.get("symbols_missing_bars", [])
        }),
        "buy_notional_evaluated": _round(next((row["buy_notional_evaluated"] for row in offset_rows if row["is_baseline"]), None)),
        "sell_notional_evaluated": _round(next((row["sell_notional_evaluated"] for row in offset_rows if row["is_baseline"]), None)),
        "coverage_ratio": _round(coverage_ratio),
        "best_offset_vs_baseline": best,
        "worst_offset_vs_baseline": worst,
        "reason_codes": reasons,
        "source_artifacts": [str(repo / "outputs" / "precompute" / trade_date / "planned_execution_payload.json")],
    }
    payload = {
        **summary,
        "cache_key_version": CACHE_KEY_VERSION,
        "plan_date": day.plan_date,
        "execution_date": day.execution_date,
        "planned_for_raw": day.planned_for_raw,
        "plan_trade_count": total_trades,
        "offsets": offset_rows,
        "per_symbol_contributions": symbol_rows,
        "notes": [
            "Research-only counterfactual; no orders submitted and no execution timing changed.",
            "Prices use the open of the first cached minute bar with bar_start_ts at or after the hypothetical execution timestamp.",
        ],
    }
    _write_json(out_dir / "execution_timing_counterfactual.json", payload)
    _write_json(out_dir / "execution_timing_summary.json", summary)
    markdown = render_execution_timing_markdown(payload)
    _write_text(out_dir / "execution_timing_counterfactual.md", markdown)
    _write_text(out_dir / "execution_timing_summary.md", render_execution_timing_summary_markdown(summary))
    return payload


def render_execution_timing_summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        f"# Execution Timing Summary - {summary.get('date')}",
        "",
        f"- Available: {summary.get('available')}",
        f"- Confidence: {summary.get('confidence')}",
        f"- Baseline: {summary.get('baseline_time_et')} ({summary.get('baseline_offset')})",
        f"- Symbols evaluated: {summary.get('symbols_evaluated')}",
        f"- Coverage ratio: {summary.get('coverage_ratio')}",
        f"- Reason codes: {', '.join(summary.get('reason_codes') or [])}",
        "",
    ]
    best = summary.get("best_offset_vs_baseline") or {}
    worst = summary.get("worst_offset_vs_baseline") or {}
    lines.append(f"- Best offset vs baseline: {best.get('execution_time_et', 'n/a')} ({best.get('total_estimated_bps_impact_vs_baseline', 'n/a')} bps)")
    lines.append(f"- Worst offset vs baseline: {worst.get('execution_time_et', 'n/a')} ({worst.get('total_estimated_bps_impact_vs_baseline', 'n/a')} bps)")
    return "\n".join(lines)


def render_execution_timing_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Execution Timing Counterfactual - {payload.get('date')}",
        "",
        "This research-only artifact compares hypothetical opening-window offsets against the 09:35 ET baseline.",
        "",
        "| Offset | Time ET | Baseline | Symbols | Missing Bars | Buy Notional | Sell Notional | Slippage USD | Impact bps | Reason Codes |",
        "|---|---:|---|---:|---|---:|---:|---:|---:|---|",
    ]
    for row in payload.get("offsets") or []:
        lines.append(
            "| {offset} | {time} | {base} | {symbols} | {missing} | {buy} | {sell} | {usd} | {bps} | {reasons} |".format(
                offset=row.get("offset_label"),
                time=row.get("execution_time_et"),
                base="yes" if row.get("is_baseline") else "no",
                symbols=row.get("symbols_evaluated"),
                missing=", ".join(row.get("symbols_missing_bars") or []),
                buy=row.get("buy_notional_evaluated"),
                sell=row.get("sell_notional_evaluated"),
                usd=row.get("estimated_slippage_vs_baseline_usd"),
                bps=row.get("total_estimated_bps_impact_vs_baseline"),
                reasons=", ".join(row.get("reason_codes") or []),
            )
        )
    lines.extend(["", "## Per-Symbol Contributions", "", "| Symbol | Side | Qty | Contribution vs 09:35 |", "|---|---|---:|---|"])
    for row in payload.get("per_symbol_contributions") or []:
        contrib = ", ".join(f"{k}={v}" for k, v in sorted((row.get("contribution_to_timing_difference_usd") or {}).items()))
        lines.append(f"| {row.get('symbol')} | {row.get('side')} | {row.get('qty')} | {contrib} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build read-only execution timing counterfactual artifacts.")
    parser.add_argument("--date", required=True, help="Plan/precompute date in YYYY-MM-DD format.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_execution_timing_counterfactual(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    out_dir = Path(args.output_root) if args.output_root else Path(args.repo_root) / "outputs" / "research" / "execution_timing" / args.date
    print(json.dumps({"date": args.date, "available": payload["available"], "reason_codes": payload["reason_codes"], "output_dir": str(out_dir)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
