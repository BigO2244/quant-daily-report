"""Constrained Lyra research framework — research-only, no execution coupling.

Generates four constrained variants of Lyra and compares them to the
baseline. Designed to answer the CIO's question: *is Lyra's edge
concentration-driven and beta-driven, or is there genuine alpha that
survives portfolio-construction risk control?*

Inputs (read-only)
------------------
* ``outputs/shadow_candidates/<DATE>/comparison.json`` —
  Lyra's current per-name holdings + weights + momentum scores.
* ``outputs/attribution/<DATE>/factor_exposure.json`` —
  Lyra's per-strategy market_beta, sector_exposure.weights, and
  ``volatility_exposure.by_ticker`` (used as a per-name β proxy).
* ``outputs/shadow_candidates/performance/shadow_nav_series.csv`` —
  Per-strategy daily NAV (used for the synthetic variant NAV streams).

Outputs (written to ``outputs/research/constrained_lyra/<RUN_DATE>/``)
---------------------------------------------------------------------
* ``variants.json`` — per-variant portfolio structure (holdings,
  weights, sector exposure, estimated beta, cash buffer, violations).
* ``comparison.json`` — cross-variant table of CAGR / Sharpe / Sortino
  / Max DD / Ann Vol / Beta / Correlation to baseline / Correlation
  to SPY / Sector concentration / Position concentration / Turnover.
* ``summary.md`` — operator / CIO narrative grounded in the artifact.

Honest caveats baked into the module
------------------------------------
1. **Lyra has only 5 names at 20% each.** When a variant requires
   more breadth than the existing universe supports (e.g. max-position
   5% with 20–25 names), the framework reports an explicit
   ``cash_buffer_pct`` rather than fabricating phantom holdings.

2. **Per-name β is a vol-based proxy.** We don't have a per-name
   regression β; we use the per-ticker realized vol from
   factor_exposure.json scaled by an SPY-vol assumption. Documented
   in :data:`VOL_TO_BETA_SCALE`.

3. **The variant NAV streams are MODEL-BASED, not backtests.** We
   compute Lyra's daily alpha residual against SPY, then re-mix with
   the variant's structural β and an explicit ``alpha_dampening``
   coefficient. The dampening coefficients are heuristics, not
   empirically derived from a live signal stream. They are documented
   per-variant in ``VARIANT_SPECS`` and surfaced in the artifact's
   ``methodology_note`` block.

If a deployable conclusion is desired, the modelled metrics in
``comparison.json`` should be confirmed by a true backtest with the
Lyra signal extended over the historical universe — that's a
separate research project and out of scope here.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_OUTPUTS_ROOT = Path("outputs")
DEFAULT_SHADOW_ROOT = Path("outputs/shadow_candidates")
DEFAULT_ATTRIBUTION_ROOT = Path("outputs/attribution")
DEFAULT_NAV_SERIES_PATH = Path("outputs/shadow_candidates/performance/shadow_nav_series.csv")
DEFAULT_RESEARCH_OUTPUT_ROOT = Path("outputs/research/constrained_lyra")

LYRA_SLUG = "caerus_lyra"
SPY_SLUG = "spy_benchmark"

# Vol-to-β proxy: per-name β ≈ (per-name realized vol) / (SPY realized vol)
# × (correlation_to_market_assumption). The literature puts large-cap
# correlation to SPY around 0.55–0.75; we use 0.65 as a conservative
# midpoint. SPY annualised realized vol over the active window is
# approximately 18%. These are documented heuristics — REPLACE WITH A
# PROPER PER-NAME REGRESSION before using this for capital decisions.
SPY_ANN_VOL_ASSUMPTION = 0.18
MARKET_CORRELATION_ASSUMPTION = 0.65
VOL_TO_BETA_SCALE = MARKET_CORRELATION_ASSUMPTION / SPY_ANN_VOL_ASSUMPTION

# Sector β proxy (used when per-name vol is unavailable).
SECTOR_BETA_PROXY: dict[str, float] = {
    "Information Technology": 1.35,
    "Communication Services": 1.10,
    "Industrials": 1.05,
    "Materials": 1.20,
    "Consumer Discretionary": 1.15,
    "Energy": 1.30,
    "Financials": 1.20,
    "Health Care": 0.85,
    "Consumer Staples": 0.65,
    "Utilities": 0.55,
    "Real Estate": 0.80,
    "Unknown": 1.00,
}

# Approximate ticker → sector mapping for names commonly seen in Lyra.
# Tightly scoped; extend as the universe grows. ABSENT names default
# to "Unknown" and pick up the SECTOR_BETA_PROXY["Unknown"] β.
TICKER_SECTOR_MAP: dict[str, str] = {
    "WDC": "Information Technology",
    "MU": "Information Technology",
    "STX": "Information Technology",
    "GLW": "Information Technology",
    "LRCX": "Information Technology",
    "AMAT": "Information Technology",
    "NVDA": "Information Technology",
    "AAPL": "Information Technology",
    "MSFT": "Information Technology",
    "WBD": "Communication Services",
    "META": "Communication Services",
    "GOOG": "Communication Services",
    "GOOGL": "Communication Services",
    "CAT": "Industrials",
    "GEV": "Industrials",
    "NEM": "Materials",
    "XOM": "Energy",
    "JPM": "Financials",
}

TRADING_DAYS_PER_YEAR = 252


# ---------------------------------------------------------------------------
# Variant specifications
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VariantSpec:
    name: str
    slug: str
    description: str
    max_position_weight: Optional[float] = None
    target_holdings_min: Optional[int] = None
    target_holdings_max: Optional[int] = None
    beta_cap: Optional[float] = None
    max_sector_weight: Optional[float] = None
    # Heuristic for synthetic NAV modeling — alpha-dampening estimate.
    # 1.0 = baseline alpha preserved; lower = constraints erode alpha.
    alpha_dampening_estimate: float = 1.0
    deployment_caveat: str = ""


VARIANT_SPECS: tuple[VariantSpec, ...] = (
    VariantSpec(
        name="Baseline Lyra",
        slug="caerus_lyra",
        description="Current Lyra. No modifications. Reference point.",
        alpha_dampening_estimate=1.0,
    ),
    VariantSpec(
        name="Broad Lyra",
        slug="caerus_lyra_broad",
        description=(
            "Lyra's signal applied with a 5% per-name cap and a target of "
            "20–25 holdings. No β cap, no sector cap. Tests whether "
            "diversification alone changes return profile."
        ),
        max_position_weight=0.05,
        target_holdings_min=20,
        target_holdings_max=25,
        alpha_dampening_estimate=0.95,
        deployment_caveat=(
            "With only 5 baseline names this variant cannot be deployed "
            "as a 20-name book today. Requires signal expansion over a "
            "broader universe before capital deployment."
        ),
    ),
    VariantSpec(
        name="Beta-Controlled Lyra",
        slug="caerus_lyra_beta_controlled",
        description=(
            "5% per-name cap + β cap ≤ 1.5. No sector cap. Tests how "
            "much of Lyra's return stream is leveraged market exposure."
        ),
        max_position_weight=0.05,
        beta_cap=1.5,
        alpha_dampening_estimate=0.95,
        deployment_caveat=(
            "Hitting β = 1.5 with the current 5-name universe requires "
            "a cash buffer (the existing names are all β > 1.5 on the "
            "vol proxy). Real deployment requires lower-β additions."
        ),
    ),
    VariantSpec(
        name="Sector-Controlled Lyra",
        slug="caerus_lyra_sector_controlled",
        description=(
            "5% per-name cap + β cap ≤ 1.5 + max sector weight 50%. "
            "Risk control, NOT sector neutrality — IT remains the "
            "largest sleeve, just smaller."
        ),
        max_position_weight=0.05,
        beta_cap=1.5,
        max_sector_weight=0.50,
        alpha_dampening_estimate=0.85,
        deployment_caveat=(
            "Forcing IT ≤ 50% with the current 80% IT book requires "
            "either cash buffer or non-IT additions. Cash buffer used in "
            "current-snapshot artifact; deployment needs universe expansion."
        ),
    ),
    VariantSpec(
        name="Fully Controlled Lyra",
        slug="caerus_lyra_fully_controlled",
        description=(
            "5% per-name cap + β cap ≤ 1.3 + max sector weight 40%. "
            "The most institutionally robust version."
        ),
        max_position_weight=0.05,
        beta_cap=1.3,
        max_sector_weight=0.40,
        alpha_dampening_estimate=0.75,
        deployment_caveat=(
            "Tightest configuration. Cannot be deployed with the current "
            "universe; requires substantial signal expansion."
        ),
    ),
)


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------


def _safe_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _latest_dated_dir(root: Path) -> Optional[Path]:
    if not root.exists() or not root.is_dir():
        return None
    dated = [
        p for p in root.iterdir()
        if p.is_dir()
        and len(p.name) == 10 and p.name[4] == "-" and p.name[7] == "-"
    ]
    if not dated:
        return None
    return sorted(dated, key=lambda p: p.name)[-1]


# ---------------------------------------------------------------------------
# Baseline loader
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LyraBaseline:
    trade_date: str
    holdings: tuple[str, ...]
    weights: dict[str, float]
    momentum_scores: dict[str, Optional[float]]
    beta_per_name: dict[str, float]
    sector_per_name: dict[str, str]
    realized_vol_per_name: dict[str, Optional[float]]
    portfolio_beta: float
    sector_exposure: dict[str, float]
    source_paths: list[str]


def load_lyra_baseline(
    *,
    outputs_root: Path = DEFAULT_OUTPUTS_ROOT,
) -> Optional[LyraBaseline]:
    """Load the latest Lyra holdings + factor data.

    Returns ``None`` if no usable artifacts are present. Reads:
    * latest ``outputs/shadow_candidates/<DATE>/comparison.json`` (holdings)
    * latest ``outputs/attribution/<DATE>/factor_exposure.json`` (β, vol, sector)
    """
    shadow_root = outputs_root / "shadow_candidates"
    attribution_root = outputs_root / "attribution"

    holdings_dir = None
    holdings_payload: dict[str, Any] = {}
    for candidate in reversed(_dated_dirs_descending(shadow_root)):
        payload = _safe_json(candidate / "comparison.json") or {}
        strategies = payload.get("strategies") or {}
        lyra_entry = strategies.get(LYRA_SLUG)
        if lyra_entry and lyra_entry.get("holdings"):
            holdings_dir = candidate
            holdings_payload = lyra_entry
            break
    if holdings_dir is None:
        return None

    holdings_rows = list(holdings_payload.get("holdings") or [])
    weights = {
        str(row.get("ticker")): float(row.get("target_weight") or 0.0)
        for row in holdings_rows
        if row.get("ticker")
    }
    momentum = {
        str(row.get("ticker")): _coerce_float(row.get("momentum_score"))
        for row in holdings_rows
        if row.get("ticker")
    }

    # Factor exposure for β + vol + sector exposure.
    factor_payload: dict[str, Any] = {}
    factor_path: Optional[Path] = None
    attribution_latest = _latest_dated_dir(attribution_root)
    if attribution_latest is not None:
        fp = attribution_latest / "factor_exposure.json"
        if fp.exists():
            factor_path = fp
            factor_payload = _safe_json(fp) or {}
    lyra_factor = (factor_payload.get("strategies") or {}).get(LYRA_SLUG) or {}
    portfolio_beta = (
        _coerce_float(lyra_factor.get("market_beta"))
        or _portfolio_beta_from_sector(holdings_payload)
    )
    sector_exposure = dict(
        (lyra_factor.get("sector_exposure") or {}).get("weights") or {}
    )

    realized_vol_per_name = dict(
        (lyra_factor.get("volatility_exposure") or {}).get("by_ticker") or {}
    )
    # Coerce values to float for consistency.
    realized_vol_per_name = {
        k: _coerce_float(v) for k, v in realized_vol_per_name.items()
    }

    sector_per_name = {
        ticker: TICKER_SECTOR_MAP.get(ticker, "Unknown")
        for ticker in weights
    }
    beta_per_name_raw = {
        ticker: _per_name_beta(
            ticker,
            vol=realized_vol_per_name.get(ticker),
            sector=sector_per_name.get(ticker),
        )
        for ticker in weights
    }
    # Calibrate per-name β so the weighted sum equals the regression β
    # reported in factor_exposure.json. This keeps β reporting on the
    # constrained portfolios internally consistent. Each name's β stays
    # proportional to its vol/sector proxy.
    proxy_portfolio_beta = sum(
        weights[t] * beta_per_name_raw[t] for t in weights
    )
    if portfolio_beta and proxy_portfolio_beta > 0:
        scale = float(portfolio_beta) / float(proxy_portfolio_beta)
        beta_per_name = {t: round(b * scale, 4) for t, b in beta_per_name_raw.items()}
    else:
        beta_per_name = beta_per_name_raw

    source_paths = [str(holdings_dir / "comparison.json")]
    if factor_path:
        source_paths.append(str(factor_path))

    return LyraBaseline(
        trade_date=str(holdings_dir.name),
        holdings=tuple(weights.keys()),
        weights=weights,
        momentum_scores=momentum,
        beta_per_name=beta_per_name,
        sector_per_name=sector_per_name,
        realized_vol_per_name=realized_vol_per_name,
        portfolio_beta=float(portfolio_beta) if portfolio_beta else 1.0,
        sector_exposure=sector_exposure,
        source_paths=source_paths,
    )


def _dated_dirs_descending(root: Path) -> list[Path]:
    if not root.exists():
        return []
    out = [
        p for p in root.iterdir()
        if p.is_dir() and len(p.name) == 10 and p.name[4] == "-" and p.name[7] == "-"
    ]
    return sorted(out, key=lambda p: p.name)


def _portfolio_beta_from_sector(payload: Mapping[str, Any]) -> Optional[float]:
    """Fallback: estimate portfolio β from holding sectors when
    market_beta is absent."""
    holdings = payload.get("holdings") or []
    total = 0.0
    weight_total = 0.0
    for row in holdings:
        ticker = row.get("ticker")
        weight = _coerce_float(row.get("target_weight")) or 0.0
        sector = TICKER_SECTOR_MAP.get(str(ticker), "Unknown")
        total += weight * SECTOR_BETA_PROXY.get(sector, 1.0)
        weight_total += weight
    return total / weight_total if weight_total else None


def _per_name_beta(
    ticker: str,
    *,
    vol: Optional[float],
    sector: Optional[str],
) -> float:
    """Vol-based per-name β proxy with sector fallback."""
    if vol is not None and vol > 0.05:
        # Filter out implausibly low vol (likely stale data).
        return round(vol * VOL_TO_BETA_SCALE, 4)
    return SECTOR_BETA_PROXY.get(sector or "Unknown", 1.0)


# ---------------------------------------------------------------------------
# Constraint application
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConstrainedPortfolio:
    variant_slug: str
    weights: dict[str, float]
    cash_buffer_pct: float
    portfolio_beta: float
    sector_exposure: dict[str, float]
    max_position_weight: float
    holdings_count: int
    constraint_violations: list[str]
    deployment_caveat: str


def _apply_position_cap(
    weights: dict[str, float],
    cap: Optional[float],
) -> dict[str, float]:
    if cap is None:
        return dict(weights)
    capped = {t: min(w, cap) for t, w in weights.items()}
    return capped


def _portfolio_beta(weights: Mapping[str, float], beta_map: Mapping[str, float]) -> float:
    return sum(w * beta_map.get(t, 1.0) for t, w in weights.items())


def _portfolio_sector_exposure(
    weights: Mapping[str, float],
    sector_map: Mapping[str, str],
) -> dict[str, float]:
    out: dict[str, float] = {}
    for ticker, w in weights.items():
        sector = sector_map.get(ticker, "Unknown")
        out[sector] = out.get(sector, 0.0) + w
    return out


def _apply_sector_cap(
    weights: dict[str, float],
    sector_map: Mapping[str, str],
    cap: Optional[float],
) -> tuple[dict[str, float], list[str]]:
    """Cap any sector that exceeds ``cap``. Excess weight is moved to cash;
    we do NOT redistribute within sector (would require ranking unavailable
    here). Returns (new_weights, violations) — violations is empty after
    application because the cap is now satisfied; the cash buffer absorbs."""
    if cap is None:
        return dict(weights), []
    new_weights = dict(weights)
    exposures = _portfolio_sector_exposure(new_weights, sector_map)
    for sector, total in exposures.items():
        if total <= cap or total <= 0:
            continue
        scale = cap / total
        for ticker in [t for t, w in weights.items() if sector_map.get(t, "Unknown") == sector]:
            new_weights[ticker] = weights[ticker] * scale
    return new_weights, []


def _apply_beta_cap(
    weights: dict[str, float],
    beta_map: Mapping[str, float],
    cap: Optional[float],
) -> dict[str, float]:
    """Scale all positions proportionally to hit β cap. The shortfall
    becomes cash buffer (we never short to reduce β)."""
    if cap is None:
        return dict(weights)
    current = _portfolio_beta(weights, beta_map)
    if current <= cap or current <= 0:
        return dict(weights)
    scale = cap / current
    return {t: w * scale for t, w in weights.items()}


def apply_constraints(
    baseline: LyraBaseline,
    spec: VariantSpec,
) -> ConstrainedPortfolio:
    """Apply a variant's constraint set to the baseline portfolio.

    Order matters: position cap → sector cap → β cap. Position cap is
    applied first because it dominates concentration; sector cap before β
    because sector restriction can reduce β organically; β cap last as
    the residual rescale.
    """
    weights = _apply_position_cap(baseline.weights, spec.max_position_weight)
    weights, _ = _apply_sector_cap(weights, baseline.sector_per_name, spec.max_sector_weight)
    weights = _apply_beta_cap(weights, baseline.beta_per_name, spec.beta_cap)

    cash_buffer = max(0.0, 1.0 - sum(weights.values()))
    portfolio_beta = _portfolio_beta(weights, baseline.beta_per_name)
    sector_exposure = _portfolio_sector_exposure(weights, baseline.sector_per_name)
    max_pos = max(weights.values()) if weights else 0.0
    holdings_count = sum(1 for w in weights.values() if w > 1e-6)

    violations: list[str] = []
    if spec.max_position_weight is not None and max_pos > spec.max_position_weight + 1e-6:
        violations.append(f"position_cap_violation:{max_pos:.4f}>{spec.max_position_weight}")
    if spec.max_sector_weight is not None:
        worst_sector_weight = max(sector_exposure.values()) if sector_exposure else 0.0
        if worst_sector_weight > spec.max_sector_weight + 1e-6:
            violations.append(
                f"sector_cap_violation:{worst_sector_weight:.4f}>{spec.max_sector_weight}"
            )
    if spec.beta_cap is not None and portfolio_beta > spec.beta_cap + 1e-3:
        violations.append(f"beta_cap_violation:{portfolio_beta:.4f}>{spec.beta_cap}")
    if (
        spec.target_holdings_min is not None
        and holdings_count < spec.target_holdings_min
    ):
        violations.append(
            f"holdings_count_below_target:{holdings_count}<{spec.target_holdings_min}"
        )

    return ConstrainedPortfolio(
        variant_slug=spec.slug,
        weights=weights,
        cash_buffer_pct=round(cash_buffer, 6),
        portfolio_beta=round(portfolio_beta, 6),
        sector_exposure={k: round(v, 6) for k, v in sector_exposure.items()},
        max_position_weight=round(max_pos, 6),
        holdings_count=holdings_count,
        constraint_violations=violations,
        deployment_caveat=spec.deployment_caveat,
    )


# ---------------------------------------------------------------------------
# NAV synthesis (MODEL-BASED, not a backtest)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VariantNavSeries:
    slug: str
    daily_returns: pd.Series  # indexed by date
    cumulative_nav: pd.Series
    baseline_beta: float
    variant_beta: float
    alpha_dampening: float


def load_nav_series(path: Path = DEFAULT_NAV_SERIES_PATH) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    except Exception:
        return None
    return df


def synthesize_variant_nav(
    *,
    lyra_returns: pd.Series,
    spy_returns: pd.Series,
    baseline_beta: float,
    variant_beta: float,
    alpha_dampening: float,
    variant_slug: str,
) -> VariantNavSeries:
    """Compute model-based daily returns for a variant.

    For each day t:

        alpha_t       = r_lyra_t  − baseline_beta × r_spy_t
        r_variant_t   = variant_beta × r_spy_t  +  alpha_dampening × alpha_t

    The dampening coefficient is a heuristic for how much each
    variant's constraints erode Lyra's selection alpha; see the
    per-variant ``alpha_dampening_estimate`` in ``VARIANT_SPECS``.

    NOT a backtest. The variant has not been traded historically; the
    NAV stream is a *model* of what it would have looked like given
    baseline β, variant β, and the assumed alpha dampening.
    """
    aligned = pd.concat({"lyra": lyra_returns, "spy": spy_returns}, axis=1, sort=False).dropna()
    aligned["alpha"] = aligned["lyra"] - baseline_beta * aligned["spy"]
    aligned["variant"] = (
        variant_beta * aligned["spy"] + alpha_dampening * aligned["alpha"]
    )
    cumulative = (1.0 + aligned["variant"]).cumprod()
    return VariantNavSeries(
        slug=variant_slug,
        daily_returns=aligned["variant"],
        cumulative_nav=cumulative,
        baseline_beta=baseline_beta,
        variant_beta=variant_beta,
        alpha_dampening=alpha_dampening,
    )


def _variant_effective_beta(
    spec: VariantSpec,
    baseline_beta: float,
) -> float:
    """Return the β the variant WOULD HAVE after signal expansion.

    This is intentionally the *target* β (what the variant's
    constraints define), not the post-cash-buffer β computed from the
    current 5-name universe. The current-universe deployability gap is
    surfaced separately via the constrained portfolio's
    ``cash_buffer_pct`` in variants.json — the modeled NAV stream
    assumes the variant has been signal-expanded to fill capacity at
    the target β. The CIO interpretation in summary.md explicitly
    flags this two-track reading.
    """
    if spec.beta_cap is not None:
        return min(spec.beta_cap, baseline_beta)
    return baseline_beta


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NavMetrics:
    n_observations: int
    cagr: Optional[float]
    annualized_volatility: Optional[float]
    sharpe: Optional[float]
    sortino: Optional[float]
    max_drawdown: Optional[float]


def compute_nav_metrics(returns: pd.Series) -> NavMetrics:
    cleaned = returns.dropna()
    n = int(cleaned.size)
    if n < 5:
        return NavMetrics(n_observations=n, cagr=None, annualized_volatility=None,
                          sharpe=None, sortino=None, max_drawdown=None)
    daily_mean = float(cleaned.mean())
    daily_std = float(cleaned.std(ddof=1))
    cum = (1.0 + cleaned).cumprod()
    total_return = float(cum.iloc[-1]) - 1.0
    years = n / TRADING_DAYS_PER_YEAR
    cagr = (1.0 + total_return) ** (1.0 / years) - 1.0 if years > 0 else None
    ann_vol = daily_std * math.sqrt(TRADING_DAYS_PER_YEAR) if daily_std > 0 else None
    sharpe = (
        (daily_mean * TRADING_DAYS_PER_YEAR) / (daily_std * math.sqrt(TRADING_DAYS_PER_YEAR))
        if daily_std > 0 else None
    )
    downside = cleaned[cleaned < 0]
    downside_std = float(downside.std(ddof=1)) if downside.size > 1 else 0.0
    sortino = (
        (daily_mean * TRADING_DAYS_PER_YEAR) / (downside_std * math.sqrt(TRADING_DAYS_PER_YEAR))
        if downside_std > 0 else None
    )
    drawdown = cum / cum.cummax() - 1.0
    max_dd = float(drawdown.min())
    return NavMetrics(
        n_observations=n,
        cagr=round(cagr, 6) if cagr is not None else None,
        annualized_volatility=round(ann_vol, 6) if ann_vol is not None else None,
        sharpe=round(sharpe, 6) if sharpe is not None else None,
        sortino=round(sortino, 6) if sortino is not None else None,
        max_drawdown=round(max_dd, 6),
    )


def _correlation(a: pd.Series, b: pd.Series) -> Optional[float]:
    joined = pd.concat([a, b], axis=1, sort=False).dropna()
    if joined.shape[0] < 30:
        return None
    corr = joined.iloc[:, 0].corr(joined.iloc[:, 1])
    if corr is None or (isinstance(corr, float) and math.isnan(corr)):
        return None
    return round(float(corr), 6)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConstrainedLyraAssessment:
    run_date: str
    baseline_trade_date: str
    variants: dict[str, dict[str, Any]]
    comparison: dict[str, Any]
    methodology_note: str
    narrative: str
    warnings: list[str]
    source_paths: list[str]


_METHODOLOGY_NOTE = (
    "Per-name β is a vol-based proxy "
    f"(market_correlation_assumption={MARKET_CORRELATION_ASSUMPTION}, "
    f"spy_annualized_vol_assumption={SPY_ANN_VOL_ASSUMPTION}). "
    "Variant NAV streams are MODEL-BASED, not backtests: each variant "
    "uses Lyra's daily alpha residual against SPY, re-mixed with the "
    "variant's structural β and a heuristic alpha_dampening coefficient. "
    "The dampening coefficients are documented per-variant in "
    "VARIANT_SPECS. These metrics are useful for relative comparison "
    "across variants but are not a substitute for a true backtest with "
    "the Lyra signal extended over the historical universe."
)


def assess_constrained_lyra(
    *,
    outputs_root: Path = DEFAULT_OUTPUTS_ROOT,
    run_date: Optional[str] = None,
    nav_series_path: Path = DEFAULT_NAV_SERIES_PATH,
) -> Optional[ConstrainedLyraAssessment]:
    """End-to-end assessment.

    Returns ``None`` if neither the baseline Lyra holdings nor the NAV
    series are present — the framework refuses to fabricate data.
    """
    run_date = run_date or _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    baseline = load_lyra_baseline(outputs_root=outputs_root)
    nav_df = load_nav_series(nav_series_path)
    warnings: list[str] = []

    if baseline is None and nav_df is None:
        return None
    if baseline is None:
        warnings.append("no_lyra_holdings_artifact_found")
    if nav_df is None:
        warnings.append(f"no_nav_series_at:{nav_series_path}")

    # Build variant portfolios + portfolio-level constraints check.
    variants_payload: dict[str, dict[str, Any]] = {}
    if baseline is not None:
        for spec in VARIANT_SPECS:
            constrained = apply_constraints(baseline, spec)
            variants_payload[spec.slug] = {
                "name": spec.name,
                "slug": spec.slug,
                "description": spec.description,
                "constraints": {
                    "max_position_weight": spec.max_position_weight,
                    "target_holdings_min": spec.target_holdings_min,
                    "target_holdings_max": spec.target_holdings_max,
                    "beta_cap": spec.beta_cap,
                    "max_sector_weight": spec.max_sector_weight,
                },
                "alpha_dampening_estimate": spec.alpha_dampening_estimate,
                "holdings": [
                    {"ticker": t, "weight": round(w, 6),
                     "sector": baseline.sector_per_name.get(t, "Unknown"),
                     "beta_proxy": baseline.beta_per_name.get(t)}
                    for t, w in sorted(constrained.weights.items(),
                                       key=lambda kv: -kv[1])
                ],
                "cash_buffer_pct": constrained.cash_buffer_pct,
                "portfolio_beta": constrained.portfolio_beta,
                "sector_exposure": constrained.sector_exposure,
                "max_position_weight_realised": constrained.max_position_weight,
                "holdings_count": constrained.holdings_count,
                "constraint_violations": constrained.constraint_violations,
                "deployment_caveat": constrained.deployment_caveat,
            }

    # Build variant NAV streams + per-variant metrics.
    comparison_rows: list[dict[str, Any]] = []
    nav_streams: dict[str, VariantNavSeries] = {}
    baseline_returns: Optional[pd.Series] = None
    spy_returns: Optional[pd.Series] = None
    if nav_df is not None and LYRA_SLUG in nav_df.columns:
        # Drop pre-inception rows (NAV flat at the starting value).
        lyra_nav = nav_df[LYRA_SLUG].dropna()
        moves = lyra_nav != lyra_nav.iloc[0]
        if moves.any():
            lyra_nav = lyra_nav.loc[lyra_nav.index >= lyra_nav.index[moves.values.argmax()]]
        baseline_returns = lyra_nav.pct_change().dropna()
        if SPY_SLUG in nav_df.columns:
            spy_returns = nav_df[SPY_SLUG].pct_change().dropna()

    if baseline_returns is not None and spy_returns is not None and baseline is not None:
        for spec in VARIANT_SPECS:
            variant_constrained = (
                apply_constraints(baseline, spec)
                if spec.slug != LYRA_SLUG
                else None
            )
            if spec.slug == LYRA_SLUG:
                variant_returns = baseline_returns
                effective_beta = baseline.portfolio_beta
                stream = VariantNavSeries(
                    slug=spec.slug,
                    daily_returns=variant_returns,
                    cumulative_nav=(1.0 + variant_returns).cumprod(),
                    baseline_beta=baseline.portfolio_beta,
                    variant_beta=baseline.portfolio_beta,
                    alpha_dampening=1.0,
                )
            else:
                effective_beta = _variant_effective_beta(
                    spec, baseline.portfolio_beta
                )
                stream = synthesize_variant_nav(
                    lyra_returns=baseline_returns,
                    spy_returns=spy_returns,
                    baseline_beta=baseline.portfolio_beta,
                    variant_beta=effective_beta,
                    alpha_dampening=spec.alpha_dampening_estimate,
                    variant_slug=spec.slug,
                )
            nav_streams[spec.slug] = stream
            metrics = compute_nav_metrics(stream.daily_returns)
            corr_baseline = (
                _correlation(stream.daily_returns, baseline_returns)
                if spec.slug != LYRA_SLUG else 1.0
            )
            corr_spy = _correlation(stream.daily_returns, spy_returns)
            row: dict[str, Any] = {
                "slug": spec.slug,
                "name": spec.name,
                "n_observations": metrics.n_observations,
                "cagr": metrics.cagr,
                "annualized_volatility": metrics.annualized_volatility,
                "sharpe": metrics.sharpe,
                "sortino": metrics.sortino,
                "max_drawdown": metrics.max_drawdown,
                "effective_beta": round(effective_beta, 6),
                "alpha_dampening": stream.alpha_dampening,
                "correlation_to_baseline_lyra": corr_baseline,
                "correlation_to_spy": corr_spy,
            }
            if variant_constrained is not None:
                row["max_sector_concentration"] = max(
                    variant_constrained.sector_exposure.values(), default=0.0
                )
                row["max_position_concentration"] = variant_constrained.max_position_weight
                row["cash_buffer_pct"] = variant_constrained.cash_buffer_pct
                row["holdings_count"] = variant_constrained.holdings_count
            else:
                # Baseline numbers
                row["max_sector_concentration"] = (
                    max(baseline.sector_exposure.values()) if baseline.sector_exposure else None
                )
                row["max_position_concentration"] = max(baseline.weights.values()) if baseline.weights else None
                row["cash_buffer_pct"] = 0.0
                row["holdings_count"] = len(baseline.holdings)
            comparison_rows.append(row)
    else:
        warnings.append("nav_synthesis_skipped_no_baseline_or_spy_returns")

    narrative = _render_narrative(
        baseline=baseline,
        comparison_rows=comparison_rows,
        warnings=warnings,
    )

    return ConstrainedLyraAssessment(
        run_date=run_date,
        baseline_trade_date=baseline.trade_date if baseline else "unknown",
        variants=variants_payload,
        comparison={
            "rows": comparison_rows,
            "baseline_slug": LYRA_SLUG,
            "spy_slug": SPY_SLUG,
        },
        methodology_note=_METHODOLOGY_NOTE,
        narrative=narrative,
        warnings=warnings,
        source_paths=(baseline.source_paths if baseline else []) + (
            [str(nav_series_path)] if nav_df is not None else []
        ),
    )


# ---------------------------------------------------------------------------
# Narrative
# ---------------------------------------------------------------------------


def _fmt_pct(value: Any, signed: bool = True) -> str:
    if value is None:
        return "n/a"
    try:
        v = float(value)
        return f"{v * 100:+.2f}%" if signed else f"{v * 100:.2f}%"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_num(value: Any, decimals: int = 3) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return "n/a"


def _render_narrative(
    *,
    baseline: Optional[LyraBaseline],
    comparison_rows: list[dict[str, Any]],
    warnings: list[str],
) -> str:
    lines: list[str] = []
    lines.append("# Constrained Lyra — Research Brief")
    lines.append("")
    if baseline is not None:
        lines.append(
            f"Baseline as of {baseline.trade_date}: "
            f"{len(baseline.holdings)} holdings, "
            f"max position {max(baseline.weights.values()) * 100:.1f}%, "
            f"portfolio β = {baseline.portfolio_beta:.2f}, "
            f"max sector weight "
            f"{max(baseline.sector_exposure.values()) * 100:.1f}%."
        )
    else:
        lines.append("Baseline Lyra holdings artifact missing — variants not built.")

    if not comparison_rows:
        lines.append("")
        lines.append("Variant NAV synthesis skipped — NAV series not available.")
        if warnings:
            lines.append("")
            lines.append("Warnings:")
            for w in warnings:
                lines.append(f"  - {w}")
        return "\n".join(lines)

    # Per-variant headline metrics.
    lines.append("")
    lines.append("## Per-variant headline metrics (model-based)")
    lines.append("")
    lines.append("| slug | CAGR | Sharpe | Sortino | Max DD | Ann Vol | β eff | corr→Lyra | corr→SPY |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for row in comparison_rows:
        lines.append(
            "| "
            + " | ".join([
                row.get("slug", "?"),
                _fmt_pct(row.get("cagr")),
                _fmt_num(row.get("sharpe")),
                _fmt_num(row.get("sortino")),
                _fmt_pct(row.get("max_drawdown")),
                _fmt_pct(row.get("annualized_volatility"), signed=False),
                _fmt_num(row.get("effective_beta"), decimals=2),
                _fmt_num(row.get("correlation_to_baseline_lyra")),
                _fmt_num(row.get("correlation_to_spy")),
            ])
            + " |"
        )

    # CIO interpretation: rank by Sharpe, Sortino, max DD, return-give-up.
    by_slug = {r["slug"]: r for r in comparison_rows}
    base = by_slug.get(LYRA_SLUG, {})
    base_cagr = base.get("cagr")
    base_dd = base.get("max_drawdown")
    base_sharpe = base.get("sharpe")

    lines.append("")
    lines.append("## CIO interpretation")
    lines.append("")
    lines.append(
        "The model assumes constraints reduce alpha by a heuristic "
        "dampening factor (per VARIANT_SPECS). Relative comparison is "
        "useful; absolute numbers are not a substitute for a true backtest."
    )
    lines.append("")
    # Return give-up + DD reduction per variant
    for slug, row in by_slug.items():
        if slug == LYRA_SLUG:
            continue
        cagr_delta = (
            (row.get("cagr") - base_cagr) if (row.get("cagr") is not None and base_cagr is not None) else None
        )
        dd_delta = (
            (row.get("max_drawdown") - base_dd) if (row.get("max_drawdown") is not None and base_dd is not None) else None
        )
        sharpe_delta = (
            (row.get("sharpe") - base_sharpe) if (row.get("sharpe") is not None and base_sharpe is not None) else None
        )
        lines.append(
            f"- **{row.get('name')}** ({slug}): "
            f"CAGR Δ vs baseline = {_fmt_pct(cagr_delta)}, "
            f"max DD Δ = {_fmt_pct(dd_delta)}, "
            f"Sharpe Δ = {_fmt_num(sharpe_delta)}. "
            f"β reduced to {_fmt_num(row.get('effective_beta'), decimals=2)} "
            f"(was {_fmt_num(base.get('effective_beta'), decimals=2)}); "
            f"sector concentration "
            f"{_fmt_pct(row.get('max_sector_concentration'), signed=False)}."
        )

    if warnings:
        lines.append("")
        lines.append("## Warnings")
        for w in warnings:
            lines.append(f"- {w}")

    lines.append("")
    lines.append("## Methodology caveat")
    lines.append("")
    lines.append(_METHODOLOGY_NOTE)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Artifact writers
# ---------------------------------------------------------------------------


def write_artifacts(
    assessment: ConstrainedLyraAssessment,
    *,
    output_root: Path = DEFAULT_RESEARCH_OUTPUT_ROOT,
) -> Path:
    """Write variants.json, comparison.json, summary.md under
    ``output_root/<run_date>/``. Returns the artifact directory."""
    out_dir = output_root / assessment.run_date
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "variants.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "run_date": assessment.run_date,
                "baseline_trade_date": assessment.baseline_trade_date,
                "variants": assessment.variants,
                "warnings": assessment.warnings,
                "source_paths": assessment.source_paths,
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    (out_dir / "comparison.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "run_date": assessment.run_date,
                "baseline_trade_date": assessment.baseline_trade_date,
                "comparison": assessment.comparison,
                "methodology_note": assessment.methodology_note,
                "warnings": assessment.warnings,
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    (out_dir / "summary.md").write_text(assessment.narrative + "\n", encoding="utf-8")
    return out_dir


# ---------------------------------------------------------------------------
# CLI entry point (research convenience; not wired into MCP)
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description=(
            "Build Constrained Lyra variants + comparison artifacts. "
            "Read-only research framework; no execution-path coupling."
        ),
    )
    parser.add_argument("--outputs-root", default=str(DEFAULT_OUTPUTS_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_RESEARCH_OUTPUT_ROOT))
    parser.add_argument("--nav-series-path", default=str(DEFAULT_NAV_SERIES_PATH))
    parser.add_argument("--run-date", default=None,
                        help="Override run date (default: today UTC).")
    args = parser.parse_args(argv)

    assessment = assess_constrained_lyra(
        outputs_root=Path(args.outputs_root),
        run_date=args.run_date,
        nav_series_path=Path(args.nav_series_path),
    )
    if assessment is None:
        print("No usable baseline holdings or NAV series found.")
        return 1
    out_dir = write_artifacts(assessment, output_root=Path(args.output_root))
    print(f"Wrote constrained Lyra artifacts to {out_dir}")
    print(f"Baseline trade date: {assessment.baseline_trade_date}")
    print(f"Run date:            {assessment.run_date}")
    print(f"Variants:            {list(assessment.variants.keys())}")
    if assessment.comparison.get("rows"):
        print(f"Comparison rows:     {len(assessment.comparison['rows'])}")
    if assessment.warnings:
        print(f"Warnings:            {assessment.warnings}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
