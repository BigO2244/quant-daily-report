# FR-064 Multi-Asset Research Framework

Status: DRAFT_RESEARCH
Owner: Caerus Research Program
Last Updated: 2026-06-08
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL

## Purpose

FR-064 defines the design and audit framework for evaluating whether
non-equity sleeves could improve Caerus portfolio quality. It does not implement
trading, allocation, or order generation.

## Candidate Sleeves

- Treasury duration: `SHY`, `IEF`, `TLT`
- Cash / T-bill: `SGOV`, `BIL`
- Gold: `GLD`, `IAU`
- Broad commodities: `DBC`, `PDBC`
- Managed-futures proxy: `DBMF`, `CTA`
- Defensive equity ETF proxy: `SPLV`, `USMV`
- Options overlay: `DEFERRED_DESIGN_ONLY`

## Research Questions

- Do non-equity sleeves improve drawdown, recovery, volatility, and correlation
  quality versus the equity-only stack?
- Which sleeve returns are robust across regimes?
- Which required data is currently available, stale, or missing?
- What promotion preconditions would be required before any implementation?
- When should options be considered, and which infrastructure must exist first?

## Required Data

- Point-in-time daily adjusted prices for candidate ETFs.
- Trading calendar alignment with existing equity research artifacts.
- Regime labels and VIX/volatility context available as of each date.
- Expense, liquidity, and tradability metadata before any future promotion
  discussion.
- Clear source paths and reason codes for every missing or stale input.

## Non-Goals

- No allocation engine.
- No trading implementation.
- No broker calls or order submission.
- No options execution integration.
- No change to live/paper strategy weights.
- No fabricated prices, returns, or sleeve histories.

## Artifact Contract

The audit artifact must include `candidate_sleeves`, `required_data`,
`available_data`, `missing_data`, `research_questions`,
`promotion_preconditions`, `execution_impact: NON_EXECUTIONAL`,
`options_status: DEFERRED_DESIGN_ONLY`, and `reason_codes`.
