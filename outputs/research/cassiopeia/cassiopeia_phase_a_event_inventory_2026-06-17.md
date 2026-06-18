# Cassiopeia Phase A Event Inventory

Date: 2026-06-17  
Classification: BLOCKED_DATA  
Governance Label: RESEARCH_ONLY  
Execution Impact: NON_EXECUTIONAL  

RESEARCH_ONLY  
NO_RUNTIME_CHANGE

## Executive Summary

Cassiopeia remains viable as a research hypothesis, but it is not Shadow-ready.
The first decision-grade evidence generated here is an event inventory and PIT
feasibility artifact, not an alpha backtest. The repo has enough evidence to
define what can be tested immediately and what cannot:

- Immediately testable: SEC acceptance-timestamped event-tape construction,
  especially Schedule 13D/EDGAR-derived activist events.
- Limited: Cygnus earnings 8-K events prove the PIT timestamp pattern, but
  earnings drift is Cygnus-owned and should not be silently reassigned.
- Limited/exclusion-only: Sharadar delisting events can support survivorship
  and exclusion controls, not positive long-only Cassiopeia catalysts.
- Unavailable: analyst actions and index inclusion/exclusion tapes with source
  publication timestamps.
- Excluded from MVP: M&A/deal-risk and rumor-like events because outcome leakage
  risk is high without a stricter event contract.

The decision is BLOCKED_DATA for Shadow readiness, with a narrow unblocked next
evidence task: build a PIT-safe Cassiopeia 13D event tape using EDGAR
acceptanceDateTime, ticker mapping lineage, and raw payload hashes.

## Agent Loop Findings

| Agent | Finding |
|---|---|
| Event Auditor | Existing evidence supports EDGAR timestamp auditing and security-master delisting controls. No repo-local analyst or index event tape exists. |
| Event Architect | The taxonomy must classify event classes as SUPPORTED, LIMITED, UNAVAILABLE, or EXCLUDED and fail closed when availability timestamp or lineage is missing. |
| Evidence Builder | Phase A can generate a decision-grade inventory and feasibility artifact, but cannot produce a Cassiopeia alpha result without a Cassiopeia event tape. |
| Differentiation Analyst | Cassiopeia is conceptually differentiated from Polaris, Orion, Lyra, and Phoenix, but empirical differentiation is unmeasured because no Cassiopeia holdings exist. |
| Reviewer | The conclusion is invalid for Shadow until event timing, survivorship, ticker mapping, source restatements, and overlap diagnostics are tested from an event tape. |

## Event Taxonomy

| Event Class | Classification | PIT Feasibility | Immediate Test? | Decision |
|---|---|---:|---:|---|
| Activist 13D filings | SUPPORTED | High for EDGAR timing; medium for extraction | Yes, tape-build only | Highest-value next task |
| Earnings 8-K Item 2.02 | LIMITED | High | Timing reference only | Keep Cygnus-owned unless carved out |
| Guidance | LIMITED | Medium | Timing/extraction audit only | Needs parser and direction labels |
| Corporate actions/delistings | LIMITED | Medium | Exclusion/control only | Do not use as positive long catalyst |
| Macro events | LIMITED | Medium | Context only | Better as overlay/regime input |
| Analyst upgrades/targets | UNAVAILABLE | High if vendor timestamps exist | No | Needs PIT publication tape |
| Index inclusion/exclusion | UNAVAILABLE | High if official timestamps exist | No | Needs official announcement tape |
| Dividend changes | UNAVAILABLE | Medium | No | Needs announcement timestamp tape |
| M&A/deal-risk/rumors | EXCLUDED | Low for MVP | No | Exclude until outcome leakage controls exist |

## PIT Feasibility Analysis

Available repo-local evidence:

- `outputs/research/cygnus/2026-06-10/cygnus_event_tape.csv`: 7,267 EDGAR
  earnings 8-K events, 199 unique tickers, zero missing timestamps, with
  `acceptance_datetime_utc`, `acceptance_datetime_et`, and availability dates.
- `outputs/research/cygnus/2026-06-10/cygnus_acceptance_timestamp_audit.json`:
  `look_ahead_safe=true`, zero missing timestamps, and no listed look-ahead
  violations.
- `data/pit_universe/security_events.csv`: 14,790 Sharadar delisting events,
  useful for survivorship and exclusion controls.
- `data/macro/`: macro series exist, but release timestamp handling was not
  validated for event-driven single-name selection.

PIT-safe rule for Cassiopeia Phase A: event date alone is insufficient. A
candidate event requires availability timestamp, source identifier, source
ingested timestamp, raw payload hash, ticker mapping lineage, and fail-closed
handling when any of those fields are missing.

## Event Coverage

| Source | Rows | Coverage Role | Decision |
|---|---:|---|---|
| Cygnus EDGAR earnings tape | 7,267 | Timestamp pattern proof | Reuse timing contract; do not claim Cassiopeia alpha |
| Sharadar security events | 14,790 | Delisting/survivorship controls | Use for exclusions and lineage |
| Cassiopeia analyst tape | 0 | MVP analyst actions | Missing |
| Cassiopeia index tape | 0 | MVP index events | Missing |
| Cassiopeia 13D tape | 0 | MVP activist events | Build next |

## Candidate Event Classes

Supported now for a first Cassiopeia-specific build:

- Activist 13D filings from EDGAR acceptance timestamps.

Limited but useful:

- Earnings 8-K events as an event timing pattern, not as Cassiopeia alpha.
- Guidance events if parsed from EDGAR filings with direction labels.
- Delisting/corporate action events as exclusions.
- Macro events as context, not single-name event selection.

Unavailable for immediate testing:

- Analyst upgrades, downgrades, initiations, and target-price changes.
- Index additions and removals.
- Dividend changes.

Excluded from MVP:

- M&A rumors, announced deals, deal completions, and deal breaks.
- Qualitative legal/regulatory events without structured source lineage.

## Differentiation Assessment

| Comparator | Differentiation | Rationale |
|---|---|---|
| Polaris | MEDIUM | Cassiopeia is event-availability driven rather than pure core momentum, but price confirmation may create overlap. |
| Orion | MEDIUM | Orion is rank-decay momentum; Cassiopeia trigger source differs, but overlap must be measured. |
| Lyra | MEDIUM | Lyra is holding-period momentum; Cassiopeia should be event-window based, but no holdings evidence exists. |
| Phoenix | HIGH | Phoenix is crisis-reversal; Cassiopeia is discrete catalyst-driven. Overlap should be episodic rather than structural. |

Cassiopeia is differentiated by thesis. It is not yet differentiated by
measured holdings, returns, correlation, or active share.

## Risks

- Hidden look-ahead from effective dates, completed outcomes, or revised event
  metadata.
- Survivorship bias if event tapes are joined to current ticker universes.
- Sparse samples by event family.
- Ticker drift, CUSIP/CIK ambiguity, and corporate-action mapping errors.
- Vendor/source restatements.
- Overlap with momentum sleeves once price confirmation is added.
- Transaction cost sensitivity around crowded catalyst dates.

## Missing Data

1. Cassiopeia PIT event contract.
2. Cassiopeia event tape with availability timestamps.
3. Analyst action publication timestamp tape.
4. Official index announcement timestamp tape.
5. 13D extraction and issuer ticker mapping lineage.
6. Forward-return event study by family.
7. Holdings overlap and return correlation versus Polaris, Orion, Lyra, and Phoenix.

## Readiness Classification

Classification: BLOCKED_DATA

Evidence:

- A PIT-safe event timing pattern exists via Cygnus EDGAR evidence.
- A Cassiopeia-specific event tape does not exist.
- Analyst and index MVP classes are absent from repo-local data.
- Activist 13D is the fastest PIT-safe Cassiopeia-specific path because EDGAR
  acceptanceDateTime is already proven as a source timing pattern.
- No production behavior, allocation, execution, broker behavior, risk controls,
  promotion thresholds, or cron behavior changed.

## Highest-Value Next Evidence Task

Build `cassiopeia_v0_activist_13d_event_tape`:

- parse Schedule 13D and 13D/A filings from EDGAR;
- persist `acceptance_datetime_utc`, `acceptance_datetime_et`,
  `availability_date`, `cik`, issuer ticker mapping, accession number, primary
  document, source URL or identifier, `source_ingested_at`, and raw payload hash;
- fail closed on missing timestamp or ticker mapping;
- produce coverage, sparse-sample, and timing audit artifacts before any return
  study.

Only after that tape exists should Cassiopeia run a forward-return event study.
