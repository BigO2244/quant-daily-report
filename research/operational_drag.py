from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.economic_reconciliation import verify_canonical_economic_artifact_hash


SCHEMA_VERSION = "caerus_operational_drag_v1"
DEFAULT_INCEPTION_DATE = "2026-02-23"
UNDERDEPLOYMENT_THRESHOLD = 0.20

STABLE_WINDOWS: tuple[tuple[str, str | None], ...] = (
    ("since_inception", DEFAULT_INCEPTION_DATE),
    ("since_2026_04_15", "2026-04-15"),
    ("since_2026_05_01", "2026-05-01"),
    ("since_2026_05_28", "2026-05-28"),
    ("latest_available_clean_window", None),
)

PRICE_CSV_CANDIDATES = (
    "outputs/prices/close_history.csv",
    "outputs/prices/price_history.csv",
    "outputs/perf/price_history.csv",
    "outputs/price_history.csv",
    "data/price_history.csv",
    "alpha_stack_cache/csv_export/prices_matrix.csv",
    "data/alpha_stack_cache/csv_export/prices_matrix.csv",
)

PRICE_PARQUET_CANDIDATES = (
    "alpha_stack_cache/prices/_matrix_prices_2007_2026.parquet",
    "outputs/research/flow_detection_v1/price_panel.parquet",
    "outputs/research/ma_vol_hypothesis/price_panel.parquet",
)

ACTUAL_NAV_CSV_CANDIDATES = (
    "outputs/portfolio_history/nav.csv",
    "outputs/perf/live_overlay_nav_series.csv",
    "outputs/perf/nav_timeseries.csv",
)

# FR-058: actual-NAV sources are merged by date and ranked by confidence rather
# than by first-file-found. Higher confidence wins on same-date conflicts; the
# union of all source dates determines coverage so the series extends to the
# freshest available date. live_overlay is the broker-authoritative (Alpaca
# portfolio-history), continuous, self-healing series; run snapshots are
# authoritative per run but fragmentary (used to extend coverage); the
# portfolio_history / legacy nav_timeseries CSVs are the stalest fallbacks.
ACTUAL_NAV_CONFIDENCE_LIVE_OVERLAY = 30
ACTUAL_NAV_CONFIDENCE_RUN_SNAPSHOT = 20
ACTUAL_NAV_CONFIDENCE_PORTFOLIO_HISTORY = 10

# Relative-path CSV sources mapped to (source_class, confidence).
_ACTUAL_NAV_CSV_SOURCES = (
    ("outputs/perf/live_overlay_nav_series.csv", "live_overlay", ACTUAL_NAV_CONFIDENCE_LIVE_OVERLAY),
    ("outputs/portfolio_history/nav.csv", "portfolio_history", ACTUAL_NAV_CONFIDENCE_PORTFOLIO_HISTORY),
    ("outputs/perf/nav_timeseries.csv", "portfolio_history", ACTUAL_NAV_CONFIDENCE_PORTFOLIO_HISTORY),
)

_ACTUAL_NAV_REASON_BY_CLASS = {
    "run_snapshot": "actual_nav_from_run_snapshot",
    "live_overlay": "actual_nav_from_live_overlay",
    "portfolio_history": "actual_nav_from_portfolio_history",
}

BENCHMARK_CSV_CANDIDATES = (
    "outputs/perf/live_overlay_benchmark_close_history.csv",
    "outputs/perf/benchmark_close_history.csv",
)

# Source priority for the price store. Higher wins on (symbol, date) conflicts.
# The fresh, date-scoped hydration artifact must take precedence over the
# fresher parquet panels, which in turn take precedence over the stale CSVs.
# Equal priority preserves within-source last-wins semantics.
PRICE_PRIORITY_HYDRATION = 30
PRICE_PRIORITY_PARQUET = 20
PRICE_PRIORITY_CSV = 10


@dataclass(frozen=True)
class PlanPosition:
    symbol: str
    target_weight: float | None
    shares: float | None
    plan_price: float | None
    reason: str | None = None


@dataclass(frozen=True)
class PlanSnapshot:
    date: str
    strategy_id: str
    equity: float | None
    cash_weight: float
    positions: tuple[PlanPosition, ...]
    source_path: Path
    plan_source: str
    reason_codes: tuple[str, ...]


class PriceStore:
    def __init__(self) -> None:
        self.prices: dict[str, dict[str, float]] = {}
        self.source_by_symbol_date: dict[str, dict[str, str]] = {}
        self.source_dates_by_path: dict[str, set[str]] = {}
        self.source_paths: list[str] = []
        self.reason_codes: list[str] = []
        self.candidate_paths: list[str] = []
        self.failed_paths: list[dict[str, str]] = []
        self.priority_by_symbol_date: dict[str, dict[str, int]] = {}
        self._active_priority: int = 0

    def set_load_priority(self, priority: int) -> None:
        """Set the priority applied to subsequent add() calls for the next source."""
        self._active_priority = priority

    def add(self, symbol: str, date: str, close: float, source: Path) -> None:
        symbol = symbol.upper().strip()
        if not symbol or not _is_date(date):
            return
        priority = self._active_priority
        existing = self.priority_by_symbol_date.get(symbol, {}).get(date)
        # A lower-priority source must never override a value already supplied by
        # a higher-priority source. Equal priority overwrites (within-source
        # last-wins is preserved). Fallback behaviour is preserved because lower
        # priority sources still fill any (symbol, date) the higher ones lack.
        if existing is not None and priority < existing:
            return
        self.prices.setdefault(symbol, {})[date] = close
        self.priority_by_symbol_date.setdefault(symbol, {})[date] = priority
        source_text = str(source)
        self.source_by_symbol_date.setdefault(symbol, {})[date] = source_text
        self.source_dates_by_path.setdefault(source_text, set()).add(date)
        if source_text not in self.source_paths:
            self.source_paths.append(source_text)

    def record_candidate(self, path: Path | str) -> None:
        _diag_add_candidate(self.diagnostics_payload, path)

    @property
    def diagnostics_payload(self) -> dict[str, Any]:
        return {
            "candidate_paths": self.candidate_paths,
            "selected_paths": self.source_paths,
            "failed_paths": self.failed_paths,
        }

    def record_failure(self, path: Path | str, reason: str) -> None:
        _diag_add_failure(self.diagnostics_payload, path, reason)

    def get(self, symbol: str, date: str) -> float | None:
        return self.prices.get(symbol.upper().strip(), {}).get(date)

    def source_for(self, symbol: str, date: str) -> str | None:
        return self.source_by_symbol_date.get(symbol.upper().strip(), {}).get(date)

    def dates_for_symbols(self, symbols: set[str]) -> set[str]:
        out: set[str] = set()
        for symbol in symbols:
            out.update(self.prices.get(symbol.upper().strip(), {}).keys())
        return out

    def diagnostics(self) -> dict[str, Any]:
        date_count = len({date for by_date in self.prices.values() for date in by_date})
        source_max_dates = {
            source: max(dates)
            for source, dates in sorted(self.source_dates_by_path.items())
            if dates
        }
        return {
            "candidate_paths": list(self.candidate_paths),
            "selected_paths": list(self.source_paths),
            "failed_paths": list(self.failed_paths),
            "loaded_symbol_count": len(self.prices),
            "loaded_date_count": date_count,
            "max_available_date": max(source_max_dates.values()) if source_max_dates else None,
            "source_max_dates": source_max_dates,
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


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: _csv_value(row.get(name)) for name in fieldnames})


def _csv_value(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True)
    return "" if value is None else value


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
    numeric = _safe_float(value)
    if numeric is None:
        return None
    return round(numeric, digits)


def _is_date(value: Any) -> bool:
    try:
        dt.date.fromisoformat(str(value))
    except Exception:
        return False
    return True


def _date_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)[:10]
    return text if _is_date(text) else None


def _date_range_end(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if ":" in text:
        text = text.split(":", 1)[1]
    return _date_text(text)


def _sort_dates(values: set[str] | list[str]) -> list[str]:
    return sorted(value for value in values if _is_date(value))


def _dedupe_reasons(reasons: list[str] | tuple[str, ...]) -> list[str]:
    clean = sorted({str(reason) for reason in reasons if reason})
    return clean or ["ok"]


def _append_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _is_material_data_reason(reason: str) -> bool:
    text = str(reason).lower()
    material_tokens = (
        "missing",
        "unavailable",
        "unreadable",
        "failed",
        "stale_trade_date",
        "actual_nav_stale",
        "no_aligned",
        "fewer_than_two",
        "reconciliation_not_clean",
        "planned_buys_without_submissions",
        "eligible_trades_not_fully_accepted",
        "partial_execution",
        "partial_fill",
        "unfilled",
        "price_history_missing",
        "price_panel",
    )
    return any(token in text for token in material_tokens)


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except Exception:
        return []


def _new_diagnostics() -> dict[str, Any]:
    return {"candidate_paths": [], "selected_paths": [], "failed_paths": []}


def _diag_add_candidate(diagnostics: dict[str, Any], path: Path | str) -> None:
    text = str(path)
    candidates = diagnostics.setdefault("candidate_paths", [])
    if text not in candidates:
        candidates.append(text)


def _diag_add_selected(diagnostics: dict[str, Any], path: Path | str) -> None:
    _diag_add_candidate(diagnostics, path)
    text = str(path)
    selected = diagnostics.setdefault("selected_paths", [])
    if text not in selected:
        selected.append(text)


def _diag_add_failure(diagnostics: dict[str, Any], path: Path | str, reason: str) -> None:
    _diag_add_candidate(diagnostics, path)
    failures = diagnostics.setdefault("failed_paths", [])
    entry = {"path": str(path), "reason": str(reason)}
    if entry not in failures:
        failures.append(entry)


def _merge_diagnostics(*items: dict[str, Any]) -> dict[str, Any]:
    merged = _new_diagnostics()
    for diagnostics in items:
        for path in diagnostics.get("candidate_paths", []):
            _diag_add_candidate(merged, path)
        for path in diagnostics.get("selected_paths", []):
            _diag_add_selected(merged, path)
        for failure in diagnostics.get("failed_paths", []):
            if isinstance(failure, dict):
                _diag_add_failure(merged, failure.get("path", ""), failure.get("reason", "unknown"))
    return merged


def _load_manual_aliases(repo: Path) -> dict[str, str]:
    path = repo / "data" / "security_master" / "manual_aliases.json"
    payload = _read_json(path)
    aliases = payload.get("aliases") if isinstance(payload, dict) else None
    if not isinstance(aliases, dict):
        return {}
    out: dict[str, str] = {}
    for source, target in aliases.items():
        src = str(source or "").upper().strip()
        dst = str(target or "").upper().strip()
        if src and dst and src != dst:
            out[src] = dst
    return out


def _resolve_alias(symbol: str, aliases: dict[str, str]) -> tuple[str, list[dict[str, str]]]:
    original = str(symbol or "").upper().strip()
    if not original:
        return "", []
    current = original
    seen = {current}
    resolutions: list[dict[str, str]] = []
    while current in aliases:
        resolved = aliases[current]
        if not resolved or resolved in seen:
            break
        resolutions.append(
            {
                "original_symbol": current,
                "resolved_symbol": resolved,
                "source": "data/security_master/manual_aliases.json",
                "reason": f"manual_alias:{current}->{resolved}",
            }
        )
        current = resolved
        seen.add(current)
    if current != original and not resolutions:
        resolutions.append(
            {
                "original_symbol": original,
                "resolved_symbol": current,
                "source": "data/security_master/manual_aliases.json",
                "reason": f"manual_alias:{original}->{current}",
            }
        )
    return current, resolutions


def _alias_resolution_reason(resolution: dict[str, str]) -> str:
    original = resolution.get("original_symbol") or ""
    resolved = resolution.get("resolved_symbol") or ""
    return f"symbol_alias_resolved:{original}->{resolved}" if original and resolved else "symbol_alias_resolved"


def _position_map_from_any(value: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    if isinstance(value, dict):
        for symbol, qty in value.items():
            normalized = str(symbol or "").upper().strip()
            numeric = _safe_float(qty)
            if normalized and numeric is not None:
                out[normalized] = out.get(normalized, 0.0) + float(numeric)
    elif isinstance(value, list):
        for row in value:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or row.get("ticker") or "").upper().strip()
            qty = _safe_float(row.get("qty") or row.get("shares") or row.get("quantity"))
            if symbol and qty is not None:
                out[symbol] = out.get(symbol, 0.0) + float(qty)
    return {symbol: qty for symbol, qty in sorted(out.items()) if abs(qty) > 1e-9}


def _normalize_position_aliases(
    positions: dict[str, float],
    aliases: dict[str, str],
) -> tuple[dict[str, float], list[dict[str, str]]]:
    normalized: dict[str, float] = {}
    resolutions: list[dict[str, str]] = []
    seen_reasons: set[str] = set()
    for symbol, qty in sorted((positions or {}).items()):
        resolved, symbol_resolutions = _resolve_alias(symbol, aliases)
        if not resolved:
            continue
        normalized[resolved] = normalized.get(resolved, 0.0) + float(qty)
        for resolution in symbol_resolutions:
            key = json.dumps(resolution, sort_keys=True)
            if key not in seen_reasons:
                resolutions.append(resolution)
                seen_reasons.add(key)
    return {symbol: qty for symbol, qty in sorted(normalized.items()) if abs(qty) > 1e-9}, resolutions


def _position_mismatches(expected: dict[str, float], actual: dict[str, float]) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    for symbol in sorted(set(expected) | set(actual)):
        expected_qty = float(expected.get(symbol, 0.0))
        actual_qty = float(actual.get(symbol, 0.0))
        delta = actual_qty - expected_qty
        if abs(delta) <= 1e-9:
            continue
        if symbol in expected and symbol not in actual:
            classification = "MISSING_BROKER_POSITION"
        elif symbol not in expected and symbol in actual:
            classification = "UNEXPECTED_BROKER_POSITION"
        else:
            classification = "QTY_MISMATCH"
        mismatches.append(
            {
                "symbol": symbol,
                "expected_qty": _round(expected_qty, 6),
                "broker_qty": _round(actual_qty, 6),
                "delta_qty": _round(delta, 6),
                "classification": classification,
            }
        )
    return mismatches


def _raw_reconciliation_position_issue(payload: dict[str, Any]) -> bool:
    if payload.get("missing_in_actual") or payload.get("missing_in_expected") or payload.get("qty_mismatches"):
        return True
    for row in payload.get("share_deltas") or []:
        if isinstance(row, dict) and str(row.get("classification") or "").upper() != "MATCH":
            return True
    return False


@contextmanager
def _suppress_stderr_fd():
    """Silence optional parquet-engine CPU probes that are noisy in sandboxes."""
    if os.environ.get("CAERUS_OPERATIONAL_DRAG_DEBUG"):
        yield
        return
    original = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(original, 2)
        os.close(original)
        os.close(devnull)


def load_price_store(repo_root: Path | str = Path("."), *, trade_date: str | None = None) -> PriceStore:
    repo = Path(repo_root)
    store = PriceStore()
    # 1) Fresh, date-scoped hydration artifact: highest priority, discovered first.
    store.set_load_priority(PRICE_PRIORITY_HYDRATION)
    _load_operational_drag_price_hydration(repo, store, trade_date=trade_date)
    # 2) Fresher parquet / price-panel sources.
    store.set_load_priority(PRICE_PRIORITY_PARQUET)
    _load_parquet_price_panel(repo, store)
    # 3) Stale historical CSV fallback (still consulted; only fills gaps).
    store.set_load_priority(PRICE_PRIORITY_CSV)
    for rel in PRICE_CSV_CANDIDATES:
        path = repo / rel
        store.record_candidate(path)
        if not path.exists():
            store.record_failure(path, "missing")
            continue
        loaded = 0
        rows = _read_csv_rows(path)
        if not rows:
            store.record_failure(path, "empty_or_unreadable")
            continue
        loaded = _load_price_csv_rows(rows, path, store)
        if loaded <= 0:
            store.record_failure(path, "no_valid_price_rows")
    if not store.source_paths:
        store.reason_codes.append("price_history_missing")
    return store


def _load_operational_drag_price_hydration(repo: Path, store: PriceStore, *, trade_date: str | None) -> None:
    if not trade_date:
        return
    path = repo / "outputs" / "operational_drag" / trade_date / "price_hydration.json"
    store.record_candidate(path)
    payload = _read_json(path)
    if payload is None:
        store.record_failure(path, "missing_or_unreadable")
        return
    payload_date = _date_text(payload.get("date") or payload.get("trade_date"))
    if payload_date and payload_date != trade_date:
        store.record_failure(path, f"stale_trade_date:{payload_date}")
        return
    prices = payload.get("prices")
    if not isinstance(prices, list) or not prices:
        store.record_failure(path, "no_price_rows")
        _append_reason(store.reason_codes, "price_hydration_empty")
        return
    loaded = 0
    skipped_future = 0
    for row in prices:
        if not isinstance(row, dict):
            continue
        date = _date_text(row.get("date"))
        if not date:
            continue
        if date > trade_date:
            skipped_future += 1
            continue
        symbol = str(row.get("symbol") or row.get("ticker") or "").upper().strip()
        close = _safe_float(row.get("close") or row.get("price") or row.get("spy_price"))
        if symbol and close is not None:
            store.add(symbol, date, close, path)
            loaded += 1
    if loaded <= 0:
        store.record_failure(path, "no_valid_price_rows")
        _append_reason(store.reason_codes, "price_hydration_no_valid_prices")
    if skipped_future:
        _append_reason(store.reason_codes, "price_hydration_future_rows_ignored")


def _load_price_csv_rows(rows: list[dict[str, str]], path: Path, store: PriceStore) -> int:
    if not rows:
        return 0
    columns = {str(col).lower(): col for col in rows[0].keys()}
    date_col = columns.get("date") or columns.get("as_of") or columns.get("timestamp")
    symbol_col = columns.get("symbol") or columns.get("ticker") or columns.get("asset")
    close_col = (
        columns.get("close")
        or columns.get("adj_close")
        or columns.get("price")
        or columns.get("last")
        or columns.get("execution_price")
    )
    loaded = 0
    if date_col is not None and symbol_col is not None and close_col is not None:
        for row in rows:
            date = _date_text(row.get(date_col))
            symbol = str(row.get(symbol_col) or "").upper().strip()
            close = _safe_float(row.get(close_col))
            if date and symbol and close is not None:
                store.add(symbol, date, close, path)
                loaded += 1
        return loaded
    if date_col is None:
        return 0
    metadata = {
        "date",
        "as_of",
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "adj_close",
        "volume",
        "sector",
        "ticker",
        "symbol",
        "asset",
    }
    for row in rows:
        date = _date_text(row.get(date_col))
        if not date:
            continue
        for col, value in row.items():
            if str(col).lower() in metadata:
                continue
            close = _safe_float(value)
            symbol = str(col).upper().strip()
            if symbol and close is not None:
                store.add(symbol, date, close, path)
                loaded += 1
    return loaded


def _load_parquet_price_panel(repo: Path, store: PriceStore) -> None:
    unreadable_existing = False
    parse_failed_existing = False
    for rel in PRICE_PARQUET_CANDIDATES:
        path = repo / rel
        store.record_candidate(path)
        if not path.exists():
            store.record_failure(path, "missing")
            continue
        loaded_before = sum(len(by_date) for by_date in store.prices.values())
        try:
            os.environ.setdefault("ARROW_USER_SIMD_LEVEL", "NONE")
            with _suppress_stderr_fd():
                import pandas as pd  # type: ignore

                df = pd.read_parquet(path)
        except Exception as exc:
            unreadable_existing = True
            store.record_failure(path, f"unreadable:{type(exc).__name__}")
            continue
        try:
            _load_price_dataframe(df, path, store)
        except Exception as exc:
            parse_failed_existing = True
            store.record_failure(path, f"parse_failed:{type(exc).__name__}")
            continue
        loaded_after = sum(len(by_date) for by_date in store.prices.values())
        if loaded_after == loaded_before:
            store.record_failure(path, "no_supported_price_rows")
    if unreadable_existing and not store.source_paths:
        store.reason_codes.append("price_panel_parquet_unreadable")
    if parse_failed_existing and not store.source_paths:
        store.reason_codes.append("price_panel_parse_failed")


def _load_price_dataframe(df: Any, path: Path, store: PriceStore) -> None:
    columns = {str(col).lower(): col for col in df.columns}
    date_col = columns.get("date") or columns.get("as_of") or columns.get("timestamp")
    symbol_col = columns.get("symbol") or columns.get("ticker") or columns.get("asset")
    close_col = columns.get("close") or columns.get("adj_close") or columns.get("price") or columns.get("last")
    if date_col is not None and symbol_col is not None:
        if close_col is None:
            store.record_failure(path, "missing_close_column")
            if not store.source_paths:
                store.reason_codes.append("price_panel_missing_close_column")
            return
        frame = df[[date_col, symbol_col, close_col]].copy()
        try:
            import pandas as pd  # type: ignore

            frame[date_col] = pd.to_datetime(frame[date_col], errors="coerce").dt.strftime("%Y-%m-%d")
        except Exception:
            pass
        frame = frame.dropna(subset=[date_col, symbol_col, close_col])
        for raw_date, raw_symbol, raw_close in frame.itertuples(index=False, name=None):
            date = _date_text(raw_date)
            symbol = str(raw_symbol).upper().strip()
            close = _safe_float(raw_close)
            if date and symbol and close is not None:
                store.add(symbol, date, close, path)
        return
    if date_col is not None:
        skip_columns = {date_col}
        metadata = {
            "open",
            "high",
            "low",
            "close",
            "adj_close",
            "volume",
            "sector",
            "ticker",
            "symbol",
            "asset",
        }
        for _, row in df.iterrows():
            date = _date_text(row[date_col])
            if not date:
                continue
            for col, value in row.items():
                if col in skip_columns or str(col).lower() in metadata:
                    continue
                close = _safe_float(value)
                symbol = str(col).upper().strip()
                if symbol and close is not None:
                    store.add(symbol, date, close, path)
        return
    for idx, row in df.iterrows():
        date = _date_text(idx)
        if not date:
            continue
        for col, value in row.items():
            close = _safe_float(value)
            symbol = str(col).upper().strip()
            if symbol and close is not None:
                store.add(symbol, date, close, path)


def discover_plan_snapshots(repo_root: Path | str, trade_date: str) -> list[PlanSnapshot]:
    repo = Path(repo_root)
    snapshots: list[PlanSnapshot] = []
    precompute_root = repo / "outputs" / "precompute"
    if precompute_root.exists():
        for day_dir in sorted(path for path in precompute_root.iterdir() if path.is_dir() and _is_date(path.name)):
            if day_dir.name > trade_date:
                continue
            snapshot = _plan_from_daily_snapshot(day_dir / "daily_snapshot.json", day_dir / "planned_execution_payload.json")
            if snapshot is not None:
                snapshots.append(snapshot)
                continue
            payload_snapshot = _plan_from_execution_payload(day_dir / "planned_execution_payload.json")
            if payload_snapshot is not None:
                snapshots.append(payload_snapshot)
    portfolio_root = repo / "outputs" / "portfolio_history"
    if portfolio_root.exists():
        for day_dir in sorted(path for path in portfolio_root.iterdir() if path.is_dir() and _is_date(path.name)):
            if day_dir.name > trade_date:
                continue
            snapshot = _plan_from_holdings_snapshot(day_dir / "holdings_snapshot.json")
            if snapshot is not None and not any(existing.date == snapshot.date for existing in snapshots):
                snapshots.append(snapshot)
    # A sealed exact plan is the only execution intent for its run.  Once an
    # exact-v3 outcome has independently reconciled, the same-day precompute
    # target book is lineage, not a set of unsubmitted orders.  Replace only
    # that date; historical target snapshots retain their existing semantics.
    exact_evidence = _validated_exact_execution_evidence(repo, trade_date)
    exact_snapshot = _plan_from_exact_execution(exact_evidence) if exact_evidence else None
    if exact_snapshot is not None:
        snapshots = [snapshot for snapshot in snapshots if snapshot.date != trade_date]
        snapshots.append(exact_snapshot)
    return sorted(snapshots, key=lambda item: (item.date, item.plan_source))


def _resolve_repo_path(repo: Path, value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text)
    return path if path.is_absolute() else repo / path


def _exact_execution_payload_candidates(repo: Path, trade_date: str) -> list[Path]:
    pointer_path = repo / "outputs" / "workflow" / trade_date / "execution.json"
    if pointer_path.exists():
        # The workflow pointer is authoritative for the current run.  Never
        # skip a bad/partial current run and bless an older same-day run.
        pointer = _read_json(pointer_path)
        if not pointer or _payload_date(pointer) != trade_date:
            return []
        status = str(pointer.get("status") or "").strip().lower()
        substatus = str(pointer.get("substatus") or "").strip().lower()
        allowed_state = (
            status == "running" and substatus == "paper_posttrade_verification_started"
        ) or (status == "success" and not substatus) or (
            status == "no_action" and substatus == "authorized_no_trade"
        )
        if (
            str(pointer.get("stage") or "").strip().lower() != "execution"
            or str(pointer.get("mode") or "").strip().upper() != "PAPER"
            or not allowed_state
        ):
            return []
        run_root = _resolve_repo_path(repo, pointer.get("run_root"))
        run_id = str(pointer.get("run_id") or "").strip()
        if run_root is None or not run_id or run_root.name != run_id:
            return []
        return [run_root / "execution_payload.json"]
    # Current-date health without an authoritative pointer is ambiguous.  Do
    # not infer the current run from directory ordering.
    return []


def _order_identity(row: Any) -> tuple[str, str, str, float] | None:
    if not isinstance(row, dict):
        return None
    client_order_id = str(row.get("client_order_id") or "").strip()
    symbol = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
    side = str(row.get("side") or "").strip().upper()
    quantity = _safe_float(
        row.get("quantity")
        if row.get("quantity") is not None
        else row.get("qty", row.get("shares"))
    )
    if not client_order_id or not symbol or side not in {"BUY", "SELL"} or quantity is None:
        return None
    return client_order_id, symbol, side, float(quantity)


def _filled_order_economics(row: Any) -> tuple[float, float] | None:
    identity = _order_identity(row)
    if identity is None or not isinstance(row, dict):
        return None
    filled_quantity = _safe_float(row.get("filled_qty"))
    filled_price = _safe_float(row.get("filled_avg_price"))
    if (
        str(row.get("status") or "").upper().split(".")[-1] != "FILLED"
        or filled_quantity is None
        or abs(filled_quantity - identity[3]) > 1e-8
        or filled_price is None
        or filled_price <= 0.0
    ):
        return None
    return float(filled_quantity), float(filled_price)


def _validated_exact_execution_evidence(
    repo: Path,
    trade_date: str,
) -> dict[str, Any] | None:
    """Return only cryptographically sealed, economically clean execution truth.

    This deliberately does not trust a terminal label alone.  The exact plan
    must re-validate, persisted order identities must match it, all submitted
    orders must be filled, and the content-hashed canonical economic artifact
    must be RECONCILED.  A no-trade remains valid only when every count and
    order list is zero.  Any ambiguity falls back to fail-closed analysis.
    """

    from authority.exact_plan import exact_execution_plan_from_dict

    for payload_path in _exact_execution_payload_candidates(repo, trade_date):
        run_id = _run_id_from_path(payload_path)
        if run_id is not None and not run_id.startswith(trade_date):
            continue
        payload = _read_json(payload_path)
        if not payload or _payload_date(payload) != trade_date:
            continue
        if str(payload.get("execution_source") or "") != "exact_execution_plan_v3":
            continue
        terminal_status = str(payload.get("terminal_status") or "").upper()
        terminal_outcome = str(payload.get("terminal_outcome") or "").upper()
        no_trade = (
            terminal_status == "AUTHORIZED_NO_TRADE"
            and terminal_outcome == "AUTHORIZED_NO_TRADE"
        )
        submitted_success = (
            terminal_status == "SUBMITTED"
            and terminal_outcome == "RECONCILED_SUCCESS"
            and str(payload.get("reconciliation_status") or "").upper() == "CLEAN"
        )
        if not no_trade and not submitted_success:
            continue
        pointer = _read_json(repo / "outputs" / "workflow" / trade_date / "execution.json") or {}
        pointer_state = (
            str(pointer.get("status") or "").strip().lower(),
            str(pointer.get("substatus") or "").strip().lower(),
        )
        if no_trade and pointer_state != ("no_action", "authorized_no_trade"):
            continue
        if submitted_success and pointer_state not in {
            ("running", "paper_posttrade_verification_started"),
            ("success", ""),
        }:
            continue
        raw_counts = {
            key: _safe_float(payload.get(key))
            for key in (
                "orders_requested_count",
                "orders_submitted_count",
                "orders_filled_count",
                "orders_suppressed_count",
            )
        }
        if any(
            value is None or value < 0 or not float(value).is_integer()
            for value in raw_counts.values()
        ):
            continue
        count_values = {key: int(value) for key, value in raw_counts.items() if value is not None}
        package = payload.get("exact_execution_plan")
        if not isinstance(package, dict):
            continue
        try:
            exact = exact_execution_plan_from_dict(
                package,
                expected_plan_id=str(package.get("plan_id") or ""),
                expected_run_id=str(package.get("run_id") or ""),
                expected_account_scope="PAPER",
            )
        except (TypeError, ValueError, OverflowError):
            continue
        if exact.trade_date != trade_date:
            continue
        if str(payload.get("exact_execution_plan_hash") or "") != exact.content_hash:
            continue
        run_root = payload_path.parent
        if str(payload.get("run_id") or "") != run_root.name:
            continue
        intended_payload = _read_json(run_root / "live_pilot_orders_intended.json")
        submitted_payload = _read_json(run_root / "live_pilot_orders_submitted.json")
        intended_rows = intended_payload.get("orders") if isinstance(intended_payload, dict) else None
        submitted_rows = submitted_payload.get("orders") if isinstance(submitted_payload, dict) else None
        if not isinstance(intended_rows, list) or not isinstance(submitted_rows, list):
            continue
        exact_identities = {_order_identity(dict(row)) for row in exact.orders}
        intended_identities = {_order_identity(row) for row in intended_rows}
        submitted_identities = {_order_identity(row) for row in submitted_rows}
        if None in exact_identities or None in intended_identities or None in submitted_identities:
            continue
        order_count = len(exact.orders)
        if no_trade:
            if exact.orders or intended_rows or submitted_rows:
                continue
            if any(value != 0 for value in count_values.values()):
                continue
        else:
            if count_values != {
                "orders_requested_count": order_count,
                "orders_submitted_count": order_count,
                "orders_filled_count": order_count,
                "orders_suppressed_count": 0,
            }:
                continue
            if (
                len(intended_rows) != order_count
                or len(submitted_rows) != order_count
                or exact_identities != intended_identities
                or exact_identities != submitted_identities
            ):
                continue
            if any(_filled_order_economics(row) is None for row in submitted_rows):
                continue
        economic_path = run_root / "canonical_economic_verification.json"
        economic = _read_json(economic_path)
        if not economic or _payload_date(economic) != trade_date:
            continue
        if (
            str(economic.get("status") or "").upper() != "RECONCILED"
            or economic.get("reconciled") is not True
            or not verify_canonical_economic_artifact_hash(economic)
        ):
            continue
        economic_recon = economic.get("economic_reconciliation")
        sleeve_recon = economic.get("sleeve_attribution_reconciliation")
        if not isinstance(economic_recon, dict) or not isinstance(sleeve_recon, dict):
            continue
        if (
            str(economic_recon.get("status") or "").upper() != "RECONCILED"
            or economic_recon.get("reconciled") is not True
            or str(sleeve_recon.get("status") or "").upper() != "RECONCILED"
            or sleeve_recon.get("reconciled") is not True
        ):
            continue
        positions = economic_recon.get("positions") or {}
        tolerance = economic_recon.get("tolerance") or {}
        quantity_tolerance = _safe_float(tolerance.get("quantity_abs"))
        cash_tolerance = _safe_float(tolerance.get("cash_abs"))
        if quantity_tolerance is None or quantity_tolerance < 0:
            continue
        if cash_tolerance is None or cash_tolerance < 0:
            continue
        exact_positions = _position_map_from_any(
            [dict(row) for row in exact.expected_posttrade_positions]
        )
        economic_expected = _position_map_from_any(positions.get("expected"))
        economic_actual = _position_map_from_any(positions.get("actual"))
        symbols = set(exact_positions) | set(economic_expected) | set(economic_actual)
        if any(
            abs(exact_positions.get(symbol, 0.0) - economic_expected.get(symbol, 0.0))
            > quantity_tolerance
            or abs(exact_positions.get(symbol, 0.0) - economic_actual.get(symbol, 0.0))
            > quantity_tolerance
            for symbol in symbols
        ):
            continue
        cash = economic_recon.get("cash") or {}
        expected_cash = _safe_float(cash.get("expected"))
        actual_cash = _safe_float(cash.get("actual"))
        if expected_cash is None or actual_cash is None:
            continue
        if no_trade:
            fill_adjusted_cash = float(exact.expected_posttrade_cash)
        else:
            fill_adjusted_cash = float(exact.starting_cash)
            for row in submitted_rows:
                economics = _filled_order_economics(row)
                if economics is None:
                    break
                quantity, price = economics
                notional = quantity * price
                if str(row.get("side") or "").upper() == "SELL":
                    fill_adjusted_cash += notional
                else:
                    fill_adjusted_cash -= notional
            fees = _safe_float((economic_recon.get("fills") or {}).get("fees")) or 0.0
            if fees < 0.0:
                continue
            fill_adjusted_cash -= fees
        if (
            abs(expected_cash - fill_adjusted_cash) > cash_tolerance
            or abs(actual_cash - fill_adjusted_cash) > cash_tolerance
        ):
            continue
        return {
            "payload_path": payload_path,
            "run_root": run_root,
            "payload": payload,
            "exact_plan": exact,
            "economic_path": economic_path,
            "economic": economic,
            "no_trade": no_trade,
        }
    return None


def _validated_exact_no_trade_evidence(
    repo: Path,
    trade_date: str,
) -> dict[str, Any] | None:
    """Backward-compatible helper for callers that specifically require no-trade."""

    evidence = _validated_exact_execution_evidence(repo, trade_date)
    return evidence if evidence is not None and evidence.get("no_trade") is True else None


def _plan_from_exact_execution(evidence: dict[str, Any]) -> PlanSnapshot | None:
    exact = evidence.get("exact_plan")
    economic = evidence.get("economic") or {}
    run_root = Path(evidence["run_root"])
    post_snapshot = _read_json(run_root / "live_pilot_broker_snapshot_post.json") or {}
    mark_by_symbol: dict[str, float] = {}
    for row in post_snapshot.get("positions") or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        quantity = _safe_float(row.get("qty") or row.get("quantity"))
        market_value = _safe_float(row.get("market_value"))
        if symbol and quantity and market_value is not None:
            mark_by_symbol[symbol] = abs(market_value / quantity)
    positions = tuple(
        PlanPosition(
            symbol=str(row.get("symbol") or "").strip().upper(),
            target_weight=None,
            shares=_safe_float(row.get("quantity")),
            plan_price=mark_by_symbol.get(str(row.get("symbol") or "").strip().upper()),
            reason="exact_authorized_posttrade_position",
        )
        for row in exact.expected_posttrade_positions
        if str(row.get("symbol") or "").strip() and _safe_float(row.get("quantity")) is not None
    )
    econ = economic.get("economic_reconciliation") or {}
    nav = _safe_float((econ.get("nav") or {}).get("broker_equity")) or _safe_float(exact.portfolio_nav)
    cash = _safe_float((econ.get("cash") or {}).get("actual"))
    if cash is None:
        return None
    cash_weight = (cash / nav) if nav and nav > 0 else 0.0
    no_trade = evidence.get("no_trade") is True
    return PlanSnapshot(
        date=exact.trade_date,
        strategy_id=str(exact.strategy_id),
        equity=nav,
        cash_weight=max(0.0, min(1.0, cash_weight)),
        positions=positions,
        source_path=Path(evidence["payload_path"]),
        plan_source=(
            "exact_authorized_no_trade_posttrade_state"
            if no_trade
            else "exact_authorized_reconciled_posttrade_state"
        ),
        reason_codes=(
            "plan_source_exact_authorized_no_trade"
            if no_trade
            else "plan_source_exact_reconciled_execution",
        ),
    )


def _plan_from_exact_no_trade(evidence: dict[str, Any]) -> PlanSnapshot | None:
    """Backward-compatible wrapper for the original no-trade call surface."""

    if evidence.get("no_trade") is not True:
        return None
    return _plan_from_exact_execution(evidence)


def _strategy_from_payload(payload: dict[str, Any] | None) -> str:
    if not payload:
        return "caerus_polaris"
    for key in ("strategy_id", "live_strategy", "strategy", "selected_strategy"):
        value = payload.get(key)
        if value:
            return str(value)
    run_id = str(payload.get("run_id") or "")
    if "orion" in run_id.lower():
        return "caerus_orion"
    if "lyra" in run_id.lower():
        return "caerus_lyra"
    return "caerus_polaris"


def _plan_from_daily_snapshot(path: Path, payload_path: Path) -> PlanSnapshot | None:
    payload = _read_json(path)
    if payload is None:
        return None
    orders = payload.get("orders")
    if not isinstance(orders, list) or not orders:
        return None
    positions: list[PlanPosition] = []
    reasons: list[str] = ["plan_source_daily_snapshot_target_weights"]
    for row in orders:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("ticker") or row.get("symbol") or "").upper().strip()
        if symbol in {"", "CASH"}:
            continue
        target_weight = _safe_float(row.get("target_weight") or row.get("weight"))
        plan_price = _safe_float(row.get("execution_price") or row.get("entry_price") or row.get("price"))
        shares = _safe_float(row.get("shares") or row.get("qty") or row.get("quantity"))
        if target_weight is None and shares is None:
            continue
        positions.append(PlanPosition(symbol=symbol, target_weight=target_weight, shares=shares, plan_price=plan_price))
    if not positions:
        return None
    payload_plan = _read_json(payload_path)
    equity = _safe_float(payload.get("equity") or (payload_plan or {}).get("equity") or (payload_plan or {}).get("portfolio_value"))
    cash_weight = _safe_float(
        payload.get("target_cash_weight")
        or payload.get("cash_target")
        or payload.get("cash_target_weight")
        or (payload_plan or {}).get("cash_target_weight")
    )
    if cash_weight is None:
        invested = sum(float(pos.target_weight or 0.0) for pos in positions)
        cash_weight = max(0.0, 1.0 - invested) if invested > 0 else 0.0
        reasons.append("cash_weight_inferred_from_target_weights")
    date = _date_text(payload.get("asof")) or _date_text(payload.get("date")) or path.parent.name
    return PlanSnapshot(
        date=date,
        strategy_id=_strategy_from_payload(payload_plan or payload),
        equity=equity,
        cash_weight=max(0.0, min(1.0, float(cash_weight))),
        positions=tuple(positions),
        source_path=path,
        plan_source="daily_snapshot_target_weights",
        reason_codes=tuple(_dedupe_reasons(reasons)),
    )


def _plan_from_execution_payload(path: Path) -> PlanSnapshot | None:
    payload = _read_json(path)
    if payload is None:
        return None
    trades = payload.get("trades")
    if not isinstance(trades, list) or not trades:
        return None
    positions: list[PlanPosition] = []
    buy_notional = 0.0
    for row in trades:
        if not isinstance(row, dict) or str(row.get("side") or "").upper() != "BUY":
            continue
        symbol = str(row.get("ticker") or row.get("symbol") or "").upper().strip()
        shares = _safe_float(row.get("shares") or row.get("qty") or row.get("quantity"))
        plan_price = _safe_float(row.get("entry_price") or row.get("execution_price") or row.get("price"))
        notional = _safe_float(row.get("notional"))
        if notional is None and shares is not None and plan_price is not None:
            notional = shares * plan_price
        if symbol and shares is not None:
            buy_notional += float(notional or 0.0)
            positions.append(
                PlanPosition(
                    symbol=symbol,
                    target_weight=None,
                    shares=shares,
                    plan_price=plan_price,
                    reason="buy_trade_delta",
                )
            )
    if not positions:
        return None
    equity = _safe_float(payload.get("equity") or payload.get("portfolio_value") or payload.get("investable_dollars"))
    cash_weight = _safe_float(payload.get("cash_target_weight"))
    if cash_weight is None and equity and equity > 0:
        cash_weight = max(0.0, 1.0 - buy_notional / equity)
    return PlanSnapshot(
        date=_date_text(payload.get("trade_date")) or path.parent.name,
        strategy_id=_strategy_from_payload(payload),
        equity=equity,
        cash_weight=max(0.0, min(1.0, float(cash_weight or 0.0))),
        positions=tuple(positions),
        source_path=path,
        plan_source="planned_execution_payload_buy_trades",
        reason_codes=("target_holdings_missing_using_buy_trade_intent_only",),
    )


def _plan_from_holdings_snapshot(path: Path) -> PlanSnapshot | None:
    payload = _read_json(path)
    if payload is None:
        return None
    date = _date_text(payload.get("trade_date") or payload.get("date")) or path.parent.name
    strategies = payload.get("strategies") if isinstance(payload.get("strategies"), dict) else {}
    selected_strategy = None
    holdings = None
    for strategy_id in ("caerus_polaris", "polaris", "caerus_orion", "caerus_lyra"):
        candidate = strategies.get(strategy_id) if isinstance(strategies, dict) else None
        candidate_holdings = candidate.get("holdings") if isinstance(candidate, dict) else None
        if isinstance(candidate_holdings, list) and candidate_holdings:
            selected_strategy = strategy_id
            holdings = candidate_holdings
            break
    if holdings is None and isinstance(payload.get("holdings"), list):
        selected_strategy = _strategy_from_payload(payload)
        holdings = payload.get("holdings")
    if not isinstance(holdings, list) or not holdings:
        return None
    positions: list[PlanPosition] = []
    for row in holdings:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("ticker") or row.get("symbol") or "").upper().strip()
        if not symbol or symbol == "CASH":
            continue
        target_weight = _safe_float(row.get("target_weight") or row.get("weight"))
        shares = _safe_float(row.get("shares") or row.get("qty") or row.get("quantity"))
        plan_price = _safe_float(row.get("price") or row.get("close") or row.get("execution_price"))
        if target_weight is not None or shares is not None:
            positions.append(PlanPosition(symbol, target_weight, shares, plan_price))
    if not positions:
        return None
    invested = sum(float(pos.target_weight or 0.0) for pos in positions)
    cash_weight = max(0.0, 1.0 - invested) if invested > 0 else 0.0
    return PlanSnapshot(
        date=date,
        strategy_id=str(selected_strategy or "caerus_polaris"),
        equity=_safe_float(payload.get("equity") or payload.get("portfolio_value")),
        cash_weight=cash_weight,
        positions=tuple(positions),
        source_path=path,
        plan_source="portfolio_history_holdings_snapshot",
        reason_codes=("plan_source_portfolio_history_holdings",),
    )


def build_intended_nav(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    price_store: PriceStore | None = None,
    date_axis: list[str] | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root)
    prices = price_store or load_price_store(repo, trade_date=trade_date)
    snapshots = discover_plan_snapshots(repo, trade_date)
    source_artifacts = [str(snapshot.source_path) for snapshot in snapshots]
    if not snapshots:
        return {
            "schema_version": SCHEMA_VERSION,
            "date": trade_date,
            "available": False,
            "confidence": "LOW",
            "strategy_id": None,
            "live_strategy": None,
            "timeseries": [],
            "missing_symbols": [],
            "reason_codes": _dedupe_reasons(["missing_plan_artifacts"] + prices.reason_codes),
            "source_artifacts": [],
            "source_diagnostics": {
                "price": prices.diagnostics(),
                "plan": {
                    "candidate_paths": [],
                    "selected_paths": [],
                    "failed_paths": [{"path": str(repo / "outputs" / "precompute"), "reason": "missing_plan_artifacts"}],
                },
            },
        }

    all_symbols = {pos.symbol for snapshot in snapshots for pos in snapshot.positions}
    dates = set(date_axis or [])
    dates.update(prices.dates_for_symbols(all_symbols))
    dates.update(snapshot.date for snapshot in snapshots)
    dates = {date for date in dates if snapshots[0].date <= date <= trade_date}
    ordered_dates = _sort_dates(dates)
    if not ordered_dates:
        ordered_dates = [snapshots[-1].date]

    active_plan: PlanSnapshot | None = None
    active_shares: dict[str, float] = {}
    active_weights: dict[str, float | None] = {}
    intended_cash = 0.0
    previous_equity: float | None = None
    base_equity: float | None = None
    rows: list[dict[str, Any]] = []

    marked_to_market = False
    for date in ordered_dates:
        latest_plan = _latest_plan_for_date(snapshots, date)
        row_reasons: list[str] = []
        if latest_plan is None:
            rows.append(_missing_intended_row(date, ["missing_plan_for_date"]))
            continue
        if active_plan is None or latest_plan.date != active_plan.date:
            # FR-060: before adopting the new target, mark the carried (prior)
            # intended holdings to market at TODAY's prices. Using that
            # marked-to-market value as the rebalance basis lets price moves since
            # the last rebalance flow into the intended return, instead of being
            # erased by a same-day reconstruction (which returns the prior equity
            # exactly -> intended_return_daily == 0.0). No look-ahead: only
            # prices on or before `date` are ever read.
            carry_equity: float | None = None
            if active_shares:
                carry_row = _mark_intended_row(
                    date=date,
                    plan=active_plan,
                    shares=active_shares,
                    weights=active_weights,
                    cash=intended_cash,
                    prices=prices,
                    row_reasons=[],
                )
                carry_equity = _safe_float(carry_row.get("intended_equity_value"))
            active_plan = latest_plan
            if latest_plan.plan_source in {
                "exact_authorized_no_trade_posttrade_state",
                "exact_authorized_reconciled_posttrade_state",
            }:
                # The sealed run is an observed, broker-bound state at this
                # execution window.  Do not size it from a hypothetical prior
                # target-book NAV carried into the day.
                rebalance_equity = latest_plan.equity
                row_reasons.append("intended_rebalance_uses_exact_execution_nav")
            elif carry_equity is not None:
                rebalance_equity = carry_equity
                row_reasons.append("intended_rebalance_marked_to_market")
                marked_to_market = True
            else:
                rebalance_equity = previous_equity or latest_plan.equity
                if rebalance_equity is None:
                    rebalance_equity = _notional_from_plan(latest_plan)
                    row_reasons.append("intended_base_equity_inferred_from_plan_notional")
                elif active_shares:
                    # Carried holdings could not be fully priced today; fall back to
                    # the prior equity (labeled, no fabricated mark).
                    row_reasons.append("intended_rebalance_carry_unpriced_fallback")
            active_shares, active_weights, intended_cash, rebalance_reasons = _rebalance_intended_positions(
                latest_plan,
                date=date,
                equity=float(rebalance_equity or 0.0),
                prices=prices,
            )
            row_reasons.extend(rebalance_reasons)
        row = _mark_intended_row(
            date=date,
            plan=active_plan,
            shares=active_shares,
            weights=active_weights,
            cash=intended_cash,
            prices=prices,
            row_reasons=row_reasons,
        )
        equity = _safe_float(row.get("intended_equity_value"))
        if equity is not None:
            if base_equity is None:
                base_equity = equity
                row["intended_return_daily"] = 0.0
                row["intended_return_cumulative"] = 0.0
            else:
                row["intended_return_daily"] = _round((equity / previous_equity - 1.0) if previous_equity else None)
                row["intended_return_cumulative"] = _round((equity / base_equity - 1.0) if base_equity else None)
            previous_equity = equity
        else:
            row["intended_return_daily"] = None
            row["intended_return_cumulative"] = None
        rows.append(row)

    available_rows = [row for row in rows if row.get("intended_equity_value") is not None]
    latest = available_rows[-1] if available_rows else rows[-1]
    missing_symbols = sorted({symbol for row in rows for symbol in row.get("missing_symbols", [])})
    # FR-060: signal whether intended NAV is a true day-over-day mark-to-market
    # series (>=2 priced days carried across a date boundary) vs a single-day
    # snapshot that cannot express day-over-day return.
    mtm_reasons: list[str] = []
    if marked_to_market or len(available_rows) > 1:
        mtm_reasons.append("intended_nav_marked_to_market")
    elif available_rows:
        mtm_reasons.append("intended_nav_single_day_no_mtm")
    reasons = _dedupe_reasons(
        [reason for row in rows for reason in row.get("reason_codes", []) if reason != "ok"]
        + mtm_reasons
        + list(prices.reason_codes)
    )
    available = bool(available_rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "available": available,
        "confidence": "MEDIUM" if available and not missing_symbols else "LOW",
        "strategy_id": latest.get("strategy_id"),
        "live_strategy": latest.get("strategy_id"),
        "intended_equity_value": latest.get("intended_equity_value"),
        "intended_cash": latest.get("intended_cash"),
        "intended_gross_exposure": latest.get("intended_gross_exposure"),
        "intended_positions": latest.get("intended_positions") or [],
        "intended_return_daily": latest.get("intended_return_daily"),
        "intended_return_cumulative": latest.get("intended_return_cumulative"),
        "price_source": prices.source_paths,
        "plan_source": latest.get("plan_source"),
        "missing_symbols": missing_symbols,
        "reason_codes": reasons,
        "timeseries": rows,
        "source_artifacts": source_artifacts + prices.source_paths,
        "source_diagnostics": {
            "price": prices.diagnostics(),
            "plan": {
                "candidate_paths": source_artifacts,
                "selected_paths": source_artifacts,
                "failed_paths": [],
            },
        },
    }


def _latest_plan_for_date(snapshots: list[PlanSnapshot], date: str) -> PlanSnapshot | None:
    candidates = [snapshot for snapshot in snapshots if snapshot.date <= date]
    return candidates[-1] if candidates else None


def _notional_from_plan(plan: PlanSnapshot) -> float | None:
    total = 0.0
    for position in plan.positions:
        if position.shares is not None and position.plan_price is not None:
            total += abs(position.shares * position.plan_price)
    if plan.cash_weight < 1.0 and total > 0.0:
        return total / max(0.000001, 1.0 - plan.cash_weight)
    return total or None


def _rebalance_intended_positions(
    plan: PlanSnapshot,
    *,
    date: str,
    equity: float,
    prices: PriceStore,
) -> tuple[dict[str, float], dict[str, float | None], float, list[str]]:
    shares: dict[str, float] = {}
    weights: dict[str, float | None] = {}
    reasons: list[str] = list(plan.reason_codes)
    invested_value = 0.0
    for position in plan.positions:
        price = prices.get(position.symbol, date)
        if price is None and date == plan.date:
            price = position.plan_price
            if price is not None:
                _append_reason(reasons, "rebalance_uses_plan_execution_price")
        if position.target_weight is not None and price:
            qty = (float(position.target_weight) * equity) / price
            shares[position.symbol] = qty
            weights[position.symbol] = float(position.target_weight)
            invested_value += qty * price
        elif position.shares is not None:
            shares[position.symbol] = float(position.shares)
            weights[position.symbol] = position.target_weight
            if price:
                invested_value += float(position.shares) * price
            _append_reason(reasons, "position_uses_plan_shares")
        else:
            _append_reason(reasons, f"missing_position_size:{position.symbol}")
    if any(pos.target_weight is not None for pos in plan.positions):
        cash = equity * max(0.0, min(1.0, plan.cash_weight))
    else:
        cash = max(0.0, equity - invested_value)
        _append_reason(reasons, "cash_inferred_from_trade_intent_notional")
    return shares, weights, cash, _dedupe_reasons(reasons)


def _missing_intended_row(date: str, reasons: list[str]) -> dict[str, Any]:
    return {
        "date": date,
        "strategy_id": None,
        "intended_equity_value": None,
        "intended_cash": None,
        "intended_gross_exposure": None,
        "intended_positions": [],
        "intended_return_daily": None,
        "intended_return_cumulative": None,
        "price_source": [],
        "plan_source": None,
        "missing_symbols": [],
        "reason_codes": _dedupe_reasons(reasons),
    }


def _mark_intended_row(
    *,
    date: str,
    plan: PlanSnapshot,
    shares: dict[str, float],
    weights: dict[str, float | None],
    cash: float,
    prices: PriceStore,
    row_reasons: list[str],
) -> dict[str, Any]:
    positions: list[dict[str, Any]] = []
    missing_symbols: list[str] = []
    market_value = 0.0
    reasons = list(row_reasons)
    for symbol, qty in sorted(shares.items()):
        price = prices.get(symbol, date)
        if price is None and date == plan.date:
            price = next((pos.plan_price for pos in plan.positions if pos.symbol == symbol), None)
            if price is not None:
                _append_reason(reasons, "mark_uses_plan_execution_price")
        if price is None:
            missing_symbols.append(symbol)
            continue
        value = qty * price
        market_value += abs(value)
        positions.append(
            {
                "symbol": symbol,
                "shares": _round(qty, 6),
                "price": _round(price, 6),
                "market_value": _round(value, 6),
                "target_weight": _round(weights.get(symbol), 8),
            }
        )
    if missing_symbols:
        for symbol in missing_symbols:
            _append_reason(reasons, f"missing_price:{symbol}")
        equity = None
        gross = None
    else:
        equity = cash + market_value
        gross = market_value / equity if equity else None
    return {
        "date": date,
        "strategy_id": plan.strategy_id,
        "intended_equity_value": _round(equity, 6),
        "intended_cash": _round(cash, 6),
        "intended_gross_exposure": _round(gross, 10),
        "intended_positions": positions,
        "price_source": prices.source_paths,
        "plan_source": plan.plan_source,
        "missing_symbols": sorted(missing_symbols),
        "reason_codes": _dedupe_reasons(reasons),
    }


def build_actual_nav(*, trade_date: str, repo_root: Path | str = Path(".")) -> dict[str, Any]:
    repo = Path(repo_root)
    rows, nav_diagnostics, latest_nav_class = _actual_rows_from_nav_series(repo, trade_date)
    snapshot_diagnostics = _new_diagnostics()
    reasons: list[str] = []
    snapshot_row: dict[str, Any] | None = None
    if not rows:
        snapshot_row, snapshot_diagnostics = _actual_row_from_snapshot(repo, trade_date)
        if snapshot_row:
            rows = [snapshot_row]
            reasons.append("actual_nav_series_missing_using_broker_snapshot")
        else:
            nav_diagnostics["max_available_date"] = None
            return {
                "schema_version": SCHEMA_VERSION,
                "date": trade_date,
                "available": False,
                "confidence": "LOW",
                "actual_positions": [],
                "timeseries": [],
                "reason_codes": _dedupe_reasons(["missing_actual_nav", "actual_nav_missing"]),
                "source_artifacts": [],
                "source_diagnostics": {
                    "actual_nav": nav_diagnostics,
                    "nav": nav_diagnostics,
                    "snapshot": snapshot_diagnostics,
                    "positions": _new_diagnostics(),
                },
            }
    else:
        latest_row_date = rows[-1].get("date")
        if latest_row_date and latest_row_date < trade_date:
            snapshot_row, snapshot_diagnostics = _actual_row_from_snapshot(repo, trade_date)
            if snapshot_row:
                rows.append(snapshot_row)
                reasons.append("actual_nav_extended_from_current_broker_artifact")
    rows = _compute_return_fields(rows, value_key="actual_equity_value", daily_key="actual_return_daily", cumulative_key="actual_return_cumulative")
    latest = rows[-1]
    positions, position_sources, position_reasons, position_diagnostics = _actual_positions_for_date(repo, trade_date)
    if positions:
        latest["actual_positions"] = positions
    reasons.extend(position_reasons)
    source_artifacts = sorted({source for row in rows for source in row.get("source_artifacts", [])} | set(position_sources))
    reasons.extend(reason for row in rows for reason in row.get("reason_codes", []) if reason != "ok")
    # FR-058: record final coverage, provenance of the latest date, and staleness.
    latest_available_date = latest.get("date")
    nav_diagnostics["max_available_date"] = latest_available_date
    provenance = _ACTUAL_NAV_REASON_BY_CLASS.get(latest_nav_class) if latest_nav_class else None
    if provenance:
        reasons.append(provenance)
    if latest_available_date and latest_available_date < trade_date:
        reasons.append("actual_nav_stale")
    return {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "available": True,
        "confidence": "MEDIUM" if latest.get("actual_equity_value") is not None else "LOW",
        "actual_equity_value": latest.get("actual_equity_value"),
        "actual_cash": latest.get("actual_cash"),
        "actual_gross_exposure": latest.get("actual_gross_exposure"),
        "actual_positions": latest.get("actual_positions") or [],
        "actual_return_daily": latest.get("actual_return_daily"),
        "actual_return_cumulative": latest.get("actual_return_cumulative"),
        "actual_nav_max_available_date": latest_available_date,
        "broker_source": latest.get("broker_source"),
        "reconciliation_source": latest.get("reconciliation_source"),
        "reason_codes": _dedupe_reasons(reasons),
        "timeseries": rows,
        "source_artifacts": source_artifacts,
        "source_diagnostics": {
            "actual_nav": nav_diagnostics,
            "nav": nav_diagnostics,
            "snapshot": snapshot_diagnostics,
            "positions": position_diagnostics,
        },
    }


def _ranked_actual_nav_candidates(repo: Path) -> list[tuple[Path, str, int]]:
    """FR-058: actual-NAV source candidates tagged with (source_class, confidence)."""
    ranked: list[tuple[Path, str, int]] = [
        (repo / rel, source_class, confidence)
        for rel, source_class, confidence in _ACTUAL_NAV_CSV_SOURCES
    ]
    for path in _run_scoped_paths(repo, "snapshots/nav_timeseries.csv"):
        ranked.append((path, "run_snapshot", ACTUAL_NAV_CONFIDENCE_RUN_SNAPSHOT))
    return ranked


def _actual_rows_from_nav_series(
    repo: Path, trade_date: str
) -> tuple[list[dict[str, Any]], dict[str, Any], str | None]:
    diagnostics = _new_diagnostics()
    by_date: dict[str, dict[str, Any]] = {}
    confidence_by_date: dict[str, int] = {}
    class_by_date: dict[str, str] = {}
    for path, source_class, confidence in _ranked_actual_nav_candidates(repo):
        _diag_add_candidate(diagnostics, path)
        if not path.exists():
            _diag_add_failure(diagnostics, path, "missing")
            continue
        rows = _read_csv_rows(path)
        if not rows:
            _diag_add_failure(diagnostics, path, "empty_or_unreadable")
            continue
        parsed = 0
        won = 0
        for raw in rows:
            date = _date_text(raw.get("date") or raw.get("as_of_date"))
            if not date or date > trade_date:
                continue
            equity = _safe_float(raw.get("equity") or raw.get("portfolio_value") or raw.get("nav"))
            if equity is None:
                continue
            parsed += 1
            # Freshest-by-union with confidence tie-break: a lower-confidence
            # source never overrides a value a higher-confidence source supplied,
            # but still fills any date the higher-confidence sources lack.
            existing = confidence_by_date.get(date)
            if existing is not None and confidence < existing:
                continue
            cash = _safe_float(raw.get("cash") or raw.get("cash_value"))
            gross = _safe_float(raw.get("gross_exposure") or raw.get("gross") or raw.get("exposure"))
            if gross is None and cash is not None:
                gross = max(0.0, (float(equity) - float(cash)) / float(equity))
            by_date[date] = {
                "date": date,
                "actual_equity_value": _round(equity, 6),
                "actual_cash": _round(cash, 6),
                "actual_gross_exposure": _round(gross, 10),
                "actual_positions": [],
                "broker_source": str(path),
                "actual_nav_source_class": source_class,
                "reconciliation_source": None,
                "reason_codes": ["ok"],
                "source_artifacts": [str(path)],
            }
            confidence_by_date[date] = confidence
            class_by_date[date] = source_class
            won += 1
        if won > 0:
            _diag_add_selected(diagnostics, path)
        elif parsed == 0:
            _diag_add_failure(diagnostics, path, "no_valid_nav_rows")
        else:
            # Source was valid but every date was supplied by a higher-confidence
            # source; record it as considered, not failed.
            _diag_add_failure(diagnostics, path, "superseded_by_higher_confidence")
    ordered = [by_date[date] for date in sorted(by_date)]
    max_available_date = max(by_date) if by_date else None
    diagnostics["max_available_date"] = max_available_date
    latest_source_class = class_by_date.get(max_available_date) if max_available_date else None
    return ordered, diagnostics, latest_source_class


def _actual_row_from_snapshot(repo: Path, trade_date: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    diagnostics = _new_diagnostics()
    candidates = _actual_json_candidates(repo, trade_date)
    for path in candidates:
        _diag_add_candidate(diagnostics, path)
        payload = _read_json(path)
        if payload is None:
            _diag_add_failure(diagnostics, path, "missing_or_unreadable")
            continue
        payload = _enrich_actual_payload_from_sibling_artifacts(path, payload)
        payload_date = _payload_date(payload)
        if payload_date and payload_date != trade_date:
            _diag_add_failure(diagnostics, path, f"stale_trade_date:{payload_date}")
            continue
        equity = _actual_equity_from_payload(payload)
        positions = _positions_from_actual_payload(payload)
        cash = _actual_cash_from_payload(payload)
        market_value = sum(abs(float(pos.get("market_value") or 0.0)) for pos in positions)
        row_reasons: list[str] = []
        if equity is None and cash is not None and market_value > 0.0:
            equity = cash + market_value
            row_reasons.append("actual_equity_inferred_from_positions_and_cash")
        if equity is None:
            _diag_add_failure(diagnostics, path, "missing_actual_equity")
            continue
        gross = market_value / equity if equity and market_value else None
        if gross is None and equity and cash is not None:
            gross = max(0.0, (float(equity) - float(cash)) / float(equity))
        if "recon_posttrade_" in path.name:
            row_reasons.append("actual_from_posttrade_reconciliation")
            assessment = _reconciliation_assessment(payload, repo=repo, source_path=path)
            if not assessment.get("clean"):
                row_reasons.append("reconciliation_not_clean")
            for resolution in assessment.get("alias_resolutions_applied") or []:
                row_reasons.append(_alias_resolution_reason(resolution))
        else:
            row_reasons.append("actual_from_broker_snapshot")
        _diag_add_selected(diagnostics, path)
        return {
            "date": trade_date,
            "actual_equity_value": _round(equity, 6),
            "actual_cash": _round(cash, 6),
            "actual_gross_exposure": _round(gross, 10),
            "actual_positions": positions,
            "broker_source": str(path),
            "reconciliation_source": str(path) if "recon_posttrade_" in path.name else None,
            "reason_codes": _dedupe_reasons(row_reasons),
            "source_artifacts": [str(path)],
        }, diagnostics
    return None, diagnostics


def _run_scoped_paths(repo: Path, suffix: str) -> list[Path]:
    paths: list[Path] = []
    for runs_root in (
        repo / "outputs" / "runs",
        repo / "outputs" / "paper_lane" / "runs",
        repo / "outputs" / "live_pilot" / "runs",
    ):
        if runs_root.exists():
            paths.extend(runs_root.glob(f"*/{suffix}"))
    return sorted(paths, reverse=True)


def _actual_json_candidates(repo: Path, trade_date: str) -> list[Path]:
    candidates: list[Path] = []
    exact_evidence = _validated_exact_execution_evidence(repo, trade_date)
    if exact_evidence is not None:
        candidates.append(Path(exact_evidence["run_root"]) / "live_pilot_broker_snapshot_post.json")
    pointer_path = repo / "outputs" / "workflow" / trade_date / "execution.json"
    if pointer_path.exists():
        pointer = _read_json(pointer_path)
        run_root = _resolve_repo_path(repo, pointer.get("run_root")) if pointer else None
        run_id = str((pointer or {}).get("run_id") or "").strip()
        if (
            not pointer
            or _payload_date(pointer) != trade_date
            or run_root is None
            or not run_id
            or run_root.name != run_id
        ):
            return []
        candidates.extend(
            [
                run_root / "broker" / f"recon_posttrade_{trade_date}.json",
                run_root / "broker" / "posttrade_account_snapshot.json",
                run_root / "broker" / "posttrade_positions.json",
                run_root / "trading_day_summary.json",
                run_root / "execution_payload.json",
                run_root / "live_pilot_broker_snapshot_post.json",
            ]
        )
        return _dedupe_paths(candidates)
    candidates.extend(_run_scoped_paths(repo, f"broker/recon_posttrade_{trade_date}.json"))
    candidates.append(repo / "outputs" / "broker" / f"recon_posttrade_{trade_date}.json")
    candidates.extend(_run_scoped_paths(repo, "broker/posttrade_account_snapshot.json"))
    candidates.extend(_run_scoped_paths(repo, "broker/posttrade_positions.json"))
    candidates.extend(_run_scoped_paths(repo, "trading_day_summary.json"))
    candidates.extend(_run_scoped_paths(repo, "execution_payload.json"))
    candidates.extend(_run_scoped_paths(repo, "live_pilot_broker_snapshot_post.json"))
    candidates.append(repo / "outputs" / "broker" / "posttrade_account_snapshot.json")
    candidates.append(repo / "outputs" / "broker" / "posttrade_positions.json")
    candidates.append(repo / "outputs" / "broker_snapshot" / f"broker_snapshot_{trade_date}.json")
    candidates.append(repo / "outputs" / "broker" / "broker_snapshot_latest.json")
    return _dedupe_paths(candidates)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        text = str(path)
        if text not in seen:
            out.append(path)
            seen.add(text)
    return out


def _payload_date(payload: dict[str, Any]) -> str | None:
    return _date_text(
        payload.get("trade_date")
        or payload.get("report_date")
        or payload.get("date")
        or payload.get("as_of")
        or payload.get("captured_at")
    )


def _actual_equity_from_payload(payload: dict[str, Any]) -> float | None:
    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    portfolio_state = payload.get("portfolio_state") if isinstance(payload.get("portfolio_state"), dict) else {}
    broker_preflight = payload.get("broker_preflight") if isinstance(payload.get("broker_preflight"), dict) else {}
    return _safe_float(
        payload.get("broker_equity")
        or payload.get("posttrade_equity")
        or payload.get("equity_after")
        or payload.get("portfolio_value")
        or payload.get("equity")
        or portfolio_state.get("equity")
        or portfolio_state.get("total_equity")
        or portfolio_state.get("portfolio_value")
        or broker_preflight.get("equity")
        or account.get("equity")
        or account.get("portfolio_value")
    )


def _actual_cash_from_payload(payload: dict[str, Any]) -> float | None:
    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    portfolio_state = payload.get("portfolio_state") if isinstance(payload.get("portfolio_state"), dict) else {}
    broker_preflight = payload.get("broker_preflight") if isinstance(payload.get("broker_preflight"), dict) else {}
    return _safe_float(
        payload.get("broker_cash")
        or payload.get("posttrade_cash")
        or payload.get("cash_after")
        or payload.get("cash")
        or portfolio_state.get("cash_after")
        or portfolio_state.get("cash")
        or broker_preflight.get("cash")
        or account.get("cash")
    )


def _enrich_actual_payload_from_sibling_artifacts(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Combine split broker account/position artifacts from the same run.

    Some run roots persist account and positions separately. Operational drag
    needs both NAV/cash and positions for current-date attribution, so this
    read-only merge lets either sibling artifact satisfy the actual snapshot
    contract without changing the source artifacts.
    """
    enriched = dict(payload or {})
    if path.name == "posttrade_positions.json":
        account_path = path.with_name("posttrade_account_snapshot.json")
        account = _read_json(account_path)
        if isinstance(account, dict):
            enriched.setdefault("account", account.get("account") if isinstance(account.get("account"), dict) else account)
            for key in ("trade_date", "cash", "equity", "portfolio_value", "buying_power"):
                if enriched.get(key) is None and account.get(key) is not None:
                    enriched[key] = account.get(key)
    elif path.name == "posttrade_account_snapshot.json":
        positions_path = path.with_name("posttrade_positions.json")
        positions = _read_json(positions_path)
        if isinstance(positions, dict):
            for key in ("positions_current", "positions", "normalized_positions"):
                if enriched.get(key) is None and positions.get(key) is not None:
                    enriched[key] = positions.get(key)
    return enriched


def _reconciliation_assessment(
    payload: dict[str, Any],
    *,
    repo: Path | None = None,
    source_path: Path | str | None = None,
) -> dict[str, Any]:
    if payload.get("schema_version") == "caerus.canonical_economic_verification.v1":
        economic = payload.get("economic_reconciliation")
        sleeve = payload.get("sleeve_attribution_reconciliation")
        economic = economic if isinstance(economic, dict) else {}
        sleeve = sleeve if isinstance(sleeve, dict) else {}
        positions = economic.get("positions") if isinstance(economic.get("positions"), dict) else {}
        raw_expected = _position_map_from_any(positions.get("expected"))
        raw_actual = _position_map_from_any(positions.get("actual"))
        aliases = _load_manual_aliases(repo) if repo is not None else {}
        expected, expected_aliases = _normalize_position_aliases(raw_expected, aliases)
        actual, actual_aliases = _normalize_position_aliases(raw_actual, aliases)
        mismatches = _position_mismatches(expected, actual)
        alias_resolutions = expected_aliases + [
            item for item in actual_aliases if item not in expected_aliases
        ]
        quantity_deltas = _position_map_from_any(positions.get("quantity_deltas"))
        material_delta = any(abs(float(value)) > 1e-8 for value in quantity_deltas.values())
        status_clean = (
            str(payload.get("status") or "").upper() == "RECONCILED"
            and payload.get("reconciled") is True
            and str(economic.get("status") or "").upper() == "RECONCILED"
            and economic.get("reconciled") is True
            and str(sleeve.get("status") or "").upper() == "RECONCILED"
            and sleeve.get("reconciled") is True
            and verify_canonical_economic_artifact_hash(payload)
        )
        clean = status_clean and not mismatches and not material_delta
        reasons = ["reconciliation_clean" if clean else "reconciliation_not_clean"]
        reasons.extend(_alias_resolution_reason(item) for item in alias_resolutions)
        if any(row.get("classification") == "MISSING_BROKER_POSITION" for row in mismatches):
            reasons.append("missing_broker_position")
        return {
            "clean": clean,
            "status_clean": status_clean,
            "alias_resolved_clean": False,
            "verdict": str(payload.get("status") or "").upper(),
            "drift_status": str(economic.get("status") or "").upper(),
            "expected_positions": expected,
            "broker_positions": actual,
            "raw_expected_positions": raw_expected,
            "raw_broker_positions": raw_actual,
            "mismatches": mismatches,
            "alias_resolutions_applied": alias_resolutions,
            "source_path": str(source_path) if source_path is not None else None,
            "reason_codes": _dedupe_reasons(reasons),
        }
    verdict = str(payload.get("verdict") or payload.get("status") or "").upper()
    drift_status = str(payload.get("drift_status") or payload.get("comparison_status") or "").upper()
    verdict_clean = verdict in {"", "PASS", "OK", "CLEAN"}
    drift_clean = not drift_status or "OK" in drift_status or "RECONCILED" in drift_status
    status_clean = verdict_clean and drift_clean
    aliases = _load_manual_aliases(repo) if repo is not None else {}
    has_expected_positions = isinstance(payload.get("expected_positions"), (dict, list))
    has_actual_positions = isinstance(payload.get("actual_positions"), (dict, list))
    raw_expected = _position_map_from_any(payload.get("expected_positions")) if has_expected_positions else {}
    raw_actual = _position_map_from_any(payload.get("actual_positions")) if has_actual_positions else {}
    expected, expected_aliases = _normalize_position_aliases(raw_expected, aliases)
    actual, actual_aliases = _normalize_position_aliases(raw_actual, aliases)
    mismatches = _position_mismatches(expected, actual) if has_expected_positions and has_actual_positions else []
    alias_resolutions = expected_aliases + [
        item for item in actual_aliases
        if item not in expected_aliases
    ]
    raw_position_issue = _raw_reconciliation_position_issue(payload)
    alias_resolved_clean = bool(alias_resolutions) and raw_position_issue and not mismatches
    clean = (status_clean and not raw_position_issue and not mismatches) or alias_resolved_clean
    reason_codes: list[str] = []
    if clean:
        reason_codes.append("reconciliation_clean")
    else:
        reason_codes.append("reconciliation_not_clean")
    reason_codes.extend(_alias_resolution_reason(item) for item in alias_resolutions)
    if any(row.get("classification") == "MISSING_BROKER_POSITION" for row in mismatches):
        reason_codes.append("missing_broker_position")
    return {
        "clean": clean,
        "status_clean": status_clean,
        "alias_resolved_clean": alias_resolved_clean,
        "verdict": verdict,
        "drift_status": drift_status,
        "expected_positions": expected,
        "broker_positions": actual,
        "raw_expected_positions": raw_expected,
        "raw_broker_positions": raw_actual,
        "mismatches": mismatches,
        "alias_resolutions_applied": alias_resolutions,
        "source_path": str(source_path) if source_path is not None else None,
        "reason_codes": _dedupe_reasons(reason_codes),
    }


def _reconciliation_clean(payload: dict[str, Any], *, repo: Path | None = None) -> bool:
    return bool(_reconciliation_assessment(payload, repo=repo).get("clean"))


def _positions_from_actual_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("actual_positions"), dict):
        return [
            {"symbol": str(symbol).upper(), "shares": _round(qty, 6), "price": None, "market_value": None}
            for symbol, qty in sorted(payload["actual_positions"].items())
            if str(symbol).strip()
        ]
    return _positions_from_broker_snapshot(payload)


def _positions_from_broker_snapshot(payload: dict[str, Any]) -> list[dict[str, Any]]:
    positions = (
        payload.get("positions_current")
        or payload.get("positions")
        or payload.get("normalized_positions")
        or []
    )
    out: list[dict[str, Any]] = []
    if isinstance(positions, dict):
        for symbol, qty in sorted(positions.items()):
            numeric_qty = _safe_float(qty)
            normalized = str(symbol or "").upper().strip()
            if normalized and numeric_qty is not None:
                out.append(
                    {
                        "symbol": normalized,
                        "shares": _round(numeric_qty, 6),
                        "price": None,
                        "market_value": None,
                    }
                )
        return out
    if not isinstance(positions, list):
        return out
    for row in positions:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or row.get("ticker") or "").upper().strip()
        qty = _safe_float(row.get("qty") or row.get("shares") or row.get("quantity"))
        market_value = _safe_float(row.get("market_value"))
        price = _safe_float(row.get("current_price") or row.get("price"))
        if symbol and qty is not None:
            out.append(
                {
                    "symbol": symbol,
                    "shares": _round(qty, 6),
                    "price": _round(price, 6),
                    "market_value": _round(market_value, 6),
                }
            )
    return sorted(out, key=lambda row: row["symbol"])


def _positions_from_portfolio_history(repo: Path, trade_date: str, diagnostics: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    path = repo / "outputs" / "portfolio_history" / "positions.csv"
    _diag_add_candidate(diagnostics, path)
    if not path.exists():
        _diag_add_failure(diagnostics, path, "missing")
        return [], [], []
    rows = _read_csv_rows(path)
    if not rows:
        _diag_add_failure(diagnostics, path, "empty_or_unreadable")
        return [], [], []
    positions: list[dict[str, Any]] = []
    for raw in rows:
        date = _date_text(raw.get("as_of_date") or raw.get("date") or raw.get("trade_date"))
        if date != trade_date:
            continue
        symbol = str(raw.get("ticker") or raw.get("symbol") or "").upper().strip()
        qty = _safe_float(raw.get("quantity") or raw.get("qty") or raw.get("shares"))
        price = _safe_float(raw.get("current_price") or raw.get("price"))
        market_value = _safe_float(raw.get("market_value"))
        if symbol and qty is not None:
            positions.append(
                {
                    "symbol": symbol,
                    "shares": _round(qty, 6),
                    "price": _round(price, 6),
                    "market_value": _round(market_value, 6),
                }
            )
    if not positions:
        _diag_add_failure(diagnostics, path, f"no_positions_for_date:{trade_date}")
        return [], [], []
    _diag_add_selected(diagnostics, path)
    return sorted(positions, key=lambda row: row["symbol"]), [str(path)], ["actual_positions_from_portfolio_history"]


def _actual_positions_for_date(repo: Path, trade_date: str) -> tuple[list[dict[str, Any]], list[str], list[str], dict[str, Any]]:
    diagnostics = _new_diagnostics()
    for path in _actual_json_candidates(repo, trade_date):
        _diag_add_candidate(diagnostics, path)
        payload = _read_json(path)
        if payload is None:
            _diag_add_failure(diagnostics, path, "missing_or_unreadable")
            continue
        payload_date = _payload_date(payload)
        if payload_date and payload_date != trade_date:
            _diag_add_failure(diagnostics, path, f"stale_trade_date:{payload_date}")
            continue
        positions = _positions_from_actual_payload(payload)
        if not positions:
            _diag_add_failure(diagnostics, path, "no_positions")
            continue
        reasons: list[str]
        if "recon_posttrade_" in path.name:
            reasons = ["actual_positions_from_reconciliation"]
            assessment = _reconciliation_assessment(payload, repo=repo, source_path=path)
            if assessment.get("clean"):
                reasons.append("actual_positions_from_reconciled_posttrade")
            else:
                reasons.append("reconciliation_not_clean")
            for resolution in assessment.get("alias_resolutions_applied") or []:
                reasons.append(_alias_resolution_reason(resolution))
        else:
            reasons = ["actual_positions_from_broker_artifact"]
        _diag_add_selected(diagnostics, path)
        return positions, [str(path)], _dedupe_reasons(reasons), diagnostics
    csv_positions, sources, reasons = _positions_from_portfolio_history(repo, trade_date, diagnostics)
    if csv_positions:
        return csv_positions, sources, reasons, diagnostics
    return [], [], ["actual_positions_unavailable"], diagnostics


def build_benchmark_nav(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    aligned_dates: list[str] | None = None,
    price_store: PriceStore | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root)
    prices = price_store or load_price_store(repo, trade_date=trade_date)
    rows, source_paths, diagnostics = _benchmark_rows(repo, trade_date, prices)
    reasons: list[str] = []
    if aligned_dates is not None:
        wanted = set(aligned_dates)
        existing = {row["date"] for row in rows}
        missing = sorted(wanted - existing)
        rows = [row for row in rows if row["date"] in wanted]
        reasons.extend(f"missing_spy_price:{date}" for date in missing)
    rows = _compute_return_fields(rows, value_key="spy_price", daily_key="spy_return_daily", cumulative_key="spy_return_cumulative")
    if not rows:
        return {
            "schema_version": SCHEMA_VERSION,
            "date": trade_date,
            "available": False,
            "confidence": "LOW",
            "timeseries": [],
            "reason_codes": _dedupe_reasons(["missing_spy_benchmark"] + reasons),
            "source_artifacts": [],
            "source_diagnostics": {"benchmark": diagnostics, "price": prices.diagnostics()},
        }
    latest = rows[-1]
    return {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "available": True,
        "confidence": "MEDIUM" if not reasons else "LOW",
        "spy_price": latest.get("spy_price"),
        "spy_return_daily": latest.get("spy_return_daily"),
        "spy_return_cumulative": latest.get("spy_return_cumulative"),
        "price_source": source_paths,
        "reason_codes": _dedupe_reasons(reasons),
        "timeseries": rows,
        "source_artifacts": source_paths,
        "source_diagnostics": {"benchmark": diagnostics, "price": prices.diagnostics()},
    }


def _benchmark_rows(repo: Path, trade_date: str, prices: PriceStore) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    diagnostics = _new_diagnostics()
    candidates = [repo / rel for rel in BENCHMARK_CSV_CANDIDATES]
    by_date: dict[str, dict[str, Any]] = {}
    source_by_date: dict[str, str] = {}
    # The fresh, date-scoped hydration artifact is the top-priority SPY source.
    # It is consulted before the curated benchmark CSVs so the current trade
    # date is sourced from hydration rather than a stale fallback. CSVs and the
    # parquet price store still fill every other date (fallback preserved).
    hydration_path = repo / "outputs" / "operational_drag" / str(trade_date) / "price_hydration.json"
    hydration_text = str(hydration_path)
    _diag_add_candidate(diagnostics, hydration_path)
    hydration_spy = prices.prices.get("SPY", {})
    for date in sorted(hydration_spy):
        if date > trade_date or date in by_date:
            continue
        if prices.source_for("SPY", date) != hydration_text:
            continue
        by_date[date] = {"date": date, "spy_price": _round(hydration_spy[date], 6), "reason_codes": ["ok"]}
        source_by_date[date] = hydration_text
        _diag_add_selected(diagnostics, hydration_path)
    for path in candidates:
        _diag_add_candidate(diagnostics, path)
        if not path.exists():
            _diag_add_failure(diagnostics, path, "missing")
            continue
        rows = _read_csv_rows(path)
        if not rows:
            _diag_add_failure(diagnostics, path, "empty_or_unreadable")
            continue
        loaded = 0
        for raw in rows:
            date = _date_text(raw.get("date"))
            if not date or date > trade_date or date in by_date:
                continue
            price = _safe_float(raw.get("spy_close") or raw.get("close") or raw.get("price"))
            if price is None:
                continue
            by_date[date] = {"date": date, "spy_price": _round(price, 6), "reason_codes": ["ok"]}
            source_by_date[date] = str(path)
            loaded += 1
        if loaded > 0:
            _diag_add_selected(diagnostics, path)
        else:
            _diag_add_failure(diagnostics, path, "no_valid_spy_rows")
    spy_prices = prices.prices.get("SPY", {})
    for date in sorted(spy_prices):
        if date > trade_date or date in by_date:
            continue
        source = prices.source_for("SPY", date) or (prices.source_paths[0] if prices.source_paths else "price_store")
        by_date[date] = {"date": date, "spy_price": _round(spy_prices[date], 6), "reason_codes": ["ok"]}
        source_by_date[date] = source
        _diag_add_selected(diagnostics, source)
    rows = [by_date[date] | {"price_source": source_by_date[date]} for date in sorted(by_date)]
    return rows, _dedupe_list(list(source_by_date.values())), diagnostics


def _compute_return_fields(
    rows: list[dict[str, Any]],
    *,
    value_key: str,
    daily_key: str,
    cumulative_key: str,
) -> list[dict[str, Any]]:
    previous: float | None = None
    base: float | None = None
    out: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: item["date"]):
        value = _safe_float(row.get(value_key))
        if value is None:
            row[daily_key] = None
            row[cumulative_key] = None
            out.append(row)
            continue
        if base is None:
            base = value
            row[daily_key] = 0.0
            row[cumulative_key] = 0.0
        else:
            row[daily_key] = _round((value / previous - 1.0) if previous else None)
            row[cumulative_key] = _round((value / base - 1.0) if base else None)
        previous = value
        out.append(row)
    return out


def build_operational_drag(
    *,
    trade_date: str,
    intended: dict[str, Any],
    actual: dict[str, Any],
    benchmark: dict[str, Any],
) -> dict[str, Any]:
    intended_by_date = {row["date"]: row for row in intended.get("timeseries") or []}
    actual_by_date = {row["date"]: row for row in actual.get("timeseries") or []}
    spy_by_date = {row["date"]: row for row in benchmark.get("timeseries") or []}
    aligned_dates = sorted(set(intended_by_date) & set(actual_by_date) & set(spy_by_date))
    rows: list[dict[str, Any]] = []
    base_intended: float | None = None
    base_actual: float | None = None
    base_spy: float | None = None
    prev_intended: float | None = None
    prev_actual: float | None = None
    prev_spy: float | None = None
    for date in aligned_dates:
        intended_row = intended_by_date[date]
        actual_row = actual_by_date[date]
        spy_row = spy_by_date[date]
        intended_value = _safe_float(intended_row.get("intended_equity_value"))
        actual_value = _safe_float(actual_row.get("actual_equity_value"))
        spy_value = _safe_float(spy_row.get("spy_price"))
        if intended_value is not None and actual_value is not None and spy_value is not None and base_intended is None:
            base_intended = intended_value
            base_actual = actual_value
            base_spy = spy_value
            prev_intended = intended_value
            prev_actual = actual_value
            prev_spy = spy_value
        intended_return = (
            intended_value / prev_intended - 1.0
            if intended_value is not None and prev_intended
            else None
        )
        actual_return = (
            actual_value / prev_actual - 1.0
            if actual_value is not None and prev_actual
            else None
        )
        spy_return = (
            spy_value / prev_spy - 1.0
            if spy_value is not None and prev_spy
            else None
        )
        intended_cumulative = (
            intended_value / base_intended - 1.0
            if intended_value is not None and base_intended
            else None
        )
        actual_cumulative = (
            actual_value / base_actual - 1.0
            if actual_value is not None and base_actual
            else None
        )
        spy_cumulative = (
            spy_value / base_spy - 1.0
            if spy_value is not None and base_spy
            else None
        )
        if intended_value is not None:
            prev_intended = intended_value
        if actual_value is not None:
            prev_actual = actual_value
        if spy_value is not None:
            prev_spy = spy_value
        intended_gross = _safe_float(intended_row.get("intended_gross_exposure"))
        actual_gross = _safe_float(actual_row.get("actual_gross_exposure"))
        exposure_gap = (
            intended_gross - actual_gross
            if intended_gross is not None and actual_gross is not None
            else None
        )
        reasons = _dedupe_reasons(
            [reason for reason in intended_row.get("reason_codes", []) if reason != "ok"]
            + [reason for reason in actual_row.get("reason_codes", []) if reason != "ok"]
            + [reason for reason in spy_row.get("reason_codes", []) if reason != "ok"]
        )
        rows.append(
            {
                "date": date,
                "intended_return_daily": _round(intended_return),
                "actual_return_daily": _round(actual_return),
                "spy_return_daily": _round(spy_return),
                "daily_operational_drag": _round(
                    intended_return - actual_return
                    if intended_return is not None and actual_return is not None
                    else None
                ),
                "cumulative_operational_drag": _round(
                    intended_cumulative - actual_cumulative
                    if intended_cumulative is not None and actual_cumulative is not None
                    else None
                ),
                "intended_vs_spy_excess": _round(
                    intended_cumulative - spy_cumulative
                    if intended_cumulative is not None and spy_cumulative is not None
                    else None
                ),
                "actual_vs_spy_excess": _round(
                    actual_cumulative - spy_cumulative
                    if actual_cumulative is not None and spy_cumulative is not None
                    else None
                ),
                "actual_underdeployment": _round(max(0.0, exposure_gap) if exposure_gap is not None else None),
                "intended_gross_exposure": _round(intended_gross),
                "actual_gross_exposure": _round(actual_gross),
                "exposure_gap": _round(exposure_gap),
                "reason_codes": reasons,
            }
        )
    available_rows = [row for row in rows if row.get("daily_operational_drag") is not None]
    reasons = []
    if not rows:
        reasons.append("no_aligned_nav_dates")
    if len(available_rows) < 2:
        reasons.append("operational_drag_unavailable_fewer_than_two_aligned_observations")
    latest = available_rows[-1] if len(available_rows) >= 2 else (rows[-1] if rows else {})
    reasons.extend(reason for row in rows for reason in row.get("reason_codes", []) if reason != "ok")
    material_reasons = [reason for reason in reasons if _is_material_data_reason(reason)]
    return {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "available": len(available_rows) >= 2,
        "confidence": "MEDIUM" if len(available_rows) >= 2 and not material_reasons else "LOW",
        "latest": latest,
        "timeseries": rows,
        "reason_codes": _dedupe_reasons(reasons),
        "source_artifacts": _dedupe_list(
            list(intended.get("source_artifacts") or [])
            + list(actual.get("source_artifacts") or [])
            + list(benchmark.get("source_artifacts") or [])
        ),
        "source_diagnostics": {
            "intended": intended.get("source_diagnostics") or {},
            "actual": actual.get("source_diagnostics") or {},
            "benchmark": benchmark.get("source_diagnostics") or {},
        },
    }


def _dedupe_list(values: list[str]) -> list[str]:
    return sorted({str(value) for value in values if value})


def build_operational_drag_attribution(
    *,
    trade_date: str,
    repo_root: Path | str,
    operational_drag: dict[str, Any],
    intended: dict[str, Any],
    actual: dict[str, Any],
) -> dict[str, Any]:
    repo = Path(repo_root)
    rows = operational_drag.get("timeseries") or []
    entries: list[dict[str, Any]] = []
    underdeploy_rows = [
        row for row in rows
        if (_safe_float(row.get("actual_underdeployment")) or 0.0) >= UNDERDEPLOYMENT_THRESHOLD
    ]
    if underdeploy_rows:
        estimated = sum((_safe_float(row.get("daily_operational_drag")) or 0.0) * 10_000.0 for row in underdeploy_rows)
        entries.append(
            {
                "category": "under_deployment_cash_drag",
                "date_range": _date_range(underdeploy_rows),
                "estimated_drag_bps": _round(estimated, 4),
                "supporting_artifacts": operational_drag.get("source_artifacts") or [],
                "confidence": "MEDIUM",
                "explanation": "Actual gross exposure was materially below intended gross exposure.",
                "reason_codes": ["actual_gross_below_intended_gross"],
            }
        )
    entries.extend(_attribution_from_plan_payloads(repo, trade_date))
    entries.extend(_attribution_from_reconciliation(repo, trade_date))
    missing_reasons = sorted(
        {
            reason
            for payload in (intended, actual, operational_drag)
            for reason in payload.get("reason_codes", [])
            if "missing" in str(reason)
        }
    )
    if missing_reasons:
        entries.append(
            {
                "category": "missing_data",
                "date_range": trade_date,
                "estimated_drag_bps": None,
                "supporting_artifacts": operational_drag.get("source_artifacts") or [],
                "confidence": "HIGH",
                "explanation": "One or more required operational drag inputs were unavailable.",
                "reason_codes": missing_reasons,
            }
        )
    if not entries:
        entries.append(
            {
                "category": "unknown",
                "date_range": trade_date,
                "estimated_drag_bps": None,
                "supporting_artifacts": operational_drag.get("source_artifacts") or [],
                "confidence": "LOW",
                "explanation": "No deterministic attribution category matched the available artifacts.",
                "reason_codes": ["no_attribution_signal"],
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "available": bool(entries),
        "confidence": _aggregate_confidence(entries),
        "attributions": entries,
        "reason_codes": _dedupe_reasons([reason for entry in entries for reason in entry.get("reason_codes", [])]),
        "source_artifacts": _dedupe_list([artifact for entry in entries for artifact in entry.get("supporting_artifacts", [])]),
    }


def _date_range(rows: list[dict[str, Any]]) -> str:
    dates = [row["date"] for row in rows if row.get("date")]
    if not dates:
        return ""
    return dates[0] if dates[0] == dates[-1] else f"{dates[0]}:{dates[-1]}"


def _run_id_from_path(path: Path | str | None) -> str | None:
    if path is None:
        return None
    parts = Path(path).parts
    for idx, part in enumerate(parts):
        if part == "runs" and idx + 1 < len(parts):
            return parts[idx + 1]
    return None


def _current_reconciliation_candidates(repo: Path, trade_date: str) -> list[Path]:
    pointer_path = repo / "outputs" / "workflow" / trade_date / "execution.json"
    if pointer_path.exists():
        pointer = _read_json(pointer_path)
        if not pointer or _payload_date(pointer) != trade_date:
            return []
        run_root = _resolve_repo_path(repo, pointer.get("run_root"))
        run_id = str(pointer.get("run_id") or "").strip()
        if (
            str(pointer.get("stage") or "").strip().lower() != "execution"
            or str(pointer.get("mode") or "").strip().upper() != "PAPER"
            or run_root is None
            or not run_id
            or run_root.name != run_id
        ):
            return []
        return [
            run_root / "live_pilot_reconciliation.json",
            run_root / "broker" / f"recon_posttrade_{trade_date}.json",
        ]
    candidates: list[Path] = []
    candidates.extend(_run_scoped_paths(repo, "live_pilot_reconciliation.json"))
    candidates.extend(_run_scoped_paths(repo, f"broker/recon_posttrade_{trade_date}.json"))
    candidates.append(repo / "outputs" / "broker" / f"recon_posttrade_{trade_date}.json")
    return _dedupe_paths(candidates)


def _select_current_reconciliation(
    repo: Path,
    trade_date: str,
) -> tuple[Path | None, dict[str, Any] | None, dict[str, Any]]:
    diagnostics = _new_diagnostics()
    exact_evidence = _validated_exact_execution_evidence(repo, trade_date)
    if exact_evidence is not None:
        path = Path(exact_evidence["economic_path"])
        _diag_add_candidate(diagnostics, path)
        _diag_add_selected(diagnostics, path)
        return path, dict(exact_evidence["economic"]), diagnostics
    for path in _current_reconciliation_candidates(repo, trade_date):
        _diag_add_candidate(diagnostics, path)
        payload = _read_json(path)
        if payload is None:
            _diag_add_failure(diagnostics, path, "missing_or_unreadable")
            continue
        payload_date = (
            _payload_date(payload)
            or _date_text(path.stem.replace("recon_posttrade_", ""))
            or _date_text(_run_id_from_path(path))
        )
        if payload_date != trade_date:
            _diag_add_failure(diagnostics, path, f"stale_trade_date:{payload_date or 'unknown'}")
            continue
        _diag_add_selected(diagnostics, path)
        return path, payload, diagnostics
    return None, None, diagnostics


def _has_execution_evidence(repo: Path, trade_date: str) -> bool:
    if _validated_exact_execution_evidence(repo, trade_date) is not None:
        return True
    pointer_path = repo / "outputs" / "workflow" / trade_date / "execution.json"
    if pointer_path.exists():
        pointer = _read_json(pointer_path)
        run_root = _resolve_repo_path(repo, pointer.get("run_root")) if pointer else None
        run_id = str((pointer or {}).get("run_id") or "").strip()
        if (
            not pointer
            or _payload_date(pointer) != trade_date
            or run_root is None
            or not run_id
            or run_root.name != run_id
        ):
            return False
        result_paths = [run_root / "execution_results.json"]
        order_paths = [run_root / "broker" / f"orders_{trade_date}.csv"]
    else:
        result_paths = _run_scoped_paths(repo, "execution_results.json")
        order_paths = _run_scoped_paths(repo, f"broker/orders_{trade_date}.csv")
    for path in result_paths:
        run_id = _run_id_from_path(path)
        if not run_id or not run_id.startswith(trade_date):
            continue
        payload = _read_json(path)
        if payload is None:
            continue
        status = str(
            payload.get("status")
            or payload.get("final_execution_status")
            or payload.get("execution_status")
            or ""
        ).upper()
        if status in {"EXECUTED", "RECONCILED_SUCCESS", "PARTIAL"}:
            return True
    for path in order_paths:
        run_id = _run_id_from_path(path)
        if run_id and run_id.startswith(trade_date) and _read_csv_rows(path):
            return True
    return False


def _attribution_from_plan_payloads(repo: Path, trade_date: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    execution_evidence: dict[str, bool] = {}
    for day_dir in sorted((repo / "outputs" / "precompute").glob("*")) if (repo / "outputs" / "precompute").exists() else []:
        if not day_dir.is_dir() or not _is_date(day_dir.name) or day_dir.name > trade_date:
            continue
        path = day_dir / "planned_execution_payload.json"
        payload = _read_json(path)
        if payload is None:
            continue
        payload_text = json.dumps(payload, sort_keys=True).lower()
        date = str(payload.get("trade_date") or day_dir.name)
        if "stale_price" in payload_text or "stale_prices" in payload_text:
            entries.append(_incident_entry("stale_price_gate", date, path, "Plan/execution payload references stale-price gating.", ["stale_price_gate_detected"]))
        planned_buys = int(_safe_float(payload.get("buys")) or 0)
        submitted = int(_safe_float(payload.get("submitted_count") or payload.get("orders_submitted_count")) or 0)
        eligible = int(_safe_float(payload.get("execution_eligible_trades_count") or payload.get("executable_trades_count")) or 0)
        accepted = int(_safe_float(payload.get("accepted_count") or payload.get("orders_filled_count")) or 0)
        if planned_buys > 0 and submitted == 0 and eligible > 0:
            execution_evidence.setdefault(date, _has_execution_evidence(repo, date))
            if execution_evidence[date]:
                continue
            entries.append(_incident_entry("buy_suppression", date, path, "Planned buys existed but no orders were submitted.", ["planned_buys_without_submissions"]))
        elif eligible > 0 and 0 <= accepted < eligible:
            execution_evidence.setdefault(date, _has_execution_evidence(repo, date))
            if execution_evidence[date] and submitted == 0:
                continue
            entries.append(_incident_entry("partial_execution", date, path, "Accepted/filled order count is below eligible planned trade count.", ["eligible_trades_not_fully_accepted"]))
        blocked = payload.get("blocked_tickers")
        if isinstance(blocked, list) and blocked:
            entries.append(_incident_entry("symbol_resolution", date, path, "Payload contains blocked tickers.", ["blocked_tickers_present"]))
    return entries


def _incident_entry(category: str, date: str, path: Path, explanation: str, reasons: list[str]) -> dict[str, Any]:
    return {
        "category": category,
        "date_range": date,
        "estimated_drag_bps": None,
        "supporting_artifacts": [str(path)],
        "confidence": "MEDIUM",
        "explanation": explanation,
        "reason_codes": reasons,
    }


def _attribution_from_reconciliation(repo: Path, trade_date: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    recon_root = repo / "outputs" / "broker"
    current_path, current_payload, _diag = _select_current_reconciliation(repo, trade_date)
    historical: list[tuple[str, Path, dict[str, Any]]] = []
    for path in sorted(recon_root.glob("recon_posttrade_*.json")) if recon_root.exists() else []:
        date = path.stem.replace("recon_posttrade_", "")
        if not _is_date(date) or date >= trade_date:
            continue
        payload = _read_json(path)
        if payload is None:
            continue
        historical.append((date, path, payload))
    current = [(trade_date, current_path, current_payload)] if current_path and current_payload else []
    for date, path, payload in historical + current:
        assessment = _reconciliation_assessment(payload, repo=repo, source_path=path)
        classifications = [
            str(row.get("classification") or "")
            for row in assessment.get("mismatches") or []
            if isinstance(row, dict)
        ]
        if "MISSING_BROKER_POSITION" in classifications:
            entries.append(_incident_entry("missing_broker_position", date, path, "Reconciliation found expected holdings missing at the broker.", ["missing_broker_position"]))
        if not assessment.get("clean"):
            entries.append(_incident_entry("reconciliation_mismatch", date, path, "Reconciliation reported drift or a non-pass verdict.", ["reconciliation_not_clean"]))
    return entries


def build_reconciliation_drift_diagnostic(
    *,
    trade_date: str,
    repo_root: Path | str,
    actual: dict[str, Any],
    attribution: dict[str, Any],
) -> dict[str, Any]:
    repo = Path(repo_root)
    recon_path, payload, diagnostics = _select_current_reconciliation(repo, trade_date)
    stale_warnings = [
        dict(item)
        for item in diagnostics.get("failed_paths", [])
        if isinstance(item, dict) and "stale_trade_date" in str(item.get("reason") or "")
    ]
    historical_recon_issues = [
        {
            "category": entry.get("category"),
            "date_range": entry.get("date_range"),
            "reason_codes": entry.get("reason_codes") or [],
            "supporting_artifacts": entry.get("supporting_artifacts") or [],
        }
        for entry in attribution.get("attributions") or []
        if isinstance(entry, dict)
        and _date_range_end(entry.get("date_range"))
        and (_date_range_end(entry.get("date_range")) or "") < trade_date
        and set(entry.get("reason_codes") or []) & {"missing_broker_position", "reconciliation_not_clean"}
    ]
    current_attribution_issues = [
        {
            "category": entry.get("category"),
            "date_range": entry.get("date_range"),
            "reason_codes": entry.get("reason_codes") or [],
            "supporting_artifacts": entry.get("supporting_artifacts") or [],
        }
        for entry in attribution.get("attributions") or []
        if isinstance(entry, dict)
        and (not _date_range_end(entry.get("date_range")) or (_date_range_end(entry.get("date_range")) or "") >= trade_date)
        and set(entry.get("reason_codes") or []) & {"missing_broker_position", "reconciliation_not_clean"}
    ]
    if payload is None or recon_path is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "date": trade_date,
            "available": False,
            "status": "missing_reconciliation_artifact",
            "decision_grade_impact": "blocks_decision_grade",
            "selected_run_id": None,
            "expected_positions_source": None,
            "broker_positions_source": None,
            "reconciliation_source": None,
            "mismatches": [],
            "alias_resolutions_applied": [],
            "stale_artifact_warnings": stale_warnings,
            "reason_codes": ["reconciliation_artifact_missing"],
            "recommended_action": "Restore or regenerate the current-date posttrade reconciliation artifact before treating operational drag as decision-grade.",
            "source_diagnostics": {"reconciliation": diagnostics},
            "historical_reconciliation_issues": historical_recon_issues,
            "current_attribution_issues": current_attribution_issues,
        }
    assessment = _reconciliation_assessment(payload, repo=repo, source_path=recon_path)
    mismatches = list(assessment.get("mismatches") or [])
    reason_codes = list(assessment.get("reason_codes") or [])
    if mismatches and any(row.get("classification") == "MISSING_BROKER_POSITION" for row in mismatches):
        reason_codes.append("missing_broker_position")
    clean = bool(assessment.get("clean"))
    if clean:
        status = "clean_reconciled"
        impact = "none"
        action = (
            "No broker/model remediation required for the selected current-date reconciliation artifact. "
            "If operational-drag previously showed reconciliation blockers, treat them as attribution/classification false positives from historical artifacts."
        )
    else:
        status = "true_reconciliation_drift"
        impact = "blocks_decision_grade"
        action = "Review the listed mismatches against the selected expected and broker-position sources before the next execution window."
    position_diag = (actual.get("source_diagnostics") or {}).get("positions") or {}
    selected_position_sources = position_diag.get("selected_paths") or []
    return {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "available": True,
        "status": status,
        "decision_grade_impact": impact,
        "selected_run_id": _run_id_from_path(recon_path) or payload.get("run_id"),
        "expected_positions_source": str(recon_path) if assessment.get("expected_positions") else None,
        "broker_positions_source": str(recon_path) if assessment.get("broker_positions") else (selected_position_sources[0] if selected_position_sources else None),
        "reconciliation_source": str(recon_path),
        "mismatches": mismatches,
        "alias_resolutions_applied": assessment.get("alias_resolutions_applied") or [],
        "stale_artifact_warnings": stale_warnings,
        "reason_codes": _dedupe_reasons(reason_codes),
        "recommended_action": action,
        "source_diagnostics": {"reconciliation": diagnostics, "actual_positions": position_diag},
        "historical_reconciliation_issues": historical_recon_issues,
        "current_attribution_issues": current_attribution_issues,
    }


def _aggregate_confidence(entries: list[dict[str, Any]]) -> str:
    levels = [str(entry.get("confidence") or "LOW").upper() for entry in entries]
    if "HIGH" in levels and "LOW" not in levels:
        return "HIGH"
    if "MEDIUM" in levels:
        return "MEDIUM"
    return "LOW"


def build_stable_window_analysis(*, trade_date: str, operational_drag: dict[str, Any]) -> dict[str, Any]:
    rows = [
        row for row in operational_drag.get("timeseries") or []
        if row.get("daily_operational_drag") is not None
    ]
    rows = sorted(rows, key=lambda row: row["date"])
    windows: list[dict[str, Any]] = []
    for name, start in STABLE_WINDOWS:
        if name == "latest_available_clean_window":
            window_rows = _latest_clean_rows(rows)
            requested_start = window_rows[0]["date"] if window_rows else None
        else:
            requested_start = start
            window_rows = [row for row in rows if requested_start and row["date"] >= requested_start]
        windows.append(_window_summary(name=name, requested_start=requested_start, rows=window_rows, exact_start=start))
    available = any(window.get("available") for window in windows)
    return {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "available": available,
        "confidence": "MEDIUM" if available else "LOW",
        "windows": windows,
        "reason_codes": _dedupe_reasons([reason for window in windows for reason in window.get("reason_codes", []) if reason != "ok"]),
        "source_artifacts": operational_drag.get("source_artifacts") or [],
    }


def _latest_clean_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clean = [row for row in rows if row.get("reason_codes") in (["ok"], [], None)]
    if len(clean) >= 2:
        return clean
    return rows[-2:] if len(rows) >= 2 else []


def _window_summary(
    *,
    name: str,
    requested_start: str | None,
    rows: list[dict[str, Any]],
    exact_start: str | None,
) -> dict[str, Any]:
    if len(rows) < 2:
        return {
            "window": name,
            "available": False,
            "requested_start": requested_start,
            "actual_start": rows[0]["date"] if rows else None,
            "end": rows[-1]["date"] if rows else None,
            "confidence": "LOW",
            "missing_data_caveats": ["fewer_than_two_aligned_observations"],
            "reason_codes": ["window_unavailable_fewer_than_two_observations"],
        }
    reasons: list[str] = []
    if exact_start and rows[0]["date"] != exact_start:
        reasons.append("window_start_missing_using_first_available")
    reasons.extend(reason for row in rows for reason in row.get("reason_codes", []) if reason != "ok")
    intended_daily = [_safe_float(row.get("intended_return_daily")) or 0.0 for row in rows[1:]]
    actual_daily = [_safe_float(row.get("actual_return_daily")) or 0.0 for row in rows[1:]]
    spy_daily = [_safe_float(row.get("spy_return_daily")) or 0.0 for row in rows[1:]]
    intended_return = _compound(intended_daily)
    actual_return = _compound(actual_daily)
    spy_return = _compound(spy_daily)
    avg_intended_gross = _mean([_safe_float(row.get("intended_gross_exposure")) for row in rows])
    avg_actual_gross = _mean([_safe_float(row.get("actual_gross_exposure")) for row in rows])
    return {
        "window": name,
        "available": True,
        "requested_start": requested_start,
        "actual_start": rows[0]["date"],
        "end": rows[-1]["date"],
        "observation_count": len(rows),
        "intended_cumulative_return": _round(intended_return),
        "actual_cumulative_return": _round(actual_return),
        "spy_cumulative_return": _round(spy_return),
        "operational_drag": _round(intended_return - actual_return),
        "intended_excess_vs_spy": _round(intended_return - spy_return),
        "actual_excess_vs_spy": _round(actual_return - spy_return),
        "average_intended_gross_exposure": _round(avg_intended_gross),
        "average_actual_gross_exposure": _round(avg_actual_gross),
        "confidence": "MEDIUM" if not reasons else "LOW",
        "missing_data_caveats": reasons,
        "reason_codes": _dedupe_reasons(reasons),
    }


def _compound(values: list[float]) -> float:
    total = 1.0
    for value in values:
        total *= 1.0 + value
    return total - 1.0


def _mean(values: list[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return sum(clean) / len(clean) if clean else None


def render_stable_window_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("current_date_summary") or {}
    health = summary.get("current_date_health") or {}
    lines = [
        f"# Operational Drag Stable-Window Analysis - {payload.get('date')}",
        "",
        f"- Available: {payload.get('available')}",
        f"- Confidence: {payload.get('confidence')}",
    ]
    if summary:
        # FR-061: requested-date health first, historical caveats clearly separated.
        lines.extend(
            [
                f"- Requested date status: {summary.get('current_date_status')}",
                f"- Latest aligned date: {health.get('latest_aligned_date')} (requested {health.get('requested_date')})",
                f"- Decision grade: {summary.get('decision_grade')} — {summary.get('decision_grade_explanation')}",
                f"- Current-date reason codes: {', '.join(summary.get('current_date_reason_codes') or []) or 'none'}",
                f"- Historical caveats: {', '.join(summary.get('historical_reason_codes') or []) or 'none'}",
                f"- Window caveats: {', '.join(summary.get('window_reason_codes') or []) or 'none'}",
            ]
        )
    lines.extend(
        [
            f"- Reason codes (flat): {', '.join(payload.get('reason_codes') or [])}",
            "",
            "| Window | Status | Actual Start | End | Intended | Actual | SPY | Drag | Avg Intended Gross | Avg Actual Gross | Confidence | Caveats |",
            "|---|---|---|---|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for row in payload.get("windows") or []:
        lines.append(
            "| {window} | {status} | {start} | {end} | {intended} | {actual} | {spy} | {drag} | {igross} | {agross} | {confidence} | {caveats} |".format(
                window=row.get("window"),
                status="available" if row.get("available") else "unavailable",
                start=row.get("actual_start") or "n/a",
                end=row.get("end") or "n/a",
                intended=_fmt_pct(row.get("intended_cumulative_return")),
                actual=_fmt_pct(row.get("actual_cumulative_return")),
                spy=_fmt_pct(row.get("spy_cumulative_return")),
                drag=_fmt_pct(row.get("operational_drag")),
                igross=_fmt_pct(row.get("average_intended_gross_exposure")),
                agross=_fmt_pct(row.get("average_actual_gross_exposure")),
                confidence=row.get("confidence"),
                caveats=", ".join(row.get("missing_data_caveats") or row.get("reason_codes") or []),
            )
        )
    return "\n".join(lines)


def _fmt_pct(value: Any) -> str:
    numeric = _safe_float(value)
    if numeric is None:
        return "n/a"
    return f"{numeric * 100:.2f}%"


def _sorted_unique(codes: list[str]) -> list[str]:
    return sorted({str(code) for code in codes if code and code != "ok"})


def classify_operational_drag_reasons(
    *,
    trade_date: str,
    intended: dict[str, Any],
    actual: dict[str, Any],
    benchmark: dict[str, Any],
    drag: dict[str, Any],
    attribution: dict[str, Any],
    windows: dict[str, Any],
) -> dict[str, Any]:
    """FR-061: classify (never delete) operational-drag reason codes into
    current-date / historical / window buckets and a materiality cross-cut, and
    derive a CIO-readable current-date status + decision-grade explanation."""
    # Bucket strictly by the row date a code is emitted on, across every component
    # that has a dated timeseries (intended/actual/benchmark/drag). This prevents a
    # flattened aggregate (e.g. drag.reason_codes, which unions all historical
    # rows) from flooding the current-date bucket with historical missing prices.
    current_set: set[str] = set()
    historical_set: set[str] = set()
    all_row_codes: set[str] = set()
    for component in (intended, actual, benchmark, drag):
        for row in component.get("timeseries") or []:
            rdate = row.get("date")
            codes = [code for code in (row.get("reason_codes") or []) if code and code != "ok"]
            all_row_codes.update(codes)
            if rdate is not None and rdate < trade_date:
                historical_set.update(codes)
            else:
                current_set.update(codes)
    # Series-level run codes not attributable to any dated row describe this run
    # as a whole (e.g. actual_nav_stale, price_history_missing) -> current-date.
    for component in (intended, actual, benchmark, drag):
        for code in component.get("reason_codes") or []:
            if code and code != "ok" and code not in all_row_codes:
                current_set.add(code)

    attribution_row_codes: set[str] = set()
    for entry in attribution.get("attributions") or []:
        if not isinstance(entry, dict):
            continue
        raw_codes = [code for code in (entry.get("reason_codes") or []) if code and code != "ok"]
        attribution_row_codes.update(raw_codes)
        codes = [code for code in raw_codes if code not in all_row_codes]
        end_date = _date_range_end(entry.get("date_range"))
        if end_date and end_date < trade_date:
            historical_set.update(codes)
        else:
            current_set.update(codes)
    # Fallback for legacy attribution payloads without dated entries.
    for code in attribution.get("reason_codes") or []:
        if code and code != "ok" and code not in attribution_row_codes:
            current_set.add(code)

    window_set: set[str] = set()
    for code in windows.get("reason_codes") or []:
        if code and code != "ok":
            window_set.add(code)
    for win in windows.get("windows") or []:
        for code in win.get("reason_codes") or []:
            if code and code != "ok":
                window_set.add(code)

    current = sorted(current_set)
    historical = sorted(historical_set)
    window = sorted(window_set)
    all_codes = _sorted_unique(current + historical + window)
    material = [code for code in all_codes if _is_material_data_reason(code)]
    non_material = [code for code in all_codes if not _is_material_data_reason(code)]
    current_material = [code for code in current if _is_material_data_reason(code)]

    latest = drag.get("latest") if isinstance(drag.get("latest"), dict) else {}
    latest_aligned_date = latest.get("date")
    reaches_requested_date = bool(drag.get("available")) and latest_aligned_date == trade_date
    freshness = build_operational_drag_freshness_diagnostics(
        trade_date=trade_date,
        intended=intended,
        actual=actual,
        benchmark=benchmark,
        drag=drag,
        current_material=current_material,
    )

    if not reaches_requested_date:
        status = "current_date_unavailable"
    elif current_material:
        status = "current_date_available_with_caveats"
    elif historical or window or current:
        status = "current_date_available_with_historical_caveats"
    else:
        status = "current_date_ok"

    decision_grade = reaches_requested_date and not current_material
    if not reaches_requested_date:
        explanation = (
            f"Not decision-grade: the aligned intended/actual/SPY series does not reach {trade_date} "
            f"(latest aligned date {latest_aligned_date})."
        )
    elif current_material:
        explanation = (
            f"Not decision-grade for {trade_date}: material current-date data gaps present "
            f"({', '.join(current_material)})."
        )
    else:
        caveat_note = (
            " Historical/window caveats exist but do not affect the requested date."
            if (historical or window)
            else ""
        )
        explanation = (
            f"Decision-grade for {trade_date}: current-date intended, actual, and SPY data are present "
            f"and aligned.{caveat_note}"
        )

    return {
        "current_date_status": status,
        "decision_grade": decision_grade,
        "decision_grade_explanation": explanation,
        "current_date_reason_codes": current,
        "historical_reason_codes": historical,
        "window_reason_codes": window,
        "material_reason_codes": material,
        "non_material_reason_codes": non_material,
        "current_date_health": {
            "requested_date": trade_date,
            "latest_aligned_date": latest_aligned_date,
            "reaches_requested_date": reaches_requested_date,
            "current_date_material_reason_codes": current_material,
            "stale_components": freshness["stale_components"],
            "blocking_components": freshness["blocking_components"],
            "dependency_diagnostics": freshness["components"],
        },
        "freshness_diagnostics": freshness,
    }


def build_operational_drag_freshness_diagnostics(
    *,
    trade_date: str,
    intended: dict[str, Any],
    actual: dict[str, Any],
    benchmark: dict[str, Any],
    drag: dict[str, Any],
    current_material: list[str],
) -> dict[str, Any]:
    components = {
        "intended": _component_freshness(
            component="intended",
            trade_date=trade_date,
            payload=intended,
            timeseries_key="timeseries",
            source_diag=intended.get("source_diagnostics") or {},
            material_reasons=current_material,
        ),
        "actual": _component_freshness(
            component="actual",
            trade_date=trade_date,
            payload=actual,
            timeseries_key="timeseries",
            source_diag=actual.get("source_diagnostics") or {},
            material_reasons=current_material,
        ),
        "benchmark": _component_freshness(
            component="benchmark",
            trade_date=trade_date,
            payload=benchmark,
            timeseries_key="timeseries",
            source_diag=benchmark.get("source_diagnostics") or {},
            material_reasons=current_material,
        ),
        "operational_drag": _component_freshness(
            component="operational_drag",
            trade_date=trade_date,
            payload=drag,
            timeseries_key="timeseries",
            source_diag=drag.get("source_diagnostics") or {},
            material_reasons=current_material,
        ),
    }
    stale_components = sorted(
        name for name, row in components.items()
        if row.get("freshness_status") in {"STALE", "MISSING", "UNAVAILABLE"}
    )
    blocking_components = sorted(name for name, row in components.items() if row.get("blocks_decision_grade"))
    return {
        "schema_version": "operational_drag_freshness.v1",
        "requested_date": trade_date,
        "latest_aligned_date": ((drag.get("latest") or {}) if isinstance(drag.get("latest"), dict) else {}).get("date"),
        "decision_grade_ready": not blocking_components,
        "stale_components": stale_components,
        "blocking_components": blocking_components,
        "components": components,
    }


def _component_freshness(
    *,
    component: str,
    trade_date: str,
    payload: dict[str, Any],
    timeseries_key: str,
    source_diag: dict[str, Any],
    material_reasons: list[str],
) -> dict[str, Any]:
    rows = payload.get(timeseries_key) if isinstance(payload.get(timeseries_key), list) else []
    row_dates = sorted(str(row.get("date")) for row in rows if isinstance(row, dict) and _is_date(row.get("date")))
    latest_date = row_dates[-1] if row_dates else None
    selected_paths = _selected_paths_from_diagnostics(source_diag)
    failed_paths = _failed_paths_from_diagnostics(source_diag)
    component_reasons = [
        str(reason)
        for reason in (payload.get("reason_codes") or [])
        if reason and reason != "ok"
    ]
    blocking_reasons = sorted(
        {
            reason for reason in list(material_reasons or [])
            if _reason_blocks_component(component, reason)
        }
    )
    if not bool(payload.get("available")):
        freshness_status = "UNAVAILABLE"
    elif latest_date is None:
        freshness_status = "MISSING"
    elif latest_date < trade_date:
        freshness_status = "STALE"
    else:
        freshness_status = "CURRENT"
    missing_dependency_reason = None
    if freshness_status in {"UNAVAILABLE", "MISSING", "STALE"}:
        missing_dependency_reason = blocking_reasons[0] if blocking_reasons else f"{component}_does_not_reach_requested_date"
    return {
        "component": component,
        "requested_date": trade_date,
        "source_date": latest_date,
        "freshness_status": freshness_status,
        "source_paths": selected_paths,
        "failed_paths": failed_paths,
        "reason_codes": _dedupe_reasons(component_reasons),
        "missing_dependency_reason": missing_dependency_reason,
        "blocking_component": component if missing_dependency_reason or blocking_reasons else None,
        "blocks_decision_grade": bool(freshness_status != "CURRENT" or blocking_reasons),
        "blocking_reason_codes": blocking_reasons,
    }


def _selected_paths_from_diagnostics(diagnostics: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if not isinstance(diagnostics, dict):
        return out
    for key, value in diagnostics.items():
        if key == "selected_paths" and isinstance(value, list):
            out.extend(str(path) for path in value if path)
        elif isinstance(value, dict):
            out.extend(_selected_paths_from_diagnostics(value))
    return _dedupe_list(out)


def _failed_paths_from_diagnostics(diagnostics: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not isinstance(diagnostics, dict):
        return out
    for key, value in diagnostics.items():
        if key == "failed_paths" and isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    out.append({"path": str(item.get("path") or ""), "reason": str(item.get("reason") or "unknown")})
        elif isinstance(value, dict):
            out.extend(_failed_paths_from_diagnostics(value))
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in out:
        key = (item["path"], item["reason"])
        if key not in seen:
            deduped.append(item)
            seen.add(key)
    return deduped


def _reason_blocks_component(component: str, reason: str) -> bool:
    text = str(reason).lower()
    if not _is_material_data_reason(text):
        return False
    if component == "actual":
        return "actual" in text or "broker" in text or "reconciliation" in text or "position" in text
    if component == "intended":
        return "intended" in text or "plan" in text or "price" in text or "target" in text
    if component == "benchmark":
        return "spy" in text or "benchmark" in text or "price" in text
    if component == "operational_drag":
        return True
    return False


def build_operational_drag_analysis(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
    write: bool = True,
) -> dict[str, Any]:
    if not _is_date(trade_date):
        raise ValueError(f"trade_date must be YYYY-MM-DD, got {trade_date!r}")
    repo = Path(repo_root)
    price_store = load_price_store(repo, trade_date=trade_date)
    actual = build_actual_nav(trade_date=trade_date, repo_root=repo)
    actual_dates = [row["date"] for row in actual.get("timeseries") or []]
    benchmark_seed = build_benchmark_nav(trade_date=trade_date, repo_root=repo, price_store=price_store)
    benchmark_dates = [row["date"] for row in benchmark_seed.get("timeseries") or []]
    intended = build_intended_nav(
        trade_date=trade_date,
        repo_root=repo,
        price_store=price_store,
        date_axis=_sort_dates(set(actual_dates) | set(benchmark_dates)),
    )
    intended_dates = [row["date"] for row in intended.get("timeseries") or []]
    aligned_benchmark_dates = _sort_dates(set(actual_dates) | set(intended_dates))
    benchmark = build_benchmark_nav(
        trade_date=trade_date,
        repo_root=repo,
        aligned_dates=aligned_benchmark_dates,
        price_store=price_store,
    )
    drag = build_operational_drag(
        trade_date=trade_date,
        intended=intended,
        actual=actual,
        benchmark=benchmark,
    )
    attribution = build_operational_drag_attribution(
        trade_date=trade_date,
        repo_root=repo,
        operational_drag=drag,
        intended=intended,
        actual=actual,
    )
    reconciliation_diagnostic = build_reconciliation_drift_diagnostic(
        trade_date=trade_date,
        repo_root=repo,
        actual=actual,
        attribution=attribution,
    )
    current_execution_pointer = repo / "outputs" / "workflow" / trade_date / "execution.json"
    if (
        current_execution_pointer.exists()
        and reconciliation_diagnostic.get("decision_grade_impact") == "blocks_decision_grade"
    ):
        diagnostic_reasons = [
            str(reason)
            for reason in reconciliation_diagnostic.get("reason_codes") or []
            if reason and reason != "ok"
        ] or ["reconciliation_not_clean"]
        attribution_entries = list(attribution.get("attributions") or [])
        attribution_entries.append(
            {
                "category": "reconciliation_health",
                "date_range": trade_date,
                "estimated_drag_bps": None,
                "supporting_artifacts": [
                    path
                    for path in [reconciliation_diagnostic.get("reconciliation_source")]
                    if path
                ],
                "confidence": "HIGH",
                "explanation": "The current run does not have clean, authoritative reconciliation evidence.",
                "reason_codes": diagnostic_reasons,
            }
        )
        attribution["attributions"] = attribution_entries
        attribution["reason_codes"] = _dedupe_reasons(
            [
                reason
                for entry in attribution_entries
                for reason in entry.get("reason_codes", [])
            ]
        )
        attribution["confidence"] = _aggregate_confidence(attribution_entries)
    windows = build_stable_window_analysis(trade_date=trade_date, operational_drag=drag)
    # FR-061: classify (never delete) reason codes into current-date / historical /
    # window / materiality buckets so a clean requested date is not obscured by
    # historical or non-trading-day missing-price noise.
    classification = classify_operational_drag_reasons(
        trade_date=trade_date,
        intended=intended,
        actual=actual,
        benchmark=benchmark,
        drag=drag,
        attribution=attribution,
        windows=windows,
    )
    drag.update(classification)
    windows["current_date_summary"] = classification
    out_dir = (Path(output_root) if output_root is not None else repo / "outputs" / "operational_drag") / trade_date
    artifact_paths = {
        "intended_nav": str(out_dir / "intended_nav.json"),
        "intended_nav_timeseries": str(out_dir / "intended_nav_timeseries.csv"),
        "actual_nav": str(out_dir / "actual_nav.json"),
        "actual_nav_timeseries": str(out_dir / "actual_nav_timeseries.csv"),
        "benchmark_nav": str(out_dir / "benchmark_nav.json"),
        "operational_drag": str(out_dir / "operational_drag.json"),
        "operational_drag_timeseries": str(out_dir / "operational_drag_timeseries.csv"),
        "operational_drag_attribution": str(out_dir / "operational_drag_attribution.json"),
        "reconciliation_drift_diagnostic": str(out_dir / "reconciliation_drift_diagnostic.json"),
        "stable_window_analysis": str(out_dir / "stable_window_analysis.json"),
        "stable_window_analysis_md": str(out_dir / "stable_window_analysis.md"),
    }
    if write:
        _write_json(out_dir / "intended_nav.json", intended)
        _write_csv(out_dir / "intended_nav_timeseries.csv", intended.get("timeseries") or [], _intended_csv_fields())
        _write_json(out_dir / "actual_nav.json", actual)
        _write_csv(out_dir / "actual_nav_timeseries.csv", actual.get("timeseries") or [], _actual_csv_fields())
        _write_json(out_dir / "benchmark_nav.json", benchmark)
        _write_json(out_dir / "operational_drag.json", drag)
        _write_csv(out_dir / "operational_drag_timeseries.csv", drag.get("timeseries") or [], _drag_csv_fields())
        _write_json(out_dir / "operational_drag_attribution.json", attribution)
        _write_json(out_dir / "reconciliation_drift_diagnostic.json", reconciliation_diagnostic)
        _write_json(out_dir / "stable_window_analysis.json", windows)
        _write_text(out_dir / "stable_window_analysis.md", render_stable_window_markdown(windows))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "generated_at": f"{trade_date}T00:00:00Z",
        "available": bool(drag.get("available")),
        "confidence": drag.get("confidence", "LOW"),
        "intended_nav": intended,
        "actual_nav": actual,
        "benchmark_nav": benchmark,
        "operational_drag": drag,
        "operational_drag_attribution": attribution,
        "reconciliation_drift_diagnostic": reconciliation_diagnostic,
        "stable_window_analysis": windows,
        "artifact_paths": artifact_paths,
        "source_diagnostics": {
            "price": price_store.diagnostics(),
            "intended": intended.get("source_diagnostics") or {},
            "actual": actual.get("source_diagnostics") or {},
            "benchmark": benchmark.get("source_diagnostics") or {},
        },
        "reason_codes": _dedupe_reasons(
            [reason for section in (intended, actual, benchmark, drag, attribution, windows) for reason in section.get("reason_codes", []) if reason != "ok"]
        ),
        # FR-061: classified, CIO-readable view (flat reason_codes above kept for
        # backward compatibility; nothing is dropped, only bucketed).
        "current_date_status": classification["current_date_status"],
        "decision_grade": classification["decision_grade"],
        "decision_grade_explanation": classification["decision_grade_explanation"],
        "current_date_reason_codes": classification["current_date_reason_codes"],
        "historical_reason_codes": classification["historical_reason_codes"],
        "window_reason_codes": classification["window_reason_codes"],
        "material_reason_codes": classification["material_reason_codes"],
        "non_material_reason_codes": classification["non_material_reason_codes"],
        "current_date_health": classification["current_date_health"],
        "freshness_diagnostics": classification["freshness_diagnostics"],
    }
    return payload


def _intended_csv_fields() -> list[str]:
    return [
        "date",
        "strategy_id",
        "intended_equity_value",
        "intended_cash",
        "intended_gross_exposure",
        "intended_positions",
        "intended_return_daily",
        "intended_return_cumulative",
        "price_source",
        "plan_source",
        "missing_symbols",
        "reason_codes",
    ]


def _actual_csv_fields() -> list[str]:
    return [
        "date",
        "actual_equity_value",
        "actual_cash",
        "actual_gross_exposure",
        "actual_positions",
        "actual_return_daily",
        "actual_return_cumulative",
        "broker_source",
        "reconciliation_source",
        "reason_codes",
    ]


def _drag_csv_fields() -> list[str]:
    return [
        "date",
        "intended_return_daily",
        "actual_return_daily",
        "spy_return_daily",
        "daily_operational_drag",
        "cumulative_operational_drag",
        "intended_vs_spy_excess",
        "actual_vs_spy_excess",
        "actual_underdeployment",
        "intended_gross_exposure",
        "actual_gross_exposure",
        "exposure_gap",
        "reason_codes",
    ]


def load_latest_operational_drag_summary(
    *,
    outputs_root: Path | str = Path("outputs"),
    trade_date: str | None = None,
) -> dict[str, Any]:
    outputs = Path(outputs_root)
    root = outputs / "operational_drag"
    if trade_date:
        candidates = [root / trade_date]
    else:
        candidates = sorted([path for path in root.glob("*") if path.is_dir() and _is_date(path.name)])
    if not candidates:
        return {
            "status": "NO_OPERATIONAL_DRAG_DATA",
            "tool": "operational_drag_analysis",
            "trade_date": trade_date,
            "reason_codes": ["missing_operational_drag_artifacts"],
            "warnings": ["No outputs/operational_drag/<date>/ artifacts found."],
        }
    selected = candidates[-1]
    drag = _read_json(selected / "operational_drag.json") or {}
    windows = _read_json(selected / "stable_window_analysis.json") or {}
    attribution = _read_json(selected / "operational_drag_attribution.json") or {}
    actual = _read_json(selected / "actual_nav.json") or {}
    intended = _read_json(selected / "intended_nav.json") or {}
    benchmark = _read_json(selected / "benchmark_nav.json") or {}
    return {
        "status": "OK" if drag else "NO_OPERATIONAL_DRAG_DATA",
        "tool": "operational_drag_analysis",
        "trade_date": selected.name,
        "available": bool(drag.get("available")),
        "confidence": drag.get("confidence") or "LOW",
        "latest_operational_drag": drag.get("latest") or {},
        "stable_windows": windows.get("windows") or [],
        "main_drag_contributors": attribution.get("attributions") or [],
        "data_coverage": {
            "intended_available": bool(intended.get("available")),
            "actual_available": bool(actual.get("available")),
            "benchmark_available": bool(benchmark.get("available")),
            "aligned_observations": len(drag.get("timeseries") or []),
        },
        "missing_artifact_warnings": [
            reason
            for payload in (intended, actual, benchmark, drag, windows)
            for reason in payload.get("reason_codes", [])
            if "missing" in str(reason) or "unavailable" in str(reason)
        ],
        "reason_codes": _dedupe_reasons(drag.get("reason_codes") or []),
        "current_date_status": drag.get("current_date_status"),
        "decision_grade": drag.get("decision_grade"),
        "freshness_diagnostics": drag.get("freshness_diagnostics") or {},
        "source_artifacts": [
            str(selected / name)
            for name in (
                "intended_nav.json",
                "actual_nav.json",
                "benchmark_nav.json",
                "operational_drag.json",
                "operational_drag_attribution.json",
                "stable_window_analysis.json",
            )
            if (selected / name).exists()
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build read-only intended-vs-actual operational drag artifacts.")
    parser.add_argument("--date", required=True, help="Trade date to analyze, YYYY-MM-DD.")
    parser.add_argument("--repo-root", default=".", help="Repository root. Defaults to current directory.")
    parser.add_argument("--output-root", help="Optional output root; defaults to outputs/operational_drag.")
    parser.add_argument("--no-write", action="store_true", help="Build in memory without writing artifacts.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_operational_drag_analysis(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
        write=not args.no_write,
    )
    diagnostics = payload.get("source_diagnostics") or {}
    selected_sources = {
        "price": (diagnostics.get("price") or {}).get("selected_paths") or [],
        "actual_nav": ((diagnostics.get("actual") or {}).get("nav") or {}).get("selected_paths") or [],
        "actual_positions": ((diagnostics.get("actual") or {}).get("positions") or {}).get("selected_paths") or [],
        "benchmark": (((diagnostics.get("benchmark") or {}).get("benchmark") or {}).get("selected_paths")) or [],
    }
    candidate_counts = {
        "price": len((diagnostics.get("price") or {}).get("candidate_paths") or []),
        "actual_nav": len(((diagnostics.get("actual") or {}).get("nav") or {}).get("candidate_paths") or []),
        "actual_positions": len(((diagnostics.get("actual") or {}).get("positions") or {}).get("candidate_paths") or []),
        "benchmark": len((((diagnostics.get("benchmark") or {}).get("benchmark") or {}).get("candidate_paths")) or []),
    }
    print(json.dumps({
        "date": payload["date"],
        "available": payload["available"],
        "confidence": payload["confidence"],
        "reason_codes": payload["reason_codes"],
        "artifact_paths": payload["artifact_paths"],
        "selected_sources": selected_sources,
        "source_candidate_counts": candidate_counts,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
