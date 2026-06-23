# FR-076 Reliability Historical Replay

Status: RESEARCH_REVIEW_COMPLETE
Date: 2026-06-19
Scope: Research-only replay of FR-074 reliability classifications across local Caerus run artifacts from the last 60-90 days.

## Objective

Estimate what FR-074 reliability readiness would have reported if it had existed during the available historical execution window. This review is explicitly non-operational: it does not modify execution behavior, strategy selection, allocation, broker submission, or promotion gates.

## Source Window

- Window basis: run-root timestamp date.
- Window start: 2026-03-21.
- Window end: 2026-06-19.
- Local run roots reviewed: 20.
- Replay method: `core.operational_invariants.build_execution_reliability_report`.
- Operational mutation avoided: the replay did not call `write_execution_reliability_report`, so it did not append to `outputs/reliability/reliability_history.json` and did not overwrite operational readiness artifacts.

## Output Artifacts

- `outputs/research/fr074_replay/fr074_replay_summary.json`
- `outputs/research/fr074_replay/fr074_replay_runs.json`
- `outputs/research/fr074_replay/fr074_replay_timeline.csv`
- `outputs/research/fr074_replay/fr074_top_failure_reasons.csv`
- `outputs/research/fr074_replay/fr074_replay_report.md`

## Reliability Distribution

| Classification | Count |
|---|---:|
| RELIABILITY_GREEN | 20 |
| RELIABILITY_YELLOW | 0 |
| RELIABILITY_RED | 0 |

## Top Failure Reasons

| Failure reason | Count |
|---|---:|
| none | 20 |

## Reliability Timeline

| Run ID | Trade date | Score | Classification | Fail invariants | Warn invariants | Top failure reason | Evidence coverage |
|---|---:|---:|---|---:|---:|---|---|
| `2026-03-21T225850-0400_e55782b` | 2026-03-21 | 100 | RELIABILITY_GREEN | 0 | 0 | none | LOW |
| `2026-03-21T230047-0400_dede2b3` | 2026-03-21 | 100 | RELIABILITY_GREEN | 0 | 0 | none | LOW |
| `2026-03-21T230234-0400_f65f7b5` | 2026-03-21 | 100 | RELIABILITY_GREEN | 0 | 0 | none | LOW |
| `2026-03-22T084245-0400_797b5dc` | 2026-03-22 | 100 | RELIABILITY_GREEN | 0 | 0 | none | LOW |
| `2026-03-22T084436-0400_c1f18f2` | 2026-03-22 | 100 | RELIABILITY_GREEN | 0 | 0 | none | LOW |
| `2026-03-22T084714-0400_3cf63d4` | 2026-03-22 | 100 | RELIABILITY_GREEN | 0 | 0 | none | LOW |
| `2026-03-22T084826-0400_c993847` | 2026-03-22 | 100 | RELIABILITY_GREEN | 0 | 0 | none | LOW |
| `2026-03-22T090814-0400_7d993c8` | 2026-03-22 | 100 | RELIABILITY_GREEN | 0 | 0 | none | LOW |
| `2026-03-22T090939-0400_7baba0b` | 2026-03-22 | 100 | RELIABILITY_GREEN | 0 | 0 | none | LOW |
| `2026-03-22T091031-0400_040a4dd` | 2026-03-22 | 100 | RELIABILITY_GREEN | 0 | 0 | none | LOW |
| `2026-03-24T220924-0400_1c8695f` | 2026-03-24 | 100 | RELIABILITY_GREEN | 0 | 0 | none | LOW |
| `2026-03-26T151337-0400_2373f93` | 2026-03-18 | 100 | RELIABILITY_GREEN | 0 | 0 | none | LOW |
| `2026-04-09T154606-0400_e2a4400` | 2099-01-01 | 100 | RELIABILITY_GREEN | 0 | 0 | none | LOW |
| `2026-04-09T161214-0400_e77be36` | 2026-04-09 | 100 | RELIABILITY_GREEN | 0 | 0 | none | LOW |
| `2026-04-09T161639-0400_aac397a` | 2099-01-01 | 100 | RELIABILITY_GREEN | 0 | 0 | none | LOW |
| `2026-04-10T164001-0400_0d4dcad` | 2026-04-10 | 100 | RELIABILITY_GREEN | 0 | 0 | none | LOW |
| `2026-04-13T104353-0400_f0df16f` | 2026-03-12 | 100 | RELIABILITY_GREEN | 0 | 0 | none | LOW |
| `2026-04-13T105653-0400_79a7a06` | 2026-04-13 | 100 | RELIABILITY_GREEN | 0 | 0 | none | LOW |
| `2026-04-13T110024-0400_67948e0` | 2026-04-13 | 100 | RELIABILITY_GREEN | 0 | 0 | none | LOW |
| `2026-04-16T131433-0400_15ae127` | 2026-04-16 | 100 | RELIABILITY_GREEN | 0 | 0 | none | LOW |

## Longest Clean-Run Streak

- Longest FR-074-native GREEN streak: 20 runs.
- Streak start: `2026-03-21T225850-0400_e55782b`.
- Streak end: `2026-04-16T131433-0400_15ae127`.

## Current Readiness If FR-074 Had Existed Since Inception

| Field | Value |
|---|---:|
| Current classification | RELIABILITY_GREEN |
| Current score | 100 |
| Clean-run streak | 20 |
| Trailing 5-run score | 100.0 |
| Trailing 20-run score | 100.0 |
| Fail frequency | 0.0 |
| Warn frequency | 0.0 |
| Last fail reason | none |
| Days since last fail | n/a |

## Critical Caveat

The FR-074-native replay result is not sufficient evidence that the historical system was operationally GREEN.

Every reviewed run has LOW evidence coverage relative to today’s FR-074 artifact expectations. Most run roots predate FR-074 and lack one or more of:

- `execution_results.json`
- `audit/execution_integrity.json`
- `audit/execution_target_attainment_<date>.json`
- `broker/recon_posttrade_<date>.json`

Some run roots are planner-failure roots without `execution_payload.json`. Current FR-074 scoring does not treat those historical artifact gaps as failures by itself, so the all-GREEN distribution should be treated as a classifier-coverage finding, not as a clean operational-performance finding.

## Recommended Threshold Adjustments

1. Do not use the historical all-GREEN distribution alone as the Phase C promotion threshold baseline.
2. Add an artifact-completeness invariant or replay confidence field before enabling operational gates.
3. Require `execution_results.json` for any run with `execution_status` `READY`, `EXECUTED`, or `RECONCILED_SUCCESS`.
4. Require a nonempty terminal reason for planner-failure roots and `failed_pre_execution` operator summaries.
5. For Phase C, require both `RELIABILITY_GREEN` and FULL evidence coverage; score alone is too permissive on legacy artifacts.
6. Consider classifying missing posttrade reconciliation as YELLOW for no-submission runs and RED for submitted/accepted runs.
7. Separate historical replay confidence from live readiness classification so legacy artifact gaps do not contaminate current operations.

## Governance Conclusion

FR-076 establishes a baseline but does not provide promotion-grade historical assurance. The useful finding is that FR-074’s current invariant set is strong for modern run roots with complete artifacts, but under-sensitive for legacy run roots with missing execution evidence.

Recommended next step: FR-074 Phase C should add artifact-completeness gating and require FULL evidence coverage before any reliability readiness classification can be used as an operational promotion gate.
