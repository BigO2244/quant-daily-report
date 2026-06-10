# Point-in-Time Universe Architecture and Survivorship Remediation Plan

Date: 2026-06-10
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL
Local-only: yes
VM/deploy actions: none
Holdout access: none

## 1. Executive Summary

The 2026-06-10 survivorship audit returned **CONFIRMED_BIASED** with **HIGH**
confidence. The root cause is structural: `data/universe.csv` is a static current
universe with no point-in-time membership dates, no delisting support, and no
stable security identifier. Current backtests should remain preserved for lineage,
but they are not decision-grade evidence.

Recommended remediation:

1. Build a canonical point-in-time universe dataset and `Universe(as_of_date)`
   contract.
2. Verify a vendor before official rebaseline. Recommended first vendor gate:
   **Sharadar conditional**, using the existing coverage verifier once a trial key
   is available. If Sharadar fails delisted/PIT reconstruction checks, evaluate
   Norgate next; CRSP is preferred only if WRDS access is available.
3. Rebaseline in order: Polaris, Orion, Lyra, then future sleeves. Do not touch
   the 2025-forward holdout until the PIT framework and pre-registered protocol
   are accepted.

## 2. Canonical PIT Universe Schema

Canonical storage should be append-only, versioned, and source-attributed. The
minimum table is `data/pit_universe/memberships.parquet`; CSV mirrors may exist
for review but parquet should be canonical.

### 2.1 Security master table

Recommended path: `data/pit_universe/security_master.parquet`.

| Field | Type | Required | Description |
|---|---|---:|---|
| `security_id` | string | yes | Internal immutable ID. Never changes across ticker changes. |
| `vendor_security_id` | string | yes | Vendor stable identifier when available. |
| `perm_id` | string | optional | CRSP PERMNO/PERMCO or equivalent stable ID. |
| `ticker` | string | yes | Current or row-effective ticker. Not stable identity. |
| `company_name` | string | yes | Row-effective issuer/security name. |
| `exchange` | string | yes | Listing venue, row-effective. |
| `asset_type` | string | yes | Common stock, ADR, ETF, preferred, etc. |
| `listing_date` | date | yes | First known trading/listing date. |
| `delisting_date` | date/null | yes | Delisting date if inactive. |
| `delisting_reason` | string/null | recommended | Bankruptcy, merger, acquisition, exchange move, ticker change, unknown. |
| `is_active` | bool | yes | Active at latest vendor snapshot. |
| `source` | string | yes | Vendor/source system. |
| `source_asof_date` | date | yes | Date the source snapshot was obtained. |
| `confidence` | enum | yes | HIGH, MEDIUM, LOW. |

### 2.2 Membership table

Recommended path: `data/pit_universe/memberships.parquet`.

| Field | Type | Required | Description |
|---|---|---:|---|
| `security_id` | string | yes | Internal immutable ID. |
| `ticker` | string | yes | Ticker valid for this membership row. |
| `company_name` | string | yes | Name valid for this membership row. |
| `exchange` | string | yes | Venue valid for this membership row. |
| `membership_family` | string | yes | `caerus_large_cap`, `sp500_proxy`, `small_cap_band`, etc. |
| `membership_start_date` | date | yes | First date security is eligible for the family. |
| `membership_end_date` | date/null | yes | Last eligible date; null means still eligible. |
| `listing_date` | date | yes | First known trading/listing date. |
| `delisting_date` | date/null | yes | Final trading/delisting date if known. |
| `source` | string | yes | Vendor/source for this row. |
| `source_asof_date` | date | yes | Source vintage. |
| `confidence` | enum | yes | HIGH, MEDIUM, LOW. |
| `reason_codes` | list/string | yes | `ok`, `missing_delisting_reason`, `estimated_membership_start`, etc. |

### 2.3 Symbol change table

Recommended path: `data/pit_universe/symbol_changes.parquet`.

Required fields:

- `security_id`
- `old_ticker`
- `new_ticker`
- `effective_date`
- `reason`
- `source`
- `confidence`

Ticker changes must not create a new `security_id` unless the underlying security
actually changed. Backtests join on `security_id`; tickers are display/order-route
labels only.

### 2.4 Corporate action/event table

Recommended path: `data/pit_universe/security_events.parquet`.

Required event types:

- `MERGER`
- `ACQUISITION`
- `BANKRUPTCY`
- `DELISTING`
- `SPINOFF`
- `TICKER_CHANGE`
- `EXCHANGE_CHANGE`
- `REENTRY`

Required fields:

- `event_id`
- `security_id`
- `event_type`
- `effective_date`
- `successor_security_id`
- `cash_or_share_consideration`
- `delisting_return_available`
- `source`
- `confidence`

### 2.5 Re-entry handling

Re-entry means a security leaves and later re-enters a membership family. It must
be represented as multiple membership rows sharing the same `security_id` when the
security identity is unchanged. If an issuer emerges from bankruptcy or a new legal
security is issued, create a new `security_id` and link predecessor/successor in
`security_events.parquet`.

## 3. Universe(as_of_date) Contract

`Universe(as_of_date, membership_family, min_confidence="MEDIUM")` becomes the only
canonical research universe interface.

### Inputs

- `as_of_date`: trading or calendar date.
- `membership_family`: family such as `caerus_large_cap`, `sp500_proxy`, or
  `small_cap_band`.
- `min_confidence`: default `MEDIUM`.
- Optional filters: `asset_type`, `exchange`, `include_inactive_if_valid_then`,
  `require_price_available`, `require_fundamentals_available`.

### Outputs

A deterministic table sorted by `security_id`, then `ticker`:

- `security_id`
- `ticker`
- `company_name`
- `exchange`
- `membership_family`
- `membership_start_date`
- `membership_end_date`
- `listing_date`
- `delisting_date`
- `source`
- `confidence`
- `reason_codes`
- optional `price_available_as_of`
- optional `fundamentals_available_as_of`

### Invariants

- No security is returned when `membership_start_date > as_of_date`.
- No security is returned when `membership_end_date < as_of_date`.
- No security is returned before `listing_date`.
- A delisted security can be returned for dates before or on `delisting_date`.
- The same `security_id` appears at most once for a given `as_of_date` and family.
- Ticker changes preserve `security_id`.
- Results are deterministic and source-attributed.
- Missing source coverage degrades visibly with reason codes; it never silently
  falls back to `data/universe.csv`.

### Edge cases

- **IPO during lookback:** eligible only after listing and membership start. Signals
  requiring 252 trading days may remain unavailable until enough history exists.
- **Delisting during holding period:** include delisting return when available;
  otherwise mark blocker `missing_delisting_return`.
- **Merger/acquisition:** close predecessor at effective date and optionally map
  proceeds/successor using event data. Do not backfill successor history into
  predecessor identity.
- **Ticker change:** same `security_id`; ticker display changes at effective date.
- **Re-entry:** separate membership intervals; no continuous eligibility through
  the gap.
- **Low-confidence rows:** excluded by default from official backtests; allowed
  only in diagnostic runs with explicit reason codes.

## 4. Migration Strategy

The migration should be staged so existing operational behavior is untouched until
research has a validated PIT artifact.

### Easy migration

These paths primarily need a loader swap or optional `--universe-provider pit`:

- `research/flow_detection/data.py`
- `research/flow_detection/run.py`
- `research/flow_detection/v2_run.py`
- `research/alpha_lab_v1/run.py`
- `research/alpha_lab_v2/run.py`
- `scripts/backfill_shadow_artifacts.py`
- `scripts/refresh_shadow_scorecard_artifacts.py`
- `scripts/hydrate_price_cache_only.py`
- `scripts/research/check_price_cache_coverage.py`
- `research_registry/research/universe_quality.py`

### Moderate migration

These need `security_id`-aware joins, symbol mapping, or artifact schema updates:

- `research/shadow_tracking/run.py`
- `alpha_stack/research/backtest.py`
- `alpha_stack/research/shadow_runner.py`
- `alpha_stack/research/validation_runner.py`
- `alpha_stack/datastore/breadth.py`
- `scripts/research/run_phoenix_research.py`
- `scripts/research/run_cygnus_research.py`
- `research/cygnus/events.py`
- `core/quant_report.py`
- `scripts/diag_regime_engine.py`
- `research/universe_governance.py`
- `research/security_master_reconciliation.py`
- `research_registry/research/security_master_diagnostics.py`

### High-risk migration

These are high-risk because they touch portfolio interpretation, sector maps, or
production-adjacent checks. They must remain read-only during the PIT build and be
migrated only after research parity tests pass:

- `core/risk_controls.py` sector map loading
- `core/security_master.py`
- `core/universe_v4.py`
- Any execution/precompute path that imports a universe for diagnostics or
  validation.

## 5. Vendor Decision Matrix

| Vendor/source | Delisted coverage | PIT constituent coverage | Symbol changes | Cost | Implementation effort | Suitability |
|---|---|---|---|---|---|---|
| Sharadar / Nasdaq Data Link | Likely good for delisted prices via SEP/SFP, must verify | Market-cap/PIT reconstruction likely possible; official index membership uncertain | Moderate; stable IDs/tickers available but must verify | Moderate | Low/medium REST/bulk integration | **Recommended first conditional gate** |
| Norgate | Strong survivorship-free equity data | Strong index membership products | Strong | Moderate/high | Medium/high due Windows/NDU workflow | Best backup if Sharadar fails |
| CRSP / WRDS | Excellent | Excellent | Excellent PERMNO/PERMCO | High or access-gated | Medium if access exists | Gold standard if available |
| Polygon | Delisted coverage must be verified | Not enough for PIT constituent membership alone | Moderate | Moderate/high | Medium | Useful pricing supplement, not sufficient alone |
| Tiingo | Delisted coverage must be verified | Not sufficient for membership alone | Moderate | Low/moderate | Low | Useful supplement, not canonical alone |
| Nasdaq Data Link other datasets | Depends on dataset | Depends on dataset | Depends | Variable | Low/medium | Candidate only with explicit delisted/PIT proof |
| Reconstructed index announcements | No prices | Partial and fragile | Weak | Low | High manual QA burden | Not canonical; cross-check only |
| SEC/yfinance/local cache | No survivorship-free universe | None | Weak | Low | Low | Diagnostics only, not official |

Recommended vendor path:

1. Run the existing Sharadar verifier when a trial key exists.
2. Accept Sharadar only if it proves delisted coverage, stable IDs, and PIT
   reconstruction for both large-cap and small-cap families.
3. If Sharadar fails, evaluate Norgate next.
4. Use CRSP if WRDS access exists or becomes economical.

## 6. Rebaseline Plan

Do not rerun official production backtests or touch the 2025-forward holdout until
Phase 1 artifacts and governance checks are accepted.

### Phase 1 — PIT universe creation

Scope:

- Vendor coverage verification.
- Build `security_master`, `memberships`, `symbol_changes`, and `security_events`.
- Implement read-only `Universe(as_of_date)` module and CLI.
- Generate diagnostic artifacts for 2014-01-02, 2016-01-04, 2020-01-02,
  2022-01-03, and 2024-01-02.

Estimated effort: 4-8 engineering days after vendor access.
Runtime: minutes for metadata, hours for initial price/fundamental hydration.
Compute cost: low on Mac Studio; network/vendor API limits likely dominate.

### Phase 2 — Polaris rebaseline

Scope:

- Rerun baseline engine over PIT universe through validation window only.
- Compare legacy vs PIT metrics and attribution.
- Preserve old artifacts as `legacy_current_universe`.

Estimated effort: 2-4 engineering days.
Runtime: 1-4 hours depending on price/fundamental hydration.

### Phase 3 — Orion rebaseline

Scope:

- Migrate shadow/backtest runner to PIT loader.
- Rebuild Orion artifacts with security IDs and PIT membership counts.

Estimated effort: 2-4 engineering days.
Runtime: 1-3 hours.

### Phase 4 — Lyra rebaseline

Scope:

- Rebuild constrained Lyra comparisons using PIT membership and sector maps.
- Re-evaluate Lyra/Orion differentiation after PIT correction.

Estimated effort: 2-4 engineering days.
Runtime: 1-3 hours.

### Phase 5 — future sleeves

Scope:

- Phoenix, Cygnus v1, Vela, multi-asset sleeves, and any future research must use
  `Universe(as_of_date)` from inception.

Estimated effort: incremental after framework exists.

## 7. Governance Plan

Existing artifacts should be preserved, not deleted. They should be relabeled:

- `legacy_current_universe`: produced from static `data/universe.csv`.
- `pit_universe`: produced from canonical `Universe(as_of_date)`.
- `diagnostic_only`: useful for development but not decision-grade.

Recommended governance actions:

1. Mark all pre-PIT backtests as **NON_DECISION_GRADE_SURVIVORSHIP_BIASED**.
2. Add a `universe_method` field to backtest and shadow artifacts.
3. Add a `universe_artifact_path` and `universe_snapshot_hash`.
4. Preserve contaminated results under legacy namespaces for lineage.
5. Require promotion packets to reject any strategy whose evidence lacks
   `universe_method: pit_universe`.
6. Keep `data/universe.csv` only as a current operational/watchlist input until it
   can be retired; it must not be used for official historical evidence.

## 8. Draft FR Specification

FR: Point-in-Time Universe Build and Backtest Rebaseline

Status: DRAFT_RESEARCH

Objective:

Build the canonical PIT universe framework for Caerus research, remove
current-universe survivorship contamination from official historical evidence,
and rebaseline Polaris, Orion, and Lyra before any further promotion decisions.

Success criteria:

- `Universe(as_of_date)` returns deterministic PIT-eligible securities with stable
  `security_id`.
- Delisted securities and ended memberships are represented.
- Symbol changes preserve stable security identity.
- Vendor coverage audit passes for required history and membership families.
- Legacy current-universe backtests are explicitly labeled non-decision-grade.
- Polaris/Orion/Lyra PIT rebaseline artifacts include universe hashes, membership
  counts, delisting coverage, and comparison against legacy results.
- No execution, broker, cron, strategy registry, or production allocation behavior
  changes during the research build.

Implementation phases:

1. Vendor verification and PIT source selection.
2. Canonical data model and fixture build.
3. `Universe(as_of_date)` module, CLI, and tests.
4. Backtest/shadow loader migration behind an explicit research flag.
5. Polaris rebaseline.
6. Orion rebaseline.
7. Lyra rebaseline.
8. Governance packet integration and legacy artifact relabeling.

Risks:

- Vendor data may lack adequate delisted coverage.
- Symbol-change handling can create duplicate or missing identities.
- Delisting returns may be unavailable or require conservative degradation.
- Backtest results may deteriorate materially after PIT correction.
- API/bulk-download limits may dominate runtime.

Validation plan:

- Unit tests for IPO, delisting, ticker change, merger, acquisition, bankruptcy,
  and re-entry cases.
- Deterministic snapshot tests for historical dates.
- Cross-check membership counts against vendor documentation.
- Ensure no `data/universe.csv` fallback in official PIT mode.
- Compare legacy vs PIT backtest metrics and top contributors.
- Verify 2025-forward holdout remains untouched until explicitly authorized.

Deployment plan:

Research-only until accepted. No VM deployment, cron installation, execution path
change, model promotion, or strategy registry edit is part of the initial FR.
After local validation, source code may be merged, but production consumers must
continue using existing behavior until a separate deployment/governance approval.

## 9. Recommended First Implementation Task

Implement a local read-only PIT universe skeleton with fixtures before vendor data:

1. Create `research/pit_universe/` with schema dataclasses and a
   `Universe(as_of_date)` resolver.
2. Add fixture CSV/parquet rows covering IPO, delisting, ticker change, merger,
   acquisition, bankruptcy, and re-entry.
3. Add tests proving eligibility invariants and deterministic ordering.
4. Add a CLI that emits a diagnostic snapshot but does not touch existing
   backtest runners.

This creates the contract safely before any vendor import work begins.
