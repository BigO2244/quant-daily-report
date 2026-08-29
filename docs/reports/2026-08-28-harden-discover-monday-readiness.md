# HARDEN + DISCOVER — Monday Readiness

Date: 2026-08-28  
Owner: Atlas / COO  
Scope: read-only production inspection plus isolated code and replay work; no
production deployment, capital change, or strategy promotion was performed.

## Executive verdict

Monday is **YELLOW / ready to trade within existing lane authority**. Lyra Live
and Orion PAPER remain active; Shadow remains non-capital. The retrospective
20-session Trading Integrity Rate is **0/20 (0%)** because the evidence standard
is new and the most recent session proves five of six controls. That assurance
baseline is not itself a trading halt or a change in capital authority.

The remaining current-session exception is universe pedigree, explicitly
labeled `NON_DECISION_GRADE_UNIVERSE`. It blocks a fully certified rating and
blocks scaling or promotion claims. It does not show stale prices, a stale
decision, artifact divergence, broker mismatch, or unsafe order lineage. Paper
continues under its existing fail-closed execution controls while the PIT
universe work is completed.

The repeated Orion targets were investigated. The target did not remain static
because computation froze: market, feature, and rank hashes changed daily.
Orion's rank-decay rule legitimately retained LRCX while MU, WDC, STX, and INTC
remained highly ranked. The recent loss is primarily an alpha and portfolio
construction problem, not an execution contamination problem.

## Trading integrity baseline

Window: 2026-08-03 through 2026-08-28, twenty trading sessions. `P`, `F`, and
`U` mean proved pass, proved fail, and unproved respectively.

| Session | Data/PIT | Compute | Decision | Artifact | Execution | Reconciliation |
|---|---:|---:|---:|---:|---:|---:|
| 08-03 | U | U | U | U | U | U |
| 08-04 | U | U | U | U | U | U |
| 08-05 | U | U | U | U | U | U |
| 08-06 | U | U | U | U | U | U |
| 08-07 | U | U | U | F | F | F |
| 08-10 | U | U | U | U | U | F |
| 08-11 | U | U | U | U | U | F |
| 08-12 | U | U | U | U | U | P |
| 08-13 | U | U | U | F | F | F |
| 08-14 | U | U | U | U | P | P |
| 08-17 | U | U | P | P | P | P |
| 08-18 | U | U | P | P | P | P |
| 08-19 | U | U | P | P | P | F |
| 08-20 | U | U | P | P | P | F |
| 08-21 | U | U | P | P | P | P |
| 08-24 | U | U | P | P | P | P |
| 08-25 | U | U | F | F | F | F |
| 08-26 | U | U | P | P | P | P |
| 08-27 | U | U | P | P | P | P |
| 08-28 | U | P | P | P | P | P |

The automated certifier is intentionally stricter than this forensic matrix
when persisted proof is absent. It records 26/120 proved control observations:
Data 0, Compute 1, Decision 1, Artifact 9, Execution 7, Reconciliation 8. A
session is certified only when all six controls pass.

## Root causes and repairs

1. **Mutable source references.** Dated Orion shadow publications could be
   rebuilt after morning precompute, so later source hashes no longer matched
   the original session manifests. Precompute bundles themselves remained
   intact. The builder now copies each accepted sleeve source and Orion prior
   state into its precompute bundle, hashes those sealed bytes, and binds the
   package and execution plan to those immutable copies. Mutation or tampering
   tests fail closed.
2. **End-of-day same-as-of failure.** Alpaca portfolio history lagged the causal
   account/positions pull by one completed session. Nightly reporting therefore
   failed every run from 2026-08-14 and canonical NAV froze at 2026-08-13. The
   reporting builder now materializes the current reconciled causal valuation
   as the missing current-session row without rewriting the append-only broker
   history. A replay against copied production inputs closed 2026-08-27 at
   $10,626.11 with exact `2026-08-27T23:15:03Z` alignment and no warnings when
   built from broker authority.
3. **Historical NAV disagreement.** Thirty-eight older immutable canonical NAV
   rows differ from a current broker-history reconstruction by more than one
   basis point. They remain untouched and must go through the explicit
   restatement procedure. Until resolved, the existing reporting surface is
   degraded even though the current-row code defect is fixed.
4. **Provider/freshness incident.** On 2026-08-25, empty yfinance responses left
   the price cache at 2026-08-21. Precompute and self-heal failed closed. This is
   a clean safety outcome but still lacks a robust provider catch-up path.
5. **Execution/reconciliation incidents.** The 2026-08-19 and 2026-08-20
   no-order/no-trade handling caused reconciliation failures and was repaired
   in the existing code. The 2026-08-17 launch required a manual correction
   after planning from a synthetic $10,000 basis rather than broker NAV of
   $11,822.55.

## Orion, Lyra, and Polaris autopsy

All three sleeves are transformations of the same primitive:
`0.5 * return_12_1 + 0.3 * return_6_1 + 0.2 * return_3`, followed by a daily
cross-sectional rank. Polaris selects the top ten daily at equal weight. Orion
selects the top five but retains incumbents through rank ten, equal weighted;
PAPER applies 19% each plus 5% cash. Lyra selects the top five weekly at equal
weight.

| Evidence window | Polaris | Orion | Lyra | SPY |
|---|---:|---:|---:|---:|
| Historical 2014-2024 CAGR | 30.68% | 40.74% | 40.74% | n/a |
| Historical Sharpe | 0.851 | 0.933 | 0.919 | n/a |
| Historical max drawdown | -54.43% | -57.95% | -64.19% | n/a |
| Current 2026-05-12 to 08-27 | 4.21% | 3.69% | 9.64% | 4.46% |
| Current 3M | -0.79% | -6.40% | 1.75% | 1.93% |
| Current 1M | 5.19% | 0.48% | 2.79% | 3.97% |
| Latest 10 sessions | -3.79% | -5.92% | -5.71% | -0.86% |

Current maximum drawdowns are -32.93%, -36.85%, and -30.51% versus -4.50% for
SPY. Pairwise sleeve return correlations are 0.978-0.987. These are not three
independent sources of edge.

The 2026-08-17 through 08-26 PAPER return was -10.995% versus -11.641% for the
matched Orion shadow target; execution improved the result by 0.646 percentage
points. That strongly rejects execution contamination as the primary cause of
the drawdown.

### Diagnosis and falsifiers

- **Portfolio construction: high support.** Extreme concentration, equal
  weighting, shared factor exposure, and rank-decay retention amplify losses.
  Falsified if a primitive, unconcentrated signal portfolio exhibits the same
  deterioration after costs across industries and breadth buckets.
- **Edge decay: moderate support, unproved.** The 3M and latest-ten-session
  evidence is weak, but 1M and Lyra evidence is mixed and the live sample is
  short. Falsified by sustained out-of-sample recovery in raw rank spreads with
  breadth and capacity intact.
- **Regime mismatch: weak-to-moderate support.** Recent losses are compatible
  with a hostile cross-sectional environment, but runtime labels the regime
  `risk_on_trending`. Falsified if rank-spread losses remain after conditioning
  on reversal intensity, dispersion, volatility, and breadth.
- **System contamination: low support for recent loss.** Daily input hashes
  changed, selections are reproducible, and executed return tracked intended
  return. This conclusion is falsified by a replay mismatch or a causal data
  defect. Historical/runtime comparability remains limited because the
  historical universe is roughly 1,600 names and runtime uses roughly 200.

Decision: keep all three in their current lifecycle states for observation; do
not promote, retire, or allocate more capital based on this evidence.

## Orthogonal Alpha queue

Use a rolling twenty-family budget: momentum/trend 4, panic/reversal 4,
event-driven 4, behavioral/attention 3, cross-sectional/relative value 3, and
weird/experimental 2. Variants do not count as independent hypotheses.

Priority order:

1. Industry earnings-information diffusion after a reporter's large abnormal-
   volume reaction; test not-yet-reporting same-industry peers for 5/20-day
   drift against industry controls.
2. Insider-conviction clusters using the rebuilt original Form 4 XML tape.
3. Forced-deleveraging panic reversal, conditioned on volume/liquidity stress.
4. Under-attended filing drift using causal filing timestamps and attention.
5. Net payout yield and asset growth as separate cross-sectional primitives.
6. 13D initiation drift; secondary-offering reversal; options-demand proxy;
   index-deletion pressure; procurement-intent drift; 13F crowded unwind.

### Weekend primitive-gate results

The frozen HYP-2026-003 insider-conviction data gate was rerun in an isolated
temporary mirror. It returned `BLOCKED_DATA` without accessing returns or
holdouts and without attempting a return variant. PIT security master,
membership, characteristics, factor, sector-return, CIK identity, and the
rebuilt original-XML Form 4 tape all passed. The only failed provider was PIT
prices/liquidity because exact historical delisting settlement remains
unverified. HYP-3 is not ready for an evaluator or Shadow. Replacing the frozen
requirement with observed-price and sensitivity scenarios would be a new child
contract requiring an explicit refreeze decision.

The earnings-information-diffusion readiness inspection found 313,449 of
313,450 original SEC filings hydrated with exact acceptance timestamps and
preserved submissions. The existing earnings tape includes Item 2.02 and
conservative availability timestamps, but lacks source hashes; the causal
characteristics panel has only eleven broad sectors, not a finalized PIT
industry-peer history. No prices, returns, challenge observations, or holdouts
were read. The mechanism is ready for a discussion-stage Candidate Packet, not
FREEZE/RUN. Before freeze it needs a hash-bound Item 2.02 packet, PIT issuer-
industry history with peer-coverage proof, and a rule that measures shock only
after certified SEC availability and trades after that reaction.

Generic residual momentum (-6.56%), seasonality (-7.29%), and the tested generic
reversal construction (-44.56% across variants) are killed as currently
specified. The regulatory lead-lag and government-contract variants are parked
for inadequate comparable samples, not counted as negative alpha evidence.

Shadow admission should require causal inputs, a leakage audit, a specific
economic mechanism, and either positive primitive economics or a high-
information falsification rationale. It should not require family-wide
multiple-testing correction, 60-day proof, or promotion-grade stability.
Shadow remains explicitly `UNPROVEN` with 20- and 60-session checkpoints. Paper
and Live retain progressively higher evidence, control, and capital gates.

## Current grades

| Dimension | Grade | Evidence |
|---|---:|---|
| Trading Integrity | D | 0/20 sessions fully certified; latest is 5/6 |
| Operational Control | C+ | Canonical lane truth is deployed and fail-closed lineage exists; PIT universe, historical NAV restatements, and hardening deployment remain open |
| Alpha Quality | C- | Three sleeves are one highly correlated momentum family with severe drawdowns |
| Research Throughput | D+ | No canonical research-event ledger, zero challenge reads, and most gates blocked; several hypotheses were nevertheless resolved honestly |

## Validation

- 60 focused tests passed across source sealing, execution lineage, Orion
  freshness, six-control certification, portfolio reporting, and daily audit.
- The 2026-08-27 end-of-day repair replay passed against an isolated copy of
  production broker inputs with exact timestamp, $10,626.11 equity, and no
  warnings when rebuilt from broker authority.
- The repository-wide suite produced 3,444 passes, 12 skips, and 236 failures.
  Failures cluster in pre-existing execution/regime tests whose fixtures are
  fixed to earlier August 2026 sessions and are rejected as stale by current
  date authorization. None occurred in the changed modules. The full suite is
  therefore not represented as clean; date-independent fixture repair remains
  validation-harness debt.

## CIO brief for Monday

**OPERATIONS: YELLOW.** Trading Integrity Rate 0/20 is a retrospective evidence
baseline, not a halt. No current transaction-lineage or broker-reconciliation
failure is known. Exceptions: non-PIT decision-universe pedigree; 38 unresolved
historical NAV restatements; hardening commit not deployed.

**CAPITAL.** Lyra Live is active, funded, and recurring. Orion PAPER is active
and should continue trading under its existing authority and execution gates.
Shadow remains non-capital; no promotion was made. The disabled legacy FR-104
Live pilot is a separate lane and does not negate Lyra Live.

**ALPHA.** Best relative evidence: Lyra, but not independent of Orion/Polaris.
Worst deterioration: Orion, -6.40% over 3M and -5.92% over the latest ten
sessions. New Shadow: none pending causal gates. Killed: generic residual
momentum, seasonality, and the tested generic reversal construction.

**Atlas challenge to the CIO.** The intuition that the same target implies a
stale system is contradicted by daily hash and rank evidence. The more important
problem is that Caerus has treated three portfolio wrappers around one primitive
as alpha diversification. The operating response is not another momentum
variant; it is source-of-edge diversification plus narrower, explicit exposure
control.

## CIO decisions — maximum three

1. No capital decision is required for Monday: keep Lyra Live and Orion PAPER
   operating under their current independent authorities; do not scale either
   while the certification window is incomplete.
2. Authorize the scoped hardening commit for normal deployment review and the
   audited historical NAV restatement procedure. No direct production mutation
   should occur outside that path.
3. Approve the permissive Shadow admission rule and authorize an earnings-
   diffusion Candidate Packet plus the three missing causal data products. Do
   not issue FREEZE/RUN yet. Separately decide whether HYP-3 keeps its exact
   delisting-settlement contract or is refrozen as a new sensitivity-based
   child hypothesis.
