MODE: RESEARCH
PROJECT_TYPE: Trading Analysis (read-only)
RISK_TIER: Low (study) / evaluates a HIGH blast-radius change
OBJECTIVE: Quantify whether Caerus loses opportunity by executing the daily paper plan at 9:35 AM ET instead of nearer the 9:30 AM open, using only data available at or before each simulated execution timestamp, with deterministic artifacts and no change to live behavior.

---

# Execution Timing Sensitivity Study — 9:35 ET vs. Near-Open

## 0. Why this is tractable (and where the trap is)

Caerus already separates *what to trade* from *when to fill it*:

- **Phase 1 (precompute, 7:00 AM ET)** freezes the plan — tickers, sides, share
  counts — in `outputs/precompute/<DATE>/planned_execution_payload.json` using a
  `PREV_CLOSE` price basis (`pricing_source: PREV_CLOSE`, `pricing_asof`).
- **Phase 2 (execute, 9:35 AM ET)** runs that exact plan
  (`PRECOMPUTE_EXECUTE_EXACT_PLAN=1`) as broker orders. The `entry_price` in the
  payload is a *reference* (prev close), not a limit.

Consequence: moving execution from 9:35 to ~9:30 changes **fill price only**, not
the order set. The study is therefore a *fill-quality / market-impact* question,
not a signal-regeneration question. This removes the largest source of
look-ahead risk — we are never re-deriving the plan with later information.

The remaining trap is in the **simulation**: a fill estimate for time *T* must be
built strictly from market data observable at or before *T*. Using the 9:30–9:40
bar's close to price a 9:31 fill, or using the day's VWAP to price an open fill,
would silently inject look-ahead. The methodology below enforces an as-of cutoff
on every price read.

## 1. Proposed methodology

### 1.1 Design
A paired, per-trade counterfactual replay. For each historical trading day with a
persisted plan, hold the plan constant and estimate the fill the same orders would
have received at a set of candidate execution offsets after the open:

```
offsets = {T+0 (09:30:00), T+1m, T+2m, T+5m (baseline), T+10m}
```

T+5m is the incumbent (cron fires at 9:35). T+0 / T+1m / T+2m are the "tighter
timing" candidates; T+10m is included to show the cost curve's slope in the other
direction. The 9:35 baseline is the *control*, so the headline metric is signed
relative to it.

### 1.2 Fill model (deterministic, no randomness)
For each trade `i` with signed share count `q_i` (BUY = +, SELL = −):

1. **Reference fill price** at offset `Δ`: the first trade print at-or-after the
   simulated timestamp `09:30:00 + Δ`, sourced from minute (or finer) bars. If
   only OHLCV minute bars are available, use the **open** of the bar whose start
   is the first ≥ the simulated timestamp. Never use that bar's high/low/close
   (those are not yet observable at the bar's start).
2. **Spread/half-spread cost**: add a modeled half-spread on the aggressive side
   (BUY pays +½ spread, SELL pays −½ spread). Spread estimated per symbol from
   the quote feed if available; otherwise from a bps assumption table
   (§6) bucketed by liquidity tier.
3. **Open-volatility / impact term**: a deterministic widening factor applied to
   the half-spread for the first N minutes after the open, decaying linearly to
   1.0 by T+10m, to reflect the well-documented open auction noise. The factor
   schedule is a fixed input, not fitted, so the run is reproducible.

The estimated fill is `fill_i(Δ) = ref_price_i(Δ) ± half_spread_i × open_vol_factor(Δ)`.

### 1.3 Headline metric — "opportunity delta vs. 9:35"
Per day `d`, per offset `Δ`:

```
cost(d, Δ)        = Σ_i  q_i × fill_i(Δ)           # signed cash outlay
opportunity(d,Δ)  = cost(d, 9:35) − cost(d, Δ)     # >0 means Δ is cheaper than 9:35
```

`opportunity` is reported in dollars and in basis points of gross traded notional
`Σ_i |q_i × ref_price_i|`. We aggregate across days into mean / median / p10 / p90
and a t-test (or, preferably, a sign/Wilcoxon test, since slippage is non-normal)
of whether the median opportunity differs from zero. **The decision is whether
near-open execution captures statistically and economically meaningful basis
points net of the modeled open-volatility penalty** — not whether some days look
better.

### 1.4 Determinism rules
- No `random` without a fixed seed; preferably no randomness at all.
- No `mtime`-based file discovery; iterate plans by date glob, sorted.
- Every price read records its `source`, `bar_start_ts`, and `asof_cutoff_ts` in
  the artifact so the read can be audited for look-ahead after the fact.
- Identical inputs ⇒ byte-identical artifact (modulo a single `generated_at`
  field), matching the existing `trading_turnover_cost_audit` discipline.

### 1.5 What the study explicitly does NOT do
Does not submit orders, touch Alpaca, change `cron_execute.sh`, the crontab,
`PREFERRED_TARGET_MINUTE` in `core/timing_policy.py`, reconciliation, or
order-submission code. It reads historical plans and historical market data and
writes a report. (See constraints, §6.)

## 2. Required data sources and gaps

### 2.1 Available in-repo (sufficient for the plan side)
- **Historical plans**: `outputs/precompute/<DATE>/planned_execution_payload.json`
  — gives tickers, sides, shares, prev-close reference, `planned_for`,
  `pricing_asof`. This is the canonical, deterministic order set per day.
- **Execution / reconciliation history**: `outputs/execution_history.csv`,
  `outputs/broker/recon_posttrade_<DATE>.json`,
  `outputs/broker_snapshot/broker_snapshot_<DATE>.json` — lets us identify which
  days actually traded and validate that the replayed order set matches what was
  filled (share-count reconciliation).
- **Daily price cache**: `data/cache`, hydrated via `core/price_hydration.py`
  (provider currently `yfinance`) — daily bars only.

### 2.2 The critical gap — intraday data
The study needs **minute (or finer) bars and ideally NBBO quotes for the first
~15 minutes after the open**, per symbol, for the historical dates studied. The
repo's price hydration is daily (`PREV_CLOSE`) and `yfinance`-based; there is no
persisted minute-bar store. Options, in order of preference:

1. **Alpaca Market Data API** (`/v2/stocks/bars` at `1Min`, and
   `/v2/stocks/quotes` for spread) — already a dependency (`brokers/`), keys
   present. Best fidelity; respects the as-of cutoff cleanly. Note IEX vs. SIP
   feed entitlement affects quote quality on the paper plan.
2. **yfinance 1-minute history** — free, but limited lookback (~30 days for 1m)
   and no true quote/spread; spread would fall back to the bps table. Adequate
   for a first pass on recent days only.
3. **Polygon / other vendor** — highest quality quotes, new dependency and cost.

**Gap summary to flag to the operator:**
- No persisted intraday bars today → must fetch and **snapshot to an immutable
  research cache** so re-runs are deterministic and don't re-hit the vendor.
- Quote/spread data may be entitlement-limited on paper → spread term may be
  modeled rather than measured for some symbols; this must be labeled per trade.
- **Sample size is the binding constraint.** Persisted plans are currently very
  sparse — `outputs/execution_history.csv` holds only a handful of real rows
  (single digits, plus test rows like `test_run_1` / `2099-01-01` that must be
  filtered out), and only one precompute bundle (`2026-03-24`) is on disk. The
  study is therefore **directional only** until plan history accrues. Intraday
  vendor data can be backfilled for past dates, but the **plan** cannot be
  reconstructed for days where no payload was persisted — plan availability, not
  market data, caps N. Report N prominently and refuse strong claims below a
  pre-registered minimum (e.g. N < 20 trading days ⇒ "insufficient power").

## 3. Files / scripts proposed (all additive, read-only)

> **Implementation status (2026-05-29):** the intraday research cache
> (`scripts/research/intraday_research_cache.py` +
> `Tests/test_intraday_research_cache.py`) is implemented. It is research-only
> and additive — it does not touch `cron_execute.sh`, the crontab,
> `brokers/`, `reconciliation.py`, or any execution-path artifact. The
> remaining items below (`timing_sensitivity_study.py`, fill model, study
> tests, reports) are not yet built.



```
specs/execution_timing_sensitivity_study.md        # this document
scripts/research/timing_sensitivity_study.py       # main analysis entrypoint
scripts/research/intraday_research_cache.py         # fetch + freeze minute/quote bars
core/research/timing_fill_model.py                 # deterministic fill model (pure fn)
reports/timing_sensitivity/<DATE>/summary.md       # human-readable report
outputs/research/timing_sensitivity/<DATE>/         # deterministic JSON artifacts (§4)
tests/test_timing_fill_model.py                     # fill-model unit tests
tests/test_timing_no_lookahead.py                  # as-of cutoff enforcement test
tests/test_timing_study_determinism.py             # identical-input ⇒ identical-output
data/research_cache/intraday/<symbol>/<DATE>.parquet  # immutable frozen bars
```

Naming and placement follow existing conventions: research tooling under
`scripts/research/`, deterministic artifacts under `outputs/`, narrative under
`reports/`, and a `specs/` design doc — mirroring `trading_turnover_cost_audit`
(spec + `trading_audit.py` + `reports/trading_audit/` + slippage tests). No file
in `scripts/cron_*.sh`, the crontab, `brokers/`, or reconciliation code is created
or modified.

## 4. Artifact schema proposal

Two artifacts per run date, both deterministic JSON.

### 4.1 `per_trade_timing.json`
```json
{
  "schema_version": "1.0",
  "study_run_id": "2026-05-29T141500Z_a1b2c3d",
  "generated_at": "2026-05-29T14:15:00Z",
  "trade_date": "2026-03-24",
  "plan_source": "outputs/precompute/2026-03-24/planned_execution_payload.json",
  "plan_pricing_asof": "2026-03-23",
  "baseline_offset": "T+5m",
  "intraday_source": "alpaca:1Min",
  "feed": "iex",
  "trades": [
    {
      "ticker": "AAPL",
      "side": "BUY",
      "shares": 2,
      "prev_close_ref": 251.64,
      "fills_by_offset": {
        "T+0":  {"ref_price": 252.10, "bar_start_ts": "2026-03-24T09:30:00-04:00",
                 "asof_cutoff_ts": "2026-03-24T09:30:00-04:00", "half_spread": 0.04,
                 "open_vol_factor": 1.8, "modeled_fill": 252.172, "spread_basis": "quote"},
        "T+1m": {"...": "..."},
        "T+2m": {"...": "..."},
        "T+5m": {"...": "..."},
        "T+10m":{"...": "..."}
      }
    }
  ]
}
```

### 4.2 `timing_summary.json`
```json
{
  "schema_version": "1.0",
  "study_run_id": "2026-05-29T141500Z_a1b2c3d",
  "date_range": {"start": "2026-03-24", "end": "2026-05-28", "n_days": 41},  // illustrative shape only; real N is currently single digits (see §2.2)
  "baseline_offset": "T+5m",
  "gross_notional_total": 412350.18,
  "by_offset": {
    "T+0": {
      "opportunity_usd": {"mean": 18.40, "median": 7.10, "p10": -22.0, "p90": 71.3, "sum": 754.4},
      "opportunity_bps": {"mean": 1.9, "median": 0.8, "p10": -2.4, "p90": 7.6},
      "open_vol_penalty_bps_mean": 2.7,
      "net_opportunity_bps_mean": -0.8,
      "days_positive": 22, "days_negative": 19,
      "wilcoxon_p": 0.34, "significant_at_5pct": false
    },
    "T+1m": {"...": "..."},
    "T+2m": {"...": "..."},
    "T+10m": {"...": "..."}
  },
  "data_quality": {
    "days_with_quote_data": 17, "days_with_bar_only": 24,
    "symbols_modeled_spread": 38, "lookback_truncated": false
  },
  "verdict": {
    "headline_bps_vs_935": 0.8,
    "net_of_open_vol_bps": -0.8,
    "economically_material": false,
    "recommend_fr": false
  }
}
```

Both artifacts carry `schema_version`, a content-hash-derived `study_run_id`, and
per-read provenance (`bar_start_ts`, `asof_cutoff_ts`, `spread_basis`) so any
look-ahead can be detected by inspection.

## 5. Validation approach

1. **No-look-ahead test** (`test_timing_no_lookahead.py`): assert that for every
   recorded fill, `bar_start_ts >= simulated_execution_ts` and
   `asof_cutoff_ts <= bar_start_ts`; assert no field used for a given offset
   derives from a bar starting after that offset. Fail the run if violated.
2. **Determinism test** (`test_timing_study_determinism.py`): run the study twice
   over the frozen research cache; assert artifacts are byte-identical except
   `generated_at`.
3. **Fill-model unit tests** (`test_timing_fill_model.py`): BUY pays +half-spread,
   SELL pays −half-spread; open-vol factor decays to 1.0 by T+10m; signed-cash and
   bps math match hand-worked fixtures.
4. **Plan-reconciliation cross-check**: for days present in
   `outputs/broker/recon_posttrade_<DATE>.json`, confirm the replayed order set's
   share counts equal the reconciled positions delta — guards against replaying a
   plan that didn't actually execute.
5. **Reasonableness band**: estimated 9:35 fills should land within a sane band of
   the day's actual reconciled fills where available; large divergence flags a
   data or model bug, not an opportunity.
6. **Subagent audit (high-stakes)**: spawn an independent reviewer to read the
   methodology and a sample artifact specifically hunting for look-ahead and for
   the baseline being mislabeled — because the entire conclusion hinges on the
   9:35 control being honest.
7. **Sensitivity to assumptions**: re-run with the spread bps table at low/mid/high
   to confirm the verdict's sign is robust to the spread assumption, since spread
   is partly modeled.

## 6. Risks / assumptions

**Assumptions**
- The plan is fixed at 7:00 AM and unchanged by execution timing (verified:
  `PRECOMPUTE_EXECUTE_EXACT_PLAN=1`, payload `plan_only`/`PREV_CLOSE`). If a future
  change re-prices or re-sizes at execution time, this study's core premise breaks
  and it must be redesigned.
- Orders behave as marketable/market orders at the open; no resting limits.
- Open-volatility penalty schedule is a *modeling choice*, not measured per day;
  results are reported both gross and net of it so the operator sees the lever.

**Risks**
- **Look-ahead via data plumbing** — highest risk; mitigated by as-of cutoffs,
  provenance fields, and the dedicated test + subagent audit.
- **Survivorship / sample size** — few dozen days; the study reports N, CIs, and a
  non-parametric test rather than over-claiming.
- **Quote-feed entitlement (IEX vs SIP)** on paper may understate true spreads at
  the open, biasing toward "tighter timing looks cheap." Labeled per trade; spread
  sensitivity sweep checks robustness.
- **Vendor lookback limits** (yfinance 1m ≈ 30 days) may force two data tiers;
  freeze whatever is fetched into the immutable research cache so conclusions are
  reproducible even after the vendor window rolls off.
- **Operational misread** — the study must not be mistaken for permission to retime
  execution. The original 9:35 offset was a *scheduling-reliability guardrail*;
  even if near-open fills are cheaper, tighter timing reduces the buffer for the
  7:00 AM bundle validation + self-heal path that `cron_execute.sh` depends on.
  That reliability cost is out of scope for the fill-price study and must be
  weighed separately.

## 7. Recommendation — should this become a governed FR item?

**The study itself: no FR needed.** It is read-only, additive, writes only to
`outputs/research/`, `reports/`, and a research data cache, touches no scheduler,
broker, or reconciliation code. Under the repo's blast-radius framework
(`docs/governance/fr_governance_model.md`) it is **LOW** (read-only tooling /
diagnostics). It can proceed as ordinary research work and produce its artifacts
without the FR lifecycle.

**Acting on the result: yes, gated behind a HIGH-blast-radius FR.** The governance
model explicitly says to "escalate blast radius when a change can affect execution
timing." Any change to `cron_execute.sh`, `crontab.txt`, or
`PREFERRED_TARGET_MINUTE`/`PREFERRED_TARGET_HOUR` in `core/timing_policy.py` is
**HIGH** and must go through `BACKLOG → READY → READY_VALIDATED → IN_PROGRESS →
PROMOTION_READY → DEPLOYED_OBSERVING → DEPLOYED`, with a documented rollback path
(revert the cron minute) and an observation window (e.g., N clean sessions, no
self-heal/bundle-validation failures attributable to the tighter window).

**Concrete recommendation:** run the study as LOW research now. Only file the
timing-change FR if the study shows the near-open opportunity is **both
statistically significant and economically material net of the modeled open-vol
penalty AND net of the scheduling-reliability margin** the 9:35 guardrail buys. If
the net opportunity is small (the plausible prior, given a small-cap-free, modest-
turnover paper book filling a few hundred dollars per name), the right outcome is a
`REVIEWED_DEFERRED` registry entry documenting that 9:35 was retained on purpose —
which is itself a valuable governance artifact.
