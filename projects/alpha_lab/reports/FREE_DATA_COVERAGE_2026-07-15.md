# Alpha Lab Free-Data Coverage

Date: 2026-07-15

Governance: RESEARCH_ONLY / NONEXECUTIONAL / NO_RETURN_OR_HOLDOUT_ACCESS

## Decision

The free stack is not sufficient to run the four frozen historical evaluators.
It is sufficient to build the common market/identity/control spine, reconstruct
the insider and earnings source filings, and begin honest forward proxy
accumulation for options and analyst aggregates.

`READY_FOR_DATA_GATES` in the infrastructure status means the collectors and
common inputs are operable. It does not mean a frozen experiment is ready. The
experiment-specific gate remains the authority, and all four currently return
`BLOCKED_DATA` without reading returns or holdout observations.

## Coverage by frozen hypothesis

| Hypothesis | Captured now | Free continuation | Gap that free data cannot honestly replace |
|---|---|---|---|
| HYP-2026-002 earnings revisions | PIT security identity, prices/liquidity panel, factors, filing-time characteristics, 257,996 SEC Item 2.02 events, 200-name yfinance forward analyst snapshot | Hydrate and parse original earnings 8-K/8-K-A submissions; accumulate daily current aggregate snapshots; optional free Alpha Vantage snapshot after key registration | Analyst-level historical forecasts with original timestamps, corrections, withdrawals, stable analyst/broker IDs, and historical PIT contributor lineage |
| HYP-2026-003 insider clusters | SEC quarterly discovery tape, exact original-XML selector, exact acceptance-time parser and checkpoints | Complete all 316,822 original Form 4/4-A candidates and rebuild the canonical event tape from originals | No paid source is required if the full original capture and frozen reconciliation gates pass; terminal delisting returns remain a common blocker |
| HYP-2026-004 options information | Daily no-cost yfinance option-chain proxy infrastructure | Continue weekday forward snapshots and five-session maturation | Historical option trades, NBBO quotes, conditions, open interest vintages, and surface/Greek inputs are not universally free |
| HYP-2026-005 supply-chain diffusion | SEC earnings anchors, EIA energy controls, BEA public industry/NAICS mapping, USAspending exact-name federal award proxy | Finish the government-customer subgraph; hydrate original 8-K evidence; add BEA current-vintage IO tables after free key registration | Universal effective-dated issuer customer/supplier graph, dependency, termination, and source-publication history; historical analyst shocks also remain paid |

## Common delisting constraint

Sharadar ACTIONS and the 2011-2026 SEC index produced 17,712 scoped terminal
action candidates, 8,196 action-to-security mappings, and 55,454 nearby 8-K
filing candidates. These are discovery evidence only. They do not prove cash or
share consideration, contingent-value rights, bankruptcy recovery, or a zero
terminal value. Original exhibits and case-specific settlement evidence must be
parsed before the price/liquidity contract can certify terminal returns.

## Free credentials still useful

The ignored project `.env` is ready for:

- `BEA_API_KEY` — free registration; enables current-vintage InputOutput table
  capture. BEA data are industry controls, not issuer edges.
- `ALPHA_VANTAGE_API_KEY` — free registration; enables bounded current aggregate
  earnings-estimate and listing-status snapshots. It is not historical
  analyst-level PIT data.

Neither key is required for the no-key work already in progress. Credential
values are never persisted in research bundles.

## Start rule

Do not run a frozen return evaluator until every required provider gate says
ready. Forward proxy observations may accumulate before then, but they cannot
rewrite the frozen historical contract or support an alpha claim by themselves.
