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
source-reported Alpha Lab item is insider-conviction clusters
(`HYP-2026-003`), currently `FROZEN_DATA_READY_GATE_PENDING`. Its explicit next
action is to rerun the frozen gate against the rebuilt fail-closed original-XML
tape. This brief recommends reviewing that gate as the next Alpha Lab research
action; it does not authorize or execute it.

## Owner response format

The three recommendations can be resolved together with:

`ACCEPT PARK FOR EXP-2026-0006, EXP-2026-0007, AND EXP-2026-0008`

Any exception should name the experiment and select either
`REQUEST_FURTHER_EVIDENCE` or `AUTHORIZE_NEW_HYPOTHESIS` with a rationale.
