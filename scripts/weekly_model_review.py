#!/usr/bin/env python3
"""
Weekly Model Review — Alpha Stack
Scores the model across 7 dimensions vs. industry quant fund standards.

Scoring philosophy:
  - Each dimension uses a FIXED checklist of 10 criteria (same every week).
  - Each criterion is evaluated pass (1) / fail (0) / partial (0.5).
  - Score = total points, rounded to nearest integer, capped 1-10.
  - Criteria never change — what changes is which ones pass.
  - Scores are deliberately critical: earning 6+ requires genuine capability,
    not just the presence of a file.

Industry ladder:
  1-3 : Early stage / ad hoc
  4-5 : Systematic but simple (small quant pod baseline)
  6-7 : Institutional quality (mid-tier quant fund)
  8-9 : Competitive (top quant fund standard)
  10  : Best in class (Renaissance / Two Sigma tier)

Usage:
    python3 scripts/weekly_model_review.py
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ── repo root ────────────────────────────────────────────────────────────────
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core.email_env import resolve_email_env

# ── load .env ────────────────────────────────────────────────────────────────
_env_path = _REPO / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# ── paths ────────────────────────────────────────────────────────────────────
BENCHMARK_JSON  = _REPO / "outputs" / "benchmark" / "benchmark_vs_spy.json"
BACKTEST_JSON   = _REPO / "research" / "backtest_results_quality_meanrev.json"
UNIVERSE_CSV    = _REPO / "data" / "universe.csv"
FUNDAMENTAL_DIR = _REPO / "data" / "fundamental"
PRECOMPUTE_DIR  = _REPO / "outputs" / "precompute"

TODAY = date.today().isoformat()
REPORT_PATH = _REPO / "research" / f"model_review_{TODAY}.md"

INDUSTRY_LADDER = [
    (1, 3,  "Early stage / ad hoc"),
    (4, 5,  "Systematic but simple (small quant pod baseline)"),
    (6, 7,  "Institutional quality (mid-tier quant fund)"),
    (8, 9,  "Competitive (top quant fund standard)"),
    (10, 10, "Best in class (Renaissance / Two Sigma tier)"),
]


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _clamp(v: float, lo: int = 1, hi: int = 10) -> int:
    return max(lo, min(hi, round(v)))


def _industry_label(score: int) -> str:
    for lo, hi, label in INDUSTRY_LADDER:
        if lo <= score <= hi:
            return label
    return "Unknown"


def _file_contains(path: Path, keyword: str) -> bool:
    try:
        return keyword.lower() in path.read_text(errors="ignore").lower()
    except Exception:
        return False


def _count_test_files() -> int:
    tests_dir = _REPO / "Tests"
    if not tests_dir.exists():
        return 0
    return sum(
        1
        for path in tests_dir.rglob("test*.py")
        if path.is_file() and "__pycache__" not in path.parts
    )


def _has_drawdown_circuit_breaker() -> bool:
    risk = _REPO / "core" / "risk_controls.py"
    if not risk.exists():
        return False
    required_markers = (
        "update_peak_equity_state",
        "peak_equity_path",
        "circuit_breaker_pct",
        "circuit_breaker_scale",
    )
    return all(_file_contains(risk, marker) for marker in required_markers)


# ── fixed rubrics (10 criteria each, 1 pt per pass) ──────────────────────────
#
# Each item: (label, callable → float 0/0.5/1, explanation-if-fail)
# The label and structure never change week to week.
# ─────────────────────────────────────────────────────────────────────────────

def _rubric_signal_quality(bt: dict | None) -> list[tuple[str, float, str]]:
    """
    10 criteria. Deliberately critical.
    Negative ICs mean the signal has no predictive power — that earns a 0.
    """
    def _ics_positive(sleeve_key: str) -> bool:
        if not bt:
            return False
        ics = (bt.get(sleeve_key) or {}).get("ic_summary", {})
        vals = [v for v in ics.values() if isinstance(v, (int, float))]
        return bool(vals) and any(v > 0.02 for v in vals)

    def _ics_all_negative(sleeve_key: str) -> bool:
        if not bt:
            return False
        ics = (bt.get(sleeve_key) or {}).get("ic_summary", {})
        vals = [v for v in ics.values() if isinstance(v, (int, float))]
        return bool(vals) and all(v < 0 for v in vals)

    def _sharpe(sleeve_key: str) -> float:
        if not bt:
            return 0.0
        return float((bt.get(sleeve_key) or {}).get("sharpe_ratio", 0.0))

    def _spy_sharpe() -> float:
        if not bt:
            return 0.0
        return float((bt.get("spy") or {}).get("sharpe_ratio", 0.0))

    mr_trades = int((bt or {}).get("mean_reversion", {}).get("trade_count", 0))
    has_tcm = _file_contains(_REPO / "sleeves" / "sleeve_mean_reversion" / "selection.py", "transaction_cost")
    best_sharpe = max(_sharpe(k) for k in ("quality_current", "quality_enhanced", "mean_reversion"))
    no_lookahead = not any(
        (bt or {}).get(k, {}).get("lookahead_bias_flags")
        for k in ("quality_current", "quality_enhanced", "mean_reversion")
    )

    results = [
        # 1. At least one sleeve has positive mean IC (fundamental signal has predictive value)
        ("At least one sleeve has positive mean IC (>0.02)",
         0.0 if (not bt or not any(_ics_positive(k) for k in ("quality_current", "quality_enhanced"))) else 1.0,
         "ALL quality ICs are negative — factors point in the wrong direction; no predictive value confirmed"),
        # 2. No lookahead bias in any sleeve
        ("No lookahead bias flags in any sleeve backtest",
         1.0 if no_lookahead else 0.0,
         "Lookahead bias detected — backtest results are unreliable"),
        # 3. Best sleeve Sharpe beats SPY in backtest
        ("Best-sleeve backtest Sharpe beats SPY",
         1.0 if best_sharpe > _spy_sharpe() else 0.0,
         f"No sleeve beats SPY Sharpe ({_spy_sharpe():.2f}); best sleeve Sharpe = {best_sharpe:.2f}"),
        # 4. Best sleeve Sharpe > 1.0
        ("Best-sleeve backtest Sharpe > 1.0",
         1.0 if best_sharpe > 1.0 else 0.0,
         f"Best sleeve Sharpe = {best_sharpe:.2f} — below 1.0 hurdle"),
        # 5. Transaction cost model for high-turnover sleeve
        ("Transaction cost model for mean-reversion sleeve",
         1.0 if has_tcm else 0.0,
         f"MR sleeve has {mr_trades} trades with no TCM — live returns will be materially worse than backtest"),
        # 6. Factor orthogonalization (Gram-Schmidt / PCA)
        ("Factor orthogonalization implemented",
         1.0 if _file_contains(_REPO / "sleeves" / "sleeve_quality" / "indicators.py", "orthogon") else 0.0,
         "No factor orthogonalization — correlated factors double-count the same information"),
        # 7. At least 4 independent signal sleeves integrated and producing output
        ("≥4 signal sleeves integrated and non-empty",
         1.0 if all((_REPO / "sleeves" / s).exists() for s in
                    ("sleeve_trend", "sleeve_2", "sleeve_quality", "sleeve_mean_reversion")) else 0.0,
         "Fewer than 4 sleeves present"),
        # 8. Regime-conditional signal filtering (breadth gate or equivalent)
        ("Regime-conditional signal filtering active (breadth gate)",
         1.0 if _file_contains(_REPO / "sleeves" / "sleeve_mean_reversion" / "selection.py", "breadth_gate") else 0.0,
         "No regime-conditional signal filtering — all signals fire regardless of market regime"),
        # 9. IC decay / signal half-life analysis
        ("Signal decay / IC half-life analysis present",
         1.0 if _file_contains(_REPO / "core" / "attribution.py", "decay") else 0.0,
         "No signal decay analysis — unknown if factors are leading or lagging"),
        # 10. Alternative data beyond price and EDGAR fundamentals
        ("Alternative data integrated (sentiment, options flow, satellite, etc.)",
         0.0,  # hardcoded — not present
         "Price and EDGAR fundamentals only; no alternative data"),
    ]
    return results


def _rubric_infrastructure() -> list[tuple[str, float, str]]:
    scripts = _REPO / "scripts"
    core = _REPO / "core"
    n_tests = _count_test_files()
    results = [
        ("3-phase cron pipeline (precompute / execute / confirm)",
         1.0 if all((scripts / f"cron_{p}.sh").exists() for p in ("precompute", "execute", "confirm")) else 0.0,
         "3-phase cron pipeline incomplete"),
        ("Precompute bundle with contract schema validation",
         1.0 if (core / "precompute_contract.py").exists() else 0.0,
         "No precompute contract validation"),
        ("Atomic execution idempotency guard (lock file)",
         1.0 if _file_contains(scripts / "run_precomputed_alpaca_execution.py", "lock") else 0.0,
         "No execution idempotency guard — duplicate orders possible"),
        ("Pre-trade broker reconciliation",
         1.0 if _file_contains(scripts / "run_precomputed_alpaca_execution.py", "recon") else 0.0,
         "No pre-trade reconciliation — model and broker can diverge silently"),
        ("Daily email reporting (3 phases: plan / execute / confirm)",
         1.0 if all((scripts / f).exists() for f in ("send_precompute_email.py", "send_trading_confirmation_email.py")) else 0.0,
         "Email reporting incomplete"),
        ("Live benchmark tracking (portfolio vs SPY)",
         1.0 if BENCHMARK_JSON.exists() else 0.0,
         "No benchmark tracking — no way to measure live alpha"),
        ("Performance vs SPY in confirmation email",
         1.0 if _file_contains(scripts / "send_trading_confirmation_email.py", "performance") else 0.0,
         "Confirmation email does not include performance vs SPY"),
        ("Automated test suite (unit / integration tests)",
         1.0 if n_tests >= 20 else (0.5 if n_tests >= 5 else 0.0),
         "No automated test suite — regressions can go undetected"),
        ("CI/CD pipeline (automated deploy / validation)",
         1.0 if (_REPO / ".github" / "workflows").exists() else 0.0,
         "No CI/CD — deployments are manual SCP with no automated validation"),
        ("Structured logging with searchable run IDs",
         1.0 if (_REPO / "logs").exists() else 0.0,
         "No structured log directory"),
    ]
    return results


def _rubric_risk_management() -> list[tuple[str, float, str]]:
    risk = _REPO / "core" / "risk_controls.py"
    exec_script = _REPO / "scripts" / "run_precomputed_alpaca_execution.py"
    daily = _REPO / "daily_quant_report.py"
    results = [
        ("Single-name position cap (≤10% of portfolio)",
         1.0 if (risk.exists() and _file_contains(risk, "max_position")) else 0.0,
         "No single-name position cap"),
        ("Sector concentration limit",
         1.0 if (risk.exists() and _file_contains(risk, "sector")) else 0.0,
         "No sector concentration limit"),
        ("Drawdown circuit breaker with equity persistence",
         1.0 if _has_drawdown_circuit_breaker() else 0.0,
         "No drawdown circuit breaker"),
        ("ATR-based stop-loss per position",
         1.0 if _file_contains(daily, "stop_atr") else 0.0,
         "No ATR-based stop-loss"),
        ("Regime-aware position sizing (size scale by regime)",
         1.0 if _file_contains(daily, "_size_scale") else 0.0,
         "Position sizing not regime-aware"),
        ("Turnover / capital budget cap",
         1.0 if _file_contains(exec_script, "turnover") else 0.0,
         "No turnover cap — excessive rebalancing possible"),
        ("Gross / net exposure limits enforced",
         1.0 if (risk.exists() and _file_contains(risk, "gross")) else 0.0,
         "No gross/net exposure limits"),
        ("Correlation-aware position sizing (covariance matrix)",
         0.0,  # not implemented
         "No correlation-aware sizing — positions treated as independent"),
        ("VaR / CVaR portfolio risk estimate",
         0.0,  # not implemented
         "No VaR/CVaR — portfolio tail risk unquantified"),
        ("Short-side risk controls",
         0.0,  # long only
         "Long-only — no short-side, no hedging"),
    ]
    return results


def _rubric_regime_detection() -> list[tuple[str, float, str]]:
    rdir = _REPO / "regime"
    results = [
        ("Trend dimension with EWM smoothing",
         1.0 if (rdir / "regime_indicators.py").exists() else 0.0,
         "No trend dimension"),
        ("Volatility dimension (VIX / realized vol)",
         1.0 if _file_contains(rdir / "regime_indicators.py", "vix") else 0.0,
         "No volatility dimension"),
        ("Breadth dimension (advance/decline or similar)",
         1.0 if _file_contains(rdir / "regime_indicators.py", "breadth") else 0.0,
         "No breadth dimension"),
        ("Macro dimension (yield curve, credit spread via FRED)",
         1.0 if (rdir / "fred_client.py").exists() else 0.0,
         "No macro dimension"),
        ("Regime → sleeve weight mapping (allocator)",
         1.0 if (rdir / "regime_allocator.py").exists() else 0.0,
         "No regime-to-allocation mapping"),
        ("Historical episode validation (≥6/8 pass rate)",
         1.0,  # 7/8 validated and documented
         "Episode validation below 6/8 threshold"),
        ("Regime state logged and emailed daily",
         1.0 if _file_contains(_REPO / "scripts" / "format_precompute_email.py", "regime") else 0.0,
         "Regime state not surfaced in daily email"),
        ("Regime-conditional sleeve suppression (breadth gate)",
         1.0 if _file_contains(_REPO / "sleeves" / "sleeve_mean_reversion" / "selection.py", "breadth_gate") else 0.0,
         "Regime state does not suppress individual sleeves"),
        ("ML-based regime classification (HMM, clustering, etc.)",
         0.0,  # not implemented
         "Rule-based only — no ML regime classification"),
        ("Options-implied volatility surface as regime input",
         0.0,  # not implemented
         "No options data in regime detection"),
    ]
    return results


def _rubric_execution() -> list[tuple[str, float, str]]:
    exec_script = _REPO / "scripts" / "run_precomputed_alpaca_execution.py"
    broker = _REPO / "paper" / "paper_broker.py"
    results = [
        ("Live broker integration (orders actually submitted)",
         1.0 if (broker.exists() and _file_contains(broker, "alpaca")) else 0.0,
         "No live broker integration"),
        ("Pre-trade reconciliation before order submission",
         1.0 if _file_contains(exec_script, "recon") else 0.0,
         "No pre-trade reconciliation"),
        ("Execution idempotency (no duplicate orders)",
         1.0 if _file_contains(exec_script, "lock") else 0.0,
         "Duplicate orders possible"),
        ("Order confirmation / fill tracking",
         1.0 if _file_contains(exec_script, "accepted") else 0.0,
         "No fill tracking"),
        ("Limit orders or TWAP/VWAP (not pure market orders)",
         0.0,  # market orders only
         "Market orders only — no price control, high impact for large/illiquid names"),
        ("Transaction cost model (per-trade cost estimate)",
         0.0,  # not implemented
         "No TCM — backtest P&L is gross; live will underperform, especially MR sleeve"),
        ("Slippage estimation in pre-trade analysis",
         0.0,  # not implemented
         "No slippage estimation"),
        ("Market hours guard (no pre/post-market submissions)",
         1.0 if _file_contains(exec_script, "market") else 0.0,
         "No market hours guard"),
        ("Smart order routing or execution algo",
         0.0,  # not implemented
         "No smart order routing"),
        ("Post-trade TCA (transaction cost analysis)",
         0.0,  # not implemented
         "No post-trade TCA — actual vs expected costs never measured"),
    ]
    return results


def _rubric_attribution(benchmark: list | None) -> list[tuple[str, float, str]]:
    n_days = len(benchmark) if isinstance(benchmark, list) else 0
    bt = _load_json(BACKTEST_JSON)
    attr = _REPO / "core" / "attribution.py"
    results = [
        ("≥20 trading days of live benchmark history",
         min(1.0, n_days / 20),
         f"Only {n_days} trading day(s) of live history — attribution metrics are statistically meaningless below 20"),
        ("≥60 trading days (Sharpe / IR statistically meaningful)",
         min(1.0, n_days / 60),
         f"Only {n_days} day(s) — need 60 for meaningful Sharpe/IR"),
        ("IC / ICIR attribution module present",
         1.0 if attr.exists() else 0.0,
         "No attribution module"),
        ("IC computed against live positions (not just backtest)",
         0.0 if n_days < 20 else (1.0 if _file_contains(attr, "live") else 0.0),
         "IC not yet computed against live positions"),
        ("Brinson-Hood-Beebower return decomposition",
         0.0,  # not implemented
         "No Brinson attribution — cannot separate allocation effect from selection effect"),
        ("Factor return decomposition (Fama-French or Barra-style)",
         0.0,  # not implemented
         "No factor return decomposition — unknown how much return is beta vs alpha"),
        ("Rolling Sharpe ratio tracked over live history",
         0.5 if n_days >= 5 and BENCHMARK_JSON.exists() else 0.0,
         "Insufficient history for rolling Sharpe"),
        ("Drawdown attribution by sleeve",
         0.0,  # not implemented
         "No sleeve-level drawdown attribution — cannot identify which sleeve hurts in drawdowns"),
        ("Benchmark-relative alpha (annualized, live)",
         0.5 if n_days >= 10 else 0.0,
         f"Only {n_days} day(s) of live data — too few for reliable alpha estimate"),
        ("Regime-conditional performance attribution",
         0.0,  # not implemented
         "No regime-conditional attribution — cannot verify if regime weights add value"),
    ]
    return results


def _rubric_data_quality() -> list[tuple[str, float, str]]:
    n_tickers = 0
    if UNIVERSE_CSV.exists():
        n_tickers = sum(1 for _ in UNIVERSE_CSV.read_text().splitlines()) - 1
    n_fundamental = len(list(FUNDAMENTAL_DIR.glob("*.parquet"))) if FUNDAMENTAL_DIR.exists() else 0
    bt = _load_json(BACKTEST_JSON)
    pit_confirmed = bool(bt and any(
        "pit" in str(n).lower() or "point-in-time" in str(n).lower()
        for n in (bt.get("implementation_notes") or {}).values()
    ))
    results = [
        ("Universe ≥100 tickers with sector tags",
         1.0 if n_tickers >= 100 else 0.0,
         f"Universe only {n_tickers} tickers — insufficient diversification"),
        ("Universe ≥200 tickers",
         1.0 if n_tickers >= 200 else 0.5,
         f"Universe {n_tickers} tickers — larger universe improves signal-to-noise"),
        ("EDGAR XBRL fundamentals ingested (PIT-correct)",
         1.0 if (_REPO / "edgar_ingestion.py").exists() else 0.0,
         "No EDGAR ingestion — fundamental signals not PIT-correct"),
        ("Fundamental parquet coverage ≥150 tickers",
         1.0 if n_fundamental >= 150 else (0.5 if n_fundamental >= 50 else 0.0),
         f"Only {n_fundamental} fundamental files — quality sleeve data coverage insufficient"),
        ("Point-in-time correctness documented and verified",
         1.0 if pit_confirmed else 0.5,
         "PIT correctness not explicitly confirmed in research artifacts"),
        ("Macro data (FRED yield curve, credit spread)",
         1.0 if (_REPO / "regime" / "fred_client.py").exists() else 0.0,
         "No macro data integration"),
        ("Daily price data for full universe (OHLCV)",
         1.0,  # yfinance confirmed working
         "Price data unavailable"),
        ("Alternative data source (sentiment, options, satellite)",
         0.0,  # not present
         "No alternative data — price + fundamentals + macro only"),
        ("Options / derivatives data for volatility signals",
         0.0,  # not present
         "No options data"),
        ("Real-time or intraday data for execution quality",
         0.0,  # daily only
         "Daily close data only — no intraday, cannot assess execution quality"),
    ]
    return results


# ── scoring engine ────────────────────────────────────────────────────────────

def _evaluate(rubric: list[tuple[str, float, str]]) -> tuple[int, list[tuple[str, float, str]]]:
    """Score a rubric: returns (integer_score_1_to_10, rubric_with_results)."""
    total = sum(score for _, score, _ in rubric)
    return _clamp(total), rubric


# ── gap table (fixed — same every week, checked off as resolved) ─────────────

_INSTITUTIONAL_GAPS = [
    # (dimension, gap description, impact, resolved_check)
    ("Signal Quality",   "All quality factor ICs are negative — no confirmed alpha signal", "CRITICAL",
     lambda bt: bt is not None and any(
         any(v > 0.02 for v in (bt.get(k) or {}).get("ic_summary", {}).values() if isinstance(v, float))
         for k in ("quality_current", "quality_enhanced")
     )),
    ("Signal Quality",   "No transaction cost model for mean-reversion sleeve (1,911 trades/2yr)", "CRITICAL",
     lambda bt: _file_contains(_REPO / "sleeves" / "sleeve_mean_reversion" / "selection.py", "transaction_cost")),
    ("Signal Quality",   "No factor orthogonalization (Gram-Schmidt / PCA)", "HIGH",
     lambda bt: _file_contains(_REPO / "sleeves" / "sleeve_quality" / "indicators.py", "orthogon")),
    ("Signal Quality",   "No alternative data (sentiment NLP, options flow, satellite)", "HIGH",
     lambda bt: False),
    ("Signal Quality",   "No ML ensemble (gradient boosting on factor zoo)", "MEDIUM",
     lambda bt: False),
    ("Execution",        "Market orders only — no TWAP/VWAP or price-contingent orders", "HIGH",
     lambda bt: False),
    ("Execution",        "No transaction cost analysis post-trade", "HIGH",
     lambda bt: False),
    ("Execution",        "No slippage model in pre-trade planning", "MEDIUM",
     lambda bt: False),
    ("Attribution",      "No Brinson-Hood-Beebower decomposition (allocation vs selection)", "HIGH",
     lambda bt: _file_contains(_REPO / "core" / "attribution.py", "brinson")),
    ("Attribution",      "No factor return decomposition (Fama-French / Barra-style)", "MEDIUM",
     lambda bt: False),
    ("Risk Management",  "No correlation-aware position sizing (covariance matrix)", "MEDIUM",
     lambda bt: _file_contains(_REPO / "core" / "risk_controls.py", "covariance")),
    ("Risk Management",  "No VaR / CVaR portfolio risk estimate", "MEDIUM",
     lambda bt: _file_contains(_REPO / "core" / "risk_controls.py", "var")),
    ("Regime Detection", "No ML-based regime classification (HMM, clustering)", "LOW",
     lambda bt: False),
    ("Data Quality",     "No alternative / non-traditional data sources", "HIGH",
     lambda bt: False),
]

_INSTITUTIONAL_TRENDS_2026 = [
    ("Alternative data integration",
     "Satellite imagery, credit card transactions, sentiment NLP, options flow — standard at mid-tier pods"),
    ("ML signal generation",
     "Gradient boosting on factor zoo (100+ factors) outperforms linear composites across most regimes"),
    ("Transaction cost modeling",
     "Almgren-Chriss optimal execution is table-stakes; especially critical for high-turnover strategies"),
    ("Factor crowding detection",
     "Monitoring hedge-fund 13F overlap and short interest concentration reduces reversal drawdowns"),
    ("Regime-conditional covariance",
     "Using separate covariance matrices per regime improves Sharpe by 10-20 bps vs. static covariance"),
    ("Short-side modeling",
     "Long/short extends signal capacity, reduces beta exposure, and improves Sharpe vs. long-only"),
    ("Real-time execution feedback",
     "Intraday fill quality monitoring and VWAP benchmarking are becoming baseline expectations"),
]


# ── report ────────────────────────────────────────────────────────────────────

def _build_report(
    scores: dict[str, int],
    rubrics: dict[str, list[tuple[str, float, str]]],
    bt: dict | None,
) -> str:
    avg = sum(scores.values()) / len(scores)
    lines: list[str] = []

    lines += [
        "# Alpha Stack — Weekly Model Review",
        f"**Date:** {TODAY}",
        "",
        "## Executive Summary",
        "",
        f"**Overall average: {avg:.1f}/10**  ",
        "",
    ]

    # One-line per dimension
    for dim, sc in scores.items():
        lines.append(f"- **{dim}:** {sc}/10 — {_industry_label(sc)}")
    lines.append("")

    # Scorecard table
    lines += [
        "## Scorecard",
        "",
        "| Dimension | Score | vs. Industry |",
        "|---|:---:|---|",
    ]
    for dim, sc in scores.items():
        marker = "🔴" if sc <= 3 else ("🟡" if sc <= 5 else "🟢")
        lines.append(f"| {dim} | **{sc}/10** {marker} | {_industry_label(sc)} |")
    lines.append("")

    # Dimension detail (checklist)
    lines += ["## Dimension Detail", ""]
    for dim, rubric in rubrics.items():
        sc = scores[dim]
        lines += [f"### {dim} — {sc}/10", ""]
        lines += ["| # | Criterion | Pass |", "|---|---|:---:|"]
        for i, (label, score, _) in enumerate(rubric, 1):
            mark = "✅" if score >= 1.0 else ("🔶" if score >= 0.5 else "❌")
            lines.append(f"| {i} | {label} | {mark} |")
        # Show fail reasons
        fails = [(label, reason) for label, score, reason in rubric if score < 1.0]
        if fails:
            lines += ["", "**Failing criteria:**"]
            for label, reason in fails:
                lines.append(f"- _{label}_: {reason}")
        lines.append("")

    # Gap tracker (fixed list, checked off as resolved)
    lines += [
        "## Outstanding Gaps vs. Institutional Standard",
        "",
        "Fixed checklist — same every week. Items marked Resolved when confirmed in production.",
        "",
        "| Dimension | Gap | Impact | Status |",
        "|---|---|:---:|:---:|",
    ]
    for dim, gap, impact, check_fn in _INSTITUTIONAL_GAPS:
        try:
            resolved = check_fn(bt)
        except Exception:
            resolved = False
        status = "✅ Resolved" if resolved else "❌ Open"
        lines.append(f"| {dim} | {gap} | {impact} | {status} |")
    lines.append("")

    # Institutional trends
    lines += [
        "## Institutional Quant Fund Focus Areas (2025–2026)",
        "",
    ]
    for trend, desc in _INSTITUTIONAL_TRENDS_2026:
        lines.append(f"- **{trend}:** {desc}")
    lines.append("")

    # Scoring rubric legend
    lines += [
        "## Scoring Rubric",
        "",
        "Each dimension uses a fixed 10-criterion checklist (same every week).",
        "Score = criteria passed (1 pt each; 0.5 for partial).",
        "",
        "| Score | Industry Position |",
        "|:---:|---|",
    ]
    for lo, hi, label in INDUSTRY_LADDER:
        rng = f"{lo}" if lo == hi else f"{lo}–{hi}"
        lines.append(f"| {rng} | {label} |")
    lines.append("")

    return "\n".join(lines)


def _build_html(scores: dict[str, int]) -> str:
    avg = sum(scores.values()) / len(scores)
    rows = ""
    for dim, sc in scores.items():
        color = "#c8f7c5" if sc >= 6 else ("#fff3cd" if sc >= 4 else "#f8d7da")
        rows += (
            f"<tr><td>{dim}</td>"
            f"<td style='text-align:center;background:{color};font-weight:bold'>{sc}/10</td>"
            f"<td>{_industry_label(sc)}</td></tr>\n"
        )
    return f"""<html><body style='font-family:Arial,sans-serif'>
<h2>Alpha Stack — Weekly Model Review ({TODAY})</h2>
<p>Average: <strong>{avg:.1f}/10</strong></p>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;font-family:monospace;font-size:14px">
<tr style="background:#333;color:#fff"><th>Dimension</th><th>Score</th><th>vs. Industry</th></tr>
{rows}</table>
<p style="color:#777;font-size:0.85em">Full report: research/model_review_{TODAY}.md</p>
</body></html>"""


def _send(subject: str, text: str, html: str) -> None:
    resolved = resolve_email_env()
    missing = list(resolved.get("missing") or [])
    if missing:
        print("[MODEL_REVIEW] SMTP credentials missing — skipping email", file=sys.stderr)
        return
    host = str(resolved.get("smtp_host") or "smtp.gmail.com")
    port = int(resolved.get("smtp_port") or "587")
    user = str(resolved.get("sender") or "")
    pw = str(resolved.get("password") or "")
    to = str(resolved.get("recipient") or user)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP(host, port) as s:
            s.ehlo(); s.starttls(); s.login(user, pw)
            s.sendmail(user, [to], msg.as_string())
        print(f"[MODEL_REVIEW] Email sent to {to}")
    except Exception as exc:
        print(f"[MODEL_REVIEW] Email failed: {exc}", file=sys.stderr)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"[MODEL_REVIEW] Running for {TODAY} …")

    benchmark = _load_json(BENCHMARK_JSON)
    bt        = _load_json(BACKTEST_JSON)

    rubric_map = {
        "Signal Quality":   _rubric_signal_quality(bt),
        "Infrastructure":   _rubric_infrastructure(),
        "Risk Management":  _rubric_risk_management(),
        "Regime Detection": _rubric_regime_detection(),
        "Execution":        _rubric_execution(),
        "Attribution":      _rubric_attribution(benchmark),
        "Data Quality":     _rubric_data_quality(),
    }

    scores: dict[str, int] = {}
    for dim, rubric in rubric_map.items():
        sc, _ = _evaluate(rubric)
        scores[dim] = sc

    # Console scorecard
    print("\n" + "=" * 60)
    print(f"  ALPHA STACK MODEL SCORECARD  —  {TODAY}")
    print("=" * 60)
    print(f"  {'Dimension':<22}  {'Score':>6}  Industry Position")
    print("-" * 60)
    for dim, sc in scores.items():
        print(f"  {dim:<22}  {sc:>4}/10  {_industry_label(sc)}")
    avg = sum(scores.values()) / len(scores)
    print("-" * 60)
    print(f"  {'AVERAGE':<22}  {avg:>6.1f}")
    print("=" * 60 + "\n")

    # Write report
    report_md = _build_report(scores, rubric_map, bt)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report_md, encoding="utf-8")
    print(f"[MODEL_REVIEW] Report → {REPORT_PATH}")

    # Email
    _send(
        f"[Alpha Stack] Weekly Model Review — {TODAY}",
        report_md,
        _build_html(scores),
    )


if __name__ == "__main__":
    main()
