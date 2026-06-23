# FR-068 Certification Requirement Lineage

Date: 2026-06-23
Scope: Governance review / certification architecture only
Runtime impact: none

## Executive Summary

The current blocker `PIT_EXACT_LARGE_CAP_DAILY_MARKETCAP_MISSING` originated as a research certification guard against historical large-cap universe contamination. The risk is real: the current `caerus_large_cap` artifact uses `scale_source=scalemarketcap`, which is a current/vendor size bucket and is not date-effective.

The current requirement is over-specific. It correctly rejects current `scalemarketcap`, but it incorrectly treats `scale_source=marketcap` as the only certifiable implementation path. The actual certification objective is PIT-valid, survivorship-free, security-id keyed, date-effective large-cap membership.

Final recommendation: REPLACE REQUIREMENT.

## 1. Where The Requirement Originated

The requirement appears in three layers:

| Layer | File | Current behavior |
|---|---|---|
| Large-cap family definition | `research/pit_large_cap_family.py` | Allows either `scalemarketcap` or numeric `marketcap`, but documents `scalemarketcap` as current/approximate and DAILY market cap as PIT-exact |
| Replay panel manifest | `research/canonical_replay_panel.py` | `_classify_scale_precision()` returns `PIT_EXACT_SCALE` only when `scale_source == marketcap`; `scalemarketcap` returns blocker `PIT_EXACT_LARGE_CAP_DAILY_MARKETCAP_MISSING` |
| Formal certification | `research/replay_certification.py` | `--require-decision-grade-scale` promotes the blocker into a FAIL |

Governance docs then inherited that implementation framing:

- `reports/decision_grade_certification.md` states that certification failed because the current family uses current `scalemarketcap`, not date-effective DAILY market cap.
- `reports/fr068_marketcap_root_cause.md` traces the large-cap artifact to `scale_source=scalemarketcap`.
- `docs/governance/fr_registry.md`, `docs/governance/fr_active_backlog.md`, and `docs/governance/CURRENT_RESEARCH_ROADMAP.md` describe the remaining blocker as replacing current-scale `scalemarketcap` with DAILY-marketcap PIT-exact membership.

## 2. Why It Was Introduced

FR-068 was created to eliminate survivorship and look-ahead contamination in historical sleeve evidence. Earlier research showed that static current-universe backtests were materially distorted.

The specific large-cap certification requirement was introduced because:

1. `Universe(as_of_date)` solved security existence through listing/delisting dates.
2. Sharadar SEP hydration improved price coverage, including delisted names in the current large-cap family.
3. The `caerus_large_cap` family still needed a size filter to preserve the intended large-cap sleeve domain.
4. The available large-cap filter was current `scalemarketcap`, which can project later/current size status into historical dates.

The gate was therefore intended to prevent current-size membership from being treated as decision-grade.

## 3. Risks It Was Intended To Mitigate

| Risk | Intended mitigation |
|---|---|
| Membership look-ahead | Do not allow a current size bucket to determine historical large-cap membership |
| Size survivorship | Include securities that were historically large even if they later shrank, merged, delisted, or dropped from current large/mega classification |
| Candidate-set contamination | Prevent rank, score, target-weight, and allocation studies from operating on a hindsight-biased opportunity set |
| False allocator conclusions | Prevent conviction allocation, sleeve promotion, or portfolio construction studies from attributing universe bias to model or sizing skill |

## 4. Whether The Risks Still Exist

Yes, for the current `caerus_large_cap` artifact.

Current artifact facts:

| Artifact | Current state |
|---|---|
| PIT security master | 20,618 securities, 14,790 delisted |
| `caerus_large_cap` membership | 1,600 securities |
| Large-cap scale source | 1,600 rows with `scale_source=scalemarketcap` |
| Resolver | `Universe(as_of_date, "caerus_large_cap")` resolves the artifact correctly |
| Decision-grade membership | Not certified, because the size filter is not date-effective |

The residual risk is not security existence, ticker identity, or basic resolver wiring. The residual risk is that current `scalemarketcap` can define the historical large-cap opportunity set.

## 5. Lineage Conclusion

The requirement was introduced for a valid reason: preventing current-size look-ahead contamination.

The wording and code path are too implementation-specific. They should not require Sharadar DAILY market cap specifically. They should require certified PIT date-effective large-cap membership, with DAILY market cap as one valid implementation.

## Governance Recommendation

REPLACE REQUIREMENT.
