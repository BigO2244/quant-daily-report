from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BENCHMARK_PATH = Path("outputs/benchmark/benchmark_vs_spy.json")
BROKER_SNAPSHOT_PATH = Path("outputs/broker/broker_snapshot_latest.json")


def _log(msg: str) -> None:
    logger.info("[BENCHMARK] %s", msg)


def _warn(msg: str) -> None:
    logger.warning("[BENCHMARK][WARN] %s", msg)


def load_existing_benchmark(path: Path = BENCHMARK_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        _warn(f"could not parse {path}, starting fresh")
    return []


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


def write_benchmark_artifact(records: list[dict[str, Any]], path: Path = BENCHMARK_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")


def update_benchmark_vs_spy(
    *,
    trade_date: str | None = None,
    broker_snapshot_path: Path = BROKER_SNAPSHOT_PATH,
    benchmark_path: Path = BENCHMARK_PATH,
    run_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Append today's benchmark record and recompute all returns.

    Returns the new record on success, None if skipped.
    Non-blocking: all failures are logged as warnings.
    """
    import datetime as dt

    if not trade_date:
        trade_date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

    records = load_existing_benchmark(benchmark_path)

    # Idempotent: skip if today already recorded
    existing_dates = {r["date"] for r in records}
    if trade_date in existing_dates:
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

    write_benchmark_artifact(records, benchmark_path)
    _log(f"updated for {trade_date}")
    return new_record
