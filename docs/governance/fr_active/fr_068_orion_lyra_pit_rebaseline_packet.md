# FR-068 Orion/Lyra PIT Rebaseline Packet

Status: RESEARCH_EVIDENCE_PACKET
Owner: Caerus Research Program
Last Updated: 2026-06-17
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL
Decision Status: REDUNDANT_CONTINUE_OBSERVING

This packet audits the available FR-068 / FR-069 evidence for Orion and Lyra
and tests whether Lyra's apparent lead is statistically meaningful. It does not
retire Orion, retire Lyra, promote either sleeve, change allocations, change
strategy selection, alter risk controls, submit broker orders, install cron, or
change paper/live trading behavior.

RESEARCH_ONLY
NO_RUNTIME_CHANGE

## 1. Executive Summary

Lyra is not proven better than Orion.

The available matched-window evidence supports three conclusions:

1. Orion and Lyra are materially redundant core-momentum sleeves.
2. Lyra's current apparent lead is not statistically meaningful.
3. The actual Orion/Lyra PIT rebaseline is still missing, so no promotion,
   retirement, or allocation decision is decision-grade.

Current governance classification remains:

`REDUNDANT_CONTINUE_OBSERVING`

Lyra may remain the low-confidence watch-list leader because some governance
surfaces rank it first, but the statistical evidence does not justify treating
Lyra as materially superior.

## 2. Evidence Inventory

| Evidence | Path | Status | Finding |
|---|---|---|---|
| PIT universe readiness | `outputs/research/pit_rebaseline/fr068_phase_2_5_readiness.json` | Decision-grade infrastructure evidence | PIT large-cap family built; SEP hydration complete; no unresolved blockers. |
| Polaris PIT priced rebaseline | `outputs/research/pit_rebaseline/polaris_priced_2026-06-10.json` | Decision-grade for Polaris | Legacy current-universe results are materially distorted; promotion evidence must use PIT. |
| Orion PIT rebaseline | `outputs/research/pit_rebaseline/` | Missing | No Orion PIT rebaseline artifact exists. |
| Lyra PIT rebaseline | `outputs/research/pit_rebaseline/` | Missing | No Lyra PIT rebaseline artifact exists. |
| Orion/Lyra redundancy packet | `docs/governance/fr_active/fr_069_orion_lyra_redundancy_packet.md` | Governance evidence | Current state is `REDUNDANT_CONTINUE_OBSERVING`; no retirement/promotion. |
| PIT evidence plan | `docs/governance/fr_active/fr_069_orion_lyra_pit_evidence_plan.md` | Required protocol | Requires PIT universe hashes, matched windows, overlap, risk, cost, and regime evidence. |
| Deep differentiation | `outputs/research/strategy_differentiation/2026-06-08/strategy_differentiation_deep.json` | Useful, not final PIT disposition | Orion/Lyra verdict `WEAK_DIFFERENTIATION`, confidence `MEDIUM`. |
| Differentiation diagnostic | `outputs/research/differentiation_diagnostic/2026-06-02/differentiation_diagnostic.json` | Useful, not final PIT disposition | Orion/Lyra verdict `TRUE_WEAK_DIFFERENTIATION`, confidence `HIGH`. |
| Promotion readiness windows | `outputs/research/promotion_readiness/2026-06-08/promotion_readiness_windows.json` | Matched-window evidence | Both WATCH at 20/40 days; both NOT_READY at 60 days; confidence LOW at 60 days. |
| Promotion governance | `outputs/research/promotion_governance/2026-06-08/promotion_governance.json` | Governance evidence | Both BLOCKED; Lyra rank 1 and Orion rank 2 with equal rank score. |
| Shadow readiness | `outputs/shadow_candidates/2026-06-08/promotion_readiness.json` | Low-confidence current leader surface | Lyra is current leader, but both are OBSERVE, LOW confidence, zero valid observation windows. |
| Latest shadow comparison | `outputs/shadow_candidates/2026-06-08/comparison.json` | Holdings/overlap evidence | Orion and Lyra share four of five names; both have 20% max weight and 60% top-3 concentration. |
| Regime attribution | `outputs/research/regime_attribution/2026-06-08/regime_attribution.json` | Context evidence | Long-history regime results are similar; not a substitute for Orion/Lyra PIT rebaseline. |

## 3. PIT Methodology

The required decision-grade protocol is defined in FR-068 and FR-069:

- use `universe_method=pit_universe`;
- use universe snapshot hashes and price-source lineage;
- exclude the 2025-forward holdout where applicable;
- compare Orion and Lyra over identical windows;
- report returns, drawdown, volatility, turnover, concentration, overlap,
  factor/sector exposure, cost sensitivity, and regime behavior;
- preserve explicit reason codes and source artifact paths.

The PIT foundation is ready, but the Orion and Lyra PIT rebaseline artifacts
are not present. Therefore this packet separates:

- **available matched-window shadow evidence**, which can classify the current
  observed relationship; from
- **missing PIT rebaseline evidence**, which is required before any disposition.

## 4. Matched Window Results

Latest matched-window evidence from
`outputs/research/promotion_readiness/2026-06-08/promotion_readiness_windows.json`:

| Window | Orion total return | Lyra total return | Leader | Orion volatility | Lyra volatility | Orion turnover | Lyra turnover |
|---:|---:|---:|---|---:|---:|---:|---:|
| 20D | 13.09% | 9.92% | Orion | 65.41% | 70.31% | 1.90% | 3.81% |
| 40D | 52.04% | 46.31% | Orion | 53.14% | 56.57% | 1.48% | 2.96% |
| 60D | 80.83% | 83.16% | Lyra | 54.50% | 60.15% | 1.48% | 2.96% |

Interpretation:

- Orion leads the 20D and 40D windows.
- Lyra leads the 60D window by only 2.33 percentage points.
- Orion has lower realized volatility in every displayed window.
- Orion has roughly half Lyra's turnover in every displayed window.
- Both remain blocked by weak differentiation.

## 5. Correlation Analysis

Orion/Lyra correlations are high enough to treat the pair as effectively
equivalent until stronger PIT evidence proves otherwise:

| Source | Window | Orion/Lyra return correlation | Interpretation |
|---|---:|---:|---|
| Deep differentiation | 20D | 0.9766 | Redundancy warning. |
| Deep differentiation | 40D | 0.9758 | Redundancy warning. |
| Deep differentiation | 60D | 0.9777 | Redundancy warning. |
| Differentiation diagnostic | up to 60D | 0.9823 | True weak differentiation. |
| Full nonzero shadow NAV series | 2015-01-02 to 2026-06-05 | 0.9700 | Persistent structural similarity. |

High correlation alone is not a retirement decision. It is sufficient to keep
the pair under redundancy review and to prevent promotion claims based on small
performance differences.

## 6. Overlap Analysis

Latest holdings on 2026-06-08:

| Sleeve | Holdings |
|---|---|
| Orion | WDC, STX, MU, LRCX, INTC |
| Lyra | WDC, STX, MU, INTC, GLW |

Overlap:

- shared names: WDC, STX, MU, INTC;
- unique Orion name: LRCX;
- unique Lyra name: GLW;
- latest overlap weight: 80%;
- deep differentiation 20/40/60D holdings overlap: 71.0% / 74.3% / 74.3%;
- sector overlap: 1.0;
- factor similarity: 0.9765;
- active share proxy: 0.257 to 0.29 in the 2026-06-08 deep differentiation
  surface, and 0.20 in the 2026-06-02 diagnostic.

This is redundancy evidence, not distinct-sleeve evidence.

## 7. Drawdown Analysis

| Window / Regime | Orion max drawdown | Lyra max drawdown | Leader |
|---|---:|---:|---|
| 20D | -13.53% | -13.49% | Lyra by 0.04pp |
| 40D | -13.53% | -13.49% | Lyra by 0.04pp |
| 60D | -16.45% | -16.73% | Orion by 0.28pp |
| Bull trend regime | -16.48% | -18.40% | Orion |
| Panic regime | -79.78% | -80.23% | Orion |
| Recovery regime | -4.50% | -5.61% | Orion |

Drawdown does not support a Lyra superiority conclusion. Orion is slightly
better in several risk slices, but the differences are small relative to the
pair's shared momentum exposure.

## 8. Cost Sensitivity

No dedicated Orion/Lyra PIT cost-sensitivity artifact was found.

Available turnover evidence implies Orion is more cost-efficient in the current
matched windows:

- 20D turnover: Orion 1.90%, Lyra 3.81%;
- 40D/60D turnover: Orion 1.48%, Lyra 2.96%;
- shadow readiness average turnover: Orion 1.43%, Lyra 2.86%.

Because Lyra's return edge is small and statistically weak, higher turnover is
a material counterweight. A dedicated PIT cost study is required before treating
Lyra as the preferred sleeve.

## 9. Regime Analysis

Regime attribution context from 2026-06-08:

| Regime | Orion total return | Lyra total return | Leader |
|---|---:|---:|---|
| Bear trend | -23.99% | -26.80% | Orion |
| Bull trend | 161.73 | 146.28 | Orion |
| High vol | 39.57% | 39.95% | Lyra by small amount |
| Low vol | 39.66% | 43.32% | Lyra |
| Neutral | 53.94% | 81.60% | Lyra |
| Panic | -79.03% | -79.50% | Orion |
| Recovery | 65.76% | 65.10% | Orion |

The regime result is mixed. Orion leads in bull trend, bear trend, panic, and
recovery; Lyra leads in low-vol and neutral regimes. This does not establish a
stable Lyra advantage, and the artifact is contextual rather than a completed
Orion/Lyra PIT rebaseline.

## 10. Statistical Confidence

Paired daily return-difference test from
`outputs/shadow_candidates/performance/shadow_nav_series.csv`, using Lyra minus
Orion:

| Window | Observations | Lyra minus Orion total return | Mean daily diff | t-stat | Correlation | Classification |
|---|---:|---:|---:|---:|---:|---|
| Latest 20D | 20 | -3.17pp | -0.1296% | -0.58 | 0.9766 | No Lyra lead. |
| Latest 40D | 40 | -5.73pp | -0.0894% | -0.71 | 0.9758 | No Lyra lead. |
| Latest 60D | 60 | +2.33pp | +0.0346% | +0.32 | 0.9777 | Not meaningful. |
| Full nonzero series | 2,873 | +28.46pp cumulative index spread | +0.0010% | +0.09 | 0.9700 | Not meaningful. |

Statistical conclusion:

- Lyra's apparent outperformance is not statistically meaningful.
- Orion is not statistically proven superior either.
- The pair should be treated as effectively equivalent until the missing PIT
  rebaseline is generated.

Confidence:

`MEDIUM` for redundancy/equivalence under available matched evidence.

`LOW` for final disposition because Orion/Lyra PIT rebaseline artifacts are
missing.

## 11. Governance Classification

Allowed governance output:

`REDUNDANT_CONTINUE_OBSERVING`

Secondary label:

`LYRA_LOW_CONFIDENCE_WATCH_LIST_LEADER`

Rejected outputs:

- `LYRA_PROVEN_BETTER`: unsupported by paired statistics.
- `ORION_RETIREMENT_WATCH`: premature without PIT rebaseline and cost study.
- `ORION_PROVEN_BETTER`: unsupported by paired statistics.
- `PROMOTION_READY`: blocked by weak differentiation, missing PIT disposition
  evidence, and governance blockers.

Governance actions not authorized:

- no Orion retirement;
- no Lyra retirement;
- no promotion;
- no allocation change;
- no strategy selection change;
- no Argo allocation input change.

## 12. Missing Evidence

The blocking gaps are:

1. Orion PIT rebaseline artifact with `universe_method=pit_universe`.
2. Lyra PIT rebaseline artifact with `universe_method=pit_universe`.
3. Universe snapshot hashes for both sleeves.
4. Matched PIT daily return series for Orion, Lyra, Polaris, and SPY.
5. PIT holdings overlap, active share, sector, and factor exposure series.
6. PIT turnover and explicit transaction-cost sensitivity.
7. PIT drawdown decomposition and tail-loss co-movement.
8. Regime decomposition from the PIT rebaseline outputs.
9. FR-069 evidence envelopes for Orion and Lyra.
10. Owner-reviewed disposition thresholds for retirement-watch versus continue.

## 13. Recommended Next Evidence Generation

Do not stop at "insufficient evidence." The smallest next packet that can
resolve uncertainty is:

**FR-068 Orion/Lyra PIT Matched Rebaseline Artifact Generation**

Scope:

1. Reuse the existing FR-068 PIT large-cap family and Sharadar SEP price cache.
2. Run Orion and Lyra over the same pre-holdout PIT window used by the Polaris
   priced rebaseline.
3. Emit one machine-readable packet:
   `outputs/research/pit_rebaseline/orion_lyra_matched_2026-06-XX.json`.
4. Include:
   - universe hash;
   - price-source manifest hash;
   - strategy parameters;
   - matched daily returns;
   - return/drawdown/volatility/turnover/cost metrics;
   - overlap/active-share/sector/factor metrics;
   - paired daily return-difference test;
   - regime decomposition;
   - reason codes and decision-grade flags.
5. Produce companion FR-069 evidence envelopes for Orion and Lyra.

Go/no-go criteria for disposition after that packet:

- If Lyra beats Orion after costs with paired t-stat >= 2.0, lower or comparable
  drawdown, and materially differentiated holdings/regime behavior, promote
  Lyra to preferred-retain watch list.
- If Orion beats Lyra after costs with paired t-stat >= 2.0 and lower turnover
  or drawdown, promote Orion to preferred-retain watch list.
- If neither lead is statistically meaningful and overlap/correlation remain
  high, keep both observing or open an owner-approved retirement-watch packet
  for the higher-cost / lower-clarity sleeve.

## 14. Reviewer Challenge

Attempted invalidation:

- **Sample size:** latest windows are too short for final disposition; full
  nonzero series is longer but not a completed PIT Orion/Lyra rebaseline.
- **Survivorship bias:** Polaris PIT evidence proves legacy current-universe
  results can be materially distorted; Orion/Lyra must be rerun PIT before
  decisions.
- **PIT validity:** actual Orion/Lyra PIT artifacts are absent.
- **Overlap assumptions:** multiple independent surfaces show high overlap and
  correlation; redundancy conclusion is robust.
- **Cost assumptions:** no dedicated cost artifact exists; turnover evidence
  favors Orion and weakens any Lyra superiority claim.
- **Regime assumptions:** regime evidence is mixed and contextual; not decisive.

Reviewer conclusion: the packet can support continued observation and a
specific next evidence task, but cannot support retirement or promotion.

## 15. Final Research Answer

Is Lyra actually better?

No. Lyra is not proven better. It is only a low-confidence watch-list leader in
some governance surfaces.

How confident are we?

- Medium confidence that Orion and Lyra are effectively equivalent/redundant
  under available matched evidence.
- Low confidence in any final disposition because the Orion/Lyra PIT rebaseline
  is missing.

Is Orion a retirement-watch candidate?

Not yet. Orion is a redundancy-review candidate, but retirement-watch requires
the missing PIT matched rebaseline and cost/drawdown decomposition.

Most important next research task:

Generate the Orion/Lyra PIT matched rebaseline artifact and evidence envelopes.
