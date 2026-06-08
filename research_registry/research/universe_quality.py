from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from research_registry.research.model_quality_common import (
    md_join,
    model_quality_dir,
    normalize_date,
    read_json,
    symbol,
    write_json,
    write_text,
)

SCHEMA_VERSION = "caerus_universe_quality_v1"
DEFAULT_PRICE_PATHS = (
    Path("outputs/research/flow_detection_v1/price_panel.parquet"),
    Path("alpha_stack_cache/csv_export/prices_matrix.csv"),
    Path("data/alpha_stack_cache/csv_export/prices_matrix.csv"),
)


def _read_universe(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.exists():
        return [], ["UNIVERSE_FILE_MISSING"]
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows = [dict(row) for row in csv.DictReader(lines)]
    except Exception:
        return [], ["UNIVERSE_PARSE_ERROR"]
    out: list[dict[str, Any]] = []
    reasons: list[str] = []
    for idx, row in enumerate(rows, start=2):
        ticker = symbol(row.get("ticker") or row.get("symbol"))
        if not ticker:
            reasons.append(f"BLANK_SYMBOL_ROW:{idx}")
            continue
        out.append({"ticker": ticker, "sector": str(row.get("sector") or "").strip() or None})
    return out, sorted(set(reasons)) or ["ok"]


def _manual_aliases(repo: Path) -> dict[str, str]:
    payload = read_json(repo / "data" / "security_master" / "manual_aliases.json") or {}
    aliases = payload.get("aliases") or {}
    if not isinstance(aliases, dict):
        return {}
    return {symbol(src): symbol(dst) for src, dst in aliases.items() if symbol(src) and symbol(dst)}


def _ticker_exceptions(repo: Path) -> dict[str, Any]:
    payload = read_json(repo / "data" / "ticker_exceptions.json") or {}
    aliases = payload.get("aliases") or {}
    ignored = payload.get("ignore") or []
    return {
        "aliases": {symbol(src): symbol(dst) for src, dst in aliases.items() if symbol(src) and symbol(dst)}
        if isinstance(aliases, dict)
        else {},
        "ignore": sorted({symbol(item) for item in ignored if symbol(item)}) if isinstance(ignored, list) else [],
    }


def _security_master(repo: Path) -> tuple[dict[str, Any] | None, Path | None]:
    latest = repo / "data" / "security_master" / "ticker_universe_latest.json"
    if latest.exists():
        return read_json(latest), latest
    pointer = read_json(repo / "data" / "security_master" / "latest.json")
    rel = str((pointer or {}).get("ticker_universe_path") or "")
    if rel:
        path = Path(rel)
        if not path.is_absolute():
            path = repo / path
        if path.exists():
            return read_json(path), path
    return None, None


def _price_path(repo: Path, override: Path | None = None) -> Path | None:
    if override is not None:
        path = override if override.is_absolute() else repo / override
        return path if path.exists() else None
    for rel in DEFAULT_PRICE_PATHS:
        path = repo / rel
        if path.exists():
            return path
    return None


def _price_coverage(
    *,
    repo: Path,
    trade_date: str,
    tickers: list[str],
    price_path: Path | None = None,
    stale_days: int = 5,
) -> dict[str, Any]:
    path = _price_path(repo, price_path)
    wanted = sorted(set(tickers))
    if path is None:
        return {
            "available": False,
            "price_source": None,
            "covered_symbols": [],
            "missing_symbols": wanted,
            "stale_symbols": [],
            "coverage_ratio": 0.0,
            "reason_codes": ["PRICE_SOURCE_MISSING"],
        }
    if path.suffix.lower() == ".parquet":
        return _price_coverage_parquet(path=path, trade_date=trade_date, tickers=wanted, stale_days=stale_days)
    return _price_coverage_csv(path=path, trade_date=trade_date, tickers=wanted, stale_days=stale_days)


def _price_coverage_csv(*, path: Path, trade_date: str, tickers: list[str], stale_days: int) -> dict[str, Any]:
    target = normalize_date(trade_date)
    latest_by_symbol = {ticker: None for ticker in tickers}
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = reader.fieldnames or []
            if "Date" in fields:
                for row in reader:
                    row_date = str(row.get("Date") or "")
                    if not row_date or row_date > target:
                        continue
                    for ticker in tickers:
                        value = row.get(ticker)
                        if value not in (None, ""):
                            latest_by_symbol[ticker] = row_date
            elif {"date", "ticker"}.issubset(set(fields)):
                for row in reader:
                    row_date = str(row.get("date") or "")
                    ticker = symbol(row.get("ticker"))
                    if ticker in latest_by_symbol and row_date and row_date <= target and row.get("close") not in (None, ""):
                        latest_by_symbol[ticker] = row_date
            else:
                return {
                    "available": False,
                    "price_source": str(path),
                    "covered_symbols": [],
                    "missing_symbols": tickers,
                    "stale_symbols": [],
                    "coverage_ratio": 0.0,
                    "reason_codes": ["PRICE_SOURCE_UNSUPPORTED_CSV_SHAPE"],
                }
    except Exception:
        return {
            "available": False,
            "price_source": str(path),
            "covered_symbols": [],
            "missing_symbols": tickers,
            "stale_symbols": [],
            "coverage_ratio": 0.0,
            "reason_codes": ["PRICE_SOURCE_READ_FAILED"],
        }
    return _coverage_from_latest(path=path, trade_date=target, latest_by_symbol=latest_by_symbol, stale_days=stale_days)


def _price_coverage_parquet(*, path: Path, trade_date: str, tickers: list[str], stale_days: int) -> dict[str, Any]:
    try:
        import pandas as pd
    except Exception:
        return {
            "available": False,
            "price_source": str(path),
            "covered_symbols": [],
            "missing_symbols": tickers,
            "stale_symbols": [],
            "coverage_ratio": 0.0,
            "reason_codes": ["PANDAS_UNAVAILABLE_FOR_PARQUET"],
        }
    try:
        frame = pd.read_parquet(path, columns=["date", "ticker", "close"])
    except Exception:
        return {
            "available": False,
            "price_source": str(path),
            "covered_symbols": [],
            "missing_symbols": tickers,
            "stale_symbols": [],
            "coverage_ratio": 0.0,
            "reason_codes": ["PRICE_SOURCE_READ_FAILED"],
        }
    if frame.empty:
        return {
            "available": False,
            "price_source": str(path),
            "covered_symbols": [],
            "missing_symbols": tickers,
            "stale_symbols": [],
            "coverage_ratio": 0.0,
            "reason_codes": ["PRICE_SOURCE_EMPTY"],
        }
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date.astype(str)
    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    frame = frame[(frame["ticker"].isin(tickers)) & (frame["date"] <= normalize_date(trade_date)) & frame["close"].notna()]
    latest_by_symbol = {ticker: None for ticker in tickers}
    for ticker, group in frame.groupby("ticker"):
        latest_by_symbol[str(ticker)] = str(group["date"].max())
    return _coverage_from_latest(path=path, trade_date=normalize_date(trade_date), latest_by_symbol=latest_by_symbol, stale_days=stale_days)


def _coverage_from_latest(*, path: Path, trade_date: str, latest_by_symbol: dict[str, str | None], stale_days: int) -> dict[str, Any]:
    import datetime as dt

    target = dt.date.fromisoformat(trade_date)
    covered = sorted([ticker for ticker, latest in latest_by_symbol.items() if latest])
    missing = sorted([ticker for ticker, latest in latest_by_symbol.items() if not latest])
    stale = []
    for ticker, latest in latest_by_symbol.items():
        if not latest:
            continue
        try:
            if (target - dt.date.fromisoformat(latest)).days > stale_days:
                stale.append(ticker)
        except Exception:
            stale.append(ticker)
    reasons = []
    if missing:
        reasons.append("PRICE_COVERAGE_MISSING_SYMBOLS")
    if stale:
        reasons.append("STALE_PRICE_SYMBOLS")
    return {
        "available": True,
        "price_source": str(path),
        "covered_symbols": covered,
        "missing_symbols": missing,
        "stale_symbols": sorted(stale),
        "latest_date_by_symbol": {ticker: latest_by_symbol[ticker] for ticker in sorted(latest_by_symbol)},
        "coverage_ratio": round(len(covered) / max(1, len(latest_by_symbol)), 10),
        "reason_codes": sorted(set(reasons)) or ["ok"],
    }


def build_universe_quality(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
    universe_path: Path | str = Path("data/universe.csv"),
    price_path: Path | str | None = None,
    write: bool = True,
) -> dict[str, Any]:
    target = normalize_date(trade_date)
    repo = Path(repo_root)
    universe_file = Path(universe_path)
    if not universe_file.is_absolute():
        universe_file = repo / universe_file
    rows, universe_reasons = _read_universe(universe_file)
    tickers = [row["ticker"] for row in rows]
    counts = Counter(tickers)
    duplicates = sorted([ticker for ticker, count in counts.items() if count > 1])
    sectors = Counter(row["sector"] or "UNKNOWN" for row in rows)
    aliases = _manual_aliases(repo)
    stale_aliases = [
        {"original_symbol": src, "resolved_symbol": dst, "reason": f"manual_alias_present_in_universe:{src}->{dst}"}
        for src, dst in sorted(aliases.items())
        if src in counts
    ]
    security_master, security_master_path = _security_master(repo)
    security_symbols = set()
    if security_master is not None:
        for row in security_master.get("symbols") or []:
            if isinstance(row, dict) and symbol(row.get("symbol")):
                security_symbols.add(symbol(row.get("symbol")))
    unknown_to_security_master = sorted([ticker for ticker in set(tickers) if security_master is not None and ticker not in security_symbols])
    ticker_exceptions = _ticker_exceptions(repo)
    ignored_for_prices = sorted(set(tickers) & set(ticker_exceptions["ignore"]))
    price_tickers = sorted(set(tickers) - set(ignored_for_prices))
    price_override = Path(price_path) if price_path is not None else None
    price = _price_coverage(repo=repo, trade_date=target, tickers=price_tickers, price_path=price_override)
    price["ignored_symbols"] = ignored_for_prices
    price["provider_aliases"] = {
        src: dst for src, dst in sorted(ticker_exceptions["aliases"].items()) if src in counts
    }
    price["coverage_universe_size"] = len(price_tickers)
    reason_codes = set(code for code in universe_reasons if code != "ok")
    if duplicates:
        reason_codes.add("DUPLICATE_UNIVERSE_SYMBOLS")
    if stale_aliases:
        reason_codes.add("STALE_ALIAS_SYMBOLS")
    if security_master is None:
        reason_codes.add("SECURITY_MASTER_MISSING")
    if unknown_to_security_master:
        reason_codes.add("SECURITY_MASTER_UNKNOWN_SYMBOLS")
    reason_codes.update(code for code in price.get("reason_codes") or [] if code != "ok")
    sector_rows = [
        {"sector": sector, "count": count, "share": round(count / max(1, len(rows)), 10)}
        for sector, count in sorted(sectors.items(), key=lambda item: (-item[1], item[0]))
    ]
    max_sector_share = sector_rows[0]["share"] if sector_rows else 0.0
    expansion_blockers = []
    if security_master is None:
        expansion_blockers.append("security_master_required_before_expansion")
    if price.get("coverage_ratio", 0.0) < 0.95:
        expansion_blockers.append("price_coverage_required_before_expansion")
    expansion_blockers.append("pit_universe_membership_policy_required")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": target,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "available": bool(rows),
        "status": "OK" if not reason_codes else "PARTIAL" if rows else "NO_DATA",
        "universe_path": str(universe_file),
        "current_universe_size": len(rows),
        "unique_symbol_count": len(set(tickers)),
        "duplicate_symbols": duplicates,
        "missing_symbols": [code for code in universe_reasons if code.startswith("BLANK_SYMBOL_ROW")],
        "sector_coverage": {
            "available": any(row.get("sector") for row in rows),
            "sector_count": len(sectors),
            "sectors": sector_rows,
            "max_sector_share": max_sector_share,
            "reason_codes": ["ok"] if any(row.get("sector") for row in rows) else ["SECTOR_DATA_MISSING"],
        },
        "price_coverage": price,
        "security_master": {
            "available": security_master is not None,
            "path": str(security_master_path) if security_master_path else None,
            "asof_date": (security_master or {}).get("asof_date") if security_master else None,
            "unknown_symbols": unknown_to_security_master,
            "reason_codes": ["ok"] if security_master is not None and not unknown_to_security_master else ["SECURITY_MASTER_MISSING"] if security_master is None else ["SECURITY_MASTER_UNKNOWN_SYMBOLS"],
        },
        "alias_issues": stale_aliases,
        "concentration_risk": {
            "max_sector_share": max_sector_share,
            "top_sector": sector_rows[0]["sector"] if sector_rows else None,
            "limited_universe_risk": len(set(tickers)) < 500,
            "reason_codes": ["LIMITED_UNIVERSE_SIZE"] if len(set(tickers)) < 500 else ["ok"],
        },
        "future_expansion_candidates": [
            {
                "candidate_set": "sp500_security_master_gated",
                "status": "BLOCKED" if expansion_blockers else "READY_FOR_RESEARCH",
                "blockers": expansion_blockers,
            },
            {
                "candidate_set": "russell_1000_liquid_names",
                "status": "BLOCKED",
                "blockers": sorted(set(expansion_blockers + ["liquidity_filter_definition_required"])),
            },
        ],
        "reason_codes": sorted(reason_codes) or ["ok"],
    }
    if write:
        out_dir = model_quality_dir(repo, target, output_root)
        write_json(out_dir / "universe_quality.json", payload)
        write_text(out_dir / "universe_quality.md", render_markdown(payload))
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Universe Quality - {payload.get('date')}",
        "",
        f"- Status: {payload.get('status')}",
        f"- Universe size: {payload.get('current_universe_size')}",
        f"- Unique symbols: {payload.get('unique_symbol_count')}",
        f"- Reason codes: {md_join(payload.get('reason_codes') or [])}",
        "",
        "## Sector Coverage",
        "",
        "| Sector | Count | Share |",
        "|---|---:|---:|",
    ]
    for row in (payload.get("sector_coverage") or {}).get("sectors") or []:
        lines.append(f"| {row.get('sector')} | {row.get('count')} | {row.get('share')} |")
    price = payload.get("price_coverage") or {}
    lines.extend([
        "",
        "## Price Coverage",
        "",
        f"- Source: {price.get('price_source')}",
        f"- Coverage ratio: {price.get('coverage_ratio')}",
        f"- Missing symbols: {md_join(price.get('missing_symbols') or [])}",
        f"- Stale symbols: {md_join(price.get('stale_symbols') or [])}",
        "",
        "## Expansion Blockers",
        "",
    ])
    for row in payload.get("future_expansion_candidates") or []:
        lines.append(f"- {row.get('candidate_set')}: {row.get('status')} ({md_join(row.get('blockers') or [])})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build research-only universe quality artifacts.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--universe-path", default="data/universe.csv")
    parser.add_argument("--price-path", default=None)
    args = parser.parse_args(argv)
    payload = build_universe_quality(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
        universe_path=Path(args.universe_path),
        price_path=Path(args.price_path) if args.price_path else None,
    )
    print(json.dumps({"date": payload["date"], "status": payload["status"], "reason_codes": payload["reason_codes"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
