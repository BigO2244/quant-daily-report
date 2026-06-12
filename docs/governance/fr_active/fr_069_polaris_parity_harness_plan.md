# FR-069 Polaris Parity Harness Plan

Status: PHASE_B_SCAFFOLD
Last Updated: 2026-06-12
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL

This plan defines the future parity harness for Polaris. It does not implement
production behavior, change the strategy registry, alter execution, or modify
paper/live trading.

## Invariant

Any future generalized sleeve interface must reproduce current Polaris research
and paper-reference artifacts before it can be used as a migration path.

Required invariant:

- current Polaris behavior unchanged;
- same eligible universe for the same `as_of_date` and PIT method;
- same signal ordering after deterministic tie-breaks;
- same selected holdings and weights within approved tolerance;
- same benchmark and transaction-cost assumptions;
- same artifact semantics and governance labels.

## Inputs Required

- FR-068 PIT universe family and snapshot hashes.
- PIT price source used by the Polaris rebaseline.
- Existing Polaris strategy/shadow artifacts.
- Existing `config/research/strategy_registry.json` Polaris metadata.
- Research-only sleeve manifest entry for `polaris`.
- Frozen date window outside protected holdout unless explicitly approved.

## Artifacts To Compare

- candidate universe membership;
- selected holdings;
- weights;
- NAV/return series;
- turnover;
- drawdown;
- attribution;
- promotion-readiness inputs;
- artifact-envelope fields.

## Tolerance

Phase C must specify numeric tolerances before implementation. Default
expectation is exact match for IDs, dates, universe membership, and holdings
where deterministic. Floating-point metrics may use small absolute tolerances
only if source calculations explain rounding differences.

## Look-Ahead Controls

- Use PIT universe membership only.
- Use only data available as of the tested `as_of_date`.
- Require `universe_snapshot_hash` and price-source metadata.
- Exclude holdout windows unless a single owner-approved holdout run is
  recorded.
- Mark legacy current-universe evidence as non-decision-grade.

## Future Validation Sequence

1. Load Polaris from the research-only manifest.
2. Load the existing Polaris reference artifacts.
3. Build generalized-sleeve research outputs in a temp/fixture path.
4. Compare artifacts with deterministic parity checks.
5. Fail closed on missing PIT lineage, missing artifact fields, or unexpected
   drift.
6. Only after parity passes may later FRs consider applying the interface to
   Orion/Lyra research evidence.
