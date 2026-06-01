from __future__ import annotations

import csv
import datetime as dt
import json
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SECURITY_MASTER_ROOT = Path("data/security_master")
MANUAL_ALIAS_PATH = Path("data/security_master/manual_aliases.json")
LATEST_POINTER_PATH = SECURITY_MASTER_ROOT / "latest.json"
TICKER_UNIVERSE_LATEST_PATH = SECURITY_MASTER_ROOT / "ticker_universe_latest.json"
MAX_UNIVERSE_AGE_DAYS = 3

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
NASDAQ_OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"


@dataclass(frozen=True)
class SymbolResolutionResult:
    trades: list[dict[str, Any]]
    status: str
    reason: str
    symbol_aliases_applied: dict[str, str]
    unknown_symbols: list[str]
    inactive_symbols: list[str]
    non_tradable_symbols: list[str]
    stale_universe: bool
    universe_asof_date: str | None
    security_master_path: str | None
    warnings: list[str]

    def to_payload(self) -> dict[str, Any]:
        return {
            "security_master_resolution_status": self.status,
            "security_master_resolution_reason": self.reason,
            "symbol_aliases_applied": dict(sorted(self.symbol_aliases_applied.items())),
            "security_master_unknown_symbols": list(self.unknown_symbols),
            "security_master_inactive_symbols": list(self.inactive_symbols),
            "security_master_non_tradable_symbols": list(self.non_tradable_symbols),
            "security_master_stale_universe": bool(self.stale_universe),
            "security_master_asof_date": self.universe_asof_date,
            "security_master_path": self.security_master_path,
            "security_master_warnings": list(self.warnings),
        }


def _today_iso() -> str:
    return dt.date.today().isoformat()


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"json_payload_not_object:{path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


def _symbol(value: object) -> str:
    return str(value or "").strip().upper()


def normalize_asset_record(asset: dict[str, Any]) -> dict[str, Any]:
    symbol = _symbol(asset.get("symbol"))
    status = str(asset.get("status") or "").strip().lower().replace("assetstatus.", "")
    return {
        "symbol": symbol,
        "name": str(asset.get("name") or "").strip(),
        "exchange": str(asset.get("exchange") or "").strip(),
        "asset_class": str(asset.get("asset_class") or asset.get("class") or "").strip().lower(),
        "status": status,
        "tradable": _truthy(asset.get("tradable")),
        "marginable": _truthy(asset.get("marginable")),
        "shortable": _truthy(asset.get("shortable")),
        "easy_to_borrow": _truthy(asset.get("easy_to_borrow")),
    }


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def load_manual_aliases(path: Path = MANUAL_ALIAS_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    payload = _read_json(path)
    aliases = payload.get("aliases") or {}
    if not isinstance(aliases, dict):
        return {}
    return {
        _symbol(src): _symbol(dst)
        for src, dst in aliases.items()
        if _symbol(src) and _symbol(dst) and _symbol(src) != _symbol(dst)
    }


def _latest_universe_path(root: Path = SECURITY_MASTER_ROOT) -> Path | None:
    pointer = root / "latest.json"
    if pointer.exists():
        payload = _read_json(pointer)
        rel = str(payload.get("ticker_universe_path") or "").strip()
        if rel:
            candidate = Path(rel)
            if not candidate.is_absolute():
                candidate = root.parent.parent / candidate
            if candidate.exists():
                return candidate
    latest = root / "ticker_universe_latest.json"
    if latest.exists():
        return latest
    return None


def load_latest_ticker_universe(root: Path = SECURITY_MASTER_ROOT) -> dict[str, Any] | None:
    path = _latest_universe_path(root)
    if path is None:
        return None
    payload = _read_json(path)
    payload["_security_master_path"] = str(path)
    return payload


def _age_days(asof_date: str | None, today: str | None = None) -> int | None:
    if not asof_date:
        return None
    try:
        asof = dt.date.fromisoformat(str(asof_date))
        ref = dt.date.fromisoformat(today or _today_iso())
    except Exception:
        return None
    return int((ref - asof).days)


def resolve_trade_plan_symbols(
    trades: Iterable[dict[str, Any]],
    *,
    root: Path = SECURITY_MASTER_ROOT,
    alias_path: Path = MANUAL_ALIAS_PATH,
    today: str | None = None,
    max_age_days: int = MAX_UNIVERSE_AGE_DAYS,
    fail_on_stale: bool = False,
    fail_on_unknown_if_universe_available: bool = True,
) -> SymbolResolutionResult:
    aliases = load_manual_aliases(alias_path)
    universe = load_latest_ticker_universe(root)
    symbols: dict[str, dict[str, Any]] = {}
    asof_date = None
    universe_path = None
    stale = False
    warnings: list[str] = []
    if universe:
        asof_date = str(universe.get("asof_date") or "")
        universe_path = str(universe.get("_security_master_path") or "")
        for record in universe.get("symbols") or []:
            if isinstance(record, dict) and _symbol(record.get("symbol")):
                symbols[_symbol(record.get("symbol"))] = record
        age = _age_days(asof_date, today=today)
        stale = age is None or age > int(max_age_days)
        if stale:
            warnings.append(f"stale_security_master:{asof_date or 'unknown'}")
    else:
        warnings.append("security_master_unavailable")

    resolved_trades: list[dict[str, Any]] = []
    applied: dict[str, str] = {}
    unknown: set[str] = set()
    inactive: set[str] = set()
    non_tradable: set[str] = set()

    for trade in trades or []:
        row = dict(trade or {})
        original = _symbol(row.get("ticker") or row.get("symbol"))
        resolved = aliases.get(original, original)
        if original and resolved and resolved != original:
            applied[original] = resolved
            row["original_ticker"] = original
            row["symbol_resolution_reason"] = f"manual_alias:{original}->{resolved}"
        if resolved:
            row["ticker"] = resolved
        if symbols and resolved:
            record = symbols.get(resolved)
            if not record:
                unknown.add(resolved)
            else:
                status = str(record.get("status") or "").strip().lower()
                if status and status != "active":
                    inactive.add(resolved)
                if not _truthy(record.get("tradable")):
                    non_tradable.add(resolved)
        resolved_trades.append(row)

    failed = bool(inactive or non_tradable)
    if symbols and fail_on_unknown_if_universe_available and unknown:
        failed = True
    if stale and fail_on_stale:
        failed = True

    reasons: list[str] = []
    if applied:
        reasons.append("symbol_aliases_applied")
    if unknown:
        reasons.extend(f"unknown_symbol:{sym}" for sym in sorted(unknown))
    if inactive:
        reasons.extend(f"inactive_symbol:{sym}" for sym in sorted(inactive))
    if non_tradable:
        reasons.extend(f"non_tradable_symbol:{sym}" for sym in sorted(non_tradable))
    if stale:
        reasons.append(f"stale_security_master:{asof_date or 'unknown'}")
    if not reasons:
        reasons.append("all_symbols_resolved")

    status = "FAIL" if failed else ("WARN" if warnings or stale else "PASS")
    return SymbolResolutionResult(
        trades=resolved_trades,
        status=status,
        reason=";".join(reasons),
        symbol_aliases_applied=applied,
        unknown_symbols=sorted(unknown),
        inactive_symbols=sorted(inactive),
        non_tradable_symbols=sorted(non_tradable),
        stale_universe=stale,
        universe_asof_date=asof_date or None,
        security_master_path=universe_path or None,
        warnings=warnings,
    )


def parse_nasdaq_symbol_directory(text: str, *, source: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    clean_lines = [
        line
        for line in str(text or "").splitlines()
        if line.strip() and not line.startswith("File Creation Time:")
    ]
    if not clean_lines:
        return rows
    reader = csv.DictReader(clean_lines, delimiter="|")
    for row in reader:
        raw_symbol = row.get("Symbol") or row.get("ACT Symbol") or row.get("Nasdaq Traded")
        symbol = _symbol(raw_symbol)
        if not symbol or symbol == "FILE CREATION TIME":
            continue
        test_issue = str(row.get("Test Issue") or "").strip().upper() == "Y"
        rows.append(
            {
                "symbol": symbol,
                "security_name": str(row.get("Security Name") or row.get("Security Name") or "").strip(),
                "listing_exchange": str(row.get("Listing Exchange") or "NASDAQ").strip(),
                "market_category": str(row.get("Market Category") or "").strip(),
                "etf": str(row.get("ETF") or "").strip().upper() == "Y",
                "test_issue": test_issue,
                "nasdaq_financial_status": str(row.get("Financial Status") or "").strip(),
                "round_lot_size": str(row.get("Round Lot Size") or "").strip(),
                "source": source,
            }
        )
    return rows


def fetch_nasdaq_symbol_directories(
    *,
    listed_url: str = NASDAQ_LISTED_URL,
    other_url: str = NASDAQ_OTHER_LISTED_URL,
    timeout_seconds: int = 20,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for url, source in ((listed_url, "nasdaqlisted"), (other_url, "otherlisted")):
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            text = response.read().decode("utf-8", errors="replace")
        records.extend(parse_nasdaq_symbol_directory(text, source=source))
    deduped: dict[str, dict[str, Any]] = {}
    for record in records:
        deduped[_symbol(record.get("symbol"))] = record
    return [deduped[key] for key in sorted(deduped)]


def build_ticker_universe_snapshot(
    *,
    asof_date: str,
    alpaca_assets: Iterable[dict[str, Any]],
    nasdaq_records: Iterable[dict[str, Any]],
    previous_universe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    alpaca_by_symbol = {
        _symbol(record.get("symbol")): normalize_asset_record(record)
        for record in alpaca_assets or []
        if _symbol(record.get("symbol"))
    }
    nasdaq_by_symbol = {
        _symbol(record.get("symbol")): dict(record)
        for record in nasdaq_records or []
        if _symbol(record.get("symbol"))
    }
    all_symbols = sorted(set(alpaca_by_symbol) | set(nasdaq_by_symbol))
    symbols: list[dict[str, Any]] = []
    for symbol in all_symbols:
        alpaca = alpaca_by_symbol.get(symbol) or {}
        nasdaq = nasdaq_by_symbol.get(symbol) or {}
        symbols.append(
            {
                "symbol": symbol,
                "name": alpaca.get("name") or nasdaq.get("security_name") or "",
                "alpaca_status": alpaca.get("status"),
                "status": alpaca.get("status") or ("listed" if nasdaq else "unknown"),
                "tradable": bool(alpaca.get("tradable")),
                "asset_class": alpaca.get("asset_class"),
                "exchange": alpaca.get("exchange") or nasdaq.get("listing_exchange") or "",
                "listed": bool(nasdaq),
                "nasdaq": nasdaq,
                "alpaca": alpaca,
            }
        )

    events = detect_symbol_change_events(
        current_symbols=symbols,
        previous_universe=previous_universe,
    )
    return {
        "schema_version": "security-master-v1",
        "asof_date": str(asof_date),
        "generated_at_utc": _utc_now_iso(),
        "sources": {
            "alpaca_assets": "alpaca:/v2/assets",
            "nasdaq_symbol_directory": [NASDAQ_LISTED_URL, NASDAQ_OTHER_LISTED_URL],
        },
        "counts": {
            "symbols": len(symbols),
            "alpaca_assets": len(alpaca_by_symbol),
            "nasdaq_records": len(nasdaq_by_symbol),
            "active_tradable": sum(
                1
                for row in symbols
                if str(row.get("status") or "").lower() == "active" and bool(row.get("tradable"))
            ),
        },
        "symbols": symbols,
        "events": events,
    }


def detect_symbol_change_events(
    *,
    current_symbols: list[dict[str, Any]],
    previous_universe: dict[str, Any] | None,
) -> dict[str, list[dict[str, Any]]]:
    current = {_symbol(row.get("symbol")): row for row in current_symbols if _symbol(row.get("symbol"))}
    prev_rows = (previous_universe or {}).get("symbols") or []
    previous = {
        _symbol(row.get("symbol")): row
        for row in prev_rows
        if isinstance(row, dict) and _symbol(row.get("symbol"))
    }
    additions = [
        {"symbol": symbol, "name": current[symbol].get("name")}
        for symbol in sorted(set(current) - set(previous))
    ]
    deletions = [
        {"symbol": symbol, "name": previous[symbol].get("name")}
        for symbol in sorted(set(previous) - set(current))
    ]
    inactive = [
        {
            "symbol": symbol,
            "status": row.get("status"),
            "tradable": row.get("tradable"),
            "name": row.get("name"),
        }
        for symbol, row in sorted(current.items())
        if str(row.get("status") or "").lower() not in {"", "active", "listed"}
        or not bool(row.get("tradable"))
    ]
    name_changes: list[dict[str, Any]] = []
    for symbol in sorted(set(current) & set(previous)):
        old_name = str(previous[symbol].get("name") or "").strip()
        new_name = str(current[symbol].get("name") or "").strip()
        if old_name and new_name and old_name != new_name:
            name_changes.append(
                {
                    "symbol": symbol,
                    "previous_name": old_name,
                    "current_name": new_name,
                }
            )
    return {
        "additions": additions,
        "deletions": deletions,
        "inactive_symbols": inactive,
        "name_changes": name_changes,
    }


def persist_security_master_snapshot(
    *,
    snapshot: dict[str, Any],
    root: Path = SECURITY_MASTER_ROOT,
) -> dict[str, str]:
    asof_date = str(snapshot.get("asof_date") or "").strip()
    if not asof_date:
        raise ValueError("snapshot_asof_date_missing")
    dated_dir = root / asof_date
    universe_path = dated_dir / "ticker_universe.json"
    events_path = dated_dir / "symbol_change_events.json"
    alpaca_path = dated_dir / "alpaca_assets.json"
    nasdaq_path = dated_dir / "nasdaq_symbol_directory.json"

    _write_json(universe_path, snapshot)
    _write_json(events_path, {"asof_date": asof_date, "events": snapshot.get("events") or {}})
    _write_json(alpaca_path, {"asof_date": asof_date, "assets": [row.get("alpaca") for row in snapshot.get("symbols") or [] if row.get("alpaca")]})
    _write_json(nasdaq_path, {"asof_date": asof_date, "records": [row.get("nasdaq") for row in snapshot.get("symbols") or [] if row.get("nasdaq")]})

    latest_universe = root / "ticker_universe_latest.json"
    latest_events = root / "symbol_change_events.json"
    shutil.copyfile(universe_path, latest_universe)
    shutil.copyfile(events_path, latest_events)
    pointer = {
        "asof_date": asof_date,
        "updated_at_utc": _utc_now_iso(),
        "snapshot_dir": str(dated_dir),
        "ticker_universe_path": str(universe_path),
        "symbol_change_events_path": str(events_path),
    }
    _write_json(root / "latest.json", pointer)
    return pointer


def update_security_master(
    *,
    asof_date: str,
    alpaca_assets: Iterable[dict[str, Any]],
    nasdaq_records: Iterable[dict[str, Any]],
    root: Path = SECURITY_MASTER_ROOT,
) -> dict[str, Any]:
    previous = load_latest_ticker_universe(root)
    snapshot = build_ticker_universe_snapshot(
        asof_date=asof_date,
        alpaca_assets=alpaca_assets,
        nasdaq_records=nasdaq_records,
        previous_universe=previous,
    )
    pointer = persist_security_master_snapshot(snapshot=snapshot, root=root)
    return {"snapshot": snapshot, "pointer": pointer}
