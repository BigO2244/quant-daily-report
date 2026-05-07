#!/usr/bin/env python3
"""
Operational Stability Window Evaluation
=======================================
Dual-mode daily analytics utility. Automatically computes STRICT and LOOSE
evaluations for Polaris, Orion, Lyra, and SPY across four windows:

  1. Since Inception     (default: 2026-03-23)
  2. Stability Window    (default: 2026-04-24)
  3. Rolling 14D
  4. Rolling 30D         (only reported when >= 5 records exist in window)

Validity modes (both always computed):
  STRICT  — execution_status == EXECUTED  AND  reconciliation == OK_RECONCILED
  LOOSE   — execution_status == EXECUTED  (recon not required)

A day additionally requires:
  - shadow_evaluation.json present with data_status OK for all strategies
  - No halt or skip reason; no duplicate replay flag

Returns from shadow_evaluation.json daily_return field (model-portfolio
returns, NOT realized execution returns).

READ-ONLY: does not modify any production artifact.

Usage:
    python3 -m research.analysis.stable_window_evaluation [options]

    --repo-root PATH        Repo root path (default: auto-detected)
    --since-start DATE      Since-inception start (default: 2026-03-23)
    --stable-start DATE     Stability window start (default: 2026-04-24)
    --end-date DATE         End date YYYY-MM-DD (default: today)
    --output-dir PATH       Override output directory
    --csv                   Export daily returns CSV (shadow-OK days)
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# ══════════════════════════════════════════════════════════════════════════════
# Config — all window dates and thresholds centralized here
# ══════════════════════════════════════════════════════════════════════════════

EVALUATION_CONTRACT_VERSION   = "v1"

SINCE_INCEPTION_START_DEFAULT = "2026-03-23"
STABLE_WINDOW_START_DEFAULT   = "2026-04-24"
ROLLING_14D_DAYS              = 14
ROLLING_30D_DAYS              = 30
ROLLING_30D_MIN_RECORDS       = 5   # suppress rolling-30D section below this count

_HERE              = Path(__file__).resolve()
_REPO_ROOT_DEFAULT = _HERE.parents[2]

STRATEGY_SLUGS = ("caerus_polaris", "caerus_orion", "caerus_lyra")
BENCHMARK_SLUG  = "spy_benchmark"
ALL_SLUGS       = STRATEGY_SLUGS + (BENCHMARK_SLUG,)

DISPLAY: dict[str, str] = {
    "caerus_polaris": "Polaris",
    "caerus_orion":   "Orion",
    "caerus_lyra":    "Lyra",
    "spy_benchmark":  "SPY",
}

MODE_LABEL: dict[str, str] = {
    "strict": "STRICT  [EXECUTED + OK_RECONCILED]",
    "loose":  "LOOSE   [EXECUTED, recon not required]",
}
MODE_DEFINITION: dict[str, str] = {
    "strict": (
        "execution_status == EXECUTED  AND  reconciliation_status == OK_RECONCILED  "
        "AND  shadow data_status == OK for all strategies  AND  no halt/skip flag"
    ),
    "loose": (
        "execution_status == EXECUTED  AND  shadow data_status == OK for all strategies  "
        "AND  no halt/skip flag  (reconciliation not required)"
    ),
}

# ══════════════════════════════════════════════════════════════════════════════
# Data classes
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ShadowDay:
    trade_date: str
    returns: dict[str, float] = field(default_factory=dict)
    navs: dict[str, float] = field(default_factory=dict)
    data_status: dict[str, str] = field(default_factory=dict)
    avg_turnover: dict[str, float | None] = field(default_factory=dict)
    shadow_ok: bool = False


@dataclass
class ProductionDay:
    trade_date: str
    run_id: str
    executed: bool = False
    recon_ok: bool = False
    recon_status: str = ""
    halt_reason: str | None = None
    skipped_duplicate: bool = False


@dataclass
class DayRecord:
    trade_date: str
    shadow: ShadowDay | None = None
    production: ProductionDay | None = None

    def fully_valid(self) -> bool:
        return (
            self.shadow is not None and self.shadow.shadow_ok
            and self.production is not None
            and self.production.executed
            and self.production.recon_ok
            and not self.production.skipped_duplicate
            and not self.production.halt_reason
        )

    def execution_valid(self) -> bool:
        return (
            self.shadow is not None and self.shadow.shadow_ok
            and self.production is not None
            and self.production.executed
            and not self.production.skipped_duplicate
            and not self.production.halt_reason
        )

    def exclusion_reason(self, *, require_recon: bool = True) -> str:
        if self.shadow is None:
            return "no_shadow_artifact"
        if not self.shadow.shadow_ok:
            return "shadow_no_data"
        if self.production is None:
            return "no_production_run"
        if not self.production.executed:
            return f"not_executed:{self.production.halt_reason or 'unknown'}"
        if self.production.skipped_duplicate:
            return "skipped_duplicate"
        if self.production.halt_reason:
            return f"halt:{self.production.halt_reason}"
        if require_recon and not self.production.recon_ok:
            return f"recon_not_ok:{self.production.recon_status or 'unknown'}"
        return "valid"


# ══════════════════════════════════════════════════════════════════════════════
# Loaders
# ══════════════════════════════════════════════════════════════════════════════

def _load_json(path: Path) -> dict | None:
    try:
        raw = path.read_text(encoding="utf-8")
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _is_date(s: str) -> bool:
    if len(s) != 10 or s.count("-") != 2:
        return False
    parts = s.split("-")
    return len(parts) == 3 and all(p.isdigit() for p in parts)


def load_shadow_days(repo_root: Path) -> dict[str, ShadowDay]:
    shadow_dir = repo_root / "outputs" / "shadow_candidates"
    result: dict[str, ShadowDay] = {}
    if not shadow_dir.exists():
        print(
            "[stable_window_eval] WARNING: shadow_candidates directory not found — "
            "shadow data will be empty",
            file=sys.stderr,
        )
        return result

    for date_dir in sorted(shadow_dir.iterdir()):
        if not date_dir.is_dir() or not _is_date(date_dir.name):
            continue
        eval_path = date_dir / "shadow_evaluation.json"
        if not eval_path.exists():
            continue
        payload = _load_json(eval_path)
        if not payload:
            continue

        trade_date = str(payload.get("trade_date") or date_dir.name).strip()
        strats = payload.get("strategies") if isinstance(payload.get("strategies"), dict) else {}

        day = ShadowDay(trade_date=trade_date)
        all_ok = True
        for slug in ALL_SLUGS:
            s = strats.get(slug) if isinstance(strats.get(slug), dict) else {}
            status = str(s.get("data_status") or "UNAVAILABLE").upper()
            day.data_status[slug] = status
            if status == "OK":
                dr   = s.get("daily_return")
                nav  = s.get("nav")
                turn = s.get("avg_turnover")
                if dr is not None:
                    day.returns[slug] = float(dr)
                if nav is not None:
                    day.navs[slug] = float(nav)
                if turn is not None:
                    day.avg_turnover[slug] = float(turn)
            else:
                all_ok = False

        day.shadow_ok = all_ok and all(slug in day.returns for slug in ALL_SLUGS)
        result[trade_date] = day

    return result


def load_production_days(repo_root: Path) -> dict[str, ProductionDay]:
    runs_dir = repo_root / "outputs" / "runs"
    candidates: dict[str, list[dict]] = {}

    if not runs_dir.exists():
        print(
            "[stable_window_eval] WARNING: outputs/runs directory not found — "
            "production data will be empty",
            file=sys.stderr,
        )
        return {}

    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir() or not run_dir.name[0].isdigit():
            continue
        try:
            summary = _load_json(run_dir / "operator_summary.json") or {}
            results = _load_json(run_dir / "execution_results.json") or {}

            trade_date = (
                str(summary.get("trade_date") or results.get("trade_date") or "").strip()
                or run_dir.name[:10]
            )
            if not _is_date(trade_date):
                continue

            op_exec   = str(summary.get("operator_execution_status") or "").strip().lower()
            terminal  = str(summary.get("terminal_status") or "").strip().lower()
            res_status = str(results.get("status") or "").strip().upper()
            submitted = int(
                summary.get("submitted_count")
                or summary.get("orders_submitted_count")
                or results.get("submitted_count")
                or 0
            )
            executed = (
                op_exec == "executed"
                or terminal == "success"
                or res_status == "EXECUTED"
                or submitted > 0
            )

            halt_reason = (
                str(summary.get("halt_reason") or results.get("halt_reason") or "").strip()
                or None
            )
            skipped_duplicate = bool(
                summary.get("skipped_duplicate")
                or res_status == "SKIPPED_DUPLICATE"
                or op_exec == "skipped_duplicate"
            )

            recon_ok     = False
            recon_status = str(summary.get("post_execution_recon_status") or "").strip().upper()
            rp_str       = str(summary.get("post_execution_recon_path") or "").strip()
            if rp_str:
                rp = Path(rp_str)
                if not rp.is_absolute():
                    rp = repo_root / rp
                if rp.exists():
                    recon_payload = _load_json(rp)
                    if recon_payload:
                        file_status = str(
                            recon_payload.get("drift_status")
                            or recon_payload.get("reconciliation_status")
                            or recon_payload.get("status")
                            or ""
                        ).strip().upper()
                        recon_status = file_status
                        recon_ok = file_status in {"OK_RECONCILED", "OK"}

            score = (
                (10 if executed else 0)
                + (5 if recon_ok else 0)
                + (3 if terminal == "success" else 0)
                + (1 if submitted > 0 else 0)
            )
            candidates.setdefault(trade_date, []).append({
                "run_id":            run_dir.name,
                "executed":          executed,
                "recon_ok":          recon_ok,
                "recon_status":      recon_status,
                "halt_reason":       halt_reason,
                "skipped_duplicate": skipped_duplicate,
                "score":             score,
            })
        except Exception as exc:
            print(
                f"[stable_window_eval] WARNING: skipping run {run_dir.name}: {exc}",
                file=sys.stderr,
            )

    result: dict[str, ProductionDay] = {}
    for trade_date, runs in candidates.items():
        best = max(runs, key=lambda r: r["score"])
        result[trade_date] = ProductionDay(
            trade_date=trade_date,
            run_id=best["run_id"],
            executed=best["executed"],
            recon_ok=best["recon_ok"],
            recon_status=best["recon_status"],
            halt_reason=best["halt_reason"],
            skipped_duplicate=best["skipped_duplicate"],
        )
    return result


def load_backtest_summary(repo_root: Path) -> dict[str, Any]:
    p = (
        repo_root
        / "outputs" / "shadow_candidates" / "performance" / "shadow_summary.json"
    )
    result = _load_json(p) or {}
    if not result:
        print(
            "[stable_window_eval] NOTE: shadow_summary.json not found — "
            "backtest reference will be absent",
            file=sys.stderr,
        )
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Window construction
# ══════════════════════════════════════════════════════════════════════════════

def build_records(
    shadow_days: dict[str, ShadowDay],
    production_days: dict[str, ProductionDay],
    start: str,
    end: str,
) -> list[DayRecord]:
    all_dates = sorted(set(shadow_days) | set(production_days))
    return [
        DayRecord(
            trade_date=d,
            shadow=shadow_days.get(d),
            production=production_days.get(d),
        )
        for d in all_dates
        if start <= d <= end
    ]


def _rolling_start(end_date: str, n_days: int) -> str:
    d = datetime.strptime(end_date, "%Y-%m-%d")
    return (d - timedelta(days=n_days)).strftime("%Y-%m-%d")


# ══════════════════════════════════════════════════════════════════════════════
# Metrics
# ══════════════════════════════════════════════════════════════════════════════

def compute_metrics(returns: list[float]) -> dict[str, Any]:
    n = len(returns)
    if n == 0:
        return {
            "n_days": 0,
            "cumulative_return": None,
            "annualized_vol": None,
            "max_drawdown": None,
            "hit_rate": None,
            "note": "insufficient_data",
        }

    cum = 1.0
    for r in returns:
        cum *= (1.0 + r)
    cum_return = cum - 1.0

    if n > 1:
        mean_r = sum(returns) / n
        var = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
        ann_vol: float | None = math.sqrt(var * 252)
    else:
        ann_vol = None

    peak = nav = 1.0
    max_dd = 0.0
    for r in returns:
        nav *= (1.0 + r)
        if nav > peak:
            peak = nav
        dd = (nav - peak) / peak
        if dd < max_dd:
            max_dd = dd

    hit_rate = sum(1 for r in returns if r > 0) / n
    note = "low_confidence" if n < 10 else ("moderate_confidence" if n < 30 else "ok")

    return {
        "n_days": n,
        "cumulative_return": round(cum_return, 6),
        "annualized_vol": round(ann_vol, 6) if ann_vol is not None else None,
        "max_drawdown": round(max_dd, 6),
        "hit_rate": round(hit_rate, 4),
        "note": note,
    }


def extract_returns(
    records: list[DayRecord], slug: str, *, require_recon: bool
) -> list[float]:
    out = []
    for rec in records:
        if require_recon:
            valid = rec.fully_valid()
        else:
            valid = rec.execution_valid()
        if valid and rec.shadow and slug in rec.shadow.returns:
            out.append(rec.shadow.returns[slug])
    return out


def extract_shadow_only_returns(records: list[DayRecord], slug: str) -> list[float]:
    return [
        rec.shadow.returns[slug]
        for rec in records
        if rec.shadow and rec.shadow.shadow_ok and slug in rec.shadow.returns
    ]


def avg_turnover_from_records(records: list[DayRecord], slug: str) -> float | None:
    vals = [
        rec.shadow.avg_turnover[slug]
        for rec in records
        if (
            rec.shadow
            and rec.shadow.shadow_ok
            and slug in rec.shadow.avg_turnover
            and rec.shadow.avg_turnover[slug] is not None
        )
    ]
    return round(sum(vals) / len(vals), 6) if vals else None  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════════════════
# Status helpers
# ══════════════════════════════════════════════════════════════════════════════

def _eval_status(n_valid: int, pol_cum: float | None, spy_cum: float | None) -> str:
    if n_valid == 0:
        return "INSUFFICIENT DATA"
    if n_valid < 5:
        return "BUILDING EVIDENCE"
    if pol_cum is None or spy_cum is None:
        return "DATA PENDING"
    excess = pol_cum - spy_cum
    if excess > 0.05:
        return "OPERATIONALLY TRUSTWORTHY"
    if excess > 0.01:
        return "SIGNAL HEALTHY"
    if excess >= -0.01:
        return "MONITORING"
    return "UNDERPERFORMING"


# ══════════════════════════════════════════════════════════════════════════════
# Formatting helpers
# ══════════════════════════════════════════════════════════════════════════════

def _pct(v: float | None, *, signed: bool = True, na: str = "n/a") -> str:
    if v is None:
        return na
    prefix = "+" if signed and v >= 0 else ""
    return f"{prefix}{v * 100:.2f}%"


def _days_label(n: int) -> str:
    return f"({n}d)" if n > 0 else "(0d)"


# ══════════════════════════════════════════════════════════════════════════════
# Report helpers
# ══════════════════════════════════════════════════════════════════════════════

def _exclusion_tally(records: list[DayRecord], *, require_recon: bool) -> dict[str, int]:
    tally: dict[str, int] = {}
    for rec in records:
        reason = rec.exclusion_reason(require_recon=require_recon)
        if reason != "valid":
            tally[reason] = tally.get(reason, 0) + 1
    return tally


def _valid_day_rows(records: list[DayRecord], *, require_recon: bool) -> list[DayRecord]:
    if require_recon:
        return [r for r in records if r.fully_valid()]
    return [r for r in records if r.execution_valid()]


def _shadow_ok_rows(records: list[DayRecord]) -> list[DayRecord]:
    return [r for r in records if r.shadow and r.shadow.shadow_ok]


def _build_diagnostic_rows(
    records: list[DayRecord],
    *,
    require_recon: bool,
) -> list[dict[str, Any]]:
    rows = []
    for rec in records:
        reason = rec.exclusion_reason(require_recon=require_recon)
        if reason == "valid":
            continue
        if rec.shadow is None:
            shadow_brief = "missing"
        elif rec.shadow.shadow_ok:
            shadow_brief = "All_OK"
        else:
            bad = [
                f"{DISPLAY.get(s, s)}:{rec.shadow.data_status.get(s, '?')}"
                for s in ALL_SLUGS
                if rec.shadow.data_status.get(s) != "OK"
            ]
            shadow_brief = " ".join(bad) if bad else "NO_DATA"
        rows.append({
            "trade_date":       rec.trade_date,
            "exclusion_reason": reason,
            "shadow_brief":     shadow_brief,
            "executed":         rec.production.executed if rec.production else None,
            "recon_status":     rec.production.recon_status if rec.production else None,
            "halt_reason":      rec.production.halt_reason if rec.production else None,
        })
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# Strategy table builder
# ══════════════════════════════════════════════════════════════════════════════

def build_strategy_table(
    windows: dict[str, list[DayRecord]],
    *,
    require_recon: bool,
    backtest_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for slug in STRATEGY_SLUGS + (BENCHMARK_SLUG,):
        row: dict[str, Any] = {"slug": slug, "display": DISPLAY.get(slug, slug)}
        for wname, records in windows.items():
            row[f"{wname}_prod"]   = compute_metrics(
                extract_returns(records, slug, require_recon=require_recon)
            )
            row[f"{wname}_shadow"] = compute_metrics(
                extract_shadow_only_returns(records, slug)
            )

        row["avg_turnover"] = avg_turnover_from_records(windows.get("since", []), slug)

        bt_strats = (
            backtest_summary.get("strategies")
            if isinstance(backtest_summary.get("strategies"), dict)
            else {}
        )
        bt_s    = bt_strats.get(slug) if isinstance(bt_strats.get(slug), dict) else {}
        bt_summ = bt_s.get("summary") if isinstance(bt_s.get("summary"), dict) else {}
        row["backtest_cagr"]        = bt_summ.get("cagr")
        row["backtest_sharpe"]      = bt_summ.get("sharpe")
        row["backtest_max_dd"]      = bt_summ.get("max_drawdown")
        row["backtest_vol"]         = bt_summ.get("annualised_vol")
        row["backtest_excess_spy"]  = bt_summ.get("excess_return_vs_spy")
        row["backtest_n_years"]     = bt_summ.get("n_years")

        rows.append(row)
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# Markdown report
# ══════════════════════════════════════════════════════════════════════════════

def generate_markdown(
    windows: dict[str, list[DayRecord]],
    strategy_rows: list[dict[str, Any]],
    *,
    mode: str,
    since_start: str,
    stable_start: str,
    end_date: str,
    generated_at: str,
    rolling14_start: str,
    rolling30_start: str,
    show_rolling30: bool,
) -> str:
    require_recon = (mode == "strict")

    since_recs   = windows.get("since", [])
    stable_recs  = windows.get("stable", [])
    r14_recs     = windows.get("rolling14", [])
    r30_recs     = windows.get("rolling30", [])

    since_valid  = _valid_day_rows(since_recs,  require_recon=require_recon)
    stable_valid = _valid_day_rows(stable_recs, require_recon=require_recon)
    r14_valid    = _valid_day_rows(r14_recs,    require_recon=require_recon)
    r30_valid    = _valid_day_rows(r30_recs,    require_recon=require_recon)

    since_shadow  = _shadow_ok_rows(since_recs)
    stable_shadow = _shadow_ok_rows(stable_recs)
    r14_shadow    = _shadow_ok_rows(r14_recs)
    r30_shadow    = _shadow_ok_rows(r30_recs)

    since_excl  = _exclusion_tally(since_recs,  require_recon=require_recon)
    stable_excl = _exclusion_tally(stable_recs, require_recon=require_recon)
    diag_rows   = _build_diagnostic_rows(since_recs, require_recon=require_recon)

    bt_n_years = next(
        (r["backtest_n_years"] for r in strategy_rows if r.get("backtest_n_years")), "?"
    )

    lines: list[str] = [
        f"# Caerus Operational Stability Evaluation — {MODE_LABEL[mode]}",
        "",
        f"Generated: {generated_at}  |  End Date: {end_date}  |  Contract: {EVALUATION_CONTRACT_VERSION}",
        "",
        f"**Mode Definition:** {MODE_DEFINITION[mode]}",
        "",
        "---",
        "",
        "## Window Summary",
        "",
        "| Window | Start | End | Shadow OK Days | Valid Days | Excluded |",
        "|--------|-------|-----|----------------|------------|----------|",
        f"| Since Inception  | {since_start}  | {end_date} | {len(since_shadow)}  | {len(since_valid)}  | {len(since_recs)  - len(since_valid)}  |",
        f"| Stability Window | {stable_start} | {end_date} | {len(stable_shadow)} | {len(stable_valid)} | {len(stable_recs) - len(stable_valid)} |",
        f"| Rolling 14D      | {rolling14_start} | {end_date} | {len(r14_shadow)} | {len(r14_valid)} | {len(r14_recs) - len(r14_valid)} |",
    ]
    if show_rolling30:
        lines.append(
            f"| Rolling 30D      | {rolling30_start} | {end_date} | {len(r30_shadow)} | {len(r30_valid)} | {len(r30_recs) - len(r30_valid)} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Performance Comparison Table",
        "",
        "> **Prod-Validated**: days meeting this mode's full validity filter.",
        "> **Shadow-Only**: all shadow-OK days regardless of production state.",
        "> **Backtest**: multi-year simulation from shadow_summary.json (reference only).",
        "",
        "### Since Inception",
        "",
        f"| Strategy | Prod {_days_label(len(since_valid))} | Shadow {_days_label(len(since_shadow))} | Ann.Vol | Max DD | Avg Turn | BT CAGR ({bt_n_years}y) |",
        "|----------|------|--------|---------|--------|----------|-----------|",
    ]
    for row in strategy_rows:
        p = row["since_prod"]
        s = row["since_shadow"]
        lines.append(
            f"| {row['display']} "
            f"| {_pct(p.get('cumulative_return'))} "
            f"| {_pct(s.get('cumulative_return'))} "
            f"| {_pct(s.get('annualized_vol'), signed=False)} "
            f"| {_pct(s.get('max_drawdown'))} "
            f"| {_pct(row.get('avg_turnover'), signed=False)} "
            f"| {_pct(row.get('backtest_cagr'), signed=False)} |"
        )

    lines += [
        "",
        "### Stability Window",
        "",
        f"| Strategy | Prod {_days_label(len(stable_valid))} | Shadow {_days_label(len(stable_shadow))} | Ann.Vol | Max DD | BT Sharpe |",
        "|----------|------|--------|---------|--------|-----------|",
    ]
    for row in strategy_rows:
        p = row["stable_prod"]
        s = row["stable_shadow"]
        lines.append(
            f"| {row['display']} "
            f"| {_pct(p.get('cumulative_return'))} "
            f"| {_pct(s.get('cumulative_return'))} "
            f"| {_pct(s.get('annualized_vol'), signed=False)} "
            f"| {_pct(s.get('max_drawdown'))} "
            f"| {row.get('backtest_sharpe') or 'n/a'} |"
        )

    # Rolling windows table
    r14_hdr = (
        f"| Strategy | Rolling14 Prod {_days_label(len(r14_valid))} "
        f"| Rolling14 Shadow {_days_label(len(r14_shadow))} |"
    )
    r14_sep = "|----------|---------|---------|"
    if show_rolling30:
        r14_hdr += (
            f" Rolling30 Prod {_days_label(len(r30_valid))} "
            f"| Rolling30 Shadow {_days_label(len(r30_shadow))} |"
        )
        r14_sep += " ---- | ---- |"

    lines += ["", "### Rolling Windows", "", r14_hdr, r14_sep]
    for row in strategy_rows:
        p14 = row["rolling14_prod"]
        s14 = row["rolling14_shadow"]
        if show_rolling30:
            p30 = row["rolling30_prod"]
            s30 = row["rolling30_shadow"]
            lines.append(
                f"| {row['display']} "
                f"| {_pct(p14.get('cumulative_return'))} "
                f"| {_pct(s14.get('cumulative_return'))} "
                f"| {_pct(p30.get('cumulative_return'))} "
                f"| {_pct(s30.get('cumulative_return'))} |"
            )
        else:
            lines.append(
                f"| {row['display']} "
                f"| {_pct(p14.get('cumulative_return'))} "
                f"| {_pct(s14.get('cumulative_return'))} |"
            )

    # Day-level detail
    lines += [
        "",
        "---",
        "",
        "## Day-Level Detail",
        "",
        "### Valid Days — Since Inception",
        "",
    ]
    if since_valid:
        for rec in since_valid:
            returns_str = " ".join(
                f"{DISPLAY[s]}={_pct(rec.shadow.returns.get(s))}"
                for s in ALL_SLUGS
                if rec.shadow and s in rec.shadow.returns
            )
            lines.append(
                f"- {rec.trade_date}  "
                f"[{rec.production.run_id[:30] if rec.production else '?'}]  "
                f"{returns_str}"
            )
    else:
        lines.append("*No valid days yet in this mode. See shadow-OK days below.*")

    lines += ["", "### Shadow-OK Days — Since Inception", ""]
    for rec in since_shadow:
        prod_note = (
            f"exec={rec.production.executed} recon={rec.production.recon_ok}"
            if rec.production
            else "no_prod_run"
        )
        pol = _pct(rec.shadow.returns.get("caerus_polaris")) if rec.shadow else "n/a"
        spy = _pct(rec.shadow.returns.get("spy_benchmark")) if rec.shadow else "n/a"
        lines.append(f"- {rec.trade_date}: Pol={pol} SPY={spy} | {prod_note}")

    prod_in_window = [r for r in since_recs if r.production is not None]
    lines += [
        "",
        "### Production Run Overview — Since Inception",
        "",
        "| Date | Executed | Recon OK | Recon Status | Has Shadow | Shadow OK |",
        "|------|----------|----------|--------------|------------|-----------|",
    ]
    for rec in prod_in_window:
        p = rec.production
        lines.append(
            f"| {rec.trade_date} "
            f"| {'Y' if p.executed else 'N'} "
            f"| {'Y' if p.recon_ok else 'N'} "
            f"| {p.recon_status or 'n/a'} "
            f"| {'Y' if rec.shadow else 'N'} "
            f"| {'Y' if (rec.shadow and rec.shadow.shadow_ok) else 'N'} |"
        )

    # Diagnostic section
    lines += [
        "",
        "---",
        "",
        "## Diagnostic — Excluded Days",
        "",
        f"### Since Inception — Exclusion Summary ({mode.upper()} mode)",
        "",
    ]
    for reason, count in sorted(since_excl.items(), key=lambda x: -x[1]):
        lines.append(f"- `{reason}`: {count} day(s)")
    if not since_excl:
        lines.append("- None (all days are valid)")

    lines += [
        "",
        f"### Stability Window — Exclusion Summary ({mode.upper()} mode)",
        "",
    ]
    for reason, count in sorted(stable_excl.items(), key=lambda x: -x[1]):
        lines.append(f"- `{reason}`: {count} day(s)")
    if not stable_excl:
        lines.append("- None (all days are valid)")

    lines += [
        "",
        "### Per-Date Exclusion Detail — Since Inception",
        "",
    ]
    if diag_rows:
        lines += [
            "| Date | Exclusion Reason | Shadow Status | Executed | Recon Status |",
            "|------|-----------------|---------------|----------|--------------|",
        ]
        for dr in diag_rows:
            exec_str = {True: "Y", False: "N", None: "—"}[dr["executed"]]
            lines.append(
                f"| {dr['trade_date']} "
                f"| `{dr['exclusion_reason']}` "
                f"| {dr['shadow_brief']} "
                f"| {exec_str} "
                f"| {dr['recon_status'] or '—'} |"
            )
    else:
        lines.append("*No excluded days — all dates in window are valid.*")

    # Shadow data coverage
    lines += [
        "",
        "---",
        "",
        "## Shadow Evaluation Data Coverage",
        "",
    ]
    for rec in since_recs:
        if rec.shadow is None:
            continue
        statuses = " ".join(
            f"{DISPLAY[s]}:{rec.shadow.data_status.get(s, '?')}"
            for s in ALL_SLUGS
        )
        lines.append(f"- {rec.trade_date}: {statuses}")

    lines += [
        "",
        "---",
        "",
        "## Interpretation Notes",
        "",
        "- Statistics from < 10 days are flagged `low_confidence` in the JSON artifact.",
        "- Shadow returns are model-portfolio returns, NOT realized execution returns.",
        "  Realized returns may differ due to partial fills, execution timing, and slippage.",
        "- Backtest metrics from `shadow_summary.json` cover ~12 years of simulated daily",
        "  rebalancing with no transaction costs assumed.",
        "- Table becomes statistically meaningful when >= 20 overlapping valid days accumulate.",
        "- Rolling windows are computed from calendar days, not trading days.",
    ]

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# JSON artifact
# ══════════════════════════════════════════════════════════════════════════════

def build_artifact(
    windows: dict[str, list[DayRecord]],
    strategy_rows: list[dict[str, Any]],
    *,
    mode: str,
    since_start: str,
    stable_start: str,
    end_date: str,
    generated_at: str,
    rolling14_start: str,
    rolling30_start: str,
) -> dict[str, Any]:
    require_recon = (mode == "strict")

    window_starts = {
        "since":     since_start,
        "stable":    stable_start,
        "rolling14": rolling14_start,
        "rolling30": rolling30_start,
    }
    window_meta: dict[str, Any] = {}
    for wname, records in windows.items():
        valid  = _valid_day_rows(records, require_recon=require_recon)
        shadow = _shadow_ok_rows(records)
        window_meta[wname] = {
            "start_date":    window_starts.get(wname, "?"),
            "end_date":      end_date,
            "total_records": len(records),
            "shadow_ok_days": len(shadow),
            "valid_days":    len(valid),
        }

    def _day_row(rec: DayRecord) -> dict:
        return {
            "trade_date": rec.trade_date,
            "run_id":     rec.production.run_id if rec.production else None,
            "executed":   rec.production.executed if rec.production else False,
            "recon_ok":   rec.production.recon_ok if rec.production else False,
            "returns":    {slug: rec.shadow.returns.get(slug) for slug in ALL_SLUGS}
                          if rec.shadow else {},
        }

    since_valid  = _valid_day_rows(windows.get("since",  []), require_recon=require_recon)
    stable_valid = _valid_day_rows(windows.get("stable", []), require_recon=require_recon)

    return {
        "schema_version":              "1.1",
        "evaluation_contract_version": EVALUATION_CONTRACT_VERSION,
        "generated_at":                generated_at,
        "validity_mode":               mode,
        "mode_definition":             MODE_DEFINITION[mode],
        "windows":                     window_meta,
        "strategies": {
            row["slug"]: {
                "display_name": row["display"],
                **{
                    f"{wname}_{mtype}": row[f"{wname}_{mtype}"]
                    for wname in windows
                    for mtype in ("prod", "shadow")
                    if f"{wname}_{mtype}" in row
                },
                "avg_turnover_observed": row["avg_turnover"],
                "backtest_reference": {
                    "cagr":                 row["backtest_cagr"],
                    "sharpe":               row["backtest_sharpe"],
                    "max_drawdown":         row["backtest_max_dd"],
                    "annualized_vol":       row["backtest_vol"],
                    "excess_return_vs_spy": row["backtest_excess_spy"],
                    "n_years":              row["backtest_n_years"],
                },
            }
            for row in strategy_rows
        },
        "valid_days_since_inception":        [_day_row(r) for r in since_valid],
        "valid_days_stable_window":          [_day_row(r) for r in stable_valid],
        "shadow_only_days_since_inception":  [_day_row(r) for r in _shadow_ok_rows(windows.get("since",  []))],
        "shadow_only_days_stable_window":    [_day_row(r) for r in _shadow_ok_rows(windows.get("stable", []))],
        "diagnostic_excluded_since":
            _build_diagnostic_rows(windows.get("since", []), require_recon=require_recon),
    }


# ══════════════════════════════════════════════════════════════════════════════
# CSV export
# ══════════════════════════════════════════════════════════════════════════════

def write_daily_returns_csv(
    records: list[DayRecord],
    path: Path,
    *,
    shadow_ok_only: bool = True,
) -> None:
    rows: list[dict] = []
    for rec in records:
        if shadow_ok_only and not (rec.shadow and rec.shadow.shadow_ok):
            continue
        row: dict[str, Any] = {
            "date":                 rec.trade_date,
            "production_executed":  rec.production.executed if rec.production else False,
            "recon_ok":             rec.production.recon_ok if rec.production else False,
        }
        for slug in ALL_SLUGS:
            row[f"return_{slug}"] = (
                rec.shadow.returns.get(slug) if rec.shadow else None
            )
        rows.append(row)

    if not rows:
        return

    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ══════════════════════════════════════════════════════════════════════════════
# Operator console summary
# ══════════════════════════════════════════════════════════════════════════════

def print_operator_summary(
    strict_rows: list[dict[str, Any]],
    strict_since_n: int,
    strict_stable_n: int,
    loose_rows: list[dict[str, Any]],
    loose_since_n: int,
    loose_stable_n: int,
    *,
    end_date: str,
    since_start: str,
    stable_start: str,
    output_dir: Path,
) -> None:
    W = 80

    def _cum(rows: list[dict], slug: str, window: str) -> float | None:
        row = next((r for r in rows if r["slug"] == slug), {})
        return row.get(f"{window}_prod", {}).get("cumulative_return")

    print()
    print("=" * W)
    print(f"  CAERUS DAILY STABILITY EVALUATION  —  {end_date}")
    print("=" * W)

    for label, rows, since_n, stable_n in (
        ("STRICT  [EXECUTED + OK_RECONCILED]",     strict_rows, strict_since_n, strict_stable_n),
        ("LOOSE   [EXECUTED, recon not required]",  loose_rows,  loose_since_n,  loose_stable_n),
    ):
        pol_si = _cum(rows, "caerus_polaris", "since")
        spy_si = _cum(rows, "spy_benchmark",  "since")
        pol_st = _cum(rows, "caerus_polaris", "stable")
        spy_st = _cum(rows, "spy_benchmark",  "stable")

        print()
        print(f"  {label}")
        print(
            f"  Since Inception  ({since_start} → {end_date}):  "
            f"{since_n}d valid  |  "
            f"Polaris {_pct(pol_si)} vs SPY {_pct(spy_si)}  |  "
            f"{_eval_status(since_n, pol_si, spy_si)}"
        )
        print(
            f"  Stability Window ({stable_start} → {end_date}):  "
            f"{stable_n}d valid  |  "
            f"Polaris {_pct(pol_st)} vs SPY {_pct(spy_st)}  |  "
            f"{_eval_status(stable_n, pol_st, spy_st)}"
        )

    # Key insight
    drag = loose_since_n - strict_since_n
    print()
    print("  KEY INSIGHT:")
    if drag > 0:
        rate = strict_since_n / loose_since_n if loose_since_n > 0 else 0.0
        print(
            f"  Strict ({strict_since_n}d) < Loose ({loose_since_n}d) — "
            f"operational drag present ({drag} day(s) excluded by recon gate)."
        )
        print(
            f"  Recon convergence: {strict_since_n}/{loose_since_n} = {rate:.0%}  "
            f"(target >= 80%)"
        )
    elif drag == 0 and loose_since_n > 0:
        print(
            f"  Strict == Loose ({strict_since_n}d) — "
            f"recon is keeping pace with execution. No operational drag."
        )
    else:
        print("  Insufficient data. Run more trading days to build evaluation window.")

    # Polaris vs SPY context
    pol_strict_cum = _cum(strict_rows, "caerus_polaris", "since")
    spy_strict_cum = _cum(strict_rows, "spy_benchmark", "since")
    pol_loose_cum  = _cum(loose_rows,  "caerus_polaris", "since")
    spy_loose_cum  = _cum(loose_rows,  "spy_benchmark",  "since")
    ref_pol, ref_spy, ref_label = (
        (pol_strict_cum, spy_strict_cum, "strict")
        if pol_strict_cum is not None
        else (pol_loose_cum, spy_loose_cum, "loose")
    )
    if ref_pol is not None and ref_spy is not None:
        excess = ref_pol - ref_spy
        direction = "outperforming" if excess >= 0 else "underperforming"
        print(
            f"  Polaris is {direction} SPY by {_pct(abs(excess))} "
            f"({ref_label} mode, since inception)."
        )

    print()
    print("  ARTIFACTS:")
    print(f"  {output_dir / 'latest_strict.md'}")
    print(f"  {output_dir / 'latest_loose.md'}")
    print("=" * W)
    print()


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Caerus Operational Stability Window Evaluation — "
            "dual-mode (strict + loose) daily analytics"
        )
    )
    parser.add_argument(
        "--since-start", default=SINCE_INCEPTION_START_DEFAULT, metavar="DATE",
        help=f"Since-inception start date YYYY-MM-DD (default: {SINCE_INCEPTION_START_DEFAULT})",
    )
    parser.add_argument(
        "--stable-start", default=STABLE_WINDOW_START_DEFAULT, metavar="DATE",
        help=f"Stability window start date YYYY-MM-DD (default: {STABLE_WINDOW_START_DEFAULT})",
    )
    parser.add_argument(
        "--end-date", default=None, metavar="DATE",
        help="End date YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--repo-root", default=None, metavar="PATH",
        help="Repo root path (default: auto-detected from script location)",
    )
    parser.add_argument(
        "--output-dir", default=None, metavar="PATH",
        help="Override output directory",
    )
    parser.add_argument(
        "--csv", action="store_true",
        help="Export daily returns CSV (shadow-OK days)",
    )
    args = parser.parse_args(argv)

    repo_root  = Path(args.repo_root).resolve() if args.repo_root else _REPO_ROOT_DEFAULT
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else repo_root / "outputs" / "research" / "stable_window_evaluation"
    )
    end_date = args.end_date or date.today().strftime("%Y-%m-%d")

    print(f"[stable_window_eval] repo_root     = {repo_root}")
    print(f"[stable_window_eval] since_start   = {args.since_start}")
    print(f"[stable_window_eval] stable_start  = {args.stable_start}")
    print(f"[stable_window_eval] end_date      = {end_date}")
    print(f"[stable_window_eval] contract      = {EVALUATION_CONTRACT_VERSION}")

    # Load all data once
    print("[stable_window_eval] Loading shadow evaluation artifacts...")
    shadow_days = load_shadow_days(repo_root)
    print(f"[stable_window_eval]   {len(shadow_days)} shadow date(s) found")

    print("[stable_window_eval] Loading production run data...")
    production_days = load_production_days(repo_root)
    print(f"[stable_window_eval]   {len(production_days)} production date(s) found")

    print("[stable_window_eval] Loading backtest reference...")
    backtest_summary = load_backtest_summary(repo_root)
    print(f"[stable_window_eval]   backtest summary: {'found' if backtest_summary else 'not found'}")

    # Build all windows
    rolling14_start = _rolling_start(end_date, ROLLING_14D_DAYS)
    rolling30_start = _rolling_start(end_date, ROLLING_30D_DAYS)

    windows: dict[str, list[DayRecord]] = {
        "since":     build_records(shadow_days, production_days, args.since_start, end_date),
        "stable":    build_records(shadow_days, production_days, args.stable_start, end_date),
        "rolling14": build_records(shadow_days, production_days, rolling14_start, end_date),
        "rolling30": build_records(shadow_days, production_days, rolling30_start, end_date),
    }
    show_rolling30 = len(windows["rolling30"]) >= ROLLING_30D_MIN_RECORDS

    for wname, records in windows.items():
        sv       = len(_shadow_ok_rows(records))
        pv_strict = len(_valid_day_rows(records, require_recon=True))
        pv_loose  = len(_valid_day_rows(records, require_recon=False))
        print(
            f"[stable_window_eval]   {wname:<12}  "
            f"{len(records):>3} records  "
            f"{sv:>2} shadow-OK  "
            f"{pv_strict:>2} strict-valid  "
            f"{pv_loose:>2} loose-valid"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    now_str    = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    ts_compact = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    mode_rows: dict[str, list[dict[str, Any]]] = {}

    for mode in ("strict", "loose"):
        require_recon = (mode == "strict")
        print(f"[stable_window_eval] Generating {mode} evaluation...")

        rows = build_strategy_table(
            windows, require_recon=require_recon, backtest_summary=backtest_summary
        )
        md = generate_markdown(
            windows, rows,
            mode=mode,
            since_start=args.since_start,
            stable_start=args.stable_start,
            end_date=end_date,
            generated_at=now_str,
            rolling14_start=rolling14_start,
            rolling30_start=rolling30_start,
            show_rolling30=show_rolling30,
        )
        artifact = build_artifact(
            windows, rows,
            mode=mode,
            since_start=args.since_start,
            stable_start=args.stable_start,
            end_date=end_date,
            generated_at=now_str,
            rolling14_start=rolling14_start,
            rolling30_start=rolling30_start,
        )
        artifact_json = json.dumps(artifact, indent=2, default=str)

        (output_dir / f"latest_{mode}.md").write_text(md, encoding="utf-8")
        (output_dir / f"latest_{mode}.json").write_text(artifact_json, encoding="utf-8")
        (output_dir / f"stable_window_eval_{mode}_{ts_compact}.md").write_text(md, encoding="utf-8")
        (output_dir / f"stable_window_eval_{mode}_{ts_compact}.json").write_text(artifact_json, encoding="utf-8")

        mode_rows[mode] = rows

    # Optional CSV — raw daily returns for shadow-OK days (mode-agnostic raw data)
    if args.csv:
        since_records = windows["since"]
        csv_path   = output_dir / f"daily_returns_{ts_compact}.csv"
        latest_csv = output_dir / "latest_daily_returns.csv"
        write_daily_returns_csv(since_records, csv_path)
        write_daily_returns_csv(since_records, latest_csv)
        print(f"[stable_window_eval] CSV: {csv_path.name}")

    strict_since_n  = len(_valid_day_rows(windows["since"],  require_recon=True))
    strict_stable_n = len(_valid_day_rows(windows["stable"], require_recon=True))
    loose_since_n   = len(_valid_day_rows(windows["since"],  require_recon=False))
    loose_stable_n  = len(_valid_day_rows(windows["stable"], require_recon=False))

    print_operator_summary(
        mode_rows["strict"], strict_since_n, strict_stable_n,
        mode_rows["loose"],  loose_since_n,  loose_stable_n,
        end_date=end_date,
        since_start=args.since_start,
        stable_start=args.stable_start,
        output_dir=output_dir,
    )

    print(f"[stable_window_eval] Output dir: {output_dir}")
    print(f"  latest_strict.md / latest_strict.json")
    print(f"  latest_loose.md  / latest_loose.json")
    print(f"  stable_window_eval_strict_{ts_compact}.*")
    print(f"  stable_window_eval_loose_{ts_compact}.*")

    return 0


if __name__ == "__main__":
    sys.exit(main())
