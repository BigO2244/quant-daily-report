from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

REPO_CODE_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_CODE_ROOT))

from core.strategy_registry import StrategyRegistryEntry, load_strategy_registry_for_repo, registry_path_for_repo


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
                "path": (
                    str(path.relative_to(self.repo_root))
                    if path is not None and path.is_relative_to(self.repo_root)
                    else str(path)
                    if path is not None
                    else ""
                ),
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
                    "inception_nav": None,
                    "inception_nav_source": "first_recorded_broker_equity",
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
                "inception_nav": start_nav,
                "inception_nav_source": "first_recorded_broker_equity",
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
                    "avg_cash_weight": _to_float(raw.get("avg_cash_weight")),
                    "avg_hhi": _to_float(raw.get("avg_hhi")),
                    "avg_effective_n": _to_float(raw.get("avg_effective_n")),
                    "alpha_per_dollar_deployed_proxy": _to_float(raw.get("alpha_per_dollar_deployed_proxy")),
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

    def _model_quality_dir(self) -> Path | None:
        root = self.repo_root / "outputs" / "model_quality"
        exact = root / self.report_date
        if exact.exists():
            return exact
        if not root.exists():
            return None
        dated = []
        for child in root.iterdir():
            if not child.is_dir():
                continue
            parsed = _parse_date(child.name)
            report = _parse_date(self.report_date)
            if parsed is not None and report is not None and parsed <= report:
                dated.append(child)
        return sorted(dated, key=lambda path: path.name)[-1] if dated else None

    def _build_decision_grade(self) -> dict[str, Any]:
        model_dir = self._model_quality_dir()
        source_date = model_dir.name if model_dir is not None else None
        expected_dir = self.repo_root / "outputs" / "model_quality" / self.report_date
        files = {
            "model_quality_packet": "model_quality_packet.json",
            "model_tournament": "model_tournament.json",
            "argo_phase_b_validation": "argo_phase_b_validation.json",
            "strategy_differentiation_deep_dive": "strategy_differentiation_deep_dive.json",
            "phoenix_phase_b_review": "phoenix_phase_b_review.json",
            "multi_asset_research_framework": "multi_asset_research_framework.json",
        }
        payloads: dict[str, dict[str, Any] | None] = {}
        source_paths: dict[str, str] = {}
        missing: list[str] = []
        for label, filename in files.items():
            path = (model_dir / filename) if model_dir is not None else (expected_dir / filename)
            payload = _read_json(path)
            payloads[label] = payload if isinstance(payload, dict) else None
            if isinstance(payload, dict):
                source_paths[label] = str(path.relative_to(self.repo_root))
            elif label in {"model_quality_packet", "model_tournament"}:
                missing.append(label)
            self._record_source(
                section="decision_grade",
                label=label.replace("_", " "),
                path=path,
                source_type="model_quality",
                trust_level="diagnostic" if isinstance(payload, dict) else "missing",
                as_of=source_date,
                used=isinstance(payload, dict),
            )
        packet = payloads["model_quality_packet"] or {}
        tournament = payloads["model_tournament"] or {}
        argo = payloads["argo_phase_b_validation"] or {}
        differentiation = payloads["strategy_differentiation_deep_dive"] or {}
        phoenix = payloads["phoenix_phase_b_review"] or {}
        multi_asset = payloads["multi_asset_research_framework"] or {}
        promotion_ready_count = sum(1 for row in tournament.get("strategies") or [] if isinstance(row, dict) and row.get("decision_grade"))
        decision_grade_strategy_change = bool((packet.get("executive_summary") or {}).get("strategy_change_decision_grade")) or bool(argo.get("decision_grade_recommendation"))
        blockers: list[str] = []
        blockers.extend(str(code) for code in packet.get("reason_codes") or [] if code != "ok")
        blockers.extend(str(code) for code in argo.get("evidence_blockers") or [] if code != "ok")
        for row in differentiation.get("retirement_watchlist") or []:
            if isinstance(row, dict):
                blockers.append(str(row.get("reason") or "RETIREMENT_WATCHLIST_ENTRY"))
        blockers.extend(f"{name.upper()}_MISSING" for name in missing)
        if source_date is not None and source_date != self.report_date:
            blockers.append("MODEL_QUALITY_DATE_DIFFERS_FROM_REPORT_DATE")
        reason_codes = sorted(set(blockers)) or ["ok"]
        status = "PARTIAL" if missing or model_dir is None or source_date != self.report_date else "READY" if decision_grade_strategy_change and reason_codes == ["ok"] else "BLOCKED"
        if status == "PARTIAL":
            self._mark(_check("decision_grade_model_quality_present", "warn", "non_blocking", "Decision-grade model-quality artifacts are incomplete."))
        else:
            self._mark(_check("decision_grade_model_quality_present", "pass", "non_blocking", "Decision-grade model-quality artifacts loaded."))
        return {
            "status": status,
            "latest_model_quality_date": source_date,
            "promotion_ready_count": promotion_ready_count,
            "decision_grade_strategy_change": decision_grade_strategy_change,
            "top_blockers": reason_codes[:8] if reason_codes != ["ok"] else [],
            "confidence_summary": {
                "model_quality_packet_status": packet.get("status"),
                "argo_recommendation_confidence": argo.get("recommendation_confidence"),
                "phoenix_confidence": phoenix.get("confidence"),
                "strategy_differentiation_counts": differentiation.get("redundancy_classification_counts"),
                "multi_asset_status": multi_asset.get("status"),
            },
            "source_paths": source_paths,
            "reason_codes": reason_codes,
        }

    def _latest_live_pilot_run_dir(self) -> Path | None:
        runs_root = self.repo_root / "outputs" / "live_pilot" / "runs"
        if not runs_root.exists():
            return None
        run_dirs = [path for path in runs_root.iterdir() if path.is_dir()]
        if not run_dirs:
            return None
        return max(run_dirs, key=lambda path: path.stat().st_mtime)

    def _latest_live_pilot_plan_path(self) -> Path | None:
        plans_root = self.repo_root / "outputs" / "live_pilot" / "plans"
        direct = plans_root / f"live_pilot_plan_{self.report_date}.json"
        if direct.exists():
            return direct
        return _latest_glob(plans_root, "live_pilot_plan_*.json")

    def _build_live_pilot_status(self) -> dict[str, Any]:
        run_root = self._latest_live_pilot_run_dir()
        plan_path = self._latest_live_pilot_plan_path()

        plan_payload = _read_json(plan_path) if plan_path is not None else None
        preflight_path = run_root / "live_pilot_preflight.json" if run_root is not None else None
        operator_path = run_root / "live_pilot_operator_summary.json" if run_root is not None else None
        evidence_path = run_root / "live_pilot_evidence_metrics.json" if run_root is not None else None
        capital_path = run_root / "live_pilot_capital_usage.json" if run_root is not None else None
        recon_path = run_root / "live_pilot_reconciliation.json" if run_root is not None else None
        submitted_path = run_root / "live_pilot_orders_submitted.json" if run_root is not None else None
        open_order_path = run_root / "live_pilot_open_order_check.json" if run_root is not None else None
        pre_snapshot_path = run_root / "live_pilot_broker_snapshot_pre.json" if run_root is not None else None
        post_snapshot_path = run_root / "live_pilot_broker_snapshot_post.json" if run_root is not None else None

        preflight = _read_json(preflight_path) if preflight_path is not None else None
        operator = _read_json(operator_path) if operator_path is not None else None
        evidence = _read_json(evidence_path) if evidence_path is not None else None
        capital = _read_json(capital_path) if capital_path is not None else None
        reconciliation = _read_json(recon_path) if recon_path is not None else None
        submitted_payload = _read_json(submitted_path) if submitted_path is not None else None
        open_order_check = _read_json(open_order_path) if open_order_path is not None else None
        pre_snapshot = _read_json(pre_snapshot_path) if pre_snapshot_path is not None else None
        post_snapshot = _read_json(post_snapshot_path) if post_snapshot_path is not None else None

        for label, path, payload in (
            ("live pilot plan", plan_path, plan_payload),
            ("live pilot preflight", preflight_path, preflight),
            ("live pilot operator summary", operator_path, operator),
            ("live pilot evidence metrics", evidence_path, evidence),
            ("live pilot reconciliation", recon_path, reconciliation),
            ("live pilot submitted orders", submitted_path, submitted_payload),
            ("live pilot open order check", open_order_path, open_order_check),
            ("live pilot broker snapshot", post_snapshot_path, post_snapshot),
        ):
            self._record_source(
                section="live_pilot",
                label=label,
                path=path,
                source_type="live_pilot_artifact",
                trust_level="runtime" if isinstance(payload, dict) else "missing",
                as_of=(payload or {}).get("generated_at") if isinstance(payload, dict) else None,
                used=isinstance(payload, dict),
            )

        if not isinstance(plan_payload, dict) and run_root is None:
            self._mark(
                _check(
                    "live_pilot_artifacts_present",
                    "warn",
                    "non_blocking",
                    "No live-pilot plan or run artifact is available; pilot evidence status is display-only unknown.",
                )
            )
            return {
                "as_of": None,
                "is_stale": True,
                "status": "NO_DATA",
                "run_id": None,
                "run_root": None,
                "plan_path": None,
                "policy": {
                    "scope": "FR-104 LIVE_PILOT only",
                    "order_type": None,
                    "normal_market_hours_only": True,
                    "capital_behavior_changed": False,
                    "paper_or_production_impact": "none",
                },
                "account": {},
                "positions": [],
                "open_orders": [],
                "submitted_orders": [],
                "latest_submitted_order": None,
                "latest_fill_status": None,
                "reconciliation": {},
                "metrics": {
                    "submitted_count": 0,
                    "accepted_count": 0,
                    "filled_count": 0,
                    "fill_rate": None,
                    "average_time_to_fill_seconds": None,
                    "slippage_bps": None,
                    "rejected_count": 0,
                    "reconciliation_clean_rate": None,
                    "cash_deployment_rate": None,
                    "idle_cash_reason": "live_pilot_artifacts_missing",
                },
            }

        self._mark(_check("live_pilot_artifacts_present", "pass", "non_blocking", "Live-pilot plan or run artifacts loaded."))

        submitted_orders = (
            submitted_payload.get("orders")
            if isinstance(submitted_payload, dict) and isinstance(submitted_payload.get("orders"), list)
            else []
        )
        account_snapshot = post_snapshot if isinstance(post_snapshot, dict) else pre_snapshot if isinstance(pre_snapshot, dict) else {}
        account = account_snapshot.get("account") if isinstance(account_snapshot.get("account"), dict) else {}
        positions = account_snapshot.get("positions") if isinstance(account_snapshot.get("positions"), list) else []
        open_orders = account_snapshot.get("open_orders") if isinstance(account_snapshot.get("open_orders"), list) else []
        latest_submitted_order = submitted_orders[-1] if submitted_orders else None
        selected_order = plan_payload.get("selected_order") if isinstance(plan_payload, dict) else None
        policy = plan_payload.get("order_policy") if isinstance(plan_payload, dict) and isinstance(plan_payload.get("order_policy"), dict) else {}

        terminal_status = (
            operator.get("terminal_status")
            if isinstance(operator, dict)
            else plan_payload.get("status")
            if isinstance(plan_payload, dict)
            else "UNKNOWN"
        )
        run_id = (
            operator.get("run_id")
            if isinstance(operator, dict)
            else run_root.name
            if run_root is not None
            else None
        )
        metrics = evidence if isinstance(evidence, dict) else {}
        capital_usage = capital if isinstance(capital, dict) else {}
        reconciliation_payload = reconciliation if isinstance(reconciliation, dict) else {}
        open_order_payload = open_order_check if isinstance(open_order_check, dict) else {}
        blocking_open_orders = (
            open_order_payload.get("blocking_open_orders")
            if isinstance(open_order_payload.get("blocking_open_orders"), list)
            else []
        )

        return {
            "as_of": (
                operator.get("generated_at")
                if isinstance(operator, dict)
                else plan_payload.get("generated_at")
                if isinstance(plan_payload, dict)
                else None
            ),
            "is_stale": False,
            "status": str(terminal_status or "UNKNOWN"),
            "run_id": run_id,
            "run_root": str(run_root.relative_to(self.repo_root)) if run_root is not None else None,
            "plan_path": str(plan_path.relative_to(self.repo_root)) if plan_path is not None else None,
            "plan_status": plan_payload.get("status") if isinstance(plan_payload, dict) else None,
            "selected_order": selected_order,
            "policy": {
                "scope": policy.get("scope") or "FR-104 LIVE_PILOT only",
                "order_type": policy.get("order_type") or (selected_order or {}).get("order_type"),
                "time_in_force": policy.get("time_in_force") or (selected_order or {}).get("time_in_force"),
                "normal_market_hours_only": policy.get("normal_market_hours_only", True),
                "cap_enforced_before_submission": policy.get("cap_enforced_before_submission", True),
                "duplicate_open_order_policy": policy.get("duplicate_open_order_policy") or "skip_if_open_live_pilot_order_detected",
                "capital_behavior_changed": False,
                "paper_or_production_impact": policy.get("paper_or_production_impact") or "none",
            },
            "account": {
                "cash": _to_float(account.get("cash")),
                "equity": _to_float(account.get("equity") or account.get("portfolio_value")),
                "buying_power": _to_float(account.get("buying_power")),
                "portfolio_value": _to_float(account.get("portfolio_value") or account.get("equity")),
                "status": account.get("status"),
                "account_id_hash": account.get("account_id_hash"),
            },
            "positions": [
                {
                    "ticker": str(row.get("symbol") or row.get("ticker") or "").strip().upper(),
                    "qty": _to_float(row.get("qty")),
                    "market_value": _to_float(row.get("market_value")),
                }
                for row in positions
                if isinstance(row, dict)
            ][:25],
            "open_orders": open_orders,
            "blocking_open_orders": blocking_open_orders,
            "submitted_orders": submitted_orders,
            "latest_submitted_order": latest_submitted_order,
            "latest_fill_status": (
                (latest_submitted_order or {}).get("status")
                or ((latest_submitted_order or {}).get("order") or {}).get("status")
                if isinstance(latest_submitted_order, dict)
                else None
            ),
            "reconciliation": {
                "status": reconciliation_payload.get("status"),
                "state": reconciliation_payload.get("state"),
                "operator_action": reconciliation_payload.get("operator_action"),
                "open_count": reconciliation_payload.get("open_count"),
                "unresolved_count": reconciliation_payload.get("unresolved_count"),
                "rejected_count": reconciliation_payload.get("rejected_count"),
            },
            "metrics": {
                "submitted_count": metrics.get("submitted_count") if metrics else (operator or {}).get("submitted_count") if isinstance(operator, dict) else None,
                "accepted_count": metrics.get("accepted_count"),
                "filled_count": metrics.get("filled_count") if metrics else (operator or {}).get("filled_count") if isinstance(operator, dict) else None,
                "fill_rate": metrics.get("fill_rate") if metrics else (operator or {}).get("fill_rate") if isinstance(operator, dict) else None,
                "average_time_to_fill_seconds": metrics.get("average_time_to_fill_seconds"),
                "slippage_bps": metrics.get("slippage_bps"),
                "rejected_count": metrics.get("rejected_count"),
                "reconciliation_clean": metrics.get("reconciliation_clean"),
                "reconciliation_clean_rate": metrics.get("reconciliation_clean_rate"),
                "cash_deployment_rate": metrics.get("cash_deployment_rate") if metrics else capital_usage.get("cash_deployment_rate"),
                "filled_notional_usd": metrics.get("filled_notional_usd") if metrics else capital_usage.get("filled_notional_usd"),
                "capital_cap_usd": metrics.get("capital_cap_usd") if metrics else capital_usage.get("capital_cap_usd") or (plan_payload or {}).get("capital_cap") if isinstance(plan_payload, dict) else None,
                "idle_cash_reason": metrics.get("idle_cash_reason") if metrics else (operator or {}).get("idle_cash_reason") if isinstance(operator, dict) else None,
                "open_order_count": len(open_orders),
                "blocking_open_order_count": len(blocking_open_orders),
            },
            "paper_live_comparability": {
                "available": False,
                "reason": "paper_live_divergence_artifact_not_available_for_live_pilot_section",
            },
        }

    def _load_sleeve_manifest(self) -> tuple[dict[str, Any] | None, Path]:
        path = self.repo_root / "research_registry" / "sleeves" / "manifest.json"
        payload = _read_json(path)
        return (payload if isinstance(payload, dict) else None), path

    def _lifecycle_stage(self, entry: StrategyRegistryEntry, manifest_row: dict[str, Any] | None) -> str:
        text = " ".join(
            [
                str(entry.status or ""),
                str(entry.execution_impact or ""),
                str((manifest_row or {}).get("lifecycle_stage") or ""),
                str((manifest_row or {}).get("status") or ""),
            ]
        ).lower()
        if "production" in text:
            return "production"
        if "pilot" in text or "live" in text:
            return "pilot/live"
        if "paper" in text:
            return "paper"
        if "shadow" in text:
            return "shadow"
        if entry.strategy_type in {"benchmark", "reference_portfolio"}:
            return "reference"
        return "research"

    def _build_sleeve_inventory(self, shadow_section: dict[str, Any]) -> dict[str, Any]:
        registry = load_strategy_registry_for_repo(self.repo_root)
        manifest, manifest_path = self._load_sleeve_manifest()
        manifest_rows = manifest.get("sleeves") if isinstance(manifest, dict) and isinstance(manifest.get("sleeves"), list) else []
        manifest_by_strategy = {
            str(row.get("strategy_id") or "").strip(): row
            for row in manifest_rows
            if isinstance(row, dict) and row.get("strategy_id")
        }
        shadow_metrics = {
            str(row.get("slug") or "").strip(): row
            for row in shadow_section.get("strategies", [])
            if isinstance(row, dict) and row.get("slug")
        }
        self._record_source(
            section="sleeve_inventory",
            label="strategy registry",
            path=registry_path_for_repo(self.repo_root),
            source_type="strategy_registry",
            trust_level="governance",
            as_of=None,
            used=True,
        )
        self._record_source(
            section="sleeve_inventory",
            label="sleeve manifest",
            path=manifest_path,
            source_type="sleeve_manifest",
            trust_level="governance" if isinstance(manifest, dict) else "missing",
            as_of=(manifest or {}).get("manifest_version") if isinstance(manifest, dict) else None,
            used=isinstance(manifest, dict),
        )

        rows: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for entry in registry.entries:
            if entry.strategy_type in {"benchmark", "reference_portfolio"}:
                continue
            manifest_row = manifest_by_strategy.get(entry.strategy_id)
            tracking = entry.shadow_tracking or {}
            metrics = shadow_metrics.get(entry.strategy_id) or {}
            lifecycle = self._lifecycle_stage(entry, manifest_row)
            counts[lifecycle] = counts.get(lifecycle, 0) + 1
            valid_days = metrics.get("valid_evaluation_days")
            checkpoints = []
            for checkpoint in tracking.get("review_checkpoints_trading_days") or []:
                try:
                    checkpoint_int = int(checkpoint)
                except Exception:
                    continue
                observed = int(valid_days or 0)
                checkpoints.append(
                    {
                        "trading_days": checkpoint_int,
                        "observed_days": observed,
                        "status": "READY_FOR_REVIEW" if observed >= checkpoint_int else "IN_PROGRESS",
                    }
                )
            artifact_required = bool(entry.active_in_shadow_tracking or entry.status in {"paper", "shadow"})
            artifact_status = "PRESENT" if metrics else "NOT_REQUIRED" if not artifact_required else "MISSING"
            promotion_readiness = metrics.get("promotion_readiness") or (
                "BASELINE" if entry.role == "baseline" else "RESEARCH" if lifecycle == "research" else "WATCHLIST"
            )
            evidence_eligible_for_promotion = bool(
                entry.eligible_for_promotion
                and artifact_status == "PRESENT"
                and not shadow_section.get("is_stale")
                and promotion_readiness == "PROMOTION_ELIGIBLE"
            )
            variant_class = (
                "alpha"
                if "alpha" in entry.strategy_id.lower() or "alpha" in entry.display_name.lower()
                else "baseline"
                if entry.role == "baseline"
                else "standard"
            )
            rows.append(
                {
                    "strategy_id": entry.strategy_id,
                    "sleeve_id": (manifest_row or {}).get("sleeve_id") or entry.compact_name(),
                    "display_name": entry.display_name,
                    "short_name": entry.compact_name(),
                    "strategy_type": entry.strategy_type,
                    "family": entry.family,
                    "lifecycle_stage": lifecycle,
                    "current_lifecycle_status": entry.status,
                    "manifest_lifecycle_stage": (manifest_row or {}).get("lifecycle_stage"),
                    "role": entry.role,
                    "variant_class": variant_class,
                    "baseline_strategy_id": tracking.get("baseline_strategy_id"),
                    "execution_impact": entry.execution_impact,
                    "eligible_for_shadow": entry.eligible_for_shadow,
                    "registry_eligible_for_promotion": entry.eligible_for_promotion,
                    "evidence_eligible_for_promotion": evidence_eligible_for_promotion,
                    "eligible_for_promotion": evidence_eligible_for_promotion,
                    "promotion_readiness": promotion_readiness,
                    "artifact_status": artifact_status,
                    "data_status": metrics.get("data_status") or ("NO_DATA" if artifact_status == "MISSING" else "OK" if artifact_status == "PRESENT" else "NOT_REQUIRED"),
                    "today_return": metrics.get("daily_return"),
                    "since_inception_return": metrics.get("cumulative_return"),
                    "drawdown": metrics.get("max_drawdown"),
                    "turnover": metrics.get("avg_turnover"),
                    "concentration": metrics.get("avg_top_3_concentration"),
                    "effective_n": metrics.get("avg_effective_n"),
                    "alpha_per_dollar_proxy": metrics.get("alpha_per_dollar_deployed_proxy"),
                    "construction": tracking.get("construction") or {},
                    "review_checkpoints": checkpoints,
                    "source_variant": tracking.get("source_variant"),
                }
            )

        missing_artifacts = [row["strategy_id"] for row in rows if row["artifact_status"] == "MISSING"]
        if missing_artifacts:
            self._mark(
                _check(
                    "sleeve_inventory_artifact_coverage",
                    "warn",
                    "non_blocking",
                    "One or more paper/shadow sleeves lack current shadow evaluation metrics.",
                    missing_strategies=missing_artifacts,
                )
            )
        else:
            self._mark(_check("sleeve_inventory_artifact_coverage", "pass", "non_blocking", "Registered sleeve inventory loaded."))

        return {
            "as_of": shadow_section.get("as_of"),
            "is_stale": bool(shadow_section.get("is_stale")),
            "status": "WARN" if missing_artifacts else "OK",
            "summary": {
                "total_registered": len(rows),
                "by_lifecycle_stage": counts,
                "alpha_variants": len([row for row in rows if row["variant_class"] == "alpha"]),
                "paper_or_live_capital_behavior_changed": False,
            },
            "rows": rows,
        }

    def _build_baseline_alpha_comparison(self, sleeve_inventory: dict[str, Any]) -> dict[str, Any]:
        rows_by_id = {
            str(row.get("strategy_id") or ""): row
            for row in sleeve_inventory.get("rows", [])
            if isinstance(row, dict)
        }
        pairs: list[dict[str, Any]] = []
        for alpha in sleeve_inventory.get("rows", []):
            if not isinstance(alpha, dict) or alpha.get("variant_class") != "alpha":
                continue
            baseline_id = str(alpha.get("baseline_strategy_id") or "").strip()
            baseline = rows_by_id.get(baseline_id)
            if not baseline:
                continue
            return_delta = (
                alpha.get("since_inception_return") - baseline.get("since_inception_return")
                if alpha.get("since_inception_return") is not None and baseline.get("since_inception_return") is not None
                else None
            )
            drawdown_delta = (
                alpha.get("drawdown") - baseline.get("drawdown")
                if alpha.get("drawdown") is not None and baseline.get("drawdown") is not None
                else None
            )
            pairs.append(
                {
                    "baseline_strategy_id": baseline_id,
                    "baseline_name": baseline.get("display_name"),
                    "alpha_strategy_id": alpha.get("strategy_id"),
                    "alpha_name": alpha.get("display_name"),
                    "baseline_return": baseline.get("since_inception_return"),
                    "alpha_return": alpha.get("since_inception_return"),
                    "return_delta": return_delta,
                    "baseline_drawdown": baseline.get("drawdown"),
                    "alpha_drawdown": alpha.get("drawdown"),
                    "drawdown_delta": drawdown_delta,
                    "baseline_turnover": baseline.get("turnover"),
                    "alpha_turnover": alpha.get("turnover"),
                    "baseline_concentration": baseline.get("concentration"),
                    "alpha_concentration": alpha.get("concentration"),
                    "baseline_effective_n": baseline.get("effective_n"),
                    "alpha_effective_n": alpha.get("effective_n"),
                    "baseline_alpha_per_dollar_proxy": baseline.get("alpha_per_dollar_proxy"),
                    "alpha_alpha_per_dollar_proxy": alpha.get("alpha_per_dollar_proxy"),
                    "review_checkpoints": alpha.get("review_checkpoints") or [],
                    "evidence_window_days": max(
                        [checkpoint.get("observed_days") or 0 for checkpoint in alpha.get("review_checkpoints") or []] or [0]
                    ),
                    "status": "IN_PROGRESS",
                }
            )
        return {
            "as_of": sleeve_inventory.get("as_of"),
            "is_stale": bool(sleeve_inventory.get("is_stale")),
            "status": "OK" if pairs else "NO_DATA",
            "summary": {
                "pair_count": len(pairs),
                "review_checkpoints": [20, 60],
            },
            "pairs": pairs,
        }

    def _build_account_layers(self, sections: dict[str, Any]) -> dict[str, Any]:
        nav = sections.get("nav", {})
        positions = sections.get("positions", {})
        live_pilot = sections.get("live_pilot", {})
        sleeve_inventory = sections.get("sleeve_inventory", {})
        lifecycle_counts = (sleeve_inventory.get("summary") or {}).get("by_lifecycle_stage") or {}
        rows = [
            {
                "layer": "Paper account",
                "status": "PAPER_OBSERVED",
                "cash": nav.get("cash"),
                "equity": nav.get("equity"),
                "buying_power": nav.get("buying_power"),
                "positions_count": (positions.get("summary") or {}).get("positions_count"),
                "source": "broker paper/account artifacts",
                "capital_behavior": "paper only",
            },
            {
                "layer": "Legacy FR-104 live-pilot account",
                "status": live_pilot.get("status") or "NO_DATA",
                "cash": (live_pilot.get("account") or {}).get("cash"),
                "equity": (live_pilot.get("account") or {}).get("equity"),
                "buying_power": (live_pilot.get("account") or {}).get("buying_power"),
                "positions_count": len(live_pilot.get("positions") or []),
                "source": live_pilot.get("run_root") or live_pilot.get("plan_path") or "outputs/live_pilot",
                "capital_behavior": "FR-104 capped pilot only",
            },
            {
                "layer": "Shadow/research sleeves",
                "status": "OBSERVED",
                "cash": None,
                "equity": None,
                "buying_power": None,
                "positions_count": lifecycle_counts.get("shadow", 0) + lifecycle_counts.get("research", 0),
                "source": "strategy registry + sleeve manifest + shadow artifacts",
                "capital_behavior": "non-capital shadow/research",
            },
        ]
        return {
            "as_of": self.generated_at.isoformat(),
            "is_stale": False,
            "status": "OK",
            "rows": rows,
        }

    def _build_governance_state(self, sections: dict[str, Any]) -> dict[str, Any]:
        live_pilot = sections.get("live_pilot", {})
        live_status = str(live_pilot.get("status") or "NO_DATA").upper()
        fr104_status = (
            "ACTIVE"
            if live_status in {"SUBMITTED", "DRY_RUN", "CLEAN"}
            else "READY"
            if live_status in {"READY_FOR_MANUAL_APPROVAL", "BLOCKED_NO_QUALIFYING_ORDER", "PLAN_ONLY"}
            else "UNKNOWN"
            if live_status == "NO_DATA"
            else "BLOCKED"
            if "BLOCKED" in live_status or "FAILED" in live_status
            else live_status
        )
        rows = [
            {
                "name": "FR-104 pilot evidence collection",
                "status": fr104_status,
                "pilot_blocking": False,
                "promotion_blocking": False,
                "production_scaling_blocking": False,
                "detail": "Level 2.5 capped live-pilot evidence can continue when approval, cap, account, market-hours, and reconciliation gates pass.",
            },
            {
                "name": "FR-068 decision-grade PIT membership",
                "status": "DEPENDENCY_BLOCKED",
                "pilot_blocking": False,
                "promotion_blocking": True,
                "production_scaling_blocking": True,
                "detail": "PIT date-effective large-cap membership authority remains unresolved; this blocks promotion and scaling, not FR-104 pilot evidence collection.",
            },
            {
                "name": "Shadow alpha promotion",
                "status": "BLOCKED",
                "pilot_blocking": False,
                "promotion_blocking": True,
                "production_scaling_blocking": True,
                "detail": "Polaris_Alpha and Orion_Alpha are SHADOW only until 20/60-day forward evidence and decision-grade PIT infrastructure are available.",
            },
            {
                "name": "Production allocator replacement",
                "status": "BLOCKED",
                "pilot_blocking": False,
                "promotion_blocking": True,
                "production_scaling_blocking": True,
                "detail": "No allocator, scheduler, broker, paper, live, or production behavior changes are authorized by dashboard reporting.",
            },
        ]
        return {
            "as_of": self.generated_at.isoformat(),
            "is_stale": False,
            "status": "OK",
            "summary": {
                "pilot_blocked": any(row["pilot_blocking"] for row in rows),
                "promotion_blocked": any(row["promotion_blocking"] for row in rows),
                "production_scaling_blocked": any(row["production_scaling_blocking"] for row in rows),
                "fr068_pilot_blocking": False,
            },
            "rows": rows,
        }

    def _latest_live_order(self, live_pilot: dict[str, Any]) -> dict[str, Any]:
        order = live_pilot.get("latest_submitted_order")
        if isinstance(order, dict):
            return order
        submitted = live_pilot.get("submitted_orders")
        if isinstance(submitted, list) and submitted and isinstance(submitted[-1], dict):
            return submitted[-1]
        selected = live_pilot.get("selected_order")
        return selected if isinstance(selected, dict) else {}

    def _live_order_status(self, order: dict[str, Any]) -> str | None:
        nested = order.get("order") if isinstance(order.get("order"), dict) else {}
        return str(order.get("status") or nested.get("status") or "").strip() or None

    def _is_live_order_open(self, order: dict[str, Any]) -> bool:
        status = str(self._live_order_status(order) or "").lower()
        if "." in status:
            status = status.rsplit(".", 1)[-1]
        return status in {
            "accepted",
            "accepted_for_bidding",
            "new",
            "open",
            "partially_filled",
            "pending_cancel",
            "pending_new",
            "pending_replace",
        }

    def _live_pilot_state(self, live_pilot: dict[str, Any]) -> str:
        status = str(live_pilot.get("status") or "NO_DATA").upper()
        metrics = live_pilot.get("metrics") or {}
        order = self._latest_live_order(live_pilot)
        if "BLOCKED" in status or "FAILED" in status or status in {"REJECTED", "ERROR"}:
            return "BLOCKED"
        if (
            status in {"SUBMITTED", "CLEAN"}
            or self._is_live_order_open(order)
            or (metrics.get("filled_count") or 0)
            or len(live_pilot.get("positions") or []) > 0
        ):
            return "ACTIVE"
        if status in {"READY_FOR_MANUAL_APPROVAL", "PLAN_ONLY", "NO_DATA", "UNKNOWN", "BLOCKED_NO_QUALIFYING_ORDER"}:
            return "IDLE"
        return "IDLE"

    def _deployed_pct(self, live_pilot: dict[str, Any]) -> float | None:
        metrics = live_pilot.get("metrics") or {}
        explicit = _to_float(metrics.get("cash_deployment_rate"))
        if explicit is not None:
            return explicit
        account = live_pilot.get("account") or {}
        equity = _to_float(account.get("equity") or account.get("portfolio_value"))
        cash = _to_float(account.get("cash"))
        if equity in (None, 0) or cash is None:
            return None
        return max(0.0, min(1.0, (equity - cash) / equity))

    def _operator_actions(self, sections: dict[str, Any], validation_status: dict[str, int]) -> list[dict[str, Any]]:
        live_pilot = sections.get("live_pilot", {})
        metrics = live_pilot.get("metrics") or {}
        governance = sections.get("governance_state", {})
        shadow = sections.get("shadow_command_center", {})
        decision_grade = sections.get("decision_grade", {})
        order = self._latest_live_order(live_pilot)
        live_state = self._live_pilot_state(live_pilot)
        deployed_pct = self._deployed_pct(live_pilot)
        actions: list[dict[str, Any]] = []

        if validation_status["blocking_failures"]:
            actions.append(
                {
                    "title": "Dashboard validation failed",
                    "status": "BLOCKED",
                    "severity": "critical",
                    "detail": f"{validation_status['blocking_failures']} blocking validation checks failed.",
                    "expected_artifact": "validation.checks",
                    "blocks_pilot": True,
                    "operator_action": "Review the validation tape before relying on the dashboard.",
                }
            )

        if self._is_live_order_open(order):
            actions.append(
                {
                    "title": "Live pilot order open",
                    "status": "ACTION_REQUIRED",
                    "severity": "action",
                    "detail": "Latest FR-104 live-pilot order is still open or pending broker terminal state.",
                    "expected_artifact": live_pilot.get("run_root") or "outputs/live_pilot/runs/<run_id>/",
                    "blocks_pilot": False,
                    "operator_action": "Monitor broker truth and do not submit duplicate exposure.",
                }
            )
        elif live_state == "BLOCKED":
            actions.append(
                {
                    "title": "Legacy FR-104 live pilot blocked",
                    "status": "ACTION_REQUIRED",
                    "severity": "action",
                    "detail": str(metrics.get("idle_cash_reason") or live_pilot.get("status") or "Live-pilot path is blocked."),
                    "expected_artifact": live_pilot.get("plan_path") or "outputs/live_pilot/plans/live_pilot_plan_<date>.json",
                    "blocks_pilot": True,
                    "operator_action": "Resolve the live-pilot reason code before the next manual attempt.",
                }
            )
        elif live_state == "IDLE" and deployed_pct in (None, 0.0):
            actions.append(
                {
                    "title": "Legacy FR-104 live pilot cash idle",
                    "status": "WATCH",
                    "severity": "watch",
                    "detail": str(metrics.get("idle_cash_reason") or "No legacy FR-104 live-pilot capital is currently deployed; this does not describe the separate Lyra Live portfolio."),
                    "expected_artifact": live_pilot.get("plan_path") or "outputs/live_pilot/plans/live_pilot_plan_<date>.json",
                    "blocks_pilot": False,
                    "operator_action": "Check whether a qualifying manually approved FR-104 order exists today.",
                }
            )

        if shadow.get("is_stale"):
            actions.append(
                {
                    "title": "Shadow NAV stale",
                    "status": "WATCH",
                    "severity": "watch",
                    "detail": "Shadow NAV latest date lags the latest shadow evaluation date.",
                    "expected_artifact": "outputs/shadow_candidates/performance/shadow_nav_series.csv",
                    "blocks_pilot": False,
                    "operator_action": "Refresh shadow scorecard artifacts before judging alpha-vs-baseline evidence.",
                }
            )

        if (governance.get("summary") or {}).get("fr068_pilot_blocking") is False:
            actions.append(
                {
                    "title": "FR-068 blocked but not pilot-blocking",
                    "status": "INFO",
                    "severity": "info",
                    "detail": "PIT date-effective membership remains a promotion/scaling blocker only.",
                    "expected_artifact": "reports/fr068_requirement_replacement_remediation_2026-06-23.md",
                    "blocks_pilot": False,
                    "operator_action": "Continue FR-104 evidence collection; do not promote or scale.",
                }
            )

        if decision_grade.get("status") in {"PARTIAL", "BLOCKED"}:
            actions.append(
                {
                    "title": "Decision-grade evidence incomplete",
                    "status": str(decision_grade.get("status") or "PARTIAL"),
                    "severity": "watch",
                    "detail": ", ".join(decision_grade.get("reason_codes") or []) or "Decision-grade artifacts are incomplete.",
                    "expected_artifact": "outputs/model_quality/<date>/",
                    "blocks_pilot": False,
                    "operator_action": "Use this as a promotion-readiness warning, not a pilot stop.",
                }
            )

        if not actions:
            actions.append(
                {
                    "title": "None",
                    "status": "OK",
                    "severity": "info",
                    "detail": "No operator action is required from current dashboard artifacts.",
                    "expected_artifact": "",
                    "blocks_pilot": False,
                    "operator_action": "Continue monitoring.",
                }
            )
        return actions

    def _build_operator_control_tower(self, sections: dict[str, Any]) -> dict[str, Any]:
        nav = sections.get("nav", {})
        live_pilot = sections.get("live_pilot", {})
        sleeve_inventory = sections.get("sleeve_inventory", {})
        alpha = sections.get("baseline_alpha_comparison", {})
        governance = sections.get("governance_state", {})
        metrics = live_pilot.get("metrics") or {}
        order = self._latest_live_order(live_pilot)
        nested_order = order.get("order") if isinstance(order.get("order"), dict) else {}
        validation_status = {
            "blocking_failures": sum(1 for check in self.checks if check["severity"] == "blocking" and check["status"] == "fail"),
            "warnings": sum(1 for check in self.checks if check["status"] == "warn"),
            "total_checks": len(self.checks),
        }
        actions = self._operator_actions(sections, validation_status)
        live_state = self._live_pilot_state(live_pilot)
        deployed_pct = self._deployed_pct(live_pilot)
        lifecycle_counts = (sleeve_inventory.get("summary") or {}).get("by_lifecycle_stage") or {}
        status_level = "ERROR" if validation_status["blocking_failures"] else "WARNING" if validation_status["warnings"] else "OK"
        action_required = any(action.get("severity") in {"critical", "action"} for action in actions)

        latest_order = {
            "ticker": order.get("symbol") or order.get("ticker") or nested_order.get("symbol"),
            "side": order.get("side") or nested_order.get("side"),
            "qty": _to_float(order.get("qty") or order.get("shares") or nested_order.get("qty")),
            "order_type": order.get("submitted_order_type") or order.get("order_type") or nested_order.get("type"),
            "status": self._live_order_status(order),
            "filled_qty": _to_float(order.get("filled_qty") or nested_order.get("filled_qty")),
            "expected_price": _to_float(order.get("expected_price") or order.get("cap_enforcement_price") or order.get("limit_price")),
            "fill_price": _to_float(order.get("fill_price") or nested_order.get("filled_avg_price")),
        }

        cards = [
            {
                "id": "paper_nav",
                "label": "Paper NAV / Return",
                "value": nav.get("equity"),
                "value_format": "money",
                "detail": f"Day return {nav.get('day_return') if nav.get('day_return') is not None else 'unavailable'}",
                "status": "OK" if nav.get("equity") is not None else "NO_DATA",
            },
            {
                "id": "live_capital",
                "label": "Legacy FR-104 Capital",
                "value": deployed_pct,
                "value_format": "percent",
                "detail": f"Cash {live_pilot.get('account', {}).get('cash')} · Equity {live_pilot.get('account', {}).get('equity')}",
                "status": live_state,
            },
            {
                "id": "latest_order",
                "label": "Latest FR-104 Order",
                "value": latest_order.get("status"),
                "value_format": "text",
                "detail": f"{latest_order.get('ticker') or '—'} {latest_order.get('side') or ''} {latest_order.get('qty') or '—'} {latest_order.get('order_type') or ''}".strip(),
                "status": latest_order.get("status") or "NO_DATA",
            },
            {
                "id": "sleeves",
                "label": "Sleeves by Lifecycle",
                "value": (sleeve_inventory.get("summary") or {}).get("total_registered"),
                "value_format": "integer",
                "detail": " · ".join(f"{key} {value}" for key, value in sorted(lifecycle_counts.items())) or "registry unavailable",
                "status": sleeve_inventory.get("status") or "NO_DATA",
            },
            {
                "id": "validation",
                "label": "Validation Status",
                "value": status_level,
                "value_format": "text",
                "detail": f"{validation_status['blocking_failures']} fail · {validation_status['warnings']} warn",
                "status": status_level,
            },
            {
                "id": "operator_action",
                "label": "Operator Action",
                "value": "REQUIRED" if action_required else "NONE",
                "value_format": "text",
                "detail": actions[0].get("title") if actions else "None",
                "status": "ACTION_REQUIRED" if action_required else "OK",
            },
        ]

        return {
            "as_of": self.generated_at.isoformat(),
            "is_stale": False,
            "status": "ACTION_REQUIRED" if action_required else status_level,
            "summary": {
                "live_pilot_state": live_state,
                "live_pilot_deployed_pct": deployed_pct,
                "live_pilot_open_orders": metrics.get("open_order_count"),
                "latest_order_status": latest_order.get("status"),
                "operator_action_required": action_required,
                "primary_action": actions[0].get("title") if actions else "None",
                "sleeve_count_by_lifecycle": lifecycle_counts,
                "alpha_pair_count": (alpha.get("summary") or {}).get("pair_count"),
                "validation_status": status_level,
                "fr068_pilot_blocking": (governance.get("summary") or {}).get("fr068_pilot_blocking"),
            },
            "cards": cards,
            "latest_order": latest_order,
            "operator_actions": actions,
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

    def _build_edge_attribution(
        self,
        *,
        positions: dict[str, Any],
        nav: dict[str, Any],
        performance: dict[str, Any],
    ) -> dict[str, Any]:
        """Explain whether benchmark drag is signal or portfolio-expression drag.

        This section is diagnostic. It does not change targets or authorize an
        order. Current target fidelity is computed directly from the paper plan
        and authoritative broker weights; realized intended-vs-actual drag is
        loaded from the existing read-only operational-drag pipeline.
        """
        plan_path = (
            self.repo_root
            / "outputs"
            / "paper_lane"
            / "plans"
            / f"live_pilot_plan_{self.report_date}.json"
        )
        plan = _read_json(plan_path)
        plan = plan if isinstance(plan, dict) else {}
        self._record_source(
            section="edge_attribution",
            label="paper target portfolio",
            path=plan_path,
            source_type="paper_transition_plan",
            trust_level="canonical",
            as_of=str(plan.get("trade_date") or "") or None,
            used=bool(plan),
        )

        target_rows = plan.get("target_portfolio") if isinstance(plan.get("target_portfolio"), list) else []
        target_weights: dict[str, float] = {}
        for row in target_rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
            weight = _to_float(row.get("target_weight"))
            if symbol and weight is not None and weight >= 0.0:
                target_weights[symbol] = target_weights.get(symbol, 0.0) + float(weight)

        actual_rows = positions.get("rows") if isinstance(positions.get("rows"), list) else []
        actual_weights: dict[str, float] = {}
        quantities: dict[str, float] = {}
        for row in actual_rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
            weight = _to_float(row.get("weight"))
            qty = _to_float(row.get("qty") or row.get("quantity"))
            if symbol and weight is not None:
                actual_weights[symbol] = actual_weights.get(symbol, 0.0) + float(weight)
            if symbol and qty is not None:
                quantities[symbol] = quantities.get(symbol, 0.0) + float(qty)

        target_weight_sum = sum(target_weights.values())
        actual_target_weight = sum(actual_weights.get(symbol, 0.0) for symbol in target_weights)
        matched_target_weight = sum(
            min(actual_weights.get(symbol, 0.0), target_weight)
            for symbol, target_weight in target_weights.items()
        )
        off_target_weight = sum(
            weight for symbol, weight in actual_weights.items() if symbol not in target_weights
        )
        target_cash_weight = _to_float(plan.get("cash_target_weight"))
        actual_cash_weight = (
            float(nav["cash"]) / float(nav["equity"])
            if nav.get("cash") is not None and nav.get("equity") not in (None, 0)
            else None
        )
        name_gap = sum(
            abs(actual_weights.get(symbol, 0.0) - target_weights.get(symbol, 0.0))
            for symbol in set(actual_weights) | set(target_weights)
        )
        cash_gap = (
            abs(actual_cash_weight - target_cash_weight)
            if actual_cash_weight is not None and target_cash_weight is not None
            else None
        )
        total_abs_gap = name_gap + (cash_gap or 0.0)
        missing_targets = sorted(
            symbol for symbol, weight in target_weights.items()
            if weight > 0.0 and actual_weights.get(symbol, 0.0) < 0.001
        )
        off_target_fractional = sorted(
            symbol for symbol, qty in quantities.items()
            if symbol not in target_weights and abs(qty - round(qty)) > 1e-9
        )

        run_candidates = sorted(
            (self.repo_root / "outputs" / "paper_lane" / "runs").glob(
                f"{self.report_date}T*_paper_cron_submit"
            )
        )
        run_root = run_candidates[-1] if run_candidates else None
        execution_path = run_root / "execution_results.json" if run_root else None
        intended_path = run_root / "live_pilot_orders_intended.json" if run_root else None
        execution = _read_json(execution_path) if execution_path else None
        intended = _read_json(intended_path) if intended_path else None
        execution = execution if isinstance(execution, dict) else {}
        intended = intended if isinstance(intended, dict) else {}
        self._record_source(
            section="edge_attribution",
            label="paper execution outcome",
            path=execution_path,
            source_type="paper_execution_results",
            trust_level="canonical",
            as_of=self.report_date if execution else None,
            used=bool(execution),
        )

        drag_dir = self.repo_root / "outputs" / "operational_drag" / self.report_date
        drag_path = drag_dir / "operational_drag.json"
        drag_payload = _read_json(drag_path)
        drag_payload = drag_payload if isinstance(drag_payload, dict) else {}
        drag_latest = drag_payload.get("latest") if isinstance(drag_payload.get("latest"), dict) else {}
        self._record_source(
            section="edge_attribution",
            label="intended versus actual return attribution",
            path=drag_path,
            source_type="operational_drag",
            trust_level="diagnostic",
            as_of=str(drag_latest.get("date") or "") or None,
            used=bool(drag_payload),
        )

        nav_values = [
            _to_float(row.get("equity"))
            for row in performance.get("_nav_rows") or []
            if _to_float(row.get("equity")) is not None
        ]
        bench_values = [
            _to_float(row.get("spy_close"))
            for row in performance.get("_bench_rows") or []
            if _to_float(row.get("spy_close")) is not None
        ]
        rolling_20d_port = self._compute_window_return(nav_values, 20)
        rolling_20d_spy = self._compute_window_return(bench_values, 20)
        rolling_20d_excess = (
            rolling_20d_port - rolling_20d_spy
            if rolling_20d_port is not None and rolling_20d_spy is not None
            else None
        )
        fidelity_blocked = bool(target_weights) and (
            off_target_weight > 0.05 or total_abs_gap > 0.10 or bool(missing_targets)
        )
        signal_weakness = rolling_20d_excess is not None and rolling_20d_excess < 0.0
        classification = (
            "MIXED_SIGNAL_AND_PORTFOLIO_DRAG"
            if signal_weakness and fidelity_blocked
            else "PORTFOLIO_FIDELITY_DRAG"
            if fidelity_blocked
            else "RECENT_SIGNAL_WEAKNESS"
            if signal_weakness
            else "NO_CURRENT_EDGE_BLOCKER_IDENTIFIED"
        )
        if fidelity_blocked:
            self._mark(
                _check(
                    "paper_target_fidelity",
                    "warn",
                    "non_blocking",
                    "Paper broker holdings materially differ from the current target portfolio.",
                )
            )

        return {
            "as_of": self.report_date,
            "is_stale": not bool(plan) or performance.get("as_of") != self.report_date,
            "status": "WATCH" if fidelity_blocked or signal_weakness else "OK",
            "classification": classification,
            "performance": {
                "rolling_20d_portfolio_return": rolling_20d_port,
                "rolling_20d_spy_return": rolling_20d_spy,
                "rolling_20d_excess_return": rolling_20d_excess,
                "since_inception_excess_return": (performance.get("summary") or {}).get(
                    "excess_since_inception_return"
                ),
            },
            "target_fidelity": {
                "target_name_count": len(target_weights),
                "actual_position_count": len(actual_weights),
                "target_weight_sum": target_weight_sum,
                "actual_weight_in_target_names": actual_target_weight,
                "matched_target_weight": matched_target_weight,
                "target_attainment_ratio": (
                    matched_target_weight / target_weight_sum if target_weight_sum > 0.0 else None
                ),
                "off_target_weight": off_target_weight,
                "target_cash_weight": target_cash_weight,
                "actual_cash_weight": actual_cash_weight,
                "total_absolute_weight_gap": total_abs_gap,
                "missing_target_symbols": missing_targets,
                "off_target_fractional_symbols": off_target_fractional,
            },
            "execution": {
                "run_id": execution.get("run_id"),
                "status": execution.get("status"),
                "reason": execution.get("reason"),
                "intended_count": len(intended.get("orders") or []),
                "submitted_count": execution.get("submitted_count"),
                "filled_count": execution.get("filled_count"),
                "suppressed_buy_count": execution.get(
                    "remaining_blocked_or_suppressed_buy_count"
                ),
            },
            "operational_drag": {
                "available": bool(drag_payload.get("available")),
                "confidence": drag_payload.get("confidence"),
                "date": drag_latest.get("date"),
                "daily": drag_latest.get("daily_operational_drag"),
                "cumulative": drag_latest.get("cumulative_operational_drag"),
                "reason_codes": drag_payload.get("reason_codes") or [],
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
        live_pilot_section = self._build_live_pilot_status()
        sleeve_inventory_section = self._build_sleeve_inventory(shadow_section)
        baseline_alpha_section = self._build_baseline_alpha_comparison(sleeve_inventory_section)
        sections = {
            "positions": positions_section,
            "nav": nav_section,
            "trades_today": trades_section,
            "performance_history": performance_section,
            "shadow_command_center": shadow_section,
            "regime_market_state": regime_section,
            "live_pilot": live_pilot_section,
            "sleeve_inventory": sleeve_inventory_section,
            "baseline_alpha_comparison": baseline_alpha_section,
        }
        sections["edge_attribution"] = self._build_edge_attribution(
            positions=positions_section,
            nav=nav_section,
            performance=performance_section,
        )
        account_layers_section = self._build_account_layers(sections)
        sections["account_layers"] = account_layers_section
        governance_section = self._build_governance_state(sections)
        sections["governance_state"] = governance_section
        decision_section = self._build_daily_decision_intelligence(sections)
        sections["daily_decision_intelligence"] = decision_section
        system_health_section = self._build_system_health_console(sections)
        sections["system_health_console"] = system_health_section
        live_readiness_section = self._build_live_readiness(sections)
        sections["live_readiness"] = live_readiness_section
        decision_grade_section = self._build_decision_grade()
        sections["decision_grade"] = decision_grade_section
        self._freshness_checks(sections)
        operator_control_section = self._build_operator_control_tower(sections)
        sections["operator_control_tower"] = operator_control_section
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
    parser.add_argument("--date", dest="report_date", default=None)
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
