#!/usr/bin/env python3
"""Read-only, artifact-grounded candidate comparison diagnostic.

This command reads existing universe, security-master, Shadow, precompute, and
paper-broker snapshot artifacts.  It never imports strategy, allocation,
execution, scheduler, or broker modules; it never writes an artifact; and it
never reconstructs a score that was not persisted by an upstream producer.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "caerus_candidate_comparison_diagnostic_v1"
DATE_FORMAT = "%Y-%m-%d"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return dt.datetime.strptime(str(value), DATE_FORMAT).date().isoformat()
    except ValueError:
        return None


def _dated_dirs(root: Path, *, as_of: str | None) -> list[Path]:
    ceiling = _iso_date(as_of)
    if not root.is_dir():
        return []
    found: list[Path] = []
    for path in root.iterdir():
        if not path.is_dir() or _iso_date(path.name) is None:
            continue
        if ceiling is None or path.name <= ceiling:
            found.append(path)
    return sorted(found, key=lambda path: path.name, reverse=True)


def _display_path(path: Path | None, repo_root: Path) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def _normalize_tickers(tickers: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in tickers:
        ticker = str(raw).strip().upper()
        if ticker and ticker not in seen:
            seen.add(ticker)
            output.append(ticker)
    if not output:
        raise ValueError("at least one ticker is required")
    return output


def _load_active_universe(repo_root: Path) -> tuple[set[str], Path]:
    path = repo_root / "data" / "universe.csv"
    symbols: set[str] = set()
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                symbol = str(row.get("ticker") or "").strip().upper()
                if symbol:
                    symbols.add(symbol)
    except OSError:
        pass
    return symbols, path


def _read_security_master_subset(path: Path, *, tickers: set[str]) -> dict[str, Any] | None:
    """Read only requested symbol rows without materializing the large artifact.

    The production security master also contains large event and provenance
    arrays.  Expanding all of them with ``json.load`` is unnecessary for this
    diagnostic and can create avoidable memory pressure on the scheduler VM.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    date_match = re.search(r'"asof_date"\s*:\s*"([0-9]{4}-[0-9]{2}-[0-9]{2})"', text)
    symbols_match = re.search(r'"symbols"\s*:\s*\[', text)
    if symbols_match is None:
        return None
    decoder = json.JSONDecoder()
    index = symbols_match.end()
    selected: list[dict[str, Any]] = []
    while index < len(text):
        while index < len(text) and text[index] in " \t\r\n,":
            index += 1
        if index >= len(text) or text[index] == "]":
            break
        try:
            row, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            return None
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        if symbol in tickers:
            selected.append(row)
            if len(selected) == len(tickers):
                break
    return {
        "asof_date": date_match.group(1) if date_match else None,
        "symbols": selected,
    }


def _latest_security_master(
    repo_root: Path,
    *,
    as_of: str | None,
    tickers: set[str],
) -> tuple[dict[str, Any], Path | None]:
    root = repo_root / "data" / "security_master"
    for directory in _dated_dirs(root, as_of=as_of):
        path = directory / "ticker_universe.json"
        payload = _read_security_master_subset(path, tickers=tickers)
        if payload is not None:
            return payload, path
    latest_path = root / "ticker_universe_latest.json"
    payload = _read_security_master_subset(latest_path, tickers=tickers)
    if payload is not None:
        artifact_date = _iso_date(str(payload.get("asof_date") or ""))
        if as_of is None or artifact_date is None or artifact_date <= str(as_of):
            return payload, latest_path
    return {}, None


def _security_master_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for row in payload.get("symbols") or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        if symbol:
            output[symbol] = row
    return output


def _shadow_attempt_status(directory: Path | None) -> dict[str, Any]:
    if directory is None:
        return {"date": None, "status": "MISSING", "reason_code": "NO_SHADOW_ARTIFACT"}
    comparison = _read_json(directory / "comparison.json") or {}
    strategy_files = sorted(directory.glob("caerus_*.json"))
    has_rank_table = any(
        isinstance((_read_json(path) or {}).get("rank_table"), list)
        and bool((_read_json(path) or {}).get("rank_table"))
        for path in strategy_files
    )
    status = str(comparison.get("status") or ("OK" if has_rank_table else "NO_DATA"))
    return {
        "date": directory.name,
        "status": status,
        "reason_code": comparison.get("reason_code"),
        "has_persisted_rank_table": has_rank_table,
    }


def _latest_valid_shadow(repo_root: Path, *, as_of: str | None) -> tuple[Path | None, dict[str, Any]]:
    directories = _dated_dirs(repo_root / "outputs" / "shadow_candidates", as_of=as_of)
    latest_attempt = _shadow_attempt_status(directories[0] if directories else None)
    for directory in directories:
        for path in sorted(directory.glob("caerus_*.json")):
            payload = _read_json(path) or {}
            if isinstance(payload.get("rank_table"), list) and payload.get("rank_table"):
                return directory, latest_attempt
    return None, latest_attempt


def _load_shadow_strategies(directory: Path | None) -> list[dict[str, Any]]:
    if directory is None:
        return []
    strategies: list[dict[str, Any]] = []
    for path in sorted(directory.glob("caerus_*.json")):
        payload = _read_json(path) or {}
        rank_table = payload.get("rank_table")
        if not isinstance(rank_table, list):
            continue
        strategies.append(
            {
                "strategy_id": str(payload.get("strategy_slug") or path.stem),
                "rank_table": rank_table,
                "target_weights": payload.get("target_weights") or {},
                "path": path,
            }
        )
    return strategies


def _latest_precompute(repo_root: Path, *, as_of: str | None) -> tuple[Path | None, dict[str, Any], dict[str, Any]]:
    for directory in _dated_dirs(repo_root / "outputs" / "precompute", as_of=as_of):
        signals = _read_json(directory / "signals.json")
        payload = _read_json(directory / "planned_execution_payload.json")
        if signals is not None or payload is not None:
            return directory, signals or {}, payload or {}
    return None, {}, {}


def _latest_broker_snapshot(repo_root: Path, *, as_of: str | None) -> tuple[Path | None, dict[str, Any]]:
    paths = list((repo_root / "outputs" / "paper_lane" / "runs").glob("*/live_pilot_broker_snapshot_post.json"))
    candidates: list[tuple[str, Path, dict[str, Any]]] = []
    for path in paths:
        payload = _read_json(path)
        if payload is None:
            continue
        captured = str(payload.get("captured_at") or path.parent.name)
        captured_date = _iso_date(captured[:10])
        if as_of is not None and captured_date is not None and captured_date > as_of:
            continue
        candidates.append((captured, path, payload))
    if not candidates:
        return None, {}
    _, path, payload = max(candidates, key=lambda item: (item[0], str(item[1])))
    return path, payload


def _symbol_from_row(row: dict[str, Any]) -> str:
    return str(row.get("ticker") or row.get("symbol") or "").strip().upper()


def _final_targets(signals: dict[str, Any]) -> tuple[dict[str, int], set[str]]:
    ranks: dict[str, int] = {}
    symbols: set[str] = set()
    for row in signals.get("signals") or []:
        if not isinstance(row, dict):
            continue
        symbol = _symbol_from_row(row)
        if not symbol or symbol == "CASH":
            continue
        symbols.add(symbol)
        ranks.setdefault(symbol, len(ranks) + 1)
    return ranks, symbols


def _planned_buys(payload: dict[str, Any]) -> set[str]:
    output: set[str] = set()
    for row in payload.get("trades") or []:
        if isinstance(row, dict) and str(row.get("side") or "").upper() == "BUY":
            symbol = _symbol_from_row(row)
            if symbol:
                output.add(symbol)
    return output


def _current_holdings(snapshot: dict[str, Any]) -> set[str]:
    output: set[str] = set()
    for row in snapshot.get("positions") or []:
        if not isinstance(row, dict):
            continue
        symbol = _symbol_from_row(row)
        try:
            quantity = float(row.get("qty") if row.get("qty") is not None else row.get("quantity", 1.0))
        except (TypeError, ValueError):
            quantity = 0.0
        if symbol and quantity != 0.0:
            output.add(symbol)
    return output


def _shadow_evidence(ticker: str, strategies: list[dict[str, Any]], repo_root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for strategy in strategies:
        match = next(
            (
                row
                for row in strategy["rank_table"]
                if isinstance(row, dict) and _symbol_from_row(row) == ticker
            ),
            None,
        )
        if match is None:
            continue
        rows.append(
            {
                "strategy_id": strategy["strategy_id"],
                "score": match.get("momentum_score"),
                "rank": match.get("momentum_rank"),
                "selected": bool(match.get("is_selected")),
                "target_weight": (strategy.get("target_weights") or {}).get(ticker),
                "source": _display_path(strategy["path"], repo_root),
            }
        )
    ranked = [row for row in rows if isinstance(row.get("rank"), (int, float))]
    best_rank = min((float(row["rank"]) for row in ranked), default=None)
    best_rows = [row for row in ranked if float(row["rank"]) == best_rank] if best_rank is not None else []
    return {
        "best_rank": int(best_rank) if best_rank is not None and best_rank.is_integer() else best_rank,
        "latest_score": best_rows[0].get("score") if best_rows else None,
        "best_sleeves": [row["strategy_id"] for row in best_rows],
        "selected_by_any_sleeve": any(row["selected"] for row in rows),
        "sleeves": rows,
    }


def _primary_blocker(
    *,
    universe_eligible: bool,
    valid_shadow_available: bool,
    shadow: dict[str, Any],
    final_target: bool,
    planned_buy: bool,
) -> str:
    if not universe_eligible:
        return "NOT_IN_ACTIVE_MODEL_UNIVERSE"
    if final_target:
        return "NONE_PLANNED_BUY" if planned_buy else "NONE_CURRENT_PRODUCTION_TARGET"
    if not valid_shadow_available:
        return "NO_VALID_SHADOW_ARTIFACT"
    if shadow.get("best_rank") is None:
        return "SHADOW_SCORE_NOT_PERSISTED_OR_SIGNAL_NOT_READY"
    if shadow.get("selected_by_any_sleeve") and not final_target:
        return "SHADOW_ONLY_NOT_IN_PRODUCTION_TARGET"
    return "NOT_SELECTED_BY_PRODUCTION_PIPELINE"


def build_candidate_comparison(
    *,
    tickers: Iterable[str],
    repo_root: str | Path,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Build the comparison entirely from existing artifacts without writes."""
    normalized = _normalize_tickers(tickers)
    root = Path(repo_root).expanduser().resolve()
    ceiling = _iso_date(as_of)
    if as_of is not None and ceiling is None:
        raise ValueError(f"invalid --as-of date: {as_of!r}")

    universe, universe_path = _load_active_universe(root)
    security_master, security_master_path = _latest_security_master(
        root,
        as_of=ceiling,
        tickers=set(normalized),
    )
    security_index = _security_master_index(security_master)
    shadow_dir, latest_shadow_attempt = _latest_valid_shadow(root, as_of=ceiling)
    strategies = _load_shadow_strategies(shadow_dir)
    precompute_dir, signals, execution_payload = _latest_precompute(root, as_of=ceiling)
    broker_path, broker_snapshot = _latest_broker_snapshot(root, as_of=ceiling)
    final_ranks, final_symbols = _final_targets(signals)
    planned_buys = _planned_buys(execution_payload)
    current_holdings = _current_holdings(broker_snapshot)

    candidates: list[dict[str, Any]] = []
    for ticker in normalized:
        security = security_index.get(ticker) or {}
        status = str(security.get("status") or security.get("alpaca_status") or "").lower()
        universe_eligible = ticker in universe
        shadow = _shadow_evidence(ticker, strategies, root)
        final_target = ticker in final_symbols
        planned_buy = ticker in planned_buys
        candidates.append(
            {
                "ticker": ticker,
                "universe_eligible": universe_eligible,
                "security_master_present": bool(security),
                "security_master_active": status in {"active", "listed"},
                "security_master_tradable": security.get("tradable"),
                "best_sleeves": shadow["best_sleeves"],
                "latest_score": shadow["latest_score"],
                "sleeve_rank": shadow["best_rank"],
                "shadow_selected": shadow["selected_by_any_sleeve"],
                "sleeve_evidence": shadow["sleeves"],
                "final_rank": final_ranks.get(ticker),
                "production_target": final_target,
                "current_holding": ticker in current_holdings,
                "planned_next_buy": planned_buy,
                "primary_blocker": _primary_blocker(
                    universe_eligible=universe_eligible,
                    valid_shadow_available=shadow_dir is not None,
                    shadow=shadow,
                    final_target=final_target,
                    planned_buy=planned_buy,
                ),
                "confidence": "HIGH" if (not universe_eligible or shadow["best_rank"] is not None or final_target) else "MEDIUM",
            }
        )

    missing_sources = [
        name
        for name, path in (
            ("active_universe", universe_path if universe_path.exists() else None),
            ("security_master", security_master_path),
            ("valid_shadow", shadow_dir),
            ("precompute", precompute_dir),
            ("broker_snapshot", broker_path),
        )
        if path is None
    ]
    status = "OK" if not missing_sources else "PARTIAL"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "mode": "READ_ONLY_EXISTING_ARTIFACTS",
        "as_of": ceiling,
        "tickers": normalized,
        "artifact_dates": {
            "security_master": security_master.get("asof_date"),
            "latest_shadow_attempt": latest_shadow_attempt,
            "latest_valid_shadow": shadow_dir.name if shadow_dir else None,
            "precompute": precompute_dir.name if precompute_dir else None,
            "broker_snapshot_captured_at": broker_snapshot.get("captured_at"),
        },
        "sources": {
            "active_universe": _display_path(universe_path, root),
            "security_master": _display_path(security_master_path, root),
            "valid_shadow_directory": _display_path(shadow_dir, root),
            "precompute_signals": _display_path(precompute_dir / "signals.json", root) if precompute_dir else None,
            "planned_execution_payload": _display_path(precompute_dir / "planned_execution_payload.json", root) if precompute_dir else None,
            "broker_snapshot": _display_path(broker_path, root),
        },
        "missing_sources": missing_sources,
        "limitations": [
            "Scores and ranks are reported only when persisted in a Shadow rank_table.",
            "Production final_rank is target-weight order, not a reconstructed raw factor rank.",
            "An absent top-15 Shadow row may mean a lower rank or an unavailable signal; the diagnostic does not infer which.",
        ],
        "candidates": candidates,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only comparison of candidate tickers against persisted Caerus artifacts."
    )
    parser.add_argument("tickers", nargs="+", help="Ticker symbols to compare.")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--as-of", default=None, help="Optional YYYY-MM-DD ceiling for dated artifacts.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = build_candidate_comparison(tickers=args.tickers, repo_root=args.repo_root, as_of=args.as_of)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
