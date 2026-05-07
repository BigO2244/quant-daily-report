#!/usr/bin/env python3
"""
Operational Stability Window Evaluation
=======================================
Standalone analysis that compares Polaris, Orion, Lyra, and SPY across:
  1. An "Official Since-Inception" window (default start: 2026-03-23)
  2. An "Operational Stability" window (default start: 2026-04-24)

A day is considered fully operationally valid when ALL of the following hold:
  - shadow_evaluation.json present and data_status == "OK" for all strategies
  - Production execution run exists with operator_execution_status == "executed"
  - Reconciliation status == OK_RECONCILED (read from actual recon file, not
    stale operator_summary field)
  - No halt or skip reason
  - No duplicate replay anomaly

Returns come from shadow_evaluation.json daily_return fields.  This is the
model-portfolio return, NOT realized execution return.

This script is READ-ONLY.  It does not modify any production artifact.

Usage:
    python3 -m research.analysis.stable_window_evaluation [options]

    --repo-root PATH        Path to repo root (default: auto-detected)
    --since-start DATE      Since-inception start date, YYYY-MM-DD (default: 2026-03-23)
    --stable-start DATE     Stability window start date, YYYY-MM-DD (default: 2026-04-24)
    --end-date DATE         End date YYYY-MM-DD (default: today)
    --output-dir PATH       Override output directory
    --loose-recon           Include executed days even when recon not confirmed
    --csv                   Also export daily returns as CSV
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

# ── Paths & constants ─────────────────────────────────────────────────────────

_HERE = Path(__file__).resolve()
_REPO_ROOT_DEFAULT = _HERE.parents[2]

SINCE_INCEPTION_START_DEFAULT = "2026-03-23"
STABLE_WINDOW_START_DEFAULT = "2026-04-24"

STRATEGY_SLUGS = ("caerus_polaris", "caerus_orion", "caerus_lyra")
BENCHMARK_SLUG = "spy_benchmark"
ALL_SLUGS = STRATEGY_SLUGS + (BENCHMARK_SLUG,)

DISPLAY = {
    "caerus_polaris": "Polaris",
    "caerus_orion": "Orion",
    "caerus_lyra": "Lyra",
    "spy_benchmark": "SPY",
}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ShadowDay:
    trade_date: str
    returns: dict[str, float] = field(default_factory=dict)   # slug → daily_return
    navs: dict[str, float] = field(default_factory=dict)      # slug → nav (informational)
    data_status: dict[str, str] = field(default_factory=dict) # slug → OK / NO_DATA / …
    avg_turnover: dict[str, float | None] = field(default_factory=dict)
    shadow_ok: bool = False   # True if ALL strategy slugs have data_status OK


@dataclass
class ProductionDay:
    trade_date: str
    run_id: str
    executed: bool = False
    recon_ok: bool = False          # from actual recon file, not operator_summary
    recon_status: str = ""          # raw status string
    halt_reason: str | None = None
    skipped_duplicate: bool = False


@dataclass
class DayRecord:
    trade_date: str
    shadow: ShadowDay | None = None
    production: ProductionDay | None = None

    def fully_valid(self) -> bool:
        return (
            self.shadow is not None
            and self.shadow.shadow_ok
            and self.production is not None
            and self.production.executed
            and self.production.recon_ok
            and not self.production.skipped_duplicate
            and not self.production.halt_reason
        )

    def execution_valid(self) -> bool:
        """Looser: executed + shadow OK, regardless of recon."""
        return (
            self.shadow is not None
            and self.shadow.shadow_ok
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


# ── Loaders ───────────────────────────────────────────────────────────────────

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
    """Return dict keyed by trade_date for every shadow_evaluation.json found."""
    shadow_dir = repo_root / "outputs" / "shadow_candidates"
    result: dict[str, ShadowDay] = {}
    if not shadow_dir.exists():
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
                dr = s.get("daily_return")
                nav = s.get("nav")
                turn = s.get("avg_turnover")
                if dr is not None:
                    day.returns[slug] = float(dr)
                if nav is not None:
                    day.navs[slug] = float(nav)
                if turn is not None:
                    day.avg_turnover[slug] = float(turn)
            else:
                all_ok = False

        # Require ALL strategies (including benchmark) to have OK data
        day.shadow_ok = all_ok and all(slug in day.returns for slug in ALL_SLUGS)
        result[trade_date] = day

    return result


def load_production_days(repo_root: Path) -> dict[str, ProductionDay]:
    """
    Return the best production day record for each trade_date.
    'Best' = highest score: executed + recon_ok > executed > not executed.
    Multiple runs on the same date → pick the one with the best score.
    """
    runs_dir = repo_root / "outputs" / "runs"
    candidates: dict[str, list[dict]] = {}

    if not runs_dir.exists():
        return {}

    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir() or not run_dir.name[0].isdigit():
            continue

        summary = _load_json(run_dir / "operator_summary.json") or {}
        results = _load_json(run_dir / "execution_results.json") or {}

        # Resolve trade_date
        trade_date = (
            str(summary.get("trade_date") or results.get("trade_date") or "").strip()
            or run_dir.name[:10]
        )
        if not _is_date(trade_date):
            continue

        # Execution status
        op_exec = str(summary.get("operator_execution_status") or "").strip().lower()
        terminal = str(summary.get("terminal_status") or "").strip().lower()
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

        # Recon: read the actual recon file (operator_summary field may be stale)
        recon_ok = False
        recon_status = str(summary.get("post_execution_recon_status") or "").strip().upper()
        recon_path_str = str(summary.get("post_execution_recon_path") or "").strip()
        if recon_path_str:
            rp = Path(recon_path_str)
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
            "run_id": run_dir.name,
            "executed": executed,
            "recon_ok": recon_ok,
            "recon_status": recon_status,
            "halt_reason": halt_reason,
            "skipped_duplicate": skipped_duplicate,
            "score": score,
        })

    # Pick best run per date
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
    """Load long-run backtest summary from shadow_summary.json for reference context."""
    summary_path = repo_root / "outputs" / "shadow_candidates" / "performance" / "shadow_summary.json"
    return _load_json(summary_path) or {}


# ── Window construction ───────────────────────────────────────────────────────

def build_records(
    shadow_days: dict[str, ShadowDay],
    production_days: dict[str, ProductionDay],
    start: str,
    end: str,
) -> list[DayRecord]:
    all_dates = sorted(set(shadow_days) | set(production_days))
    records: list[DayRecord] = []
    for d in all_dates:
        if d < start or d > end:
            continue
        records.append(DayRecord(
            trade_date=d,
            shadow=shadow_days.get(d),
            production=production_days.get(d),
        ))
    return records


# ── Metrics ───────────────────────────────────────────────────────────────────

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

    # Annualized volatility (252-day convention)
    if n > 1:
        mean_r = sum(returns) / n
        var = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
        ann_vol: float | None = math.sqrt(var * 252)
    else:
        ann_vol = None

    # Max drawdown
    peak = nav = 1.0
    max_dd = 0.0
    for r in returns:
        nav *= (1.0 + r)
        if nav > peak:
            peak = nav
        dd = (nav - peak) / peak
        if dd < max_dd:
            max_dd = dd

    hit_rate = sum(1 for r in returns if r > 0) / n if n > 0 else None

    note = "low_confidence" if n < 10 else ("moderate_confidence" if n < 30 else "ok")

    return {
        "n_days": n,
        "cumulative_return": round(cum_return, 6),
        "annualized_vol": round(ann_vol, 6) if ann_vol is not None else None,
        "max_drawdown": round(max_dd, 6),
        "hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
        "note": note,
    }


def extract_returns(records: list[DayRecord], slug: str, *, require_recon: bool) -> list[float]:
    result = []
    for rec in records:
        if require_recon and rec.fully_valid():
            if slug in rec.shadow.returns:  # type: ignore[union-attr]
                result.append(rec.shadow.returns[slug])
        elif not require_recon and rec.execution_valid():
            if slug in rec.shadow.returns:  # type: ignore[union-attr]
                result.append(rec.shadow.returns[slug])
    return result


def extract_shadow_only_returns(records: list[DayRecord], slug: str) -> list[float]:
    """Returns for all shadow-OK days regardless of production run state."""
    result = []
    for rec in records:
        if rec.shadow and rec.shadow.shadow_ok and slug in rec.shadow.returns:
            result.append(rec.shadow.returns[slug])
    return result


def avg_turnover_from_records(records: list[DayRecord], slug: str) -> float | None:
    vals = []
    for rec in records:
        if rec.shadow and rec.shadow.shadow_ok and slug in rec.shadow.avg_turnover:
            t = rec.shadow.avg_turnover[slug]
            if t is not None:
                vals.append(t)
    return round(sum(vals) / len(vals), 6) if vals else None


# ── Formatting ────────────────────────────────────────────────────────────────

def _pct(v: float | None, *, signed: bool = True, na: str = "n/a") -> str:
    if v is None:
        return na
    prefix = "+" if signed and v >= 0 else ""
    return f"{prefix}{v * 100:.2f}%"


def _days_label(n: int) -> str:
    return f"({n}d)" if n > 0 else "(0d)"


# ── Report builders ───────────────────────────────────────────────────────────

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


def build_strategy_table(
    since_records: list[DayRecord],
    stable_records: list[DayRecord],
    *,
    require_recon: bool,
    backtest_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    for slug in STRATEGY_SLUGS + (BENCHMARK_SLUG,):
        since_returns = extract_returns(since_records, slug, require_recon=require_recon)
        stable_returns = extract_returns(stable_records, slug, require_recon=require_recon)
        # Shadow-only (no production filter) for reference
        since_shadow_returns = extract_shadow_only_returns(since_records, slug)
        stable_shadow_returns = extract_shadow_only_returns(stable_records, slug)

        since_m = compute_metrics(since_returns)
        stable_m = compute_metrics(stable_returns)
        since_s = compute_metrics(since_shadow_returns)
        stable_s = compute_metrics(stable_shadow_returns)

        avg_turn = avg_turnover_from_records(since_records, slug)

        # Pull long-run backtest context
        bt_strats = (
            backtest_summary.get("strategies")
            if isinstance(backtest_summary.get("strategies"), dict)
            else {}
        )
        bt_s = bt_strats.get(slug) if isinstance(bt_strats.get(slug), dict) else {}
        bt_summ = bt_s.get("summary") if isinstance(bt_s.get("summary"), dict) else {}

        rows.append({
            "slug": slug,
            "display": DISPLAY.get(slug, slug),
            "since_inception_prod": since_m,
            "stable_window_prod": stable_m,
            "since_inception_shadow": since_s,
            "stable_window_shadow": stable_s,
            "avg_turnover": avg_turn,
            "backtest_cagr": bt_summ.get("cagr"),
            "backtest_sharpe": bt_summ.get("sharpe"),
            "backtest_max_dd": bt_summ.get("max_drawdown"),
            "backtest_vol": bt_summ.get("annualised_vol"),
            "backtest_excess_spy": bt_summ.get("excess_return_vs_spy"),
            "backtest_n_years": bt_summ.get("n_years"),
        })
    return rows


def generate_markdown(
    since_records: list[DayRecord],
    stable_records: list[DayRecord],
    strategy_rows: list[dict[str, Any]],
    *,
    since_start: str,
    stable_start: str,
    end_date: str,
    require_recon: bool,
    generated_at: str,
) -> str:
    since_valid = _valid_day_rows(since_records, require_recon=require_recon)
    stable_valid = _valid_day_rows(stable_records, require_recon=require_recon)
    since_shadow = _shadow_ok_rows(since_records)
    stable_shadow = _shadow_ok_rows(stable_records)
    since_excl = _exclusion_tally(since_records, require_recon=require_recon)
    stable_excl = _exclusion_tally(stable_records, require_recon=require_recon)

    validity_note = (
        "fully valid (executed + OK_RECONCILED + shadow OK)"
        if require_recon
        else "execution valid (executed + shadow OK, recon not required)"
    )

    lines: list[str] = [
        "# Caerus Operational Stability Window Evaluation",
        "",
        f"Generated: {generated_at}",
        f"Validity filter: {validity_note}",
        "",
        "---",
        "",
        "## Window Summary",
        "",
        "| Window | Start | End | Shadow OK Days | Prod Valid Days | Excluded |",
        "|--------|-------|-----|----------------|-----------------|----------|",
        f"| Since Inception | {since_start} | {end_date} | {len(since_shadow)} | {len(since_valid)} | {len(since_records) - len(since_valid)} |",
        f"| Stability Window | {stable_start} | {end_date} | {len(stable_shadow)} | {len(stable_valid)} | {len(stable_records) - len(stable_valid)} |",
        "",
        "### Since Inception — Exclusion Reasons",
        "",
    ]
    for reason, count in sorted(since_excl.items(), key=lambda x: -x[1]):
        lines.append(f"- `{reason}`: {count}")
    if not since_excl:
        lines.append("- None (all days valid)")

    lines += [
        "",
        "### Stability Window — Exclusion Reasons",
        "",
    ]
    for reason, count in sorted(stable_excl.items(), key=lambda x: -x[1]):
        lines.append(f"- `{reason}`: {count}")
    if not stable_excl:
        lines.append("- None (all days valid)")

    lines += [
        "",
        "---",
        "",
        "## Performance Comparison Table",
        "",
        "> **Columns explained:**",
        "> - *Prod-Validated*: returns on days meeting full validity filter only",
        "> - *Shadow-Only*: all shadow-OK days regardless of production run state",
        "> - *Backtest*: multi-year historical simulation from shadow_summary.json",
        "",
        "### Since Inception Window",
        "",
        f"| Strategy | Prod-Validated Return {_days_label(len(since_valid))} | Shadow-Only Return {_days_label(len(since_shadow))} | Ann. Vol | Max DD | Avg Turnover | Backtest CAGR ({next((r['backtest_n_years'] for r in strategy_rows if r['backtest_n_years']), '?')}y) |",
        "|----------|---------------------------|------------------------|----------|--------|--------------|--------------|",
    ]
    for row in strategy_rows:
        p = row["since_inception_prod"]
        s = row["since_inception_shadow"]
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
        f"| Strategy | Prod-Validated Return {_days_label(len(stable_valid))} | Shadow-Only Return {_days_label(len(stable_shadow))} | Ann. Vol | Max DD | Backtest Sharpe |",
        "|----------|---------------------------|------------------------|----------|--------|-----------------|",
    ]
    for row in strategy_rows:
        p = row["stable_window_prod"]
        s = row["stable_window_shadow"]
        lines.append(
            f"| {row['display']} "
            f"| {_pct(p.get('cumulative_return'))} "
            f"| {_pct(s.get('cumulative_return'))} "
            f"| {_pct(s.get('annualized_vol'), signed=False)} "
            f"| {_pct(s.get('max_drawdown'))} "
            f"| {row.get('backtest_sharpe') or 'n/a'} |"
        )

    # Valid and near-valid day listings
    lines += [
        "",
        "---",
        "",
        "## Day-Level Detail",
        "",
        "### Fully Valid Days (Production-Validated) — Since Inception",
        "",
    ]
    if since_valid:
        for rec in since_valid:
            returns_str = " ".join(
                f"{DISPLAY[s]}={_pct(rec.shadow.returns.get(s))}"  # type: ignore[union-attr]
                for s in STRATEGY_SLUGS + (BENCHMARK_SLUG,)
                if rec.shadow and s in rec.shadow.returns
            )
            lines.append(f"- {rec.trade_date} [{rec.production.run_id[:30] if rec.production else '?'}]  {returns_str}")  # type: ignore[union-attr]
    else:
        lines.append("*No fully valid days yet. See near-valid days below.*")

    lines += [
        "",
        "### Shadow-OK Days (Shadow Data Available, Any Production State) — Since Inception",
        "",
    ]
    for rec in since_shadow:
        prod_note = ""
        if rec.production:
            prod_note = f"exec={rec.production.executed} recon={rec.production.recon_ok}"
        else:
            prod_note = "no_prod_run"
        pol = _pct(rec.shadow.returns.get("caerus_polaris")) if rec.shadow else "n/a"  # type: ignore[union-attr]
        spy = _pct(rec.shadow.returns.get("spy_benchmark")) if rec.shadow else "n/a"  # type: ignore[union-attr]
        lines.append(f"- {rec.trade_date}: Pol={pol} SPY={spy} | {prod_note}")

    # Production run overview (all dates in since window)
    prod_runs_in_window = [r for r in since_records if r.production is not None]
    lines += [
        "",
        "### Production Run Overview — Since Inception",
        "",
        "| Date | Executed | Recon OK | Recon Status | Has Shadow | Shadow OK |",
        "|------|----------|----------|--------------|------------|-----------|",
    ]
    for rec in prod_runs_in_window:
        p = rec.production
        shadow_exists = rec.shadow is not None
        shadow_ok = rec.shadow.shadow_ok if rec.shadow else False
        lines.append(
            f"| {rec.trade_date} "
            f"| {'✓' if p.executed else '✗'} "  # type: ignore[union-attr]
            f"| {'✓' if p.recon_ok else '✗'} "  # type: ignore[union-attr]
            f"| {p.recon_status or 'n/a'} "  # type: ignore[union-attr]
            f"| {'✓' if shadow_exists else '✗'} "
            f"| {'✓' if shadow_ok else '✗'} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Data Coverage Assessment",
        "",
        "### Shadow Evaluation Coverage",
        "",
    ]
    all_shadow_dates = [r for r in since_records if r.shadow is not None]
    for rec in all_shadow_dates:
        statuses = " ".join(
            f"{DISPLAY[s]}:{rec.shadow.data_status.get(s, '?')}"  # type: ignore[union-attr]
            for s in ALL_SLUGS
        )
        lines.append(f"- {rec.trade_date}: {statuses}")

    lines += [
        "",
        "---",
        "",
        "## Interpretation Notes",
        "",
        f"### Current Overlap Status",
        "",
        "The system currently has two non-overlapping valid data sets:",
        "",
        "**Production-valid runs (executed + OK_RECONCILED):**",
    ]
    prod_valid_all = [r for r in since_records if r.production and r.production.executed and r.production.recon_ok]
    for rec in prod_valid_all:
        lines.append(f"- {rec.trade_date}: {rec.production.run_id[:35] if rec.production else '?'}  (shadow: {'OK' if rec.shadow and rec.shadow.shadow_ok else 'missing/NO_DATA'})")  # type: ignore[union-attr]

    lines += [
        "",
        "**Shadow-OK days (model data available):**",
    ]
    for rec in since_shadow:
        prod_status = "prod_valid" if (rec.production and rec.production.executed and rec.production.recon_ok) else ("prod_executed" if (rec.production and rec.production.executed) else "no_prod")
        lines.append(f"- {rec.trade_date}  ({prod_status})")

    lines += [
        "",
        "**Overlap** (both shadow OK and production valid): "
        + str(len([r for r in since_records if r.fully_valid()])),
        "",
        "This gap is expected: shadow evaluation began generating consistent artifacts",
        "in late April 2026, while the first confirmed-reconciled production runs were",
        "April 7–10 and May 6–7. As the system matures and both pipelines run reliably",
        "on the same dates, valid-day count will grow.",
        "",
        "### Reliability Warning",
        "",
        "- Statistics computed from < 10 days are flagged `low_confidence` in the JSON artifact.",
        "- Shadow returns are model-portfolio returns, NOT realized execution returns.",
        "  Realized returns may differ due to partial fills, execution timing, and slippage.",
        "- Avg Turnover is the average of the strategy's reported `avg_turnover` metric",
        "  across shadow-OK days; this reflects the model's historical average, not just",
        "  the production window.",
        "",
        "### What Changes This Picture",
        "",
        "The table becomes statistically meaningful when:",
        "1. Shadow evaluation pipeline runs consistently on execution days (same date).",
        "2. Production runs consistently achieve OK_RECONCILED status.",
        "3. At least 20 overlapping valid days accumulate (rough threshold for vol estimates).",
        "",
        "### Backtest Context",
        "",
        "Long-run backtest metrics from `shadow_summary.json` are shown for context only.",
        "Backtest results cover ~12 years and are based on simulated daily rebalancing",
        "with no transaction costs assumed. They should not be taken as expected live",
        "performance.",
    ]

    return "\n".join(lines)


# ── JSON artifact ─────────────────────────────────────────────────────────────

def build_artifact(
    since_records: list[DayRecord],
    stable_records: list[DayRecord],
    strategy_rows: list[dict[str, Any]],
    *,
    since_start: str,
    stable_start: str,
    end_date: str,
    require_recon: bool,
    generated_at: str,
) -> dict[str, Any]:
    since_valid = _valid_day_rows(since_records, require_recon=require_recon)
    stable_valid = _valid_day_rows(stable_records, require_recon=require_recon)
    since_shadow = _shadow_ok_rows(since_records)
    stable_shadow = _shadow_ok_rows(stable_records)

    def _day_row(rec: DayRecord) -> dict:
        return {
            "trade_date": rec.trade_date,
            "run_id": rec.production.run_id if rec.production else None,
            "executed": rec.production.executed if rec.production else False,
            "recon_ok": rec.production.recon_ok if rec.production else False,
            "returns": {
                slug: rec.shadow.returns.get(slug)
                for slug in ALL_SLUGS
            } if rec.shadow else {},
        }

    return {
        "schema_version": "1.1",
        "generated_at": generated_at,
        "validity_mode": "strict_recon" if require_recon else "execution_only",
        "windows": {
            "since_inception": {
                "start_date": since_start,
                "end_date": end_date,
                "total_records": len(since_records),
                "shadow_ok_days": len(since_shadow),
                "valid_days": len(since_valid),
            },
            "stable_window": {
                "start_date": stable_start,
                "end_date": end_date,
                "total_records": len(stable_records),
                "shadow_ok_days": len(stable_shadow),
                "valid_days": len(stable_valid),
            },
        },
        "strategies": {
            row["slug"]: {
                "display_name": row["display"],
                "since_inception": {
                    "prod_validated": row["since_inception_prod"],
                    "shadow_only": row["since_inception_shadow"],
                },
                "stable_window": {
                    "prod_validated": row["stable_window_prod"],
                    "shadow_only": row["stable_window_shadow"],
                },
                "avg_turnover_observed": row["avg_turnover"],
                "backtest_reference": {
                    "cagr": row["backtest_cagr"],
                    "sharpe": row["backtest_sharpe"],
                    "max_drawdown": row["backtest_max_dd"],
                    "annualized_vol": row["backtest_vol"],
                    "excess_return_vs_spy": row["backtest_excess_spy"],
                    "n_years": row["backtest_n_years"],
                },
            }
            for row in strategy_rows
        },
        "valid_days_since_inception": [_day_row(r) for r in since_valid],
        "valid_days_stable_window": [_day_row(r) for r in stable_valid],
        "shadow_only_days_since_inception": [_day_row(r) for r in since_shadow],
        "shadow_only_days_stable_window": [_day_row(r) for r in stable_shadow],
    }


# ── CSV export ────────────────────────────────────────────────────────────────

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
            "date": rec.trade_date,
            "production_executed": rec.production.executed if rec.production else False,
            "recon_ok": rec.production.recon_ok if rec.production else False,
        }
        for slug in ALL_SLUGS:
            row[f"return_{slug}"] = (
                rec.shadow.returns.get(slug)  # type: ignore[union-attr]
                if rec.shadow else None
            )
        rows.append(row)

    if not rows:
        return

    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ── Console summary ───────────────────────────────────────────────────────────

def print_summary(
    strategy_rows: list[dict[str, Any]],
    since_valid_n: int,
    stable_valid_n: int,
    since_shadow_n: int,
    stable_shadow_n: int,
) -> None:
    width = 102
    print()
    print("=" * width)
    print("  CAERUS OPERATIONAL STABILITY WINDOW EVALUATION")
    print("=" * width)
    print(
        f"  Since Inception:  {since_valid_n} prod-valid days  |  {since_shadow_n} shadow-only days"
    )
    print(
        f"  Stability Window: {stable_valid_n} prod-valid days  |  {stable_shadow_n} shadow-only days"
    )
    print("-" * width)

    hdr = (
        f"{'Strategy':<10}"
        f"{'SInc Prod':>12} {'SInc Shad':>12}"
        f"{'Stbl Prod':>12} {'Stbl Shad':>12}"
        f"{'Ann.Vol':>9} {'Max DD':>9} {'Avt.Turn':>10}"
        f"{'BT CAGR':>9} {'BT Shrp':>9}"
    )
    print(hdr)
    print("-" * width)
    for row in strategy_rows:
        sp = row["since_inception_prod"]
        ss = row["since_inception_shadow"]
        wp = row["stable_window_prod"]
        ws = row["stable_window_shadow"]
        print(
            f"{row['display']:<10}"
            f"{_pct(sp.get('cumulative_return')):>12}"
            f"{_pct(ss.get('cumulative_return')):>12}"
            f"{_pct(wp.get('cumulative_return')):>12}"
            f"{_pct(ws.get('cumulative_return')):>12}"
            f"{_pct(ss.get('annualized_vol'), signed=False):>9}"
            f"{_pct(ss.get('max_drawdown')):>9}"
            f"{_pct(row.get('avg_turnover'), signed=False):>10}"
            f"{_pct(row.get('backtest_cagr'), signed=False):>9}"
            f"{str(round(row['backtest_sharpe'], 2) if row.get('backtest_sharpe') else 'n/a'):>9}"
        )
    print("=" * width)
    print(
        "  Columns: SInc=Since Inception, Stbl=Stability Window, "
        "Prod=prod-validated, Shad=shadow-only"
    )
    print(
        "  Ann.Vol / Max DD / Avg.Turn computed from shadow-only days (n may be < 10 → low conf.)"
    )
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Operational Stability Window Evaluation for Caerus strategies"
    )
    parser.add_argument(
        "--since-start",
        default=SINCE_INCEPTION_START_DEFAULT,
        metavar="DATE",
        help=f"Since-inception start date YYYY-MM-DD (default: {SINCE_INCEPTION_START_DEFAULT})",
    )
    parser.add_argument(
        "--stable-start",
        default=STABLE_WINDOW_START_DEFAULT,
        metavar="DATE",
        help=f"Stability window start date YYYY-MM-DD (default: {STABLE_WINDOW_START_DEFAULT})",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        metavar="DATE",
        help="End date YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        metavar="PATH",
        help="Repo root path (default: auto-detected from script location)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        metavar="PATH",
        help="Override output directory",
    )
    parser.add_argument(
        "--loose-recon",
        action="store_true",
        help="Include executed days even when recon not confirmed OK_RECONCILED",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Export daily returns CSV in addition to JSON/markdown",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve() if args.repo_root else _REPO_ROOT_DEFAULT
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else repo_root / "outputs" / "research" / "stable_window_evaluation"
    )
    end_date = args.end_date or date.today().strftime("%Y-%m-%d")
    require_recon = not args.loose_recon

    print(f"[stable_window_eval] repo_root    = {repo_root}")
    print(f"[stable_window_eval] since_start  = {args.since_start}")
    print(f"[stable_window_eval] stable_start = {args.stable_start}")
    print(f"[stable_window_eval] end_date     = {end_date}")
    print(f"[stable_window_eval] require_recon= {require_recon}")

    # Load artifacts
    print("[stable_window_eval] Loading shadow evaluation artifacts...")
    shadow_days = load_shadow_days(repo_root)
    print(f"[stable_window_eval]   Found {len(shadow_days)} shadow evaluation date(s)")

    print("[stable_window_eval] Loading production run data...")
    production_days = load_production_days(repo_root)
    print(f"[stable_window_eval]   Found {len(production_days)} production run date(s)")

    print("[stable_window_eval] Loading backtest summary (reference context)...")
    backtest_summary = load_backtest_summary(repo_root)
    has_bt = bool(backtest_summary)
    print(f"[stable_window_eval]   Backtest summary: {'found' if has_bt else 'not found'}")

    # Build windows
    since_records = build_records(shadow_days, production_days, args.since_start, end_date)
    stable_records = build_records(shadow_days, production_days, args.stable_start, end_date)

    since_valid = _valid_day_rows(since_records, require_recon=require_recon)
    stable_valid = _valid_day_rows(stable_records, require_recon=require_recon)
    since_shadow = _shadow_ok_rows(since_records)
    stable_shadow = _shadow_ok_rows(stable_records)

    print(f"[stable_window_eval] Since inception  : {len(since_records)} dates  {len(since_shadow)} shadow-OK  {len(since_valid)} prod-valid")
    print(f"[stable_window_eval] Stability window : {len(stable_records)} dates  {len(stable_shadow)} shadow-OK  {len(stable_valid)} prod-valid")

    # Compute strategy metrics
    strategy_rows = build_strategy_table(
        since_records,
        stable_records,
        require_recon=require_recon,
        backtest_summary=backtest_summary,
    )

    # Print console table
    print_summary(
        strategy_rows,
        len(since_valid),
        len(stable_valid),
        len(since_shadow),
        len(stable_shadow),
    )

    # Generate outputs
    now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    ts_compact = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    md = generate_markdown(
        since_records,
        stable_records,
        strategy_rows,
        since_start=args.since_start,
        stable_start=args.stable_start,
        end_date=end_date,
        require_recon=require_recon,
        generated_at=now_str,
    )

    artifact = build_artifact(
        since_records,
        stable_records,
        strategy_rows,
        since_start=args.since_start,
        stable_start=args.stable_start,
        end_date=end_date,
        require_recon=require_recon,
        generated_at=now_str,
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    md_path = output_dir / f"stable_window_eval_{ts_compact}.md"
    json_path = output_dir / f"stable_window_eval_{ts_compact}.json"
    latest_md = output_dir / "latest.md"
    latest_json = output_dir / "latest.json"

    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
    latest_md.write_text(md, encoding="utf-8")
    latest_json.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")

    print(f"[stable_window_eval] Outputs written to {output_dir}")
    print(f"  {md_path.name}")
    print(f"  {json_path.name}")
    print(f"  latest.md / latest.json")

    if args.csv:
        csv_path = output_dir / f"daily_returns_{ts_compact}.csv"
        write_daily_returns_csv(since_records, csv_path)
        latest_csv = output_dir / "latest_daily_returns.csv"
        write_daily_returns_csv(since_records, latest_csv)
        print(f"  {csv_path.name}")
        print(f"  latest_daily_returns.csv")

    if len(since_valid) == 0 and len(stable_valid) == 0:
        print()
        print("[stable_window_eval] NOTE: 0 fully valid days in either window.")
        print("  Shadow-OK days exist and production runs executed, but the two")
        print("  sets do not overlap on the same dates yet. Shadow-only metrics")
        print("  are shown in the report for reference.")
        print("  Re-run with --loose-recon to see execution-only filtered metrics.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
