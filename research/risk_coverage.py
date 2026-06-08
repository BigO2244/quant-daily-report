from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd

from core.strategy_registry import active_shadow_security_selection_ids

SCHEMA_VERSION = "caerus_risk_coverage_v1"
STRATEGY_NAMES = tuple(reversed(active_shadow_security_selection_ids()))


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


def _symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:
        return None
    return out


def _round(value: Any, digits: int = 10) -> float | None:
    value = _safe_float(value)
    return round(value, digits) if value is not None else None


def _weight(row: dict[str, Any]) -> float | None:
    for key in ("target_weight", "weight", "weight_start", "allocation_weight"):
        value = _safe_float(row.get(key))
        if value is not None:
            return value
    return None


def _load_universe_sectors(repo: Path) -> tuple[dict[str, str], list[str], list[str]]:
    path = repo / "data" / "universe.csv"
    if not path.exists():
        return {}, [], ["sector_lookup_missing"]
    sectors: dict[str, str] = {}
    try:
        reader = csv.DictReader(path.read_text(encoding="utf-8").splitlines())
        for row in reader:
            symbol = _symbol(row.get("ticker") or row.get("symbol"))
            sector = str(row.get("sector") or "").strip()
            if symbol and sector:
                sectors[symbol] = sector
    except Exception:
        return {}, [str(path)], ["sector_lookup_parse_error"]
    return sectors, [str(path)], [] if sectors else ["sector_lookup_empty"]


def _normalize_holdings(
    *,
    strategy: str,
    payload: dict[str, Any],
    source_artifact: str,
    source_date: str,
    sector_lookup: dict[str, str],
) -> list[dict[str, Any]]:
    rows = payload.get("holdings") if isinstance(payload.get("holdings"), list) else []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = _symbol(row.get("ticker") or row.get("symbol"))
        if not symbol or symbol == "CASH":
            continue
        sector = str(row.get("sector") or sector_lookup.get(symbol) or "").strip() or None
        out.append(
            {
                "strategy": strategy,
                "symbol": symbol,
                "weight": _weight(row),
                "sector": sector,
                "source_date": source_date,
                "source_artifacts": [source_artifact],
            }
        )
    target_weights = payload.get("target_weights")
    if not out and isinstance(target_weights, dict):
        for raw_symbol, raw_weight in sorted(target_weights.items()):
            symbol = _symbol(raw_symbol)
            if not symbol or symbol == "CASH":
                continue
            out.append(
                {
                    "strategy": strategy,
                    "symbol": symbol,
                    "weight": _safe_float(raw_weight),
                    "sector": sector_lookup.get(symbol),
                    "source_date": source_date,
                    "source_artifacts": [source_artifact],
                }
            )
    return out


def _load_portfolio_holdings(repo: Path, trade_date: str, sector_lookup: dict[str, str]) -> tuple[list[dict[str, Any]], list[str]]:
    path = repo / "outputs" / "portfolio_history" / trade_date / "holdings_snapshot.json"
    payload = _read_json(path)
    if payload is None:
        return [], []
    rows: list[dict[str, Any]] = []
    strategies = payload.get("strategies")
    if isinstance(strategies, dict):
        for strategy, strategy_payload in sorted(strategies.items()):
            if isinstance(strategy_payload, dict):
                rows.extend(
                    _normalize_holdings(
                        strategy=str(strategy),
                        payload=strategy_payload,
                        source_artifact=str(path),
                        source_date=trade_date,
                        sector_lookup=sector_lookup,
                    )
                )
    return rows, [str(path)]


def _shadow_dates(repo: Path, trade_date: str) -> list[str]:
    root = repo / "outputs" / "shadow_candidates"
    if not root.exists():
        return []
    dates: list[str] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            pd.Timestamp(child.name)
        except Exception:
            continue
        if child.name <= trade_date:
            dates.append(child.name)
    return sorted(dates, reverse=True)


def _load_shadow_holdings_for_date(repo: Path, source_date: str, sector_lookup: dict[str, str]) -> tuple[list[dict[str, Any]], list[str]]:
    root = repo / "outputs" / "shadow_candidates" / source_date
    rows: list[dict[str, Any]] = []
    sources: list[str] = []
    comparison_path = root / "comparison.json"
    comparison = _read_json(comparison_path)
    if comparison is not None and isinstance(comparison.get("strategies"), dict):
        for strategy, payload in sorted(comparison["strategies"].items()):
            if isinstance(payload, dict):
                rows.extend(
                    _normalize_holdings(
                        strategy=str(strategy),
                        payload=payload,
                        source_artifact=str(comparison_path),
                        source_date=source_date,
                        sector_lookup=sector_lookup,
                    )
                )
        sources.append(str(comparison_path))
    for strategy in STRATEGY_NAMES:
        path = root / f"{strategy}.json"
        payload = _read_json(path)
        if payload is None:
            continue
        existing = {str(row.get("strategy")) for row in rows}
        if strategy in existing:
            continue
        rows.extend(
            _normalize_holdings(
                strategy=str(payload.get("strategy_slug") or strategy),
                payload=payload,
                source_artifact=str(path),
                source_date=source_date,
                sector_lookup=sector_lookup,
            )
        )
        sources.append(str(path))
    return rows, sources


def _load_position_attribution_holdings_for_date(repo: Path, source_date: str, sector_lookup: dict[str, str]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    path = repo / "outputs" / "attribution" / source_date / "position_attribution.json"
    payload = _read_json(path)
    if payload is None:
        return [], [], []
    positions = payload.get("positions")
    if not isinstance(positions, list):
        return [], [str(path)], ["attribution_positions_invalid"]
    rows: list[dict[str, Any]] = []
    for row in positions:
        if not isinstance(row, dict):
            continue
        strategy = str(row.get("strategy") or row.get("strategy_id") or row.get("strategy_slug") or "").strip()
        symbol = _symbol(row.get("symbol") or row.get("ticker"))
        if not strategy or not symbol or symbol == "CASH":
            continue
        rows.append(
            {
                "strategy": strategy,
                "symbol": symbol,
                "weight": _weight(row),
                "sector": str(row.get("sector") or sector_lookup.get(symbol) or "").strip() or None,
                "source_date": source_date,
                "source_artifacts": sorted(set(list(row.get("source_artifacts") or []) + [str(path)])),
            }
        )
    return rows, [str(path)], [] if rows else ["attribution_positions_empty"]


def _attribution_dates(repo: Path, trade_date: str) -> list[str]:
    root = repo / "outputs" / "attribution"
    if not root.exists():
        return []
    dates: list[str] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            pd.Timestamp(child.name)
        except Exception:
            continue
        if child.name <= trade_date and (child / "position_attribution.json").exists():
            dates.append(child.name)
    return sorted(dates, reverse=True)


def load_holdings_for_risk_coverage(repo: Path, trade_date: str) -> tuple[list[dict[str, Any]], list[str], list[str], str | None]:
    sector_lookup, sector_sources, sector_reasons = _load_universe_sectors(repo)
    reasons: list[str] = list(sector_reasons)
    rows, sources = _load_portfolio_holdings(repo, trade_date, sector_lookup)
    if rows:
        return rows, sorted(set(sources + sector_sources)), sorted(set(reasons)) or ["ok"], trade_date

    for source_date in _shadow_dates(repo, trade_date):
        rows, sources = _load_shadow_holdings_for_date(repo, source_date, sector_lookup)
        if rows:
            if source_date != trade_date:
                reasons.append("holdings_source_date_differs_from_target")
            return rows, sorted(set(sources + sector_sources)), sorted(set(reasons)) or ["ok"], source_date

    attribution_reasons: list[str] = []
    attribution_sources: list[str] = []
    for source_date in _attribution_dates(repo, trade_date):
        rows, sources, row_reasons = _load_position_attribution_holdings_for_date(repo, source_date, sector_lookup)
        attribution_sources.extend(sources)
        attribution_reasons.extend(row_reasons)
        if rows:
            if source_date != trade_date:
                reasons.append("holdings_source_date_differs_from_target")
            return rows, sorted(set(sources + sector_sources)), sorted(set(reasons + row_reasons)) or ["ok"], source_date

    if attribution_sources:
        reasons.extend(attribution_reasons)
    reasons.extend(["holdings_source_missing", "no_holdings"])
    return [], sorted(set(attribution_sources + sector_sources)), sorted(set(reasons)), None


def _load_factor_sources(repo: Path, trade_date: str) -> tuple[dict[str, Any], list[str], list[str]]:
    candidates = [
        repo / "outputs" / "attribution" / trade_date / "factor_exposure.json",
        repo / "outputs" / "risk_summary" / trade_date / "risk_summary.json",
    ]
    attribution_root = repo / "outputs" / "attribution"
    if attribution_root.exists():
        for child in sorted(attribution_root.iterdir(), key=lambda p: p.name, reverse=True):
            if child.is_dir() and child.name <= trade_date and child.name != trade_date:
                candidates.append(child / "factor_exposure.json")
    root = repo / "outputs" / "risk_summary"
    if root.exists():
        for child in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
            if child.is_dir() and child.name <= trade_date and child.name != trade_date:
                candidates.append(child / "risk_summary.json")
    for path in candidates:
        payload = _read_json(path)
        if payload is None:
            continue
        strategies = payload.get("strategies")
        if isinstance(strategies, dict) and strategies:
            reasons: list[str] = []
            source_date = str(payload.get("date") or path.parent.name)
            if source_date != trade_date:
                reasons.append("factor_source_date_differs_from_target")
            if "risk_summary" in path.parts:
                reasons.append("factor_source_risk_summary")
            return strategies, [str(path)], sorted(set(reasons))
    return {}, [], ["factor_exposure_missing"]


def _risk_level(*, position_count: int, max_weight: float | None, top3: float | None, top5: float | None, gross: float | None, max_sector: float | None) -> str:
    if position_count <= 0:
        return "UNKNOWN"
    max_weight = float(max_weight or 0.0)
    top3 = float(top3 or 0.0)
    top5 = float(top5 or 0.0)
    gross = float(gross or 0.0)
    max_sector = float(max_sector or 0.0)
    if max_weight >= 0.25 or top3 >= 0.65 or top5 >= 0.90 or gross > 1.05 or max_sector >= 0.65:
        return "HIGH"
    if max_weight >= 0.15 or top3 >= 0.45 or top5 >= 0.75 or max_sector >= 0.45:
        return "MEDIUM"
    return "LOW"


def _factor_concentration(factor_payload: dict[str, Any]) -> dict[str, Any]:
    numeric = {
        str(key): abs(float(value))
        for key, value in sorted(factor_payload.items())
        if _safe_float(value) is not None
    }
    sector = factor_payload.get("sector_exposure") if isinstance(factor_payload.get("sector_exposure"), dict) else {}
    sector_weights = sector.get("weights") if isinstance(sector.get("weights"), dict) else {}
    return {
        "max_numeric_factor_abs": _round(max(numeric.values(), default=None)),
        "numeric_factor_count": len(numeric),
        "max_sector_weight_from_factor_source": _round(max([_safe_float(v) or 0.0 for v in sector_weights.values()], default=None)) if sector_weights else None,
    }


def _strategy_row(strategy: str, rows: list[dict[str, Any]], factors: dict[str, Any]) -> dict[str, Any]:
    weights = [_safe_float(row.get("weight")) for row in rows]
    missing_weights = sum(1 for value in weights if value is None)
    numeric = sorted([float(value) for value in weights if value is not None], reverse=True)
    sectors: dict[str, float] = {}
    missing_sector = 0
    for row in rows:
        sector = str(row.get("sector") or "").strip()
        weight = _safe_float(row.get("weight")) or 0.0
        if not sector:
            missing_sector += 1
            continue
        sectors[sector] = sectors.get(sector, 0.0) + weight
    sectors = {sector: _round(weight) for sector, weight in sorted(sectors.items())}
    gross = sum(abs(value) for value in numeric) if numeric else None
    net = sum(numeric) if numeric else None
    top3 = sum(numeric[:3]) if numeric else None
    top5 = sum(numeric[:5]) if numeric else None
    top10 = sum(numeric[:10]) if numeric else None
    max_sector = max([float(value or 0.0) for value in sectors.values()], default=None)
    factor_payload = factors.get(strategy) if isinstance(factors.get(strategy), dict) else {}
    risk_level = _risk_level(
        position_count=len(rows),
        max_weight=max(numeric) if numeric else None,
        top3=top3,
        top5=top5,
        gross=gross,
        max_sector=max_sector,
    )
    reasons: list[str] = []
    if not rows:
        reasons.append("no_holdings")
    if missing_weights:
        reasons.append("missing_position_weights")
    if missing_sector:
        reasons.append("missing_sector_coverage")
    if not factor_payload:
        reasons.append("factor_exposure_missing")
    return {
        "strategy": strategy,
        "available": bool(rows) and missing_weights == 0,
        "position_count": len(rows),
        "gross_exposure": _round(gross),
        "net_exposure": _round(net),
        "cash_unallocated": _round(max(0.0, 1.0 - float(net or 0.0))) if net is not None else None,
        "max_single_name_weight": _round(max(numeric) if numeric else None),
        "top3_concentration": _round(top3),
        "top5_concentration": _round(top5),
        "top10_concentration": _round(top10),
        "sector_exposure": sectors,
        "sector_concentration": _round(max_sector),
        "missing_sector_coverage_count": missing_sector,
        "factor_concentration": _factor_concentration(factor_payload) if factor_payload else {},
        "risk_level": risk_level,
        "confidence": "LOW" if not rows or missing_weights else "MEDIUM" if missing_sector or not factor_payload else "HIGH",
        "top_holdings": [
            {"symbol": row.get("symbol"), "weight": _round(row.get("weight")), "sector": row.get("sector")}
            for row in sorted(rows, key=lambda item: (-float(item.get("weight") or 0.0), str(item.get("symbol") or "")))[:10]
        ],
        "reason_codes": sorted(set(reasons)) or ["ok"],
    }


def build_risk_coverage(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root)
    holdings, holding_sources, holding_reasons, holdings_source_date = load_holdings_for_risk_coverage(repo, trade_date)
    factors, factor_sources, factor_reasons = _load_factor_sources(repo, trade_date)
    by_strategy: dict[str, list[dict[str, Any]]] = {}
    for row in holdings:
        strategy = str(row.get("strategy") or "")
        if strategy:
            by_strategy.setdefault(strategy, []).append(row)
    strategies = {
        strategy: _strategy_row(strategy, sorted(rows, key=lambda item: str(item.get("symbol") or "")), factors)
        for strategy, rows in sorted(by_strategy.items())
    }
    position_count = sum(int(row.get("position_count") or 0) for row in strategies.values())
    available = position_count > 0 and bool(strategies) and all(bool(row.get("available")) for row in strategies.values())
    reason_codes = sorted(
        {
            str(code)
            for code in holding_reasons + factor_reasons + [code for row in strategies.values() for code in list(row.get("reason_codes") or [])]
            if code != "ok"
        }
    ) or ["ok"]
    if not available and "risk_coverage_unavailable" not in reason_codes:
        reason_codes.append("risk_coverage_unavailable")
    confidence = "LOW"
    if available:
        confidence = "MEDIUM" if any(code != "ok" for code in reason_codes) else "HIGH"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "available": available,
        "confidence": confidence,
        "holdings_source_date": holdings_source_date,
        "strategies_covered": sorted(strategies),
        "position_count": position_count,
        "gross_exposure": _round(max([_safe_float(row.get("gross_exposure")) or 0.0 for row in strategies.values()], default=None)),
        "net_exposure": _round(max([_safe_float(row.get("net_exposure")) or 0.0 for row in strategies.values()], default=None)),
        "top3_concentration": _round(max([_safe_float(row.get("top3_concentration")) or 0.0 for row in strategies.values()], default=None)),
        "top5_concentration": _round(max([_safe_float(row.get("top5_concentration")) or 0.0 for row in strategies.values()], default=None)),
        "top10_concentration": _round(max([_safe_float(row.get("top10_concentration")) or 0.0 for row in strategies.values()], default=None)),
        "max_single_name_weight": _round(max([_safe_float(row.get("max_single_name_weight")) or 0.0 for row in strategies.values()], default=None)),
        "risk_level": max([str(row.get("risk_level") or "UNKNOWN") for row in strategies.values()], default="UNKNOWN", key={"UNKNOWN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}.get),
        "reason_codes": sorted(set(reason_codes)),
        "source_artifacts": sorted(set(holding_sources + factor_sources)),
        "strategies": strategies,
    }
    out_dir = (Path(output_root) if output_root is not None else repo / "outputs" / "research" / "risk_coverage") / trade_date
    _write_json(out_dir / "risk_coverage.json", payload)
    _write_text(out_dir / "risk_coverage.md", render_markdown(payload))
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Risk Coverage - {payload.get('date')}",
        "",
        f"- Available: {payload.get('available')}",
        f"- Confidence: {payload.get('confidence')}",
        f"- Risk level: {payload.get('risk_level')}",
        f"- Holdings source date: {payload.get('holdings_source_date')}",
        f"- Reason codes: {', '.join(payload.get('reason_codes') or [])}",
        "",
        "| Strategy | Positions | Gross | Net | Top 3 | Top 5 | Top 10 | Max Name | Risk | Confidence | Reasons |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for strategy, row in sorted((payload.get("strategies") or {}).items()):
        lines.append(
            f"| {strategy} | {row.get('position_count')} | {row.get('gross_exposure')} | {row.get('net_exposure')} | {row.get('top3_concentration')} | {row.get('top5_concentration')} | {row.get('top10_concentration')} | {row.get('max_single_name_weight')} | {row.get('risk_level')} | {row.get('confidence')} | {', '.join(row.get('reason_codes') or [])} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build read-only Tier 2 risk coverage artifacts.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_risk_coverage(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(json.dumps({"date": args.date, "available": payload["available"], "confidence": payload["confidence"], "reason_codes": payload["reason_codes"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
