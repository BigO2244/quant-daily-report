# Generic Lyra v2 Prospective Capture

Status: implemented source contract; not deployed or scheduled. This workflow
is explicit-input, no-submit, and no-write by default. It does not promote Lyra,
activate Live, change PAPER, read broker state, or grant execution authority.

## First eligible chronology

The governed-universe freeze became effective on 2026-08-19. Historical Lyra
evaluation-only artifacts retain their original blockers and cannot be
relabelled. The first prospective weekly selection is therefore:

- signal and effective target: Monday 2026-08-24 after the completed close;
- execution session and capture date: Tuesday 2026-08-25;
- canonical precompute timing: 07:00 America/New_York on 2026-08-25;
- exact plan: advisory only until the full protected capture and every separate
  Live preflight gate validate.

The capture must fail closed if the 2026-08-24 close is incomplete, if any
input is stale or hash-mismatched, or if capture is attempted before the
prospective freeze.

## Exact input set

For the 2026-08-25 capture, provide these files explicitly:

- `outputs/precompute/2026-08-25/session_manifest.json`
- `outputs/precompute/2026-08-25/sleeve_evaluations.json`
- `outputs/precompute/2026-08-25/sleeve_decisions.json`
- `outputs/shadow_candidates/2026-08-24/caerus_lyra.json`
- the immediately prior weekly Lyra source, expected at
  `outputs/shadow_candidates/2026-08-17/caerus_lyra.json`
- `docs/evidence/lyra_governed_universe_freeze_2026-08-19.json`
- the exact frozen bytes at `data/universe.csv`
- `outputs/research/flow_detection_v1/price_panel.parquet`, complete through
  2026-08-24
- an immutable owner-approved `caerus.lyra_forecast_risk_policy.v1` artifact.

The final item does not yet exist. The code deliberately will not manufacture
approval. The required policy is Lyra-only, owner-approved, effective no later
than 2026-08-24, and binds the named 20-session annualized static-target return
volatility formula. Until the owner decision is sealed, readiness is BLOCKED.

## What is recomputed

The capture does not trust a legacy rank table. It records availability for
every member in the frozen universe and retains each member's point-in-time
close history through 2026-08-24. Members with fewer than 253 observations
remain governed but are ineligible. From every eligible member it recomputes:

- `r3 = close[t] / close[t-3] - 1`
- `r6_1 = close[t-21] / close[t-126] - 1`
- `r12_1 = close[t-21] / close[t-252] - 1`
- `score = 0.5 * r12_1 + 0.3 * r6_1 + 0.2 * r3`

Candidates are ranked by score descending and symbol ascending. The exact top
five receive equal 20% targets. The bundle then recomputes target-weighted risk,
20-session dollar-volume liquidity, 1%-ADV order capacity, 5%-ADV liquidation
capacity, and half-L1 turnover from the exact protected inputs. Missing,
`NOT_RECORDED`, `UNKNOWN`, placeholder, or re-sealed substitute evidence is
rejected.

## No-write rehearsal

Run `scripts/capture_generic_lyra_v2.py` with all paths above plus exact
`--session-as-of` and `--captured-at` timestamps. Omit
`--write-advisory-artifacts`. The command validates and returns the sealed
capture in memory while reporting every broker, submission, activation, and
execution authority flag as false.

Only after inspecting that result may an operator repeat the command with
`--write-advisory-artifacts`. That opt-in writes content-addressed, immutable
advisory evidence beneath the explicitly supplied output root. It still cannot
call a broker, build an executable schedule, or submit an order.

## Activation binding

The Live v1 activation preflight v2 binds both the decision hash and the full
capture hash. Runtime must load the exact protected capture, recompute the
decision byte-for-byte, and match both hashes from protected configuration.
The historical v1 BLOCKED preflight remains valid evidence, but a v1 artifact
can never authorize activation because it lacks the capture binding.

Required protected configuration pins are:

- `CAERUS_GENERIC_LIVE_LYRA_CAPTURE_PATH`
- `CAERUS_GENERIC_LIVE_LYRA_CAPTURE_HASH`
- the existing exact decision, plan, preflight, owner-decision, account, source
  deployment, and operational-proof pins.

## Remaining gates

Readiness is not green merely because the contracts exist. It still requires:

1. completed 2026-08-24 price and weekly-shadow inputs;
2. the factual 2026-08-25 immutable precompute bundle;
3. the sealed owner-approved forecast-risk policy;
4. a capture that recomputes the complete universe rank and all evidence;
5. a fresh account observation and an exact Lyra-only v4 plan;
6. byte-exact all-green Live preflight recomputation at the approved session.

There is also an intentional small-account constraint: 95% of the $460 owner
ceiling is $437, or $87.40 for each of five equal targets before whole-share
rounding. The $100 minimum order therefore exceeds each ideal target notional.
The exact planner may correctly produce no trade or a nearest-feasible result;
neither outcome may be overridden. Changing the minimum order, concentration,
capital ceiling, or allocation economics requires a new owner decision.
