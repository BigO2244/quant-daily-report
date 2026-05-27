# Feedback Loop Artifacts

## Purpose

The feedback-loop artifacts make daily shadow strategy behavior easier to inspect after the fact. They are deterministic, read-only diagnostics for Polaris, Orion, and Lyra. They do not change trading, allocation, portfolio construction, execution, or broker submission.

## Artifact List

For each trade date, the shadow runner writes:

- `outputs/shadow_candidates/YYYY-MM-DD/polaris/decision_trace.json`
- `outputs/shadow_candidates/YYYY-MM-DD/polaris/attribution.json`
- `outputs/shadow_candidates/YYYY-MM-DD/polaris/stability_analysis.json`
- `outputs/shadow_candidates/YYYY-MM-DD/polaris/regime_performance.json`
- Equivalent folders for `orion/` and `lyra/`
- `outputs/shadow_candidates/YYYY-MM-DD/feedback_loop_summary.json`
- `outputs/shadow_candidates/performance/feedback_loop_rolling_index.csv`
- `outputs/shadow_candidates/performance/feedback_loop_rolling_index.json`

The folder names use the short strategy names because these are operator-facing diagnostics. Existing canonical strategy JSON files remain unchanged as `caerus_polaris.json`, `caerus_orion.json`, and `caerus_lyra.json`.

The rolling index is additive. It stores one compact row per trade date and
strategy with daily return, turnover, top-3 concentration, valid days,
attribution status, regime, and learning readiness. Weekly reports may use it
after it has enough history, but dated JSON artifacts remain canonical during
the rollout.

## Status Semantics

- `OK`: required inputs were available for the diagnostic.
- `PARTIAL`: the artifact is useful, but one or more optional inputs were unavailable.
- `NO_DATA`: current strategy or market data was unavailable.
- `NO_PRIOR`: current data exists, but prior strategy data was unavailable.
- `BROKEN_CHAIN`: prior performance chain is unavailable or invalid.
- `UNAVAILABLE`: the diagnostic cannot be computed without fabricating data.
- `NO_REGIME_DATA`: VIX regime artifacts were not available.

Missing data is written explicitly. The generator is non-blocking and should not prevent shadow artifacts from completing.

Portfolio learning reports classify artifact health as required, optional
learning, and diagnostics-only. Missing optional learning artifacts are still
reported, but they do not by themselves make the core scoreboard unavailable.

FR-014 learning-health diagnostics are documented in
`docs/shadow_learning_health.md`. The diagnostic is read-only and summarizes
whether required, optional, diagnostic, stale, and LOW-readiness evidence should
limit weekly learning interpretation.

## Promotion Logic Excluded

`feedback_loop_summary.json` always sets:

```json
"ready_for_promotion_logic": false
```

These artifacts improve learning and review quality only. They do not contain promotion, demotion, capital allocation, or strategy switching rules.

## V1 Limitations

- Signal-level attribution is marked `UNAVAILABLE` unless true signal contribution data exists.
- Position attribution uses basic current weight times asset return.
- Decision attribution buckets contributions by entries, exits, weight increases, weight decreases, and holds.
- Regime performance depends on optional `outputs/vix_regime/` artifacts.

## Future Work

- True signal-level attribution.
- Forward return labeling.
- Optimization experiments.
- A separately reviewed promotion/demotion rubric after sufficient evidence exists.
