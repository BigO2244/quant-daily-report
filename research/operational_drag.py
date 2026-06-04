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

BENCHMARK_CSV_CANDIDATES = (
    "outputs/perf/live_overlay_benchmark_close_history.csv",
    "outputs/perf/benchmark_close_history.csv",
)


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

    def add(self, symbol: str, date: str, close: float, source: Path) -> None:
        symbol = symbol.upper().strip()
        if not symbol or not _is_date(date):
            return
        self.prices.setdefault(symbol, {})[date] = close
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
        "no_aligned",
        "fewer_than_two",
        "reconciliation_not_clean",
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
    _load_parquet_price_panel(repo, store)
    _load_operational_drag_price_hydration(repo, store, trade_date=trade_date)
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
    return sorted(snapshots, key=lambda item: (item.date, item.plan_source))


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

    for date in ordered_dates:
        latest_plan = _latest_plan_for_date(snapshots, date)
        row_reasons: list[str] = []
        if latest_plan is None:
            rows.append(_missing_intended_row(date, ["missing_plan_for_date"]))
            continue
        if active_plan is None or latest_plan.date != active_plan.date:
            active_plan = latest_plan
            rebalance_equity = previous_equity or latest_plan.equity
            if rebalance_equity is None:
                rebalance_equity = _notional_from_plan(latest_plan)
                row_reasons.append("intended_base_equity_inferred_from_plan_notional")
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
    reasons = _dedupe_reasons(
        [reason for row in rows for reason in row.get("reason_codes", []) if reason != "ok"]
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
    rows, nav_diagnostics = _actual_rows_from_nav_series(repo, trade_date)
    snapshot_diagnostics = _new_diagnostics()
    reasons: list[str] = []
    if not rows:
        snapshot_row, snapshot_diagnostics = _actual_row_from_snapshot(repo, trade_date)
        if snapshot_row:
            rows = [snapshot_row]
            reasons.append("actual_nav_series_missing_using_broker_snapshot")
        else:
            return {
                "schema_version": SCHEMA_VERSION,
                "date": trade_date,
                "available": False,
                "confidence": "LOW",
                "actual_positions": [],
                "timeseries": [],
                "reason_codes": ["missing_actual_nav"],
                "source_artifacts": [],
                "source_diagnostics": {
                    "nav": nav_diagnostics,
                    "snapshot": snapshot_diagnostics,
                    "positions": _new_diagnostics(),
                },
            }
    rows = _compute_return_fields(rows, value_key="actual_equity_value", daily_key="actual_return_daily", cumulative_key="actual_return_cumulative")
    latest = rows[-1]
    positions, position_sources, position_reasons, position_diagnostics = _actual_positions_for_date(repo, trade_date)
    if positions:
        latest["actual_positions"] = positions
    reasons.extend(position_reasons)
    source_artifacts = sorted({source for row in rows for source in row.get("source_artifacts", [])} | set(position_sources))
    reasons.extend(reason for row in rows for reason in row.get("reason_codes", []) if reason != "ok")
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
        "broker_source": latest.get("broker_source"),
        "reconciliation_source": latest.get("reconciliation_source"),
        "reason_codes": _dedupe_reasons(reasons),
        "timeseries": rows,
        "source_artifacts": source_artifacts,
        "source_diagnostics": {
            "nav": nav_diagnostics,
            "snapshot": snapshot_diagnostics,
            "positions": position_diagnostics,
        },
    }


def _actual_rows_from_nav_series(repo: Path, trade_date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    diagnostics = _new_diagnostics()
    by_date: dict[str, dict[str, Any]] = {}
    candidates = [repo / rel for rel in ACTUAL_NAV_CSV_CANDIDATES]
    candidates.extend(_run_scoped_paths(repo, "snapshots/nav_timeseries.csv"))
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
            date = _date_text(raw.get("date") or raw.get("as_of_date"))
            if not date or date > trade_date or date in by_date:
                continue
            equity = _safe_float(raw.get("equity") or raw.get("portfolio_value") or raw.get("nav"))
            if equity is None:
                continue
            cash = _safe_float(raw.get("cash") or raw.get("cash_value"))
            gross = _safe_float(raw.get("gross_exposure") or raw.get("gross") or raw.get("exposure"))
            by_date[date] = {
                "date": date,
                "actual_equity_value": _round(equity, 6),
                "actual_cash": _round(cash, 6),
                "actual_gross_exposure": _round(gross, 10),
                "actual_positions": [],
                "broker_source": str(path),
                "reconciliation_source": None,
                "reason_codes": ["ok"],
                "source_artifacts": [str(path)],
            }
            loaded += 1
        if loaded > 0:
            _diag_add_selected(diagnostics, path)
        else:
            _diag_add_failure(diagnostics, path, "no_valid_nav_rows")
    return [by_date[date] for date in sorted(by_date)], diagnostics


def _actual_row_from_snapshot(repo: Path, trade_date: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    diagnostics = _new_diagnostics()
    candidates = _actual_json_candidates(repo, trade_date)
    for path in candidates:
        _diag_add_candidate(diagnostics, path)
        payload = _read_json(path)
        if payload is None:
            _diag_add_failure(diagnostics, path, "missing_or_unreadable")
            continue
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
        if "recon_posttrade_" in path.name:
            row_reasons.append("actual_from_posttrade_reconciliation")
            if not _reconciliation_clean(payload):
                row_reasons.append("reconciliation_not_clean")
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
    runs_root = repo / "outputs" / "runs"
    if not runs_root.exists():
        return []
    return sorted(runs_root.glob(f"*/{suffix}"), reverse=True)


def _actual_json_candidates(repo: Path, trade_date: str) -> list[Path]:
    candidates: list[Path] = []
    candidates.extend(_run_scoped_paths(repo, f"broker/recon_posttrade_{trade_date}.json"))
    candidates.append(repo / "outputs" / "broker" / f"recon_posttrade_{trade_date}.json")
    candidates.extend(_run_scoped_paths(repo, "broker/posttrade_positions.json"))
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
    return _date_text(payload.get("trade_date") or payload.get("date") or payload.get("as_of") or payload.get("captured_at"))


def _actual_equity_from_payload(payload: dict[str, Any]) -> float | None:
    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    return _safe_float(
        payload.get("broker_equity")
        or payload.get("portfolio_value")
        or payload.get("equity")
        or account.get("equity")
        or account.get("portfolio_value")
    )


def _actual_cash_from_payload(payload: dict[str, Any]) -> float | None:
    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    return _safe_float(payload.get("broker_cash") or payload.get("cash") or account.get("cash"))


def _reconciliation_clean(payload: dict[str, Any]) -> bool:
    verdict = str(payload.get("verdict") or payload.get("status") or "").upper()
    drift_status = str(payload.get("drift_status") or payload.get("comparison_status") or "").upper()
    verdict_clean = verdict in {"", "PASS", "OK", "CLEAN"}
    drift_clean = not drift_status or "OK" in drift_status or "RECONCILED" in drift_status
    return verdict_clean and drift_clean


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
            if _reconciliation_clean(payload):
                reasons.append("actual_positions_from_reconciled_posttrade")
            else:
                reasons.append("reconciliation_not_clean")
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


def _attribution_from_plan_payloads(repo: Path, trade_date: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
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
            entries.append(_incident_entry("buy_suppression", date, path, "Planned buys existed but no orders were submitted.", ["planned_buys_without_submissions"]))
        elif eligible > 0 and 0 <= accepted < eligible:
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
    for path in sorted(recon_root.glob("recon_posttrade_*.json")) if recon_root.exists() else []:
        date = path.stem.replace("recon_posttrade_", "")
        if not _is_date(date) or date > trade_date:
            continue
        payload = _read_json(path)
        if payload is None:
            continue
        classifications = [
            str(row.get("classification"))
            for row in payload.get("share_deltas", [])
            if isinstance(row, dict)
        ]
        if "MISSING_BROKER_POSITION" in classifications:
            entries.append(_incident_entry("missing_broker_position", date, path, "Reconciliation found expected holdings missing at the broker.", ["missing_broker_position"]))
        verdict = str(payload.get("verdict") or "").upper()
        drift_status = str(payload.get("drift_status") or payload.get("comparison_status") or "").upper()
        if verdict in {"WARN", "FAIL"} or (drift_status and "OK" not in drift_status):
            entries.append(_incident_entry("reconciliation_mismatch", date, path, "Reconciliation reported drift or a non-pass verdict.", ["reconciliation_not_clean"]))
    return entries


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
    lines = [
        f"# Operational Drag Stable-Window Analysis - {payload.get('date')}",
        "",
        f"- Available: {payload.get('available')}",
        f"- Confidence: {payload.get('confidence')}",
        f"- Reason codes: {', '.join(payload.get('reason_codes') or [])}",
        "",
        "| Window | Status | Actual Start | End | Intended | Actual | SPY | Drag | Avg Intended Gross | Avg Actual Gross | Confidence | Caveats |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
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
    windows = build_stable_window_analysis(trade_date=trade_date, operational_drag=drag)
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
