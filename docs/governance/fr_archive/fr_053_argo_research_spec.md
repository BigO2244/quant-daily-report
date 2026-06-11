# FR-053 Argo Research Specification

Status: ACTIVE_RESEARCH — Phase B Validation
Owner: Caerus Research Program
Last Updated: 2026-06-08
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL
Implementation Status: ACTIVE — the regime / model-selection layer is implemented as research_registry/research/argo.py (schema caerus_argo_regime_selection_v1, artifact argo_regime_selection.*). Argo is the regime overlay / model-selection layer; it does not select securities.

## Purpose

Argo is the proposed Caerus regime allocation overlay. Its first objective is
to classify market regimes deterministically and produce research-only
allocation recommendations across Caerus strategy families.

Argo is not initially expected to select securities. It must not route capital,
alter strategy weights in production, change paper trading behavior, submit
orders, or modify Polaris, Orion, Phoenix, Cygnus, Cassiopeia, or any promoted
strategy. The first pass is reporting and research only.

## Research Hypothesis

Different strategy families should perform best in different market regimes.
Momentum/core strategies, crisis-reversal strategies, earnings drift, and
event-driven strategies may have distinct expected return, drawdown, turnover,
and correlation profiles depending on trend, volatility, breadth, liquidity,
credit, and market concentration.

Primary test:

- A deterministic, point-in-time regime overlay can classify market conditions
  and produce non-executing allocation recommendations that improve research
  understanding of when each strategy family is likely to be favored, without
  introducing look-ahead bias or changing deployed capital.

## Regime Taxonomy

Argo should start with a transparent taxonomy that separates market state from
capital-routing decisions.

Baseline dimensions:

| Dimension | States | Purpose |
|---|---|---|
| Trend | `strong_up`, `weak_up`, `neutral`, `downtrend`, `crisis_down` | Direction and persistence of broad market trend |
| Volatility | `calm`, `normal`, `elevated`, `crisis` | Risk environment and reversal/momentum suitability |
| Breadth | `broad_participation`, `mixed`, `narrow`, `washed_out` | Market participation and concentration risk |
| Credit/Liquidity | `supportive`, `neutral`, `tightening`, `stress` | Macro/liquidity backdrop |
| Concentration | `diffuse`, `moderate`, `high`, `extreme` | Leadership breadth and single-theme crowding |
| Risk Appetite | `risk_on`, `balanced`, `risk_off`, `deleveraging` | Cross-asset confirmation layer |

Composite regime labels should be derived from the dimensions, not manually
assigned. Example labels:

- `risk_on_broad`
- `risk_on_narrow`
- `late_cycle_momentum`
- `volatile_transition`
- `risk_off_defensive`
- `crisis_liquidation`
- `washed_out_recovery`

The taxonomy should be versioned. Any state-boundary change creates a new
schema/config version.

## Input Indicators

First-pass indicators should favor data already used or easily reproducible in
the Caerus research environment.

Required indicators:

- SPY adjusted close, 50D moving average, 200D moving average.
- SPY 20D, 60D, and 120D realized volatility.
- SPY drawdown from 20D, 60D, and 252D highs.
- VIX close or deterministic realized-volatility proxy when VIX is unavailable.
- Universe breadth: percent of `data/universe.csv` members above 50D and 200D
  moving averages.
- Equal-weight universe return versus SPY return.
- HYG and TLT adjusted closes, when available, for credit/rates proxy.
- Market concentration proxy:
  - top 5 universe market-cap weight if market caps are available; or
  - fallback price-return concentration from top contributors.
- Existing strategy shadow returns when available for research attribution.

Optional later indicators:

- Advance/decline line.
- New highs/new lows.
- Credit spreads from FRED.
- Dollar liquidity or Fed balance sheet series.
- Sector dispersion.
- Options-implied skew and term structure.
- Overnight agent liquidity/gamma states, once their availability and artifact
  timing are documented.

## Availability-Date Assumptions

Argo classifications must be point-in-time and deterministic.

Baseline rules:

- Daily OHLCV and VIX close for date `t` are available only after the market
  close on `t`; decisions based on them apply to research weights as of `t`.
- Macro and FRED-style data use release date or artifact ingestion date, not
  observation period date, unless the source explicitly proves same-day
  availability.
- Shadow strategy returns for `t` are not available for Argo classification at
  `t`; they are used only for subsequent evaluation and reporting.
- Overnight agent artifacts may be used only if their generated-at timestamp is
  before the Argo decision timestamp and the input is documented as non-future.
- Missing optional indicators produce `PARTIAL` diagnostics; they must not be
  fabricated.
- Missing required broad-market price data produces an empty-but-valid regime
  artifact with `status: NO_DATA`.

No future returns, future realized volatility, future regime labels, later
revised macro data, or forward shadow performance may influence a historical
classification.

## Classification Methodology

The first classifier should be rule-based and hysteresis-aware, not fitted.

Dimension examples:

- Trend:
  - `strong_up`: SPY close > 200D MA and 50D MA > 200D MA with positive 60D
    return.
  - `weak_up`: SPY close > 200D MA but trend confirmation is mixed.
  - `neutral`: SPY close near 200D MA with mixed 60D/120D trend.
  - `downtrend`: SPY close < 200D MA or 50D MA < 200D MA.
  - `crisis_down`: downtrend plus severe drawdown or volatility crisis.
- Volatility:
  - percentile or threshold bands using VIX and/or realized volatility.
- Breadth:
  - percent of universe above 200D MA and percent above 50D MA.
- Credit/Liquidity:
  - HYG trend, HYG/TLT relative strength, or FRED credit/liquidity series when
    availability is reliable.
- Concentration:
  - top-contributor share of universe returns or market-cap concentration.
- Risk appetite:
  - composite of SPY trend, HYG strength, TLT behavior, VIX, and breadth.

Hysteresis:

- Minimum dwell of 5 trading days for non-crisis state changes.
- Two-close confirmation for dimension changes.
- Crisis bypass allowed only for predeclared thresholds, such as volatility
  crisis plus SPY drawdown.
- Every state transition must record previous state, proposed state, accepted
  state, confirmation count, dwell age, and transition reason.

Outputs:

- Dimension states.
- Composite regime label.
- Confidence score.
- Data-quality status.
- Transition diagnostics.
- Indicator snapshot.

## Allocation Recommendation Methodology

Argo recommendations are research-only. They must not route capital.

Initial strategy universe:

- `caerus_polaris`
- `caerus_orion`
- `caerus_phoenix`
- `caerus_cygnus`
- `caerus_cassiopeia`
- `spy_benchmark`
- cash / SGOV reference bucket for reporting only

Recommendation approach:

1. Define a static prior suitability matrix by composite regime and strategy
   family.
2. Adjust research recommendations using trailing, already-realized strategy
   evidence:
   - trailing return
   - drawdown
   - volatility
   - correlation to other strategies
   - artifact freshness
   - promotion-readiness status
3. Apply deterministic caps:
   - max recommended strategy weight
   - max same-alpha-family exposure
   - minimum cash/benchmark reference in high-risk regimes
   - zero recommended weight for strategies with stale or broken artifacts
4. Emit recommended weights as reporting fields only:
   - `research_recommended_weight`
   - `paper_weight_change_allowed: false`
   - `execution_impact: NON_EXECUTIONAL`

Example suitability priors:

| Regime | Polaris/Orion | Phoenix | Cygnus | Cassiopeia | Cash/Benchmark Reference |
|---|---:|---:|---:|---:|---:|
| `risk_on_broad` | high | low | medium | medium | low |
| `risk_on_narrow` | medium | low | medium | medium | medium |
| `volatile_transition` | medium | medium | low-medium | medium | medium |
| `risk_off_defensive` | low | medium | low | low-medium | high |
| `crisis_liquidation` | very low | watch/high after washout | low | low | high |
| `washed_out_recovery` | medium | high | low-medium | medium | medium |

The matrix should be versioned and reviewed before any promotion beyond
research reporting.

## Expected Artifacts

Research baseline outputs:

- `outputs/research/argo/<date>/argo_indicator_snapshot.parquet`
- `outputs/research/argo/<date>/argo_regime.json`
- `outputs/research/argo/<date>/argo_transition_trace.json`
- `outputs/research/argo/<date>/argo_recommendations.json`
- `outputs/research/argo/<date>/argo_strategy_context.json`
- `outputs/research/argo/<date>/argo_decision_trace.json`
- `outputs/research/argo/performance/argo_regime_history.csv`
- `outputs/research/argo/performance/argo_recommendation_history.csv`
- `outputs/research/argo/performance/argo_summary.json`

Every artifact should include:

- `schema_version`
- `trade_date`
- `overlay_id: caerus_argo`
- `governance_label: RESEARCH_ONLY`
- `execution_impact: NON_EXECUTIONAL`
- `paper_weight_change_allowed: false`
- data coverage and freshness metadata
- input availability assumptions
- regime taxonomy version
- suitability matrix version
- explicit unavailable/partial status reasons

Empty-but-valid outputs:

- Missing required data should still emit `argo_regime.json` and
  `argo_recommendations.json` with `status: NO_DATA`, no recommendations, and
  explicit missing-input diagnostics.
- Missing strategy artifacts should not block regime classification; they should
  mark affected recommendation rows as `UNAVAILABLE`.

## Integration Points

Existing systems to preserve:

- Polaris remains paper/control strategy.
- Orion and Lyra remain existing shadow candidates until governance changes.
- Phoenix, Cygnus, and Cassiopeia remain research/shadow candidates according
  to their own statuses.
- SPY remains benchmark.
- Paper execution and live execution remain unchanged.

Research-only integration:

- New module under `research/argo/` for indicator construction, regime
  classification, suitability mapping, recommendation generation, and artifacts.
- Optional CLI wrapper `scripts/research/run_argo_research.py`.
- No changes to `scripts/cron_execute.sh`, `scripts/cron_precompute.sh`,
  broker/order code, or paper/live execution code.

Promotion-readiness integration:

- Add Argo regime labels to research review packets as explanatory context.
- Slice strategy promotion-readiness metrics by Argo regime after enough
  history exists.
- Report whether strategy performance is regime-concentrated or diversified.
- Mark Argo recommendations as non-executing until a separate governance task
  explicitly promotes allocation authority.

Shadow integration after research validation:

- Publish Argo reporting artifacts alongside shadow packets only after readers
  tolerate overlay metadata that is not a security-selection strategy.
- Keep Argo out of holdings comparison and broker reconciliation.
- Add dashboard/review panels for:
  - current regime
  - regime transition trace
  - strategy suitability matrix
  - non-executing recommended weights

## Backtest Methodology

Stage 1: classifier validation

- Rebuild indicator history point-in-time from available daily data.
- Generate daily regime labels from 2014 onward or earliest reliable data date.
- Audit transition traces for major historical episodes.
- Validate hysteresis and crisis-bypass behavior.
- Confirm no future returns or future realized-vol windows are used.

Stage 2: strategy-regime attribution

- Join realized strategy daily returns to prior-day Argo labels.
- Measure each strategy's return, volatility, drawdown, hit rate, and turnover
  by regime.
- Compare Polaris, Orion, Phoenix, Cygnus, Cassiopeia, SPY, and cash reference
  where histories exist.
- Do not treat missing strategy histories as zero returns.

Stage 3: recommendation simulation

- Simulate Argo research recommendations using only prior realized strategy
  evidence and same-day available regime labels.
- Compare recommended allocation baskets to static equal-weight strategy
  baskets and SPY.
- Report turnover, concentration, drawdown, realized volatility, and correlation
  to individual strategies.
- Keep results labeled `RESEARCH_ONLY_SIMULATION`.

Stage 4: robustness

- Sensitivity-test trend, volatility, breadth, and concentration thresholds.
- Test hysteresis dwell periods of 3, 5, and 10 days.
- Test suitability matrix variants without fitting to the full sample.
- Evaluate whether Argo adds value out-of-sample or merely overfits historical
  regime narratives.

## Shadow Reporting Plan

Stage 1: standalone research reporting

- Generate Argo artifacts under `outputs/research/argo/`.
- Include current regime, transition trace, and non-executing recommendations.
- Publish empty-but-valid outputs when data is missing.

Stage 2: research review packet context

- Add Argo regime fields to research review packets.
- Show strategy performance by current and historical regimes.
- Keep all recommendation fields explicitly non-executing.

Stage 3: shadow dashboard context

- Display Argo's current regime and research-only strategy suitability.
- Use warnings when required data is stale or recommendations are disabled.
- Do not expose Argo recommendations as executable instructions.

Stage 4: promotion-readiness evidence

- Require at least 60 clean forward reporting days before any promotion
  discussion.
- Require manual review of regime transitions and recommendation changes.
- Require evidence that recommendations improve research outcomes without
  excessive turnover or concentration.

## Proposed Implementation File List

Research-only first pass:

- `fr_053_argo_research_spec.md`
- `research/argo/__init__.py`
- `research/argo/indicators.py`
- `research/argo/classifier.py`
- `research/argo/hysteresis.py`
- `research/argo/recommendations.py`
- `research/argo/backtest.py`
- `research/argo/artifacts.py`
- `scripts/research/run_argo_research.py`
- `config/research/argo_regime_taxonomy.json`
- `config/research/argo_suitability_matrix.json`
- `Tests/test_argo_indicators.py`
- `Tests/test_argo_classifier.py`
- `Tests/test_argo_hysteresis.py`
- `Tests/test_argo_recommendations.py`
- `Tests/test_argo_artifacts.py`

Later reporting-framework pass:

- `scripts/research/build_research_clarity_wave.py`
- `scripts/research/build_daily_research_packet.py`
- `research_registry/research/promotion_readiness.py`
- `research_registry/research/shadow_comparison.py`
- `web/dashboard/` reporting surfaces, if dashboard context is approved.
- `Tests/test_research_registry_promotion_readiness.py`
- `Tests/test_research_registry_shadow_comparison.py`

## Risks, Assumptions, And Open Questions

Risks:

- Regime labels can become narrative overfit if thresholds are tuned to known
  crises.
- Allocation recommendations can be mistaken for routing instructions unless
  artifacts and UI labels are explicit.
- Strategy histories for Phoenix, Cygnus, and Cassiopeia may initially be too
  short for reliable recommendation adjustments.
- Missing or revised macro data can introduce look-ahead bias.
- Hysteresis can lag regime changes; crisis bypass can over-trigger if
  thresholds are loose.
- Existing shadow tracking assumes security-selection strategies, while Argo is
  an overlay.

Assumptions:

- Existing adjusted OHLCV cache is sufficient for first-pass trend, volatility,
  and breadth indicators.
- VIX or a realized-volatility fallback is available for volatility state.
- HYG and TLT are available or can be treated as optional credit/rates proxies.
- Argo recommendations remain non-executing until a separate promotion task
  explicitly changes that status.

Open questions:

- Should Argo reuse the existing alpha-stack regime classifier directly, or
  build a research overlay that can diverge without affecting production?
- Which strategy set should be included before Phoenix, Cygnus, and Cassiopeia
  have forward histories?
- Should recommendation weights be anchored to equal strategy weights, SPY, or
  current Polaris-only paper posture for research comparison?
- What minimum history is required before trailing strategy evidence may adjust
  suitability priors?
- Should overnight liquidity and gamma agents be included in v0, or deferred
  until their availability timing is documented in Argo artifacts?
- How should Argo handle conflicting states, such as strong price trend with
  weak breadth and high concentration?

## Phase B Regime Selection Validation

Phase B validates Argo as a research-only regime overlay / model-selection
layer. It does not route capital and does not change any paper/live strategy
weights. The validation must distinguish the current leaderboard winner from a
decision-grade recommendation and must make stale or unavailable evidence
visible.

Required Phase B artifact fields:

- `trade_date`, `schema_version`, `overlay_id: caerus_argo`,
  `governance_label: RESEARCH_ONLY`, and `execution_impact: NON_EXECUTIONAL`.
- `current_regime`, `current_recommendation`, `recommendation_confidence`,
  `decision_grade_recommendation`, `stability_summary`, `transition_summary`,
  `input_freshness`, `no_lookahead_checks`, `evidence_blockers`, and
  `reason_codes`.

Decision policy: a leaderboard winner is not a capital-routing recommendation.
Argo remains research-only unless separate governance explicitly approves a
promotion or allocation change.

Current Phase B evidence state (2026-06-08): `argo_regime_selection.*` is a
required daily research artifact and must emit even when evidence is incomplete.
When source evidence is stale, partial, or missing, the artifact must set
`recommendation: null`, `decision_grade: false`, and explicit `reason_codes`
instead of failing silently. The 2026-06-08 artifact is `PARTIAL`: Lyra is the
leaderboard winner, but there is no decision-grade recommendation because shadow
performance is stale to 2026-04-30, promotion governance/readiness evidence is
stale to 2026-06-02, and promotion governance blockers remain.
