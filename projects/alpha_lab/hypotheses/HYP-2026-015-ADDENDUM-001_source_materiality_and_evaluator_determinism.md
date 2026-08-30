# HYP-2026-015 Addendum 001 — Source Materiality and Evaluator Determinism

State: `OWNER_APPROVED_FROZEN_ADDENDUM`

Addendum: `HYP-2026-015-ADDENDUM-001`

Hypothesis: `HYP-2026-015`

Experiment: `EXP-2026-0015`

Classification: `RESEARCH_ONLY_NON_EXECUTIONAL`

## Authority, binding, and scope

Brett Olson approved replacing the original absolute source-universe
completeness gate with the materiality framework below and authorized the
bounded rerun and discovery/validation experiment. This addendum binds, but
does not rewrite, the original frozen specification:

- original specification:
  `hypotheses/HYP-2026-015_industry_earnings_information_diffusion.md`;
- original specification SHA-256:
  `3ca51f2f477c548d0b9ad266f004b4f61ba532f1d23961847c05db1e5fd033d6`;
- first no-return gate evidence: `evidence/EXP-2026-0015.md`;
- first no-return gate result SHA-256:
  `451c719893b868856c73d4ee75110707dd971a9b11a35f0e29eabfb851a73178`.

The original economic mechanism, `PRIMARY_V1` signal thresholds, universe,
holding period, benchmark, validation window, primary metric, trial budget,
Holm/BY methods, pass and kill hurdles, and sealed challenge boundary remain
unchanged. This addendum supersedes only:

1. the absolute requirement that every accession in the source universe be
   hydrated before the experiment may continue;
2. the undefined handling of multiple qualifying same-SIC reporters on one
   reaction session; and
3. the undefined normalized event-capital, transaction-cost, capacity, and
   cash arithmetic.

The first gate disclosed one missing original before any reporter reaction,
forward return, validation outcome, statistical trial, or challenge input was
read. This addendum is therefore a disclosed post-data-gate governance change,
not a result-informed signal or portfolio revision. It consumes no statistical
trial and creates no new threshold, horizon, industry definition, or outcome-
conditioned exclusion.

## Frozen source-materiality framework

### Denominators fixed before outcomes

The rerun must materialize and hash the following structural denominators
before reading any reporter reaction, forward return, or challenge outcome:

- `source_candidate_count`: unique candidate original-filing records in the
  bound source manifest;
- `source_hydrated_count`: those candidates with immutable original bytes,
  payload SHA-256, and exact SEC acceptance timestamp;
- `reporter_candidate_count`: unique Item 2.02 reporter accessions in the
  structurally eligible discovery/validation event inventory;
- `reporter_source_ready_count`: those reporter candidates with immutable
  original bytes, exact acceptance time, one CIK, one four-digit SIC, and one
  unique effective-dated reporter security identity;
- `potential_peer_observation_count`: structurally eligible event-by-security
  peer observations before applying the `+5.00%` reporter-return or `2.00x`
  abnormal-volume signal;
- `mapped_potential_peer_observation_count`: those potential peer observations
  with one causal effective-dated identity and event-time SIC; and
- the equivalent potential-control counts for the primary two-digit-SIC
  industry baseline.

The structural inventory may inspect source, identity, membership, calendar,
and row-availability metadata. It may not inspect reporter reaction values,
peer/control forward returns, validation results, or challenge outcomes.

### Coverage gates

The no-return rerun passes source materiality only when all of these are true:

1. `source_hydrated_count / source_candidate_count >= 0.999`;
2. `reporter_source_ready_count / reporter_candidate_count >= 0.999`;
3. `mapped_potential_peer_observation_count /
   potential_peer_observation_count >= 0.99`;
4. the equivalent potential-control mapping coverage is at least `0.99`;
5. every included reporter, peer, and control observation has `100%` causal
   source, payload-hash, acceptance-time, CIK/SIC, identity, membership, price-
   path, corporate-action, and terminal-disposition lineage required by its
   role; and
6. all missingness, concentration, and selection-relation controls below pass.

These are aggregate materiality gates. A deterministically excluded ambiguous
reporter or peer does not reintroduce an implicit `100%` source-universe or
mapping requirement. The `100%` rule applies to every observation actually
included in an event, candidate basket, or comparator.

### Deterministic exclusion before outcomes

Before any market outcome join, sort missing or ambiguous source records by
accession and exclude:

- a candidate reporter accession lacking original bytes, payload hash, exact
  acceptance time, unique CIK, four-digit SIC, or unique reporter mapping;
- every potential peer or control observation whose causal event-time identity
  or latest required SEC-header source cannot be proven; and
- any observation with an incomplete reaction/holding path, unresolved
  corporate action, or unresolved terminal disposition.

The exclusion table is immutable and must record accession or structural
observation ID, issuer CIK, calendar year, four-digit SIC when causally known,
role, failure reason, source path, source status, and the timestamp at which the
exclusion was sealed. Unknown SIC remains `UNKNOWN`; it is never inferred from
current-vintage data. Excluded rows may not be replaced, backfilled from a
different issuer, or reintroduced after outcomes are visible.

### Missingness and concentration diagnostics

The gate must report denominator, missing count, coverage, and missing share by
calendar year, four-digit SIC, issuer CIK, and experiment relevance
(`ITEM_2_02_REPORTER`, `POTENTIAL_PEER`, `PRIMARY_CONTROL`). It fails when any
of the following prespecified conditions holds:

- a calendar-year or four-digit-SIC stratum with at least `100` structural
  observations has coverage below `99.0%`;
- an issuer with at least `20` structural observations has coverage below
  `95.0%`;
- when there are at least `10` missing structural observations, one calendar
  year or SIC contains at least `50%` of all missing observations and at least
  twice its share of the corresponding denominator;
- when there are at least `10` missing structural observations, one issuer
  contains at least `25%` of all missing observations and at least three
  missing observations; or
- any selection-relation flag below is true.

With fewer than ten missing observations, share-of-missing concentration is
reported as `LOW_COUNT_NOT_SEPARATELY_TESTABLE`; the aggregate, material-
stratum, included-row, and selection-relation gates still apply. This low-count
rule prevents one pre-outcome exclusion from becoming a disguised absolute
completeness requirement.

### Plausibly selection-related missingness

The gate fails `SELECTION_RELATED_MISSINGNESS` if any exclusion or recovery
decision:

- was made after reporter reaction, peer/control forward return, validation,
  or challenge data was accessed;
- uses price change, abnormal volume, forward return, hit rate, factor result,
  or any other outcome-bearing value;
- depends on whether the missing row would help or hurt the hypothesis;
- arises from parsing a signal-defining source field only for a nonrandom
  issuer, SIC, year, or form stratum whose coverage also fails a frozen
  concentration gate; or
- uses a replacement source or current-vintage identity/SIC rule unavailable
  to fully observed rows under the same causal contract.

Acquisition absence recorded by the bound source manifest, sealed before
outcomes and handled identically for all accessions, is not by itself evidence
of selection-related missingness. Its year/SIC/issuer distribution and source
error class must nevertheless be reported.

### Frozen adverse missingness sensitivity

The evaluator must run a separate adverse sensitivity after calculating the
included validation clusters. Every deterministically excluded reporter or
structurally affected peer/control set that could have produced a validation
cluster is mapped to one unique potential-cluster key. For each such key, add
one synthetic cluster whose base-cost and stress-cost incremental return equal
the minimum corresponding net incremental return among included validation
clusters. Multiple exclusions sharing one potential-cluster key are counted
once.

The sensitivity is `NOT_EVALUABLE` if no included validation cluster exists.
Otherwise it must recompute the primary mean, one-sided confidence bound,
effective sample size, Holm result, and economic/stress gates after adding the
synthetic clusters. A positive classification is prohibited unless the
included-only result and this frozen adverse sensitivity both pass every
original validation gate. The evaluator must also report the break-even mean
return that the excluded potential clusters would need to overturn the
`+0.50%` hurdle. This is a conservative worst-observed sensitivity, not a claim
of a mathematical lower bound on equity active returns.

## Structural floors versus realized signal floors

The no-return gate's `150` cluster, `100` peer, and `20` four-digit-SIC counts
are structural pre-signal eligibility potential. A potential cluster is a
causally sourced reporter-session/SIC cluster with sufficient structurally
eligible peers before applying the reporter's `+5.00%` return and `2.00x`
abnormal-volume conditions. Applying those two conditions would access market
outcomes and is forbidden in the no-return gate.

The evaluator must recheck the same floors on actually qualifying 2019-2024
validation clusters after the outcome-bearing signal is lawfully opened. The
hypothesis fails if either the structural gate or the realized qualifying
validation sample has fewer than `150` independent clusters, `100` unique
peers, or `20` four-digit SICs. Structural potential is not represented as an
achieved validation sample.

## Deterministic multi-reporter cluster rule

1. Evaluate each sourced reporter independently against the frozen `+5.00%`
   return and `2.00x` abnormal-dollar-volume thresholds.
2. For one reaction session and four-digit SIC, combine all reporters that
   independently qualify into exactly one cluster. A nonqualifying reporter is
   not added merely because another reporter in its SIC qualifies.
3. De-duplicate multiple qualifying accessions for the same reporter security
   within that session. Preserve every accession in lineage, but include the
   reporter security once.
4. Define `reporter_set_id` as the SHA-256 of the canonical JSON array of sorted
   unique qualifying reporter security IDs. Define `event_cluster_id` as the
   SHA-256 of reaction session, four-digit SIC, `reporter_set_id`, and the
   sorted qualifying accession set.
5. Exclude every reporter in that set from the peer, industry-control, and raw-
   momentum candidate pools. Form the peer basket once; no reporter adds
   weight, resets a hold, or duplicates the basket.
6. Do not aggregate reporter reactions into a new selection statistic. Every
   included reporter already passed both thresholds. Report the reporter-set
   minimum, mean, and maximum return and abnormal-volume ratio as diagnostics.
7. The reporter-only comparator equally weights the unique qualifying
   reporters in `reporter_set_id` and otherwise uses the same entry, exit,
   capital, capacity, cash, overlap, and cost arithmetic.
8. For the frozen reporter-issuer × SIC × quarter independence unit, the
   reporter component is `reporter_set_id`, the SIC component is the four-digit
   SIC, and the quarter is the quarter containing the reaction session. One
   cluster produces one observation; it is never replicated once per reporter.

## Normalized event capital, cash, capacity, and costs

### Event normalization

- Each event cluster is an independent event-study sleeve with normalized
  capital `1.0`, corresponding to `$1,000,000` for the primary capacity-aware
  calculation.
- Candidate, primary industry, reporter-only, and raw-momentum comparators each
  receive their own normalized `1.0` capital. They are counterfactual sleeves,
  not simultaneous claims on one portfolio.
- Before capacity or overlap constraints, each sleeve equally weights all of
  its frozen constituents and target weights sum to `1.0`.
- Event clusters are not combined into a calendar portfolio in the primary
  test. Portfolio-level concurrency and capital allocation remain future
  portfolio-utility questions.

### Overlap and cash

Process clusters in ascending reaction session, four-digit SIC, and
`event_cluster_id`. Within each candidate or comparator sleeve history, a
security already inside its five-session holding window cannot receive a new
or larger position. Its prespecified equal-weight slot becomes zero-return
cash; the weight is not redistributed. A candidate cluster must still have at
least three newly allocatable peers after this overlap rule or it is not an
actually qualifying cluster. Comparator overlap is tracked independently from
the candidate sleeve and uses the same rule.

### Capacity

For capital level `C` and prespecified target weight `w_i`, target order dollars
are `C * w_i`. The maximum permitted order is `5%` of the security's causal
trailing 20-session median dollar ADV. Evaluate `$100K`, `$1M`, and `$10M`.

- A capital level passes only when every uncapped target order is within the
  `5%` ADV limit.
- For return diagnostics at a failing capital level, cap each affected weight
  at `0.05 * ADV_i / C`; leave the residual as cash; never redistribute it.
- The primary metric uses the `$1M` capacity-aware weights. The original `$1M`
  capacity gate still fails if any target weight requires capping, even if the
  resulting cash-capped return is positive.

### Return and transaction-cost arithmetic

For sleeve `s` with executed entry weights `w_i` and total executed gross
weight `G_s = sum(w_i)`:

```text
gross_return_s = sum(w_i * security_five_session_total_return_i)
net_return_s(c) = gross_return_s - 2 * c * G_s
cash_weight_s = 1 - G_s
primary_incremental_return(c) = net_return_candidate(c)
                                - net_return_industry_control(c)
```

where `c = 0.0015` at base cost and `c = 0.0030` at stress cost. Cash earns
zero. Costs are charged on the fixed initial event capital at entry and exit;
there is no intra-hold rebalancing. When candidate and industry sleeves both
invest fully, identical costs cancel in their difference by construction. That
is the economically neutral consequence of identical turnover and is not a
reason to add an asymmetric penalty. Different invested gross weights retain
their actual different costs.

The raw-momentum comparator selects the same number of names as the candidate
peer basket using the frozen 20-session return, excludes all cluster reporters
and candidate peers, breaks score ties by ascending security ID, and applies
the same arithmetic. A primary industry baseline with no eligible control
cannot be imputed; the cluster fails closed as `MISSING_PRIMARY_BASELINE`.

## Deterministic cluster inference

First group all qualifying event-cluster incremental returns sharing the same
`reporter_set_id`, four-digit SIC, and reaction-session calendar quarter. If a
group contains more than one event cluster, its inference observation is the
equal-weight arithmetic mean of those event-cluster returns. This produces one
observation per frozen reporter-issuer-set × SIC × quarter independence unit.
The primary validation point estimate is the equal-weight arithmetic mean
across those unit observations; event frequency within a unit never gives that
unit extra statistical weight.

For `n` unit observations `x_i`, calculate the sample standard deviation with
`n - 1` degrees of freedom, `SE = sample_sd / sqrt(n)`, and
`t = mean(x_i) / SE`. The expected direction is positive. The raw one-sided
p-value is `1 - StudentT_CDF(t, df=n-1)`. The one-sided 90% lower confidence
bound is:

```text
LCB_90 = mean(x_i) - StudentT_PPF(0.90, df=n-1) * SE
```

If sample standard deviation is exactly zero, use these deterministic limits:
positive mean gives `p=0` and `LCB_90=mean`; zero mean gives `p=0.5` and
`LCB_90=0`; negative mean gives `p=1` and `LCB_90=mean`. The result is not
inference-eligible for `n < 2`, and the original economic gate separately
requires `n >= 150`.

Within-family Holm adjustment receives exactly the one frozen primary trial,
so adjusted and raw p-values coincide. The frozen BY wave currently contains
one member, so its adjusted and raw p-values also coincide; no additional
family may be inserted after outcomes. Passing still requires the primary mean
to be at least `+0.50%`, `LCB_90 > 0`, the one-sided Holm result to reject at
`alpha=0.10`, and every original stress, breadth, concentration, issuer, and
capacity gate to pass.

## Content-addressed pre-outcome registration exception

The owner approved a narrow governance override so the unpublished canonical-
ledger implementation does not block this non-capital discovery/validation
test. After the revised no-return gate passes, but before any reporter reaction
or forward return is read, the GCP run must seal one content-addressed
`preregistration.json` with these fields:

- schema version, UTC creation time, repository commit, and run ID;
- hypothesis, experiment, family, wave, and statistical trial IDs;
- ordered wave membership `[EXP-2026-0015]`, trial ordinal `1`, maximum family
  trial units `1`, selection-trial units `0`, and ordered variant census
  `[PRIMARY_V1]`;
- original hypothesis path/hash and this addendum path/hash;
- evaluator-spec path/hash, evaluator code-manifest path/hash, frozen variant-
  definition hash, and frozen internal-search-census hash;
- ready no-return data-gate path/hash, input data-manifest path/hash, exclusion-
  manifest path/hash, and data snapshot hash;
- primary metric, expected direction, null, `+0.50%` economic hurdle, effective
  sample floors, Holm one-sided `alpha=0.10`, and BY `q=0.10`;
- discovery and validation windows; and
- challenge epoch ID, period, panel hash, and state `SEALED_UNOPENED`, together
  with `outcome_data_accessed=false`, `challenge_accessed=false`,
  `orders_submitted=false`, and `trading_behavior_changed=false`.

`registration_hash` is the SHA-256 of canonical JSON for the complete object
with only `registration_hash` omitted. After adding that hash, write the packet
create-only, hash it into a manifest, and write the manifest last. Neither file
may be replaced. The exact frozen statistical trial ID is
`FAMILY-2026-0015-T001`; later canonical import must preserve that ID, all
content hashes, the one-trial census, and the original event times rather than
allocate a new trial.

The same run directory must contain a create-only hash-chained `events.jsonl`.
Its required ordered events are:

1. `preoutcome_registration_sealed`, referencing `registration_hash` and the
   last-written manifest hash while all outcome flags are false;
2. `outcome_access_started`, appended immediately before the first reporter-
   reaction or forward-return read;
3. `validation_evaluation_completed`, referencing the immutable evaluator
   result hash; and
4. `statistical_trial_closed`, referencing the terminal trial-result hash,
   raw and adjusted p-values, and trial delta `1`.

Each event records event ID/type, occurred and recorded timestamps, payload,
previous-event hash, and its own canonical event hash. The first event uses the
all-zero genesis hash. If a run reaches `outcome_access_started`, the one trial
is consumed even if interrupted; recovery may finalize the same manifested run
idempotently but may not create or reopen another trial.

This fallback state is
`LOCAL_PREREGISTRATION_PENDING_AUTHENTICATED_LEDGER_IMPORT`. Any resulting
positive, negative, or inconclusive validation evidence is
`NON_DECISION_GRADE` until the exact packet and complete event chain are
imported into the canonical ledger and authenticated under its identity and
review separation rules. The fallback preserves Holm and BY accounting; it is
not permission to create another family, wave, trial, or outcome-bearing
variant.

## Outcome, ledger, and challenge boundary

This addendum authorizes the revised no-return gate and, after that gate passes
and the content-addressed registration above is sealed, the single frozen
discovery/2019-2024 validation trial. It supersedes the original canonical-
ledger prerequisite only for this one non-capital discovery/validation run.
Canonical ledger import and authentication remain mandatory before the result
can support an owner decision, independent-review completion, lifecycle step,
or capital action.

The 2025-01-01 through 2026-06-30 challenge period remains sealed. No source
materiality decision, data-gate pass, validation result, or owner run authority
opens it. Challenge access still requires the original separately authorized,
registered, single-use complete-epoch procedure.

No term in this addendum authorizes Shadow, Paper, Live, registry, allocation,
broker, order, cron, deployment, or production changes.

## Addendum record

- Approved by: Brett Olson, CIO, through explicit owner direction to replace
  the absolute source gate with this materiality framework and rerun the
  authorized experiment; drafted by the Quant Research Scientist under Atlas.
- Approved at: 2026-08-30, America/New_York.
- Original frozen specification SHA-256:
  `3ca51f2f477c548d0b9ad266f004b4f61ba532f1d23961847c05db1e5fd033d6`.
- Addendum SHA-256: `6a3747d98e89efdb3f73e0f7a3587992b38804789e43534a7ec03842ee5e3c8e` (all bytes before
  `## Addendum record`).
- Outcome access at freeze: `NONE`; reporter reactions, forward returns,
  validation outcomes, statistical inference, and challenge inputs remained
  unread while this addendum was drafted.
- Canonical-ledger status at freeze:
  `FALLBACK_AUTHORIZED_PENDING_AUTHENTICATED_IMPORT`; this repository record is
  not represented as an authenticated global-ledger event.
