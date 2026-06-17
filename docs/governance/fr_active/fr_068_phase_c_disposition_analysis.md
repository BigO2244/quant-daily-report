# FR-068 Phase C Orion/Lyra Disposition Analysis

Status: RESEARCH_DISPOSITION_PACKET
Owner: Caerus Research Program
Last Updated: 2026-06-17
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL
Decision Status: MERGE_WATCH / LYRA_REDEPLOYMENT_WATCH

This packet uses the generated FR-068 PIT rebaseline artifact to support an
owner decision on whether Orion and Lyra should continue as separate sleeves,
be merged, be redeployed, or enter a retirement-watch process. It does not
retire Orion, retire Lyra, change allocations, change strategy selection, alter
risk controls, submit broker orders, install cron, or change paper/live trading
behavior.

RESEARCH_ONLY
NO_RUNTIME_CHANGE

## 1. Executive Summary

The PIT evidence does not justify carrying Orion and Lyra as two independently
distinct future sleeves.

The evidence now supports a decision-oriented governance path:

1. Treat Orion and Lyra as variations of the same core-momentum sleeve family.
2. Move the pair from open-ended redundancy observation to `MERGE_WATCH`.
3. Use Orion as the provisional retained canonical implementation if the pair
   must be consolidated today, because Orion has lower turnover, lower
   drawdown, lower volatility, and marginally better cost-adjusted cumulative
   return in the PIT artifact.
4. Place Lyra on `REDEPLOYMENT_WATCH`, not immediate retirement, because the
   evidence is strong enough to reject independent-sleeve status but not enough
   to authorize retirement without owner approval.

This is governance decision support only. No sleeve is retired by this packet.

## 2. Evidence Reviewed

| Evidence | Path | Finding |
|---|---|---|
| Orion/Lyra PIT artifact | `outputs/research/pit_rebaseline/orion_lyra_matched_2026-06-17.json` | Decision-grade matched PIT evidence exists for returns, turnover, drawdown, overlap, active share, costs, regimes, and paired significance. |
| PIT rebaseline packet | `docs/governance/fr_active/fr_068_orion_lyra_pit_rebaseline_packet.md` | Previous state was `REDUNDANT_CONTINUE_OBSERVING`; the new artifact resolved the missing-evidence blocker. |
| Orion/Lyra redundancy packet | `docs/governance/fr_active/fr_069_orion_lyra_redundancy_packet.md` | Prior evidence already classified the pair as materially redundant and blocked promotion/retirement without PIT rebaseline. |
| FR-069 Phase C readiness | `docs/governance/fr_active/fr_069_phase_c_readiness.md` | Defines Research -> Shadow -> Paper -> Pilot Capital -> Production -> Retired lifecycle gates and owner-approval requirements. |
| Sleeve manifest | `research_registry/sleeves/manifest.json` | Orion and Lyra are governed shadow candidates; neither may be promoted, renamed, redeployed, or retired by a research packet alone. |

Key PIT artifact values:

| Metric | Orion | Lyra | Interpretation |
|---|---:|---:|---|
| Cumulative return at 10 bps cost | 42.1362x | 41.6263x | Orion marginally leads after realistic costs. |
| CAGR at 10 bps cost | 40.74% | 40.74% | Economically tied. |
| Volatility | 50.19% | 51.53% | Orion lower. |
| Sharpe | 0.933 | 0.919 | Orion slightly higher. |
| Max drawdown | -57.95% | -64.19% | Orion materially lower drawdown. |
| Average turnover | 4.45% | 7.91% | Orion materially lower turnover. |
| Average holding period | 64.14 days | 36.46 days | Orion is less churn-heavy. |
| Return correlation | 0.9202 | 0.9202 | High structural similarity. |
| Holdings overlap | 80.77% | 80.77% | Redundant holdings footprint. |
| Active share | 19.19% | 19.19% | Too low for distinct-sleeve treatment. |
| Paired t-stat | 0.0809 | 0.0809 | No statistically meaningful Lyra lead. |

## 3. Institutional Portfolio View

An institutional portfolio manager would reasonably treat Orion and Lyra as
variations of the same sleeve, not as two distinct capital destinations.

The case is straightforward:

- correlation is high at 0.9202 across 2,767 PIT-matched observations;
- holdings overlap averages 80.77%;
- active share averages only 19.19%;
- sector and factor differentiation are not proven;
- paired significance is effectively zero with a t-stat of 0.0809;
- Lyra requires materially more turnover to produce no statistically meaningful
  advantage;
- Orion has lower realized drawdown and volatility in the generated artifact.

Keeping both as temporary shadow controls is reasonable. Carrying both forward
as separate promotion candidates is not.

## 4. Redundancy Assessment

The redundancy conclusion is now decision-grade for governance triage.

| Question | Answer | Rationale |
|---|---|---|
| Are Orion and Lyra distinct sleeves? | No | 80.77% average holdings overlap and 19.19% active share are below the differentiation needed for separate sleeves. |
| Is Lyra statistically better? | No | Paired t-stat is 0.0809 and Lyra trails Orion after 10 bps cost in cumulative return. |
| Is Orion statistically better? | No | Orion has better risk/cost characteristics, but not a statistically significant return lead. |
| Does cost sensitivity matter? | Yes | At 0 bps Lyra leads by 4.2081x, but at 10/25/50 bps Orion leads by 0.5099x, 5.1077x, and 8.4956x. |
| Is continued open-ended observation likely to change the conclusion? | No | The artifact covers 2,767 matched PIT observations; more passive observation is unlikely to create active share, lower correlation, or a meaningful t-stat without a thesis change. |

Conclusion: the right governance state is no longer generic
`REDUNDANT_CONTINUE_OBSERVING`. The pair should enter a bounded disposition
process under `MERGE_WATCH`, with Lyra separately marked for possible
redeployment.

## 5. Disposition Options

| Option | Assessment | Recommendation |
|---|---|---|
| A. Continue as distinct sleeves | Not supported | Reject. The overlap, active share, and t-stat do not justify independent future capital tracks. |
| B. Treat as same-sleeve variants | Supported | Accept as the current evidence-based classification. |
| C. Candidate merge | Supported | Primary governance path. Prepare a merge packet that selects one canonical implementation. |
| D. Candidate redeployment | Supported for Lyra | Lyra can be reserved for a future renamed/redeployed thesis after owner approval. |
| E. Candidate retirement | Premature as an action today | Use retirement-watch only after owner-approved disposition criteria are adopted. |

Recommended action:

`MERGE_WATCH / LYRA_REDEPLOYMENT_WATCH`

This recommendation does not execute a merge, retirement, or redeployment.

## 6. Risks

- The PIT artifact lacks a PIT sector map and PIT factor exposure model, so
  sector/factor redundancy is inferred from existing governance context rather
  than the new artifact.
- The PIT universe uses the FR-068 large-cap family approximation; this is
  acceptable for disposition triage but should be called out in any owner
  retirement memo.
- The 2025-forward holdout is excluded by design. A future post-2024 holdout
  result could still challenge the conclusion.
- Cost assumptions may differ from future execution reality, but higher
  turnover makes Lyra more fragile across reasonable cost assumptions.
- Immediate retirement is not authorized by FR-069 lifecycle rules and should
  require owner approval.

## 7. Evidence Still Needed

Evidence that would reverse this recommendation:

1. Lyra beats Orion after realistic costs over a new holdout-safe PIT window
   with paired t-stat >= 2.0.
2. Lyra active share persistently exceeds 35% to 40% while holdings overlap
   falls below 65%.
3. Lyra demonstrates a distinct regime edge that is not explained by turnover,
   cost drag, or one substitutable holding.
4. Lyra produces lower drawdown or lower volatility after costs while
   maintaining return parity.
5. A PIT sector/factor decomposition shows materially different exposures that
   provide useful portfolio diversification.

Evidence still useful before an owner action:

- PIT sector and factor overlap model.
- Post-2024 holdout comparison once the holdout is released for governance
  review.
- A short owner-facing merge packet with explicit action choices and rollback
  criteria.

## 8. Recommended Governance Path

| Lifecycle gate | Orion | Lyra | Recommendation |
|---|---|---|---|
| Research | PASS | PASS | Existing artifacts support same-family classification. |
| Shadow | CONTINUE, bounded | CONTINUE, bounded | Keep both only through an owner-approved disposition window. |
| Paper | BLOCKED | BLOCKED | Do not promote both as separate sleeves. |
| Pilot Capital | BLOCKED | BLOCKED | Not applicable before Paper approval. |
| Production | BLOCKED | BLOCKED | No production promotion. |
| Retired | NO ACTION TODAY | WATCH ONLY | Retirement requires owner approval and a final disposition packet. |

Recommended next governance state:

- Pair-level: `MERGE_WATCH`
- Orion: `PROVISIONAL_CANONICAL_CORE_MOMENTUM`
- Lyra: `REDEPLOYMENT_WATCH`

Rationale: if consolidation were forced today, Orion is the better retained
implementation because it provides the same economic exposure with lower
turnover, lower drawdown, lower volatility, and no worse PIT return evidence.

## 9. Potential Replacement Sleeve Analysis

If one sleeve slot is eventually freed, the highest-value replacement candidate
is Phoenix.

| Candidate | Assessment | Priority |
|---|---|---|
| Phoenix | Best replacement sleeve candidate because crisis/dislocation/recovery behavior is most differentiated from core momentum. | 1 |
| Argo | High-value research infrastructure for event evidence, but not yet a direct capital-sleeve replacement. | 2 |
| Cassiopeia | Useful future regime/model-selection framework; should not replace a core security-selection sleeve until evidence exists. | 3 |
| Cygnus | Drift detection is valuable governance infrastructure, but the v0 research path remains data-blocked and is not a sleeve replacement. | 4 |

Phoenix should be prioritized for future research if Orion/Lyra consolidation
frees research capacity. Phoenix offers the clearest diversification against
momentum-family concentration.

## 10. Reviewer Challenge

The strongest challenge to this conclusion is that a high-return historical PIT
sample can still hide future regime breaks. That challenge does not rescue
Lyra as an independent sleeve. It argues for a cautious owner-approved
disposition process rather than an immediate retirement.

The conclusion is also not based on a single metric. It is supported by
correlation, overlap, active share, paired significance, turnover, cost
sensitivity, drawdown, and volatility. To invalidate it, Lyra needs new
evidence of differentiated, cost-adjusted, holdout-safe utility.

## 11. Decision Status

`RESEARCH_ONLY / NO_RUNTIME_CHANGE`

Allowed next work:

- prepare an owner-facing Orion/Lyra merge-watch disposition packet;
- add PIT sector/factor overlap diagnostics;
- generate post-holdout evidence when available;
- advance Phoenix research as the likely replacement candidate.

Disallowed actions from this packet:

- retire Orion;
- retire Lyra;
- promote Orion or Lyra;
- rename or redeploy either strategy name;
- change allocation, rankings, signals, sizing, execution, broker behavior,
  target generation, cron, or risk controls.
