# Orion parity capture and adaptive Shadow readiness

Status date: 2026-08-18 (America/New_York)

## Current factual status

A later read-only VM search resolved the repository-only blocker. The
2026-08-18 scheduled PAPER session contains a complete same-session Orion
decision, pretrade broker state, canonical target, exact plan, and intended
order vector. The stdout-only collector emitted:

- `orion_legacy_paper_factual_fixture_20260818.json`;
- fixture content hash
  `c9d37d63f48282d2421a427f08e0aa3eaee4b31ca07228cdd47196446a498cfb`;
- `orion_legacy_paper_factual_vm_sources_20260818.json`, which preserves every
  remote path, byte hash, causal identifier, and the no-write/no-submit audit.

This makes the legacy factual comparison input `READY`; it is not itself a
generic-path parity result, promotion decision, deployment assignment, or
execution authorization. The comparison target was 5% cash and 19% each in
INTC, LRCX, MU, STX, and WDC. The intended order vector was BUY one WDC at a
$514.91 limit and SELL two INTC at a $97.15 limit.

## Generic dual-compute status: review required

The actual generic advisory chain ran from the immutable 2026-08-18
evaluation and legacy-decision batches through standard decision v2, lane
allocation, canonical target, independent Risk, and exact plan v4. The full
lineage is sealed at `orion_generic_factual_replay_20260818.json` with parity
status `REVIEW_REQUIRED`.

The historical decision adapter uses declared formulas rather than filling
unknown history with estimates. A hash-valid, complete legacy recommendation
gets source-completeness confidence 1.0; risk and capacity are explicitly
`NOT_RECORDED_IN_LEGACY_V1`; turnover is labeled as the full-from-cash
one-way upper-bound proxy; and liquidity remains `UNKNOWN`. The targetless
`sleeve_quality` functional diagnostic becomes a non-tradable `OBSERVATION`,
so it cannot enter allocation or acquire invented target economics.

The generic target exactly matches the legacy target: 5% cash and 19% each in
INTC, LRCX, MU, STX, and WDC. The order vector does not match:

- both paths sell two INTC at a $97.15 limit;
- generic exact plan v4 also sells one LRCX at a $321.10 limit;
- legacy instead buys one WDC at a $514.91 limit.

This is a planner-semantics difference: generic v4 floors every symbol to its
strict target quantity, while the legacy whole-share planner selected a
nearest-feasible portfolio under its drift tolerance. The result is not a
promotion or cutover approval. Generic PAPER remains `NOT_YET_CUT_OVER`, the
active PAPER runtime remains unchanged, and the artifact grants no execution
or activation authority.

## Corrected cash-aware replay: exact parity

The initial mismatch artifact remains immutable. A separate corrected replay,
`orion_generic_factual_replay_cash_aware_20260818.json`, replaces only the
mechanical whole-share realization method; it does not alter the approved
target, Risk package semantics, sleeve set, or authority state.

The corrected plan uses an exhaustive proof-bounded precision grid to minimize
squared position-and-cash tracking error. It then applies deterministic
absolute-error, turnover-including-fees, order-count, and symbol-vector tie
breakers. Sell proceeds fund buys in the same projection. Nonnegative holdings,
minimum cash, no leverage, order/notional caps, quantity precision, and causal
sleeve contributions remain hard gates. Protective limit ticks round toward
the safe side of the adverse-price collar.

The result is `EXACT_PARITY`: both paths sell two INTC at $97.15 and buy one
WDC at $514.91. The proof evaluates 66 bounded candidates and records a $0 fee
assumption. Projected cash is $568.53, an error of -$1.57725 against the 5%
cash target and inside the governed $7.0921 tolerance. This closes the factual
Stage 4–8 parity gate only. PAPER remains `NOT_YET_CUT_OVER`; Live remains
disabled with its kill switch armed; no broker call, submission, deployment,
execution, or activation authority is granted.

## Cash-aware realization correction: exact parity

The initial `REVIEW_REQUIRED` artifact above remains immutable evidence. A
separate corrected replay is sealed at
`orion_generic_factual_replay_cash_aware_20260818.json`. Exact plan v4 now uses
the governed `cash_aware_nearest_feasible_v1` realization policy: an exhaustive,
proof-bounded precision-grid search minimizes joint position-and-cash tracking
error, then absolute error, fee-inclusive turnover, order count, and the stable
symbol/quantity vector. It preserves the Risk-approved target weights and
causal sleeve lineage; it does not select or substitute securities.

The corrected result is `EXACT_PARITY`. It sells two INTC at $97.15 and buys
one WDC at $514.91. Side-aware tick rounding floors BUY collars and ceils SELL
collars so an order never becomes more adverse than its governed limit. The
projected cash is $568.53 versus the $570.10725 cash target, an error of
-$1.57725 inside the explicitly inherited $7.0921 tolerance and above the 2.5%
hard cash floor. Fees are explicitly assumed to be $0 per order for this
historical replay.

This proof resolves the generic-path economic parity gate for the captured
session only. It remains no-submit and non-authoritative; generic PAPER is
still `NOT_YET_CUT_OVER` and active PAPER remains unchanged pending the other
migration gates and owner approval.

The earlier local-only search remains sealed as a historical `BLOCKED`
observation:

- `orion_legacy_paper_fixture_capture_status_20260818.json`
- committed Orion policy evidence is available at revision
  `1b397d004b4d75bbcc1a7efb0e1b2ad55613fdac`;
- the repository and reachable Git history did not contain the complete input
  set;
- the missing fields were subsequently recovered from immutable VM outputs;
- the modified worktree registry is recorded only as an observation and is not
  authority evidence;
- generic PAPER is `NOT_YET_CUT_OVER`; active PAPER runtime remains unchanged;
- no sleeve, including Lyra, is inferred to have current PAPER authority from
  local or research artifacts.

## Structural replay is not factual parity

`orion_legacy_synthetic_replay_20260812.json` is the strongest structural
Orion replay available from committed sources. It was generated in an
isolated `git archive` snapshot using the pure/no-submit
`build_exact_execution_plan` fixture. It reproduces only test economics:

- starting NAV `$1,000`, cash `$900`, and one `OLD` share;
- sell one `OLD` share at a synthetic `$100` limit;
- buy two `AAPL` shares at a synthetic `$50` limit;
- post-plan comparison target: 90% cash and 10% AAPL.

The artifact is deliberately labeled `SYNTHETIC_REPLAY` and
`STRUCTURAL_COMPARISON_ONLY`. It has no historical broker evidence, no factual
return evidence, no cutover eligibility, and no execution or activation
authority. It cannot satisfy or replace either the factual Orion input or the
factual generic-path comparison.

The adaptive allocator also had no factual input set in the initial capture.
That historical readiness remains `BLOCKED`, not synthetic performance
evidence:

- `adaptive_shadow_evidence_readiness_20260818.json`
- at that capture, no owner-approved adaptive policy artifact;
- no factual sleeve-decision v2 batch;
- no causal, pre-as-of signal set;
- no deployment-policy hash selected for this research observation.

## Read-only evidence search completed

The search covered the current workspace, all reachable Git refs, deleted
historical paths, real parity fixtures, GHA captures, PAPER state, broker
snapshots, intended orders, execution payloads, and precompute artifacts.

Potentially complete execution sessions were rejected rather than relabeled:

| Session | Evidence found | Why it is not an Orion fixture |
|---|---|---|
| 2026-07-07 | Real parity directory with snapshot, planned payload, execution payload/results, pre/post positions and signals | The planned payload explicitly names `growth_engine_v4` as the execution strategy and Polaris as the Shadow baseline. Orion was a Shadow challenger in that revision. |
| 2026-03-12 | Deleted GHA run recoverable from parent `485d284b43bd6b79e7419b592b58ee9ab694e07d` | Run ID is `2026-03-12:main:growth_engine_v4`; it has no Orion decision lineage. |
| 2026-03-16 | Deleted Alpaca PAPER/GHA run recoverable from the same historical tree | Run ID is `2026-03-16:main:growth_engine_v4`; it has no Orion decision lineage. |
| 2026-04-08 and 2026-05-06 | Current broker reconciliation files | No matching Orion decision, canonical economic target, or same-run intended-order lineage is present. |

Research NAV, scorecard, attribution, and dashboard artifacts were excluded:
they are not broker-state/decision/target/order evidence and cannot establish
legacy PAPER economics.

The clean committed snapshot also contains point-in-time research datasets,
but none is a sealed adaptive policy, causal signal set, sleeve-decision v2
batch, or selected deployment-policy artifact. Those datasets cannot be
relabeled as an adaptive Shadow evidence session, so the adaptive readiness
artifact remains `BLOCKED` rather than emitting synthetic performance.

The owner subsequently approved exact candidate hash `0ee486...` for Shadow
observation only. The decision record is
`docs/governance/decision_records/adaptive_shadow_v1_owner_approval_20260818.json`.
The enabled observation readiness result is sealed separately at
`adaptive_shadow_v1_activation_readiness_20260818.json`. It remains
`BLOCKED_STATIC_POLARIS_FALLBACK`: Shadow deployment membership, a complete
Polaris+Lyra decision-v2 batch, both causal signals, 60-valid/20-green history,
and full capacity/liquidity/overlap constraint evidence are missing. No
adaptive performance evidence was emitted.

## Exact Orion recovery inputs

Capture one clean session only when all five items are available and agree on
trade date, session, as-of time, account hash, and broker-state hash:

1. The committed registry revision containing the Orion-only legacy PAPER
   allocation policy.
2. The sealed Orion decision used for that session, including its decision,
   session, and content hashes.
3. The pretrade broker account and position snapshot used by planning.
4. The canonical legacy portfolio target after cash policy, expressed as cash
   plus symbol weights summing to one.
5. The final intended-order vector before submission, including symbol, side,
   quantity, order type, time in force, and limit price.

Do not reconstruct a missing item from fills, current positions, a dashboard,
or a research return series. If any item is unavailable, the correct result is
another `BLOCKED` capture status.

## Read-only Orion collector

`scripts/collect_orion_legacy_fixture.py` reads explicit local artifacts and
prints JSON to stdout. It never contacts the broker or changes any file.
Without `--emit-fixture`, it prints only the READY/BLOCKED inventory.

```text
python scripts/collect_orion_legacy_fixture.py \
  --committed-revision <git-sha> \
  --observed-at <iso-timestamp> \
  --decision <sealed-orion-decision.json> \
  --broker-state <pretrade-broker-state.json> \
  --economic-target <normalized-economic-target.json> \
  --orders <normalized-intended-orders.json>
```

Only after the inventory is READY may the same command add
`--emit-fixture` plus explicit trade date, session ID/hash, as-of time,
account hash, and captured-at time. Output remains stdout-only.

## Off-by-default adaptive Shadow runner

`scripts/run_adaptive_shadow_evidence.py` is disabled unless
`--enable-shadow-observation` is present. A disabled call emits `DISABLED`; an
enabled call with missing inputs emits `BLOCKED_STATIC_POLARIS_FALLBACK`. The
runner emits readiness only—never security targets, orders, PAPER eligibility,
LIVE eligibility, execution authority, or activation authority.

```text
python scripts/run_adaptive_shadow_evidence.py \
  --candidate docs/governance/proposals/adaptive_shadow_v1_policy_candidate.json \
  --owner-decision docs/governance/decision_records/adaptive_shadow_v1_owner_approval_20260818.json \
  --registry config/research/strategy_registry.json \
  --observed-at <iso-timestamp> \
  --enable-shadow-observation
```

Readiness additionally requires explicit paths for Shadow deployment
membership, the decision-v2 batch, Polaris and Lyra causal signals, readiness
history, and constraint evidence. The runner hashes those immutable inputs,
prints to stdout, and does not write runtime pointers or configuration. Even a
`READY_FOR_ADAPTIVE_EVIDENCE_RUN` result remains non-executable and holds the
static Polaris control until separate adaptive evidence is sealed.
