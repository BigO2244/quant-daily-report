from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from research_registry.research.model_quality_common import (
    md_join,
    model_quality_dir,
    normalize_date,
    symbol,
    write_json,
    write_text,
)

SCHEMA_VERSION = "caerus_multi_asset_research_framework_v1"
OPTIONS_STATUS = "DEFERRED_DESIGN_ONLY"
PRICE_SOURCE_CANDIDATES = (
    Path("outputs/research/flow_detection_v1/price_panel.csv"),
    Path("outputs/research/ma_vol_hypothesis/price_panel.csv"),
    Path("alpha_stack_cache/csv_export/prices_matrix.csv"),
    Path("data/alpha_stack_cache/csv_export/prices_matrix.csv"),
)
CANDIDATE_SLEEVES = (
    {"sleeve_id": "treasury_duration", "asset_class": "fixed_income", "symbols": ["SHY", "IEF", "TLT"]},
    {"sleeve_id": "cash_tbill", "asset_class": "cash_like", "symbols": ["SGOV", "BIL"]},
    {"sleeve_id": "gold", "asset_class": "precious_metals", "symbols": ["GLD", "IAU"]},
    {"sleeve_id": "broad_commodities", "asset_class": "commodities", "symbols": ["DBC", "PDBC"]},
    {"sleeve_id": "managed_futures_proxy", "asset_class": "trend_following_proxy", "symbols": ["DBMF", "CTA"]},
    {"sleeve_id": "defensive_equity_proxy", "asset_class": "defensive_equity", "symbols": ["SPLV", "USMV"]},
    {"sleeve_id": "options_overlay", "asset_class": "options", "symbols": [], "status": OPTIONS_STATUS},
)


def build_multi_asset_research_framework(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
    write: bool = True,
) -> dict[str, Any]:
    target = normalize_date(trade_date)
    repo = Path(repo_root)
    available_symbols, source_paths = _available_price_symbols(repo)
    candidate_sleeves = _candidate_sleeves(available_symbols)
    required = _required_data()
    missing_data = _missing_data(candidate_sleeves)
    available_data = _available_data(candidate_sleeves, source_paths)
    reason_codes = {"MULTI_ASSET_FRAMEWORK_RESEARCH_ONLY", "OPTIONS_DEFERRED_DESIGN_ONLY"}
    if not source_paths:
        reason_codes.add("PRICE_SOURCE_MISSING")
    for row in missing_data:
        reason_codes.add(f"MISSING_DATA:{row['symbol']}")
    payload = {
        "trade_date": target,
        "schema_version": SCHEMA_VERSION,
        "status": "DRAFT_RESEARCH" if not missing_data else "PARTIAL",
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "candidate_sleeves": candidate_sleeves,
        "required_data": required,
        "available_data": available_data,
        "missing_data": missing_data,
        "research_questions": _research_questions(),
        "promotion_preconditions": _promotion_preconditions(),
        "integration_scope": {
            "execution_integration": False,
            "broker_submission": False,
            "allocation_engine": False,
            "order_generation": False,
            "cron_change": False,
        },
        "options_status": OPTIONS_STATUS,
        "source_paths": source_paths,
        "reason_codes": sorted(reason_codes) or ["ok"],
    }
    if write:
        out_dir = model_quality_dir(repo, target, output_root)
        write_json(out_dir / "multi_asset_research_framework.json", payload)
        write_text(out_dir / "multi_asset_research_framework.md", render_markdown(payload))
    return payload


def _available_price_symbols(repo: Path) -> tuple[set[str], list[str]]:
    available: set[str] = set()
    sources: list[str] = []
    for relative in PRICE_SOURCE_CANDIDATES:
        path = repo / relative
        if not path.exists() or path.suffix.lower() != ".csv":
            continue
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames:
                    continue
                fields = [str(field) for field in reader.fieldnames]
                if {"date", "ticker", "close"}.issubset({field.lower() for field in fields}):
                    for row in reader:
                        ticker = symbol(row.get("ticker") or row.get("Ticker") or row.get("symbol") or row.get("Symbol"))
                        if ticker:
                            available.add(ticker)
                else:
                    for field in fields:
                        if field.lower() not in {"date", "datetime"}:
                            ticker = symbol(field)
                            if ticker:
                                available.add(ticker)
            sources.append(str(relative))
        except Exception:
            continue
    return available, sources


def _candidate_sleeves(available_symbols: set[str]) -> list[dict[str, Any]]:
    rows = []
    for raw in CANDIDATE_SLEEVES:
        symbols = list(raw.get("symbols") or [])
        available = [ticker for ticker in symbols if ticker in available_symbols]
        missing = [ticker for ticker in symbols if ticker not in available_symbols]
        status = raw.get("status") or ("AVAILABLE" if symbols and not missing else "PARTIAL" if available else "MISSING")
        rows.append(
            {
                "sleeve_id": raw["sleeve_id"],
                "asset_class": raw["asset_class"],
                "symbols": symbols,
                "available_symbols": available,
                "missing_symbols": missing,
                "status": status,
                "reason_codes": ["ok"] if status == "AVAILABLE" else [OPTIONS_STATUS] if status == OPTIONS_STATUS else [f"MISSING_DATA:{ticker}" for ticker in missing] or ["DESIGN_ONLY"],
            }
        )
    return rows


def _required_data() -> list[dict[str, str]]:
    return [
        {"name": "daily_adjusted_prices", "requirement": "PIT-safe adjusted close history for every candidate ETF"},
        {"name": "trading_calendar_alignment", "requirement": "aligned dates versus equity strategy and SPY research artifacts"},
        {"name": "regime_context", "requirement": "VIX/regime labels available as of each date"},
        {"name": "liquidity_metadata", "requirement": "volume, spread, and tradability checks before any promotion discussion"},
        {"name": "expense_and_tax_context", "requirement": "expense ratios and vehicle-specific caveats for portfolio-quality interpretation"},
    ]


def _available_data(candidate_sleeves: list[dict[str, Any]], source_paths: list[str]) -> list[dict[str, Any]]:
    rows = []
    for sleeve in candidate_sleeves:
        for ticker in sleeve.get("available_symbols") or []:
            rows.append({"sleeve_id": sleeve["sleeve_id"], "symbol": ticker, "source_paths": source_paths})
    return sorted(rows, key=lambda row: (row["sleeve_id"], row["symbol"]))


def _missing_data(candidate_sleeves: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows = []
    for sleeve in candidate_sleeves:
        for ticker in sleeve.get("missing_symbols") or []:
            rows.append({"sleeve_id": sleeve["sleeve_id"], "symbol": ticker, "required_data": "daily_adjusted_prices"})
    return sorted(rows, key=lambda row: (row["sleeve_id"], row["symbol"]))


def _research_questions() -> list[str]:
    return [
        "Do non-equity sleeves improve drawdown, recovery, volatility, and correlation quality versus the equity-only stack?",
        "Which sleeve returns are robust across regimes without look-ahead bias?",
        "Which candidate sleeves have sufficient PIT-safe data and liquidity metadata?",
        "Which preconditions would be required before any allocation implementation could be proposed?",
        "When should options be considered, and which audited infrastructure must exist first?",
    ]


def _promotion_preconditions() -> list[str]:
    return [
        "complete adjusted price coverage for all selected sleeve proxies",
        "regime-stratified historical analysis with explicit source paths",
        "liquidity and tradability review",
        "portfolio-level correlation, drawdown, turnover, and recovery analysis",
        "separate governance approval before any allocation or execution implementation",
    ]


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Multi-Asset Research Framework - {payload.get('trade_date')}",
        "",
        f"- Status: {payload.get('status')}",
        f"- Governance: {payload.get('governance_label')} / {payload.get('execution_impact')}",
        f"- Options status: {payload.get('options_status')}",
        f"- Reason codes: {md_join(payload.get('reason_codes') or [])}",
        "",
        "## Candidate Sleeves",
        "",
        "| Sleeve | Asset Class | Symbols | Available | Missing | Status |",
        "|---|---|---|---|---|---|",
    ]
    for row in payload.get("candidate_sleeves") or []:
        lines.append(
            f"| {row.get('sleeve_id')} | {row.get('asset_class')} | {md_join(row.get('symbols') or [])} | "
            f"{md_join(row.get('available_symbols') or [])} | {md_join(row.get('missing_symbols') or [])} | {row.get('status')} |"
        )
    lines.extend(["", "## Promotion Preconditions", ""])
    for item in payload.get("promotion_preconditions") or []:
        lines.append(f"- {item}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit the multi-asset research framework without execution integration.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_multi_asset_research_framework(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(json.dumps({"trade_date": payload["trade_date"], "status": payload["status"], "options_status": payload["options_status"], "reason_codes": payload["reason_codes"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
