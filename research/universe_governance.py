from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

from core.security_master import (
    MANUAL_ALIAS_PATH,
    MAX_UNIVERSE_AGE_DAYS,
    SECURITY_MASTER_ROOT,
    load_latest_ticker_universe,
    load_manual_aliases,
)
from research.risk_coverage import load_holdings_for_risk_coverage


SCHEMA_VERSION = "caerus_universe_governance_v1"


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


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _age_days(asof_date: str | None, trade_date: str) -> int | None:
    if not asof_date:
        return None
    try:
        return (dt.date.fromisoformat(trade_date) - dt.date.fromisoformat(str(asof_date))).days
    except Exception:
        return None


def _planned_symbols(repo: Path, trade_date: str) -> tuple[list[str], list[str], list[str]]:
    path = repo / "outputs" / "precompute" / trade_date / "planned_execution_payload.json"
    payload = _read_json(path)
    if payload is None:
        return [], [], ["planned_execution_payload_missing"]
    symbols = sorted(
        {
            _symbol(row.get("ticker") or row.get("symbol"))
            for row in payload.get("trades") or []
            if isinstance(row, dict) and _symbol(row.get("ticker") or row.get("symbol"))
        }
    )
    return symbols, [str(path)], [] if symbols else ["planned_symbols_empty"]


def _holdings_symbols(repo: Path, trade_date: str) -> tuple[list[str], list[str], list[str]]:
    rows, sources, reasons, _source_date = load_holdings_for_risk_coverage(repo, trade_date)
    symbols = sorted({_symbol(row.get("symbol")) for row in rows if _symbol(row.get("symbol"))})
    return symbols, sources, [] if symbols else [reason for reason in reasons if reason != "sector_lookup_missing"] or ["holdings_symbols_missing"]


def _record_status(record: dict[str, Any]) -> str:
    return str(record.get("status") or record.get("alpaca_status") or "").strip().lower()


def _record_exchange(record: dict[str, Any]) -> str:
    return str(record.get("exchange") or record.get("listing_exchange") or "").strip()


def _check_symbols(
    *,
    symbols: list[str],
    context: str,
    aliases: dict[str, str],
    alias_source: str,
    records: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str]]:
    rows: list[dict[str, Any]] = []
    alias_rows: dict[str, dict[str, str]] = {}
    blockers: list[str] = []
    for original in sorted(symbols):
        resolved = aliases.get(original, original)
        if resolved != original:
            alias_rows[original] = {
                "original_symbol": original,
                "resolved_symbol": resolved,
                "source": alias_source,
                "reason": f"manual_alias:{original}->{resolved}",
            }
        symbol_records = records.get(resolved, [])
        status = "PASS"
        reason_codes: list[str] = []
        record = symbol_records[0] if symbol_records else {}
        if not symbol_records:
            status = "BLOCKER"
            reason_codes.append(f"unknown_symbol:{resolved}")
            blockers.append(f"{context}:unknown_symbol:{resolved}")
        else:
            if len(symbol_records) > 1:
                status = "BLOCKER"
                reason_codes.append(f"duplicate_symbol_record:{resolved}")
                blockers.append(f"{context}:duplicate_symbol_record:{resolved}")
            record_status = _record_status(record)
            if record_status and record_status not in {"active", "listed"}:
                status = "BLOCKER"
                reason_codes.append(f"inactive_symbol:{resolved}")
                blockers.append(f"{context}:inactive_symbol:{resolved}")
            if record.get("tradable") is None:
                reason_codes.append(f"missing_tradability_metadata:{resolved}")
            elif not _truthy(record.get("tradable")):
                status = "BLOCKER"
                reason_codes.append(f"non_tradable_symbol:{resolved}")
                blockers.append(f"{context}:non_tradable_symbol:{resolved}")
            if not _record_exchange(record):
                reason_codes.append(f"missing_exchange_metadata:{resolved}")
        rows.append(
            {
                "context": context,
                "original_symbol": original,
                "resolved_symbol": resolved,
                "status": status,
                "record_status": _record_status(record) or None,
                "tradable": record.get("tradable") if record else None,
                "exchange": _record_exchange(record) or None,
                "reason_codes": sorted(set(reason_codes)) or ["ok"],
            }
        )
    return rows, [alias_rows[key] for key in sorted(alias_rows)], blockers


def build_universe_governance(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
    max_age_days: int = MAX_UNIVERSE_AGE_DAYS,
) -> dict[str, Any]:
    repo = Path(repo_root)
    root = repo / SECURITY_MASTER_ROOT
    alias_path = repo / MANUAL_ALIAS_PATH
    aliases = load_manual_aliases(alias_path)
    universe = load_latest_ticker_universe(root)
    planned, planned_sources, planned_reasons = _planned_symbols(repo, trade_date)
    holdings, holdings_sources, holdings_reasons = _holdings_symbols(repo, trade_date)
    reasons: list[str] = list(planned_reasons + holdings_reasons)
    records: dict[str, list[dict[str, Any]]] = {}
    universe_path = None
    asof_date = None
    stale = False
    if universe is None:
        reasons.append("security_master_missing")
    else:
        universe_path = str(universe.get("_security_master_path") or "")
        asof_date = str(universe.get("asof_date") or "")
        age = _age_days(asof_date, trade_date)
        stale = age is None or age > int(max_age_days) or age < 0
        if stale:
            reasons.append(f"stale_security_master:{asof_date or 'unknown'}")
        for row in universe.get("symbols") or []:
            if not isinstance(row, dict):
                continue
            symbol = _symbol(row.get("symbol"))
            if symbol:
                records.setdefault(symbol, []).append(row)

    symbol_checks: list[dict[str, Any]] = []
    alias_resolutions: list[dict[str, str]] = []
    blockers: list[str] = []
    if records:
        planned_rows, planned_aliases, planned_blockers = _check_symbols(
            symbols=planned,
            context="planned",
            aliases=aliases,
            alias_source=str(alias_path),
            records=records,
        )
        holding_rows, holding_aliases, holding_blockers = _check_symbols(
            symbols=holdings,
            context="holdings",
            aliases=aliases,
            alias_source=str(alias_path),
            records=records,
        )
        symbol_checks.extend(planned_rows + holding_rows)
        alias_resolutions = sorted(
            {json.dumps(row, sort_keys=True): row for row in planned_aliases + holding_aliases}.values(),
            key=lambda row: (row["original_symbol"], row["resolved_symbol"]),
        )
        blockers.extend(planned_blockers + holding_blockers)

    duplicate_symbols = sorted(symbol for symbol, rows in records.items() if len(rows) > 1)
    blockers.extend(f"duplicate_symbol_record:{symbol}" for symbol in duplicate_symbols)
    if stale:
        blockers.append(f"stale_security_master:{asof_date or 'unknown'}")
    reason_codes = sorted({str(code) for code in reasons + blockers if code and code != "ok"}) or ["ok"]
    available = universe is not None and not blockers
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "available": available,
        "confidence": "LOW" if universe is None or blockers else "MEDIUM" if reason_codes != ["ok"] else "HIGH",
        "security_master_asof_date": asof_date,
        "security_master_path": universe_path,
        "stale_universe": stale,
        "planned_symbols": planned,
        "holdings_symbols": holdings,
        "symbol_checks": sorted(symbol_checks, key=lambda row: (row["context"], row["resolved_symbol"], row["original_symbol"])),
        "alias_resolutions": alias_resolutions,
        "duplicate_symbols": duplicate_symbols,
        "blockers": sorted(set(blockers)),
        "reason_codes": reason_codes,
        "source_artifacts": sorted(set(planned_sources + holdings_sources + ([universe_path] if universe_path else []) + ([str(alias_path)] if alias_path.exists() else []))),
        "coverage_summary": {
            "security_master_symbol_count": sum(len(rows) for rows in records.values()),
            "planned_symbol_count": len(planned),
            "holdings_symbol_count": len(holdings),
            "alias_resolution_count": len(alias_resolutions),
            "duplicate_symbol_count": len(duplicate_symbols),
        },
    }
    out_dir = (Path(output_root) if output_root is not None else repo / "outputs" / "research" / "universe_governance") / trade_date
    _write_json(out_dir / "universe_governance.json", payload)
    _write_text(out_dir / "universe_governance.md", render_markdown(payload))
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Universe Governance - {payload.get('date')}",
        "",
        f"- Available: {payload.get('available')}",
        f"- Confidence: {payload.get('confidence')}",
        f"- Security master as-of: {payload.get('security_master_asof_date')}",
        f"- Stale universe: {payload.get('stale_universe')}",
        f"- Blockers: {', '.join(payload.get('blockers') or [])}",
        f"- Reason codes: {', '.join(payload.get('reason_codes') or [])}",
        "",
        "| Context | Original | Resolved | Status | Tradable | Exchange | Reasons |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in payload.get("symbol_checks") or []:
        lines.append(
            f"| {row.get('context')} | {row.get('original_symbol')} | {row.get('resolved_symbol')} | {row.get('status')} | {row.get('tradable')} | {row.get('exchange')} | {', '.join(row.get('reason_codes') or [])} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build read-only universe governance artifacts.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_universe_governance(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(json.dumps({"date": args.date, "available": payload["available"], "confidence": payload["confidence"], "reason_codes": payload["reason_codes"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
