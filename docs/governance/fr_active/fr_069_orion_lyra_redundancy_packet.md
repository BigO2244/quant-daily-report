# FR-069 Orion/Lyra Redundancy Governance Packet

Status: RESEARCH_ONLY_PACKET
Owner: Caerus Research Program
Last Updated: 2026-06-17
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL
Decision Status: REDUNDANT_CONTINUE_OBSERVING

This packet applies the FR-069 Phase C readiness gates to Orion and Lyra before
any promotion, retirement, rename, or redeployment decision. It uses existing
artifacts only. It does not change live or paper trading behavior, strategy
selection, allocation, target generation, risk controls, broker behavior, or
cron.

## Executive Summary

Current evidence says Orion and Lyra are materially redundant expressions of
the same core-momentum sleeve family. The strongest current redundancy signals
are:

- Orion/Lyra return correlation near 0.98 across 20/40/60-day windows.
- Orion/Lyra holdings overlap above the FR-063 redundancy-warning threshold.
- Sector overlap of 1.0 and factor similarity around 0.976.
- Both are blocked by promotion governance for weak differentiation.

The evidence is not decision-grade for retirement or promotion. Both sleeves
remain `current_shadow_challenger` entries in the FR-069 manifest and must
continue observing. Lyra is the current preferred watch-list candidate only by a
small and low-confidence lead in the shadow promotion-readiness surface; Orion
retains evidence advantages in the 20/40-day window metrics and lower turnover.

Recommendation: keep both observing, mark the pair as
`REDUNDANT_CONTINUE_OBSERVING`, and require PIT-safe rebaseline plus a
post-FR-070 canonical observation window before any retirement proposal.

## Evidence Sources Inspected

| Source | Path | Finding |
|---|---|---|
| FR-069 Phase C readiness | `docs/governance/fr_active/fr_069_phase_c_readiness.md` | Defines Research -> Shadow -> Paper -> Pilot Capital -> Production -> Retired gates. |
| FR-069 Orion/Lyra PIT plan | `docs/governance/fr_active/fr_069_orion_lyra_pit_evidence_plan.md` | Requires PIT evidence, correlation, overlap, risk, turnover, regime decomposition, and owner approval before disposition. |
| FR-063 redundancy study | `docs/governance/fr_active/fr_063_orion_lyra_redundancy_study.md` | High correlation is a warning, not a retirement decision; allowed output includes `REDUNDANT_CONTINUE_OBSERVING`. |
| Sleeve manifest | `research_registry/sleeves/manifest.json` | At packet creation, Orion and Lyra used Shadow-observed manifest classifications. Current capital authority is orthogonal; both still require decision-grade PIT evidence and owner approval before a research-driven promotion, retirement, rename, or allocation change. |
| Deep differentiation | `outputs/research/strategy_differentiation/2026-06-08/strategy_differentiation_deep.json` | Orion/Lyra verdict is `WEAK_DIFFERENTIATION`, confidence `MEDIUM`. |
| Differentiation diagnostic | `outputs/research/differentiation_diagnostic/2026-06-02/differentiation_diagnostic.json` | Orion/Lyra verdict is `TRUE_WEAK_DIFFERENTIATION`, confidence `HIGH`. |
| Promotion readiness windows | `outputs/research/promotion_readiness/2026-06-08/promotion_readiness_windows.json` | Both are `WATCH` for 20/40 days and `NOT_READY` for 60 days; confidence drops to `LOW` at 60 days. |
| Promotion governance | `outputs/research/promotion_governance/2026-06-08/promotion_governance.json` | Both are `BLOCKED`, evidence strength `LOW`; Lyra ranks 1 and Orion ranks 2, both with equal rank score. |
| Shadow readiness | `outputs/shadow_candidates/2026-06-08/promotion_readiness.json` | Current leader is Lyra, but both have `OBSERVE`, `LOW` confidence, zero valid observation windows, and instability/missing-data reason codes. |
| Latest shadow comparison | `outputs/shadow_candidates/2026-06-08/comparison.json` | Orion and Lyra each hold five names with four shared holdings and identical concentration. |

## Orion vs Lyra Comparison

| Dimension | Orion | Lyra | Interpretation |
|---|---:|---:|---|
| Manifest lifecycle | `shadow_observed` | `shadow_observed` | Same lifecycle state. |
| Current 2026-06-08 holdings | WDC, STX, MU, LRCX, INTC | WDC, STX, MU, INTC, GLW | Four of five holdings overlap; Orion-only LRCX, Lyra-only GLW. |
| Holdings count | 5 | 5 | Same concentration design. |
| Max weight | 20% | 20% | Same max-name exposure. |
| Top-3 concentration | 60% | 60% | Same concentration profile. |
| Estimated holding period | 156 days | 133 days | Orion appears slower-turnover / longer-hold in latest comparison. |
| 20-day return | 13.09% | 9.92% | Orion leads in the short window. |
| 40-day return | 52.04% | 46.31% | Orion leads in the middle window. |
| 60-day return | 80.83% | 83.16% | Lyra leads slightly in the longest current window. |
| 20-day turnover | 1.90% | 3.81% | Orion lower. |
| 40/60-day turnover | 1.48% | 2.96% | Orion lower. |
| 60-day max drawdown | -16.45% | -16.73% | Orion slightly lower drawdown. |
| Shadow readiness cumulative excess vs Polaris | 133.01 | 137.52 | Lyra slight lead, but confidence is `LOW`. |
| Shadow readiness state | `OBSERVE` | `OBSERVE` | Neither is promotion-ready. |
| Promotion governance rank | 2 | 1 | Lyra slight current preference, equal rank score and both blocked. |

## Redundancy Assessment

The redundancy evidence is strong enough to justify continued focused review,
but not strong enough to retire either sleeve.

| Metric | Current evidence | Threshold interpretation |
|---|---:|---|
| 20-day return correlation | 0.9766 | Above FR-063 redundancy warning level of 0.90. |
| 40-day return correlation | 0.9758 | Above warning level. |
| 60-day return correlation | 0.9777 | Above warning level. |
| 20-day holdings overlap | 0.71 | Above FR-063 warning level of 70%. |
| 40/60-day holdings overlap | 0.7429 | Above warning level. |
| Active share proxy | 0.2571 to 0.29 | Below/near weak-differentiation floor. |
| Sector overlap | 1.0 | No sector differentiation. |
| Factor similarity | 0.9765 | Very high factor similarity. |

Current classification: `REDUNDANT_CONTINUE_OBSERVING`.

Rejected classifications:

- `DISTINCT_CONTINUE_OBSERVING`: not supported because correlation, overlap,
  sector exposure, and factor similarity are too high.
- `RETIREMENT_WATCH`: premature because PIT rebaseline, canonical observation,
  and data-quality gates remain incomplete.
- `BLOCKED_INSUFFICIENT_EVIDENCE`: too conservative for redundancy; there is
  enough evidence to classify redundancy risk, but not enough for disposition.

## Performance and Readiness

Performance evidence is mixed:

- Orion leads Lyra on 20-day and 40-day total return, excess return versus
  Polaris, hit rate, turnover, and slightly lower drawdown.
- Lyra leads Orion on 60-day total return and the shadow readiness cumulative
  excess surface.
- The promotion governance artifact ranks Lyra first and Orion second, but both
  have equal rank score, `LOW` evidence strength, and `BLOCKED` decisions.

Readiness evidence is not sufficient for promotion:

- Both have `weak_differentiation` blockers.
- Both have `execution_timing_unavailable`, `no_planned_orders`, and
  `plan_payload_missing` blockers in promotion governance.
- Both have universe/security-master blockers in promotion governance.
- Shadow readiness reports `insufficient_history`, `missing_data_penalty`, and
  `unstable_performance` for both.
- The 60-day promotion readiness window is `NOT_READY` for both with `LOW`
  confidence.

## Governance Recommendation

1. Keep both Orion and Lyra in Shadow.
2. Prefer Lyra only as the current watch-list leader because current governance
   rankings and shadow readiness identify Lyra first.
3. Do not retire Orion or Lyra.
4. Do not promote Orion or Lyra.
5. Do not rename or redeploy either strategy name.
6. Treat Orion/Lyra as a correlated pair under FR-069 and require a single
   future owner-approved disposition packet before any action.

Governance state:

| Gate | Status | Rationale |
|---|---|---|
| Research | PASS | Existing artifacts support redundancy-risk classification. |
| Shadow | CONTINUE | Both are already shadow-observed; no change. |
| Paper | BLOCKED | Weak differentiation, missing PIT/readiness evidence, and no execution-risk review. |
| Pilot Capital | BLOCKED | Not applicable before Paper approval. |
| Production | BLOCKED | No promotion packet or owner approval. |
| Retired | BLOCKED | Retirement requires explicit owner approval and decision-grade evidence. |

## Required Evidence Before Retirement or Promotion

Any future disposition packet must include:

1. PIT rebaseline for both Orion and Lyra using `universe_method=pit_universe`.
2. Universe snapshot hashes and price-source lineage.
3. Holdout exclusion flag set before metric computation.
4. Matched-window daily returns over the canonical
   `dated_same_day_close_to_close_v1` observation series.
5. Post-FR-070 stable observation window after execution/target-attainment
   artifacts are classified cleanly.
6. Pairwise return correlation against Orion, Lyra, Polaris, and SPY.
7. Holdings overlap, active share, sector/factor exposure, and concentration.
8. Turnover, costs, drawdown, hit rate, and tail-risk diagnostics.
9. Regime decomposition with `INSUFFICIENT_SAMPLE` labels where appropriate.
10. Valid FR-069 sleeve evidence envelopes for both Orion and Lyra.
11. Explicit owner decision for promotion, retirement, rename, or continued
    observation.

## Risks and Assumptions

- Some historical Shadow NAV rows are lineage-only after the owner-approved
  same-day restatement. This packet uses existing artifacts as governance
  evidence, not as final promotion evidence.
- The latest detailed artifacts inspected are dated 2026-06-08. They predate
  the 2026-06-17 FR-073 sleeve numeric diagnostics incident and should not be
  treated as post-incident promotion evidence.
- High correlation and holdings overlap may be partially structural because
  both strategies are core-momentum variants with five-name concentration.
- Lyra's current watch-list lead is low-confidence and should not be treated as
  a final preference.
- Orion's lower turnover and stronger 20/40-day window metrics remain relevant
  counter-evidence against immediate retirement.

## Decision Status

`RESEARCH_ONLY / NO_RUNTIME_CHANGE`

Allowed next state: continue shadow observation and build decision-grade PIT
evidence envelopes.

Disallowed actions from this packet:

- retire Orion;
- retire Lyra;
- promote Orion or Lyra;
- rename or reuse either strategy name;
- change allocation, rankings, signals, sizing, execution, broker behavior,
  target generation, cron, or risk controls.
