"""Security master reconciliation — Workstream 2.

Verifies every symbol that the various Caerus governance/execution
artifacts reference is present in the security master, and flags
stale aliases, missing metadata, duplicates and inactive mappings.

Research-only. Reads the security master + downstream artifacts and
emits a diagnostic report. Does NOT modify the security master.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "caerus_security_master_reconciliation_v1"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except Exception:
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _normalize(symbol: Any) -> str:
    return str(symbol or "").strip().upper()


def _collect_symbols(*iterables: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    for it in iterables:
        if not it:
            continue
        for s in it:
            norm = _normalize(s)
            if norm:
                seen.add(norm)
    return sorted(seen)


def _load_security_master(repo: Path) -> tuple[dict[str, Any] | None, list[str], str | None]:
    """Return (universe_payload, symbols_in_master, security_master_asof_date)."""
    sm_root = repo / "data" / "security_master"
    ticker_path = sm_root / "ticker_universe_latest.json"
    latest_pointer = sm_root / "latest.json"
    payload = _read_json(ticker_path)
    if payload is None:
        payload = _read_json(latest_pointer)
        if isinstance(payload, dict) and payload.get("ticker_universe_path"):
            payload = _read_json(repo / str(payload.get("ticker_universe_path")))
    if not isinstance(payload, dict):
        return None, [], None
    asof = payload.get("asof_date") or payload.get("as_of") or payload.get("date")
    symbols: list[str] = []
    records = payload.get("records") if isinstance(payload.get("records"), list) else payload.get("tickers")
    if isinstance(records, list):
        for row in records:
            if isinstance(row, dict):
                sym = _normalize(row.get("symbol"))
                if sym:
                    symbols.append(sym)
            else:
                sym = _normalize(row)
                if sym:
                    symbols.append(sym)
    return payload, symbols, str(asof) if asof else None


def _load_manual_aliases(repo: Path) -> dict[str, str]:
    path = repo / "data" / "security_master" / "manual_aliases.json"
    data = _read_json(path)
    if not isinstance(data, dict):
        return {}
    aliases = data.get("aliases") if isinstance(data.get("aliases"), dict) else {}
    return {_normalize(k): _normalize(v) for k, v in aliases.items() if k and v}


def _load_shadow_holdings(repo: Path, trade_date: str) -> tuple[list[str], dict[str, list[str]], str | None]:
    """Walk shadow comparison artifacts for any date <= trade_date and
    return (all_holdings_symbols, per_strategy_symbols, selected_date)."""
    root = repo / "outputs" / "shadow_candidates"
    if not root.exists():
        return [], {}, None
    candidate_dates: list[str] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            from datetime import date as _date

            _date.fromisoformat(child.name)
        except Exception:
            continue
        if child.name > trade_date:
            continue
        if (child / "comparison.json").exists():
            candidate_dates.append(child.name)
    if not candidate_dates:
        return [], {}, None
    # Walk newest-first, skipping comparisons whose strategies block is
    # empty (those are status/error payloads, not real holdings).
    selected = None
    payload: dict[str, Any] | None = None
    for candidate in reversed(candidate_dates):
        candidate_payload = _read_json(root / candidate / "comparison.json")
        if isinstance(candidate_payload, dict) and isinstance(candidate_payload.get("strategies"), dict) and candidate_payload.get("strategies"):
            selected = candidate
            payload = candidate_payload
            break
    if payload is None:
        return [], {}, candidate_dates[-1]
    strategies = payload.get("strategies") or {}
    per_strategy: dict[str, list[str]] = {}
    all_holdings: set[str] = set()
    for strategy, row in strategies.items():
        holdings = (row or {}).get("holdings") or []
        symbols = sorted({_normalize(h.get("ticker")) for h in holdings if isinstance(h, dict) and h.get("ticker")})
        per_strategy[strategy] = [s for s in symbols if s]
        all_holdings.update(per_strategy[strategy])
    return sorted(all_holdings), per_strategy, selected


def _load_planned_symbols(repo: Path, trade_date: str) -> tuple[list[str], str | None]:
    payload = _read_json(repo / "outputs" / "precompute" / trade_date / "planned_execution_payload.json")
    if not isinstance(payload, dict):
        # Fall back to most recent payload.
        precompute_root = repo / "outputs" / "precompute"
        if precompute_root.exists():
            for child in sorted(precompute_root.iterdir(), reverse=True):
                if child.is_dir() and (child / "planned_execution_payload.json").exists():
                    p2 = _read_json(child / "planned_execution_payload.json")
                    if isinstance(p2, dict):
                        return _collect_symbols(
                            (o.get("ticker") or o.get("symbol") for o in p2.get("trades", []) if isinstance(o, dict)),
                            (o.get("ticker") or o.get("symbol") for o in p2.get("orders", []) if isinstance(o, dict)),
                        ), child.name
        return [], None
    return _collect_symbols(
        (o.get("ticker") or o.get("symbol") for o in payload.get("trades", []) if isinstance(o, dict)),
        (o.get("ticker") or o.get("symbol") for o in payload.get("orders", []) if isinstance(o, dict)),
    ), trade_date


def _load_attribution_symbols(repo: Path, trade_date: str) -> list[str]:
    payload = _read_json(repo / "outputs" / "attribution" / trade_date / "position_attribution.json")
    if not isinstance(payload, dict):
        return []
    rows = payload.get("positions") if isinstance(payload.get("positions"), list) else payload.get("rows")
    if not isinstance(rows, list):
        return []
    return _collect_symbols(r.get("symbol") or r.get("ticker") for r in rows if isinstance(r, dict))


def _load_timing_symbols(repo: Path, trade_date: str) -> list[str]:
    payload = _read_json(repo / "outputs" / "research" / "execution_timing" / trade_date / "execution_timing_summary.json")
    if not isinstance(payload, dict):
        return []
    return _collect_symbols(payload.get("symbols_missing_bars") or [])


def build_security_master_reconciliation(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root)
    universe_payload, master_symbols, sm_asof = _load_security_master(repo)
    master_set = set(master_symbols)
    aliases = _load_manual_aliases(repo)
    holdings_symbols, per_strategy_holdings, shadow_date = _load_shadow_holdings(repo, trade_date)
    planned_symbols, planned_date = _load_planned_symbols(repo, trade_date)
    attribution_symbols = _load_attribution_symbols(repo, trade_date)
    timing_symbols = _load_timing_symbols(repo, trade_date)

    all_symbols = sorted(set(holdings_symbols + planned_symbols + attribution_symbols + timing_symbols))

    def _resolve(symbol: str) -> str:
        norm = _normalize(symbol)
        return aliases.get(norm, norm)

    symbol_checks: list[dict[str, Any]] = []
    unknown_symbols: list[str] = []
    aliased_symbols: list[dict[str, str]] = []
    for symbol in all_symbols:
        resolved = _resolve(symbol)
        in_master = bool(master_set) and resolved in master_set
        contexts: list[str] = []
        if symbol in holdings_symbols:
            contexts.append("holdings")
        if symbol in planned_symbols:
            contexts.append("planned")
        if symbol in attribution_symbols:
            contexts.append("attribution")
        if symbol in timing_symbols:
            contexts.append("timing_missing_bars")
        if not master_set:
            status = "unknown_master_unavailable"
        elif in_master:
            status = "ok"
        else:
            status = "unknown_symbol"
            unknown_symbols.append(symbol)
        if resolved != symbol:
            aliased_symbols.append({"original": symbol, "resolved": resolved})
        symbol_checks.append(
            {
                "symbol": symbol,
                "resolved_symbol": resolved,
                "in_master": in_master,
                "contexts": contexts,
                "status": status,
            }
        )

    duplicates = [sym for sym, count in Counter(master_symbols).items() if count > 1]
    inactive_aliases = [
        {"original": k, "resolved": v}
        for k, v in aliases.items()
        if master_set and v not in master_set
    ]

    reason_codes: list[str] = []
    if not master_set:
        reason_codes.append("security_master_unavailable")
    if unknown_symbols:
        reason_codes.append("unknown_symbols_present")
    if duplicates:
        reason_codes.append("duplicate_master_records")
    if inactive_aliases:
        reason_codes.append("inactive_aliases_present")
    if not reason_codes:
        reason_codes.append("ok")

    coverage = {
        "holdings_symbol_count": len(holdings_symbols),
        "planned_symbol_count": len(planned_symbols),
        "attribution_symbol_count": len(attribution_symbols),
        "timing_missing_bars_count": len(timing_symbols),
        "unique_symbol_count": len(all_symbols),
        "master_record_count": len(master_symbols),
        "unknown_symbol_count": len(unknown_symbols),
        "duplicate_count": len(duplicates),
        "inactive_alias_count": len(inactive_aliases),
        "shadow_holdings_date": shadow_date,
        "planned_payload_date": planned_date,
    }

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "available": bool(master_set),
        "confidence": "HIGH" if master_set and not reason_codes else "MEDIUM" if master_set else "LOW",
        "security_master_asof_date": sm_asof,
        "coverage": coverage,
        "symbol_checks": symbol_checks,
        "unknown_symbols": sorted(unknown_symbols),
        "duplicates": sorted(duplicates),
        "inactive_aliases": inactive_aliases,
        "aliased_symbols": aliased_symbols,
        "per_strategy_holdings": per_strategy_holdings,
        "reason_codes": sorted(set(reason_codes)),
        "source_artifacts": sorted(
            p
            for p, present in [
                ("data/security_master/", bool(master_symbols)),
                ("data/security_master/manual_aliases.json", bool(aliases)),
                (f"outputs/shadow_candidates/{shadow_date}/comparison.json" if shadow_date else "", bool(shadow_date)),
                (f"outputs/precompute/{planned_date}/planned_execution_payload.json" if planned_date else "", bool(planned_date)),
                (f"outputs/attribution/{trade_date}/position_attribution.json", bool(attribution_symbols)),
                (f"outputs/research/execution_timing/{trade_date}/execution_timing_summary.json", bool(timing_symbols)),
            ]
            if p
        ),
    }

    out_dir = (Path(output_root) if output_root is not None else repo / "outputs" / "research" / "security_master_reconciliation") / trade_date
    _write_json(out_dir / "security_master_reconciliation.json", payload)
    _write_text(out_dir / "security_master_reconciliation.md", render_markdown(payload))
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    cov = payload.get("coverage") or {}
    lines = [
        f"# Security Master Reconciliation - {payload.get('date')}",
        "",
        f"- Available: {payload.get('available')}",
        f"- Confidence: {payload.get('confidence')}",
        f"- Security master as-of: {payload.get('security_master_asof_date')}",
        f"- Reason codes: {', '.join(payload.get('reason_codes') or [])}",
        "",
        "## Coverage",
        "",
        "| Source | Count |",
        "|---|---:|",
        f"| Holdings symbols | {cov.get('holdings_symbol_count')} |",
        f"| Planned symbols | {cov.get('planned_symbol_count')} |",
        f"| Attribution symbols | {cov.get('attribution_symbol_count')} |",
        f"| Timing missing-bars symbols | {cov.get('timing_missing_bars_count')} |",
        f"| Unique symbols overall | {cov.get('unique_symbol_count')} |",
        f"| Security master records | {cov.get('master_record_count')} |",
        f"| Unknown symbols | {cov.get('unknown_symbol_count')} |",
        f"| Duplicate master records | {cov.get('duplicate_count')} |",
        f"| Inactive aliases | {cov.get('inactive_alias_count')} |",
        "",
        "## Symbol Checks",
        "",
        "| Symbol | Resolved | In Master | Contexts | Status |",
        "|---|---|---|---|---|",
    ]
    for row in payload.get("symbol_checks") or []:
        lines.append(
            f"| {row.get('symbol')} | {row.get('resolved_symbol')} | {row.get('in_master')} | {', '.join(row.get('contexts') or [])} | {row.get('status')} |"
        )
    if payload.get("unknown_symbols"):
        lines += ["", "## Unknown Symbols", "", ", ".join(payload["unknown_symbols"])]
    if payload.get("inactive_aliases"):
        lines += ["", "## Inactive Aliases", "", "| Original | Resolved |", "|---|---|"]
        for alias in payload["inactive_aliases"]:
            lines.append(f"| {alias.get('original')} | {alias.get('resolved')} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile every governance-referenced symbol against the security master.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_security_master_reconciliation(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(
        json.dumps(
            {
                "date": args.date,
                "available": payload["available"],
                "confidence": payload["confidence"],
                "coverage": payload["coverage"],
                "reason_codes": payload["reason_codes"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
