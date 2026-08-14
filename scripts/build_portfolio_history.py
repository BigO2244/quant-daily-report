from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


TRANSACTION_FIELDS = [
    "date",
    "timestamp",
    "source",
    "order_id",
    "activity_id",
    "ticker",
    "side",
    "quantity",
    "fill_price",
    "notional",
    "signed_notional",
    "status",
    "sleeve",
    "reason",
]

POSITION_FIELDS = [
    "as_of_date",
    "source",
    "ticker",
    "quantity",
    "current_price",
    "market_value",
    "cost_basis",
    "unrealized_pl",
    "unrealized_plpc",
    "weight",
]

NAV_FIELDS = [
    "date",
    "equity",
    "cash",
    "gross_exposure",
    "net_exposure",
    "return_1d",
    "turnover_dollars",
    "turnover_pct",
    "cumulative_return",
    # FR-066 §3 benchmark / beta-adjusted scoreboard columns (additive).
    "spy_close",
    "spy_return_1d",
    "benchmark_nav",
    "excess_return_1d",
    "rolling_beta_60d",
    "beta_adjusted_excess_1d",
    "source",
]

# FR-066: 1 bp of equity reconciliation tolerance; append-only immutability.
NAV_RECON_TOLERANCE_REL = 1.0 / 10_000.0
TRADING_DAYS_PER_YEAR = 252
BETA_WINDOW = 60
BETA_MIN_OBS = 30
IR_WINDOWS = (63, 126, 252)

ATTRIBUTION_FIELDS = [
    "ticker",
    "quantity",
    "market_value",
    "weight",
    "unrealized_pl",
    "unrealized_plpc",
    "buy_count",
    "sell_count",
    "net_quantity_traded",
    "traded_notional",
]


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size <= 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})
    return path


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _iso_date(value: Any) -> str | None:
    text = str(value or "").strip()
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if match:
        return match.group(0)
    return None


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _relative(repo_root: Path, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def _snapshot_report_date(path: Path, payload: dict[str, Any]) -> str | None:
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    return (
        _iso_date(meta.get("report_date"))
        or _iso_date(payload.get("trade_date"))
        or _iso_date(payload.get("captured_at"))
        or _iso_date(path.name)
    )


def _broker_snapshot_paths(repo_root: Path) -> list[Path]:
    return sorted((repo_root / "outputs" / "broker_snapshot").glob("broker_snapshot_*.json"))


def _transaction_from_fill(fill: dict[str, Any], *, report_date: str, source: str) -> dict[str, Any] | None:
    ticker = str(fill.get("symbol") or fill.get("ticker") or "").strip().upper()
    side = _upper(fill.get("side"))
    qty = _to_float(fill.get("qty") or fill.get("filled_qty") or fill.get("quantity"))
    price = _to_float(fill.get("price") or fill.get("filled_avg_price") or fill.get("fill_price"))
    if not ticker or not side or qty is None or price is None:
        return None
    timestamp = str(fill.get("transaction_time") or fill.get("submitted_at") or fill.get("filled_at") or "").strip()
    date = _iso_date(timestamp) or report_date
    notional = abs(float(qty) * float(price))
    signed = notional if side == "BUY" else -notional if side == "SELL" else notional
    return {
        "date": date,
        "timestamp": timestamp,
        "source": source,
        "order_id": str(fill.get("order_id") or fill.get("id") or "").strip(),
        "activity_id": str(fill.get("id") or "").strip(),
        "ticker": ticker,
        "side": side,
        "quantity": abs(float(qty)),
        "fill_price": float(price),
        "notional": notional,
        "signed_notional": signed,
        "status": "FILLED",
        "sleeve": "",
        "reason": "",
    }


def _ledger_transaction(row: dict[str, Any]) -> dict[str, Any] | None:
    ticker = str(row.get("ticker") or "").strip().upper()
    side = _upper(row.get("side"))
    qty = _to_float(row.get("quantity") or row.get("shares"))
    price = _to_float(row.get("fill_price") or row.get("price"))
    notional = _to_float(row.get("notional"))
    if not ticker or not side:
        return None
    if notional is None and qty is not None and price is not None:
        notional = abs(float(qty) * float(price))
    if notional is None:
        return None
    signed = abs(notional) if side == "BUY" else -abs(notional) if side == "SELL" else notional
    return {
        "date": _iso_date(row.get("trade_date") or row.get("date")) or "",
        "timestamp": str(row.get("timestamp_et") or "").strip(),
        "source": f"ledger_{str(row.get('source') or '').strip() or 'unknown'}",
        "order_id": str(row.get("order_id") or "").strip(),
        "activity_id": "",
        "ticker": ticker,
        "side": side,
        "quantity": abs(float(qty)) if qty is not None else "",
        "fill_price": float(price) if price is not None else "",
        "notional": abs(float(notional)),
        "signed_notional": signed,
        "status": str(row.get("execution_status") or "").strip(),
        "sleeve": str(row.get("sleeve") or "").strip(),
        "reason": str(row.get("reason") or "").strip(),
    }


def _build_transactions(repo_root: Path, report_date: str | None) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    rows: list[dict[str, Any]] = []
    sources: list[str] = []
    warnings: list[str] = []
    seen: set[tuple[Any, ...]] = set()

    for path in _broker_snapshot_paths(repo_root):
        payload = _read_json(path)
        if not isinstance(payload, dict):
            continue
        snap_date = _snapshot_report_date(path, payload)
        if report_date and snap_date and snap_date > report_date:
            continue
        fills = payload.get("fills_report_date")
        fill_source = "broker_fill"
        if not isinstance(fills, list) or not fills:
            fills = payload.get("orders_report_date")
            fill_source = "broker_order"
        if not isinstance(fills, list):
            continue
        used = False
        for fill in fills:
            if not isinstance(fill, dict):
                continue
            if fill_source == "broker_order" and str(fill.get("status") or "").lower() not in {"filled", "partially_filled"}:
                continue
            tx = _transaction_from_fill(fill, report_date=snap_date or report_date or "", source=fill_source)
            if tx is None:
                continue
            key = (
                tx["source"],
                tx["activity_id"],
                tx["order_id"],
                tx["ticker"],
                tx["side"],
                tx["quantity"],
                tx["fill_price"],
                tx["timestamp"],
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(tx)
            used = True
        if used:
            sources.append(_relative(repo_root, path))

    if rows:
        rows.sort(key=lambda row: (str(row.get("date") or ""), str(row.get("timestamp") or ""), str(row.get("ticker") or "")))
        return rows, sources, warnings

    ledger_path = repo_root / "outputs" / "ledger" / "trades.csv"
    for row in _read_csv_rows(ledger_path):
        if report_date and _iso_date(row.get("trade_date") or row.get("date")) and _iso_date(row.get("trade_date") or row.get("date")) > report_date:
            continue
        tx = _ledger_transaction(row)
        if tx is not None:
            rows.append(tx)
    if rows:
        rows.sort(key=lambda row: (str(row.get("date") or ""), str(row.get("timestamp") or ""), str(row.get("ticker") or "")))
        sources.append(_relative(repo_root, ledger_path))
        warnings.append("Broker fill history unavailable; transaction table fell back to the model ledger.")
    else:
        warnings.append("No broker fills or ledger transactions were available.")
    return rows, sources, warnings


def _latest_snapshot_payload(repo_root: Path, report_date: str | None) -> tuple[dict[str, Any] | None, Path | None, str | None]:
    candidates: list[tuple[str, Path, dict[str, Any]]] = []
    for path in _broker_snapshot_paths(repo_root):
        payload = _read_json(path)
        if not isinstance(payload, dict):
            continue
        snap_date = _snapshot_report_date(path, payload)
        if report_date and snap_date and snap_date > report_date:
            continue
        candidates.append((snap_date or "", path, payload))
    if candidates:
        snap_date, path, payload = sorted(candidates, key=lambda item: (item[0], str(item[1])))[-1]
        return payload, path, snap_date

    path = repo_root / "outputs" / "broker" / "posttrade_positions.json"
    payload = _read_json(path)
    if isinstance(payload, dict):
        return payload, path, _iso_date(payload.get("trade_date") or payload.get("captured_at"))
    return None, None, None


def _build_positions(repo_root: Path, report_date: str | None) -> tuple[list[dict[str, Any]], str | None, str | None, list[str]]:
    payload, path, snap_date = _latest_snapshot_payload(repo_root, report_date)
    if not isinstance(payload, dict):
        return [], None, None, ["No broker position snapshot was available."]

    positions = payload.get("positions_current")
    if not isinstance(positions, list):
        positions = payload.get("positions")
    if not isinstance(positions, list):
        return [], _relative(repo_root, path), snap_date, ["Broker position snapshot did not contain a positions list."]

    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    equity = _to_float(account.get("equity") or account.get("portfolio_value"))
    rows: list[dict[str, Any]] = []
    for item in positions:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("symbol") or item.get("ticker") or "").strip().upper()
        qty = _to_float(item.get("qty") or item.get("quantity") or item.get("shares"))
        market_value = _to_float(item.get("market_value"))
        if not ticker:
            continue
        rows.append(
            {
                "as_of_date": snap_date or report_date or "",
                "source": _relative(repo_root, path),
                "ticker": ticker,
                "quantity": qty,
                "current_price": _to_float(item.get("current_price") or item.get("price")),
                "market_value": market_value,
                "cost_basis": _to_float(item.get("cost_basis")),
                "unrealized_pl": _to_float(item.get("unrealized_pl")),
                "unrealized_plpc": _to_float(item.get("unrealized_plpc")),
                "weight": (market_value / equity) if market_value is not None and equity not in (None, 0) else None,
            }
        )
    rows.sort(key=lambda row: (-(abs(float(row["market_value"])) if row.get("market_value") not in (None, "") else 0.0), row["ticker"]))
    return rows, _relative(repo_root, path), snap_date, []


def _build_nav(repo_root: Path, report_date: str | None) -> tuple[list[dict[str, Any]], str | None, list[str]]:
    # There is one actual-PAPER accounting authority: the direct Alpaca ledger.
    # Model/overlay NAV series remain research comparisons and can never be
    # silently promoted into the canonical realized performance record.
    source_path = repo_root / "outputs" / "ledger" / "paper" / "daily_nav.csv"
    if not _read_csv_rows(source_path):
        return [], None, [
            "Canonical broker-truth PAPER daily_nav.csv is unavailable; no model NAV fallback was used."
        ]
    state_payload = _read_json(
        repo_root / "outputs" / "ledger" / "paper" / "daily_state_latest.json"
    )
    state_days = (
        (state_payload.get("days") or [])
        if isinstance(state_payload, dict)
        else []
    )
    state_by_date = {
        str(row.get("date") or ""): row
        for row in state_days
        if isinstance(row, dict)
    }

    rows: list[dict[str, Any]] = []
    for raw in _read_csv_rows(source_path):
        date = _iso_date(raw.get("date"))
        if not date:
            continue
        if report_date and date > report_date:
            continue
        state = state_by_date.get(date) or {}
        equity = _to_float(raw.get("equity") or raw.get("portfolio_value"))
        positions_value = _to_float(state.get("positions_market_value"))
        gross_exposure = (
            abs(float(positions_value)) / float(equity)
            if positions_value is not None and equity not in (None, 0)
            else None
        )
        rows.append(
            {
                "date": date,
                "equity": equity,
                "cash": _to_float(state.get("cash")),
                "gross_exposure": gross_exposure,
                "net_exposure": gross_exposure,
                "return_1d": None,
                "turnover_dollars": None,
                "turnover_pct": None,
                "source": _relative(repo_root, source_path),
            }
        )
    rows.sort(key=lambda row: row["date"])
    first_equity = next((row["equity"] for row in rows if row.get("equity") not in (None, 0)), None)
    prev_equity: float | None = None
    for row in rows:
        equity = row.get("equity")
        if row.get("return_1d") is None and equity is not None and prev_equity not in (None, 0):
            row["return_1d"] = (float(equity) / float(prev_equity)) - 1.0
        row["cumulative_return"] = (
            (float(equity) / float(first_equity)) - 1.0
            if equity is not None and first_equity not in (None, 0)
            else None
        )
        if equity is not None:
            prev_equity = float(equity)
    return rows, _relative(repo_root, source_path), []


def _build_attribution(positions: list[dict[str, Any]], transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tx_by_ticker: dict[str, dict[str, float]] = defaultdict(lambda: {"buy_count": 0.0, "sell_count": 0.0, "net_quantity": 0.0, "notional": 0.0})
    for tx in transactions:
        ticker = str(tx.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        side = _upper(tx.get("side"))
        qty = _to_float(tx.get("quantity")) or 0.0
        notional = _to_float(tx.get("notional")) or 0.0
        if side == "BUY":
            tx_by_ticker[ticker]["buy_count"] += 1.0
            tx_by_ticker[ticker]["net_quantity"] += qty
        elif side == "SELL":
            tx_by_ticker[ticker]["sell_count"] += 1.0
            tx_by_ticker[ticker]["net_quantity"] -= qty
        tx_by_ticker[ticker]["notional"] += abs(notional)

    rows: list[dict[str, Any]] = []
    position_tickers = set()
    for pos in positions:
        ticker = str(pos.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        position_tickers.add(ticker)
        tx = tx_by_ticker[ticker]
        rows.append(
            {
                "ticker": ticker,
                "quantity": pos.get("quantity"),
                "market_value": pos.get("market_value"),
                "weight": pos.get("weight"),
                "unrealized_pl": pos.get("unrealized_pl"),
                "unrealized_plpc": pos.get("unrealized_plpc"),
                "buy_count": int(tx["buy_count"]),
                "sell_count": int(tx["sell_count"]),
                "net_quantity_traded": tx["net_quantity"],
                "traded_notional": tx["notional"],
            }
        )
    for ticker, tx in sorted(tx_by_ticker.items()):
        if ticker in position_tickers:
            continue
        rows.append(
            {
                "ticker": ticker,
                "quantity": 0.0,
                "market_value": 0.0,
                "weight": 0.0,
                "unrealized_pl": "",
                "unrealized_plpc": "",
                "buy_count": int(tx["buy_count"]),
                "sell_count": int(tx["sell_count"]),
                "net_quantity_traded": tx["net_quantity"],
                "traded_notional": tx["notional"],
            }
        )
    rows.sort(key=lambda row: (-(abs(float(row["market_value"])) if row.get("market_value") not in (None, "") else 0.0), row["ticker"]))
    return rows


def _read_existing_canonical_nav(out_dir: Path) -> dict[str, dict[str, Any]]:
    """Existing canonical nav rows keyed by date (for the append-only merge)."""
    rows = _read_csv_rows(out_dir / "nav.csv")
    by_date: dict[str, dict[str, Any]] = {}
    for raw in rows:
        date = _iso_date(raw.get("date"))
        if not date:
            continue
        merged = {field: raw.get(field) for field in NAV_FIELDS}
        merged["date"] = date
        merged["equity"] = _to_float(raw.get("equity"))
        by_date[date] = merged
    return by_date


def _merge_append_only(
    existing: dict[str, dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge candidate rows into the canonical series without dropping or
    silently overwriting any historical date (FR-066 §5 immutability).

    Returns (merged_rows_sorted, restatement_candidates). A candidate whose
    equity disagrees with an existing immutable row beyond 1 bp is reported as
    a restatement candidate but NOT applied — restating a historical row is an
    explicit, logged operator action via outputs/portfolio_history/restatements.json.
    """
    merged: dict[str, dict[str, Any]] = {date: dict(row) for date, row in existing.items()}
    restatement_candidates: list[dict[str, Any]] = []
    for cand in candidates:
        date = _iso_date(cand.get("date"))
        if not date:
            continue
        if date not in merged:
            merged[date] = dict(cand)
            continue
        prior_equity = _to_float(merged[date].get("equity"))
        cand_equity = _to_float(cand.get("equity"))
        if (
            prior_equity not in (None, 0)
            and cand_equity is not None
            and abs(cand_equity - prior_equity) / prior_equity > NAV_RECON_TOLERANCE_REL
        ):
            restatement_candidates.append(
                {
                    "date": date,
                    "canonical_equity": prior_equity,
                    "candidate_equity": cand_equity,
                    "candidate_source": cand.get("source"),
                    "rel_diff_bps": round(abs(cand_equity - prior_equity) / prior_equity * 10_000.0, 3),
                }
            )
        # Append-only: keep the immutable canonical equity row as-is.
    ordered = [merged[date] for date in sorted(merged)]
    return ordered, restatement_candidates


def _read_benchmark_series(repo_root: Path) -> tuple[dict[str, dict[str, float | None]], str | None]:
    """date -> {spy_close, spy_return} from the live benchmark close history."""
    path = repo_root / "outputs" / "perf" / "live_overlay_benchmark_close_history.csv"
    by_date: dict[str, dict[str, float | None]] = {}
    for raw in _read_csv_rows(path):
        date = _iso_date(raw.get("date"))
        if not date:
            continue
        by_date[date] = {
            "spy_close": _to_float(raw.get("spy_close")),
            "spy_return": _to_float(raw.get("spy_return") or raw.get("spy_return_1d")),
        }
    return by_date, (_relative(repo_root, path) if by_date else None)


def _rolling_beta(pairs: list[tuple[float, float] | None], window: int, min_obs: int) -> float | None:
    """Beta of portfolio vs SPY over the trailing `window` paired daily returns.

    `pairs[i]` is (port_return, spy_return) or None where unavailable.
    """
    sample = [p for p in pairs[-window:] if p is not None]
    if len(sample) < min_obs:
        return None
    port = [p[0] for p in sample]
    spy = [p[1] for p in sample]
    n = len(sample)
    mean_p = sum(port) / n
    mean_s = sum(spy) / n
    cov = sum((port[i] - mean_p) * (spy[i] - mean_s) for i in range(n)) / n
    var_s = sum((s - mean_s) ** 2 for s in spy) / n
    if var_s <= 0:
        return None
    return cov / var_s


def _augment_benchmark(rows: list[dict[str, Any]], benchmark: dict[str, dict[str, float | None]]) -> None:
    """Fill the SPY-relative and beta-adjusted columns in place (FR-066 §3)."""
    base_equity = next((r["equity"] for r in rows if _to_float(r.get("equity")) not in (None, 0)), None)
    base_spy = next(
        (benchmark[r["date"]]["spy_close"] for r in rows
         if r["date"] in benchmark and benchmark[r["date"]]["spy_close"] not in (None, 0)),
        None,
    )
    pair_history: list[tuple[float, float] | None] = []
    for row in rows:
        date = row["date"]
        bench = benchmark.get(date) or {}
        spy_close = bench.get("spy_close")
        spy_ret = bench.get("spy_return")
        port_ret = _to_float(row.get("return_1d"))

        row["spy_close"] = spy_close
        row["spy_return_1d"] = spy_ret
        row["benchmark_nav"] = (
            float(base_equity) * (float(spy_close) / float(base_spy))
            if spy_close not in (None, 0) and base_spy not in (None, 0) and base_equity not in (None, 0)
            else None
        )
        row["excess_return_1d"] = (
            float(port_ret) - float(spy_ret) if port_ret is not None and spy_ret is not None else None
        )

        pair_history.append((float(port_ret), float(spy_ret)) if port_ret is not None and spy_ret is not None else None)
        beta = _rolling_beta(pair_history, BETA_WINDOW, BETA_MIN_OBS)
        row["rolling_beta_60d"] = beta
        row["beta_adjusted_excess_1d"] = (
            float(port_ret) - float(beta) * float(spy_ret)
            if beta is not None and port_ret is not None and spy_ret is not None
            else None
        )


def _annualized_ir(daily_excess: list[float]) -> float | None:
    n = len(daily_excess)
    if n < 2:
        return None
    mean = sum(daily_excess) / n
    var = sum((x - mean) ** 2 for x in daily_excess) / (n - 1)
    std = var ** 0.5
    if std <= 0:
        return None
    return (mean / std) * (TRADING_DAYS_PER_YEAR ** 0.5)


def _drawdowns(equities: list[float]) -> tuple[float | None, float | None]:
    """(max_drawdown, current_drawdown) as negative fractions from prior peak."""
    if not equities:
        return None, None
    peak = equities[0]
    max_dd = 0.0
    for eq in equities:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = min(max_dd, (eq / peak) - 1.0)
    last = equities[-1]
    current_dd = (last / peak) - 1.0 if peak > 0 else None
    return max_dd, current_dd


def _derived_scoreboard(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Cumulative excess, IR windows, drawdowns, and the headline scoreboard
    metric (information ratio of beta-adjusted excess vs SPY) — FR-066 §3."""
    excess = [_to_float(r.get("excess_return_1d")) for r in rows]
    excess = [x for x in excess if x is not None]
    beta_excess = [_to_float(r.get("beta_adjusted_excess_1d")) for r in rows]
    beta_excess = [x for x in beta_excess if x is not None]
    equities = [float(r["equity"]) for r in rows if _to_float(r.get("equity")) is not None]

    cumulative_excess = 1.0
    for x in excess:
        cumulative_excess *= (1.0 + x)
    cumulative_excess -= 1.0

    information_ratio = {
        f"ir_{w}d": _annualized_ir(excess[-w:]) for w in IR_WINDOWS
    }
    max_dd, current_dd = _drawdowns(equities)
    scoreboard_metric = _annualized_ir(beta_excess[-TRADING_DAYS_PER_YEAR:]) if beta_excess else None
    return {
        "scoreboard_metric": "beta_adjusted_excess_information_ratio_vs_spy",
        "beta_adjusted_excess_information_ratio": scoreboard_metric,
        "cumulative_excess_return": cumulative_excess if excess else None,
        "information_ratio": information_ratio,
        "max_drawdown": max_dd,
        "current_drawdown": current_dd,
        "excess_observation_count": len(excess),
        "beta_adjusted_observation_count": len(beta_excess),
        "note": "CAGR reported but never used as a promotion or review headline (FR-066 §3).",
    }


def _write_checksum_manifest(out_dir: Path, repo_root: Path, artifacts: dict[str, Path]) -> dict[str, Any]:
    """sha256 + row-count manifest, refreshed on each append (FR-066 §5)."""
    import hashlib

    entries: dict[str, Any] = {}
    for name, path in sorted(artifacts.items()):
        if not path.exists():
            entries[name] = {"path": _relative(repo_root, path), "exists": False}
            continue
        data = path.read_bytes()
        line_count = data.count(b"\n")
        row_count = max(line_count - 1, 0) if path.suffix == ".csv" else None
        entries[name] = {
            "path": _relative(repo_root, path),
            "exists": True,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "row_count": row_count,
        }
    manifest = {
        "schema_version": "caerus_portfolio_history_checksum_v1",
        "governance_label": "OPERATIONAL_TELEMETRY",
        "execution_impact": "NON_EXECUTIONAL",
        "artifacts": entries,
    }
    (out_dir / "checksum_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def build_portfolio_history(
    repo_root: Path | str = ".",
    *,
    report_date: str | None = None,
    append_only: bool = True,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    out_dir = root / "outputs" / "portfolio_history"
    report_date = _iso_date(report_date) if report_date else None

    transactions, transaction_sources, tx_warnings = _build_transactions(root, report_date)
    positions, position_source, positions_as_of, pos_warnings = _build_positions(root, report_date)
    candidate_nav, nav_source, nav_warnings = _build_nav(root, report_date)
    attribution = _build_attribution(positions, transactions)

    # FR-066: append-only canonical NAV series. Existing historical rows are
    # immutable; candidates only add new dates. Equity conflicts are surfaced as
    # restatement candidates, never silently overwritten.
    restatement_candidates: list[dict[str, Any]] = []
    if append_only:
        existing_nav = _read_existing_canonical_nav(out_dir)
        nav_rows, restatement_candidates = _merge_append_only(existing_nav, candidate_nav)
    else:
        nav_rows = candidate_nav

    # Recompute the inception-anchored cumulative return across the merged series.
    first_equity = next((r["equity"] for r in nav_rows if _to_float(r.get("equity")) not in (None, 0)), None)
    for row in nav_rows:
        equity = _to_float(row.get("equity"))
        row["cumulative_return"] = (
            (float(equity) / float(first_equity)) - 1.0
            if equity is not None and first_equity not in (None, 0)
            else None
        )

    # FR-066 §3: SPY-relative and beta-adjusted scoreboard columns (additive).
    benchmark_by_date, benchmark_source = _read_benchmark_series(root)
    _augment_benchmark(nav_rows, benchmark_by_date)
    scoreboard = _derived_scoreboard(nav_rows)
    if not benchmark_by_date:
        nav_warnings.append("No SPY benchmark close history available; benchmark/beta columns are null.")
    if restatement_candidates:
        nav_warnings.append(
            f"{len(restatement_candidates)} candidate NAV row(s) disagree with the immutable canonical "
            "series beyond 1 bp; append-only guard kept the canonical values. "
            "Log explicit restatements in outputs/portfolio_history/restatements.json if a correction is intended."
        )

    tx_path = _write_csv(out_dir / "transactions.csv", TRANSACTION_FIELDS, transactions)
    pos_path = _write_csv(out_dir / "positions.csv", POSITION_FIELDS, positions)
    nav_path = _write_csv(out_dir / "nav.csv", NAV_FIELDS, nav_rows)
    attr_path = _write_csv(out_dir / "attribution.csv", ATTRIBUTION_FIELDS, attribution)

    latest_nav = nav_rows[-1] if nav_rows else {}
    latest_equity = latest_nav.get("equity")
    total_traded = sum(abs(float(tx.get("notional") or 0.0)) for tx in transactions)
    summary = {
        "report_date": report_date or latest_nav.get("date") or positions_as_of,
        "as_of_date": positions_as_of or latest_nav.get("date"),
        "source_priority": [
            "broker_fills",
            "broker_positions",
            "broker_truth_paper_daily_nav",
            "model_ledger_fallback",
        ],
        "counts": {
            "transactions": len(transactions),
            "positions": len(positions),
            "nav_rows": len(nav_rows),
            "attribution_rows": len(attribution),
        },
        "latest": {
            "equity": latest_equity,
            "cash": latest_nav.get("cash"),
            "return_1d": latest_nav.get("return_1d"),
            "cumulative_return": latest_nav.get("cumulative_return"),
            "positions_count": len(positions),
            "total_traded_notional": total_traded,
            "turnover_pct": (total_traded / float(latest_equity)) if latest_equity not in (None, 0) else None,
        },
        "scoreboard": scoreboard,
        "restatement_candidates": restatement_candidates,
        "paths": {
            "transactions": _relative(root, tx_path),
            "positions": _relative(root, pos_path),
            "nav": _relative(root, nav_path),
            "attribution": _relative(root, attr_path),
            "transaction_sources": transaction_sources,
            "position_source": position_source or "",
            "nav_source": nav_source or "",
            "benchmark_source": benchmark_source or "",
        },
        "warnings": tx_warnings + pos_warnings + nav_warnings,
    }
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    checksum_manifest = _write_checksum_manifest(
        out_dir,
        root,
        {
            "transactions": tx_path,
            "positions": pos_path,
            "nav": nav_path,
            "attribution": attr_path,
            "summary": summary_path,
        },
    )

    return {
        "summary": summary,
        "transactions": transactions,
        "positions": positions,
        "nav": nav_rows,
        "attribution": attribution,
        "checksum_manifest": checksum_manifest,
    }


def load_portfolio_history(repo_root: Path | str = ".", *, report_date: str | None = None) -> dict[str, Any]:
    return build_portfolio_history(repo_root, report_date=report_date)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build canonical portfolio history artifacts for the dashboard.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--trade-date", default=None)
    args = parser.parse_args(argv)
    payload = build_portfolio_history(args.repo_root, report_date=args.trade_date)
    print(json.dumps({"summary": payload["summary"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
