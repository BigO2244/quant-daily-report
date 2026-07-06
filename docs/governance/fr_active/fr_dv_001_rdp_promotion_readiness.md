# FR-DV-001 RDP Promotion Readiness Review

Status: REVIEW_COMPLETE

Owner / steward: Caerus Research Program

Runtime impact: Documentation-only audit. This review does not promote the
Research Data Platform, alter execution, submit broker orders, change scheduler
behavior, change allocations, or wire RDP artifacts into production sleeve
consumers.

## Executive Summary

Recommendation: `OBSERVE_LONGER`

Confidence score: `0.74`

The Research Data Platform has demonstrated strong observe-only readiness for
the core momentum comparison set. Polaris, Lyra, and Orion pass input, signal,
and output parity on the retained 2026-06-24 artifacts, and all existing FR-DH
validators pass against the current artifact set.

The platform has not yet met the burden of proof for promotion to canonical
research data source for all Caerus research. The strongest blockers are not
code correctness failures; they are evidence-depth and data-completeness gaps:

- data-trust status is still `WARN`;
- 15 data-trust warnings remain across 22 cataloged datasets;
- 4 cataloged datasets are missing artifacts;
- 1 cataloged dataset is blocked;
- several normalized datasets remain sample-scale or source-policy limited;
- feature-store artifacts are reproducible and validated, but key feature sets
  still carry observe-only warnings;
- the retained hydration history is latest-run focused rather than a multi-day
  observation ledger.

Core momentum RDP parity is a major strength and should continue. It is not
sufficient evidence to promote the full RDP as canonical for all research.

## Evidence Reviewed

Reviewed artifacts:

- `data/hydration_logs/latest_hydration_swarm.json`
- `data/manifests/dataset_freshness.json`
- `data/manifests/hydration_capability_matrix.json`
- `data/manifests/p1_normalization_manifest.json`
- `data/manifests/p2_normalization_manifest.json`
- `data/manifests/p3_normalization_manifest.json`
- `data/manifests/feature_store_manifest.json`
- `data/manifests/feature_definitions.json`
- `data/manifests/feature_coverage.json`
- `data/manifests/research_data_observability.json`
- `outputs/data_trust/data_trust_summary.json`
- `outputs/research/data_migration/2026-06-24/migration_readiness.json`
- `outputs/research/data_migration/2026-06-24/core_momentum_parity_summary.json`
- `outputs/research/data_migration/2026-06-24/sleeve_parity_polaris.json`
- `outputs/research/data_migration/2026-06-24/sleeve_parity_lyra.json`
- `outputs/research/data_migration/2026-06-24/sleeve_parity_orion.json`

Migration readiness artifacts were reviewed but not regenerated. Updating them
was not appropriate for this audit because the request was review-only and the
existing 2026-06-24 artifacts were sufficient to support the recommendation.

## Metrics

### Hydration Stability

Latest retained hydration run:

| Metric | Value |
|---|---:|
| Hydration artifact | `data/hydration_logs/latest_hydration_swarm.json` |
| As-of date | `2026-06-24` |
| Generated/completed | `2026-06-25T03:10:39Z` |
| Mode | focused, limit-sample, sleeve `polaris` |
| Focused symbols | 15 |
| Datasets attempted | 5 |
| Successful datasets | 5 |
| Blocked datasets | 0 |
| Attempt count | 5 |
| Attempt statuses | 4 `OK`, 1 `PARTIAL` |
| Credential failures | 0 observed in retained run |
| Broker submission invoked | `false` |

Dataset-level hydration results:

| Dataset | Source | Status | Rows | PIT / safety status |
|---|---|---|---:|---|
| `ohlcv_prices` | `yahoo_chart_public` | `OK` | 7,515 | `PIT_SAFE_SAMPLE_AS_OF_DATED` |
| `security_master_pit` | `nasdaq_sharadar` | `OK` | 15 | `PIT_GRADE_SHARADAR_TICKERS_DATE_WINDOWS` |
| `corporate_actions` | `yahoo_chart_public` | `OK` | 362 | `PIT_SAFE_PUBLIC_CHART_EVENTS_AS_OF_DATED_NEEDS_VENDOR_AUDIT` |
| `dataset_freshness` | `internal_dataset_freshness` | `OK` | 1 raw / 5 freshness rows | `PIT_SAFE_INTERNAL_RUN_METADATA` |
| `fundamentals_pit` | `nasdaq_sharadar` | `PARTIAL` | 2 | `PIT_SAFE_SAMPLE_FILING_FIELDS_REQUIRE_RESTATEMENT_AUDIT` |

Freshness results:

| Dataset | Freshness | Hydration | Validation |
|---|---|---|---|
| `ohlcv_prices` | `OK` | `OK` | `VALIDATED_JSON_SHAPE` |
| `security_master_pit` | `OK` | `OK` | `VALIDATED_PIT_SECURITY_MASTER_SHAPE` |
| `corporate_actions` | `OK` | `OK` | `VALIDATED_JSON_SHAPE` |
| `dataset_freshness` | `OK` | `OK` | `VALIDATED_INTERNAL_MANIFEST_SHAPE` |
| `fundamentals_pit` | `WARN_PARTIAL` | `PARTIAL` | `VALIDATED_NASDAQ_DATA_LINK_DATATABLE_SHAPE` |

Audit interpretation: latest-run hydration is stable for the focused core
dataset set, but persisted history is not deep enough for promotion. Only a
latest-run hydration log is retained in `data/hydration_logs/`; this prevents a
strong multi-day stability claim from repository artifacts alone.

### Dataset Quality

Normalization manifests:

| Stage | Datasets | Normalized | Failed | Validation |
|---|---:|---:|---:|---|
| P1 | 4 | 4 | 0 | validator `OK` |
| P2 | 1 | 1 | 0 | validator `OK` |
| P3 | 3 | 3 | 0 | validator `OK` |

P1 details:

| Dataset | Status | Validation | Rows | Readiness concern |
|---|---|---|---:|---|
| `ohlcv_prices` | `OK` | `PASS` | 7,515 | sample/as-of-dated source policy remains observe-only |
| `security_master_pit` | `OK` | `PASS` | 15 | PIT-grade Sharadar/TICKERS date windows, still narrow coverage |
| `corporate_actions` | `OK` | `PASS` | 362 | vendor audit remains before production impact |
| `dataset_freshness` | `OK` | `PASS` | 5 | adequate for current focused run |

P2/P3 warnings:

| Dataset | Status | Validation | Rows | Warning |
|---|---|---|---:|---|
| `fundamentals_pit` | `OK` | `PASS` | 2 | freshness `WARN_PARTIAL`; restatement/version policy remains observe-only |
| `etf_index_constituents` | `WARN` | `WARN` | 5 | membership ranges and security-id joins remain observe-only |
| `institutional_13f` | `WARN` | `WARN` | 25 | filing metadata only; holdings/CUSIP parsing pending |
| `news_metadata` | `WARN` | `WARN` | 5 | public-source publication timestamps require validation |

Observability/data-trust quality:

| Metric | Value |
|---|---:|
| Cataloged datasets | 22 |
| Observe-only datasets | 17 |
| Blocked datasets | 1 |
| Missing-artifact datasets | 4 |
| Data-trust critical findings | 0 |
| Data-trust warnings | 15 |
| Data-trust readiness | `WARN` |

Missing or blocked datasets:

- `short_interest`: missing artifact.
- `options_iv_open_interest`: missing artifact.
- `analyst_estimate_revisions`: blocked.
- `news_sentiment_embeddings`: missing artifact.
- `alternative_datasets`: missing artifact.

Audit interpretation: structural validators pass, but broad dataset quality is
not promotion-ready. The platform is reliable for the core momentum parity
slice, not for all cataloged research datasets.

### Core Sleeve Parity

Core momentum summary:

| Metric | Value |
|---|---|
| Artifact | `outputs/research/data_migration/2026-06-24/core_momentum_parity_summary.json` |
| As-of date | `2026-06-24` |
| Overall status | `PASS` |
| Sleeves reviewed | 3 |
| Pass count | 3 |
| Warning count | 0 |
| Blocked count | 0 |
| Broker submission invoked | `false` |
| Sleeve runtime invoked | `false` |
| Allocation mutation invoked | `false` |
| Promotion invoked | `false` |

Sleeve-level parity:

| Sleeve | Input parity | Signal parity | Output parity | Missing symbols | Warnings | Failures |
|---|---|---|---|---:|---:|---:|
| Polaris | `PASS` | `PASS` | `PASS` | 0 | 0 | 0 |
| Lyra | `PASS` | `PASS` | `PASS` | 0 | 0 | 0 |
| Orion | `PASS` | `PASS` | `PASS` | 0 | 0 | 0 |

Output parity details:

| Sleeve | Legacy holdings | FR-DH reconstructable holdings | Selected set match | Selected order match | Matching target weights |
|---|---:|---:|---|---|---:|
| Polaris | 10 | 10 | `true` | `true` | 10 |
| Lyra | 5 | 5 | `true` | `true` | 5 |
| Orion | 5 | 5 | `true` | `true` | 5 |

Drift / false positives / unexpected differences:

- No drift was reported in the retained parity artifacts.
- No false positives were reported.
- No unexpected differences were reported.
- The parity adapters reconstructed the expected selections and target weights
  for the 2026-06-24 current-date candidates.

Audit interpretation: core momentum parity is strong enough to continue
observe-only monitoring and to justify a scoped future promotion review. It is
not by itself enough to promote the whole RDP as canonical for Caerus.

### Feature Store

Feature-store artifact:

| Metric | Value |
|---|---:|
| Feature sets | 2 |
| Built feature sets | 2 |
| Failed feature sets | 0 |
| Feature definitions | 7 |
| Feature-store validator | `OK` |

Feature sets:

| Feature set | Rows | Validation | Warning |
|---|---:|---|---|
| `fundamental_features` | 2 | `WARN` | unresolved source-symbol security ids; restatement/version policy observe-only |
| `macro_regime_features` | 16,104 | `WARN` | release-date policy observe-only for one or more inputs |

Feature coverage:

| Feature set | Feature | Coverage |
|---|---|---:|
| `fundamental_features` | `net_margin` | 1.0 |
| `fundamental_features` | `revenue_positive` | 1.0 |
| `fundamental_features` | `profit_positive` | 1.0 |
| `macro_regime_features` | `yield_curve_inverted` | 0.9999379 |
| `macro_regime_features` | `credit_stress` | 0.62829111 |
| `macro_regime_features` | `high_volatility` | 0.00055887 |
| `macro_regime_features` | `rate_10y_percent` | 0.9999379 |

Audit interpretation: feature artifacts are deterministic, versioned, and
validated structurally. They are not promotion-ready for production research
consumption because key feature sets remain observe-only and carry validation
warnings.

### Observability

Observed reporting surfaces:

- Hydration log exists and records read-only runtime impact.
- Dataset freshness exists and captures focused P1/P2 rows.
- Research data observability manifest exists for 22 datasets.
- Data-trust summary exists and records forbidden effects as false.
- Migration readiness exists for 9 sleeves.
- Core momentum parity summary exists for Polaris, Lyra, and Orion.

Safety flags:

| Artifact family | Broker submission | Sleeve runtime | Allocation mutation | Promotion |
|---|---|---|---|---|
| Hydration swarm | `false` | n/a | n/a | n/a |
| Data trust summary | `false` | n/a | n/a | n/a |
| Migration readiness | `false` | `false` | n/a | n/a |
| Core parity summary | `false` | `false` | `false` | `false` |

Audit interpretation: observability is one of the platform strengths. The
reports are machine-readable, deterministic, and explicit about forbidden
runtime effects. However, the observability state itself recommends caution
because data-trust status is `WARN`.

## Validation Results

All existing FR-DH validators were run against existing artifacts.

| Command | Result |
|---|---|
| `.venv/bin/python scripts/data_hydration/validate_research_data_catalog.py` | `OK` |
| `.venv/bin/python scripts/data_hydration/validate_hydration_swarm.py` | `OK` |
| `.venv/bin/python scripts/data_hydration/validate_dataset_freshness.py --required-datasets ohlcv_prices security_master_pit corporate_actions dataset_freshness fundamentals_pit` | `OK` |
| `.venv/bin/python scripts/data_hydration/validate_p1_normalization.py` | `OK` |
| `.venv/bin/python scripts/data_hydration/validate_p2_normalization.py` | `OK` |
| `.venv/bin/python scripts/data_hydration/validate_p3_normalization.py` | `OK` |
| `.venv/bin/python scripts/data_hydration/validate_feature_store.py` | `OK` |
| `.venv/bin/python scripts/data_hydration/validate_research_data_observability.py` | `OK` |
| `.venv/bin/python scripts/data_hydration/validate_data_trust_summary.py` | `OK` |
| `.venv/bin/python scripts/data_hydration/validate_sleeve_migration_readiness.py --path outputs/research/data_migration/2026-06-24/migration_readiness.json` | `OK` |

Validation interpretation: artifact structure and validator expectations pass.
The promotion recommendation is constrained by observation depth and data
quality warnings, not by failing validators.

## Findings

1. Core momentum parity is clean for the retained 2026-06-24 artifacts.
2. P1 normalization is structurally sound and covers current core momentum
   symbols.
3. Security master is PIT-grade for the retained Sharadar/TICKERS coverage and
   has no current-reference fallback rows in the core coverage set.
4. Corporate actions and prices are sufficient for observe-only parity but
   still include source-policy caveats before production impact.
5. Fundamentals and feature-store artifacts are present but not mature enough
   for broad canonical promotion.
6. Data-trust summary is `WARN`, not `PASS`.
7. Broader catalog completeness is not achieved: blocked/missing datasets
   remain.
8. Persisted hydration history is latest-run focused; there is not enough
   retained multi-day run history to prove operational stability over the
   observation period.

## Strengths

- All FR-DH validators pass.
- Core sleeve parity passes for Polaris, Lyra, and Orion.
- Migration readiness reports 5 sleeves as `READY_OBSERVE_ONLY`, including
  Polaris, Polaris Alpha, Orion, Orion Alpha, and Lyra.
- The platform consistently records read-only runtime impact.
- Forbidden-effect flags remain false for broker submission, sleeve runtime,
  allocation mutation, and promotion.
- P1 datasets required for core momentum have complete symbol coverage in the
  reviewed readiness artifact.
- Observability is broad and machine-readable.

## Weaknesses

- Data-trust status remains `WARN`.
- Latest hydration was focused and sample-limited, not full-platform.
- Fundamentals are partial.
- Feature-store outputs are still observe-only and carry warnings.
- Several datasets remain missing or blocked.
- Some public-source PIT claims require vendor or source-policy audit before
  promotion.
- Retained hydration history is not sufficient to measure multi-day success
  rate, retry behavior, or anomaly frequency.

## Remaining Risks

### Technical Risks

- Canonical adapters may still contain edge cases that have not appeared in the
  narrow focused run.
- Retry behavior is not proven by retained artifacts.
- Full platform runs may expose source-specific failures hidden by focused
  sleeve runs.

### Data Risks

- Prices and corporate actions are observe-only source-policy safe, not yet
  promoted as production-impacting canonical sources.
- Fundamentals need restatement/version policy hardening.
- Macro features need release-date policy hardening.
- Some event and institutional datasets are filing-metadata only.
- Missing and blocked datasets prevent broad platform completeness.

### Governance Risks

- Promotion scope could be misread. Core momentum parity does not imply
  platform-wide canonical readiness.
- RDP parity could be mistaken for alpha or promotion evidence.
- Future datasets could be added without Phase 2 hypothesis and contribution
  discipline unless the new governance gate is enforced.

### Operational Risks

- Latest-run-only hydration history weakens operational stability proof.
- No production consumer should rely on these artifacts without separate
  promotion approval.
- Generated artifacts remain research outputs and should not be treated as
  production inputs by default.

## Promotion Recommendation

Recommendation: `OBSERVE_LONGER`

Rationale:

The RDP has earned continued observe-only operation and a narrower future
promotion review for the core momentum data path. It has not yet met the burden
of proof to become the canonical research data source for all Caerus research.

This is not a `NOT_READY` recommendation because:

- validators pass;
- core parity passes;
- P1 coverage is strong for reviewed core symbols;
- observability is materially better than the pre-RDP state.

This is not `READY_WITH_MINOR_RECOMMENDATIONS` because:

- the data-trust summary is still `WARN`;
- broader catalog coverage is incomplete;
- feature-store warnings are meaningful;
- the retained hydration history is too shallow for a promotion-grade
  stability conclusion.

## Required Follow-up

Before a renewed promotion review, require:

1. At least 5 consecutive market days of retained hydration, freshness,
   observability, migration readiness, and parity artifacts.
2. Historical hydration ledger retention, not only `latest_hydration_swarm.json`.
3. Core momentum parity PASS across multiple dates, not only the current
   retained 2026-06-24 artifact set.
4. Data-trust status reduced from `WARN` to `PASS` for the proposed promotion
   scope, or explicit scoped waiver language.
5. Fundamentals policy review for restatements, source-security resolution, and
   feature lineage before Value/Quality promotion work.
6. Macro release-date policy review before Argo/macro features are considered
   canonical.
7. Source-policy decision for prices and corporate actions before they become
   production-impacting canonical inputs.
8. Explicit promotion scope: core momentum only, or full RDP. These require
   different evidence thresholds.

## Confidence Level

Confidence score: `0.74`

Confidence basis:

- High confidence that the reviewed core momentum parity artifacts pass.
- High confidence that current validators pass.
- Medium confidence in short-run hydration stability for the focused dataset
  set.
- Low-to-medium confidence in broad platform readiness due to missing/blocked
  datasets and limited retained history.

## Final Committee Decision

Keep RDP observe-only. Do not promote.

Approved next state:

- continue daily observe-only RDP parity monitoring;
- preserve artifact history;
- prepare a scoped core-momentum canonical-data promotion review only after
  multi-day evidence exists;
- keep broader RDP datasets in research/observe-only status until data-trust
  warnings and missing datasets are resolved or explicitly scoped out.

Runtime impact statement: this audit created a governance recommendation only.
It does not modify code, execution logic, scheduler behavior, broker behavior,
allocation, sleeve promotion, or production data-consumer behavior.
