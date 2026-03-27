from __future__ import annotations

import json
import logging
import csv
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BENCHMARK_PATH = Path("outputs/benchmark/benchmark_vs_spy.json")
BROKER_SNAPSHOT_PATH = Path("outputs/broker/broker_snapshot_latest.json")


def _log(msg: str) -> None:
    logger.info("[BENCHMARK] %s", msg)


def _warn(msg: str) -> None:
    logger.warning("[BENCHMARK][WARN] %s", msg)


def _coerce_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def load_benchmark_with_meta(
    path: Path = BENCHMARK_PATH,
) -> tuple[list[dict[str, Any]], str | None]:
    """Return (records, inception_date).

    Handles both old format (plain list) and new format
    (object with 'records' and 'inception_date' keys).
    Returns ([], None) when the file is missing or unreadable.
    """
    if not path.exists():
        return [], None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data, None
        if isinstance(data, dict):
            records = data.get("records", [])
            inception_date = data.get("inception_date") or None
            return records, inception_date
    except Exception:
        _warn(f"could not parse {path}, starting fresh")
    return [], None


def load_existing_benchmark(path: Path = BENCHMARK_PATH) -> list[dict[str, Any]]:
    """Return only the records list (backward-compatible helper)."""
    records, _ = load_benchmark_with_meta(path)
    return records


def _load_spy_close_history(workspace_root: Path) -> dict[str, float]:
    path = workspace_root / "outputs" / "perf" / "benchmark_close_history.csv"
    if not path.exists():
        return {}

    out: dict[str, float] = {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                trade_date = str(row.get("date") or "").strip()
                spy_close = _coerce_float(row.get("spy_close"))
                if trade_date and spy_close is not None:
                    out[trade_date] = spy_close
    except Exception as exc:
        _warn(f"could not read benchmark_close_history.csv: {exc}")
    return out


def _portfolio_value_from_summary(summary: dict[str, Any]) -> float | None:
    benchmark = summary.get("benchmark") if isinstance(summary.get("benchmark"), dict) else {}
    broker_context = (
        summary.get("broker_context") if isinstance(summary.get("broker_context"), dict) else {}
    )
    portfolio_state = (
        summary.get("portfolio_state") if isinstance(summary.get("portfolio_state"), dict) else {}
    )

    for candidate in (
        broker_context.get("broker_preflight_equity"),
        broker_context.get("broker_equity_at_planning"),
        benchmark.get("portfolio_value"),
    ):
        value = _coerce_float(candidate)
        if value is not None and value > 0:
            return value

    cash_after = _coerce_float(portfolio_state.get("cash_after"))
    market_value = _coerce_float(portfolio_state.get("portfolio_market_value"))
    if cash_after is not None and market_value is not None:
        return cash_after + market_value
    return None


def _recover_benchmark_records_from_run_summaries(workspace_root: Path) -> list[dict[str, Any]]:
    runs_root = workspace_root / "outputs" / "runs"
    if not runs_root.exists():
        return []

    spy_history = _load_spy_close_history(workspace_root)
    by_date: dict[str, tuple[str, dict[str, Any]]] = {}

    for summary_path in sorted(runs_root.glob("*/trading_day_summary.json")):
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        trade_date = str(summary.get("trade_date") or "").strip()
        if not trade_date:
            continue

        portfolio_value = _portfolio_value_from_summary(summary)
        benchmark = summary.get("benchmark") if isinstance(summary.get("benchmark"), dict) else {}
        spy_price = _coerce_float(benchmark.get("spy_value"))
        if spy_price is None:
            spy_price = spy_history.get(trade_date)

        if portfolio_value is None or spy_price is None:
            continue

        generated_at = str(summary.get("generated_at") or "")
        recovered = {
            "date": trade_date,
            "portfolio_value": round(portfolio_value, 2),
            "portfolio_return_daily": 0.0,
            "portfolio_return_cum": 0.0,
            "spy_price": round(spy_price, 4),
            "spy_return_daily": 0.0,
            "spy_return_cum": 0.0,
            "excess_return_daily": 0.0,
            "excess_return_cum": 0.0,
        }
        current = by_date.get(trade_date)
        if current is None or generated_at >= current[0]:
            by_date[trade_date] = (generated_at, recovered)

    records = [payload for _, payload in sorted(by_date.values(), key=lambda item: item[1]["date"])]
    return _recompute_returns(records)


def _merge_benchmark_records(
    existing_records: list[dict[str, Any]],
    recovered_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for record in recovered_records:
        trade_date = str(record.get("date") or "").strip()
        if trade_date:
            merged[trade_date] = dict(record)
    for record in existing_records:
        trade_date = str(record.get("date") or "").strip()
        if trade_date:
            merged[trade_date] = dict(record)
    ordered = [merged[key] for key in sorted(merged)]
    return _recompute_returns(ordered)


def resolve_broker_snapshot_path(
    *,
    broker_snapshot_path: Path = BROKER_SNAPSHOT_PATH,
    run_root: Path | str | None = None,
) -> Path | None:
    candidates: list[Path] = []

    if broker_snapshot_path:
        candidates.append(Path(broker_snapshot_path))

    if run_root:
        broker_dir = Path(run_root) / "broker"
        candidates.extend(
            [
                broker_dir / "posttrade_account_snapshot.json",
                broker_dir / "pretrade_account_snapshot.json",
            ]
        )

    for candidate in candidates:
        if candidate.exists():
            return candidate

    if candidates:
        _warn(
            "missing broker snapshot"
            + f" (checked: {', '.join(str(path) for path in candidates)})"
        )
    else:
        _warn("missing broker snapshot")
    return None


def read_broker_equity(path: Path = BROKER_SNAPSHOT_PATH) -> float | None:
    if not path.exists():
        _warn("missing broker snapshot")
        return None
    try:
        snap = json.loads(path.read_text(encoding="utf-8"))
        equity = snap.get("equity") or snap.get("portfolio_value")
        if equity is None:
            account = snap.get("account") or {}
            equity = account.get("equity") or account.get("portfolio_value")
        if equity is not None:
            return float(equity)
        _warn("broker snapshot has no equity field")
    except Exception as exc:
        _warn(f"failed to read broker snapshot: {exc}")
    return None


def fetch_spy_close(trade_date: str) -> float | None:
    try:
        import yfinance as yf

        ticker = yf.Ticker("SPY")
        hist = ticker.history(period="10d", interval="1d")
        if hist.empty:
            _warn("yfinance returned empty SPY history")
            return None
        hist.index = hist.index.tz_localize(None) if hist.index.tz is None else hist.index.tz_convert(None)
        hist.index = hist.index.normalize()
        target = hist.index[hist.index <= trade_date]
        if target.empty:
            _warn(f"no SPY data on or before {trade_date}")
            return None
        return float(hist.loc[target[-1], "Close"])
    except Exception as exc:
        _warn(f"failed to fetch SPY: {exc}")
        return None


def _recompute_returns(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recompute all return fields from raw values. Ensures determinism."""
    if not records:
        return records

    base_pv = records[0]["portfolio_value"]
    base_spy = records[0]["spy_price"]

    for i, rec in enumerate(records):
        pv = rec["portfolio_value"]
        spy = rec["spy_price"]

        if i == 0:
            rec["portfolio_return_daily"] = 0.0
            rec["spy_return_daily"] = 0.0
        else:
            prev_pv = records[i - 1]["portfolio_value"]
            prev_spy = records[i - 1]["spy_price"]
            rec["portfolio_return_daily"] = round((pv / prev_pv - 1) if prev_pv else 0.0, 8)
            rec["spy_return_daily"] = round((spy / prev_spy - 1) if prev_spy else 0.0, 8)

        rec["portfolio_return_cum"] = round((pv / base_pv - 1) if base_pv else 0.0, 8)
        rec["spy_return_cum"] = round((spy / base_spy - 1) if base_spy else 0.0, 8)
        rec["excess_return_daily"] = round(rec["portfolio_return_daily"] - rec["spy_return_daily"], 8)
        rec["excess_return_cum"] = round(rec["portfolio_return_cum"] - rec["spy_return_cum"], 8)

    return records


def write_benchmark_artifact(
    records: list[dict[str, Any]],
    path: Path = BENCHMARK_PATH,
    inception_date: str | None = None,
) -> None:
    """Write benchmark file in the new object format with inception_date metadata."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "inception_date": inception_date,
        "records": records,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def update_benchmark_vs_spy(
    *,
    trade_date: str | None = None,
    broker_snapshot_path: Path = BROKER_SNAPSHOT_PATH,
    benchmark_path: Path = BENCHMARK_PATH,
    run_root: Path | str | None = None,
    workspace_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Append today's benchmark record and recompute all returns.

    Returns the new record on success, None if skipped.
    Non-blocking: all failures are logged as warnings.
    """
    import datetime as dt

    if not trade_date:
        trade_date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

    records, existing_inception = load_benchmark_with_meta(benchmark_path)
    workspace = Path(workspace_root) if workspace_root is not None else Path.cwd()
    recovered_records = _recover_benchmark_records_from_run_summaries(workspace)
    merged_records = _merge_benchmark_records(records, recovered_records)
    recovered_history = merged_records != records
    if recovered_history:
        records = merged_records
        _log(
            f"recovered {len(recovered_records)} benchmark records from trading_day_summary artifacts"
        )

    # Idempotent: skip if today already recorded
    existing_dates = {r["date"] for r in records}
    if trade_date in existing_dates:
        should_persist_repair = recovered_history or (benchmark_path.exists() and existing_inception is None)
        if should_persist_repair:
            inception_date = existing_inception or (records[0]["date"] if records else None)
            write_benchmark_artifact(records, benchmark_path, inception_date=inception_date)
        _log(f"already recorded for {trade_date}, skipping")
        return None

    resolved_snapshot = resolve_broker_snapshot_path(
        broker_snapshot_path=broker_snapshot_path,
        run_root=run_root,
    )
    if resolved_snapshot is None:
        return None

    equity = read_broker_equity(resolved_snapshot)
    if equity is None:
        return None

    spy_price = fetch_spy_close(trade_date)
    if spy_price is None:
        return None

    new_record: dict[str, Any] = {
        "date": trade_date,
        "portfolio_value": round(equity, 2),
        "portfolio_return_daily": 0.0,
        "portfolio_return_cum": 0.0,
        "spy_price": round(spy_price, 4),
        "spy_return_daily": 0.0,
        "spy_return_cum": 0.0,
        "excess_return_daily": 0.0,
        "excess_return_cum": 0.0,
    }
    records.append(new_record)
    records.sort(key=lambda r: r["date"])
    records = _recompute_returns(records)

    # Set inception_date once on first write; never overwrite on subsequent updates
    if existing_inception:
        inception_date = existing_inception
    else:
        inception_date = records[0]["date"]

    write_benchmark_artifact(records, benchmark_path, inception_date=inception_date)
    _log(f"updated for {trade_date}")
    return new_record
