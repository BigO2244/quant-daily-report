# Alpha Lab Current State

As of 2026-07-24 UTC, the correct classification is:

> Caerus has a plausible weak broad-selection signal and positive short-window
> paper performance. Independent, factor-adjusted, implementation-net alpha is
> unproven. Tight concentration is not supported as an alpha amplifier.

## Evidence we can use

- Broker-truth paper performance was positive over a short 88-day window.
- The intended-versus-realized implementation gap was measurable and modest
  relative to the full return, so execution is not the entire explanation.
- Broad momentum ranking showed a weak positive cross-sectional effect.
- The target book carried elevated market and momentum exposure.
- Fine ranking inside the selected book was not demonstrated.
- Legacy concentration evidence weakened materially after survivorship and
  point-in-time corrections.
- Live history is an evidence-collection sample, not an alpha validation sample.

Alpha Lab v1/v2 are retained as historical lineage. Their static-universe,
randomized-window, Sharpe-led methodology is not the validation foundation for
this project, and their prior `PASS` labels carry no standing here.

## Current labels

| Component | Current label | Reason |
|---|---|---|
| Broad momentum selection | `UNPROVEN` | Weak broad IC; factor-adjusted net edge not established |
| Tight concentration | `REJECTED_AS_ALPHA_AMPLIFIER` | Fine-ranking evidence absent; mainly raises idiosyncratic variance |
| Existing paper book | `PROMISING_FACTOR_OR_RETURN_ENGINE` | Positive short window; attribution and power remain insufficient |
| Capped live pilot | `FORWARD_EVIDENCE_LANE` | Broker and implementation learning only; no scaling inference |

## Experiment 0001

The first factory experiment should evaluate the frozen 2026-07-14 Caerus
model and answer:

> How much realized return is attributable to market beta, known factors,
> broad security selection, concentration, portfolio construction, and
> implementation?

No later experiment may rewrite this result. Improvements become a new
hypothesis ID.

## Four-lane factory status

On 2026-07-14, Brett froze and authorized experiments for earnings revisions,
insider-conviction clusters, options-information lead, and supply-chain shock
diffusion. Their first deterministic data/provenance gates completed in
`BLOCKED_DATA`; no forward returns or locked holdout observations were read.

All four therefore remain `UNPROVEN`. Earnings revisions, historical options,
and historical supply-chain relationships still require licensed inputs under
their frozen contracts. The shared identity, survivorship-free security-
existence universe, CIK mapping, factor, sector, commodity, SEC Company Facts,
and SEC quarterly insider-discovery assets are now locally materialized. The
full Form 4/4-A original-submission stream is finalized with zero capture
errors. Its 316,822 discovery rows resolve to 155,253 distinct source payloads
and 155,245 unique accessions; eight accessions have distinct historical
payloads and are retained separately. The first 500-original audit passed exact
acceptance/XML/issuer parsing but failed the frozen 99% field-reconciliation
threshold against the lossy quarterly extract. The quarterly extract is
therefore discovery-only; full-history events must be materialized directly
from original ownership XML. No return test may treat the quarterly flat file's
inferred availability time as authoritative. See
`reports/DATA_READINESS_2026-07-14.md` and the experiment Alpha Cards for the
exact acquisition boundary. The procurement sequence, current costs, and
official provider links are in
`reports/DATA_SHOPPING_LIST_2026-07-15.md`.

## Forward options proxy infrastructure

On 2026-07-15, Brett directed Alpha Lab to defer the `$1,410` Cboe pilot and
proxy as much as possible with free current-chain data. The resulting
`options_proxy/` package is a standalone-automated, forward-only, non-executing observation
lane linked to HYP-2026-004.

It writes immutable snapshots, proxy features, hypothetical research targets,
separate five-day return evaluations, scoreboards, and an AST-based production
boundary attestation. It has no broker, allocation, execution, production
scheduler, cron, paper, live, or strategy-registry integration. Its evidence is explicitly
`PROXY_FORWARD_OBSERVATION_ONLY` and cannot satisfy or rewrite the frozen
trade-level HYP-2026-004 data contract.

The forward lane has one ineligible first observation and five eligible daily
cohorts from 2026-07-16 through 2026-07-22, each with five hypothetical targets.
As of 2026-07-22 all five remain correctly labeled
`WAITING_FOR_HOLDING_WINDOW`; the first cohort reaches its five-later-session
maturity on 2026-07-23. Zero completed evaluations before that date is expected
and is not a collection failure.

## Shared point-in-time data spine

On 2026-07-15, Alpha Lab added a research-only shared data spine for Sharadar,
SEC EDGAR, Kenneth French/AQR factors, FRED/ALFRED, EIA, OCC, and vendor trial
samples. Free factor, macro, natural-gas, petroleum, SEC Company Facts, SEC
filing indexes, and quarterly insider archives were captured with immutable
manifests. The rotated Sharadar credential is stored only in the ignored root
`.env`, which the research CLI loads automatically; TICKERS, ACTIONS, and the
21.8-million-row survivorship-aware SEP capture are complete. Stronger
entitlement probes found that DAILY and SF1 are sample-only under the current
plan and therefore are not valid historical research inputs. See
`reports/DATA_SPINE_BUILD_2026-07-15.md`.

The completed SEP compilation produced 21,840,452 causal security-day rows and
the same number of filing-time characteristic rows across 12,554 histories.
The post-build frozen gates read no returns or holdout data. Shared identity,
membership, CIK, factors, sectors, commodities, and characteristics now pass;
all four lanes remain `BLOCKED_DATA` on the explicit original-lineage, true-
delisting, earnings-event, or licensed alternative-data assets recorded in the
build report.

Later on 2026-07-15, the free SEC bulk submissions archive was captured as an
immutable 1.55 GB research bundle. After correcting the distinction between an
issuer announcement and SEC acceptance, the frozen-window Item 2.02 tape has
257,996 rows from 2011-01-03 through 2026-06-30. SEC acceptance is retained as
exact filing availability; issuer announcement time remains unknown, and raw
8-K/exhibit hashes remain blocked until original submissions are hydrated.

The original Form 4 stream is finalized at 316,822 of 316,822 discovery rows
with exact acceptance-time inventory and zero capture errors. Global payload
deduplication yields 155,253 distinct source documents and a canonical tape of
310,836 unique transaction events, including 259,532 eligible open-market
purchase rows. Source presence, ownership-XML parsing, and acceptance-time
coverage are 100%; effective-dated security identity maps 99.9717%. The tape is
usable for pilot backtests, but 3,630 Form 4/A documents keep promotion-quality
claims blocked until amendment supersession is reconciled. The canonical tape
is an original-XML materialization, not a reconciliation toward the lossy
quarterly tables. A separate combined index has 313,450 original 8-K/8-K-A
candidates spanning earnings releases and near-delisting corporate-action
evidence. The latter includes 55,454 unique 8-K filing candidates linked to
8,196 mapped actions, but it does not certify terminal settlement value until
original exhibits are parsed.

The 2026-07-22 frozen four-lane data-gate run still returned `BLOCKED_DATA` for
all four hypotheses, but no longer for a generic absence of collected data.
The common price panel still lacks independently verified delisting/terminal
settlement returns. Earnings revisions additionally lack licensed analyst-level
estimate history and original 8-K announcement/exhibit semantics. Insider
clusters now pass the effective-dated CIK input and use the canonical original-
XML tape; their remaining lane-specific blocker is Form 4/A supersession.
Options still lack the frozen exchange-grade trade/NBBO/OCC tape, while the free
forward proxy accumulates separately. Supply-chain diffusion still lacks the
licensed analyst history and effective-dated universal customer/supplier graph.

The free EIA electricity bulk archive (290,265,140 bytes) and eight explicitly
current-vintage electricity controls are captured. BEA's public industry/code
concordance and API guide are also retained. A no-key USAspending collector is
checkpointing exact-normalized legal-name federal contract awards as a
government-customer subgraph; it is not mislabeled as a universal supplier
graph. An initial exact-name trial exposed multiple USAspending recipient
identities behind some normalized names; those trial edges were rejected. The
current collector requires a unique exact recipient search term and records
ambiguous names instead of assigning their awards to an issuer. Its first v3
partition retained 985 award edges for two unique issuer matches, rejected one
ambiguous name, and had zero errors or truncations. Free Alpha
Vantage and BEA API adapters are ready but require their free
registration keys. A first no-key yfinance analyst snapshot captured 199 usable
current aggregate EPS/revenue estimate, trend, and revision records across the
200-name research universe; it is forward-proxy evidence only and has no
analyst/broker identity or historical PIT lineage. No returns or holdout data
were read. A separate Codex automation, `Alpha Lab analyst proxy daily`, runs
this forward snapshot at 18:10 ET on weekdays; it has no production scheduler
or trading integration. The exact captured/in-progress/irreducible split is in
`reports/FREE_DATA_COVERAGE_2026-07-15.md`.

## Natural next research families

1. Point-in-time earnings revisions and post-earnings drift.
2. Event catalysts such as activist 13D filings, insider purchases, and
   repurchases.
3. Point-in-time quality/value interactions.
4. Cross-asset trend, initially classified as a potential diversifier.
5. Short-horizon reversal or liquidity provision only if TCA and capacity make
   the mechanism implementable.

## Discovery-to-decision control plane

On 2026-07-20, Alpha Lab added a research-only control plane that can evaluate
heterogeneous frozen techniques, route licensed-data needs to Brett, assess
research and Shadow gates, and build a deterministic CIO review queue. It
supports `DATA_ACCESS_REVIEW`, `RESEARCH_DECISION_REVIEW`,
`SHADOW_ACTIVATION_REVIEW`, `SHADOW_CHECKPOINT_REVIEW`, and
`PAPER_PROMOTION_REVIEW` items.

The mechanism is nominative, not authoritative: it performs no purchase,
registry edit, Shadow activation, Paper promotion, broker action, allocation,
or production scheduling. Candidate snapshots are immutable evidence
compilations rather than a parallel strategy registry. All persisted queue and
evaluator bundles are constrained to the authoritative GCP Alpha Lab data root.
The separate Codex automation `Alpha Lab CIO review queue` checks for new or
materially changed owner decisions at 18:45 ET on weekdays and remains quiet
when the decision fingerprint is unchanged. See `CONTROL_PLANE.md`.

## Constraint remediation and eight-family freeze — 2026-07-23

Brett approved prioritizing a 12-family research workload and then explicitly
used `FREEZE HYPOTHESIS` and `RUN EXPERIMENT` for the eight previously unfrozen
families: current Caerus decomposition plus residual momentum, stock-specific
seasonality, short-horizon reversal, cross-asset trend, executive tone
surprise, net payout/share issuance, and asset growth/investment. They are
HYP-2026-001 and HYP-2026-006 through HYP-2026-012. Every family has a hashed
preregistration, bounded evaluator spec, locked 2025-01-01 through 2026-06-30
challenge window, and secondary-only regime diagnostics.

The price compiler no longer mislabels the provider's final observed daily
return as a delisting or terminal settlement return. The v3 panel retains that
observation separately and leaves settlement fields null. A new immutable
terminal-return sensitivity command produces pessimistic-total-loss and
zero-incremental scenarios without representing either as verified truth. This
supports honest robustness work for future price-derived hypotheses but does
not satisfy the frozen four-lane exact terminal-return gate.

The original Form 4 materializer now resolves amendment ambiguity by
fail-closed issuer exclusion instead of guessed supersession. The remediation
was deployed to the GCP Alpha Lab checkout and the full-history tape was rebuilt
as immutable bundle
`20260723T183854Z-f30d85ba2232`. It excludes all events for the 1,733 issuers
with a captured Form 4/A and retains 137,522 event rows, including 113,248
eligible open-market purchases. The provider certification is `READY`,
historical point-in-time verification is true, and original ownership XML with
exact EDGAR acceptance remains canonical. The prior tape remains immutable.
HYP-2026-003 remains `UNPROVEN` until its own frozen gate is rerun; rebuilding
the defining input is data remediation, not an insider-alpha result.

The GCP market pair was also rebuilt and atomically published. The observed-
price panel and filing-time characteristic panel each contain 21,840,452 rows
and 12,554 security histories from 2011-01-03 through 2026-06-30. Their
respective SHA-256 hashes are
`7b6518bc30d84820b5113465fb23d54de36012195ed1672ed19aca9e216c99c0`
and
`4d93e2865c760dc3908b308640d862f89ab98c8d2440d9cadbe1e193786d7287`.
Observed prices and characteristics are PIT-certified. The exact historical
settlement contract remains correctly blocked; the separate terminal-return
sensitivity bundle `20260724T051532Z-b9720e1e9675` reports both frozen
pessimistic-total-loss and zero-incremental scenarios without claiming a
verified settlement value.

The apparent 2026-07-23 Shadow `NO_DATA` state was an expected premarket
artifact, not a failed week-long collection. The 18:30 ET post-close hydrator
completed successfully through 2026-07-22 and advanced the canonical Shadow NAV
series to 49 rows. No Shadow runtime, cron, strategy, allocation, broker, paper,
or live behavior was changed as part of this research remediation.

## Eight-family frozen run outcome — 2026-07-24 UTC

All eight authorized families passed through one deterministic data gate after
the market rebuild. Three had complete frozen inputs and ran through DISCOVERY
plus the locked 2019-2024 validation period. Five stopped before any return
join. The untouched 2025-01-01 through 2026-06-30 challenge period was not
accessed by any family.

| Experiment | Family | Outcome | Frozen primary result or blocker |
|---|---|---|---|
| EXP-2026-0001 | Current Caerus decomposition | `ITERATE — BLOCKED_DATA` | Missing `caerus_research_decision_tape_v1` |
| EXP-2026-0006 | Residual momentum | `PARK — UNPROVEN` | -6.56% worst-case validation annualized excess |
| EXP-2026-0007 | Stock-specific seasonality | `PARK — UNPROVEN` | -7.29% worst-case validation annualized excess |
| EXP-2026-0008 | Short-horizon reversal | `PARK — UNPROVEN` | -44.56% worst-case validation annualized excess |
| EXP-2026-0009 | Cross-asset trend | `ITERATE — BLOCKED_DATA` | Missing `cross_asset_price_panel_v1` |
| EXP-2026-0010 | Executive tone surprise | `ITERATE — BLOCKED_DATA` | Blocked PIT earnings-event semantics and transcript history |
| EXP-2026-0011 | Net payout/share issuance | `ITERATE — BLOCKED_DATA` | Missing `pit_net_payout_features_v1` |
| EXP-2026-0012 | Asset growth/investment | `ITERATE — BLOCKED_DATA` | Missing `pit_asset_growth_features_v1` |

Capacity supported $1 million for all three evaluated primary variants, so
capacity was not the reason they failed. Every evaluated primary was negative
under the frozen cost/terminal envelope, and the corrected family-significance
gate remains unimplemented. Regime diagnostics were secondary only: they made
no alpha, allocation, activation, or promotion claim. All eight classifications
remain `UNPROVEN`; the three `PARK` verdicts apply frozen experiment criteria
and do not retire or reweight a production strategy.
