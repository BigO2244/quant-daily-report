# FR-068 Decision-Grade Certification Standard Proposal

Date: 2026-06-23
Scope: Governance review / certification architecture only
Runtime impact: none

## Executive Summary

Decision-grade certification should test what must be true for allocator, sleeve-promotion, and portfolio-construction research to be reliable. It should not require one vendor table or one implementation path.

The current strict requirement proves too much:

- Correct: current `scalemarketcap` cannot certify historical large-cap membership.
- Incorrect: Sharadar DAILY `marketcap` is the only possible certifiable method.

Final recommendation: REPLACE REQUIREMENT.

## Alternative Certification Paths

| Option | PIT correctness | Survivorship correctness | Security-id correctness | Complexity | Certification suitability |
|---|---|---|---|---|---|
| A. Daily marketcap | High if rows are date-stamped and used only as of decision date | High if active and delisted securities are covered across the full PIT master | High if keyed by `security_id` or deterministically mapped | Medium | Strong; valid implementation, not uniquely required |
| B. PIT index membership | High if add/remove events are effective-dated | High if deleted/acquired constituents remain in history | High if constituents map to `security_id` | Medium to high | Strong when the research universe is explicitly index-based |
| C. Scheduled reconstitution membership | High if membership dates are pre-specified and effective-dated | Medium to high depending on source coverage | High if rows are security-id keyed | Medium | Valid when the sleeve policy reconstitutes on that cadence |
| D. Decision-time universe artifacts | High for replaying actual decisions if artifacts were generated at decision time | High for names actually considered; incomplete for omitted-name counterfactuals | Medium to high depending on historical mapping | Medium | Valid for actual-decision replay; not sufficient for full opportunity-set reconstruction |
| E. PIT shares x close reconstruction | High if shares are known-as-of, split-aware, and prices are unadjusted | High if shares/prices include delisted securities | High if keyed or mapped to `security_id` | High | Valid in principle; current local coverage is insufficient |
| F. Hybrid methodology | Variable | Variable | Variable | High | Certifiable only if each component has explicit precedence, lineage, and coverage diagnostics |

## Replacement Standard

### Mandatory Controls

A certification-eligible large-cap membership artifact must satisfy all of the following:

1. PIT-valid: membership uses only information available on or before each historical decision date.
2. Survivorship-free: active, delisted, acquired, merged, renamed, and otherwise inactive securities are eligible when they were historically in-scope.
3. Security-id keyed: canonical identity is `security_id`; ticker is display/source metadata only.
4. Date-effective: each membership row has effective start/end semantics or a deterministic decision-date membership rule.
5. Universe-policy explicit: the artifact declares whether it represents numeric large-cap threshold, index membership, scheduled reconstitution, actual decision-time candidate universe, or another approved policy.
6. Coverage-audited: the artifact reports coverage against the PIT security master or the declared membership authority.
7. Reproducible: inputs, hashes, source paths, generation command, and lineage digest are recorded.
8. Fail-closed: missing size, membership, identity, or price evidence does not silently fall back to `data/universe.csv`, current ticker lists, or current scale.

### Optional Implementation Paths

The standard should allow these passing methods when the mandatory controls pass:

- `PIT_DAILY_MARKETCAP`
- `PIT_INDEX_MEMBERSHIP`
- `PIT_RECONSTITUTION_MEMBERSHIP`
- `PIT_DECISION_TAPE`
- `PIT_SHARES_PRICE_RECONSTRUCTION`
- `PIT_HYBRID_MEMBERSHIP`

### Disallowed Approaches

These methods should not be eligible for decision-grade PASS:

- current `scalemarketcap` alone;
- `data/universe.csv`;
- current index constituent lists backfilled through history;
- current surviving ticker lists;
- ticker-keyed membership without deterministic security-id mapping;
- price panels derived from current-only sources;
- any method that does not record effective dates and lineage.

## Proposed Certification Language

Replace:

`PIT_EXACT_LARGE_CAP_DAILY_MARKETCAP_MISSING`

with:

`PIT_DATE_EFFECTIVE_LARGE_CAP_MEMBERSHIP_REQUIRED`

Decision-grade certification language:

> A replay universe may be certified decision-grade only when its membership is PIT-valid, survivorship-free, security-id keyed, and date-effective under the declared universe policy. The certifier must record the membership method, source lineage, coverage diagnostics, and no-look-ahead controls. Daily numeric market cap is one acceptable implementation path, but not the only one. Current `scalemarketcap`, current constituent lists, `data/universe.csv`, and ticker-keyed current-survivor universes are not decision-grade membership authorities.

## FR-068 Compliance Assessment Under Revised Framework

| Requirement | Current FR-068 status | Assessment |
|---|---|---|
| PIT security master | 20,618 securities / 14,790 delisted | PASS |
| Security existence `Universe(as_of_date)` | Implemented and resolver-certified | PASS |
| No `data/universe.csv` fallback | Certified replay paths prohibit it | PASS |
| Security-id keyed replay panel | Implemented; ticker display only | PASS |
| Decision tapes | Implemented and reproducible | PASS / PARTIAL because tapes inherit membership caveat |
| Canonical allocator baseline | Specified | PASS |
| Exposure-matched framework | Implemented | PASS |
| Pricing for current family | SEP cache complete for current 1,600-family panel | PARTIAL |
| Date-effective large-cap membership | Current artifact uses `scale_source=scalemarketcap` | FAIL |

Current FR-068 infrastructure classification: PARTIAL.

Exact remaining blockers:

1. `PIT_DATE_EFFECTIVE_LARGE_CAP_MEMBERSHIP_REQUIRED`
2. `CURRENT_SCALE_MEMBERSHIP_NOT_DECISION_GRADE`
3. `PRICING_CERTIFICATION_MUST_BE_RECHECKED_AFTER_MEMBERSHIP_REPLACEMENT`

## Governance Recommendation

REPLACE REQUIREMENT.

Do not relax the gate to allow the current `scalemarketcap` family to pass.

Do replace the vendor-specific DAILY-marketcap language with method-neutral certification language requiring PIT-valid, survivorship-free, security-id keyed, date-effective large-cap membership.
