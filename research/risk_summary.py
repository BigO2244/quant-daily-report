from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "caerus_risk_summary_v1"
STRATEGY_FILE_NAMES = {
    "caerus_polaris.json",
    "caerus_orion.json",
    "caerus_lyra.json",
}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def _symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _weight(row: dict[str, Any]) -> float | None:
    for key in ("target_weight", "weight", "weight_start", "allocation_weight"):
        value = _safe_float(row.get(key))
        if value is not None:
            return value
    return None


def _confidence(*, position_count: int, missing_weights: int, missing_sectors: int) -> str:
    if position_count <= 0 or missing_weights > 0:
        return "LOW"
    if missing_sectors > 0:
        return "MEDIUM"
    return "HIGH"


def _risk_level_from_concentration(
    *,
    position_count: int,
    max_weight: float | None,
    top3: float | None,
    top5: float | None,
) -> str:
    if position_count <= 0:
        return "UNKNOWN"
    max_weight = float(max_weight or 0.0)
    top3 = float(top3 or 0.0)
    top5 = float(top5 or 0.0)
    if max_weight >= 0.20 or top3 >= 0.60 or top5 >= 0.85:
        return "HIGH"
    if max_weight >= 0.10 or top3 >= 0.40 or top5 >= 0.65:
        return "MEDIUM"
    return "LOW"


def _risk_level_from_exposure(
    *,
    position_count: int,
    missing_sectors: int,
    max_sector_weight: float | None,
    market_beta: float | None,
) -> str:
    if position_count <= 0:
        return "UNKNOWN"
    max_sector_weight = float(max_sector_weight or 0.0)
    beta = _safe_float(market_beta)
    if missing_sectors >= position_count:
        return "UNKNOWN"
    if max_sector_weight >= 0.60 or (beta is not None and beta >= 1.50):
        return "HIGH"
    if max_sector_weight >= 0.40 or missing_sectors > 0 or (beta is not None and beta >= 1.10):
        return "MEDIUM"
    return "LOW"


def _load_universe_sectors(repo: Path) -> tuple[dict[str, str], list[str]]:
    path = repo / "data" / "universe.csv"
    if not path.exists():
        return {}, []
    sectors: dict[str, str] = {}
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        reader = csv.DictReader(lines)
        for row in reader:
            symbol = _symbol(row.get("ticker") or row.get("symbol"))
            sector = str(row.get("sector") or "").strip()
            if symbol and sector:
                sectors[symbol] = sector
    except Exception:
        return {}, []
    return sectors, [str(path)]


def _normalize_holdings(
    *,
    strategy: str,
    payload: dict[str, Any],
    source_artifact: str,
    sector_lookup: dict[str, str],
) -> list[dict[str, Any]]:
    rows = payload.get("holdings") or []
    holdings: list[dict[str, Any]] = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = _symbol(row.get("ticker") or row.get("symbol"))
            if not symbol or symbol == "CASH":
                continue
            sector = str(row.get("sector") or sector_lookup.get(symbol) or "").strip() or None
            holdings.append(
                {
                    "strategy": strategy,
                    "symbol": symbol,
                    "weight": _weight(row),
                    "sector": sector,
                    "source_artifacts": [source_artifact],
                }
            )

    if not holdings and isinstance(payload.get("target_weights"), dict):
        for raw_symbol, raw_weight in sorted(payload["target_weights"].items()):
            symbol = _symbol(raw_symbol)
            if not symbol or symbol == "CASH":
                continue
            holdings.append(
                {
                    "strategy": strategy,
                    "symbol": symbol,
                    "weight": _safe_float(raw_weight),
                    "sector": sector_lookup.get(symbol),
                    "source_artifacts": [source_artifact],
                }
            )
    return holdings


def _load_position_attribution_holdings(
    *,
    repo_root: Path,
    trade_date: str,
    sector_lookup: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    path = repo_root / "outputs" / "attribution" / trade_date / "position_attribution.json"
    payload = _read_json(path)
    if payload is None:
        return [], [], []
    positions = payload.get("positions") or []
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
        sector = str(row.get("sector") or sector_lookup.get(symbol) or "").strip() or None
        source_artifacts = sorted(set(list(row.get("source_artifacts") or []) + [str(path)]))
        rows.append(
            {
                "strategy": strategy,
                "symbol": symbol,
                "weight": _weight(row),
                "sector": sector,
                "source_artifacts": source_artifacts,
            }
        )

    if not rows:
        return [], [str(path)], ["attribution_positions_empty"]
    return rows, [str(path)], []


def load_strategy_holdings(repo_root: Path, trade_date: str) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    sector_lookup, sector_sources = _load_universe_sectors(repo_root)
    rows: list[dict[str, Any]] = []
    sources: list[str] = []
    reasons: list[str] = []
    seen: set[str] = set()

    portfolio_path = repo_root / "outputs" / "portfolio_history" / trade_date / "holdings_snapshot.json"
    portfolio = _read_json(portfolio_path)
    if portfolio is not None:
        strategies = portfolio.get("strategies") or {}
        if isinstance(strategies, dict):
            for strategy, strategy_payload in sorted(strategies.items()):
                if not isinstance(strategy_payload, dict):
                    continue
                rows.extend(
                    _normalize_holdings(
                        strategy=str(strategy),
                        payload=strategy_payload,
                        source_artifact=str(portfolio_path),
                        sector_lookup=sector_lookup,
                    )
                )
                seen.add(str(strategy))
        sources.append(str(portfolio_path))

    shadow_dir = repo_root / "outputs" / "shadow_candidates" / trade_date
    if shadow_dir.exists():
        for path in sorted(shadow_dir.iterdir(), key=lambda item: item.name):
            if path.name not in STRATEGY_FILE_NAMES or not path.is_file():
                continue
            payload = _read_json(path)
            if payload is None:
                continue
            strategy = str(payload.get("strategy_slug") or path.stem)
            if strategy in seen:
                continue
            rows.extend(
                _normalize_holdings(
                    strategy=strategy,
                    payload=payload,
                    source_artifact=str(path),
                    sector_lookup=sector_lookup,
                )
            )
            seen.add(strategy)
            sources.append(str(path))

    if not rows:
        attribution_rows, attribution_sources, attribution_reasons = _load_position_attribution_holdings(
            repo_root=repo_root,
            trade_date=trade_date,
            sector_lookup=sector_lookup,
        )
        rows.extend(attribution_rows)
        sources.extend(attribution_sources)
        reasons.extend(attribution_reasons)

    if not sources:
        reasons.append("holdings_source_missing")
    elif not rows:
        reasons.append("no_holdings")
    if not sector_lookup:
        reasons.append("sector_lookup_missing")
    return rows, sorted(set(sources + sector_sources)), sorted(set(reasons))


def _load_existing_exposure(repo: Path, trade_date: str) -> tuple[dict[str, Any], list[str]]:
    path = repo / "outputs" / "attribution" / trade_date / "exposure_summary.json"
    payload = _read_json(path)
    return payload or {}, [str(path)] if payload is not None else []


def _load_existing_concentration(repo: Path, trade_date: str) -> tuple[dict[str, Any], list[str]]:
    path = repo / "outputs" / "attribution" / trade_date / "concentration_analysis.json"
    payload = _read_json(path)
    return payload or {}, [str(path)] if payload is not None else []


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 10)


def _top_holdings(rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    ranked = sorted(
        rows,
        key=lambda row: (
            -float(row.get("weight") or 0.0),
            str(row.get("symbol") or ""),
        ),
    )
    return [
        {
            "symbol": row.get("symbol"),
            "weight": row.get("weight"),
            "sector": row.get("sector"),
        }
        for row in ranked[:limit]
    ]


def _strategy_summary(
    *,
    strategy: str,
    rows: list[dict[str, Any]],
    existing_concentration: dict[str, Any],
    existing_exposure: dict[str, Any],
) -> dict[str, Any]:
    weights = [_safe_float(row.get("weight")) for row in rows]
    missing_weights = sum(1 for value in weights if value is None)
    numeric_weights = sorted([float(value) for value in weights if value is not None], reverse=True)
    position_count = len(rows)
    max_weight = max(numeric_weights) if numeric_weights else None
    top3 = sum(numeric_weights[:3]) if numeric_weights else None
    top5 = sum(numeric_weights[:5]) if numeric_weights else None
    hhi = sum(value * value for value in numeric_weights) if numeric_weights else None

    sector_weights: dict[str, float] = {}
    missing_sectors = 0
    for row in rows:
        sector = str(row.get("sector") or "").strip()
        weight = _safe_float(row.get("weight")) or 0.0
        if not sector:
            missing_sectors += 1
            continue
        sector_weights[sector] = sector_weights.get(sector, 0.0) + weight
    sector_weights = {sector: _round(weight) for sector, weight in sorted(sector_weights.items())}
    max_sector_weight = max(sector_weights.values()) if sector_weights else None

    conc_payload = ((existing_concentration.get("strategies") or {}).get(strategy) or {}) if isinstance(existing_concentration, dict) else {}
    exp_payload = ((existing_exposure.get("strategies") or {}).get(strategy) or {}) if isinstance(existing_exposure, dict) else {}
    exp_sector = exp_payload.get("sector_exposure") if isinstance(exp_payload, dict) else {}
    if isinstance(exp_sector, dict) and exp_sector.get("weights"):
        sector_weights = {
            str(sector): _round(_safe_float(weight))
            for sector, weight in sorted((exp_sector.get("weights") or {}).items())
        }
        max_sector_weight = _safe_float(exp_sector.get("max_sector_weight"))

    market_beta = _safe_float(exp_payload.get("market_beta")) if isinstance(exp_payload, dict) else None
    concentration_risk = _risk_level_from_concentration(
        position_count=position_count,
        max_weight=max_weight,
        top3=top3,
        top5=top5,
    )
    exposure_risk = _risk_level_from_exposure(
        position_count=position_count,
        missing_sectors=missing_sectors,
        max_sector_weight=max_sector_weight,
        market_beta=market_beta,
    )
    confidence = _confidence(position_count=position_count, missing_weights=missing_weights, missing_sectors=missing_sectors)

    reason_codes: list[str] = []
    if position_count == 0:
        reason_codes.append("no_holdings")
    if missing_weights:
        reason_codes.append("missing_position_weights")
    if missing_sectors:
        reason_codes.append("missing_sector_coverage")
    if not reason_codes:
        reason_codes = ["ok"]

    return {
        "strategy": strategy,
        "position_count": position_count,
        "top_holdings": _top_holdings(rows),
        "max_position_weight": _round(max_weight),
        "top3_concentration": _round(top3),
        "top5_concentration": _round(top5),
        "hhi": _round(_safe_float(conc_payload.get("hhi")) if isinstance(conc_payload, dict) and conc_payload.get("hhi") is not None else hhi),
        "sector_exposure": sector_weights,
        "missing_sector_coverage_count": missing_sectors,
        "max_sector_weight": _round(max_sector_weight),
        "market_beta": _round(market_beta),
        "concentration_risk_level": concentration_risk,
        "exposure_risk_level": exposure_risk,
        "confidence": confidence,
        "reason_codes": sorted(set(reason_codes)) if reason_codes != ["ok"] else ["ok"],
    }


def _aggregate_level(levels: list[str]) -> str:
    order = {"UNKNOWN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
    if not levels:
        return "UNKNOWN"
    return max((level if level in order else "UNKNOWN" for level in levels), key=lambda item: order[item])


def _min_confidence(values: list[str]) -> str:
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    if not values:
        return "LOW"
    return min((value if value in order else "LOW" for value in values), key=lambda item: order[item])


def build_risk_summary(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root)
    holdings, holding_sources, holding_reasons = load_strategy_holdings(repo, trade_date)
    existing_exposure, exposure_sources = _load_existing_exposure(repo, trade_date)
    existing_concentration, concentration_sources = _load_existing_concentration(repo, trade_date)

    by_strategy: dict[str, list[dict[str, Any]]] = {}
    for row in holdings:
        by_strategy.setdefault(str(row.get("strategy") or ""), []).append(row)

    strategies = {
        strategy: _strategy_summary(
            strategy=strategy,
            rows=sorted(rows, key=lambda row: str(row.get("symbol") or "")),
            existing_concentration=existing_concentration,
            existing_exposure=existing_exposure,
        )
        for strategy, rows in sorted(by_strategy.items())
        if strategy
    }

    all_reasons = sorted({
        code
        for code in holding_reasons
        + [
            code
            for row in strategies.values()
            for code in list(row.get("reason_codes") or [])
            if code != "ok"
        ]
        if code != "ok"
    })
    if not all_reasons:
        all_reasons = ["ok"]

    strategies_covered = sorted(strategies)
    position_count = sum(int(row.get("position_count") or 0) for row in strategies.values())
    top_holdings = {
        strategy: list(row.get("top_holdings") or [])
        for strategy, row in sorted(strategies.items())
    }
    sector_exposure = {
        strategy: dict(row.get("sector_exposure") or {})
        for strategy, row in sorted(strategies.items())
    }
    max_position_weight = max(
        [_safe_float(row.get("max_position_weight")) or 0.0 for row in strategies.values()],
        default=None,
    )
    top3 = max(
        [_safe_float(row.get("top3_concentration")) or 0.0 for row in strategies.values()],
        default=None,
    )
    top5 = max(
        [_safe_float(row.get("top5_concentration")) or 0.0 for row in strategies.values()],
        default=None,
    )
    missing_sector_count = sum(int(row.get("missing_sector_coverage_count") or 0) for row in strategies.values())
    confidence = _min_confidence([str(row.get("confidence") or "LOW") for row in strategies.values()])
    if not strategies:
        confidence = "LOW"
    source_artifacts = sorted(set(holding_sources + exposure_sources + concentration_sources))

    risk_summary = {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "strategies_covered": strategies_covered,
        "position_count": position_count,
        "top_holdings": top_holdings,
        "max_position_weight": _round(max_position_weight),
        "top3_concentration": _round(top3),
        "top5_concentration": _round(top5),
        "sector_exposure": sector_exposure,
        "missing_sector_coverage_count": missing_sector_count,
        "concentration_risk_level": _aggregate_level([str(row.get("concentration_risk_level")) for row in strategies.values()]),
        "exposure_risk_level": _aggregate_level([str(row.get("exposure_risk_level")) for row in strategies.values()]),
        "confidence": confidence,
        "reason_codes": all_reasons,
        "source_artifacts": source_artifacts,
        "strategies": strategies,
    }
    concentration_summary = {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "strategies_covered": strategies_covered,
        "position_count": position_count,
        "top_holdings": top_holdings,
        "max_position_weight": risk_summary["max_position_weight"],
        "top3_concentration": risk_summary["top3_concentration"],
        "top5_concentration": risk_summary["top5_concentration"],
        "sector_exposure": sector_exposure,
        "missing_sector_coverage_count": missing_sector_count,
        "concentration_risk_level": risk_summary["concentration_risk_level"],
        "exposure_risk_level": risk_summary["exposure_risk_level"],
        "confidence": confidence,
        "reason_codes": all_reasons,
        "source_artifacts": source_artifacts,
        "strategies": {
            strategy: {
                key: row.get(key)
                for key in (
                    "position_count",
                    "top_holdings",
                    "max_position_weight",
                    "top3_concentration",
                    "top5_concentration",
                    "hhi",
                    "concentration_risk_level",
                    "confidence",
                    "reason_codes",
                )
            }
            for strategy, row in sorted(strategies.items())
        },
    }
    exposure_summary = {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "strategies_covered": strategies_covered,
        "position_count": position_count,
        "top_holdings": top_holdings,
        "max_position_weight": risk_summary["max_position_weight"],
        "top3_concentration": risk_summary["top3_concentration"],
        "top5_concentration": risk_summary["top5_concentration"],
        "sector_exposure": sector_exposure,
        "missing_sector_coverage_count": missing_sector_count,
        "concentration_risk_level": risk_summary["concentration_risk_level"],
        "exposure_risk_level": risk_summary["exposure_risk_level"],
        "confidence": confidence,
        "reason_codes": all_reasons,
        "source_artifacts": source_artifacts,
        "strategies": {
            strategy: {
                key: row.get(key)
                for key in (
                    "position_count",
                    "sector_exposure",
                    "missing_sector_coverage_count",
                    "max_sector_weight",
                    "market_beta",
                    "exposure_risk_level",
                    "confidence",
                    "reason_codes",
                )
            }
            for strategy, row in sorted(strategies.items())
        },
    }

    out_root = Path(output_root) if output_root is not None else repo / "outputs" / "risk_summary"
    out_dir = out_root / trade_date
    _write_json(out_dir / "risk_summary.json", risk_summary)
    _write_json(out_dir / "concentration_summary.json", concentration_summary)
    _write_json(out_dir / "exposure_summary.json", exposure_summary)
    return {
        "risk_summary": risk_summary,
        "concentration_summary": concentration_summary,
        "exposure_summary": exposure_summary,
        "artifact_paths": {
            "risk_summary": str(out_dir / "risk_summary.json"),
            "concentration_summary": str(out_dir / "concentration_summary.json"),
            "exposure_summary": str(out_dir / "exposure_summary.json"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build canonical Caerus risk/concentration summary artifacts.")
    parser.add_argument("--date", required=True, help="Risk summary date in YYYY-MM-DD format.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    result = build_risk_summary(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(json.dumps(result["risk_summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
