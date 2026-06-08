from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from research_registry.research.model_quality_common import (
    collect_reason_codes,
    dated_source,
    md_join,
    model_quality_dir,
    normalize_date,
    read_json,
    round_or_none,
    safe_float,
    source_status,
    symbol,
    write_json,
    write_text,
)

SCHEMA_VERSION = "caerus_model_quality_attribution_v1"


def _records(payload: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    raw = (payload or {}).get(key) or []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _record_strategy(row: dict[str, Any]) -> str:
    return str(row.get("strategy") or row.get("strategy_id") or row.get("strategy_slug") or "").strip()


def _record_symbol(row: dict[str, Any]) -> str:
    return symbol(row.get("symbol") or row.get("ticker"))


def _aggregate_rows(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = _record_strategy(row) if key == "strategy" else _record_symbol(row)
        if not name:
            continue
        bucket = grouped.setdefault(
            name,
            {
                key: name,
                "positions": 0,
                "weight": 0.0,
                "pnl_contribution": 0.0,
                "complete_records": 0,
                "reason_codes": set(),
            },
        )
        weight = safe_float(row.get("weight")) or 0.0
        contribution = safe_float(row.get("pnl_contribution_pct") or row.get("pnl_contribution")) or 0.0
        bucket["positions"] += 1
        bucket["weight"] += weight
        bucket["pnl_contribution"] += contribution
        if row.get("return_pct") is not None or row.get("realized_return") is not None:
            bucket["complete_records"] += 1
        for code in row.get("reason_codes") or []:
            if code != "ok":
                bucket["reason_codes"].add(str(code))
    out: list[dict[str, Any]] = []
    for bucket in grouped.values():
        reasons = sorted(bucket.pop("reason_codes")) or ["ok"]
        out.append(
            {
                key: bucket[key],
                "positions": int(bucket["positions"]),
                "weight": round_or_none(bucket["weight"]),
                "pnl_contribution": round_or_none(bucket["pnl_contribution"]),
                "complete_records": int(bucket["complete_records"]),
                "confidence": "HIGH" if reasons == ["ok"] and bucket["positions"] == bucket["complete_records"] else "MEDIUM" if bucket["complete_records"] else "LOW",
                "reason_codes": reasons,
            }
        )
    return sorted(out, key=lambda item: (-abs(float(item.get("pnl_contribution") or 0.0)), str(item.get(key) or "")))


def _top_decisions(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    clean: list[dict[str, Any]] = []
    for row in decisions:
        clean.append(
            {
                "strategy": _record_strategy(row),
                "symbol": _record_symbol(row),
                "rank": round_or_none(row.get("rank")),
                "weight": round_or_none(row.get("weight")),
                "realized_return": round_or_none(row.get("realized_return")),
                "pnl_contribution": round_or_none(row.get("pnl_contribution")),
                "signal_snapshot": dict(row.get("signal_snapshot") or {}),
                "confidence": row.get("confidence") or "LOW",
                "reason_codes": list(row.get("reason_codes") or ["ok"]),
            }
        )
    clean = [row for row in clean if row["strategy"] and row["symbol"]]
    ordered = sorted(clean, key=lambda row: (-(safe_float(row.get("pnl_contribution")) or 0.0), row["strategy"], row["symbol"]))
    detractors = sorted(clean, key=lambda row: ((safe_float(row.get("pnl_contribution")) or 0.0), row["strategy"], row["symbol"]))
    return {
        "decisions_analyzed": len(clean),
        "top_entries": ordered[:10],
        "top_detractors": detractors[:10],
        "reason_codes": ["ok"] if clean else ["NO_DECISION_ROWS"],
    }


def _signal_bucket_section(signal_payload: dict[str, Any] | None) -> dict[str, Any]:
    rows = _records(signal_payload, "signals")
    out = []
    for row in rows:
        out.append(
            {
                "signal_name": str(row.get("signal_name") or ""),
                "observations": int(safe_float(row.get("observations")) or 0),
                "average_score": round_or_none(row.get("average_score")),
                "average_realized_return": round_or_none(row.get("average_realized_return")),
                "hit_rate": round_or_none(row.get("hit_rate")),
                "confidence": row.get("confidence") or "LOW",
                "reason_codes": list(row.get("reason_codes") or ["ok"]),
            }
        )
    out = [row for row in out if row["signal_name"]]
    return {
        "available": bool(out),
        "signals": sorted(out, key=lambda row: (-int(row["observations"]), row["signal_name"])),
        "reason_codes": ["ok"] if out else ["NO_SIGNAL_BUCKET_EVIDENCE"],
    }


def _regime_section(regime_payload: dict[str, Any] | None) -> dict[str, Any]:
    strategies = (regime_payload or {}).get("strategies") or {}
    out: list[dict[str, Any]] = []
    if isinstance(strategies, dict):
        for strategy, block in sorted(strategies.items()):
            regimes = (block or {}).get("regimes") if isinstance(block, dict) else {}
            if not isinstance(regimes, dict):
                continue
            regime_rows = []
            for regime, metrics in sorted(regimes.items()):
                if not isinstance(metrics, dict):
                    continue
                regime_rows.append(
                    {
                        "regime": str(regime),
                        "observation_count": int(safe_float(metrics.get("observation_count")) or 0),
                        "total_return": round_or_none(metrics.get("total_return")),
                        "hit_rate": round_or_none(metrics.get("hit_rate")),
                        "max_drawdown": round_or_none(metrics.get("max_drawdown")),
                        "confidence": metrics.get("confidence") or "LOW",
                        "reason_codes": list(metrics.get("reason_codes") or ["ok"]),
                    }
                )
            out.append({"strategy": str(strategy), "regimes": regime_rows})
    return {
        "available": bool(out),
        "strategies": out,
        "reason_codes": ["ok"] if out else ["NO_REGIME_ATTRIBUTION"],
    }


def _construction_section(risk_payload: dict[str, Any] | None) -> dict[str, Any]:
    strategies = (risk_payload or {}).get("strategies") or {}
    out = []
    if isinstance(strategies, dict):
        for strategy, row in sorted(strategies.items()):
            if not isinstance(row, dict):
                continue
            out.append(
                {
                    "strategy": str(strategy),
                    "position_count": int(safe_float(row.get("position_count")) or 0),
                    "gross_exposure": round_or_none(row.get("gross_exposure")),
                    "top3_concentration": round_or_none(row.get("top3_concentration")),
                    "top5_concentration": round_or_none(row.get("top5_concentration")),
                    "max_single_name_weight": round_or_none(row.get("max_single_name_weight")),
                    "risk_level": row.get("risk_level") or "UNKNOWN",
                    "confidence": row.get("confidence") or "LOW",
                    "reason_codes": list(row.get("reason_codes") or ["ok"]),
                }
            )
    return {
        "available": bool(out),
        "strategies": out,
        "reason_codes": ["ok"] if out else ["NO_PORTFOLIO_CONSTRUCTION_ATTRIBUTION"],
    }


def _operational_section(
    *,
    drag_payload: dict[str, Any] | None,
    timing_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    reasons: list[str] = []
    if drag_payload is None:
        reasons.append("NO_OPERATIONAL_DRAG_ARTIFACT")
    if timing_payload is None:
        reasons.append("NO_EXECUTION_TIMING_ARTIFACT")
    return {
        "available": drag_payload is not None or timing_payload is not None,
        "operational_drag": {
            "available": drag_payload is not None,
            "drag_return": round_or_none((drag_payload or {}).get("operational_drag_return") or (drag_payload or {}).get("drag_return")),
            "reason_codes": list((drag_payload or {}).get("reason_codes") or ([] if drag_payload else ["NO_OPERATIONAL_DRAG_ARTIFACT"])),
        },
        "execution_timing": {
            "available": timing_payload is not None,
            "coverage_ratio": round_or_none((timing_payload or {}).get("coverage_ratio")),
            "best_offset_vs_baseline": (timing_payload or {}).get("best_offset_vs_baseline"),
            "worst_offset_vs_baseline": (timing_payload or {}).get("worst_offset_vs_baseline"),
            "reason_codes": list((timing_payload or {}).get("reason_codes") or ([] if timing_payload else ["NO_EXECUTION_TIMING_ARTIFACT"])),
        },
        "reason_codes": sorted(set(reasons)) or ["ok"],
    }


def build_model_quality_attribution(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
    write: bool = True,
) -> dict[str, Any]:
    target = normalize_date(trade_date)
    repo = Path(repo_root)
    source_blocks: list[dict[str, Any]] = []

    attr_dir, attr_date, attr_reasons = dated_source(repo, "outputs/attribution", target, "position_attribution.json")
    dec_dir, dec_date, dec_reasons = dated_source(repo, "outputs/decision_attribution", target, "decision_attribution.json")
    regime_dir, regime_date, regime_reasons = dated_source(repo, "outputs/research/regime_attribution", target, "regime_attribution.json")
    risk_dir, risk_date, risk_reasons = dated_source(repo, "outputs/research/risk_coverage", target, "risk_coverage.json")
    drag_dir, drag_date, drag_reasons = dated_source(repo, "outputs/operational_drag", target, "operational_drag_attribution.json")
    timing_dir, timing_date, timing_reasons = dated_source(repo, "outputs/research/execution_timing", target, "execution_timing_summary.json")

    for name, directory, source_date, reasons, filename in (
        ("position_attribution", attr_dir, attr_date, attr_reasons, "position_attribution.json"),
        ("decision_attribution", dec_dir, dec_date, dec_reasons, "decision_attribution.json"),
        ("regime_attribution", regime_dir, regime_date, regime_reasons, "regime_attribution.json"),
        ("risk_coverage", risk_dir, risk_date, risk_reasons, "risk_coverage.json"),
        ("operational_drag", drag_dir, drag_date, drag_reasons, "operational_drag_attribution.json"),
        ("execution_timing", timing_dir, timing_date, timing_reasons, "execution_timing_summary.json"),
    ):
        source_blocks.append(
            source_status(
                name=name,
                path=(directory / filename) if directory else None,
                source_date=source_date,
                target_date=target,
                reason_codes=reasons,
            )
        )

    position_payload = read_json(attr_dir / "position_attribution.json") if attr_dir else None
    attr_summary = read_json(attr_dir / "attribution_summary.json") if attr_dir else None
    decision_payload = read_json(dec_dir / "decision_attribution.json") if dec_dir else None
    strategy_decision_summary = read_json(dec_dir / "strategy_decision_summary.json") if dec_dir else None
    signal_payload = read_json(dec_dir / "signal_outcome_summary.json") if dec_dir else None
    regime_payload = read_json(regime_dir / "regime_attribution.json") if regime_dir else None
    risk_payload = read_json(risk_dir / "risk_coverage.json") if risk_dir else None
    drag_payload = read_json(drag_dir / "operational_drag_attribution.json") if drag_dir else None
    timing_payload = read_json(timing_dir / "execution_timing_summary.json") if timing_dir else None

    positions = _records(position_payload, "positions")
    decisions = _records(decision_payload, "decisions")
    strategy_contribution = _aggregate_rows(positions, "strategy")
    symbol_contribution = _aggregate_rows(positions, "symbol")
    entry_exit = _top_decisions(decisions)
    signal_bucket = _signal_bucket_section(signal_payload)
    regime_bucket = _regime_section(regime_payload)
    construction = _construction_section(risk_payload)
    operational = _operational_section(drag_payload=drag_payload, timing_payload=timing_payload)

    reason_codes = collect_reason_codes(
        attr_reasons,
        dec_reasons,
        regime_reasons,
        risk_reasons,
        drag_reasons,
        timing_reasons,
        ["EMPTY_POSITION_ATTRIBUTION"] if attr_dir and not positions else [],
        ["EMPTY_DECISION_ATTRIBUTION"] if dec_dir and not decisions else [],
        entry_exit["reason_codes"],
        signal_bucket["reason_codes"],
        regime_bucket["reason_codes"],
        construction["reason_codes"],
        operational["reason_codes"],
    )
    available = any(block["status"] != "MISSING" for block in source_blocks)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": target,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "available": available,
        "status": "OK" if reason_codes == ["ok"] else "PARTIAL" if available else "NO_DATA",
        "confidence": "MEDIUM" if available else "LOW",
        "reason_codes": reason_codes,
        "source_statuses": source_blocks,
        "strategy_return_contribution": {
            "available": bool(strategy_contribution),
            "strategies": strategy_contribution,
            "top_contributor_per_strategy": (attr_summary or {}).get("top_contributor_per_strategy") or {},
            "top_detractor_per_strategy": (attr_summary or {}).get("top_detractor_per_strategy") or {},
            "reason_codes": ["ok"] if strategy_contribution else ["NO_STRATEGY_CONTRIBUTION_ROWS"],
        },
        "symbol_return_contribution": {
            "available": bool(symbol_contribution),
            "symbols": symbol_contribution,
            "reason_codes": ["ok"] if symbol_contribution else ["NO_SYMBOL_CONTRIBUTION_ROWS"],
        },
        "entry_exit_contribution": entry_exit,
        "decision_summary": {
            "available": bool((strategy_decision_summary or {}).get("strategies")),
            "strategies": list((strategy_decision_summary or {}).get("strategies") or []),
            "reason_codes": list((strategy_decision_summary or {}).get("reason_codes") or ["NO_STRATEGY_DECISION_SUMMARY"]),
        },
        "signal_bucket_contribution": signal_bucket,
        "regime_bucket_contribution": regime_bucket,
        "portfolio_construction_contribution": construction,
        "operational_drag_contribution": operational,
    }
    if write:
        out_dir = model_quality_dir(repo, target, output_root)
        write_json(out_dir / "attribution_quality.json", payload)
        write_text(out_dir / "attribution_quality.md", render_markdown(payload))
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Attribution Quality - {payload.get('date')}",
        "",
        f"- Status: {payload.get('status')}",
        f"- Confidence: {payload.get('confidence')}",
        f"- Reason codes: {md_join(payload.get('reason_codes') or [])}",
        "",
        "## Source Status",
        "",
        "| Source | Status | Source date | Reasons |",
        "|---|---:|---:|---|",
    ]
    for row in payload.get("source_statuses") or []:
        lines.append(f"| {row.get('name')} | {row.get('status')} | {row.get('source_date')} | {md_join(row.get('reason_codes') or [])} |")
    lines.extend(["", "## Strategy Contribution", "", "| Strategy | PnL contribution | Positions | Confidence | Reasons |", "|---|---:|---:|---:|---|"])
    for row in (payload.get("strategy_return_contribution") or {}).get("strategies") or []:
        lines.append(f"| {row.get('strategy')} | {row.get('pnl_contribution')} | {row.get('positions')} | {row.get('confidence')} | {md_join(row.get('reason_codes') or [])} |")
    lines.extend(["", "## Top Symbols", "", "| Symbol | PnL contribution | Positions | Confidence |", "|---|---:|---:|---:|"])
    for row in ((payload.get("symbol_return_contribution") or {}).get("symbols") or [])[:15]:
        lines.append(f"| {row.get('symbol')} | {row.get('pnl_contribution')} | {row.get('positions')} | {row.get('confidence')} |")
    lines.extend(["", "## Decision Attribution", ""])
    lines.append(f"- Decisions analyzed: {(payload.get('entry_exit_contribution') or {}).get('decisions_analyzed')}")
    top_entries = (payload.get("entry_exit_contribution") or {}).get("top_entries") or []
    if top_entries:
        lines.extend(["", "| Strategy | Symbol | Rank | PnL contribution | Realized return |", "|---|---|---:|---:|---:|"])
        for row in top_entries[:10]:
            lines.append(f"| {row.get('strategy')} | {row.get('symbol')} | {row.get('rank')} | {row.get('pnl_contribution')} | {row.get('realized_return')} |")
    lines.extend(["", "## Operational Drag", ""])
    operational = payload.get("operational_drag_contribution") or {}
    lines.append(f"- Available: {operational.get('available')}")
    lines.append(f"- Reasons: {md_join(operational.get('reason_codes') or [])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build research-only model-quality attribution artifacts.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_model_quality_attribution(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(json.dumps({"date": payload["date"], "status": payload["status"], "reason_codes": payload["reason_codes"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
