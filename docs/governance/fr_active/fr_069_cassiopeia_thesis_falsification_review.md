# FR-069 Cassiopeia Thesis Falsification Review

Status: THESIS_FALSIFICATION_REVIEW_COMPLETE
Owner: Caerus Research Program
Last Updated: 2026-06-19
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL
Classification: CASSIOPEIA_ACTIVIST_13D_DEMOTED_TO_SECONDARY_PATH
Falsification Assessment: THESIS_WEAK
Recommended Direction: DEMOTE_ACTIVIST_13D_AS_PRIMARY_DIRECTION
Confidence: MEDIUM_HIGH

RESEARCH_ONLY
NO_RUNTIME_CHANGE

## 1. Executive Summary

This review answers whether activist 13D should remain Cassiopeia's primary
event-driven research direction after Phase B and Phase B2 evidence.

Forced recommendation: demote activist 13D from Cassiopeia's primary research
direction to a secondary falsification branch, and make broader event-family
source discovery and event-contract validation the primary Cassiopeia path.

This is not a recommendation to activate Cassiopeia, allocate capital, change
execution, change broker behavior, change risk controls, change promotion
logic, or create trades. Cassiopeia remains Research-stage and non-executing.

Rationale:

- Phase A correctly identified activist 13D as the fastest PIT-safe event tape
  to build because EDGAR `acceptanceDateTime` supports defensible availability
  timing.
- Phase B proved the tape can be built: 430 raw mapped rows, 142 usable filing
  rows, 132 deduped ticker-date events, zero missing timestamps, PIT-safe
  availability, and `LIQUIDITY_OK`.
- Phase B filing-level returns were only mildly supportive: the 60D
  SPY-relative mean was positive, but 1D, 5D, and 20D means were flat to
  negative.
- Phase B2 materially weakened the alpha case after campaign-level dedupe:
  142 PIT-valid filing rows collapsed into 38 filer-subject campaigns, and
  first-campaign returns were negative at 1D, 5D, 20D, and 60D.
- Original `SC 13D` campaign initiations were especially adverse: 13
  first-campaign events had a 60D mean of about `-9.57%`.
- Cost sensitivity worsens already-negative first-campaign results.

The activist 13D thesis is not fully falsified because the campaign sample is
small, PIT sector/SIC cohorts are unavailable, and no complete campaign-level
portfolio simulation exists. However, the evidence is sufficiently negative to
stop treating activist 13D as Cassiopeia's primary direction.

## 2. Evidence For Thesis

The evidence that still supports activist 13D is mostly feasibility and
structural differentiation, not alpha strength.

| Evidence | Interpretation |
|---|---|
| Phase A PIT feasibility | Activist 13D is a plausible Cassiopeia event family because SEC EDGAR acceptance timestamps create a point-in-time availability source. |
| Phase B tape construction | The repo can build a deterministic 13D tape with availability timestamps, tradable-date mapping, forward-return measurement, and liquidity joins. |
| PIT safety | Phase B records zero missing timestamps and `pit_safe=true`; no filing is tradable before EDGAR availability. |
| Filing-level 60D result | Deduped ticker-date events had 60D SPY-relative mean `0.0176470355` and hit rate `0.553030303`. |
| Liquidity | Phase B classified liquidity as `LIQUIDITY_OK`; measured coverage was `1.0`; minimum 5% ADV capacity was about `$253.7M`, well above the `$1M` reference capital test. |
| Differentiation | Activist 13D is triggered by an external corporate filing rather than a price-rank, trend, or crisis-reversal signal. |
| Larger-liquidity hint | Phase B2's `gte_500m` campaign bucket had 8 events and a positive 60D mean of `0.0630926159`, though the sample is too small for decision-grade use. |

This evidence justifies preserving activist 13D as a secondary research branch
and as a useful event-contract proving ground.

## 3. Evidence Against Thesis

The strongest evidence against the activist thesis is that filing-level
evidence weakens after campaign-level dedupe.

| Evidence | Interpretation |
|---|---|
| Filing-level short horizons | Phase B 1D, 5D, and 20D SPY-relative means were negative despite the positive 60D mean. |
| Campaign-level dedupe | Phase B2 collapsed 142 PIT-valid filing rows into only 38 filer-subject campaigns. |
| First-campaign returns | First-campaign means were negative at every measured horizon: 1D `-0.006789635`, 5D `-0.0172086245`, 20D `-0.0272886997`, and 60D `-0.0307274376`. |
| Original initiation weakness | Original `SC 13D` first-campaign events counted only 13 campaigns and had 60D mean `-0.0957321405`. |
| Amendment inflation | Follow-on amendments showed better 60D results than first campaign events, implying the Phase B filing-level positive 60D drift may be inflated by repeated amendment rows rather than initiation alpha. |
| Costs | First-campaign 60D mean worsened from `-0.0307274376` at 0 bps round trip to `-0.0407274376` at 50 bps round trip. |
| Sector evidence | PIT sector/SIC cohorts are unavailable; current industry proxy cohorts are explicitly not PIT decision-grade. |
| Decision-grade flag | Phase B2 marks campaign-level alpha preliminary and `decision_grade=false`. |

The evidence does not support continuing activist 13D as the highest-priority
Cassiopeia alpha path.

## 4. Falsification Assessment

Classification: THESIS_WEAK

The simple thesis that activist 13D filings create tradable persistent
long-only drift is weak. It is not strong enough to remain Cassiopeia's primary
research direction.

Falsification checks:

| Test | Result | Assessment |
|---|---|---|
| Filing-level vs campaign-level distortion | Filing-level 60D is positive; first-campaign 60D is negative. | Strong contradiction. |
| Amendment inflation | Follow-on amendments improve aggregate filing results; original first campaigns are materially negative. | Strong contradiction. |
| Sample-size limitation | Only 38 campaigns; original `SC 13D` first-campaign count is 13. | Prevents full `THESIS_FALSIFIED` classification. |
| Survivorship bias | Events fail closed on mapping and active-security checks, but PIT sector/SIC and broader universe controls remain incomplete. | Partially controlled, still unresolved. |
| Timing bias | EDGAR acceptance-derived tradable dates are PIT-safe; 2025+ is excluded as holdout. | Timing contract is supportive. |
| Cost sensitivity | Costs worsen negative first-campaign returns. | Contradiction. |
| Concentration effects | Liquidity buckets and current-industry proxy cohorts are sparse; `gte_500m` has only 8 events. | Hidden subgroup alpha remains possible but unproven. |
| Overlap/correlation | Orion/Lyra sparse daily correlations are low; Polaris return correlation is unavailable. | Differentiation not a blocker, but not alpha evidence. |

Forced falsification conclusion: activist 13D is not fully falsified as a
research family, but it is falsified as Cassiopeia's current primary direction.

## 5. Alternative Event Classes

Cassiopeia should remain an event-driven sleeve candidate, but its primary
research path should broaden beyond activist-only 13D. Feasibility below uses
the existing FR-052/FR-069 requirement that events need availability
timestamps, source lineage, ticker mapping, and PIT-safe forward-return
measurement.

| Event Class | Potential | Feasibility | Rationale |
|---|---|---|---|
| Earnings-related events | HIGH POTENTIAL | READY_NOW | EDGAR acceptance timestamp precedent is already proven by Cygnus; ownership remains Cygnus unless Cassiopeia receives an explicit carve-out for non-earnings variants or combined-event attribution. |
| Guidance changes | HIGH POTENTIAL | BLOCKED_DATA | Likely event-driven and economically relevant, but needs parser, direction labels, timestamped source lineage, and ownership boundary with Cygnus. |
| Insider activity / Form 4 | HIGH POTENTIAL | READY_NOW_FOR_SOURCE_AUDIT | SEC-driven and timestampable; structurally distinct from momentum; needs Form 4 tape/parser, issuer/officer mapping, transaction-type labels, buy/sell direction handling, and cluster filters. |
| Item-coded non-earnings 8-K events | HIGH POTENTIAL | READY_NOW_FOR_SOURCE_AUDIT | Existing EDGAR timing machinery can generalize to deterministic item classes before semantic NLP; ownership must avoid duplicating Cygnus earnings drift. |
| Dividend actions | MEDIUM POTENTIAL | BLOCKED_DATA | Structured event family, but repo-local announcement timestamp tape is unavailable; ex-date or pay-date would be look-ahead-prone if used as availability. |
| Buybacks | MEDIUM POTENTIAL | BLOCKED_DATA | Potentially useful corporate-action signal, but current repo data does not provide a completed announcement parser or buyback-specific tape; needs authorization size, completion/revision lineage, and leak controls. |
| Spin-offs | HIGH POTENTIAL | BLOCKED_DATA | Economically plausible and differentiated, but event lifecycle is complex and requires announcement, Form 10/S-1/8-K tape, parent/remainco mapping, effective-date rules, when-issued handling, and outcome-leakage controls. |
| Index membership changes | HIGH POTENTIAL | BLOCKED_VENDOR | FR-052 lists index additions as MVP candidate, but official historical announcement timestamps are not repo-local and may require licensed/vendor data. |
| Corporate actions | MEDIUM POTENTIAL | BLOCKED_DATA | Current repo data is useful for survivorship and exclusions, not positive event selection; needs announcement-level source lineage. |
| Regulatory filings | MEDIUM POTENTIAL | READY_NOW_FOR_SOURCE_AUDIT | EDGAR timing is available, but each filing family needs a separate economic thesis, direction labels, and leakage controls. |
| Activist 13D | MEDIUM POTENTIAL | READY_NOW_SECONDARY | Event tape exists and liquidity is viable, but campaign-level returns are negative and further work should be falsification-oriented rather than primary-path promotion. |

## 6. Feasibility Ranking

Forced research queue:

| Rank | Event Class | Research Priority | Status | Next Action |
|---:|---|---|---|---|
| 1 | Insider activity / Form 4 | HIGH | READY_NOW_FOR_SOURCE_AUDIT | Build a research-only Form 4 source audit and pre-register issuer/officer mapping, transaction labels, and cluster-buy/sell rules. |
| 2 | Item-coded non-earnings 8-K events | HIGH | READY_NOW_FOR_SOURCE_AUDIT | Reuse EDGAR availability plumbing for deterministic non-earnings 8-K item classes with explicit Cygnus boundary. |
| 3 | Guidance changes | HIGH | BLOCKED_DATA | Inventory timestamped sources, define positive/negative direction labels, and define ownership boundary with Cygnus. |
| 4 | Spin-offs | HIGH | BLOCKED_DATA | Identify timestamped Form 10/S-1/8-K event sources and parent/remainco mapping requirements. |
| 5 | Index membership changes | HIGH | BLOCKED_VENDOR | Determine whether official timestamped S&P/Russell/Nasdaq announcement history is available and licensable. |
| 6 | Activist 13D Phase B3 | MEDIUM | READY_NOW_SECONDARY | Continue only as a secondary falsification branch: original-only initiation cohort, PIT sector/SIC map, longer sample, campaign consolidation, and portfolio simulation. |
| 7 | Buybacks | MEDIUM | BLOCKED_DATA | Locate announcement timestamp source and define authorization-size, completion, and revision fields. |
| 8 | Dividend actions | LOW_MEDIUM | BLOCKED_VENDOR | Search for announcement timestamp source; do not use ex-date as availability. |
| 9 | Broad corporate actions | LOW | READY_NOW_FOR_CONTROLS | Use current corporate-action data mainly for survivorship/exclusions unless announcement timestamps are sourced. |

The highest-ROI shift is not to abandon Cassiopeia. It is to replace activist
13D as the primary direction with Form 4 insider activity and item-coded
non-earnings 8-K source audits first, followed by guidance, spin-off, and
index-membership source discovery. Activist 13D remains a secondary branch
because the tape is already built.

## 7. Recommended Direction

Decision: demote activist 13D as Cassiopeia's primary research direction.

Equivalent option from the decision menu: Keep activist as secondary path.

Confidence: MEDIUM_HIGH.

Actionable interpretation:

1. Do not continue activist 13D as the lead Cassiopeia alpha path.
2. Do not freeze activist 13D completely; retain it as a secondary Phase B3
   falsification branch because the PIT-safe event tape and liquidity checks are
   reusable.
3. Do not replace Cassiopeia as event-driven. Replace the activist-only primary
   path with broader event-family source discovery and event-contract
   validation.
4. Do not promote, Shadow-onboard, or implement Cassiopeia until a future event
   family produces decision-grade positive expectancy after costs with explicit
   overlap/correlation evidence.

The activist evidence is negative enough to change research direction, but not
complete enough to retire all activist research permanently.

## 8. Risks

- Sample-size overreaction: 38 campaigns and 13 original first-campaign `SC 13D`
  events are small samples; a longer window could change the result.
- Hidden subgroup alpha: larger-liquidity campaigns, specific filer identities,
  campaign intent, or sector cohorts could contain alpha, but current subgroup
  evidence is sparse and not PIT sector decision-grade.
- Event selection bias: activist 13D was selected because it was the fastest
  EDGAR-backed tape to build, not because it was proven to be the highest-alpha
  Cassiopeia event family.
- Amendment interpretation risk: some amendment-first campaigns may reflect
  missing original filings, mapping exclusions, or out-of-window history rather
  than true economic follow-on events.
- Survivorship and mapping risk: current CIK-to-ticker mappings and PIT security
  joins are conservative but may miss historical ticker changes.
- Premature abandonment risk: a stronger original-only, campaign-quality,
  filer-intent, PIT-sector, and portfolio-simulated B3 pass could rehabilitate
  a narrower activist strategy.
- Vendor blocker risk: the best alternative event classes may require paid or
  unavailable timestamped historical data.
- Ownership boundary risk: earnings-related and guidance events may overlap
  with Cygnus unless event-family ownership is explicitly approved.
- Runtime drift risk: future research must remain outside allocation, broker,
  risk, execution, promotion, and cron paths until separately authorized.

## 9. Explicit Statement

RESEARCH_ONLY
NO_RUNTIME_CHANGE

This review does not activate Cassiopeia, generate live signals, change
allocations, change execution, change broker behavior, change risk controls,
change promotion thresholds, modify cron, or create trades.

Referenced evidence:

- `docs/governance/fr_active/fr_069_cassiopeia_phase_a_evidence.md`
- `docs/governance/fr_active/fr_069_cassiopeia_phase_b_13d_event_tape.md`
- `docs/governance/fr_active/fr_069_cassiopeia_onboarding_packet.md`
- `docs/governance/fr_archive/fr_052_cassiopeia_research_spec.md`
- `outputs/research/cassiopeia/cassiopeia_phase_a_event_inventory_2026-06-17.json`
- `outputs/research/cassiopeia/cassiopeia_phase_b_13d_event_tape_2026-06-18.json`
- `outputs/research/cassiopeia/cassiopeia_phase_b2_campaign_segmentation_2026-06-19.json`
