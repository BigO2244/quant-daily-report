from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from research_registry.research.model_quality_common import (
    collect_reason_codes,
    md_join,
    model_quality_dir,
    normalize_date,
    read_json,
    round_or_none,
    symbol,
    write_json,
    write_text,
)

SCHEMA_VERSION = "caerus_lyra_orion_differentiation_v1"
LYRA = "caerus_lyra"
ORION = "caerus_orion"


def build_lyra_orion_differentiation(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
    write: bool = True,
) -> dict[str, Any]:
    target = normalize_date(trade_date)
    repo = Path(repo_root)
    lyra_snapshot, lyra_source = _load_strategy_snapshot(repo=repo, target=target, strategy=LYRA)
    orion_snapshot, orion_source = _load_strategy_snapshot(repo=repo, target=target, strategy=ORION)
    sector_map = _load_sector_map(repo / "data" / "universe.csv")

    holdings = _holdings_comparison(lyra_snapshot, orion_snapshot, sector_map=sector_map)
    sector_diff = _sector_comparison(holdings)
    turnover = _turnover_comparison(repo=repo, target=target, lyra_snapshot=lyra_snapshot, orion_snapshot=orion_snapshot)
    concentration = _concentration_comparison(lyra_snapshot, orion_snapshot)
    performance = _performance_spread(repo=repo, target=target)
    attribution = _attribution_spread(repo=repo, target=target)
    decisions = _decision_spread(repo=repo, target=target)
    regime = _regime_spread(repo=repo, target=target)
    source_statuses = [lyra_source, orion_source]
    reason_codes = set(collect_reason_codes(
        lyra_source.get("reason_codes") or [],
        orion_source.get("reason_codes") or [],
        holdings.get("reason_codes") or [],
        sector_diff.get("reason_codes") or [],
        turnover.get("reason_codes") or [],
        performance.get("reason_codes") or [],
        attribution.get("reason_codes") or [],
        decisions.get("reason_codes") or [],
        regime.get("reason_codes") or [],
    ))
    reason_codes.discard("ok")
    reason_codes.add("EXPLANATORY_STUDY_NOT_PROMOTION_RECOMMENDATION")

    evidence = _evidence_sufficiency(performance=performance, holdings=holdings, reason_codes=reason_codes)
    hypotheses = _hypotheses(
        holdings=holdings,
        attribution=attribution,
        sector_diff=sector_diff,
        turnover=turnover,
        performance=performance,
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": target,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "status": "OK" if lyra_snapshot and orion_snapshot else "PARTIAL",
        "observed_facts_only": True,
        "strategies": {"leader": LYRA, "challenger": ORION},
        "source_statuses": source_statuses,
        "executive_summary": _summary(performance=performance, holdings=holdings, attribution=attribution, evidence=evidence),
        "performance_spread": performance,
        "holdings_overlap_difference": holdings,
        "sector_exposure_difference": sector_diff,
        "turnover_difference": turnover,
        "concentration_difference": concentration,
        "attribution_by_symbol": attribution,
        "attribution_by_decision": decisions,
        "regime_specific_behavior": regime,
        "evidence_sufficiency": evidence,
        "hypotheses_explaining_lyra_outperformance": hypotheses,
        "next_evidence_required": _next_evidence(reason_codes=reason_codes, evidence=evidence),
        "decision_grade_flag": False,
        "reason_codes": sorted(reason_codes) or ["ok"],
    }
    if write:
        out_dir = model_quality_dir(repo, target, output_root)
        write_json(out_dir / "lyra_orion_differentiation.json", payload)
        write_text(out_dir / "lyra_orion_differentiation.md", render_markdown(payload))
    return payload


def _load_strategy_snapshot(*, repo: Path, target: str, strategy: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    root = repo / "outputs" / "shadow_candidates"
    exact = root / target / f"{strategy}.json"
    if exact.exists():
        payload = read_json(exact)
        reasons = ["ok"] if payload is not None else ["SNAPSHOT_PARSE_ERROR"]
        if payload and str(payload.get("effective_trade_date") or payload.get("trade_date") or "")[:10] < target:
            reasons.append(f"STALE_{strategy.upper()}_SNAPSHOT")
        return payload, {
            "name": strategy,
            "status": "PRESENT" if payload is not None else "MALFORMED",
            "path": str(exact),
            "source_date": target,
            "target_date": target,
            "reason_codes": sorted(set(reasons)),
        }
    candidates: list[Path] = []
    if root.exists():
        for child in root.iterdir():
            if not child.is_dir():
                continue
            try:
                child_date = normalize_date(child.name)
            except Exception:
                continue
            path = child / f"{strategy}.json"
            if child_date <= target and path.exists():
                candidates.append(path)
    if candidates:
        selected = sorted(candidates, key=lambda path: path.parent.name)[-1]
        payload = read_json(selected)
        return payload, {
            "name": strategy,
            "status": "STALE" if payload is not None else "MALFORMED",
            "path": str(selected),
            "source_date": selected.parent.name,
            "target_date": target,
            "reason_codes": ["SOURCE_DATE_DIFFERS_FROM_TARGET"] if payload is not None else ["SNAPSHOT_PARSE_ERROR"],
        }
    return None, {
        "name": strategy,
        "status": "MISSING",
        "path": None,
        "source_date": None,
        "target_date": target,
        "reason_codes": [f"{strategy.upper()}_SNAPSHOT_MISSING"],
    }


def _load_sector_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(line for line in handle if line.strip())
            for row in reader:
                ticker = symbol(row.get("ticker") or row.get("symbol"))
                sector = str(row.get("sector") or "").strip()
                if ticker:
                    out[ticker] = sector or "UNKNOWN"
    except Exception:
        return {}
    return out


def _holdings_from_snapshot(snapshot: dict[str, Any] | None, *, sector_map: dict[str, str]) -> dict[str, dict[str, Any]]:
    if not snapshot:
        return {}
    out: dict[str, dict[str, Any]] = {}
    weights = snapshot.get("target_weights") if isinstance(snapshot.get("target_weights"), dict) else {}
    holdings = snapshot.get("holdings") if isinstance(snapshot.get("holdings"), list) else []
    for row in holdings:
        if not isinstance(row, dict):
            continue
        ticker = symbol(row.get("ticker") or row.get("symbol"))
        if not ticker:
            continue
        weight = round_or_none(row.get("target_weight") if row.get("target_weight") is not None else weights.get(ticker), 10)
        out[ticker] = {
            "ticker": ticker,
            "target_weight": weight if weight is not None else 0.0,
            "sector": str(row.get("sector") or sector_map.get(ticker) or "UNKNOWN"),
            "momentum_rank": round_or_none(row.get("momentum_rank"), 6),
            "momentum_score": round_or_none(row.get("momentum_score"), 10),
            "estimated_holding_period_days": round_or_none(row.get("estimated_holding_period_days"), 6),
        }
    for ticker_raw, weight_raw in weights.items():
        ticker = symbol(ticker_raw)
        if ticker and ticker not in out:
            out[ticker] = {
                "ticker": ticker,
                "target_weight": round_or_none(weight_raw, 10) or 0.0,
                "sector": sector_map.get(ticker) or "UNKNOWN",
                "momentum_rank": None,
                "momentum_score": None,
                "estimated_holding_period_days": None,
            }
    return out


def _holdings_comparison(
    lyra_snapshot: dict[str, Any] | None,
    orion_snapshot: dict[str, Any] | None,
    *,
    sector_map: dict[str, str],
) -> dict[str, Any]:
    lyra = _holdings_from_snapshot(lyra_snapshot, sector_map=sector_map)
    orion = _holdings_from_snapshot(orion_snapshot, sector_map=sector_map)
    lyra_symbols = set(lyra)
    orion_symbols = set(orion)
    union = sorted(lyra_symbols | orion_symbols)
    common = sorted(lyra_symbols & orion_symbols)
    differences = []
    active_share = 0.0
    for ticker in union:
        lyra_weight = float((lyra.get(ticker) or {}).get("target_weight") or 0.0)
        orion_weight = float((orion.get(ticker) or {}).get("target_weight") or 0.0)
        delta = round(lyra_weight - orion_weight, 10)
        active_share += abs(delta)
        differences.append(
            {
                "ticker": ticker,
                "sector": (lyra.get(ticker) or orion.get(ticker) or {}).get("sector") or "UNKNOWN",
                "lyra_weight": round(lyra_weight, 10),
                "orion_weight": round(orion_weight, 10),
                "weight_delta_lyra_minus_orion": delta,
                "lyra_rank": (lyra.get(ticker) or {}).get("momentum_rank"),
                "orion_rank": (orion.get(ticker) or {}).get("momentum_rank"),
            }
        )
    reasons = []
    if not lyra:
        reasons.append("LYRA_HOLDINGS_MISSING")
    if not orion:
        reasons.append("ORION_HOLDINGS_MISSING")
    if lyra and orion and not common:
        reasons.append("NO_HOLDINGS_OVERLAP")
    if lyra and orion and lyra == orion:
        reasons.append("IDENTICAL_STRATEGY_HOLDINGS")
    return {
        "lyra_holdings_count": len(lyra),
        "orion_holdings_count": len(orion),
        "common_symbols": common,
        "lyra_only_symbols": sorted(lyra_symbols - orion_symbols),
        "orion_only_symbols": sorted(orion_symbols - lyra_symbols),
        "overlap_ratio": round(len(common) / max(1, len(union)), 10),
        "active_share": round(active_share / 2.0, 10),
        "weight_differences": sorted(differences, key=lambda row: (-abs(row["weight_delta_lyra_minus_orion"]), row["ticker"])),
        "lyra_holdings": [lyra[ticker] for ticker in sorted(lyra)],
        "orion_holdings": [orion[ticker] for ticker in sorted(orion)],
        "reason_codes": sorted(set(reasons)) or ["ok"],
    }


def _sector_comparison(holdings: dict[str, Any]) -> dict[str, Any]:
    exposures: dict[str, dict[str, float]] = defaultdict(lambda: {"lyra": 0.0, "orion": 0.0})
    unknown = False
    for row in holdings.get("weight_differences") or []:
        sector = str(row.get("sector") or "UNKNOWN")
        if sector == "UNKNOWN":
            unknown = True
        exposures[sector]["lyra"] += float(row.get("lyra_weight") or 0.0)
        exposures[sector]["orion"] += float(row.get("orion_weight") or 0.0)
    rows = []
    for sector, values in sorted(exposures.items()):
        rows.append(
            {
                "sector": sector,
                "lyra_weight": round(values["lyra"], 10),
                "orion_weight": round(values["orion"], 10),
                "weight_delta_lyra_minus_orion": round(values["lyra"] - values["orion"], 10),
            }
        )
    return {
        "available": bool(rows) and not unknown,
        "sector_differences": sorted(rows, key=lambda row: (-abs(row["weight_delta_lyra_minus_orion"]), row["sector"])),
        "reason_codes": ["SECTOR_DATA_MISSING"] if unknown or not rows else ["ok"],
    }


def _turnover_comparison(*, repo: Path, target: str, lyra_snapshot: dict[str, Any] | None, orion_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    lyra_prev = _previous_snapshot(repo=repo, target=target, strategy=LYRA)
    orion_prev = _previous_snapshot(repo=repo, target=target, strategy=ORION)
    lyra_turnover = _snapshot_turnover(lyra_snapshot, lyra_prev)
    orion_turnover = _snapshot_turnover(orion_snapshot, orion_prev)
    reasons = []
    if lyra_prev is None:
        reasons.append("LYRA_PRIOR_SNAPSHOT_MISSING")
    if orion_prev is None:
        reasons.append("ORION_PRIOR_SNAPSHOT_MISSING")
    return {
        "lyra_expected_turnover": round_or_none((lyra_snapshot or {}).get("expected_turnover"), 10),
        "orion_expected_turnover": round_or_none((orion_snapshot or {}).get("expected_turnover"), 10),
        "lyra_observed_weight_turnover_vs_prior": lyra_turnover,
        "orion_observed_weight_turnover_vs_prior": orion_turnover,
        "turnover_delta_lyra_minus_orion": round_or_none(
            lyra_turnover - orion_turnover if lyra_turnover is not None and orion_turnover is not None else None,
            10,
        ),
        "reason_codes": sorted(set(reasons)) or ["ok"],
    }


def _previous_snapshot(*, repo: Path, target: str, strategy: str) -> dict[str, Any] | None:
    root = repo / "outputs" / "shadow_candidates"
    candidates: list[Path] = []
    if root.exists():
        for child in root.iterdir():
            if not child.is_dir():
                continue
            try:
                child_date = normalize_date(child.name)
            except Exception:
                continue
            path = child / f"{strategy}.json"
            if child_date < target and path.exists():
                candidates.append(path)
    if not candidates:
        return None
    return read_json(sorted(candidates, key=lambda path: path.parent.name)[-1])


def _snapshot_turnover(current: dict[str, Any] | None, prior: dict[str, Any] | None) -> float | None:
    if not current or not prior:
        return None
    current_weights = {symbol(k): float(v or 0.0) for k, v in ((current.get("target_weights") or {}).items())}
    prior_weights = {symbol(k): float(v or 0.0) for k, v in ((prior.get("target_weights") or {}).items())}
    union = sorted(set(current_weights) | set(prior_weights))
    if not union:
        return None
    return round(sum(abs(current_weights.get(ticker, 0.0) - prior_weights.get(ticker, 0.0)) for ticker in union) / 2.0, 10)


def _concentration_comparison(lyra_snapshot: dict[str, Any] | None, orion_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    lyra = _concentration(lyra_snapshot)
    orion = _concentration(orion_snapshot)
    return {
        "lyra": lyra,
        "orion": orion,
        "top3_concentration_delta_lyra_minus_orion": round_or_none(
            (lyra.get("top3_concentration") or 0.0) - (orion.get("top3_concentration") or 0.0),
            10,
        ),
        "hhi_delta_lyra_minus_orion": round_or_none((lyra.get("hhi") or 0.0) - (orion.get("hhi") or 0.0), 10),
        "reason_codes": ["ok"] if lyra.get("holdings_count") and orion.get("holdings_count") else ["CONCENTRATION_INPUT_MISSING"],
    }


def _concentration(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    weights = [float(value or 0.0) for value in ((snapshot or {}).get("target_weights") or {}).values()]
    weights = sorted(weights, reverse=True)
    return {
        "holdings_count": len(weights),
        "max_weight": round(weights[0], 10) if weights else None,
        "top3_concentration": round(sum(weights[:3]), 10) if weights else None,
        "hhi": round(sum(weight * weight for weight in weights), 10) if weights else None,
    }


def _performance_spread(*, repo: Path, target: str) -> dict[str, Any]:
    tournament = read_json(repo / "outputs" / "model_quality" / target / "model_tournament.json") or {}
    strategies = {
        row.get("strategy"): row
        for row in (tournament.get("strategies") or [])
        if isinstance(row, dict) and row.get("strategy") in {LYRA, ORION}
    }
    shadow_eval = read_json(repo / "outputs" / "shadow_candidates" / target / "shadow_evaluation.json") or {}
    shadow_strategies = shadow_eval.get("strategies") or {}
    lyra_metrics = dict((strategies.get(LYRA) or {}).get("metrics") or {})
    orion_metrics = dict((strategies.get(ORION) or {}).get("metrics") or {})
    current_lyra = shadow_strategies.get(LYRA) or {}
    current_orion = shadow_strategies.get(ORION) or {}
    fields = ("total_return", "excess_return_vs_spy", "hit_rate", "max_drawdown", "volatility", "turnover", "coverage_days")
    spreads = {
        field: round_or_none(
            (lyra_metrics.get(field) if lyra_metrics.get(field) is not None else 0.0)
            - (orion_metrics.get(field) if orion_metrics.get(field) is not None else 0.0),
            10,
        )
        for field in fields
        if lyra_metrics.get(field) is not None and orion_metrics.get(field) is not None
    }
    if current_lyra and current_orion:
        spreads["current_day_return"] = round_or_none(
            float(current_lyra.get("daily_return") or 0.0) - float(current_orion.get("daily_return") or 0.0),
            10,
        )
        spreads["current_day_excess_vs_spy"] = round_or_none(
            float(current_lyra.get("excess_return_vs_spy") or 0.0) - float(current_orion.get("excess_return_vs_spy") or 0.0),
            10,
        )
    reasons = []
    if not lyra_metrics:
        reasons.append("LYRA_TOURNAMENT_METRICS_MISSING")
    if not orion_metrics:
        reasons.append("ORION_TOURNAMENT_METRICS_MISSING")
    if not current_lyra:
        reasons.append("LYRA_CURRENT_SHADOW_EVAL_MISSING")
    if not current_orion:
        reasons.append("ORION_CURRENT_SHADOW_EVAL_MISSING")
    return {
        "source": str(repo / "outputs" / "model_quality" / target / "model_tournament.json"),
        "lyra_metrics": lyra_metrics,
        "orion_metrics": orion_metrics,
        "spread_lyra_minus_orion": spreads,
        "current_day": {
            "lyra": _selected_current_fields(current_lyra),
            "orion": _selected_current_fields(current_orion),
        },
        "reason_codes": sorted(set(reasons)) or ["ok"],
    }


def _selected_current_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in ("daily_return", "cumulative_return", "excess_return_vs_spy", "avg_turnover", "avg_top_3_concentration", "status", "data_status")
    }


def _attribution_spread(*, repo: Path, target: str) -> dict[str, Any]:
    payload = read_json(repo / "outputs" / "attribution" / target / "position_attribution.json") or {}
    positions = payload.get("positions") if isinstance(payload.get("positions"), list) else []
    return _spread_rows(
        rows=positions,
        value_keys=("pnl_contribution_pct", "pnl_contribution"),
        source=str(repo / "outputs" / "attribution" / target / "position_attribution.json"),
        missing_reason="POSITION_ATTRIBUTION_MISSING",
    )


def _decision_spread(*, repo: Path, target: str) -> dict[str, Any]:
    payload = read_json(repo / "outputs" / "decision_attribution" / target / "decision_attribution.json") or {}
    rows = payload.get("decisions") if isinstance(payload.get("decisions"), list) else []
    return _spread_rows(
        rows=rows,
        value_keys=("pnl_contribution", "pnl_contribution_pct"),
        source=str(repo / "outputs" / "decision_attribution" / target / "decision_attribution.json"),
        missing_reason="DECISION_ATTRIBUTION_MISSING",
    )


def _spread_rows(*, rows: list[Any], value_keys: tuple[str, ...], source: str, missing_reason: str) -> dict[str, Any]:
    by_strategy: dict[str, dict[str, float]] = {LYRA: defaultdict(float), ORION: defaultdict(float)}
    reason_codes = []
    if not rows:
        reason_codes.append(missing_reason)
    for row in rows:
        if not isinstance(row, dict):
            continue
        strategy = str(row.get("strategy") or "")
        if strategy not in by_strategy:
            continue
        ticker = symbol(row.get("symbol") or row.get("ticker"))
        if not ticker:
            continue
        value = None
        for key in value_keys:
            if row.get(key) is not None:
                value = round_or_none(row.get(key), 10)
                break
        if value is not None:
            by_strategy[strategy][ticker] += float(value)
    union = sorted(set(by_strategy[LYRA]) | set(by_strategy[ORION]))
    spread = []
    for ticker in union:
        lyra_value = by_strategy[LYRA].get(ticker, 0.0)
        orion_value = by_strategy[ORION].get(ticker, 0.0)
        spread.append(
            {
                "symbol": ticker,
                "lyra_contribution": round(lyra_value, 10),
                "orion_contribution": round(orion_value, 10),
                "spread_lyra_minus_orion": round(lyra_value - orion_value, 10),
            }
        )
    if rows and not spread:
        reason_codes.append("LYRA_ORION_ROWS_MISSING")
    return {
        "source": source,
        "symbol_spread": sorted(spread, key=lambda row: (-abs(row["spread_lyra_minus_orion"]), row["symbol"])),
        "top_spread_drivers": sorted(spread, key=lambda row: (-abs(row["spread_lyra_minus_orion"]), row["symbol"]))[:10],
        "reason_codes": sorted(set(reason_codes)) or ["ok"],
    }


def _regime_spread(*, repo: Path, target: str) -> dict[str, Any]:
    tournament = read_json(repo / "outputs" / "model_quality" / target / "model_tournament.json") or {}
    strategies = {
        row.get("strategy"): row
        for row in (tournament.get("strategies") or [])
        if isinstance(row, dict) and row.get("strategy") in {LYRA, ORION}
    }
    lyra_regime = ((strategies.get(LYRA) or {}).get("metrics") or {}).get("regime_specific_return") or {}
    orion_regime = ((strategies.get(ORION) or {}).get("metrics") or {}).get("regime_specific_return") or {}
    regimes = sorted(set(lyra_regime) | set(orion_regime))
    rows = []
    for regime in regimes:
        lyra_total = round_or_none((lyra_regime.get(regime) or {}).get("total_return"), 10)
        orion_total = round_or_none((orion_regime.get(regime) or {}).get("total_return"), 10)
        rows.append(
            {
                "regime": regime,
                "lyra_total_return": lyra_total,
                "orion_total_return": orion_total,
                "spread_lyra_minus_orion": round_or_none(
                    lyra_total - orion_total if lyra_total is not None and orion_total is not None else None,
                    10,
                ),
                "lyra_observation_count": (lyra_regime.get(regime) or {}).get("observation_count"),
                "orion_observation_count": (orion_regime.get(regime) or {}).get("observation_count"),
            }
        )
    return {
        "regime_spread": sorted(rows, key=lambda row: (-abs(row.get("spread_lyra_minus_orion") or 0.0), row["regime"])),
        "reason_codes": ["ok"] if rows else ["REGIME_ATTRIBUTION_MISSING"],
    }


def _evidence_sufficiency(*, performance: dict[str, Any], holdings: dict[str, Any], reason_codes: set[str]) -> dict[str, Any]:
    lyra_coverage = ((performance.get("lyra_metrics") or {}).get("coverage_days"))
    orion_coverage = ((performance.get("orion_metrics") or {}).get("coverage_days"))
    min_coverage = min([value for value in (lyra_coverage, orion_coverage) if value is not None], default=0)
    blockers = []
    if min_coverage < 252:
        blockers.append("INSUFFICIENT_COVERAGE_DAYS")
    if holdings.get("active_share", 0.0) < 0.20:
        blockers.append("LOW_ACTIVE_SHARE")
    if "POSITION_ATTRIBUTION_MISSING" in reason_codes or "DECISION_ATTRIBUTION_MISSING" in reason_codes:
        blockers.append("ATTRIBUTION_EVIDENCE_INCOMPLETE")
    blockers.append("NO_PROMOTION_OR_TUNING_FROM_THIS_STUDY")
    return {
        "decision_grade": False,
        "confidence_level": "MEDIUM" if min_coverage >= 252 and len(blockers) <= 2 else "LOW",
        "coverage_days_min": min_coverage,
        "blockers": sorted(set(blockers)),
        "reason_codes": ["NO_DECISION_GRADE_RECOMMENDATION"],
    }


def _hypotheses(
    *,
    holdings: dict[str, Any],
    attribution: dict[str, Any],
    sector_diff: dict[str, Any],
    turnover: dict[str, Any],
    performance: dict[str, Any],
) -> list[dict[str, Any]]:
    hypotheses: list[dict[str, Any]] = []
    top_driver = (attribution.get("top_spread_drivers") or [None])[0]
    if top_driver:
        hypotheses.append(
            {
                "type": "observed_attribution_driver",
                "hypothesis": "Current observed spread is concentrated in the largest Lyra-minus-Orion symbol attribution driver.",
                "evidence": top_driver,
            }
        )
    if holdings.get("lyra_only_symbols") or holdings.get("orion_only_symbols"):
        hypotheses.append(
            {
                "type": "holding_substitution",
                "hypothesis": "Lyra and Orion share the same momentum core, but replacement-name selection explains the mechanical difference.",
                "evidence": {
                    "lyra_only_symbols": holdings.get("lyra_only_symbols"),
                    "orion_only_symbols": holdings.get("orion_only_symbols"),
                    "active_share": holdings.get("active_share"),
                },
            }
        )
    if (sector_diff.get("sector_differences") or []):
        hypotheses.append(
            {
                "type": "sector_tilt",
                "hypothesis": "Sector exposure differences may explain part of the spread when replacement names come from different sectors.",
                "evidence": (sector_diff.get("sector_differences") or [])[:5],
            }
        )
    if turnover.get("turnover_delta_lyra_minus_orion") is not None:
        hypotheses.append(
            {
                "type": "turnover_rule_difference",
                "hypothesis": "Turnover and holding-period rules differ, but this packet does not tune either rule.",
                "evidence": {
                    "turnover_delta_lyra_minus_orion": turnover.get("turnover_delta_lyra_minus_orion"),
                    "performance_turnover_spread": (performance.get("spread_lyra_minus_orion") or {}).get("turnover"),
                },
            }
        )
    return hypotheses or [
        {
            "type": "insufficient_inputs",
            "hypothesis": "Inputs are insufficient to explain Lyra versus Orion mechanically.",
            "evidence": {},
        }
    ]


def _summary(*, performance: dict[str, Any], holdings: dict[str, Any], attribution: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    current_spread = (performance.get("spread_lyra_minus_orion") or {}).get("current_day_return")
    top_driver = (attribution.get("top_spread_drivers") or [None])[0]
    return {
        "performance_spread_current_day_lyra_minus_orion": current_spread,
        "common_symbols": holdings.get("common_symbols") or [],
        "lyra_only_symbols": holdings.get("lyra_only_symbols") or [],
        "orion_only_symbols": holdings.get("orion_only_symbols") or [],
        "active_share": holdings.get("active_share"),
        "top_attribution_driver": top_driver,
        "decision_grade": evidence.get("decision_grade"),
    }


def _next_evidence(*, reason_codes: set[str], evidence: dict[str, Any]) -> list[str]:
    out = [
        "accumulate additional Lyra/Orion daily snapshots without changing either strategy",
        "track attribution spread by symbol and replacement-name decisions across regimes",
    ]
    if "SECTOR_DATA_MISSING" in reason_codes:
        out.append("add PIT-safe sector coverage before treating sector explanations as complete")
    if "ATTRIBUTION_EVIDENCE_INCOMPLETE" in evidence.get("blockers", []):
        out.append("refresh position and decision attribution inputs")
    return out


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("executive_summary") or {}
    evidence = payload.get("evidence_sufficiency") or {}
    lines = [
        f"# Lyra vs Orion Differentiation - {payload.get('date')}",
        "",
        "## Executive Summary",
        "",
        f"- Decision-grade: {payload.get('decision_grade_flag')}",
        f"- Confidence: {evidence.get('confidence_level')}",
        f"- Current-day spread Lyra minus Orion: {summary.get('performance_spread_current_day_lyra_minus_orion')}",
        f"- Active share: {summary.get('active_share')}",
        f"- Common symbols: {md_join(summary.get('common_symbols') or [])}",
        f"- Lyra-only symbols: {md_join(summary.get('lyra_only_symbols') or [])}",
        f"- Orion-only symbols: {md_join(summary.get('orion_only_symbols') or [])}",
        f"- Reason codes: {md_join(payload.get('reason_codes') or [])}",
        "",
        "## Holdings Overlap/Difference",
        "",
        "| Ticker | Sector | Lyra Weight | Orion Weight | Delta | Lyra Rank | Orion Rank |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in (payload.get("holdings_overlap_difference") or {}).get("weight_differences") or []:
        lines.append(
            f"| {row.get('ticker')} | {row.get('sector')} | {row.get('lyra_weight')} | {row.get('orion_weight')} | "
            f"{row.get('weight_delta_lyra_minus_orion')} | {row.get('lyra_rank')} | {row.get('orion_rank')} |"
        )
    lines.extend(["", "## Attribution By Symbol", "", "| Symbol | Lyra | Orion | Spread |", "|---|---:|---:|---:|"])
    for row in (payload.get("attribution_by_symbol") or {}).get("top_spread_drivers") or []:
        lines.append(
            f"| {row.get('symbol')} | {row.get('lyra_contribution')} | {row.get('orion_contribution')} | {row.get('spread_lyra_minus_orion')} |"
        )
    lines.extend(["", "## Hypotheses", ""])
    for row in payload.get("hypotheses_explaining_lyra_outperformance") or []:
        lines.append(f"- {row.get('type')}: {row.get('hypothesis')}")
    lines.extend(["", "## Next Evidence Required", ""])
    for item in payload.get("next_evidence_required") or []:
        lines.append(f"- {item}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare Lyra and Orion research-only mechanics.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_lyra_orion_differentiation(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(
        json.dumps(
            {
                "date": payload["date"],
                "status": payload["status"],
                "decision_grade_flag": payload["decision_grade_flag"],
                "reason_codes": payload["reason_codes"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
