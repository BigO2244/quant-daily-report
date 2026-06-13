# 2026-06-13 Codex Mini Context Audit - Independent Audit

## Scope

- Incident: impossible Daily Model Scorecard values reported through 2026-06-12.
- Repository branch audited: `codex/audit-shadow-nav-context`.
- Local audit base SHA before patch: `34fdd42fe5204e43e055b2c8271092559b912842`.
- `origin/main` at evidence capture: `a1ddc68351daccc0be7ff2f131a817479ef178d9`.
- VM deployment was not performed.

## AIOPS

- Spec: `specs/2026-06-13_shadow_nav_context_audit.md`
- Command: `python3 -m aiops run-all --spec specs/2026-06-13_shadow_nav_context_audit.md --mode HARDEN`
- Run artifacts: `reports/ai_runs/20260613_111834_34fdd42/`
- Result: `FAILED`, exit code `5`.
- Concrete blocker: AIOPS dispatch failed before run/verify; `run_all_summary.md` records `dispatch=1`, `run=-1`, `verify=-1`.
- Manual deterministic audit was performed after preserving evidence.

## Evidence Preservation

Evidence was preserved before code changes in `audit_manifest.json` and `evidence_backup/`.

Preserved minimum evidence includes:

- Git status, branch, HEAD, `origin/main`, recent log, reflog, working diff, cached diff.
- `outputs/shadow_candidates/performance/shadow_nav_series.csv`
- `outputs/shadow_candidates/performance/shadow_summary.json`
- `outputs/shadow_candidates/latest/`
- available dated `outputs/shadow_candidates/*/` artifacts.
- available price hydration artifacts.

Local `shadow_nav_series.csv` summary at capture:

- Rows: 3,125
- First date: 2014-01-02
- Last date: 2026-06-05
- Header: `date,caerus_polaris,caerus_orion,caerus_lyra,spy_benchmark`
- Continuity scan: no local continuity defects found through 2026-06-05.

Missing local artifacts around the reported incident:

- `outputs/shadow_candidates/2026-06-12`
- `outputs/shadow_candidates/2026-06-11`
- `outputs/shadow_candidates/2026-06-10`
- `outputs/shadow_candidates/2026-06-09`
- `outputs/price_hydration/2026-06-12`
- `outputs/price_hydration/2026-06-11`
- `outputs/price_hydration/2026-06-10`
- `outputs/price_hydration/2026-06-09`

## Root-Cause Finding

Confidence: `HIGH_CONFIDENCE`.

Pre-patch code had two coupled weaknesses:

1. `research/shadow_tracking/run.py::load_prior_shadow_performance()` returned `NO_PRIOR` when the previous dated artifact directory was absent.
2. `build_shadow_performance_payload()` interpreted `NO_PRIOR` as legitimate inception and initialized `previous_nav` from `1.0`.
3. `scripts/refresh_shadow_scorecard_artifacts.py::_append_nav_series()` appended or overwrote `shadow_nav_series.csv` rows without validating prior CSV NAV, reported `previous_nav`, reported `daily_return`, candidate NAV, schema, or existing-date restatement.

This explains the reported symptom: daily return can remain locally plausible while seven-day and YTD windows collapse because the cumulative NAV chain silently restarted near `1.0` inside an established history.

The exact production corrupted row is not present locally, so the first invalid production row is classified as reported, not locally reproduced from a persisted local artifact.

## Last Valid / First Invalid

- Last locally proven valid Shadow NAV date: 2026-06-05.
- First reported invalid date: 2026-06-12.
- First locally proven invalid persisted row: none found in the local checkout.

## Recent Commit Audit

| Commit | Branch context | Intended scope | Actual scope | Runtime impact | Finding |
|---|---|---|---|---|---|
| `e764819` | pre-`origin/main` ancestry | Governance refresh and initial FR-069 design | Docs/governance only | None | No Shadow runtime change. |
| `4b426a0` | `origin/main` ancestry | Add MCP target-attainment diagnostic | New read-only diagnostic and MCP surface | Read-only | No order submission/sizing/allocation changes found. |
| `a1ddc68` | `origin/main` | Post-buy artifact timing diagnostics | Adds buy-fill/posttrade telemetry and tests; touches `paper_broker.py` and execution payload plumbing | Telemetry/artifact timing | Tests passed; no evidence of changed order behavior. |
| `22bedd0` | feature branch | VM access policy | Docs/governance | None | Corrects static IP governance. |
| `981ee6c` | feature branch | Dashboard auth reset | Dashboard utility/docs | Dashboard auth only | No trading impact. |
| `e4abc60` | feature branch | Governance backlog/dashboard auth | Docs/dashboard utility | Dashboard auth/governance only | No trading impact. |
| `b8478e6` | feature branch | Promote FR-069 as architecture workstream | Docs/governance | None | Later priority language required clarification. |
| `5943842` | feature branch | FR-069 Phase A architecture package | Docs/governance | None | Research-only. |
| `34fdd42` | feature branch | FR-069 Phase B scaffolding | Research-only manifest/validator/MCP/tests/docs | Read-only metadata/tooling | No production strategy behavior change found. |

## Governance Findings

- FR-070 remains `DEPLOYED_OBSERVING`.
- Owner directive on 2026-06-13 makes FR-070 the highest immediate operational observation priority.
- FR-069 remains the next major architecture workstream and research-only.
- Orion and Lyra remain under evaluation; no retirement or rename is approved.
- FR-063 status is inconsistent by surface: registry lists `ACTIVE_RESEARCH` with archived location; backlog lists `BACKLOG_REVIEW` / deprioritized. This audit does not retire or reclassify FR-063.

## Artifact-Contract Findings

- Shadow NAV append semantics were insufficiently governed for established-chain incremental refresh.
- Existing-date overwrites were possible without explicit restatement metadata.
- Scorecard health previously emphasized freshness and could miss fresh-but-corrupt NAV continuity failures.
- Consumers at risk from a corrupted `shadow_nav_series.csv`: Daily Model Scorecard, promotion readiness, strategy differentiation/correlation work, MCP/CIO brief surfaces, research review packets, and any model-retirement/promotion narrative using seven-day/YTD windows.

## Execution-Contract Findings

- `4b426a0` adds `execution_target_attainment` as read-only MCP diagnostic.
- `a1ddc68` adds post-buy timing and terminal-state telemetry.
- Targeted tests passed for target attainment, execution lifecycle timeline, broker-authoritative phase 4 fixtures, execution pipeline hardening, reconciliation, and payload audit.
- No evidence found that recent execution work changed broker submission, order sizing, allocation, model logic, strategy logic, or cron behavior.
