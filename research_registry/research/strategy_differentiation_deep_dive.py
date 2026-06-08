from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from core.strategy_registry import load_strategy_registry_for_repo
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

SCHEMA_VERSION = "caerus_strategy_differentiation_deep_dive_v1"
DEFAULT_STRATEGIES = ("caerus_polaris", "caerus_orion", "caerus_lyra", "caerus_phoenix")
MIN_RETURN_CORRELATION_OBS = 30


def build_strategy_differentiation_deep_dive(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
    write: bool = True,
) -> dict[str, Any]:
    target = normalize_date(trade_date)
    repo = Path(repo_root)
    strategy_ids = _strategy_ids(repo)
    sector_map = _load_sector_map(repo / "data" / "universe.csv")
    snapshots = {strategy: _load_strategy_snapshot(repo, target, strategy, sector_map=sector_map) for strategy in strategy_ids}
    returns = _load_return_series(repo)
    attribution = _load_attribution(repo, target)
    tournament = read_json(repo / "outputs" / "model_quality" / target / "model_tournament.json") or {}
    pairs = []
    for left, right in itertools.combinations(strategy_ids, 2):
        pairs.append(
            _pair_record(
                left=left,
                right=right,
                left_snapshot=snapshots[left],
                right_snapshot=snapshots[right],
                returns=returns,
                attribution=attribution,
                tournament=tournament,
            )
        )
    pairs = sorted(pairs, key=lambda row: row["pair_id"])
    watchlist = _retirement_watchlist(pairs)
    reason_codes = set(collect_reason_codes(*(pair.get("reason_codes") or [] for pair in pairs)))
    if not pairs:
        reason_codes.add("NO_STRATEGY_PAIRS")
    if watchlist:
        reason_codes.add("RETIREMENT_WATCHLIST_RESEARCH_ONLY")
    payload = {
        "trade_date": target,
        "schema_version": SCHEMA_VERSION,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "strategy_set": strategy_ids,
        "pairwise": pairs,
        "pairwise_holdings_overlap": {pair["pair_id"]: pair["holdings_overlap"] for pair in pairs},
        "pairwise_active_share": {pair["pair_id"]: pair["active_share"] for pair in pairs},
        "pairwise_sector_difference": {pair["pair_id"]: pair["sector_difference"] for pair in pairs},
        "pairwise_turnover_difference": {pair["pair_id"]: pair["turnover_difference"] for pair in pairs},
        "pairwise_concentration_difference": {pair["pair_id"]: pair["concentration_difference"] for pair in pairs},
        "pairwise_return_correlation": {pair["pair_id"]: pair["return_correlation"] for pair in pairs},
        "pairwise_attribution_spread": {pair["pair_id"]: pair["attribution_spread"] for pair in pairs},
        "regime_specific_behavior": {pair["pair_id"]: pair["regime_specific_behavior"] for pair in pairs},
        "redundancy_classification_counts": _classification_counts(pairs),
        "retirement_watchlist": watchlist,
        "decision_grade_retirement_recommendation": False,
        "reason_codes": sorted(reason_codes) or ["ok"],
    }
    if write:
        out_dir = model_quality_dir(repo, target, output_root)
        write_json(out_dir / "strategy_differentiation_deep_dive.json", payload)
        write_text(out_dir / "strategy_differentiation_deep_dive.md", render_markdown(payload))
    return payload


def _strategy_ids(repo: Path) -> list[str]:
    try:
        registry = load_strategy_registry_for_repo(repo)
        ids = [
            entry.strategy_id
            for entry in registry.security_selection_entries()
            if entry.strategy_id != "spy_benchmark" and entry.status != "retired"
        ]
    except Exception:
        ids = list(DEFAULT_STRATEGIES)
    for strategy in DEFAULT_STRATEGIES:
        if strategy not in ids:
            ids.append(strategy)
    return sorted(ids)


def _load_sector_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                ticker = symbol(row.get("ticker") or row.get("symbol"))
                if ticker:
                    out[ticker] = str(row.get("sector") or "UNKNOWN")
    except Exception:
        return {}
    return out


def _load_strategy_snapshot(repo: Path, target: str, strategy: str, *, sector_map: dict[str, str]) -> dict[str, Any]:
    path, stale = _snapshot_path(repo, target, strategy)
    payload = read_json(path) if path else None
    if payload is None:
        return {
            "strategy_id": strategy,
            "available": False,
            "source_path": str(path) if path else None,
            "source_date": path.parent.name if path else None,
            "weights": {},
            "sectors": {},
            "turnover": None,
            "concentration": _concentration({}),
            "reason_codes": [f"{strategy.upper()}_SNAPSHOT_MISSING"],
        }
    weights, sectors = _weights_from_snapshot(payload, sector_map=sector_map)
    reasons = []
    if stale:
        reasons.append("SOURCE_DATE_DIFFERS_FROM_TARGET")
    if not weights:
        reasons.append("HOLDINGS_MISSING")
    return {
        "strategy_id": strategy,
        "available": bool(weights),
        "source_path": str(path),
        "source_date": path.parent.name,
        "weights": weights,
        "sectors": sectors,
        "turnover": round_or_none(payload.get("expected_turnover") or payload.get("turnover") or payload.get("avg_turnover")),
        "concentration": _concentration(weights),
        "reason_codes": sorted(set(reasons)) or ["ok"],
    }


def _snapshot_path(repo: Path, target: str, strategy: str) -> tuple[Path | None, bool]:
    exact = repo / "outputs" / "shadow_candidates" / target / f"{strategy}.json"
    if exact.exists():
        return exact, False
    root = repo / "outputs" / "shadow_candidates"
    candidates: list[Path] = []
    if root.exists():
        for child in root.iterdir():
            if not child.is_dir():
                continue
            try:
                date = normalize_date(child.name)
            except Exception:
                continue
            path = child / f"{strategy}.json"
            if date <= target and path.exists():
                candidates.append(path)
    if candidates:
        return sorted(candidates, key=lambda path: path.parent.name)[-1], True
    return None, False


def _weights_from_snapshot(payload: dict[str, Any], *, sector_map: dict[str, str]) -> tuple[dict[str, float], dict[str, str]]:
    weights: dict[str, float] = {}
    sectors: dict[str, str] = {}
    raw_weights = payload.get("target_weights") if isinstance(payload.get("target_weights"), dict) else {}
    for ticker_raw, weight_raw in raw_weights.items():
        ticker = symbol(ticker_raw)
        weight = safe_float(weight_raw)
        if ticker and weight is not None:
            weights[ticker] = float(weight)
            sectors[ticker] = sector_map.get(ticker, "UNKNOWN")
    for row in payload.get("holdings") or []:
        if not isinstance(row, dict):
            continue
        ticker = symbol(row.get("ticker") or row.get("symbol"))
        if not ticker:
            continue
        weight = safe_float(row.get("target_weight") if row.get("target_weight") is not None else raw_weights.get(ticker))
        if weight is not None:
            weights[ticker] = float(weight)
        sectors[ticker] = str(row.get("sector") or sector_map.get(ticker) or sectors.get(ticker) or "UNKNOWN")
    return dict(sorted(weights.items())), dict(sorted(sectors.items()))


def _pair_record(
    *,
    left: str,
    right: str,
    left_snapshot: dict[str, Any],
    right_snapshot: dict[str, Any],
    returns: dict[str, list[float]],
    attribution: dict[str, dict[str, float]],
    tournament: dict[str, Any],
) -> dict[str, Any]:
    pair_id = f"{left}__{right}"
    left_weights = left_snapshot.get("weights") or {}
    right_weights = right_snapshot.get("weights") or {}
    holdings_overlap = _holdings_overlap(left_weights, right_weights)
    active_share = _active_share(left_weights, right_weights)
    sector_difference = _sector_difference(left_snapshot, right_snapshot)
    turnover_difference = _turnover_difference(left_snapshot, right_snapshot)
    concentration_difference = _concentration_difference(left_snapshot, right_snapshot)
    return_correlation = _return_correlation(returns.get(left), returns.get(right))
    attribution_spread = _attribution_spread(attribution.get(left), attribution.get(right))
    regime_behavior = _regime_behavior(tournament, left, right)
    reasons = set()
    reasons.update(code for code in left_snapshot.get("reason_codes", []) if code != "ok")
    reasons.update(code for code in right_snapshot.get("reason_codes", []) if code != "ok")
    if return_correlation.get("available") is False:
        reasons.update(code for code in return_correlation.get("reason_codes", []) if code != "ok")
    if attribution_spread.get("available") is False:
        reasons.update(code for code in attribution_spread.get("reason_codes", []) if code != "ok")
    classification, confidence = _classify_pair(
        holdings_overlap=holdings_overlap,
        active_share=active_share,
        sector_difference=sector_difference,
        return_correlation=return_correlation,
        snapshots_available=bool(left_weights and right_weights),
    )
    if classification == "INSUFFICIENT_EVIDENCE":
        reasons.add("INSUFFICIENT_PAIR_EVIDENCE")
    return {
        "pair_id": pair_id,
        "left_strategy_id": left,
        "right_strategy_id": right,
        "holdings_overlap": holdings_overlap,
        "active_share": active_share,
        "sector_difference": sector_difference,
        "turnover_difference": turnover_difference,
        "concentration_difference": concentration_difference,
        "return_correlation": return_correlation,
        "attribution_spread": attribution_spread,
        "regime_specific_behavior": regime_behavior,
        "redundancy_classification": classification,
        "confidence": confidence,
        "reason_codes": sorted(reasons) or ["ok"],
    }


def _holdings_overlap(left: dict[str, float], right: dict[str, float]) -> dict[str, Any]:
    union = sorted(set(left) | set(right))
    common = sorted(set(left) & set(right))
    if not left or not right:
        return {"available": False, "symbol_overlap_ratio": None, "weight_overlap": None, "common_symbols": [], "reason_codes": ["HOLDINGS_MISSING"]}
    return {
        "available": True,
        "symbol_overlap_ratio": round(len(common) / max(1, len(union)), 10),
        "weight_overlap": round(sum(min(abs(left.get(ticker, 0.0)), abs(right.get(ticker, 0.0))) for ticker in union), 10),
        "common_symbols": common,
        "left_only_symbols": sorted(set(left) - set(right)),
        "right_only_symbols": sorted(set(right) - set(left)),
        "reason_codes": ["ok"] if common else ["NO_HOLDINGS_OVERLAP"],
    }


def _active_share(left: dict[str, float], right: dict[str, float]) -> dict[str, Any]:
    if not left or not right:
        return {"available": False, "active_share": None, "reason_codes": ["HOLDINGS_MISSING"]}
    union = sorted(set(left) | set(right))
    return {"available": True, "active_share": round(sum(abs(left.get(ticker, 0.0) - right.get(ticker, 0.0)) for ticker in union) / 2.0, 10), "reason_codes": ["ok"]}


def _sector_difference(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_exp = _sector_exposure(left.get("weights") or {}, left.get("sectors") or {})
    right_exp = _sector_exposure(right.get("weights") or {}, right.get("sectors") or {})
    if not left_exp or not right_exp:
        return {"available": False, "sector_active_share": None, "differences": [], "reason_codes": ["SECTOR_INPUTS_MISSING"]}
    sectors = sorted(set(left_exp) | set(right_exp))
    differences = [
        {"sector": sector, "left_weight": round(left_exp.get(sector, 0.0), 10), "right_weight": round(right_exp.get(sector, 0.0), 10), "weight_delta": round(left_exp.get(sector, 0.0) - right_exp.get(sector, 0.0), 10)}
        for sector in sectors
    ]
    return {
        "available": True,
        "sector_active_share": round(sum(abs(row["weight_delta"]) for row in differences) / 2.0, 10),
        "differences": sorted(differences, key=lambda row: (-abs(row["weight_delta"]), row["sector"])),
        "reason_codes": ["ok"],
    }


def _sector_exposure(weights: dict[str, float], sectors: dict[str, str]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for ticker, weight in weights.items():
        out[sectors.get(ticker) or "UNKNOWN"] += float(weight)
    return dict(sorted(out.items()))


def _turnover_difference(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_turnover = left.get("turnover")
    right_turnover = right.get("turnover")
    if left_turnover is None or right_turnover is None:
        return {"available": False, "left_turnover": left_turnover, "right_turnover": right_turnover, "absolute_difference": None, "reason_codes": ["TURNOVER_INPUTS_MISSING"]}
    return {"available": True, "left_turnover": left_turnover, "right_turnover": right_turnover, "absolute_difference": round(abs(float(left_turnover) - float(right_turnover)), 10), "reason_codes": ["ok"]}


def _concentration(weights: dict[str, float]) -> dict[str, Any]:
    values = sorted((abs(float(value)) for value in weights.values()), reverse=True)
    return {"holdings_count": len(values), "max_weight": round(values[0], 10) if values else None, "top3_concentration": round(sum(values[:3]), 10) if values else None, "hhi": round(sum(value * value for value in values), 10) if values else None}


def _concentration_difference(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_conc = left.get("concentration") or {}
    right_conc = right.get("concentration") or {}
    if not left_conc.get("holdings_count") or not right_conc.get("holdings_count"):
        return {"available": False, "top3_difference": None, "hhi_difference": None, "reason_codes": ["CONCENTRATION_INPUTS_MISSING"]}
    return {
        "available": True,
        "left": left_conc,
        "right": right_conc,
        "top3_difference": round_or_none((left_conc.get("top3_concentration") or 0.0) - (right_conc.get("top3_concentration") or 0.0)),
        "hhi_difference": round_or_none((left_conc.get("hhi") or 0.0) - (right_conc.get("hhi") or 0.0)),
        "reason_codes": ["ok"],
    }


def _load_return_series(repo: Path) -> dict[str, list[float]]:
    path = repo / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv"
    if not path.exists():
        return {}
    columns: dict[str, list[float]] = defaultdict(list)
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                for key, value in row.items():
                    if key == "date":
                        continue
                    numeric = safe_float(value)
                    if numeric is not None:
                        columns[key].append(float(numeric))
    except Exception:
        return {}
    returns = {}
    for key, values in columns.items():
        rows = []
        for prev, cur in zip(values, values[1:]):
            if prev:
                rows.append((cur / prev) - 1.0)
        returns[key] = rows
    return returns


def _return_correlation(left: list[float] | None, right: list[float] | None) -> dict[str, Any]:
    if not left or not right:
        return {"available": False, "correlation": None, "observations": 0, "reason_codes": ["RETURN_STREAM_MISSING"]}
    n = min(len(left), len(right))
    x = left[-n:]
    y = right[-n:]
    if n < MIN_RETURN_CORRELATION_OBS:
        return {"available": False, "correlation": None, "observations": n, "reason_codes": [f"INSUFFICIENT_RETURN_OBSERVATIONS:{n}/{MIN_RETURN_CORRELATION_OBS}"]}
    x_mean = sum(x) / n
    y_mean = sum(y) / n
    x_var = sum((value - x_mean) ** 2 for value in x)
    y_var = sum((value - y_mean) ** 2 for value in y)
    if x_var <= 0 or y_var <= 0:
        return {"available": False, "correlation": None, "observations": n, "reason_codes": ["RETURN_VARIANCE_ZERO"]}
    corr = sum((a - x_mean) * (b - y_mean) for a, b in zip(x, y)) / math.sqrt(x_var * y_var)
    return {"available": True, "correlation": round(corr, 10), "observations": n, "reason_codes": ["ok"]}


def _load_attribution(repo: Path, target: str) -> dict[str, dict[str, float]]:
    payload = read_json(repo / "outputs" / "attribution" / target / "position_attribution.json") or {}
    rows = payload.get("positions") if isinstance(payload.get("positions"), list) else []
    out: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        if not isinstance(row, dict):
            continue
        strategy = str(row.get("strategy") or "")
        ticker = symbol(row.get("symbol") or row.get("ticker"))
        value = safe_float(row.get("pnl_contribution_pct") if row.get("pnl_contribution_pct") is not None else row.get("pnl_contribution"))
        if strategy and ticker and value is not None:
            out[strategy][ticker] += float(value)
    return {strategy: dict(values) for strategy, values in out.items()}


def _attribution_spread(left: dict[str, float] | None, right: dict[str, float] | None) -> dict[str, Any]:
    left = left or {}
    right = right or {}
    if not left and not right:
        return {"available": False, "total_absolute_spread": None, "top_drivers": [], "reason_codes": ["ATTRIBUTION_INPUTS_MISSING"]}
    tickers = sorted(set(left) | set(right))
    rows = [{"ticker": ticker, "left_contribution": round(left.get(ticker, 0.0), 10), "right_contribution": round(right.get(ticker, 0.0), 10), "spread": round(left.get(ticker, 0.0) - right.get(ticker, 0.0), 10)} for ticker in tickers]
    return {
        "available": True,
        "total_absolute_spread": round(sum(abs(row["spread"]) for row in rows), 10),
        "top_drivers": sorted(rows, key=lambda row: (-abs(row["spread"]), row["ticker"]))[:10],
        "reason_codes": ["ok"],
    }


def _regime_behavior(tournament: dict[str, Any], left: str, right: str) -> dict[str, Any]:
    strategies = {row.get("strategy"): row for row in tournament.get("strategies") or [] if isinstance(row, dict)}
    left_regimes = ((strategies.get(left) or {}).get("metrics") or {}).get("regime_specific_return") or {}
    right_regimes = ((strategies.get(right) or {}).get("metrics") or {}).get("regime_specific_return") or {}
    regimes = sorted(set(left_regimes) | set(right_regimes))
    if not regimes:
        return {"available": False, "regime_spread": [], "reason_codes": ["REGIME_BEHAVIOR_MISSING"]}
    rows = []
    for regime in regimes:
        left_return = round_or_none((left_regimes.get(regime) or {}).get("total_return"))
        right_return = round_or_none((right_regimes.get(regime) or {}).get("total_return"))
        rows.append({"regime": regime, "left_total_return": left_return, "right_total_return": right_return, "spread": round_or_none(left_return - right_return if left_return is not None and right_return is not None else None)})
    return {"available": True, "regime_spread": rows, "reason_codes": ["ok"]}


def _classify_pair(
    *,
    holdings_overlap: dict[str, Any],
    active_share: dict[str, Any],
    sector_difference: dict[str, Any],
    return_correlation: dict[str, Any],
    snapshots_available: bool,
) -> tuple[str, str]:
    if not snapshots_available:
        return "INSUFFICIENT_EVIDENCE", "LOW"
    overlap = holdings_overlap.get("weight_overlap")
    active = active_share.get("active_share")
    sector = sector_difference.get("sector_active_share")
    corr = return_correlation.get("correlation") if return_correlation.get("available") else None
    if active is not None and overlap is not None and active <= 0.05 and overlap >= 0.90 and (corr is None or corr >= 0.90):
        return "NEAR_DUPLICATE", "MEDIUM" if corr is not None else "LOW"
    if active is not None and (active >= 0.50 or overlap == 0.0 or (sector is not None and sector >= 0.50) or (corr is not None and abs(corr) < 0.50)):
        return "DISTINCT", "MEDIUM" if corr is not None else "LOW"
    return "PARTIALLY_OVERLAPPING", "MEDIUM" if corr is not None else "LOW"


def _retirement_watchlist(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for pair in pairs:
        if pair.get("redundancy_classification") != "NEAR_DUPLICATE":
            continue
        rows.append(
            {
                "strategy_id": pair["right_strategy_id"],
                "paired_with": pair["left_strategy_id"],
                "reason": f"{pair['pair_id']} classified NEAR_DUPLICATE",
                "confidence": pair.get("confidence") or "LOW",
                "decision_grade": False,
                "reason_codes": sorted(set((pair.get("reason_codes") or []) + ["WATCHLIST_ONLY_NOT_RETIREMENT_RECOMMENDATION"])),
            }
        )
    return sorted(rows, key=lambda row: (row["strategy_id"], row["paired_with"]))


def _classification_counts(pairs: list[dict[str, Any]]) -> dict[str, int]:
    counts = {key: 0 for key in ("DISTINCT", "PARTIALLY_OVERLAPPING", "NEAR_DUPLICATE", "INSUFFICIENT_EVIDENCE")}
    for pair in pairs:
        classification = pair.get("redundancy_classification")
        if classification in counts:
            counts[classification] += 1
    return counts


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Strategy Differentiation Deep Dive - {payload.get('trade_date')}",
        "",
        f"- Governance: {payload.get('governance_label')} / {payload.get('execution_impact')}",
        f"- Strategy set: {md_join(payload.get('strategy_set') or [])}",
        f"- Decision-grade retirement recommendation: {payload.get('decision_grade_retirement_recommendation')}",
        f"- Reason codes: {md_join(payload.get('reason_codes') or [])}",
        "",
        "## Pairwise Classification",
        "",
        "| Pair | Classification | Confidence | Overlap | Active Share | Return Corr | Reasons |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for pair in payload.get("pairwise") or []:
        lines.append(
            f"| {pair.get('pair_id')} | {pair.get('redundancy_classification')} | {pair.get('confidence')} | "
            f"{(pair.get('holdings_overlap') or {}).get('weight_overlap')} | {(pair.get('active_share') or {}).get('active_share')} | "
            f"{(pair.get('return_correlation') or {}).get('correlation')} | {md_join(pair.get('reason_codes') or [])} |"
        )
    lines.extend(["", "## Retirement Watchlist", "", "| Strategy | Paired With | Confidence | Decision Grade | Reason |", "|---|---|---|:---:|---|"])
    for row in payload.get("retirement_watchlist") or []:
        lines.append(f"| {row.get('strategy_id')} | {row.get('paired_with')} | {row.get('confidence')} | {row.get('decision_grade')} | {row.get('reason')} |")
    if not payload.get("retirement_watchlist"):
        lines.append("| none | n/a | n/a | false | no near-duplicate watchlist entries |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build research-only strategy differentiation deep-dive artifacts.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_strategy_differentiation_deep_dive(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(json.dumps({"trade_date": payload["trade_date"], "pair_count": len(payload["pairwise"]), "watchlist_count": len(payload["retirement_watchlist"]), "reason_codes": payload["reason_codes"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
