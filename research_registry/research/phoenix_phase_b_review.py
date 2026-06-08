from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from research_registry.research.model_quality_common import (
    collect_reason_codes,
    md_join,
    model_quality_dir,
    normalize_date,
    read_json,
    round_or_none,
    safe_float,
    symbol,
    write_json,
    write_text,
)

SCHEMA_VERSION = "caerus_phoenix_phase_b_review_v1"
STRATEGY_ID = "caerus_phoenix"
COMPARISON_STRATEGIES = ("caerus_polaris", "caerus_orion", "caerus_lyra")
MIN_DECISION_GRADE_HISTORY_DAYS = 120
MIN_DECISION_GRADE_ACTIVE_DAYS = 20


def build_phoenix_phase_b_review(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
    write: bool = True,
) -> dict[str, Any]:
    target = normalize_date(trade_date)
    repo = Path(repo_root)
    history = _load_phoenix_history(repo, target)
    dates = [row["date"] for row in history]
    active_rows = [row for row in history if row["active"]]
    inactive_rows = [row for row in history if not row["active"]]
    regime_summary = _regime_summary(repo, dates)
    overlap = _overlap_summary(repo, history)
    drawdown = _drawdown_recovery_summary(repo)
    reason_codes = set()
    if not history:
        reason_codes.add("PHOENIX_HISTORY_MISSING")
    if not active_rows:
        reason_codes.add("NO_ACTIVE_PHOENIX_DAYS")
    if len(history) < MIN_DECISION_GRADE_HISTORY_DAYS:
        reason_codes.add(f"SPARSE_PHOENIX_HISTORY:{len(history)}/{MIN_DECISION_GRADE_HISTORY_DAYS}")
    if len(active_rows) < MIN_DECISION_GRADE_ACTIVE_DAYS:
        reason_codes.add(f"SPARSE_PHOENIX_ACTIVE_DAYS:{len(active_rows)}/{MIN_DECISION_GRADE_ACTIVE_DAYS}")
    reason_codes.update(code for code in regime_summary.get("reason_codes", []) if code != "ok")
    reason_codes.update(code for code in overlap.get("reason_codes", []) if code != "ok")
    reason_codes.update(code for code in drawdown.get("reason_codes", []) if code != "ok")
    reason_codes.update(code for row in history for code in row.get("reason_codes", []) if code != "ok")
    reason_codes.add("PHOENIX_PHASE_B_RESEARCH_ONLY")
    decision_blockers = sorted(reason_codes | {"NO_DECISION_GRADE_UNDER_PHASE_B_REVIEW"})
    payload = {
        "trade_date": target,
        "schema_version": SCHEMA_VERSION,
        "strategy_id": STRATEGY_ID,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "review_window": {
            "start_date": min(dates) if dates else None,
            "end_date": max(dates) if dates else None,
            "observation_days": len(history),
            "source_artifact_count": len(history),
        },
        "active_days": len(active_rows),
        "inactive_days": len(inactive_rows),
        "activation_reasons": _activation_reasons(active_rows),
        "candidate_count_distribution": _candidate_count_distribution(history),
        "top_candidates": _top_candidates(active_rows),
        "overlap_vs_polaris_orion_lyra": overlap,
        "regime_summary": regime_summary,
        "drawdown_recovery_summary": drawdown,
        "confidence": _confidence(history=history, active_rows=active_rows, regime_summary=regime_summary, drawdown=drawdown),
        "decision_grade": False,
        "decision_grade_blockers": decision_blockers,
        "reason_codes": sorted(reason_codes) or ["ok"],
    }
    if write:
        out_dir = model_quality_dir(repo, target, output_root)
        write_json(out_dir / "phoenix_phase_b_review.json", payload)
        write_text(out_dir / "phoenix_phase_b_review.md", render_markdown(payload))
    return payload


def _load_phoenix_history(repo: Path, target: str) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    model_root = repo / "outputs" / "model_quality"
    if model_root.exists():
        for child in sorted(model_root.iterdir(), key=lambda path: path.name):
            if not child.is_dir():
                continue
            try:
                date = normalize_date(child.name)
            except Exception:
                continue
            if date > target:
                continue
            payload = read_json(child / "phoenix_research.json")
            if payload is not None:
                rows[date] = _history_row(date, payload, child / "phoenix_research.json")
    research_root = repo / "outputs" / "research" / "phoenix"
    if research_root.exists():
        for child in sorted(research_root.iterdir(), key=lambda path: path.name):
            if not child.is_dir():
                continue
            try:
                date = normalize_date(child.name)
            except Exception:
                continue
            if date > target or date in rows:
                continue
            payload = read_json(child / "phoenix_holdings.json") or read_json(child / "phoenix_decision_trace.json")
            if payload is not None:
                rows[date] = _history_row(date, payload, child)
    return [rows[date] for date in sorted(rows)]


def _history_row(date: str, payload: dict[str, Any], source_path: Path) -> dict[str, Any]:
    candidates = _candidate_rows(payload)
    active = bool(payload.get("active")) or bool(candidates)
    reasons = sorted({str(code) for code in payload.get("reason_codes", []) if str(code)})
    if not reasons and payload.get("reason_code"):
        reasons = [str(payload["reason_code"])]
    return {
        "date": date,
        "active": active,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "source_path": str(source_path),
        "reason_codes": reasons or ["ok"],
    }


def _candidate_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("target_candidates")
    if not isinstance(raw, list):
        raw = payload.get("holdings") if isinstance(payload.get("holdings"), list) else []
    weights = payload.get("target_weights") if isinstance(payload.get("target_weights"), dict) else {}
    rows = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        ticker = symbol(item.get("ticker") or item.get("symbol"))
        if not ticker:
            continue
        rows.append(
            {
                "ticker": ticker,
                "target_weight": round_or_none(item.get("target_weight") if item.get("target_weight") is not None else weights.get(ticker)),
                "phoenix_score": round_or_none(item.get("phoenix_score") or item.get("score")),
                "sector": str(item.get("sector") or "UNKNOWN"),
                "reason_codes": sorted(str(code) for code in (item.get("reason_codes") or ["ok"])),
            }
        )
    for ticker_raw, weight_raw in weights.items():
        ticker = symbol(ticker_raw)
        if ticker and all(row["ticker"] != ticker for row in rows):
            rows.append({"ticker": ticker, "target_weight": round_or_none(weight_raw), "phoenix_score": None, "sector": "UNKNOWN", "reason_codes": ["ok"]})
    return sorted(rows, key=lambda row: (-(row.get("phoenix_score") or 0.0), row["ticker"]))


def _activation_reasons(active_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for row in active_rows:
        for code in row.get("reason_codes") or ["ok"]:
            counts[str(code)] += 1
    return [{"reason_code": code, "active_day_count": count} for code, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def _candidate_count_distribution(history: list[dict[str, Any]]) -> list[dict[str, int]]:
    counts = Counter(int(row.get("candidate_count") or 0) for row in history)
    return [{"candidate_count": count, "days": days} for count, days in sorted(counts.items())]


def _top_candidates(active_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"active_days": 0, "scores": [], "weights": [], "sectors": Counter(), "first_seen": None, "last_seen": None})
    for row in active_rows:
        date = row["date"]
        for candidate in row.get("candidates") or []:
            ticker = candidate["ticker"]
            stats[ticker]["active_days"] += 1
            if candidate.get("phoenix_score") is not None:
                stats[ticker]["scores"].append(float(candidate["phoenix_score"]))
            if candidate.get("target_weight") is not None:
                stats[ticker]["weights"].append(float(candidate["target_weight"]))
            stats[ticker]["sectors"][candidate.get("sector") or "UNKNOWN"] += 1
            stats[ticker]["first_seen"] = min(stats[ticker]["first_seen"] or date, date)
            stats[ticker]["last_seen"] = max(stats[ticker]["last_seen"] or date, date)
    rows = []
    for ticker, values in stats.items():
        sectors = values["sectors"]
        rows.append(
            {
                "ticker": ticker,
                "active_days": values["active_days"],
                "average_score": round_or_none(sum(values["scores"]) / len(values["scores"]) if values["scores"] else None),
                "average_weight": round_or_none(sum(values["weights"]) / len(values["weights"]) if values["weights"] else None),
                "primary_sector": sectors.most_common(1)[0][0] if sectors else "UNKNOWN",
                "first_seen": values["first_seen"],
                "last_seen": values["last_seen"],
            }
        )
    return sorted(rows, key=lambda row: (-row["active_days"], -(row.get("average_score") or 0.0), row["ticker"]))[:25]


def _overlap_summary(repo: Path, history: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    reasons: set[str] = set()
    for strategy in COMPARISON_STRATEGIES:
        compared = 0
        overlap_sum = 0.0
        max_overlap = 0.0
        latest_overlap: dict[str, Any] | None = None
        for row in history:
            phoenix_symbols = {candidate["ticker"] for candidate in row.get("candidates") or []}
            if not phoenix_symbols:
                continue
            snapshot = _load_shadow_snapshot(repo, row["date"], strategy)
            strategy_symbols = set(_snapshot_symbols(snapshot))
            if not strategy_symbols:
                reasons.add(f"{strategy.upper()}_SNAPSHOT_MISSING")
                continue
            compared += 1
            overlap_symbols = sorted(phoenix_symbols & strategy_symbols)
            union = phoenix_symbols | strategy_symbols
            overlap_ratio = len(overlap_symbols) / max(1, len(union))
            overlap_sum += overlap_ratio
            max_overlap = max(max_overlap, overlap_ratio)
            latest_overlap = {"date": row["date"], "overlap_symbols": overlap_symbols, "overlap_ratio": round(overlap_ratio, 10)}
        rows.append(
            {
                "strategy_id": strategy,
                "days_compared": compared,
                "average_overlap_ratio": round(overlap_sum / compared, 10) if compared else None,
                "max_overlap_ratio": round(max_overlap, 10) if compared else None,
                "latest_overlap": latest_overlap,
                "reason_codes": ["ok"] if compared else [f"{strategy.upper()}_OVERLAP_UNAVAILABLE"],
            }
        )
    if not history:
        reasons.add("PHOENIX_HISTORY_MISSING")
    return {
        "comparisons": rows,
        "reason_codes": sorted(reasons) or (["ok"] if any(row["days_compared"] for row in rows) else ["OVERLAP_INPUTS_MISSING"]),
    }


def _load_shadow_snapshot(repo: Path, date: str, strategy: str) -> dict[str, Any] | None:
    path = repo / "outputs" / "shadow_candidates" / date / f"{strategy}.json"
    return read_json(path)


def _snapshot_symbols(snapshot: dict[str, Any] | None) -> list[str]:
    if not snapshot:
        return []
    out = {symbol(ticker) for ticker in ((snapshot.get("target_weights") or {}).keys()) if symbol(ticker)}
    for row in snapshot.get("holdings") or []:
        if isinstance(row, dict):
            ticker = symbol(row.get("ticker") or row.get("symbol"))
            if ticker:
                out.add(ticker)
    return sorted(out)


def _regime_summary(repo: Path, dates: list[str]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    reasons: set[str] = set()
    rows = []
    for date in dates:
        payload, source = _load_regime(repo, date)
        if payload is None:
            reasons.add("REGIME_DATA_MISSING")
            rows.append({"date": date, "regime": None, "source_path": None, "reason_codes": ["REGIME_DATA_MISSING"]})
            continue
        regime = str(payload.get("regime") or payload.get("current_regime") or "UNKNOWN").upper()
        counts[regime] += 1
        rows.append({"date": date, "regime": regime, "source_path": str(source), "reason_codes": ["ok"]})
    return {
        "available": bool(counts),
        "regime_counts": [{"regime": regime, "days": count} for regime, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))],
        "daily_regimes": rows,
        "reason_codes": sorted(reasons) or ["ok"],
    }


def _load_regime(repo: Path, date: str) -> tuple[dict[str, Any] | None, Path | None]:
    candidates = [
        repo / "outputs" / "vix_regime" / date / "regime_current.json",
        repo / "outputs" / "vix_regime" / f"regime_{date}.json",
        repo / "outputs" / "vix_regime" / "regime_current.json",
    ]
    for path in candidates:
        payload = read_json(path)
        if payload is None:
            continue
        raw_date = str(payload.get("date") or payload.get("as_of") or "")[:10]
        if path.name == "regime_current.json" and path.parent.name != date and raw_date and raw_date != date:
            continue
        return payload, path
    return None, None


def _drawdown_recovery_summary(repo: Path) -> dict[str, Any]:
    path = repo / "outputs" / "research" / "phoenix" / "performance" / "phoenix_nav_series.csv"
    if not path.exists():
        return {"available": False, "source_path": None, "reason_codes": ["PRICE_DATA_MISSING"]}
    rows = []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for raw in reader:
                date = str(raw.get("date") or "").strip()
                nav = safe_float(raw.get("phoenix_nav") or raw.get("nav") or raw.get("caerus_phoenix"))
                if date and nav is not None:
                    rows.append({"date": date, "nav": nav})
    except Exception:
        return {"available": False, "source_path": str(path), "reason_codes": ["PRICE_DATA_UNREADABLE"]}
    if len(rows) < 2:
        return {"available": False, "source_path": str(path), "reason_codes": ["PRICE_HISTORY_TOO_SHORT"]}
    rows.sort(key=lambda row: row["date"])
    start = rows[0]["nav"]
    end = rows[-1]["nav"]
    peak = None
    max_drawdown = 0.0
    max_drawdown_date = None
    for row in rows:
        peak = row["nav"] if peak is None else max(peak, row["nav"])
        drawdown = (row["nav"] / peak) - 1.0 if peak else 0.0
        if drawdown < max_drawdown:
            max_drawdown = drawdown
            max_drawdown_date = row["date"]
    return {
        "available": True,
        "source_path": str(path),
        "start_date": rows[0]["date"],
        "end_date": rows[-1]["date"],
        "total_return": round_or_none((end / start) - 1.0 if start else None),
        "max_drawdown": round_or_none(max_drawdown),
        "max_drawdown_date": max_drawdown_date,
        "recovered_to_new_high": bool(end >= max(row["nav"] for row in rows)),
        "reason_codes": ["ok"],
    }


def _confidence(*, history: list[dict[str, Any]], active_rows: list[dict[str, Any]], regime_summary: dict[str, Any], drawdown: dict[str, Any]) -> str:
    if len(history) >= MIN_DECISION_GRADE_HISTORY_DAYS and len(active_rows) >= MIN_DECISION_GRADE_ACTIVE_DAYS and regime_summary.get("available") and drawdown.get("available"):
        return "MEDIUM"
    if history:
        return "LOW"
    return "NONE"


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Phoenix Phase B Review - {payload.get('trade_date')}",
        "",
        f"- Strategy: {payload.get('strategy_id')}",
        f"- Governance: {payload.get('governance_label')} / {payload.get('execution_impact')}",
        f"- Active days: {payload.get('active_days')}",
        f"- Inactive days: {payload.get('inactive_days')}",
        f"- Confidence: {payload.get('confidence')}",
        f"- Decision grade: {payload.get('decision_grade')}",
        f"- Reason codes: {md_join(payload.get('reason_codes') or [])}",
        "",
        "## Top Candidates",
        "",
        "| Ticker | Active Days | Avg Score | Avg Weight | Sector | First | Last |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    for row in payload.get("top_candidates") or []:
        lines.append(
            f"| {row.get('ticker')} | {row.get('active_days')} | {row.get('average_score')} | "
            f"{row.get('average_weight')} | {row.get('primary_sector')} | {row.get('first_seen')} | {row.get('last_seen')} |"
        )
    if not payload.get("top_candidates"):
        lines.append("| none | 0 | n/a | n/a | n/a | n/a | n/a |")
    lines.extend(["", "## Overlap", "", "| Strategy | Days Compared | Avg Overlap | Max Overlap | Reasons |", "|---|---:|---:|---:|---|"])
    for row in (payload.get("overlap_vs_polaris_orion_lyra") or {}).get("comparisons") or []:
        lines.append(
            f"| {row.get('strategy_id')} | {row.get('days_compared')} | {row.get('average_overlap_ratio')} | "
            f"{row.get('max_overlap_ratio')} | {md_join(row.get('reason_codes') or [])} |"
        )
    lines.extend(["", "## Regimes", "", "| Regime | Days |", "|---|---:|"])
    for row in (payload.get("regime_summary") or {}).get("regime_counts") or []:
        lines.append(f"| {row.get('regime')} | {row.get('days')} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Phoenix Phase B historical behavior review artifacts.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_phoenix_phase_b_review(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(json.dumps({"trade_date": payload["trade_date"], "active_days": payload["active_days"], "confidence": payload["confidence"], "reason_codes": payload["reason_codes"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
