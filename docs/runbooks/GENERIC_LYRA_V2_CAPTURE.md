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
- an immutable `caerus.lyra_forecast_risk_policy_proposal.v1` artifact;
- a separate immutable owner decision approving that exact proposal hash; and
- the resulting `caerus.lyra_forecast_risk_policy.v1` artifact, which must bind
  the exact owner-decision hash.
- the protected session `caerus.owner_decision.v1`, whose approved patch must
  bind the exact proposal hash, policy-owner-decision hash, and policy terms.

The proposal/decision/policy chain does not yet exist. The code deliberately
will not manufacture approval or accept a self-sealed `approved_by` string. The
required policy is Lyra-only, owner-approved, effective no later than
2026-08-24, and binds the named 20-session annualized static-target return
volatility formula, the liquidity/capacity terms, canonical full-L1 turnover,
and the governed XNYS calendar. Until the exact proposal is approved by Brett
Olson and all three hashes agree, readiness is BLOCKED.

## What is recomputed

The capture does not trust a legacy rank table. It derives the exact 253-session
XNYS window ending 2026-08-24, records availability for every member in the
frozen universe, and retains each member's point-in-time close history.
Members with fewer than 253 observations remain governed but are ineligible;
members with 253 observations on a different calendar are recorded as calendar
mismatches and are also ineligible. From every eligible member it recomputes:

- `r3 = close[t] / close[t-3] - 1`
- `r6_1 = close[t-21] / close[t-126] - 1`
- `r12_1 = close[t-21] / close[t-252] - 1`
- `score = 0.5 * r12_1 + 0.3 * r6_1 + 0.2 * r3`

Candidates are ranked by score descending and symbol ascending. The exact top
five receive equal 20% targets. The bundle then recomputes target-weighted risk,
20-session dollar-volume liquidity, 1%-ADV order capacity, 5%-ADV liquidation
capacity, and canonical full-L1 turnover from the exact protected inputs. The
20-session market evidence must itself match the governed XNYS window. Missing,
`NOT_RECORDED`, `UNKNOWN`, placeholder, or re-sealed substitute evidence is
rejected.

## No-write rehearsal

Run `scripts/capture_generic_lyra_v2.py` with all paths above, including
`--forecast-risk-policy-proposal`,
`--forecast-risk-policy-owner-decision`, `--forecast-risk-policy`, and
`--live-owner-decision` for the independently protected session approval, plus
exact `--session-as-of` and `--captured-at` timestamps. Omit
`--write-advisory-artifacts`. The command validates and returns the sealed
capture in memory while reporting every broker, submission, activation, and
execution authority flag as false.

Only after inspecting that result may an operator repeat the command with
`--write-advisory-artifacts`. That opt-in writes content-addressed, immutable
advisory evidence beneath the explicitly supplied output root. It still cannot
call a broker, build an executable schedule, or submit an order.

## Date-bound advisory capture boundary

`scripts/cron_governed_lyra_capture_20260825.sh` is a thin one-date wrapper for
the first eligible capture. It is intentionally not installed. Its template at
`config/templates/governed_lyra_capture_20260825.env.example` defaults to
`CAERUS_GOVERNED_LYRA_CAPTURE_ENABLED=0`; in that state it exits successfully
without reading any capture input or writing any file. When the literal flag is
changed to `1`, it accepts only the exact 2026-08-25 execution session and
2026-08-24 signal, at or after 08:15 ET, after the 07:00 ET precompute. It reads
the config as command-free literals rather than sourcing it and rejects every
unresolved `REPLACE_WITH_` token.

Do not install this boundary until the exact evidence-policy owner decision,
final policy, and session Live owner decision have been approved and placed at
the template paths. Enabling capture does not enable Live, PAPER, submission,
post-trade processing, a kill switch, or an execution schedule.

After those approvals, stage the disabled template first:

```bash
install -m 600 config/templates/governed_lyra_capture_20260825.env.example \
  /home/brettolson/.caerus/governed_lyra_capture_20260825.env
```

Replace the three pending policy/owner paths, independently verify their
hashes, and only then change the single capture-enable value to `1`. The exact
one-date 08:15 ET install command is:

```bash
CAPTURE_CRON='15 8 25 8 * /home/brettolson/quant-daily-report/scripts/cron_governed_lyra_capture_20260825.sh >> /home/brettolson/quant-daily-report/logs/governed_lyra_capture_20260825.log 2>&1 # CAERUS_GOVERNED_LYRA_CAPTURE=2026-08-25'
(crontab -l 2>/dev/null || true; printf '%s\n' 'CRON_TZ=America/New_York' "${CAPTURE_CRON}") \
  | awk '!seen[$0]++' | crontab -
```

This task does not run that command. Verify the exact line with `crontab -l`
before August 25. Remove only that line after success, failure, or abandonment:

```bash
CAPTURE_CRON='15 8 25 8 * /home/brettolson/quant-daily-report/scripts/cron_governed_lyra_capture_20260825.sh >> /home/brettolson/quant-daily-report/logs/governed_lyra_capture_20260825.log 2>&1 # CAERUS_GOVERNED_LYRA_CAPTURE=2026-08-25'
crontab -l | grep -Fvx "${CAPTURE_CRON}" | crontab -
mv /home/brettolson/.caerus/governed_lyra_capture_20260825.env \
  /home/brettolson/.caerus/governed_lyra_capture_20260825.env.disabled
```

The rollback removes no PAPER line and does not change either Live kill gate.

## Activation binding

The Live v1 activation preflight v4 binds the decision hash, full capture hash,
and a sealed raw-source reproduction proof. Immediately before preflight and
submission, runtime reads all twelve explicit source files, rehashes their exact
bytes and resolved paths, rebuilds the capture byte-for-byte, and passes that
proof through both boundaries. Historical v1/v2/v3 BLOCKED preflights remain valid
evidence, but neither can authorize activation because they lack the complete
raw-source binding.

Required protected configuration pins are:

- `CAERUS_GENERIC_LIVE_LYRA_CAPTURE_PATH`
- `CAERUS_GENERIC_LIVE_LYRA_CAPTURE_HASH`
- `CAERUS_GENERIC_LIVE_LYRA_RAW_SOURCE_RECOMPUTE_HASH`
- the exact protected paths for the session manifest, evaluation and legacy
  decision batches, current/prior Lyra sources, universe freeze and bytes,
  price panel, policy proposal, owner policy decision, and approved policy;
- the existing exact decision, plan, preflight, owner-decision, account, source
  deployment, and operational-proof pins.

## Remaining gates

Readiness is not green merely because the contracts exist. It still requires:

1. completed 2026-08-24 price and weekly-shadow inputs;
2. the factual 2026-08-25 immutable precompute bundle;
3. the sealed proposal → owner decision → forecast-risk-policy chain;
4. a capture that recomputes the complete universe rank and all evidence;
5. a fresh account observation and an exact Lyra-only v4 plan;
6. byte-exact all-green Live preflight recomputation at the approved session.

There is also an intentional small-account constraint: 95% of the $460 owner
ceiling is $437, or $87.40 for each of five equal targets before whole-share
rounding. The $100 minimum order therefore exceeds each ideal target notional.
The exact planner may correctly produce no trade or a nearest-feasible result;
neither outcome may be overridden. Changing the minimum order, concentration,
capital ceiling, or allocation economics requires a new owner decision.
