# FR-069 Cassiopeia Phase A Evidence

Status: PHASE_A_EVIDENCE_GENERATED  
Owner: Caerus Research Program  
Last Updated: 2026-06-17  
Governance Label: RESEARCH_ONLY  
Execution Impact: NON_EXECUTIONAL  
Classification: BLOCKED_DATA  

RESEARCH_ONLY  
NO_RUNTIME_CHANGE

## 1. Executive Summary

Cassiopeia can remain in the research program because the event-driven thesis is
materially different from the existing momentum and crisis-reversal sleeves.
However, Cassiopeia is not Shadow-ready. The repo-local evidence supports a
decision-grade Phase A conclusion: event timing can be made PIT-safe for
EDGAR-derived events, but Cassiopeia does not yet have a candidate event tape
or forward-return evidence.

The immediate evidence-generating path is not broad analyst/index testing. The
fastest PIT-safe path is a Cassiopeia-specific activist 13D event tape using
EDGAR `acceptanceDateTime`, ticker mapping lineage, and raw payload hashes.

This document and its paired artifacts do not implement production strategy
logic, activate a sleeve, generate live signals, create trades, alter
allocations, alter execution, alter broker behavior, alter risk controls, alter
promotion thresholds, or modify cron.

## 2. Event Taxonomy

| Event Class | Classification | PIT Feasibility | Immediate Phase A Decision |
|---|---|---:|---|
| Activist 13D filings | SUPPORTED | High for EDGAR timing; medium for extraction | Build first Cassiopeia-specific event tape. |
| Earnings 8-K events | LIMITED | High | Use as timing-contract precedent; keep Cygnus-owned unless explicitly carved out. |
| Guidance | LIMITED | Medium | Needs parser, direction labels, and family ownership decision. |
| Corporate actions / delistings | LIMITED | Medium | Use for survivorship and exclusions, not positive long-only catalyst selection. |
| Macro events | LIMITED | Medium | Use as context or overlay input, not MVP single-name Cassiopeia selection. |
| Analyst actions | UNAVAILABLE | High if timestamped vendor/news source exists | Not immediately testable from repo-local data. |
| Index inclusion/exclusion | UNAVAILABLE | High if official announcement tape exists | Not immediately testable from repo-local data. |
| Dividend changes | UNAVAILABLE | Medium if announcement timestamp tape exists | Not immediately testable from repo-local data. |
| M&A / rumors / deal outcomes | EXCLUDED | Low for MVP | Exclude until outcome-leakage controls and revision lineage exist. |

## 3. PIT Feasibility Analysis

Cassiopeia must be availability-timestamp first. Event date, effective date,
membership date, or completed outcome is not enough.

Available PIT-safe source pattern:

- `outputs/research/cygnus/2026-06-10/cygnus_event_tape.csv` contains 7,267
  EDGAR 8-K earnings events across 199 unique tickers.
- `outputs/research/cygnus/2026-06-10/cygnus_acceptance_timestamp_audit.json`
  records zero missing timestamps and `look_ahead_safe=true`.
- EDGAR `acceptanceDateTime` can support a Cassiopeia event-tape contract for
  SEC-derived event families such as activist 13D filings.

Available control data:

- `data/pit_universe/security_events.csv` contains 14,790 Sharadar delisting
  rows. These should support survivorship controls and negative exclusions, not
  positive event selection.

Unavailable for immediate Cassiopeia testing:

- analyst action tape with publication timestamps;
- official index announcement tape with announcement timestamps;
- dividend-change event tape with announcement timestamps;
- Cassiopeia-specific candidate event tape;
- Cassiopeia forward-return event study;
- Cassiopeia holdings overlap study.

## 4. Event Coverage

| Evidence Source | Rows | PIT Timing Quality | Cassiopeia Use |
|---|---:|---|---|
| Cygnus EDGAR earnings tape | 7,267 | High | Timing contract precedent only. |
| Sharadar security events | 14,790 | Medium | Delisting/survivorship controls. |
| Cassiopeia 13D tape | 0 | Not yet built | Highest-value next task. |
| Cassiopeia analyst tape | 0 | Missing | Blocked until source exists. |
| Cassiopeia index tape | 0 | Missing | Blocked until source exists. |

## 5. Candidate Event Classes

Immediate:

- Activist 13D filings, because EDGAR acceptance timestamps provide a
  defensible PIT availability source.

Limited:

- Earnings 8-K events, because they are PIT-safe but Cygnus-owned.
- Guidance, because source timing is plausible but direction extraction is not
  built.
- Delistings, because they are useful for exclusions and survivorship controls.

Not immediate:

- Analyst upgrades and target-price increases.
- Index additions.
- Dividend changes.

Excluded:

- M&A rumors, deal completions, and deal breaks.
- Unstructured legal/regulatory catalysts without source lineage.

## 6. Differentiation Assessment

| Comparator | Classification | Assessment |
|---|---|---|
| Polaris | MEDIUM | Different trigger source than core momentum, but price confirmation may create overlap. |
| Orion | MEDIUM | Different from rank-decay momentum, but overlap cannot be measured without holdings. |
| Lyra | MEDIUM | Event-window selection differs from holding-period momentum, but empirical correlation is missing. |
| Phoenix | HIGH | Crisis reversal and catalyst event selection are structurally different; overlap should be episodic. |

Cassiopeia is differentiated by hypothesis. It is not yet differentiated by
measured alpha, active share, correlation, drawdown profile, or holdings overlap.

## 7. Risks

- Look-ahead through effective dates, completed outcomes, or revised event
  metadata.
- Survivorship bias through current ticker membership or missing delisting joins.
- Ticker mapping drift across CIK, ticker, accession, and security master IDs.
- Sparse event samples after fail-closed timestamp rules.
- Analyst/index source restatements or retroactive corrections.
- Momentum overlap once price confirmation is added.
- Transaction costs and slippage around crowded catalyst dates.
- Earnings-event double counting with Cygnus.

## 8. Missing Data

1. Owner-approved Cassiopeia event contract.
2. Cassiopeia event tape with `availability_timestamp`.
3. Activist 13D parser and ticker mapping lineage.
4. Analyst action publication timestamp tape.
5. Official index announcement timestamp tape.
6. Forward-return event study by event family.
7. Holdings overlap and return correlation versus Polaris, Orion, Lyra, and
   Phoenix.
8. Sparse-sample diagnostics and source restatement checks.

## 9. Readiness Classification

Classification: BLOCKED_DATA

Rationale:

- Cassiopeia is viable enough to continue research because SEC-derived event
  timing can be PIT-safe and the sleeve thesis is differentiated.
- Cassiopeia is not ready for Shadow because it has no Cassiopeia-specific event
  tape, no event-family forward returns, no sparse-sample diagnostics, and no
  overlap evidence.
- The fastest path to a decision-grade alpha answer is a 13D event-tape build,
  followed by a passive forward-return study.

## 10. Artifact Links

- `outputs/research/cassiopeia/cassiopeia_phase_a_event_inventory_2026-06-17.json`
- `outputs/research/cassiopeia/cassiopeia_phase_a_event_inventory_2026-06-17.md`

Explicit statement:

RESEARCH_ONLY  
NO_RUNTIME_CHANGE
