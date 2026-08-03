# Alpha Lab Decision Brief

Generated: 2026-08-03
Source-reported research state: 2026-07-24
Source revision: PR #160 at `2b4f6c99216a2764d3692735f0e3f783ce7dca0a`
Mission: `mission_1ace1edede9d73889ccf`

## Executive recommendation

Accept the frozen `PARK` verdicts for `EXP-2026-0006`, `EXP-2026-0007`, and
`EXP-2026-0008`.

This recommendation applies only to the evaluated experiments. It does not
retire or reweight any production strategy, change allocation, authorize a new
experiment, access the locked challenge period, promote research, submit an
order, or change paper, pilot, live, scheduler, deployment, or capital behavior.

## Decision summary

| Experiment | Research family | Frozen primary result | Other decisive evidence | Recommendation |
|---|---|---:|---|---|
| `EXP-2026-0006` | Residual momentum | -6.56% annualized excess under stress costs | All three variants were negative; 71 locked-validation observations; corrected significance not implemented; positive-year contribution share 1.0 versus a 0.5 ceiling; $27.997M conservative capacity passed | `ACCEPT_PARK` |
| `EXP-2026-0007` | Stock-specific return seasonality | -7.29% annualized excess under stress costs | Primary exceeded placebo but both were negative; 71 observations; corrected significance not implemented; contribution share 1.0; $25.388M capacity passed | `ACCEPT_PARK` |
| `EXP-2026-0008` | Short-horizon reversal | -44.56% annualized excess under stress costs | Every variant was negative; costs materially worsened the result; 312 weekly observations for the primary; corrected significance not implemented; contribution share 1.0; $11.259M capacity passed | `ACCEPT_PARK` |

## Decision 1 — Residual momentum

Recommended decision: `ACCEPT_PARK` for `EXP-2026-0006`.

The primary 12-minus-1 residual-momentum variant produced -2.96% annualized
excess after base costs and -6.56% under stress costs. The 6-minus-1 and
3-minus-1 variants were also negative under both cost cases. Capacity passed,
so implementability at the stated capital level does not explain the failure.
The experiment additionally failed the frozen corrected-significance and
contributor-concentration gates.

Alternative: authorize a new hypothesis only if the residualization method or
portfolio-construction rule changes materially. The frozen result must remain
unaltered.

Evidence: [EXP-2026-0006 Alpha Card](https://github.com/BigO2244/quant-daily-report/blob/2b4f6c99216a2764d3692735f0e3f783ce7dca0a/projects/alpha_lab/evidence/EXP-2026-0006.md).

## Decision 2 — Stock-specific return seasonality

Recommended decision: `ACCEPT_PARK` for `EXP-2026-0007`.

The five-year same-calendar-month primary produced -3.69% annualized excess
after base costs and -7.29% under stress costs. It exceeded the adjacent-month
placebo, but both were economically negative. Capacity passed; positivity,
corrected significance, and contributor concentration did not.

Alternative: authorize a new hypothesis only for a materially different
lookback, calendar definition, or construction rule. Do not rewrite this
experiment.

Evidence: [EXP-2026-0007 Alpha Card](https://github.com/BigO2244/quant-daily-report/blob/2b4f6c99216a2764d3692735f0e3f783ce7dca0a/projects/alpha_lab/evidence/EXP-2026-0007.md).

## Decision 3 — Short-horizon reversal

Recommended decision: `ACCEPT_PARK` for `EXP-2026-0008`.

The five-day residual-reversal primary produced -18.56% annualized excess in
the base-cost worst-terminal case and -44.56% under stress costs. The 20-day
and volatility-scaled variants were also negative. Capacity passed, but costs
erased rather than preserved the proposed effect. Positivity, corrected
significance, and contributor concentration also failed.

Alternative: authorize a new hypothesis only for a materially different
liquidity screen, holding period, cost model, or construction rule.

Evidence: [EXP-2026-0008 Alpha Card](https://github.com/BigO2244/quant-daily-report/blob/2b4f6c99216a2764d3692735f0e3f783ce7dca0a/projects/alpha_lab/evidence/EXP-2026-0008.md).

## Portfolio consequence

Accepting these decisions clears the three `FROZEN_EVALUATED_REVIEW` items
without making an alpha, promotion, production, or capital claim. The next
source-reported Alpha Lab item was insider-conviction clusters
(`HYP-2026-003`), recorded as `FROZEN_DATA_READY_GATE_PENDING` in the pinned
2026-07-24 source snapshot. The gate-run update below supersedes that next
action without rewriting the historical source state.

## Insider-conviction gate-run update — 2026-08-03

The frozen HYP-2026-003 data gate was run on the authoritative GCP Alpha Lab
research root at `2026-08-03T18:07:57Z`. It created append-only run packet
`20260803T180757Z-hyp-2026-003-data-gate-v1` with manifest hash
`15228845dbfc4d9bdc732c6f3fec2be775d3d8e584eb4276e960ccb28c0d3263`.

Result: `BLOCKED_DATA / UNPROVEN`.

- The rebuilt original Form 4 XML event tape passed its frozen provider gate.
  It contains 137,522 retained events and 113,248 eligible purchases after
  excluding 1,733 amendment-affected issuers fail-closed.
- Security identity, universe membership, filing-time characteristics, factor
  controls, sector controls, and effective-dated CIK mapping passed.
- The sole blocked asset is `pit_prices_liquidity_v1`. Its independently
  verified delisting/terminal-settlement payout and historical PIT
  certification remain incomplete.
- Zero return variants were attempted. The gate did not calculate returns or
  select holdout observations, although its integrity checks hash the complete
  certified datasets, including return-bearing files spanning the holdout.
- No alpha claim, evaluator run, production action, or capital action is
  permitted from this result.

The next permitted research action is to resolve and certify the frozen exact
historical delisting/terminal-settlement contract. The existing sensitivity
envelope cannot silently replace that requirement. A separately reviewed
HYP-2026-003 evaluator must also exist before any return test can run.

## Owner response format

The three recommendations can be resolved together with:

`ACCEPT PARK FOR EXP-2026-0006, EXP-2026-0007, AND EXP-2026-0008`

Any exception should name the experiment and select either
`REQUEST_FURTHER_EVIDENCE` or `AUTHORIZE_NEW_HYPOTHESIS` with a rationale.
