# September 11 remediation — owner-authorized priorities

Brett's September 11 request supersedes earlier priority ordering: four active
workstreams only. Unrelated dashboard/UI, new brokers, optimizer, infrastructure
and agent architecture work is BACKLOG. No capital increase, model promotion,
Live reactivation, deployment, paid acquisition or new economic trial is performed
by this local implementation. Existing source/risk/lifecycle authorities remain.

## Capital governance and NAV (first priority)

Inspection confirmed Lyra already sizes from fresh factual broker NAV under its
August 19 hashed owner decision. PAPER has a separate full-current-account
invariant. The disabled legacy FR-104/generic Live paths retain their historical
$500 controls; they are not the Lyra operating lane. Do not globally replace 500.

INITIAL_FUNDED_CAPITAL is historical funding provenance ($500), never a sizing
cap. Brett resolved the September 11 ceiling decision: **MAX_LIVE_CAPITAL equals
the current NAV of the applicable Alpaca Live account**. It is a derived,
plan-bound value, not a separately configured dollar amount. A stale environment
value must not override broker NAV. The signed August 19 decision remains intact;
this explicit September 11 instruction governs the local remediation policy.

Sizing basis = current finite positive Alpaca account NAV. The plan records both
max_live_capital_usd and sizing_basis_usd equal to that snapshot's NAV, with an
explicit dynamic-NAV policy identity. Gross remains at most 95% of NAV and the
cash reserve remains at least 5%. Existing no-leverage, fractional, long-only,
five-name, order-count and minimum-notional constraints remain. A new valid
snapshot reflects market changes, deposits and withdrawals; an already sealed
plan is never silently resized when broker state changes.

Alpaca is the canonical actual-trading source for each Alpaca account: account
identity, NAV/equity, cash, positions, open orders, submitted orders and fills.
Live and PAPER must use their own account identity and snapshots; balances are
not pooled or replaced by Shadow/model NAV. Derived valuations and attribution
must retain broker source/time/hash lineage and reconcile to broker records.
Unexplained differences block the applicable gate. This instruction does not
activate the disabled legacy lanes or authorize another account's capital.

Trace: direct Alpaca GET → timestamped pretrade snapshot → Lyra plan
(factual NAV, derived max capital, sizing basis, gross, reserve, snapshot hash)
→ signed mutation context → broker boundary → broker fills and reconciliation.
The executor checks current broker identity, positions, cash and NAV before a
new order and confirms sell proceeds before buying. Invalid or changed inputs
block for reconciliation/replanning; posttrade checks cannot invent a new target.
Posttrade completion requires account NAV to reconcile to cash plus the market
values reported by Alpaca for all positions within the existing $0.01 economic
reconciliation tolerance. Resulting quantities must match starting holdings plus
confirmed fills within 1e-8 shares. Provider quote marks and their timestamps are
retained separately; they never replace reported broker NAV. Missing, stale or
inconsistent evidence prevents completion and preserves the failure for review.
Historical fixed-cap plans are preserved as history and cannot pass the new
policy merely by retaining or changing a numeric ceiling field.

**Owner ceiling decision resolved.** No further dollar amount is required.
Implementation and validation remain local; Live is still paused and deployment
or reactivation requires its own reviewed decision.

## Execution certification

`core/execution_certification.py` adds a five-consecutive-XNYS-session gate on top
of the existing six-control twenty-session integrity metric. It recomputes the
counter from dated evidence, rejects duplicate dates, and resets on any failure,
missing session or unexplained discrepancy. It grants no trading/promotion rights.

It checks freshness/PIT lineage, sealed hashes, exact deterministic plan rebuild,
independent same-input target replay, exact consumed orders, confirmed sell-first
execution and conservative cash budget, broker-bound NAV sizing, explained fills,
final positions, cash/NAV reconciliation and numeric provenance. Exact plans may
forbid discretionary rebudgeting; then their complete conservative budget must
be proven instead. This does not authorize downstream target reconstruction.

Target replay receipts may be retained at
`outputs/execution_certification/<date>/target_replay.json`, but neither a receipt
nor two matching copied targets can pass certification. The certifier directly
executes `core/target_replay.py`: two independent loads of the original
plan-bound evaluated inputs, original generation/allocation timestamps, admitted
source hashes, and captured registry policy drive the existing decision and
allocation producers. Full rebuilt decisions, allocation, target projection and
consumed weights must agree. Originally absent sources must remain absent.
The current implicit Aquila registry must exactly match captured registry bytes;
drift fails closed instead of substituting a current policy. Diagnostics retain
producer code hashes, role-bound inputs and the complete plan source-hash map.

This proof covers evaluated inputs → sleeve decisions → allocation → sealed
target → consumed target weights. It does not establish alpha-model economics,
broker NAV sizing or committed runtime regime/submission authority. The verifier
uses the exact-plan reader with `require_authorized=False` solely to reconstruct
and verify the immutable plan hash, then checks sealed authorization identity;
existing separate controls still verify actual execution authority. It never
reads an arbitrary current-working-directory regime path or submits orders.
The CLI `scripts/verify_execution_target_replay.py` writes an exclusive diagnostic
receipt outside the immutable input root. Missing original inputs remain an
explicit blocker; synthetic fixtures are never historical observations.

The current configured-risk-budget producer rejects empty invested targets;
this verifier does not introduce a new all-cash producer policy. A zero-order
execution of an unchanged invested portfolio is different: explicit empty
intended/submitted orders and posting activities can prove numeric provenance
when original and final holdings/cash are unchanged and economically reconciled.
Missing order/activity fields or unexplained cash/quantity changes fail closed.

September 11 is NOT automatically Day 1: recovery success is insufficient to
establish every control. Historical evidence and all failed attempts remain.

## Paper/Live parity

`core/paper_live_parity.py` compares every required field, rejects missing/invalid
values and dates, and produces EXPECTED/EXPLAINED/UNEXPLAINED rows. NAV, cash and
existing positions may differ. Other differences require a dated reason bound to
both field values and a hashed source document. Risk limits are not unrestricted
expected differences. UNEXPLAINED must be zero to report alignment.

Daily normalized intent inputs may be supplied under
`outputs/paper_live_parity/<date>/{paper_intent,live_intent,explanations}.json`;
otherwise the reader uses current PAPER execution and Lyra state artifacts.
Different models and schedules are not automatically declared equivalent.
Same-day Live plans are absent while Lyra is paused. The local PAPER and Lyra submission wrappers now recompute parity before
submission for sessions from September 11 onward and bind the actual plan hash.
Missing normalized pretrade inputs retain failed parity. Under the conflict
policy in `WORKFLOW_AUTHORITY_REGISTRY.md`, paused Live cannot independently
veto an otherwise authorized PAPER lane. The PAPER wrapper supplies its actual
already-validated exact plan; the dependency guard revalidates its hash, date,
PAPER scope and authorization, checks the canonical operating-lane registry,
and directly rereads only `CAERUS_LYRA_LIVE_ENABLED` from the canonical runtime
file `~/.caerus/lyra_live.env`. Only an explicit current value of `0` permits
the narrow PAPER dependency exception. Missing or enabled state, mismatched
registry, invalid plan, or a Live submission receives no exception. Cached
pause reports and normalized intent JSON cannot authorize this exception.

The report then says `NOT_COMPARABLE_LIVE_PAUSED`, preserving every
`UNEXPLAINED` field, `execution_gate_pass=false` and
`production_authority=false`. A separate `submission_dependency_satisfied=true`
records applicability; all existing PAPER risk, identity, cash, equality and
execution checks remain mandatory. Once Live is enabled, strict parity applies
again on the next call. Complete same-intent metadata and governed explanations
remain blockers for claiming parity. This local guard correction is not installed
on the VM and does not change lane authority. Historical sessions retain their
earlier policy.

## Shadow and research

See `shadow_health_report.md`. Every registry-required Shadow sleeve appears,
with missing artifacts/observations explicitly failing. Research-only Phoenix,
Cygnus, Cassiopeia and Argo are listed without inventing Shadow authority or
performance. Missing returns cannot be reconstructed by substituting model NAV
for broker truth. Historical observation gaps remain explicit.

Atlas admits new active research only in Panic/Reversal, Drift/Continuation,
or Macro/Regime Expression, with an economic hypothesis and preserved trial
history. Existing Phoenix, MES and FOMC rejected specifications satisfy current
mandate dispositions; they do not reject every future hypothesis in a mandate.
Research never imports or activates production capital paths. Shadow candidates
are recommendations; all lifecycle promotions still require Brett.

## Daily integration and validation

`python scripts/build_remediation_reports.py --trade-date YYYY-MM-DD` emits
certification, parity and Shadow JSON under `outputs/remediation/<date>/` and
exits nonzero unless all three pass. The existing close-chain source invokes it
unconditionally after NAV escalation, even when history/audit failed. This source
hook is not deployed in this task; no new cron or messaging automation is created.

Required checks: NAV/broker boundary regression, failure/reset certification,
parity schema/evidence validation, Shadow gaps, execution integrity, timeline,
workflow/status, compilation and diff checks. See the task forensic log for
actual commands/results, current VM reports and remaining blockers.
