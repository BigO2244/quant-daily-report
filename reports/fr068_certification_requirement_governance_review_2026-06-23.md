# FR-068 Certification Requirement Governance Review

Date: 2026-06-23
Scope: Governance review / certification architecture / research infrastructure
Runtime impact: none

## 1. Executive Summary

The assumption that decision-grade certification must require Sharadar DAILY `marketcap` specifically is not proven. It is an implementation choice embedded in the current certifier.

The real decision-grade objective is:

- PIT-valid membership;
- survivorship-free membership;
- security-id keyed membership;
- date-effective large-cap membership;
- reproducible lineage and coverage diagnostics.

Current certification correctly blocks the existing `scale_source=scalemarketcap` family from decision-grade status. It should continue to block it. But the blocker should be reframed from vendor-specific DAILY market cap to method-neutral date-effective membership.

Final recommendation: REPLACE REQUIREMENT.

## 2. Requirement Lineage

Detailed artifact: `reports/requirement_lineage.md`

Finding:

- Requirement originated in `research/canonical_replay_panel.py::_classify_scale_precision`.
- `scale_source == marketcap` is the only path classified as `PIT_EXACT_SCALE`.
- `scale_source == scalemarketcap` produces `PIT_EXACT_LARGE_CAP_DAILY_MARKETCAP_MISSING`.
- `research/replay_certification.py` promotes that blocker to FAIL when `--require-decision-grade-scale` is used.
- Governance docs inherited this implementation framing as "DAILY-marketcap PIT-exact family."

The requirement was introduced to mitigate current-size look-ahead and size survivorship contamination. Those risks still exist for the current `caerus_large_cap` artifact because all 1,600 rows use `scale_source=scalemarketcap`.

## 3. Risk Matrix

Detailed artifact: `reports/certification_risk_matrix.md`

| Risk | Current assessment |
|---|---|
| Security existence survivorship | PASS |
| Ticker identity contamination | PASS for canonical replay |
| Membership look-ahead | FAIL |
| Current-family contamination | FAIL |
| Pricing contamination | PARTIAL |

Conclusion: FR-068 is PARTIAL under a method-neutral decision-grade standard.

## 4. Alternative Certification Paths

Detailed artifact: `reports/fr068_certification_standard_proposal_2026-06-23.md`

| Path | Suitability |
|---|---|
| Daily marketcap | Strong; valid but not uniquely required |
| PIT index membership | Strong when the declared research universe is index-based |
| Scheduled reconstitution membership | Valid when the sleeve policy reconstitutes on that cadence |
| Decision-time universe artifacts | Valid for actual-decision replay; incomplete for omitted-name counterfactuals |
| PIT shares x close reconstruction | Valid in principle; current local data coverage is insufficient |
| Hybrid methodology | Certifiable only with explicit precedence, lineage, and coverage diagnostics |

## 5. Revised Certification Standard

Proposed replacement blocker:

`PIT_DATE_EFFECTIVE_LARGE_CAP_MEMBERSHIP_REQUIRED`

Proposed certification language:

> A replay universe may be certified decision-grade only when its membership is PIT-valid, survivorship-free, security-id keyed, and date-effective under the declared universe policy. The certifier must record the membership method, source lineage, coverage diagnostics, and no-look-ahead controls. Daily numeric market cap is one acceptable implementation path, but not the only one. Current `scalemarketcap`, current constituent lists, `data/universe.csv`, and ticker-keyed current-survivor universes are not decision-grade membership authorities.

Mandatory controls:

1. PIT-valid.
2. Survivorship-free.
3. Security-id keyed.
4. Date-effective.
5. Explicit universe policy.
6. Coverage-audited.
7. Reproducible.
8. Fail-closed.

Eligible implementation methods:

- `PIT_DAILY_MARKETCAP`
- `PIT_INDEX_MEMBERSHIP`
- `PIT_RECONSTITUTION_MEMBERSHIP`
- `PIT_DECISION_TAPE`
- `PIT_SHARES_PRICE_RECONSTRUCTION`
- `PIT_HYBRID_MEMBERSHIP`

Disallowed methods:

- current `scalemarketcap` alone;
- `data/universe.csv`;
- current index constituent lists backfilled through history;
- current surviving ticker lists;
- ticker-keyed membership without deterministic security-id mapping;
- price panels derived from current-only sources;
- any method without effective dates and lineage.

## 6. FR-068 Compliance Assessment

| Requirement | Current status |
|---|---|
| PIT security master | PASS |
| Delisting-aware security existence | PASS |
| `Universe(as_of_date)` resolver | PASS |
| No `data/universe.csv` fallback in certified replay | PASS |
| Security-id keyed replay panel | PASS |
| Reproducible decision tapes | PASS / PARTIAL because membership caveat is inherited |
| Canonical allocator baseline spec | PASS |
| Exposure-matched framework | PASS |
| Pricing for current family | PARTIAL |
| Date-effective large-cap membership | FAIL |

Overall FR-068 certification status under revised framework: PARTIAL.

## 7. Governance Recommendation

REPLACE REQUIREMENT.

Do not keep the old requirement as written because it requires a specific vendor-table implementation.

Do not relax the gate because current `scalemarketcap` still fails the actual research objective.

Replace the requirement with method-neutral certification of PIT-valid, survivorship-free, security-id keyed, date-effective large-cap membership.

## 8. Remaining Blockers

Exact remaining blockers:

1. `PIT_DATE_EFFECTIVE_LARGE_CAP_MEMBERSHIP_REQUIRED`
2. `CURRENT_SCALE_MEMBERSHIP_NOT_DECISION_GRADE`
3. `PRICING_CERTIFICATION_MUST_BE_RECHECKED_AFTER_MEMBERSHIP_REPLACEMENT`

Downstream allocator, conviction-allocation, and sleeve-promotion research should remain blocked from decision-grade use until those blockers are resolved.

## Validation

Governance consistency review:

- Reviewed `docs/governance/fr_registry.md`.
- Reviewed `docs/governance/fr_active_backlog.md`.
- Reviewed `docs/governance/CURRENT_RESEARCH_ROADMAP.md`.
- Reviewed FR-068 and FR-069 lineage docs under `docs/governance/fr_active/`.

Lineage review:

- Reviewed `reports/fr068_marketcap_root_cause.md`.
- Reviewed `reports/decision_grade_certification.md`.
- Reviewed `reports/security_id_replay_infrastructure.md`.
- Reviewed `reports/canonical_decision_tape.md`.
- Reviewed `reports/fr068_large_cap_membership_requirement_audit_2026-06-23.md`.

Certification review:

- Reviewed `research/canonical_replay_panel.py`.
- Reviewed `research/replay_certification.py`.
- Reviewed `research/pit_large_cap_family.py`.
- Confirmed current `data/pit_universe/security_master.csv` has 20,618 securities.
- Confirmed current `data/pit_universe/membership_universe_large_cap.csv` has 1,600 rows, all with `scale_source=scalemarketcap`.

No production, allocator, broker, scheduler, live, paper, or execution behavior was changed.

Final recommendation: REPLACE REQUIREMENT.
