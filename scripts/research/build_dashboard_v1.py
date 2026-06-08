from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any

from core.strategy_registry import StrategyRegistryEntry, load_strategy_registry_for_repo


def _read_json(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().replace("$", "").replace(",", ""))
    except Exception:
        return None


def _parse_date(value: Any) -> dt.date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def _parse_datetime(value: Any) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _series_point(date_text: str, value: float | None) -> dict[str, Any]:
    return {"date": date_text, "value": value}


def _latest_glob(path: Path, pattern: str) -> Path | None:
    matches = sorted(path.glob(pattern))
    return matches[-1] if matches else None


def _latest_dated_dir(path: Path) -> Path | None:
    if not path.exists():
        return None
    dated = [child for child in path.iterdir() if child.is_dir() and _parse_date(child.name) is not None]
    return sorted(dated, key=lambda child: child.name)[-1] if dated else None


def _status_entry(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _dashboard_strategy_display(entry: StrategyRegistryEntry) -> str:
    raw = entry.raw or {}
    explicit = str(raw.get("dashboard_display") or "").strip()
    if explicit:
        return explicit
    compact = entry.compact_name().replace("_", " ").strip()
    return compact.title() if compact else entry.display_name


def _median(values: list[float]) -> float | None:
    numeric = sorted(float(value) for value in values if value is not None)
    if not numeric:
        return None
    mid = len(numeric) // 2
    if len(numeric) % 2:
        return numeric[mid]
    return (numeric[mid - 1] + numeric[mid]) / 2.0


def _check(name: str, status: str, severity: str, detail: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "name": name,
        "status": status,
        "severity": severity,
        "detail": detail,
    }
    payload.update(extra)
    return payload


def _resolve_report_date(repo_root: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    latest = _read_json(repo_root / "outputs" / "broker" / "broker_snapshot_latest.json")
    if isinstance(latest, dict):
        candidate = str(latest.get("trade_date") or "").strip()
        if candidate:
            return candidate
    snapshot_dir = repo_root / "outputs" / "broker_snapshot"
    latest_snapshot = _latest_glob(snapshot_dir, "broker_snapshot_*.json")
    if latest_snapshot is not None:
        token = latest_snapshot.stem.replace("broker_snapshot_", "").strip()
        if _parse_date(token) is not None:
            return token
    return dt.date.today().isoformat()


class DashboardV1Builder:
    def __init__(self, repo_root: Path | str, *, report_date: str | None = None) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.report_date = _resolve_report_date(self.repo_root, report_date)
        self.generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        self.sources: list[dict[str, Any]] = []
        self.checks: list[dict[str, Any]] = []
        self.errors: list[dict[str, str]] = []
        self.warnings: list[dict[str, str]] = []

    def _record_source(
        self,
        *,
        section: str,
        label: str,
        path: Path | None,
        source_type: str,
        trust_level: str,
        as_of: str | None,
        used: bool,
    ) -> None:
        self.sources.append(
            {
                "section": section,
                "label": label,
                "path": str(path.relative_to(self.repo_root)) if path is not None else "",
                "as_of": as_of,
                "source_type": source_type,
                "trust_level": trust_level,
                "used": used,
            }
        )

    def _mark(self, check: dict[str, Any]) -> None:
        self.checks.append(check)
        if check["status"] == "fail":
            self.errors.append(_status_entry(check["name"], check["detail"]))
        elif check["status"] == "warn":
            self.warnings.append(_status_entry(check["name"], check["detail"]))

    def _load_snapshot_payload(self) -> tuple[dict[str, Any] | None, Path | None]:
        direct = self.repo_root / "outputs" / "broker_snapshot" / f"broker_snapshot_{self.report_date}.json"
        if direct.exists():
            payload = _read_json(direct)
            return (payload if isinstance(payload, dict) else None), direct
        latest = _latest_glob(self.repo_root / "outputs" / "broker_snapshot", "broker_snapshot_*.json")
        payload = _read_json(latest) if latest else None
        return (payload if isinstance(payload, dict) else None), latest

    def _load_latest_broker_account(self) -> tuple[dict[str, Any] | None, Path]:
        path = self.repo_root / "outputs" / "broker" / "broker_snapshot_latest.json"
        payload = _read_json(path)
        return (payload if isinstance(payload, dict) else None), path

    def _load_latest_positions(self) -> tuple[dict[str, Any] | None, Path]:
        path = self.repo_root / "outputs" / "broker" / "posttrade_positions.json"
        payload = _read_json(path)
        return (payload if isinstance(payload, dict) else None), path

    def _load_perf_rows(self) -> tuple[list[dict[str, Any]], Path, list[dict[str, Any]], Path]:
        perf_dir = self.repo_root / "outputs" / "perf"
        nav_path = perf_dir / "live_overlay_nav_series.csv"
        bench_path = perf_dir / "live_overlay_benchmark_close_history.csv"
        if not nav_path.exists():
            nav_path = perf_dir / "nav_timeseries.csv"
        if not bench_path.exists():
            bench_path = perf_dir / "benchmark_close_history.csv"
        return _read_csv_rows(nav_path), nav_path, _read_csv_rows(bench_path), bench_path

    def _build_positions_and_nav(self) -> tuple[dict[str, Any], dict[str, Any]]:
        account_payload, account_path = self._load_latest_broker_account()
        positions_payload, positions_path = self._load_latest_positions()

        account = account_payload.get("account") if isinstance(account_payload, dict) and isinstance(account_payload.get("account"), dict) else (account_payload or {})
        positions = positions_payload.get("positions") if isinstance(positions_payload, dict) and isinstance(positions_payload.get("positions"), list) else []
        account_as_of = None
        if isinstance(account_payload, dict):
            account_as_of = str(account_payload.get("captured_at") or account_payload.get("as_of") or "").strip() or None
        positions_as_of = None
        if isinstance(positions_payload, dict):
            positions_as_of = str(positions_payload.get("captured_at") or positions_payload.get("as_of") or "").strip() or None
        trust_level = (
            str(account_payload.get("trust_level") or "").strip().lower()
            if isinstance(account_payload, dict)
            else ""
        ) or "missing"

        self._record_source(
            section="nav",
            label="broker account snapshot",
            path=account_path,
            source_type="broker_account",
            trust_level=trust_level,
            as_of=account_as_of,
            used=bool(account_payload),
        )
        self._record_source(
            section="positions",
            label="broker positions snapshot",
            path=positions_path,
            source_type="broker_positions",
            trust_level=trust_level,
            as_of=positions_as_of,
            used=bool(positions_payload),
        )

        if not isinstance(account_payload, dict):
            self._mark(_check("nav_source_present", "fail", "blocking", "Broker account snapshot not found or malformed."))
        else:
            self._mark(_check("nav_source_present", "pass", "blocking", "Broker account snapshot loaded."))
        if not isinstance(positions_payload, dict) or not isinstance(positions, list):
            self._mark(_check("positions_source_present", "fail", "blocking", "Broker positions snapshot not found or malformed."))
        else:
            self._mark(_check("positions_source_present", "pass", "blocking", "Broker positions snapshot loaded."))

        equity = _to_float(account.get("equity") or account.get("portfolio_value"))
        cash = _to_float(account.get("cash"))
        buying_power = _to_float(account.get("buying_power") or account.get("daytrading_buying_power"))
        last_equity = _to_float(account.get("last_equity"))
        market_value = _to_float((account_payload or {}).get("market_value"))
        if market_value is None:
            numeric_values = [_to_float(item.get("market_value")) for item in positions if isinstance(item, dict)]
            usable_values = [value for value in numeric_values if value is not None]
            if usable_values:
                market_value = round(sum(usable_values), 2)

        rows: list[dict[str, Any]] = []
        for item in positions:
            if not isinstance(item, dict):
                continue
            qty = _to_float(item.get("qty"))
            cost_basis = _to_float(item.get("cost_basis"))
            avg_entry = (cost_basis / qty) if qty not in (None, 0) and cost_basis is not None else None
            last_price = _to_float(item.get("current_price"))
            row_market_value = _to_float(item.get("market_value"))
            weight = (row_market_value / equity) if row_market_value is not None and equity not in (None, 0) else None
            rows.append(
                {
                    "ticker": str(item.get("symbol") or "").strip().upper(),
                    "side": str(item.get("side") or "long").strip().lower() or "long",
                    "qty": qty,
                    "avg_entry_price": avg_entry,
                    "last_price": last_price,
                    "market_value": row_market_value,
                    "cost_basis": cost_basis,
                    "unrealized_pnl": _to_float(item.get("unrealized_pl")),
                    "unrealized_pnl_pct": _to_float(item.get("unrealized_plpc")),
                    "weight": weight,
                }
            )
        rows = [row for row in rows if row["ticker"]]
        rows.sort(key=lambda row: abs(row.get("market_value") or 0.0), reverse=True)

        weights = [row["weight"] for row in rows if row.get("weight") is not None]
        position_values = [abs(row["market_value"]) for row in rows if row.get("market_value") is not None]
        sum_market_values = round(sum(position_values), 2) if position_values else None
        cash_plus_positions = (
            round((sum_market_values or 0.0) + (cash or 0.0), 2)
            if sum_market_values is not None and cash is not None
            else None
        )
        positions_count = len(rows)

        if cash_plus_positions is not None and equity is not None and abs(cash_plus_positions - equity) <= 1.0:
            self._mark(
                _check(
                    "positions_sum_matches_nav",
                    "pass",
                    "blocking",
                    "Sum of position market values plus cash matches equity within tolerance.",
                    tolerance=1.0,
                )
            )
        else:
            self._mark(
                _check(
                    "positions_sum_matches_nav",
                    "fail",
                    "blocking",
                    "Sum of position market values plus cash does not match equity within tolerance.",
                    tolerance=1.0,
                    positions_plus_cash=cash_plus_positions,
                    equity=equity,
                )
            )

        weight_sum = sum(weights) if weights else None
        gross_exposure = (market_value / equity) if market_value is not None and equity not in (None, 0) else None
        if weight_sum is not None and gross_exposure is not None and abs(weight_sum - gross_exposure) <= 0.01:
            self._mark(_check("positions_weights_sum_reasonable", "pass", "blocking", "Position weights match gross exposure."))
        else:
            self._mark(_check("positions_weights_sum_reasonable", "fail", "blocking", "Position weights do not match gross exposure."))

        largest_position_weight = max(weights) if weights else None
        top5_concentration = sum(weights[:5]) if weights else None
        positions_section = {
            "as_of": positions_as_of,
            "source_type": "broker_positions",
            "trust_level": "canonical" if trust_level == "authoritative" else trust_level,
            "is_stale": False,
            "summary": {
                "positions_count": positions_count,
                "gross_market_value": sum_market_values,
                "net_market_value": sum_market_values,
                "cash": cash,
                "largest_position_weight": largest_position_weight,
                "top5_concentration": top5_concentration,
            },
            "rows": rows,
        }
        nav_section = {
            "as_of": account_as_of,
            "source_type": "broker_account",
            "trust_level": "canonical" if trust_level == "authoritative" else trust_level,
            "is_stale": False,
            "equity": equity,
            "cash": cash,
            "long_market_value": market_value,
            "short_market_value": 0.0,
            "gross_exposure": gross_exposure,
            "net_exposure": gross_exposure,
            "buying_power": buying_power,
            "day_pnl": (round(equity - last_equity, 2) if equity is not None and last_equity is not None else None),
            "day_return": (((equity / last_equity) - 1.0) if equity not in (None, 0) and last_equity not in (None, 0) else None),
        }
        return positions_section, nav_section

    def _build_trades_today(self) -> dict[str, Any]:
        payload, path = self._load_snapshot_payload()
        source_type = "alpaca_fills"
        as_of = None
        fills = []
        if isinstance(payload, dict):
            meta = payload.get("meta")
            if isinstance(meta, dict):
                as_of = str(meta.get("generated_at") or "").strip() or None
            raw_fills = payload.get("fills_report_date")
            if isinstance(raw_fills, list):
                fills = [fill for fill in raw_fills if isinstance(fill, dict)]
        self._record_source(
            section="trades_today",
            label="alpaca fills snapshot",
            path=path,
            source_type=source_type,
            trust_level="canonical" if payload else "missing",
            as_of=as_of,
            used=bool(payload),
        )

        if not isinstance(payload, dict):
            self._mark(_check("trades_source_present", "fail", "blocking", "Alpaca fills snapshot missing for report date."))
            return {
                "as_of": None,
                "source_type": "missing",
                "trust_level": "missing",
                "is_stale": True,
                "summary": {
                    "fills_count": 0,
                    "buy_count": 0,
                    "sell_count": 0,
                    "buy_notional": None,
                    "sell_notional": None,
                },
                "rows": [],
            }
        self._mark(_check("trades_source_present", "pass", "blocking", "Alpaca fills snapshot loaded."))

        rows: list[dict[str, Any]] = []
        buy_notional = 0.0
        sell_notional = 0.0
        out_of_date = False
        for fill in fills:
            side = str(fill.get("side") or "").strip().lower()
            qty = _to_float(fill.get("qty"))
            price = _to_float(fill.get("price"))
            notional = price * qty if price is not None and qty is not None else None
            timestamp = str(fill.get("transaction_time") or "").strip() or None
            fill_date = timestamp[:10] if timestamp else None
            if fill_date and fill_date != self.report_date:
                out_of_date = True
            if side == "buy" and notional is not None:
                buy_notional += notional
            if side == "sell" and notional is not None:
                sell_notional += notional
            rows.append(
                {
                    "filled_at": timestamp,
                    "ticker": str(fill.get("symbol") or "").strip().upper(),
                    "side": side,
                    "qty": qty,
                    "fill_price": price,
                    "notional": notional,
                    "order_id": fill.get("order_id"),
                    "client_order_id": None,
                    "source_execution_id": fill.get("id"),
                }
            )
        rows.sort(key=lambda row: str(row.get("filled_at") or ""))

        if out_of_date:
            self._mark(_check("trades_are_report_date_only", "fail", "blocking", "At least one fill is outside the report date."))
        else:
            self._mark(_check("trades_are_report_date_only", "pass", "blocking", "All fills match the report date."))

        return {
            "as_of": as_of,
            "source_type": source_type,
            "trust_level": "canonical",
            "is_stale": False,
            "summary": {
                "fills_count": len(rows),
                "buy_count": sum(1 for row in rows if row["side"] == "buy"),
                "sell_count": sum(1 for row in rows if row["side"] == "sell"),
                "buy_notional": round(buy_notional, 2) if rows else 0.0,
                "sell_notional": round(sell_notional, 2) if rows else 0.0,
            },
            "rows": rows,
        }

    def _build_performance_history(self, nav_section: dict[str, Any]) -> dict[str, Any]:
        nav_rows_raw, nav_path, bench_rows_raw, bench_path = self._load_perf_rows()
        self._record_source(
            section="performance_history",
            label="portfolio history",
            path=nav_path,
            source_type="alpaca_portfolio_history",
            trust_level="canonical",
            as_of=None,
            used=bool(nav_rows_raw),
        )
        self._record_source(
            section="performance_history",
            label="benchmark history",
            path=bench_path,
            source_type="benchmark_history",
            trust_level="canonical",
            as_of=None,
            used=bool(bench_rows_raw),
        )

        if not nav_rows_raw or not bench_rows_raw:
            self._mark(_check("performance_source_present", "fail", "blocking", "Performance history sources are missing."))
            return {
                "as_of": None,
                "source_type": "missing",
                "trust_level": "missing",
                "is_stale": True,
                "summary": {
                    "inception_date": None,
                    "latest_nav": None,
                    "since_inception_return": None,
                    "spy_since_inception_return": None,
                    "excess_since_inception_return": None,
                    "max_drawdown": None,
                },
                "series": {
                    "nav": [],
                    "daily_return": [],
                    "spy_close": [],
                    "nav_indexed": [],
                    "spy_indexed": [],
                    "excess_return_cumulative": [],
                    "drawdown": [],
                },
            }
        self._mark(_check("performance_source_present", "pass", "blocking", "Performance history sources loaded."))

        nav_rows: list[dict[str, Any]] = []
        for row in nav_rows_raw:
            date_text = str(row.get("date") or "").strip()
            equity = _to_float(row.get("equity") or row.get("portfolio_value"))
            if not date_text or equity is None:
                continue
            nav_rows.append(
                {
                    "date": date_text,
                    "equity": equity,
                    "return_1d": _to_float(row.get("return_1d")),
                }
            )
        nav_rows.sort(key=lambda row: row["date"])

        bench_rows: list[dict[str, Any]] = []
        for row in bench_rows_raw:
            date_text = str(row.get("date") or "").strip()
            spy_close = _to_float(row.get("spy_close"))
            spy_return = _to_float(row.get("spy_return"))
            if not date_text or spy_close is None:
                continue
            bench_rows.append({"date": date_text, "spy_close": spy_close, "spy_return": spy_return})
        bench_rows.sort(key=lambda row: row["date"])

        if len({row["date"] for row in nav_rows}) == len(nav_rows) and nav_rows == sorted(nav_rows, key=lambda row: row["date"]):
            self._mark(_check("performance_series_monotonic_dates", "pass", "blocking", "Portfolio history dates are ordered and unique."))
        else:
            self._mark(_check("performance_series_monotonic_dates", "fail", "blocking", "Portfolio history dates are not ordered or contain duplicates."))

        aligned = [(nav_row, next((bench for bench in bench_rows if bench["date"] == nav_row["date"]), None)) for nav_row in nav_rows]
        aligned = [(nav_row, bench_row) for nav_row, bench_row in aligned if bench_row is not None]
        if aligned:
            self._mark(_check("spy_dates_aligned", "pass", "blocking", "SPY comparison series are aligned to portfolio dates."))
        else:
            self._mark(_check("spy_dates_aligned", "fail", "blocking", "No aligned SPY dates are available for portfolio history."))

        nav_series = [_series_point(row["date"], row["equity"]) for row in nav_rows]
        daily_return_series = []
        prev_equity = None
        for row in nav_rows:
            ret = row["return_1d"]
            if ret is None and prev_equity not in (None, 0):
                ret = (row["equity"] / prev_equity) - 1.0
            if ret is not None:
                daily_return_series.append(_series_point(row["date"], ret))
            prev_equity = row["equity"]

        nav_base = nav_rows[0]["equity"] if nav_rows else None
        nav_indexed = [
            _series_point(row["date"], (row["equity"] / nav_base) * 100.0 if nav_base not in (None, 0) else None)
            for row in nav_rows
        ]
        bench_base = aligned[0][1]["spy_close"] if aligned else None
        spy_series = [_series_point(row["date"], row["spy_close"]) for row in bench_rows]
        spy_indexed = [
            _series_point(bench["date"], (bench["spy_close"] / bench_base) * 100.0 if bench_base not in (None, 0) else None)
            for _, bench in aligned
        ]

        excess_cum = []
        cumulative_port = 1.0
        cumulative_spy = 1.0
        bench_map = {row["date"]: row for row in bench_rows}
        prev_spy_close = None
        for row in nav_rows:
            bench = bench_map.get(row["date"])
            if bench is None:
                continue
            port_ret = next((point["value"] for point in daily_return_series if point["date"] == row["date"]), None)
            spy_ret = bench["spy_return"]
            if spy_ret is None and prev_spy_close not in (None, 0):
                spy_ret = (bench["spy_close"] / prev_spy_close) - 1.0
            prev_spy_close = bench["spy_close"]
            if port_ret is None or spy_ret is None:
                continue
            cumulative_port *= (1.0 + port_ret)
            cumulative_spy *= (1.0 + spy_ret)
            excess_cum.append(_series_point(row["date"], (cumulative_port - cumulative_spy)))

        drawdown = []
        peak = None
        max_drawdown = None
        for row in nav_rows:
            peak = row["equity"] if peak is None else max(peak, row["equity"])
            dd = 0.0 if peak in (None, 0) else min(0.0, (row["equity"] / peak) - 1.0)
            max_drawdown = dd if max_drawdown is None else min(max_drawdown, dd)
            drawdown.append(_series_point(row["date"], dd))

        latest_nav = nav_rows[-1]["equity"] if nav_rows else None
        latest_nav_date = nav_rows[-1]["date"] if nav_rows else None
        if latest_nav is not None and nav_section.get("equity") is not None and latest_nav_date == self.report_date and abs(latest_nav - nav_section["equity"]) <= 1.0:
            self._mark(_check("history_latest_nav_matches_nav_section", "pass", "blocking", "Latest portfolio history NAV matches current NAV."))
        else:
            self._mark(_check("history_latest_nav_matches_nav_section", "fail", "blocking", "Latest portfolio history NAV does not match current NAV.", latest_nav=latest_nav, nav_equity=nav_section.get("equity"), latest_nav_date=latest_nav_date, report_date=self.report_date))

        start_nav = nav_rows[0]["equity"] if nav_rows else None
        end_nav = latest_nav
        since_inception_return = ((end_nav / start_nav) - 1.0) if start_nav not in (None, 0) and end_nav is not None else None
        spy_since_return = None
        if aligned:
            start_spy = aligned[0][1]["spy_close"]
            end_spy = aligned[-1][1]["spy_close"]
            if start_spy not in (None, 0) and end_spy is not None:
                spy_since_return = (end_spy / start_spy) - 1.0

        return {
            "as_of": latest_nav_date,
            "source_type": "alpaca_portfolio_history",
            "trust_level": "canonical",
            "is_stale": latest_nav_date != self.report_date,
            "summary": {
                "inception_date": nav_rows[0]["date"] if nav_rows else None,
                "latest_nav": latest_nav,
                "since_inception_return": since_inception_return,
                "spy_since_inception_return": spy_since_return,
                "excess_since_inception_return": (
                    since_inception_return - spy_since_return
                    if since_inception_return is not None and spy_since_return is not None
                    else None
                ),
                "max_drawdown": max_drawdown,
            },
            "series": {
                "nav": nav_series,
                "daily_return": daily_return_series,
                "spy_close": spy_series,
                "nav_indexed": nav_indexed,
                "spy_indexed": spy_indexed,
                "excess_return_cumulative": excess_cum,
                "drawdown": drawdown,
            },
            "_nav_rows": nav_rows,
            "_bench_rows": bench_rows,
        }

    def _compute_window_return(self, values: list[float], periods: int) -> float | None:
        if len(values) < periods + 1:
            return None
        start = values[-(periods + 1)]
        end = values[-1]
        if start in (None, 0) or end is None:
            return None
        return (end / start) - 1.0

    def _build_shadow_command_center(self) -> dict[str, Any]:
        shadow_root = self.repo_root / "outputs" / "shadow_candidates"
        latest_dir = _latest_dated_dir(shadow_root)
        latest_eval_path = latest_dir / "shadow_evaluation.json" if latest_dir is not None else shadow_root / "latest" / "shadow_evaluation.json"
        latest_eval = _read_json(latest_eval_path)
        nav_path = shadow_root / "performance" / "shadow_nav_series.csv"
        nav_rows = _read_csv_rows(nav_path)
        self._record_source(
            section="shadow_command_center",
            label="shadow evaluation",
            path=latest_eval_path,
            source_type="shadow_evaluation",
            trust_level="diagnostic" if isinstance(latest_eval, dict) else "missing",
            as_of=(latest_eval or {}).get("trade_date") if isinstance(latest_eval, dict) else None,
            used=isinstance(latest_eval, dict),
        )
        self._record_source(
            section="shadow_command_center",
            label="shadow nav series",
            path=nav_path,
            source_type="shadow_nav_series",
            trust_level="diagnostic" if nav_rows else "missing",
            as_of=nav_rows[-1].get("date") if nav_rows else None,
            used=bool(nav_rows),
        )

        if not isinstance(latest_eval, dict):
            self._mark(_check("shadow_command_center_source_present", "warn", "non_blocking", "Shadow evaluation artifact unavailable."))
            return {
                "as_of": None,
                "is_stale": True,
                "status": "NO_DATA",
                "summary": {"latest_nav_date": nav_rows[-1].get("date") if nav_rows else None, "candidate_count": 0},
                "strategies": [],
                "rolling_excess_series": [],
            }
        self._mark(_check("shadow_command_center_source_present", "pass", "non_blocking", "Shadow evaluation artifact loaded."))

        registry = load_strategy_registry_for_repo(self.repo_root)
        baseline_slug = registry.baseline_strategy_id()
        strategy_meta = {
            entry.strategy_id: {
                "role": "CONTROL" if entry.strategy_id == baseline_slug else "CHALLENGER",
                "display": _dashboard_strategy_display(entry),
            }
            for entry in registry.active_shadow_security_selection_entries()
        }
        nav_by_slug: dict[str, list[float]] = {}
        spy_values: list[float] = []
        rolling_series: list[dict[str, Any]] = []
        for row in nav_rows:
            spy_value = _to_float(row.get("spy_benchmark"))
            if spy_value is not None:
                spy_values.append(spy_value)
            point: dict[str, Any] = {"date": row.get("date")}
            for slug in strategy_meta:
                value = _to_float(row.get(slug))
                nav_by_slug.setdefault(slug, []).append(value) if value is not None else None
                values = nav_by_slug.get(slug, [])
                if len(values) >= 6 and len(spy_values) >= 6:
                    strategy_5d = self._compute_window_return(values, 5)
                    spy_5d = self._compute_window_return(spy_values, 5)
                    point[slug] = strategy_5d - spy_5d if strategy_5d is not None and spy_5d is not None else None
            if any(key in point for key in strategy_meta):
                rolling_series.append(point)

        strategies: list[dict[str, Any]] = []
        eval_strategies = latest_eval.get("strategies") if isinstance(latest_eval.get("strategies"), dict) else {}
        baseline_excess = _to_float((eval_strategies.get(baseline_slug) or {}).get("excess_return_vs_spy"))
        for slug, meta in strategy_meta.items():
            raw = eval_strategies.get(slug) or {}
            values = [_to_float(row.get(slug)) for row in nav_rows if _to_float(row.get(slug)) is not None]
            spy = [_to_float(row.get("spy_benchmark")) for row in nav_rows if _to_float(row.get("spy_benchmark")) is not None]
            rolling_5d = self._compute_window_return(values, 5)
            rolling_20d = self._compute_window_return(values, 20)
            spy_5d = self._compute_window_return(spy, 5)
            spy_20d = self._compute_window_return(spy, 20)
            valid_days = _to_float(raw.get("rolling_count_of_valid_days"))
            excess = _to_float(raw.get("excess_return_vs_spy"))
            failed: list[str] = []
            if raw.get("data_status") == "NO_DATA":
                failed.append(str(raw.get("data_reason") or "NO_DATA"))
            if raw.get("status") not in ("OK", None):
                failed.append(str(raw.get("status")))
            if valid_days is None or valid_days < 30:
                failed.append("INSUFFICIENT_VALID_DAYS")
            if meta["role"] == "CHALLENGER" and baseline_excess is not None and excess is not None and excess <= baseline_excess:
                failed.append("BEHIND_POLARIS_EXCESS")
            readiness = "CONTROL" if meta["role"] == "CONTROL" else "WATCHLIST" if failed == ["INSUFFICIENT_VALID_DAYS"] else "NOT_READY" if failed else "PROMOTION_ELIGIBLE"
            strategies.append(
                {
                    "slug": slug,
                    "name": raw.get("strategy_name") or meta["display"],
                    "role": meta["role"],
                    "status": raw.get("status"),
                    "data_status": raw.get("data_status"),
                    "data_reason": raw.get("data_reason"),
                    "daily_return": _to_float(raw.get("daily_return")),
                    "cumulative_return": _to_float(raw.get("cumulative_return")),
                    "excess_return_vs_spy": excess,
                    "rolling_5d_excess": rolling_5d - spy_5d if rolling_5d is not None and spy_5d is not None else None,
                    "rolling_20d_excess": rolling_20d - spy_20d if rolling_20d is not None and spy_20d is not None else None,
                    "max_drawdown": _to_float(raw.get("max_drawdown")),
                    "realized_volatility_ann": _to_float(raw.get("realized_volatility_ann")),
                    "avg_turnover": _to_float(raw.get("avg_turnover")),
                    "avg_top_3_concentration": _to_float(raw.get("avg_top_3_concentration")),
                    "valid_evaluation_days": int(valid_days) if valid_days is not None else None,
                    "promotion_readiness": readiness,
                    "failed_criteria": failed,
                }
            )

        latest_nav_date = nav_rows[-1].get("date") if nav_rows else None
        eval_date = str(latest_eval.get("trade_date") or "")
        is_stale = bool(latest_nav_date and eval_date and latest_nav_date < eval_date)
        if is_stale:
            self._mark(_check("shadow_nav_current", "warn", "non_blocking", "Shadow NAV latest date lags latest evaluation date.", latest_nav_date=latest_nav_date, evaluation_date=eval_date))
        else:
            self._mark(_check("shadow_nav_current", "pass", "non_blocking", "Shadow NAV and evaluation dates are aligned or sufficient."))
        return {
            "as_of": eval_date or None,
            "is_stale": is_stale,
            "status": "OK" if not any(strategy["data_status"] == "NO_DATA" for strategy in strategies) else "NO_DATA",
            "summary": {
                "latest_nav_date": latest_nav_date,
                "candidate_count": len([s for s in strategies if s["role"] == "CHALLENGER"]),
                "control": baseline_slug,
                "benchmark": latest_eval.get("benchmark_symbol") or "SPY",
            },
            "strategies": strategies,
            "rolling_excess_series": rolling_series[-80:],
        }

    def _build_system_health_console(self, sections: dict[str, Any]) -> dict[str, Any]:
        health_path = self.repo_root / "outputs" / "health" / "caerus_daily_health_check" / "latest" / "health_check.json"
        health_payload = _read_json(health_path)
        hydration_root = self.repo_root / "outputs" / "price_hydration"
        hydration_path = _latest_glob(hydration_root, "*/status.json")
        hydration_payload = _read_json(hydration_path) if hydration_path else None
        recon_path = self.repo_root / "outputs" / "reconciliation" / "live_vs_shadow" / "latest" / "live_vs_shadow_reconciliation.json"
        recon_payload = _read_json(recon_path)

        self._record_source(section="system_health_console", label="daily health check", path=health_path, source_type="health_check", trust_level="diagnostic" if isinstance(health_payload, dict) else "missing", as_of=(health_payload or {}).get("trade_date") if isinstance(health_payload, dict) else None, used=isinstance(health_payload, dict))
        self._record_source(section="system_health_console", label="hydration status", path=hydration_path, source_type="price_hydration", trust_level="diagnostic" if isinstance(hydration_payload, dict) else "missing", as_of=(hydration_payload or {}).get("as_of_date") if isinstance(hydration_payload, dict) else None, used=isinstance(hydration_payload, dict))
        self._record_source(section="system_health_console", label="live vs shadow reconciliation", path=recon_path, source_type="reconciliation", trust_level="diagnostic" if isinstance(recon_payload, dict) else "missing", as_of=(recon_payload or {}).get("generated_at") if isinstance(recon_payload, dict) else None, used=isinstance(recon_payload, dict))

        health_checks = health_payload.get("checks") if isinstance(health_payload, dict) and isinstance(health_payload.get("checks"), list) else []
        failed = [check for check in health_checks if str(check.get("status") or "").upper() in {"RED", "FAIL", "ERROR"}]
        warned = [check for check in health_checks if str(check.get("status") or "").upper() in {"YELLOW", "WARN", "WARNING"}]
        status = "FAIL" if failed else "WARN" if warned or not health_checks else "PASS"
        rows = [
            {"name": "Daily health", "status": status, "detail": f"{len(failed)} fail · {len(warned)} warn · {len(health_checks)} checks"},
            {"name": "Hydration", "status": str((hydration_payload or {}).get("status") or "MISSING"), "detail": f"max cache {(hydration_payload or {}).get('max_cache_date') or '—'}"},
            {"name": "Reconciliation", "status": str((recon_payload or {}).get("classification") or "MISSING"), "detail": f"generated {(recon_payload or {}).get('generated_at') or '—'}"},
            {"name": "Dashboard validation", "status": sections.get("nav", {}).get("trust_level") or "unknown", "detail": f"{len(self.errors)} errors · {len(self.warnings)} warnings"},
        ]
        latest_execution = _latest_glob(self.repo_root / "outputs" / "runs", "*/trading_day_summary.json")
        if latest_execution:
            rows.append({"name": "Latest execution artifact", "status": "PRESENT", "detail": str(latest_execution.relative_to(self.repo_root))})
        return {
            "as_of": self.generated_at.isoformat(),
            "is_stale": status != "PASS",
            "summary": {
                "status": status,
                "failed_pipeline_count": len(failed),
                "warning_count": len(warned) + len(self.warnings),
                "latest_successful_execution": str(latest_execution.relative_to(self.repo_root)) if latest_execution else None,
                "hydration_max_cache_date": (hydration_payload or {}).get("max_cache_date") if isinstance(hydration_payload, dict) else None,
                "shadow_generation_date": sections.get("shadow_command_center", {}).get("as_of"),
            },
            "checks": rows,
        }

    def _build_regime_market_state(self) -> dict[str, Any]:
        vix_path = self.repo_root / "outputs" / "vix_regime" / "regime_current.json"
        review_path = self.repo_root / "outputs" / "engine_review" / "live_regime_review_latest.json"
        vix_payload = _read_json(vix_path)
        review_payload = _read_json(review_path)
        self._record_source(section="regime_market_state", label="vix regime", path=vix_path, source_type="vix_regime", trust_level="diagnostic" if isinstance(vix_payload, dict) else "missing", as_of=(vix_payload or {}).get("as_of") if isinstance(vix_payload, dict) else None, used=isinstance(vix_payload, dict))
        self._record_source(section="regime_market_state", label="engine review", path=review_path, source_type="engine_review", trust_level="diagnostic" if isinstance(review_payload, dict) else "missing", as_of=(review_payload or {}).get("asof_date") if isinstance(review_payload, dict) else None, used=isinstance(review_payload, dict))
        gate = review_payload.get("promotion_gate") if isinstance(review_payload, dict) and isinstance(review_payload.get("promotion_gate"), dict) else {}
        checks = gate.get("checks") if isinstance(gate.get("checks"), list) else []
        return {
            "as_of": (vix_payload or {}).get("as_of") if isinstance(vix_payload, dict) else None,
            "is_stale": False if isinstance(vix_payload, dict) else True,
            "current_regime": (vix_payload or {}).get("regime") if isinstance(vix_payload, dict) else None,
            "vix": _to_float((vix_payload or {}).get("vix")) if isinstance(vix_payload, dict) else None,
            "portfolio_scale": _to_float((vix_payload or {}).get("position_scale")) if isinstance(vix_payload, dict) else None,
            "max_positions": _to_float((vix_payload or {}).get("max_positions")) if isinstance(vix_payload, dict) else None,
            "promotion_gate_blockers": gate.get("blockers") or [],
            "confidence_state": "FALLBACK" if not isinstance(vix_payload, dict) else "AVAILABLE",
            "checks": checks[:8],
        }

    def _build_daily_decision_intelligence(self, sections: dict[str, Any]) -> dict[str, Any]:
        positions = sections["positions"].get("rows") or []
        trades = sections["trades_today"].get("rows") or []
        performance = sections["performance_history"]
        daily_returns = performance.get("series", {}).get("daily_return") or []
        latest_return = daily_returns[-1].get("value") if daily_returns else None
        buy_rows = [row for row in trades if row.get("side") == "buy"]
        sell_rows = [row for row in trades if row.get("side") == "sell"]
        largest_buys = sorted(buy_rows, key=lambda row: row.get("notional") or 0.0, reverse=True)[:5]
        largest_sells = sorted(sell_rows, key=lambda row: row.get("notional") or 0.0, reverse=True)[:5]
        leaders = sorted([row for row in positions if row.get("unrealized_pnl") is not None], key=lambda row: row.get("unrealized_pnl") or 0.0, reverse=True)[:3]
        laggards = sorted([row for row in positions if row.get("unrealized_pnl") is not None], key=lambda row: row.get("unrealized_pnl") or 0.0)[:3]
        notes: list[dict[str, Any]] = []
        if latest_return is not None:
            notes.append({"label": "Portfolio daily return", "value": latest_return, "kind": "return"})
        if largest_buys:
            notes.append({"label": "Largest buy", "value": largest_buys[0].get("ticker"), "detail": largest_buys[0].get("notional"), "kind": "trade"})
        if largest_sells:
            notes.append({"label": "Largest sell", "value": largest_sells[0].get("ticker"), "detail": largest_sells[0].get("notional"), "kind": "trade"})
        return {
            "as_of": self.report_date,
            "is_stale": False,
            "summary": {
                "buy_count": len(buy_rows),
                "sell_count": len(sell_rows),
                "latest_daily_return": latest_return,
                "turnover_proxy_notional": sum(row.get("notional") or 0.0 for row in trades),
            },
            "largest_increases": largest_buys,
            "largest_decreases": largest_sells,
            "leaders": leaders,
            "laggards": laggards,
            "notes": notes,
        }

    def _build_live_readiness(self, sections: dict[str, Any]) -> dict[str, Any]:
        perf_series = sections["performance_history"].get("series", {}).get("nav") or []
        shadow = sections.get("shadow_command_center", {})
        system = sections.get("system_health_console", {})
        validation_failures = len(self.errors)
        artifact_complete = all(source.get("used") for source in self.sources if source.get("section") in {"nav", "positions", "trades_today", "performance_history"})
        criteria = [
            {"name": "Validation integrity", "status": "PASS" if validation_failures == 0 else "FAIL", "detail": f"{validation_failures} blocking errors"},
            {"name": "Artifact completeness", "status": "PASS" if artifact_complete else "WARN", "detail": "canonical dashboard sources loaded" if artifact_complete else "one or more canonical sources missing"},
            {"name": "Shadow continuity", "status": "PASS" if not shadow.get("is_stale") else "WARN", "detail": f"NAV through {shadow.get('summary', {}).get('latest_nav_date') or '—'}"},
            {"name": "Operational health", "status": system.get("summary", {}).get("status") or "UNKNOWN", "detail": f"{system.get('summary', {}).get('failed_pipeline_count', 0)} fail · {system.get('summary', {}).get('warning_count', 0)} warn"},
        ]
        return {
            "as_of": self.generated_at.isoformat(),
            "is_stale": False,
            "summary": {
                "consecutive_healthy_days": len(perf_series),
                "artifact_completeness_streak": len(perf_series) if artifact_complete else 0,
                "shadow_evaluation_continuity": shadow.get("summary", {}).get("latest_nav_date"),
                "successful_execution_streak": None,
                "deployment_confidence": "HIGH" if all(row["status"] == "PASS" for row in criteria[:3]) else "WATCH",
            },
            "criteria": criteria,
        }

    def _build_terminal_view(self, sections: dict[str, Any]) -> dict[str, Any]:
        nav = sections["nav"]
        positions = sections["positions"]
        trades = sections["trades_today"]
        performance = sections["performance_history"]

        position_rows = positions.get("rows") or []
        fill_rows = trades.get("rows") or []
        perf_summary = performance.get("summary") or {}
        nav_rows = performance.get("_nav_rows") or []
        bench_rows = performance.get("_bench_rows") or []

        nav_values = [row.get("equity") for row in nav_rows if row.get("equity") is not None]
        bench_values = [row.get("spy_close") for row in bench_rows if row.get("spy_close") is not None]
        rolling_5d_port = self._compute_window_return(nav_values, 5)
        rolling_20d_port = self._compute_window_return(nav_values, 20)
        rolling_5d_spy = self._compute_window_return(bench_values, 5)
        rolling_20d_spy = self._compute_window_return(bench_values, 20)

        weights = [row.get("weight") for row in position_rows if row.get("weight") is not None]
        unrealized = [row for row in position_rows if row.get("unrealized_pnl") is not None]
        winners = sorted(unrealized, key=lambda row: row.get("unrealized_pnl") or 0.0, reverse=True)[:5]
        laggards = sorted(unrealized, key=lambda row: row.get("unrealized_pnl") or 0.0)[:5]
        cash_ratio = (
            nav.get("cash") / nav.get("equity")
            if nav.get("cash") is not None and nav.get("equity") not in (None, 0)
            else None
        )
        invested_ratio = (
            nav.get("long_market_value") / nav.get("equity")
            if nav.get("long_market_value") is not None and nav.get("equity") not in (None, 0)
            else None
        )
        buy_symbols = sorted({row.get("ticker") for row in fill_rows if row.get("side") == "buy" and row.get("ticker")})
        sell_symbols = sorted({row.get("ticker") for row in fill_rows if row.get("side") == "sell" and row.get("ticker")})
        fill_times = [str(row.get("filled_at") or "") for row in fill_rows if row.get("filled_at")]
        net_notional = (trades.get("summary") or {}).get("buy_notional")
        if net_notional is not None and (trades.get("summary") or {}).get("sell_notional") is not None:
            net_notional = float((trades.get("summary") or {}).get("buy_notional") or 0.0) - float((trades.get("summary") or {}).get("sell_notional") or 0.0)
        validation_checks = self.checks
        blocking_failures = sum(1 for check in validation_checks if check["severity"] == "blocking" and check["status"] == "fail")
        warnings = sum(1 for check in validation_checks if check["status"] == "warn")
        stale_sections = [
            name
            for name, section in sections.items()
            if isinstance(section, dict) and section.get("is_stale")
        ]
        recent_daily = performance.get("series", {}).get("daily_return") or []
        up_days = sum(1 for point in recent_daily if (point.get("value") or 0.0) > 0)
        down_days = sum(1 for point in recent_daily if (point.get("value") or 0.0) < 0)

        return {
            "headline": {
                "nav": nav.get("equity"),
                "day_pnl": nav.get("day_pnl"),
                "day_return": nav.get("day_return"),
                "cash": nav.get("cash"),
                "gross_exposure": nav.get("gross_exposure"),
                "positions_count": positions.get("summary", {}).get("positions_count"),
                "fills_count": trades.get("summary", {}).get("fills_count"),
                "validation_status": "ok" if blocking_failures == 0 else "error",
            },
            "benchmark": {
                "spy_close": bench_values[-1] if bench_values else None,
                "since_inception_return": perf_summary.get("since_inception_return"),
                "spy_since_inception_return": perf_summary.get("spy_since_inception_return"),
                "excess_since_inception_return": perf_summary.get("excess_since_inception_return"),
                "rolling_5d_return": rolling_5d_port,
                "rolling_5d_spy_return": rolling_5d_spy,
                "rolling_5d_excess_return": (
                    rolling_5d_port - rolling_5d_spy
                    if rolling_5d_port is not None and rolling_5d_spy is not None
                    else None
                ),
                "rolling_20d_return": rolling_20d_port,
                "rolling_20d_spy_return": rolling_20d_spy,
                "rolling_20d_excess_return": (
                    rolling_20d_port - rolling_20d_spy
                    if rolling_20d_port is not None and rolling_20d_spy is not None
                    else None
                ),
                "max_drawdown": perf_summary.get("max_drawdown"),
                "history_points": len(nav_values),
                "up_days": up_days,
                "down_days": down_days,
            },
            "positioning": {
                "cash_ratio": cash_ratio,
                "invested_ratio": invested_ratio,
                "largest_position_weight": positions.get("summary", {}).get("largest_position_weight"),
                "top5_concentration": positions.get("summary", {}).get("top5_concentration"),
                "top10_concentration": sum(weights[:10]) if weights else None,
                "average_position_weight": (sum(weights) / len(weights)) if weights else None,
                "median_position_weight": _median([weight for weight in weights if weight is not None]),
                "gross_market_value": positions.get("summary", {}).get("gross_market_value"),
            },
            "leaders": {
                "winners": winners,
                "laggards": laggards,
            },
            "tape": {
                "buy_symbols": buy_symbols,
                "sell_symbols": sell_symbols,
                "buy_notional": (trades.get("summary") or {}).get("buy_notional"),
                "sell_notional": (trades.get("summary") or {}).get("sell_notional"),
                "net_notional": net_notional,
                "last_fill_at": max(fill_times) if fill_times else None,
            },
            "health": {
                "blocking_failures": blocking_failures,
                "warnings": warnings,
                "sources_used": sum(1 for source in self.sources if source.get("used")),
                "sources_total": len(self.sources),
                "stale_sections": stale_sections,
            },
        }

    def _freshness_checks(self, sections: dict[str, Any]) -> None:
        now = self.generated_at
        for name in ("positions", "nav", "trades_today"):
            as_of = sections[name].get("as_of")
            parsed = _parse_datetime(as_of)
            if parsed is None:
                self._mark(_check(f"{name}_timestamp_fresh", "warn", "non_blocking", f"{name} timestamp unavailable."))
                sections[name]["is_stale"] = True
                continue
            is_stale = (now - parsed) > dt.timedelta(hours=36)
            sections[name]["is_stale"] = is_stale
            self._mark(
                _check(
                    f"{name}_timestamp_fresh",
                    "warn" if is_stale else "pass",
                    "non_blocking",
                    f"{name} timestamp {'is stale' if is_stale else 'is fresh'}.",
                )
            )
        perf_date = _parse_date(sections["performance_history"].get("as_of"))
        report_date = _parse_date(self.report_date)
        perf_stale = perf_date != report_date
        sections["performance_history"]["is_stale"] = perf_stale
        self._mark(
            _check(
                "performance_timestamp_fresh",
                "warn" if perf_stale else "pass",
                "non_blocking",
                "Performance history latest date matches report date." if not perf_stale else "Performance history latest date lags report date.",
            )
        )
        buying_power_missing = sections["nav"].get("buying_power") is None
        self._mark(
            _check(
                "buying_power_present",
                "warn" if buying_power_missing else "pass",
                "non_blocking",
                "Buying power unavailable from broker source." if buying_power_missing else "Buying power present.",
            )
        )

    def build(self) -> dict[str, Any]:
        positions_section, nav_section = self._build_positions_and_nav()
        trades_section = self._build_trades_today()
        performance_section = self._build_performance_history(nav_section)
        shadow_section = self._build_shadow_command_center()
        regime_section = self._build_regime_market_state()
        sections = {
            "positions": positions_section,
            "nav": nav_section,
            "trades_today": trades_section,
            "performance_history": performance_section,
            "shadow_command_center": shadow_section,
            "regime_market_state": regime_section,
        }
        decision_section = self._build_daily_decision_intelligence(sections)
        sections["daily_decision_intelligence"] = decision_section
        system_health_section = self._build_system_health_console(sections)
        sections["system_health_console"] = system_health_section
        live_readiness_section = self._build_live_readiness(sections)
        sections["live_readiness"] = live_readiness_section
        self._freshness_checks(sections)
        terminal = self._build_terminal_view(sections)

        has_blocking_failure = any(
            check["status"] == "fail" and check["severity"] == "blocking"
            for check in self.checks
        )
        has_warnings = any(check["status"] == "warn" for check in self.checks)
        level = "error" if has_blocking_failure else "warning" if has_warnings else "ok"
        summary = (
            "Blocking validation failed."
            if has_blocking_failure
            else "Dashboard published with warnings."
            if has_warnings
            else "All primary sections built from canonical sources."
        )

        return {
            "schema_version": "dashboard-v2-prototype",
            "generated_at": self.generated_at.isoformat(),
            "report_date": self.report_date,
            "environment": "paper",
            "status": {
                "level": level,
                "summary": summary,
                "errors": self.errors,
                "warnings": self.warnings,
            },
            "sections": sections,
            "terminal": terminal,
            "sources": self.sources,
            "validation": {
                "checks": self.checks,
            },
        }


def write_dashboard_v1_payload(payload: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    (output_dir / "dashboard-data.json").write_text(json_text, encoding="utf-8")
    (output_dir / "dashboard_data.json").write_text(json_text, encoding="utf-8")
    (output_dir / "dashboard-data.js").write_text(
        "window.DASHBOARD_V1 = " + json.dumps(payload, indent=2, sort_keys=True) + ";\n",
        encoding="utf-8",
    )


def write_dashboard_v1_bundle(payload: dict[str, Any], output_dir: Path) -> None:
    write_dashboard_v1_payload(payload, output_dir)
    dev_dir = output_dir.parent / f"{output_dir.name}DEV" if output_dir.name == "dashboard" else None
    if dev_dir is not None:
        write_dashboard_v1_payload(payload, dev_dir)
    summary_payload = {
        "report_date": payload.get("report_date"),
        "generated_at": payload.get("generated_at"),
        "dashboard": {
            "path": "web/dashboard/dashboard_data.json",
            "schema_version": payload.get("schema_version"),
            "status": payload.get("status", {}).get("level"),
        },
    }
    (output_dir / "trading_day_summary.json").write_text(
        json.dumps(summary_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the strict Dashboard V1 payload.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--report-date", default=None)
    parser.add_argument("--output-dir", default="web/dashboard")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    payload = DashboardV1Builder(repo_root, report_date=args.report_date).build()
    write_dashboard_v1_bundle(payload, repo_root / args.output_dir)
    print(str((repo_root / args.output_dir / "dashboard_data.json")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
