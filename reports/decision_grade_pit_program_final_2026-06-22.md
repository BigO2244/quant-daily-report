# Decision-Grade PIT Research Infrastructure Completion - Program Status

Date: 2026-06-22
Final status: BLOCKED
Governance label: RESEARCH_ONLY / NON_EXECUTIONAL
Runtime impact: none

## 1. Executive Summary

The program advanced through Phases 1-5 and stopped at Phase 6 as required by the gate rules.

Completed:

- FR-068 canonical `Universe(as_of_date, "caerus_large_cap")` resolver correction.
- Security_id replay price panel infrastructure.
- Canonical Polaris/Orion/Lyra decision tapes.
- Canonical allocator baseline specification.
- Exposure-matched research framework.

Blocked:

- Decision-grade certification failed because the current `caerus_large_cap` artifact uses current `scalemarketcap`, not date-effective DAILY market cap.
- Phase 7 conviction-allocation rebaseline was not executed.
- Phase 8 sleeve-promotion rebaseline was not executed.
- Phase 9 hardening was not executed.

## 2. Governance Updates

Ownership remains:

- FR-068: PIT universe, PIT pricing, survivorship-free infrastructure, security identity.
- FR-069 child: replay harness, decision tapes, replay framework.

No new FR was created. FR-074 was not reused.

Updated:

- `docs/governance/fr_registry.md`
- `docs/governance/fr_active_backlog.md`
- `docs/governance/CURRENT_RESEARCH_ROADMAP.md`

## 3. FR-068 Completion Status

FR-068 resolver certification: PASS.

`Universe(as_of_date, "caerus_large_cap")` now resolves the family artifact:

| Date | Members |
| --- | ---: |
| 2014-01-02 | 1,197 |
| 2020-01-02 | 1,243 |
| 2026-01-02 | 1,260 |

FR-068 decision-grade large-cap status: BLOCKED.

Reason: current family scale source is `scalemarketcap`, which is current/approximate for history.

## 4. Certification Artifacts

| Artifact | Path |
| --- | --- |
| Phase 1 certification | `reports/pit_universe_certification.md` |
| Phase 2 report | `reports/security_id_replay_infrastructure.md` |
| Phase 3 report | `reports/canonical_decision_tape.md` |
| Phase 4 baseline spec | `reports/allocator_baseline_spec.md` |
| Phase 5 exposure framework | `reports/exposure_matched_framework.md` |
| Phase 6 certification | `reports/decision_grade_certification.md` |
| Program status | `reports/decision_grade_pit_program_final_2026-06-22.md` |

## 5. Replay Infrastructure Summary

Price panel:

- path: `outputs/research/canonical_pit_replay/2026-06-22/price_panel.parquet`
- rows: 3,924,559
- securities: 1,580
- date range: 2012-06-01 to 2024-12-31
- duplicate `date, security_id` keys: 0
- delisted securities: 354
- delisted-security rows: 605,042

Decision tapes:

- Polaris: `outputs/research/canonical_pit_replay/2026-06-22/decision_tape_caerus_polaris.parquet`
- Orion: `outputs/research/canonical_pit_replay/2026-06-22/decision_tape_caerus_orion.parquet`
- Lyra: `outputs/research/canonical_pit_replay/2026-06-22/decision_tape_caerus_lyra.parquet`

## 6. Conviction Allocation Findings

Not executed in this program.

Reason: Phase 6 failed. The prior conviction findings remain non-decision-grade lineage only.

## 7. Sleeve Promotion Findings

Not executed in this program.

Reason: Phase 6 failed. No Orion, Lyra, Polaris, or promotion-readiness conclusion was changed.

## 8. Remaining Risks

- Current-scale large-cap membership may introduce look-ahead contamination.
- Delisting events do not yet include full terminal return/action enrichment.
- SEP price hydration starts from ticker-named files, although replay artifacts are security_id keyed.
- Production allocator parity replay adapter still needs implementation before allocator research.

## 9. Remaining Blockers

Primary blocker:

- `PIT_EXACT_LARGE_CAP_DAILY_MARKETCAP_MISSING`

Required next step:

- build or hydrate date-effective SHARADAR/DAILY market-cap data and rebuild `caerus_large_cap` as PIT-exact by decision date.

## 10. Final Recommendation

Final status: BLOCKED.

Do not run conviction-allocation or sleeve-promotion rebaseline until Phase 6 passes.
