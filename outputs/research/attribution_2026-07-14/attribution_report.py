"""Repeatable return-attribution engine for the Caerus strategy book.

Answers "what actually drives our P&L?" by decomposing the daily return of the
RECORDED TARGET-WEIGHT BOOK (a shadow — NOT realized cash P&L; see the binding
interpretation constraints in ATTRIBUTION_REPORT.md section 0) into additive pieces
that PROVABLY sum to the total geometric return, across four cuts: NAME, SLEEVE,
REGIME, FACTOR.

    r_p(t) = sum_i w_i(BOD,t) * r_i(t)  +  w_cash(t) * 0

Design (all documented in ATTRIBUTION_REPORT.md / BUILD_NOTES.md):

  * Position-day panel. Recorded target weights (the strategy book) are treated as
    holdings that are rebalanced to target on each signal day and then DRIFT
    (buy-and-hold) between signal days. Beginning-of-day (BOD) weights earn that
    day's close-to-close return; end-of-day weights are the drifted result. A signal
    on day D is applied T+1 (effective from D+1) to avoid using D's own close in the
    weights that earn D's return. Cash = 1 - sum(name weights), earns 0.

  * Daily name contribution c_i(t) = w_i(BOD,t) * r_i(t). By construction
    sum_i c_i(t) = r_p(t) exactly.

  * Multi-period linking = Carino (1999) logarithmic smoothing so daily
    contributions sum EXACTLY to the geometric total return R = prod(1+r_p)-1:
        k   = ln(1+R)/R           (portfolio-level, k=1 if R==0)
        k_t = ln(1+r_p_t)/r_p_t   (per day, ->1 as r_p_t->0)
        C_i = sum_t (k_t/k) * c_i(t)   ==>   sum_i C_i = R  (exact)

  * SLEEVE = name contributions grouped by that day's sleeve label. Names with a
    comma-listed multi-sleeve tag split their daily contribution EQUALLY across the
    listed sleeves (a documented CONVENTION, not ground truth). A first-listed-sleeve
    alternative is also computed and reported as a sensitivity column (per the
    2026-07-14 adversarial review the quality/trend numbers move ~1.5pp between the
    rules; sign-stable).

  * REGIME = partition DAYS by regime state; regime return = Carino-scaled sum of
    daily r_p within the bucket. No overlap by construction; buckets sum to R.
    An event-sensitivity column (bucket contribution excluding its 2 worst days) is
    reported: the adversarial review showed the regime split is EVENT-DRIVEN, not a
    structural regime edge.

  * FACTOR = OLS time-series regression of daily r_p on the pre-registered factor
    set (market, momentum, size, value, quality, lowvol) with intercept. Per-day
    factor contribution = beta_f * f(t); intercept term = "unexplained" (NOT called
    alpha); residual reported honestly. Carino-linked, they sum to R exactly.
    HAC (Newey-West) t-stats. If N < MIN_FACTOR_OBS the refusal is ENFORCED IN THE
    DATA ARTIFACTS: the factor CSVs are written with refused=True and NO betas or
    contributions, and the reconciliation CSV marks the factor row REFUSED (not
    PASS). The refusal is not just markdown text.

Reconciliation table asserts name == sleeve == regime == total to <1e-9, and reports
the factor decomposition's honest residual + R^2 + unexplained share.

RESEARCH_ONLY. Reads only research-dir panels + read-only perf NAV for validation.
Re-runnable by cron without modification: no hardcoded dates in the logic. Input
panels are CLI flags (--signals-panel / --price-panel / --factor-prices) whose
DEFAULTS point at the FROZEN 2026-07-14 research snapshot; a cron consuming fresh
data must re-point these flags at live inputs (same schemas) — no code edits needed.

Usage:
  python attribution_report.py [--start YYYY-MM-DD] [--end YYYY-MM-DD]
                               [--lane paper|live] [--out DIR]
                               [--signals-panel CSV] [--price-panel PARQUET]
                               [--factor-prices PARQUET]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

# ------------------------------------------------------------------- default paths
# NOTE: these defaults are a FROZEN research snapshot (dated 2026-07-14 study dirs).
# Deterministic and re-runnable, but NOT self-updating: point the CLI flags at fresh
# panels for a live cron.
RESEARCH = Path("outputs/research")
DEFAULT_SIGNALS_PANEL = RESEARCH / "concentration_live_signal_2026-07-14" / "A_recorded_signals_panel.csv"
DEFAULT_NAME_PRICES = RESEARCH / "concentration_live_signal_2026-07-14" / "B_price_panel.parquet"
DEFAULT_FACTOR_PRICES = RESEARCH / "attribution_2026-07-14" / "factor_prices.parquet"
REGIME_TREND = RESEARCH / "concentration_thesis_2026-07-14" / "artifacts" / "regime_spy_trend.csv"
VALIDATION_NAV = Path("outputs/perf/live_overlay_nav_series.csv")  # read-only

# live pilot went live ~2026-06-26 (armed; near-zero real fills)
LIVE_LANE_START = "2026-06-26"
MIN_FACTOR_OBS = 60  # refuse factor regression below this N

FACTORS = ["market", "momentum", "size", "value", "quality", "lowvol"]

# Verbatim interpretation constraints from the 2026-07-14 adversarial review of the
# full-window paper run. They are binding on any reading of this engine's output for
# this book; the specific figures cited refer to that full-window run.
ADVERSARIAL_CAVEATS = """\
> This engine attributes the **recorded target-weight book (a shadow)**, not realized cash
> P&L; slippage, fills, rejects, and the options overlay are excluded. The one available
> reality check (25 days vs live-overlay NAV) shows daily tracking error of 58 bps — about
> 78% of the real book's own daily volatility — and the real book running ~1.8% behind the
> shadow over those 25 days, so the +11.98% headline is a target-book figure that likely
> overstates realized return.
>
> The factor R²=0.593 is a **breadth artifact, not a skill measurement**: a random 17-name
> equal-weight book from the same universe scores a higher median R² (0.738), and 96% of
> such random books exceed 0.593. R² therefore does NOT support "~95% beta." Alpha is
> **unmeasurable at N=109** (intercept CI ≈ ±12% annualized), so "near-zero alpha" is a
> no-power non-result, not a finding. Market β≈1.2 is mildly elevated vs a random basket
> and is the one factor claim with real content.
>
> Regime results are event-driven: ELEVATED −6.83% flips positive if the two worst tape
> days are removed. Sleeve splits for multi-tagged names are an equal-split convention;
> the quality/trend numbers shift ~1.5pp under an alternative rule (sign-stable)."""


# --------------------------------------------------------------------------- helpers
def carino_scale(rp: pd.Series) -> pd.Series:
    """Per-day Carino scaling coefficient k_t/k so scaled daily returns sum to R."""
    rp = rp.astype(float)
    R = float(np.prod(1.0 + rp.values) - 1.0)
    k = np.log1p(R) / R if abs(R) > 1e-12 else 1.0
    # k_t = ln(1+rp_t)/rp_t ; ->1 as rp_t->0
    kt = np.where(np.abs(rp.values) > 1e-12, np.log1p(rp.values) / rp.values, 1.0)
    return pd.Series(kt / k, index=rp.index)


def geometric_total(rp: pd.Series) -> float:
    return float(np.prod(1.0 + rp.astype(float).values) - 1.0)


# --------------------------------------------------------------- panel reconstruction
def load_panels(signals_panel: Path, name_prices: Path, factor_prices: Path):
    sig = pd.read_csv(signals_panel)
    sig["date"] = pd.to_datetime(sig["date"])
    sig["ticker"] = sig["ticker"].astype(str).str.upper()

    px = pd.read_parquet(name_prices)
    px["date"] = pd.to_datetime(px["date"])
    px["ticker"] = px["ticker"].astype(str).str.upper()
    close = px.pivot(index="date", columns="ticker", values="close").sort_index()
    rets = close.pct_change()

    fpx = pd.read_parquet(factor_prices)
    fpx["date"] = pd.to_datetime(fpx["date"])
    fclose = fpx.pivot(index="date", columns="ticker", values="close").sort_index()
    # Drop non-equity-trading days (e.g. Memorial Day) where ^VIX carries a phantom
    # settlement level but the ETFs are closed; else pct_change corrupts the next day.
    fclose = fclose[fclose["SPY"].notna()]
    fr = fclose.pct_change()
    factors = pd.DataFrame(index=fr.index)
    factors["market"] = fr["SPY"]
    factors["momentum"] = fr["MTUM"] - fr["SPY"]
    factors["size"] = fr["IWM"] - fr["SPY"]
    factors["value"] = fr["IVE"] - fr["IVW"]
    factors["quality"] = fr["QUAL"] - fr["SPY"]
    factors["lowvol"] = fr["USMV"] - fr["SPY"]

    return sig, close, rets, factors


def build_daily_weights(sig: pd.DataFrame, trading_days: pd.DatetimeIndex):
    """BOD weight + sleeve label per (day, name). T+1 application, buy-and-hold drift.

    Returns (weights_df, sleeve_map) where weights_df is indexed by date with columns
    per name holding BOD weight, plus a 'CASH' column; sleeve_map[date] = {name: sleeve}.
    """
    sig = sig.sort_values("date")
    signal_days = sorted(sig["date"].unique())
    # target-weight dict + sleeve dict per signal day
    tgt_by_day, sleeve_by_day = {}, {}
    for d, grp in sig.groupby("date"):
        w = grp.groupby("ticker")["target_weight"].sum()
        tgt_by_day[d] = w[w > 0].to_dict()
        sleeve_by_day[d] = grp.set_index("ticker")["sleeve"].to_dict()

    rows = {}
    sleeve_map = {}
    cur_w = None          # current EOD name-weight dict (drifting)
    cur_sleeve = {}
    last_effective = None
    for d in trading_days:
        # apply any signal whose effective date (signal_day + 1 td) has arrived:
        # find the latest signal day strictly before d (T+1 rule)
        prior = [s for s in signal_days if s < d]
        eff = prior[-1] if prior else None
        if eff is not None and eff != last_effective:
            cur_w = dict(tgt_by_day[eff])
            cur_sleeve = dict(sleeve_by_day[eff])
            last_effective = eff
        if cur_w is None:
            continue  # before first signal is effective
        rows[d] = dict(cur_w)
        sleeve_map[d] = dict(cur_sleeve)
    return rows, sleeve_map, signal_days


def reconstruct(sig, close, rets, start, end, lane):
    trading_days = rets.index
    trading_days = trading_days[(trading_days >= pd.Timestamp(start)) &
                                (trading_days <= pd.Timestamp(end))]
    if lane == "live":
        trading_days = trading_days[trading_days >= pd.Timestamp(LIVE_LANE_START)]

    bod_rows, sleeve_map, signal_days = build_daily_weights(sig, rets.index)

    # iterate chronologically, applying drift; only emit rows within window.
    # A new signal becoming effective (T+1) triggers a rebalance to target; otherwise
    # weights drift buy-and-hold. Contribution uses beginning-of-day (BOD) weight.
    all_days = [d for d in rets.index if d in bod_rows]
    contrib_records = []   # (date, ticker, w_bod, r, contrib, sleeve)
    rp_records = []        # (date, rp, cash_w)
    weight_records = []    # (date, ticker, w_bod)
    drift_w = None
    seed_eff = None
    for d in all_days:
        prior = [s for s in signal_days if s < d]
        eff = prior[-1] if prior else None
        if eff != seed_eff:            # a new signal became effective -> rebalance
            drift_w = dict(bod_rows[d])
            seed_eff = eff
        bod = drift_w
        cash_w = max(0.0, 1.0 - sum(bod.values()))
        # daily returns for names held
        rp = 0.0
        day_contrib = {}
        r_row = rets.loc[d]
        for tk, w in bod.items():
            r = r_row.get(tk, np.nan)
            if pd.isna(r):
                r = 0.0  # missing price -> treat as flat (documented)
            c = w * r
            day_contrib[tk] = (w, r, c)
            rp += c
        # cash contributes 0
        in_window = (d >= pd.Timestamp(start)) and (d <= pd.Timestamp(end))
        if lane == "live":
            in_window = in_window and (d >= pd.Timestamp(LIVE_LANE_START))
        if in_window:
            rp_records.append((d, rp, cash_w))
            sm = sleeve_map[d]
            for tk, (w, r, c) in day_contrib.items():
                contrib_records.append((d, tk, w, r, c, sm.get(tk, "unknown")))
                weight_records.append((d, tk, w))
        # drift weights to EOD for next day
        new_w = {}
        for tk, w in bod.items():
            r = r_row.get(tk, np.nan)
            r = 0.0 if pd.isna(r) else r
            new_w[tk] = w * (1.0 + r)
        # cash drifts at 0 growth; renormalise to total book value including cash
        total_val = sum(new_w.values()) + cash_w  # cash *(1+0)
        if total_val > 0:
            drift_w = {tk: v / total_val for tk, v in new_w.items()}

    contrib = pd.DataFrame(contrib_records,
                           columns=["date", "ticker", "w_bod", "r", "contrib", "sleeve"])
    rp = pd.DataFrame(rp_records, columns=["date", "rp", "cash_w"]).set_index("date")["rp"]
    weights = pd.DataFrame(weight_records, columns=["date", "ticker", "w_bod"])
    return contrib, rp, weights


# ------------------------------------------------------------------- decompositions
def name_attribution(contrib, rp):
    scale = carino_scale(rp)
    contrib = contrib.copy()
    contrib["scaled"] = contrib["contrib"] * contrib["date"].map(scale)
    out = (contrib.groupby("ticker")["scaled"].sum()
           .sort_values(ascending=False).rename("contribution").reset_index())
    return out


def sleeve_attribution(contrib, rp, rule: str = "equal"):
    """Sleeve cut under a stated multi-sleeve convention.

    rule='equal' -> comma-listed multi-sleeve names split contribution equally
    rule='first' -> full contribution to the first-listed sleeve (sensitivity check)
    """
    scale = carino_scale(rp)
    rows = []
    for _, row in contrib.iterrows():
        sleeves = [s.strip() for s in str(row["sleeve"]).split(",") if s.strip()]
        if not sleeves:
            sleeves = ["unknown"]
        sc = scale.loc[row["date"]]
        if rule == "first":
            rows.append((sleeves[0], row["contrib"] * sc))
        else:
            share = row["contrib"] / len(sleeves)
            for s in sleeves:
                rows.append((s, share * sc))
    df = pd.DataFrame(rows, columns=["sleeve", "scaled"])
    return (df.groupby("sleeve")["scaled"].sum()
            .sort_values(ascending=False).rename("contribution").reset_index())


def regime_attribution(rp, regime_by_day):
    """Regime cut + event sensitivity (bucket contribution excluding its 2 worst days).

    The ex-2-worst column is diagnostic only (it does not reconcile to anything); it
    exposes how much of a bucket's result rests on isolated tape events.
    """
    scale = carino_scale(rp)
    df = pd.DataFrame({"rp": rp, "scale": scale})
    df["regime"] = df.index.map(lambda d: regime_by_day.get(d, "UNKNOWN"))
    df["scaled"] = df["rp"] * df["scale"]
    rows = []
    for reg, g in df.groupby("regime"):
        contribution = float(g["scaled"].sum())
        ex2 = float(g["scaled"].sum() - g["scaled"].nsmallest(min(2, len(g))).sum())
        rows.append((reg, len(g), contribution, ex2))
    out = pd.DataFrame(rows, columns=["regime", "n_days", "contribution",
                                      "contribution_ex_2_worst_days"])
    return out.sort_values("contribution", ascending=False).reset_index(drop=True)


def factor_attribution(rp, factors):
    """Factor cut. Returns a dict. If n < MIN_FACTOR_OBS the regression is REFUSED:
    the returned coef/linked frames carry refused=True and NO betas/contributions, so
    downstream CSVs cannot present disavowed numbers as clean results."""
    fr = factors.reindex(rp.index).dropna()
    y = rp.reindex(fr.index)
    X = fr[FACTORS].copy()
    n = len(y)
    if n < MIN_FACTOR_OBS:
        reason = f"N={n} < MIN_FACTOR_OBS={MIN_FACTOR_OBS} (6 regressors)"
        coef = pd.DataFrame({"term": ["unexplained(intercept)"] + FACTORS})
        coef["beta"] = np.nan
        coef["hac_se"] = np.nan
        coef["hac_t"] = np.nan
        coef["refused"] = True
        coef["refusal_reason"] = reason
        linked = pd.DataFrame(
            {"term": ["unexplained(intercept)"] + FACTORS + ["residual"]})
        linked["contribution"] = np.nan
        linked["refused"] = True
        linked["refusal_reason"] = reason
        return {"refused": True, "refusal_reason": reason, "coef": coef,
                "linked": linked, "r2": np.nan, "n": n, "hac_lag": np.nan,
                "unexplained": np.nan, "r_factor_days": np.nan}
    Xd = np.column_stack([np.ones(n), X.values])
    beta, *_ = np.linalg.lstsq(Xd, y.values, rcond=None)
    resid = y.values - Xd @ beta
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((y.values - y.values.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan
    # HAC (Newey-West) standard errors, lag = floor(4*(n/100)^(2/9))
    L = int(np.floor(4 * (n / 100.0) ** (2.0 / 9.0)))
    XtX_inv = np.linalg.inv(Xd.T @ Xd)
    u = Xd * resid[:, None]
    S = u.T @ u
    for lag in range(1, L + 1):
        w = 1.0 - lag / (L + 1.0)
        G = u[lag:].T @ u[:-lag]
        S += w * (G + G.T)
    cov_hac = XtX_inv @ S @ XtX_inv
    se = np.sqrt(np.diag(cov_hac))
    tstat = beta / se
    names = ["unexplained(intercept)"] + FACTORS
    coef = pd.DataFrame({"term": names, "beta": beta, "hac_se": se, "hac_t": tstat})
    coef["refused"] = False
    coef["refusal_reason"] = ""

    # Carino-linked contribution of each factor + intercept + residual == R
    scale = carino_scale(y)
    contrib = {}
    contrib["unexplained(intercept)"] = float((beta[0] * scale).sum())
    for j, f in enumerate(FACTORS, start=1):
        daily = beta[j] * X[f].values
        contrib[f] = float((pd.Series(daily, index=y.index) * scale).sum())
    contrib["residual"] = float((pd.Series(resid, index=y.index) * scale).sum())
    linked = pd.DataFrame({"term": list(contrib), "contribution": list(contrib.values())})
    linked["refused"] = False
    linked["refusal_reason"] = ""
    unexplained = contrib["unexplained(intercept)"] + contrib["residual"]
    # geometric total over EXACTLY the regression days -> the factor decomposition's
    # reconciliation base (may differ from full-window R if any factor days were absent)
    return {"refused": False, "refusal_reason": "", "coef": coef, "linked": linked,
            "r2": r2, "n": n, "hac_lag": L, "unexplained": unexplained,
            "r_factor_days": geometric_total(y)}


# ---------------------------------------------------------------------- regime source
# Canonical VIX regime thresholds (sleeves/sleeve_trend/config.py): the strategy's own
# regime state that gates exposure. One consistent taxonomy across the whole window.
VIX_LOW, VIX_ELEVATED, VIX_HIGH = 20.0, 30.0, 40.0


def classify_vix(v: float) -> str:
    if pd.isna(v):
        return "UNKNOWN_VIX"
    if v < VIX_LOW:
        return "LOW"
    if v < VIX_ELEVATED:
        return "ELEVATED"
    if v < VIX_HIGH:
        return "HIGH"
    return "CRISIS"


def regime_map(sig, all_days, factor_prices: Path):
    """date -> canonical VIX regime label.

    Primary source = cached ^VIX level classified with the strategy's own thresholds
    (single consistent taxonomy). Falls back to the recorded regime tag on any day the
    VIX level is unavailable; then to SPY-200dma trend as a last resort.
    """
    rmap = {}
    # 1) canonical VIX classification from the cached level
    factor_prices = Path(factor_prices)
    if factor_prices.exists():
        fpx = pd.read_parquet(factor_prices)
        fpx["date"] = pd.to_datetime(fpx["date"])
        vix = fpx[fpx["ticker"] == "VIX"].set_index("date")["close"]
        for d in all_days:
            if pd.Timestamp(d) in vix.index:
                rmap[pd.Timestamp(d)] = classify_vix(float(vix.loc[pd.Timestamp(d)]))
    # 2) recorded regime tag for any still-missing day
    have = sig.dropna(subset=["regime"]).groupby("date")["regime"].first()
    for d, v in have.items():
        if pd.Timestamp(d) not in rmap:
            rmap[pd.Timestamp(d)] = str(v)
    # 3) SPY-200dma trend as last resort
    if REGIME_TREND.exists():
        tr = pd.read_csv(REGIME_TREND)
        tr["date"] = pd.to_datetime(tr["date"])
        trmap = tr.set_index("date")["spy_above_200dma"].to_dict()
        for d in all_days:
            if pd.Timestamp(d) not in rmap and pd.Timestamp(d) in trmap:
                rmap[pd.Timestamp(d)] = "TREND_UP" if trmap[pd.Timestamp(d)] else "TREND_DOWN"
    return rmap


# ------------------------------------------------------------------------ validation
def validate_against_nav(rp):
    if not VALIDATION_NAV.exists():
        return None
    nav = pd.read_csv(VALIDATION_NAV)
    nav["date"] = pd.to_datetime(nav["date"])
    nav = nav.set_index("date")["return_1d"].dropna()
    j = pd.concat([rp.rename("recon"), nav.rename("nav")], axis=1, join="inner").dropna()
    if len(j) < 3:
        return {"n_overlap": len(j)}
    diff = j["recon"] - j["nav"]
    rms_te_bps = float(np.sqrt((diff ** 2).mean()) * 1e4)
    nav_vol_bps = float(j["nav"].std() * 1e4)
    return {
        "n_overlap": int(len(j)),
        "start": str(j.index.min().date()),
        "end": str(j.index.max().date()),
        "mean_diff_bps": float(diff.mean() * 1e4),
        "rms_te_bps": rms_te_bps,
        "corr": float(j["recon"].corr(j["nav"])),
        "nav_vol_bps": nav_vol_bps,
        "recon_vol_bps": float(j["recon"].std() * 1e4),
        "te_over_nav_vol": rms_te_bps / nav_vol_bps if nav_vol_bps > 0 else np.nan,
        "recon_total": float(geometric_total(j["recon"])),
        "nav_total": float(geometric_total(j["nav"])),
    }


# ------------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--lane", default="paper", choices=["paper", "live"])
    ap.add_argument("--out", default="outputs/research/attribution_2026-07-14")
    ap.add_argument("--signals-panel", default=str(DEFAULT_SIGNALS_PANEL),
                    help="recorded signals panel CSV (default: FROZEN 2026-07-14 snapshot)")
    ap.add_argument("--price-panel", default=str(DEFAULT_NAME_PRICES),
                    help="long (date,ticker,close) name-price parquet (default: frozen snapshot)")
    ap.add_argument("--factor-prices", default=str(DEFAULT_FACTOR_PRICES),
                    help="factor-ETF + VIX price parquet (default: frozen snapshot)")
    args = ap.parse_args()

    sig, close, rets, factors = load_panels(Path(args.signals_panel),
                                            Path(args.price_panel),
                                            Path(args.factor_prices))
    start = args.start or str(rets.index.min().date())
    end = args.end or str(rets.index.max().date())
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    contrib, rp, weights = reconstruct(sig, close, rets, start, end, args.lane)
    if len(rp) == 0:
        raise SystemExit("no reconstructable days in window/lane")

    R = geometric_total(rp)
    rmap = regime_map(sig, rp.index, args.factor_prices)

    name_a = name_attribution(contrib, rp)
    sleeve_a = sleeve_attribution(contrib, rp, rule="equal")     # reconciling cut
    sleeve_alt = sleeve_attribution(contrib, rp, rule="first")   # sensitivity only
    sleeve_both = (sleeve_a.merge(
        sleeve_alt.rename(columns={"contribution": "contribution_first_listed_rule"}),
        on="sleeve", how="outer").fillna(0.0)
        .sort_values("contribution", ascending=False).reset_index(drop=True))
    regime_a = regime_attribution(rp, rmap)
    fac = factor_attribution(rp, factors)
    valid = validate_against_nav(rp)

    # ---- reconciliation ----
    # name/sleeve/regime reconcile to full-window R; factor reconciles to the geometric
    # total over its own regression days (r_factor_days), disclosed if it differs from
    # R. When the factor regression is REFUSED (N<MIN_FACTOR_OBS) the factor row is
    # stamped REFUSED with no sum — it must NOT read as a passing reconciliation.
    recon_rows = [
        ("name", float(name_a["contribution"].sum()), R),
        ("sleeve", float(sleeve_a["contribution"].sum()), R),
        ("regime", float(regime_a["contribution"].sum()), R),
    ]
    if fac["refused"]:
        recon_rows.append(("factor", np.nan, np.nan))
    else:
        recon_rows.append(("factor", float(fac["linked"]["contribution"].sum()),
                           fac["r_factor_days"]))
    recon = pd.DataFrame(recon_rows, columns=["decomposition", "sum", "total_return"])
    recon["residual"] = recon["sum"] - recon["total_return"]
    recon["residual_bps"] = recon["residual"] * 1e4
    recon["status"] = np.where(
        recon["residual_bps"].isna(), "REFUSED",
        np.where(recon["residual_bps"].abs() > 1.0, "INVESTIGATE", "PASS"))
    if fac["refused"]:
        recon.loc[recon["decomposition"] == "factor", "status"] = "REFUSED"

    # ---- write CSVs ----
    tag = f"{args.lane}_{start}_{end}"
    name_a.to_csv(out / f"name_attribution_{tag}.csv", index=False)
    sleeve_both.to_csv(out / f"sleeve_attribution_{tag}.csv", index=False)
    regime_a.to_csv(out / f"regime_attribution_{tag}.csv", index=False)
    fac["coef"].to_csv(out / f"factor_betas_{tag}.csv", index=False)
    fac["linked"].to_csv(out / f"factor_attribution_{tag}.csv", index=False)
    recon.to_csv(out / f"reconciliation_{tag}.csv", index=False)
    rp.rename("rp").to_frame().assign(cum=lambda d: (1 + d.rp).cumprod() - 1) \
        .to_csv(out / f"daily_portfolio_return_{tag}.csv")
    contrib.to_csv(out / f"contrib_panel_{tag}.csv", index=False)

    # ---- markdown report ----
    md = build_markdown(args.lane, start, end, rp, R, name_a, sleeve_both, regime_a,
                        fac, recon, valid)
    (out / "ATTRIBUTION_REPORT.md").write_text(md)
    print(md)
    print("\nWrote CSVs + ATTRIBUTION_REPORT.md to", out)


def _fmt_pct(x):
    if pd.isna(x):
        return "n/a"
    return f"{x*100:+.3f}%"


def build_markdown(lane, start, end, rp, R, name_a, sleeve_both, regime_a,
                   fac, recon, valid):
    L = []
    L.append(f"# Return Attribution Report — lane={lane} — TARGET-BOOK SHADOW")
    L.append(f"\nWindow: {start} -> {end}  |  {len(rp)} trading days  |  "
             f"generated {pd.Timestamp.today().date()}")
    L.append(f"\n**Total return of the TARGET-BOOK SHADOW (geometric, Carino-linked "
             f"base): {_fmt_pct(R)}**  (mean daily {_fmt_pct(rp.mean())}, "
             f"daily vol {rp.std()*100:.3f}%)")
    L.append("\n**This is NOT realized cash P&L.** It is the return of the recorded "
             "target-weight book — rebalanced to target on signal days, drifted "
             "buy-and-hold between them, T+1 application, cash at 0% — with slippage, "
             "partial fills, rejects, and the options overlay all EXCLUDED.")
    if valid and valid.get("n_overlap", 0) >= 3:
        L.append(f"Reality check over the only available overlap "
                 f"({valid['n_overlap']} days vs the live-overlay NAV): tracking error "
                 f"{valid['rms_te_bps']:.0f} bps/day = "
                 f"**{valid['te_over_nav_vol']:.2f}x the real book's own daily vol**, "
                 f"and the real book ran "
                 f"{_fmt_pct(valid['nav_total'] - valid['recon_total'])} behind the "
                 f"shadow over those days — the shadow headline above likely "
                 f"OVERSTATES realized return.")
    L.append("\nContributions are Carino(1999)-linked so every decomposition sums "
             "EXACTLY to the total shadow return.")

    # binding interpretation constraints
    L.append("\n## 0. Interpretation constraints (verbatim from the 2026-07-14 "
             "adversarial review; binding on any reading of the tables below)")
    L.append(ADVERSARIAL_CAVEATS)

    # validation
    L.append("\n## 1. Validation: reconstruction vs recorded NAV")
    if valid is None:
        L.append("No recorded NAV series available for validation.")
    elif valid.get("n_overlap", 0) < 3:
        L.append(f"Only {valid.get('n_overlap',0)} overlapping days with a recorded "
                 "daily-NAV series — too few to validate. See Gaps.")
    else:
        L.append(f"Overlap with `outputs/perf/live_overlay_nav_series.csv` "
                 f"({valid['start']} -> {valid['end']}, n={valid['n_overlap']}):")
        L.append(f"- correlation of daily returns: **{valid['corr']:.3f}** "
                 f"(corr^2 = {valid['corr']**2:.2f}: the shadow explains only "
                 f"~{valid['corr']**2*100:.0f}% of the real book's daily-return variance)")
        L.append(f"- mean daily diff: **{valid['mean_diff_bps']:+.2f} bps**")
        L.append(f"- RMS tracking error: **{valid['rms_te_bps']:.2f} bps/day** = "
                 f"**{valid['te_over_nav_vol']:.2f}x** the real book's own daily vol "
                 f"({valid['nav_vol_bps']:.1f} bps) — the error is nearly as large as "
                 f"the thing being tracked")
        L.append(f"- shadow daily vol {valid['recon_vol_bps']:.1f} bps vs real book "
                 f"{valid['nav_vol_bps']:.1f} bps: materially different risk profile "
                 f"(the real book is damped by the options overlay / exposure scaling "
                 f"the shadow ignores)")
        L.append(f"- cumulative over overlap: shadow {_fmt_pct(valid['recon_total'])} "
                 f"vs recorded NAV {_fmt_pct(valid['nav_total'])}")
        L.append("\nThis is a loose directional/shape check, NOT a magnitude "
                 "validation. Every headline number in this report is a target-book "
                 "shadow figure.")

    # names
    L.append("\n## 2. Per-NAME attribution (Carino-linked contribution to the shadow "
             "total)")
    top = name_a.head(10); bot = name_a.tail(10).iloc[::-1]
    L.append("\nTop 10 contributors:\n")
    L.append("| ticker | contribution |\n|---|---|")
    for _, r in top.iterrows():
        L.append(f"| {r['ticker']} | {_fmt_pct(r['contribution'])} |")
    L.append("\nBottom 10 contributors:\n")
    L.append("| ticker | contribution |\n|---|---|")
    for _, r in bot.iterrows():
        L.append(f"| {r['ticker']} | {_fmt_pct(r['contribution'])} |")

    # sleeves
    L.append("\n## 3. Per-SLEEVE attribution (convention-dependent for multi-tagged "
             "names)")
    L.append("The reconciling cut splits comma-tagged multi-sleeve names EQUALLY "
             "across their listed sleeves — an arbitrary convention. The first-listed-"
             "rule column shows the sensitivity: sleeve numbers move at the ~1.5pp "
             "level between rules (e.g. quality +13.33% -> +14.89%, trend -2.66% -> "
             "-4.38% on the full window) while signs are stable.\n")
    L.append("| sleeve | contribution (equal split, reconciling) | "
             "contribution (first-listed rule, sensitivity) |\n|---|---|---|")
    for _, r in sleeve_both.iterrows():
        L.append(f"| {r['sleeve']} | {_fmt_pct(r['contribution'])} | "
                 f"{_fmt_pct(r['contribution_first_listed_rule'])} |")

    # regime
    L.append("\n## 4. Per-REGIME attribution (days partitioned; no overlap) — "
             "EVENT-DRIVEN, not structural")
    L.append("Regime = canonical VIX thresholds (LOW<20, ELEVATED 20-30, HIGH 30-40, "
             "CRISIS>=40; the strategy's own config) classified from the cached ^VIX "
             "close. The ex-2-worst-days column (diagnostic, non-reconciling) shows "
             "how much each bucket rests on its two worst tape days: read bucket "
             "differences as event outcomes, NOT a robust regime edge — on the full "
             "window ELEVATED flips from -6.83% to positive without its two worst "
             "days.\n")
    L.append("| regime | n_days | contribution | ex 2 worst days (diagnostic) |\n"
             "|---|---|---|---|")
    for _, r in regime_a.iterrows():
        L.append(f"| {r['regime']} | {int(r['n_days'])} | "
                 f"{_fmt_pct(r['contribution'])} | "
                 f"{_fmt_pct(r['contribution_ex_2_worst_days'])} |")

    # factor
    L.append("\n## 5. Per-FACTOR attribution (time-series OLS, liquid-ETF proxies)")
    L.append("Proxies: market=SPY, momentum=MTUM-SPY, size=IWM-SPY, value=IVE-IVW, "
             "quality=QUAL-SPY, lowvol=USMV-SPY.")
    if fac["refused"]:
        L.append(f"\n**REFUSED: {fac['refusal_reason']}. No betas or factor "
                 "contributions are produced anywhere — the factor CSVs are stamped "
                 "refused=True with empty values and the reconciliation row is marked "
                 "REFUSED.**")
    else:
        L.append(f"\nN={fac['n']} days, R^2={fac['r2']:.3f}, HAC(Newey-West) "
                 f"lag={fac['hac_lag']}. Intercept is labeled *unexplained*, not alpha.")
        if fac["n"] != len(rp):
            L.append(f"\n(Factor regression covers {fac['n']} of {len(rp)} portfolio "
                     f"days; days lacking factor-ETF data are excluded, so the factor "
                     f"decomposition reconciles to the "
                     f"{_fmt_pct(fac['r_factor_days'])} total over its own {fac['n']} "
                     f"days, not the full-window {_fmt_pct(R)}.)")
        L.append("")
        L.append("| term | beta | HAC se | HAC t |\n|---|---|---|---|")
        for _, r in fac["coef"].iterrows():
            L.append(f"| {r['term']} | {r['beta']:+.4f} | {r['hac_se']:.4f} | "
                     f"{r['hac_t']:+.2f} |")
        L.append("\nCarino-linked factor contributions to the shadow total:\n")
        L.append("| term | contribution |\n|---|---|")
        for _, r in fac["linked"].iterrows():
            L.append(f"| {r['term']} | {_fmt_pct(r['contribution'])} |")
        share = fac["unexplained"] / R if abs(R) > 1e-12 else float("nan")
        L.append(f"\nUnexplained (intercept + residual) linked contribution: "
                 f"{_fmt_pct(fac['unexplained'])}  (= {share*100:.1f}% of the shadow "
                 f"total).")
        L.append("\n**Do NOT read R^2 as 'share of return explained by beta' or the "
                 "small unexplained share as 'near-zero alpha'.** The adversarial "
                 "placebo test showed random same-universe 17-name books score a "
                 "HIGHER median R^2 (0.738; 96% exceed this book's 0.593) — R^2 here "
                 "is breadth-mechanical, and this book carries MORE idiosyncratic "
                 "variance than a random basket, not less. The intercept has no "
                 "statistical power at this N (CI ~ +/-12% annualized on the full "
                 "window): alpha is UNMEASURABLE, not near zero. The one factor claim "
                 "with real content is the mildly elevated market beta (~1.2, placebo "
                 "p95); the momentum load (~0.33, HAC t>3) is plausible.")

    # reconciliation
    L.append("\n## 6. Reconciliation table (hard requirement)")
    L.append("Name/sleeve/regime residuals must be ~0 by construction; a non-refused "
             "factor row sums to its regression-day total because intercept+residual "
             "are included. REFUSED = regression not run (N<MIN_FACTOR_OBS); a "
             "refused row is not a pass.\n")
    L.append("| decomposition | sum | total | residual (bps) | status |\n"
             "|---|---|---|---|---|")
    for _, r in recon.iterrows():
        res = "n/a" if pd.isna(r["residual_bps"]) else f"{r['residual_bps']:+.4f}"
        L.append(f"| {r['decomposition']} | {_fmt_pct(r['sum'])} | "
                 f"{_fmt_pct(r['total_return'])} | {res} | {r['status']} |")
    passing = recon[recon["status"] != "REFUSED"]
    worst = passing["residual_bps"].abs().max()
    L.append(f"\nLargest non-refused reconciliation residual: {worst:.4f} bps "
             + ("(PASS, <1bp)." if worst < 1.0 else "(**INVESTIGATE, >1bp**)."))

    L.append("\n## 7. Gaps & caveats")
    L.append("See BUILD_NOTES.md for the full data-inventory and gaps section, and "
             "section 0 above for the binding interpretation constraints. Input-panel "
             "defaults are a frozen 2026-07-14 snapshot; re-point --signals-panel/"
             "--price-panel/--factor-prices for fresh data.")
    return "\n".join(L)


if __name__ == "__main__":
    main()
