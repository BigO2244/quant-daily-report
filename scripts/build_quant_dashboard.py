from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass
class SourceRecord:
    path: str
    status: str


class DashboardBuilder:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.missing_files: list[str] = []
        self.warnings: list[str] = []
        self.sources: list[SourceRecord] = []
        self.degraded_metrics: list[str] = []

    def _abs(self, rel: str) -> Path:
        return self.repo_root / rel

    def _record_source(self, rel_path: str, exists: bool, used: bool = False) -> Path:
        path = self._abs(rel_path)
        if exists:
            self.sources.append(SourceRecord(rel_path, "used" if used else "present"))
        else:
            self.sources.append(SourceRecord(rel_path, "missing"))
            self.missing_files.append(rel_path)
        return path

    def _read_json(self, rel_path: str, required: bool = False, used: bool = True) -> dict[str, Any] | list[Any] | None:
        path = self._abs(rel_path)
        exists = path.exists()
        self._record_source(rel_path, exists=exists, used=used and exists)
        if not exists:
            if required:
                self.warnings.append(f"Missing required JSON artifact: {rel_path}")
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.warnings.append(f"Failed to parse JSON {rel_path}: {exc}")
            return None

    def _read_csv(self, rel_path: str, required: bool = False, used: bool = True) -> pd.DataFrame | None:
        path = self._abs(rel_path)
        exists = path.exists()
        self._record_source(rel_path, exists=exists, used=used and exists)
        if not exists:
            if required:
                self.warnings.append(f"Missing required CSV artifact: {rel_path}")
            return None
        try:
            return pd.read_csv(path)
        except Exception as exc:
            self.warnings.append(f"Failed to parse CSV {rel_path}: {exc}")
            return None

    def _find_latest_execution_payload(self, report_date: str | None) -> tuple[str | None, dict[str, Any] | None]:
        exec_dir = self._abs("outputs/execution_email")
        if not exec_dir.exists():
            self._record_source("outputs/execution_email", exists=False)
            self.warnings.append("Execution payload directory not found.")
            return None, None

        if report_date:
            candidate = f"outputs/execution_email/{report_date}.json"
            payload = self._read_json(candidate, required=False, used=True)
            if isinstance(payload, dict):
                return candidate, payload

        files = sorted(exec_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for file in files:
            name = file.name
            if ".empty." in name:
                continue
            rel = str(file.relative_to(self.repo_root))
            payload = self._read_json(rel, required=False, used=True)
            if isinstance(payload, dict):
                return rel, payload

        self.warnings.append("No usable execution payload JSON found.")
        return None, None

    def _find_latest_health_integrity(self, run_id: str | None, report_date: str | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None, str | None]:
        if not run_id or not report_date:
            return None, None, None, None

        run_root = self._abs(f"outputs/runs/{run_id}/snapshots")
        health_rel = f"outputs/runs/{run_id}/snapshots/health_{report_date}.json"
        integrity_rel = f"outputs/runs/{run_id}/snapshots/integrity_{report_date}.json"

        health = self._read_json(health_rel, required=False, used=True)
        integrity = self._read_json(integrity_rel, required=False, used=True)

        if isinstance(health, dict) or isinstance(integrity, dict):
            return (
                health if isinstance(health, dict) else None,
                integrity if isinstance(integrity, dict) else None,
                health_rel if isinstance(health, dict) else None,
                integrity_rel if isinstance(integrity, dict) else None,
            )

        if not run_root.exists():
            self.warnings.append(f"Run snapshot folder missing for run_id={run_id}")
            return None, None, None, None

        health_files = sorted(run_root.glob("health_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        integrity_files = sorted(run_root.glob("integrity_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

        health_obj = None
        integrity_obj = None
        health_used = None
        integrity_used = None

        if health_files:
            rel = str(health_files[0].relative_to(self.repo_root))
            parsed = self._read_json(rel, required=False, used=True)
            if isinstance(parsed, dict):
                health_obj = parsed
                health_used = rel

        if integrity_files:
            rel = str(integrity_files[0].relative_to(self.repo_root))
            parsed = self._read_json(rel, required=False, used=True)
            if isinstance(parsed, dict):
                integrity_obj = parsed
                integrity_used = rel

        return health_obj, integrity_obj, health_used, integrity_used

    @staticmethod
    def _coerce_num(v: Any) -> float | None:
        try:
            if v is None or (isinstance(v, str) and not v.strip()):
                return None
            n = float(v)
            return n if pd.notna(n) else None
        except Exception:
            return None

    @staticmethod
    def _safe_iso_now() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _to_series(df: pd.DataFrame | None, date_col: str, value_col: str) -> list[dict[str, Any]]:
        if df is None or date_col not in df.columns or value_col not in df.columns:
            return []
        out: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            date = str(row.get(date_col, "")).strip()
            value = DashboardBuilder._coerce_num(row.get(value_col))
            if date and value is not None:
                out.append({"date": date, "value": value})
        return out

    @staticmethod
    def _calc_drawdown(nav_series: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not nav_series:
            return []
        peak = None
        out: list[dict[str, Any]] = []
        for p in nav_series:
            value = p.get("value")
            if value is None:
                continue
            if peak is None or value > peak:
                peak = value
            dd = 0.0 if not peak else (value / peak) - 1.0
            out.append({"date": p.get("date"), "value": dd})
        return out

    @staticmethod
    def _cumulative_return(returns: pd.Series) -> float | None:
        if returns.empty:
            return None
        returns = returns.dropna()
        if returns.empty:
            return None
        prod_val = DashboardBuilder._coerce_num((1.0 + returns).prod())
        if prod_val is None:
            return None
        return float(prod_val - 1.0)

    @staticmethod
    def _returns_from_series(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(points) < 2:
            return []
        out: list[dict[str, Any]] = []
        prev = None
        for p in points:
            value = DashboardBuilder._coerce_num(p.get("value"))
            date = str(p.get("date") or "").strip()
            if value is None or not date:
                continue
            if prev is not None and prev != 0:
                out.append({"date": date, "value": (value / prev) - 1.0})
            prev = value
        return out

    def _mark_degraded(self, metric_name: str, reason: str) -> None:
        self.degraded_metrics.append(f"{metric_name}: {reason}")

    def build(self) -> dict[str, Any]:
        latest = self._read_json("outputs/latest.json", required=False, used=True)
        latest_obj = latest if isinstance(latest, dict) else {}

        report_date = str(latest_obj.get("report_date") or "").strip() or None
        run_id = str(latest_obj.get("run_id") or "").strip() or None
        mode = str(latest_obj.get("mode") or "").strip().upper() or "UNKNOWN"

        canonical_df = self._read_csv("outputs/alpha_assessment/canonical_performance.csv", required=False, used=True)
        nav_df = self._read_csv("outputs/perf/nav_timeseries.csv", required=False, used=True)
        benchmark_df = self._read_csv("outputs/perf/benchmark_close_history.csv", required=False, used=True)
        vix_df = self._read_csv("outputs/perf/vix_close_history.csv", required=False, used=True)
        trades_df = self._read_csv("outputs/ledger/trades.csv", required=False, used=True)

        _, execution_payload = self._find_latest_execution_payload(report_date)
        exec_payload_obj: dict[str, Any] = execution_payload if isinstance(execution_payload, dict) else {}
        health_obj, integrity_obj, _, _ = self._find_latest_health_integrity(run_id, report_date)

        canonical_positions_path = "canonical-model-snapshot/canonical_positions.json"
        canonical_positions = self._read_json(canonical_positions_path, required=False, used=True)
        positions_obj = canonical_positions if isinstance(canonical_positions, dict) else {}

        # Normalize canonical numeric columns if present.
        if canonical_df is not None and not canonical_df.empty:
            for col in [
                "strategy_nav",
                "strategy_return",
                "spy_close",
                "spy_return",
                "excess_return",
                "vix_close",
                "cash_weight",
                "gross_exposure",
                "turnover",
                "holdings_count",
            ]:
                if col in canonical_df.columns:
                    canonical_df[col] = pd.to_numeric(canonical_df[col], errors="coerce")
            if "date" in canonical_df.columns:
                canonical_df["date"] = pd.to_datetime(canonical_df["date"], errors="coerce").dt.strftime("%Y-%m-%d")

        if nav_df is not None and not nav_df.empty:
            for col in ["equity", "cash", "gross_exposure", "net_exposure", "return_1d", "turnover"]:
                if col in nav_df.columns:
                    nav_df[col] = pd.to_numeric(nav_df[col], errors="coerce")
            if "date" in nav_df.columns:
                nav_df["date"] = pd.to_datetime(nav_df["date"], errors="coerce").dt.strftime("%Y-%m-%d")

        if benchmark_df is not None and not benchmark_df.empty:
            for col in ["spy_close", "spy_return"]:
                if col in benchmark_df.columns:
                    benchmark_df[col] = pd.to_numeric(benchmark_df[col], errors="coerce")
            if "date" in benchmark_df.columns:
                benchmark_df["date"] = pd.to_datetime(benchmark_df["date"], errors="coerce").dt.strftime("%Y-%m-%d")

        if vix_df is not None and not vix_df.empty and "vix_close" in vix_df.columns:
            vix_df["vix_close"] = pd.to_numeric(vix_df["vix_close"], errors="coerce")
            if "date" in vix_df.columns:
                vix_df["date"] = pd.to_datetime(vix_df["date"], errors="coerce").dt.strftime("%Y-%m-%d")

        nav_series = []
        benchmark_series = []
        daily_returns_series = []
        excess_returns_series = []

        if canonical_df is not None and not canonical_df.empty:
            nav_series = self._to_series(canonical_df, "date", "strategy_nav")
            benchmark_series = self._to_series(canonical_df, "date", "spy_close")
            daily_returns_series = self._to_series(canonical_df, "date", "strategy_return")
            excess_returns_series = self._to_series(canonical_df, "date", "excess_return")

        if not nav_series and nav_df is not None and not nav_df.empty:
            nav_series = self._to_series(nav_df, "date", "equity")

        if not benchmark_series and benchmark_df is not None and not benchmark_df.empty:
            benchmark_series = self._to_series(benchmark_df, "date", "spy_close")

        if not daily_returns_series and nav_df is not None and not nav_df.empty and "return_1d" in nav_df.columns:
            daily_returns_series = self._to_series(nav_df, "date", "return_1d")

        drawdown_series = self._calc_drawdown(nav_series)

        if not daily_returns_series:
            daily_returns_series = self._returns_from_series(nav_series)
            if daily_returns_series:
                self.warnings.append("Derived strategy daily returns from NAV series due to missing canonical strategy_return values.")

        benchmark_returns_series = self._to_series(canonical_df, "date", "spy_return") if canonical_df is not None else []
        if not benchmark_returns_series and benchmark_df is not None and not benchmark_df.empty:
            benchmark_returns_series = self._to_series(benchmark_df, "date", "spy_return")
        if not benchmark_returns_series:
            benchmark_returns_series = self._returns_from_series(benchmark_series)

        # Latest row extraction for KPI-level metrics.
        latest_nav = nav_series[-1]["value"] if nav_series else None
        prev_nav = nav_series[-2]["value"] if len(nav_series) > 1 else None
        daily_pl = (latest_nav - prev_nav) if (latest_nav is not None and prev_nav is not None) else None

        latest_daily_return = daily_returns_series[-1]["value"] if daily_returns_series else None
        if latest_daily_return is None and nav_series and len(nav_series) >= 2:
            base = nav_series[-2]["value"]
            if base:
                latest_daily_return = (nav_series[-1]["value"] / base) - 1.0

        if daily_pl is None and latest_nav is not None and latest_daily_return is not None and (1.0 + latest_daily_return) != 0:
            prev_est = latest_nav / (1.0 + latest_daily_return)
            daily_pl = latest_nav - prev_est
            self.warnings.append("Estimated daily P/L from latest NAV and daily return due to missing prior NAV point.")

        benchmark_return = None
        if benchmark_returns_series:
            benchmark_return = benchmark_returns_series[-1]["value"]

        if canonical_df is not None and "excess_return" in canonical_df.columns and not canonical_df["excess_return"].dropna().empty:
            excess_return = float(canonical_df["excess_return"].dropna().iloc[-1])
        elif latest_daily_return is not None and benchmark_return is not None:
            excess_return = float(latest_daily_return - benchmark_return)
        else:
            excess_return = None

        holdings = None
        if canonical_df is not None and "holdings_count" in canonical_df.columns and not canonical_df["holdings_count"].dropna().empty:
            holdings = int(float(canonical_df["holdings_count"].dropna().iloc[-1]))
        elif isinstance(positions_obj.get("position_count"), (int, float)):
            position_count = positions_obj.get("position_count")
            if position_count is not None:
                holdings = int(position_count)

        turnover = None
        if canonical_df is not None and "turnover" in canonical_df.columns and not canonical_df["turnover"].dropna().empty:
            turnover = float(canonical_df["turnover"].dropna().iloc[-1])
        elif nav_df is not None and "turnover" in nav_df.columns and not nav_df["turnover"].dropna().empty:
            turnover = float(nav_df["turnover"].dropna().iloc[-1])

        execution_status = "UNKNOWN"
        if exec_payload_obj:
            execution_status = str(exec_payload_obj.get("execution_status") or exec_payload_obj.get("status_label") or "UNKNOWN").upper()
        elif isinstance(health_obj, dict):
            execution_status = str(health_obj.get("status") or "UNKNOWN").upper()

        # Performance summary metrics.
        mtd = qtd = si = si_alpha = best_day = worst_day = None
        current_drawdown = drawdown_series[-1]["value"] if drawdown_series else None

        if current_drawdown is None:
            self._mark_degraded("current_drawdown", "insufficient NAV history")

        if canonical_df is not None and not canonical_df.empty and "date" in canonical_df.columns:
            work = canonical_df.copy()
            work["date_dt"] = pd.to_datetime(work["date"], errors="coerce")
            work = work.dropna(subset=["date_dt"]).sort_values("date_dt")

            if "strategy_return" in work.columns:
                sr = pd.to_numeric(work["strategy_return"], errors="coerce")
                if not sr.dropna().empty:
                    last_dt = work["date_dt"].max()
                    month_mask = (work["date_dt"].dt.year == last_dt.year) & (work["date_dt"].dt.month == last_dt.month)
                    quarter = ((last_dt.month - 1) // 3) + 1
                    q_mask = (work["date_dt"].dt.year == last_dt.year) & ((((work["date_dt"].dt.month - 1) // 3) + 1) == quarter)

                    mtd = self._cumulative_return(sr[month_mask])
                    qtd = self._cumulative_return(sr[q_mask])
                    si = self._cumulative_return(sr)
                    best_day = float(sr.dropna().max()) if not sr.dropna().empty else None
                    worst_day = float(sr.dropna().min()) if not sr.dropna().empty else None

            if "spy_return" in work.columns and "strategy_return" in work.columns:
                sr = pd.to_numeric(work["strategy_return"], errors="coerce")
                br = pd.to_numeric(work["spy_return"], errors="coerce")
                aligned = pd.DataFrame({"sr": sr, "br": br}).dropna()
                if not aligned.empty:
                    strat_prod = self._coerce_num((1.0 + aligned["sr"]).prod())
                    bench_prod = self._coerce_num((1.0 + aligned["br"]).prod())
                    if strat_prod is not None and bench_prod is not None:
                        si_alpha = float((strat_prod - 1.0) - (bench_prod - 1.0))

        # Fallback for return metrics when canonical strategy_return is sparse.
        if si is None and daily_returns_series:
            daily_ser = pd.Series([x["value"] for x in daily_returns_series], dtype=float)
            si = self._cumulative_return(daily_ser)
            if si is not None:
                self.warnings.append("Derived since-inception return from fallback daily return series.")

        if mtd is None and daily_returns_series:
            dr_df = pd.DataFrame(daily_returns_series)
            dr_df["date_dt"] = pd.to_datetime(dr_df["date"], errors="coerce")
            dr_df = dr_df.dropna(subset=["date_dt"]).sort_values("date_dt")
            if not dr_df.empty:
                last_dt = dr_df["date_dt"].iloc[-1]
                mask = (dr_df["date_dt"].dt.year == last_dt.year) & (dr_df["date_dt"].dt.month == last_dt.month)
                mtd = self._cumulative_return(pd.Series(dr_df.loc[mask, "value"], dtype=float))

        if si_alpha is None and daily_returns_series and benchmark_returns_series:
            dr = pd.DataFrame(daily_returns_series).rename(columns={"value": "sr"})
            br = pd.DataFrame(benchmark_returns_series).rename(columns={"value": "br"})
            merged = dr.merge(br, on="date", how="inner")
            if not merged.empty:
                strat_prod = self._coerce_num((1.0 + pd.Series(merged["sr"], dtype=float)).prod())
                bench_prod = self._coerce_num((1.0 + pd.Series(merged["br"], dtype=float)).prod())
                if strat_prod is not None and bench_prod is not None:
                    si_alpha = float((strat_prod - 1.0) - (bench_prod - 1.0))

        if daily_pl is None:
            self._mark_degraded("daily_pl", "missing prior NAV and no return-based estimate")
        if mtd is None:
            self._mark_degraded("mtd_return", "insufficient in-month return history")
        if si is None:
            self._mark_degraded("since_inception_return", "insufficient return history")
        if excess_return is None:
            self._mark_degraded("excess_return", "missing strategy or benchmark daily return")

        # Risk section.
        latest_cash_weight = None
        latest_gross = None
        latest_largest = None
        latest_vix_regime = None

        if canonical_df is not None and not canonical_df.empty:
            if "cash_weight" in canonical_df.columns and not canonical_df["cash_weight"].dropna().empty:
                latest_cash_weight = float(canonical_df["cash_weight"].dropna().iloc[-1])
            if "gross_exposure" in canonical_df.columns and not canonical_df["gross_exposure"].dropna().empty:
                latest_gross = float(canonical_df["gross_exposure"].dropna().iloc[-1])
            if "vix_regime" in canonical_df.columns and not canonical_df["vix_regime"].dropna().empty:
                latest_vix_regime = str(canonical_df["vix_regime"].dropna().iloc[-1])

        if latest_cash_weight is None and nav_df is not None and not nav_df.empty and "cash" in nav_df.columns and "equity" in nav_df.columns:
            nav_last = nav_df.dropna(subset=["cash", "equity"]).copy()
            if not nav_last.empty:
                cash_val = float(nav_last.iloc[-1]["cash"])
                equity_val = float(nav_last.iloc[-1]["equity"])
                if equity_val != 0:
                    latest_cash_weight = cash_val / equity_val

        if latest_gross is None and nav_df is not None and not nav_df.empty and "gross_exposure" in nav_df.columns and not nav_df["gross_exposure"].dropna().empty:
            latest_gross = float(nav_df["gross_exposure"].dropna().iloc[-1])

        if exec_payload_obj:
            risk_summary = exec_payload_obj.get("risk_summary")
            if isinstance(risk_summary, dict):
                max_weight_txt = str(risk_summary.get("Max position weight (%)") or "").replace("%", "").strip()
                latest_largest = self._coerce_num(max_weight_txt)
                if latest_largest is not None:
                    latest_largest = latest_largest / 100.0

        if latest_largest is None and isinstance(positions_obj.get("positions"), dict) and latest_nav:
            # Approximation only if position quantities exist without mark prices.
            latest_largest = None

        turnover_limit_pct = 0.35

        breaker_status = None
        if exec_payload_obj:
            breaker = exec_payload_obj.get("breaker")
            if isinstance(breaker, dict):
                breaker_status = str(breaker.get("mode") or "").upper() or None
            if breaker_status is None:
                ao = exec_payload_obj.get("active_overlay")
                if ao is not None:
                    breaker_status = str(ao).upper()
        if breaker_status is None and latest_vix_regime:
            breaker_status = latest_vix_regime.upper()

        # Activity section.
        buys = sells = new_positions = full_exits = orders_filled = orders_rejected = 0
        top_changes: list[dict[str, Any]] = []

        trades_list: list[dict[str, Any]] = []
        if isinstance(exec_payload_obj.get("trades"), list):
            trades_list = [t for t in exec_payload_obj["trades"] if isinstance(t, dict)]

        if trades_list:
            for t in trades_list:
                side = str(t.get("side") or "").upper()
                reason = str(t.get("reason") or "").lower()
                if side == "BUY":
                    buys += 1
                elif side == "SELL":
                    sells += 1
                if side == "BUY" and "removed" not in reason:
                    new_positions += 1
                if side == "SELL" and "removed" in reason:
                    full_exits += 1

            equity_ref = self._coerce_num(exec_payload_obj.get("equity")) or latest_nav
            for t in sorted(trades_list, key=lambda x: abs(self._coerce_num(x.get("notional")) or 0.0), reverse=True)[:5]:
                notional = self._coerce_num(t.get("notional"))
                side = str(t.get("side") or "").upper()
                if notional is not None and equity_ref:
                    signed = (notional / equity_ref) * (1 if side == "BUY" else -1)
                else:
                    signed = None
                top_changes.append(
                    {
                        "ticker": t.get("ticker"),
                        "action": side or "N/A",
                        "change_weight": signed,
                        "reason": t.get("reason") or "Not provided",
                    }
                )

        if isinstance(health_obj, dict):
            orders_filled = int(self._coerce_num(health_obj.get("executed_trade_count")) or buys + sells)
            planned = int(self._coerce_num(health_obj.get("planned_trade_count")) or orders_filled)
            orders_rejected = max(planned - orders_filled, 0)
        else:
            orders_filled = buys + sells
            orders_rejected = 0

        # Exceptions and operating checks.
        recon_ok = None
        if exec_payload_obj and exec_payload_obj.get("recon_failure") is not None:
            recon_ok = not bool(exec_payload_obj.get("recon_failure"))
        elif isinstance(health_obj, dict) and health_obj.get("recon_delta") is not None:
            delta = abs(self._coerce_num(health_obj.get("recon_delta")) or 0.0)
            tol = abs(self._coerce_num(health_obj.get("recon_equity_tolerance")) or 0.0)
            recon_ok = delta <= tol if tol > 0 else delta == 0.0

        run_success = execution_status in {"PASS", "READY", "SUCCESS", "COMPLETED"}
        if execution_status in {"HALTED", "FAIL", "FAILED", "ERROR", "RECON_FAIL_AUTO_BOOTSTRAP"}:
            run_success = False

        required_artifacts = [
            "outputs/latest.json",
            "outputs/alpha_assessment/canonical_performance.csv",
            "outputs/perf/nav_timeseries.csv",
            "outputs/ledger/trades.csv",
        ]
        missing_required = [p for p in required_artifacts if not (self.repo_root / p).exists()]

        exceptions: list[dict[str, str]] = []
        if recon_ok is True:
            exceptions.append({"category": "Reconciliation", "status": "pass", "message": "No issues detected."})
        elif recon_ok is False:
            exceptions.append({"category": "Reconciliation", "status": "fail", "message": "Model/broker reconciliation failed."})
        else:
            exceptions.append({"category": "Reconciliation", "status": "warning", "message": "Reconciliation status unavailable."})

        if run_success:
            exceptions.append({"category": "Execution", "status": "pass", "message": f"Execution status: {execution_status}."})
        else:
            exceptions.append({"category": "Execution", "status": "fail", "message": f"Execution status: {execution_status}."})

        risk_status = "pass"
        risk_message = "No risk exceptions detected."
        if breaker_status in {"LOCK", "HALTED"}:
            risk_status = "fail"
            risk_message = f"Breaker status is {breaker_status}."
        elif breaker_status in {"PARTIAL", "ELEVATED", "HIGH", "CRISIS"}:
            risk_status = "warning"
            risk_message = f"Breaker or regime indicates caution: {breaker_status}."
        exceptions.append({"category": "Risk", "status": risk_status, "message": risk_message})

        missing_optional = sorted(set(self.missing_files) - set(missing_required))

        if missing_required:
            exceptions.append(
                {
                    "category": "Data / artifacts",
                    "status": "warning",
                    "message": f"Missing critical artifacts: {', '.join(missing_required)}",
                }
            )
        elif missing_optional:
            exceptions.append(
                {
                    "category": "Data / artifacts",
                    "status": "warning",
                    "message": f"Missing optional artifacts: {', '.join(missing_optional[:3])}",
                }
            )
        elif self.degraded_metrics:
            exceptions.append(
                {
                    "category": "Data / artifacts",
                    "status": "warning",
                    "message": f"Metrics degraded: {'; '.join(self.degraded_metrics[:3])}",
                }
            )
        else:
            exceptions.append({"category": "Data / artifacts", "status": "pass", "message": "All critical artifacts present."})

        report_generated = False
        if run_id:
            report_dir = self._abs(f"outputs/runs/{run_id}/reports")
            if report_dir.exists() and any(report_dir.glob("quant_report_*.html")):
                report_generated = True
                self.sources.append(SourceRecord(f"outputs/runs/{run_id}/reports/quant_report_*.html", "used"))
            else:
                self.sources.append(SourceRecord(f"outputs/runs/{run_id}/reports/quant_report_*.html", "missing"))

        canonical_positions_exists = (self.repo_root / canonical_positions_path).exists()

        operating_checks = [
            {
                "label": "Run completed",
                "status": "pass" if run_success else "fail",
                "detail": f"Execution status: {execution_status}.",
            },
            {
                "label": "Trades executed",
                "status": "pass" if orders_filled > 0 else "warning",
                "detail": f"Orders filled: {orders_filled}.",
            },
            {
                "label": "Reconciliation passed",
                "status": "pass" if recon_ok is True else ("fail" if recon_ok is False else "warning"),
                "detail": "Reconciliation check available." if recon_ok is not None else "Reconciliation artifact not found.",
            },
            {
                "label": "Canonical positions present",
                "status": "pass" if canonical_positions_exists else "fail",
                "detail": canonical_positions_path if canonical_positions_exists else "Canonical snapshot missing.",
            },
            {
                "label": "Ledger updated",
                "status": "pass" if trades_df is not None and not trades_df.empty else "warning",
                "detail": "Ledger has at least one trade row." if trades_df is not None and not trades_df.empty else "No trade rows found in ledger.",
            },
            {
                "label": "Daily report generated",
                "status": "pass" if report_generated else "warning",
                "detail": "Run report HTML found." if report_generated else "Run report HTML not found in latest run archive.",
            },
            {
                "label": "Metric completeness",
                "status": "pass" if not self.degraded_metrics else "warning",
                "detail": "All executive metrics computed." if not self.degraded_metrics else "; ".join(self.degraded_metrics[:3]),
            },
        ]

        status_banner = "Run status unavailable."
        if report_date and run_success:
            trades_txt = f"{orders_filled} trades executed" if orders_filled > 0 else "no trades executed"
            excess_txt = "benchmark comparison unavailable"
            if excess_return is not None:
                excess_bps = round(excess_return * 10000)
                excess_txt = f"portfolio {'outperformed' if excess_bps >= 0 else 'underperformed'} benchmark by {abs(excess_bps)} bps"
            status_banner = (
                f"Run completed successfully on {report_date}. "
                f"{trades_txt.capitalize()}. {excess_txt.capitalize()}. "
                f"{('No material exceptions.' if len([e for e in exceptions if e['status'] == 'fail']) == 0 else 'Exceptions require review.')}"
            )
        elif report_date:
            status_banner = f"Run reported {execution_status} on {report_date}. Review exceptions and operating checks."

        model = {
            "run_meta": {
                "report_date": report_date or "Not generated",
                "run_id": run_id or "Not generated",
                "mode": mode,
                "overall_status": "PASS" if run_success else ("WARNING" if execution_status == "UNKNOWN" else "FAIL"),
                "benchmark": "SPY",
                "last_updated": self._safe_iso_now(),
                "status_banner": status_banner,
            },
            "kpis": {
                "portfolio_value": latest_nav,
                "daily_pl": daily_pl,
                "daily_return": latest_daily_return,
                "benchmark_return": benchmark_return,
                "excess_return": excess_return,
                "holdings": holdings,
                "turnover": turnover,
                "run_status": execution_status,
            },
            "perf_summary": {
                "mtd_return": mtd,
                "qtd_return": qtd,
                "since_inception_return": si,
                "since_inception_alpha": si_alpha,
                "current_drawdown": current_drawdown,
                "best_day": best_day,
                "worst_day": worst_day,
            },
            "series": {
                "nav": nav_series,
                "benchmark": benchmark_series,
                "daily_returns": daily_returns_series,
                "excess_returns": excess_returns_series,
                "drawdown": drawdown_series,
            },
            "risk": {
                "drawdown": current_drawdown,
                "cash_position": latest_cash_weight,
                "gross_exposure": latest_gross,
                "largest_position_weight": latest_largest,
                "turnover_pct": turnover,
                "turnover_limit_pct": turnover_limit_pct,
                "breaker_status": breaker_status or "UNKNOWN",
            },
            "activity": {
                "buys": buys,
                "sells": sells,
                "new_positions": new_positions,
                "full_exits": full_exits,
                "orders_filled": orders_filled,
                "orders_rejected": orders_rejected,
            },
            "top_changes": top_changes,
            "exceptions": exceptions,
            "operating_checks": operating_checks,
            "sources": [s.__dict__ for s in self.sources],
            "builder_notes": {
                "missing_files": sorted(set(self.missing_files)),
                "warnings": self.warnings,
                "degraded_metrics": self.degraded_metrics,
            },
        }
        return model


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Quant Daily Executive Dashboard data JSON from repo artifacts.")
    parser.add_argument("--repo-root", default=".", help="Repository root path")
    parser.add_argument(
        "--output",
        default="web/dashboard/dashboard_data.json",
        help="Dashboard JSON output path (relative to repo root unless absolute)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = repo_root / output_path

    builder = DashboardBuilder(repo_root=repo_root)
    model = builder.build()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(model, indent=2), encoding="utf-8")

    print(f"[DASHBOARD] wrote {output_path}")
    notes = model.get("builder_notes", {})
    missing = notes.get("missing_files", [])
    warnings = notes.get("warnings", [])
    degraded = notes.get("degraded_metrics", [])
    sources = model.get("sources", [])
    used_count = len([s for s in sources if s.get("status") == "used"])
    missing_count = len([s for s in sources if s.get("status") == "missing"])

    print(f"[DASHBOARD] sources_used={used_count} sources_missing={missing_count}")
    print(f"[DASHBOARD] degraded_metrics={len(degraded)} warnings={len(warnings)}")
    for msg in warnings:
        print(f"[DASHBOARD][WARN] {msg}")
    for metric_msg in degraded:
        print(f"[DASHBOARD][DEGRADED] {metric_msg}")
    if missing:
        print("[DASHBOARD][MISSING] " + ", ".join(missing))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
