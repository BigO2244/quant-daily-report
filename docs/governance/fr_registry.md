# FR Registry

## Purpose

This registry is the canonical historical record for Caerus Friday Refactor
(FR) work. It preserves deployed history, reviewed deferred items, rollback
references, validation summaries, and final or current operational state.

Active upcoming work belongs in `docs/governance/fr_active_backlog.md`.
Methodology belongs in `docs/governance/fr_governance_model.md`.

## Wave Summary

| Phase | Date | FRs | Status | Operational Theme | Observation Focus |
|---|---|---|---|---|---|
| Wave 1 | 2026-05-15 | FR-004, FR-006, FR-009, FR-011, FR-013 | Mixed: deployed | Reporting resilience and CI governance. | Dependabot noise, pinned action availability, report artifact continuity. |
| Wave 2 | 2026-05-15 | FR-001, FR-012 | `DEPLOYED_OBSERVING` | Shadow orchestration observability and cache namespace isolation. | Shadow step status artifacts and expected first-run cache misses. |
| Wave 3 | 2026-05-15 | FR-005 | `DEPLOYED_OBSERVING` | Recovery integrity and degraded-state fail-closed behavior. | Self-heal recovery artifacts, bundle validation failures, repeated recovery attempts. |
| Phase 4 foundations | 2026-05-22 | FR-015, FR-017, FR-018 | `DEPLOYED` | Artifact governance, ownership, health, and freshness semantics. | Docs-only semantics; no runtime producers changed. |
| Phase 4 retention | 2026-05-26 | FR-019 | `DEPLOYED` | Runtime artifact retention and backup policy. | Docs-only policy; no cleanup automation or artifact mutation. |
| Phase 4 validation isolation | 2026-05-26 | FR-020 | `DEPLOYED` | Read-only validation isolation policy. | Docs-only policy; no test harness or runtime path changes. |
| Research clarity wave | 2026-05-22 | FR-023, FR-024, FR-025, FR-026, FR-027 | `DEPLOYED` | Generated/canonical separation, NAV provenance, portfolio memory, exposure intelligence, and regime fragility. | Additive generated research artifacts; no execution/accounting/timing changes. |
| Research operations | 2026-05-22 onward | FR-030 | `DEPLOYED` | Daily research interpretation packet and Orion operator launcher. | Packet source readiness, freshness, confidence, and incomplete-source handling. |

## Deployed FRs

| FR | Phase | Status | Blast Radius | Introduced | Current State | Rollback Reference |
|---|---|---|---|---|---|---|
| FR-004 feedback-loop rolling index | Wave 1 | `DEPLOYED` | LOW | 2026-05-15 | Compact learning/performance rows are additive; dated JSON remains canonical. | Revert reporting commit or stop reading/writing additive index. |
| FR-006 required vs optional artifact health | Wave 1 | `DEPLOYED` | LOW | 2026-05-15 | Optional learning artifacts remain visible without blocking core scoreboard. | Revert artifact-health classification changes. |
| FR-008 git/VM deployment governance | Governance recovery | `DEPLOYED` | HIGH | 2026-05-08 | `origin/main` is canonical; VM fast-forwards from git; SCP is exception-only. | Prefer `git revert`, push, VM fast-forward; preserve drift evidence. |
| FR-009 GitHub Actions SHA pinning | Wave 1 | `DEPLOYED` | MEDIUM | 2026-05-15 | Workflow `uses:` references are pinned to immutable SHAs. | Revert pinned SHA refs only if a SHA is invalid or unavailable. |
| FR-011 workflow permission minimization | Wave 1 | `DEPLOYED` | MEDIUM | 2026-05-15 | Workflow-scope `contents: write` removed; job-level elevation remains where needed. | Restore prior permission blocks only for confirmed permission failures. |
| FR-013 dependency monitoring governance | Wave 1 | `DEPLOYED` | LOW | 2026-05-15 | Dependabot monitors pip and GitHub Actions without auto-merge. | Disable or remove Dependabot config if advisory noise is unacceptable. |
| FR-015 artifact registry and ownership matrix | Phase 4 foundations | `DEPLOYED` | LOW | 2026-05-22 | Artifact ownership, producer/consumer semantics, freshness expectations, truth surfaces, and confidence expectations are documented in operator-readable form. | Revert docs-only commit `01e3749` or ignore registry docs. |
| FR-017 operational health aggregator | Phase 4 foundations | `DEPLOYED` | LOW | 2026-05-22 | Health aggregation semantics, degraded-state vocabulary, and operator examples are documented as read-only interpretation layers. | Revert docs-only commit `01e3749` or ignore health docs. |
| FR-018 latest publication freshness manifest | Phase 4 foundations | `DEPLOYED` | LOW | 2026-05-22 | Freshness, latest-publication, stale-state, and confidence interpretation rules are documented; no runtime enforcement was added. | Revert docs-only commit `01e3749` or ignore freshness docs. |
| FR-019 runtime artifact retention and backup policy | Phase 4 retention | `DEPLOYED` | LOW | 2026-05-26 | Retention classes, backup boundaries, evidence holds, and future cleanup automation requirements are documented. No cleanup automation or artifact mutation was added. | Revert the FR-019 docs commit or ignore `docs/artifact_retention_policy.md`. |
| FR-020 read-only validation isolation | Phase 4 validation isolation | `DEPLOYED` | LOW | 2026-05-26 | Validation output classes, bounded smoke-output rules, read-only diagnostic rules, and future test harness requirements are documented. No test harness, runtime producer, or cleanup behavior changed. | Revert the FR-020 docs commit or ignore `docs/validation_isolation_policy.md`. |
| FR-023 documentation and generated artifact separation | Research clarity wave | `DEPLOYED` | LOW | 2026-05-22 | Generated-vs-canonical documentation taxonomy is documented and referenced by research clarity outputs. | Revert commit `ab1dd95` if generated/canonical guidance must be removed. |
| FR-024 NAV surface registry and performance provenance enforcement | Research clarity wave | `DEPLOYED` | LOW | 2026-05-22 | Additive research clarity artifacts classify broker, operational shadow, research backtest, comparison, and latest/convenience surfaces. | Stop running `scripts/research/build_research_clarity_wave.py` and ignore generated registry outputs. |
| FR-025 immutable daily shadow holdings and weights history | Research clarity wave | `DEPLOYED` | MEDIUM | 2026-05-22 | Dated holdings, weights, exposure, rebalance delta, and manifest evidence support future attribution and replay-safe interpretation. | Stop writing new snapshots; preserve existing immutable evidence. |
| FR-026 exposure intelligence and concentration risk observability | Research clarity wave | `DEPLOYED` | LOW | 2026-05-22 | Exposure summaries, concentration monitor, drift summaries, and risk flags improve operator visibility into sector, concentration, turnover, and momentum sensitivity. | Stop publishing exposure artifacts and ignore generated outputs. |
| FR-027 regime decomposition and fragility reporting | Research clarity wave | `DEPLOYED` | LOW | 2026-05-22 | Advisory regime performance, fragility, exposure matrix, and attribution-by-regime outputs are generated without promotion or execution coupling. | Stop publishing regime artifacts and ignore generated outputs. |
| FR-030 daily research interpretation packet v1 | Research operations | `DEPLOYED` | LOW | 2026-05-23 | Daily packet builder publishes `packet.md`, `packet.html`, `packet.json`, and `summary.json` as advisory telemetry consumption; follow-up patches improved readability, data completeness, source-readiness guardrails, and incomplete-source guidance. | Stop running `scripts/research/build_daily_research_packet.py`; ignore generated packet outputs. |

## Deployed Observing FRs

| FR | Phase | Status | Blast Radius | Observation Status | Observation Criteria | Current State | Rollback Reference |
|---|---|---|---|---|---|---|---|
| FR-001 shadow wrapper decomposition | Wave 2 | `DEPLOYED_OBSERVING` | MEDIUM | observing | Shadow generate/latest/reconciliation step artifacts appear as expected; no shadow failure blocks trading. | Shadow remains non-blocking and now writes step status artifacts. | Revert wrapper decomposition commit to restore prior inline wrapper. |
| FR-005 self-heal recovery integrity | Wave 3 | `DEPLOYED_OBSERVING` | HIGH | observing | Self-heal artifacts reflect continuation or fail-closed halt; repeated recovery attempts remain visible; bundle validation gates execution. | Execution requires full bundle validation; partial self-heal fails closed. | Revert FR-005 commit; preserve existing recovery artifacts as evidence. |
| FR-012 CI cache namespace isolation | Wave 2 | `DEPLOYED_OBSERVING` | MEDIUM | observing | Repository-scoped cache misses/regeneration behave as expected; no workflow instability from namespace migration. | Cache keys include `github.repository_id`; first post-deploy misses may be expected. | Revert cache key namespace commit if cache misses cause unacceptable instability. |

Do not mark an observing FR `DEPLOYED` until evidence satisfies its observation
criteria. If evidence is unavailable, leave the FR observing.

## Reviewed Deferred FRs

| FR | Phase | Status | Blast Radius | Current State | Deferred Rationale | Re-entry Criteria |
|---|---|---|---|---|---|---|
| FR-003 managed ticker exceptions | Pre-Wave review | `REVIEWED_DEFERRED` | MEDIUM | Local WIP only; not deployed through Waves 1-3. | Needs isolated promotion package and hydration validation. | Dry-run hydration, targeted tests, artifact-only rollout plan. |
| FR-007 parquet scaling review | Wave 1 review | `REVIEWED_DEFERRED` | LOW | Advisory review only; single parquet remains canonical. | Runtime pressure does not justify storage migration yet. | Repeated memory/runtime pressure or coverage-sidecar insufficiency. |
| FR-010 deterministic dependency governance | Wave 1 review | `REVIEWED_DEFERRED` | MEDIUM | Advisory dependency docs/inputs exist; hash enforcement not promoted. | Premature enforcement risks VM/GitHub install failures. | Clean install validation, APScheduler decision, rollback path, advisory audit. |
| FR-022 dependency hash enforcement | Phase 4 | `REVIEWED_DEFERRED` | MEDIUM | Future extension of FR-010. | Hash enforcement should wait until dependency baselines and emergency update procedure are proven. | Same as FR-010 plus workflow install policy decision. |

## Historical Execution Notes

| Date | FR | Validation Summary | Docs / Evidence | Notes |
|---|---|---|---|---|
| 2026-05-08 | FR-008 | VM backup, stash, fetch, fast-forward, status/log review, shell syntax. | `AGENTS.md`, deployment workflow, operations docs, runbook. | Restored deterministic git-based VM deployment. |
| 2026-05-15 | FR-004, FR-006 | `Tests/test_feedback_loop_artifacts.py`, `Tests/test_portfolio_learning_report.py`, operational validation. | Feedback loop docs and FR registry. | Reporting-only and additive. |
| 2026-05-15 | FR-009, FR-011, FR-013 | Workflow YAML parse, permission validation, Dependabot config review, operational validation. | Operational validation docs and FR registry. | CI governance hardened without workflow auto-merge. |
| 2026-05-15 | FR-001 | Shadow wrapper tests, execution integration tests, shell syntax, local shadow smoke. | Operations docs and FR registry. | Decomposed non-blocking shadow observability. |
| 2026-05-15 | FR-012 | Workflow YAML/cache key review, operational validation. | Deployment docs and FR registry. | Repository-scoped cache keys added. |
| 2026-05-15 | FR-005 | Execution integration tests, bundle validation tests, shell syntax, degraded-state simulations. | Operations docs, runbook, and FR registry. | Self-heal now fails closed unless full bundle validation passes. |
| 2026-05-22 | FR-015, FR-017, FR-018 | Markdown review and governance consistency review. | `docs/artifact_registry.md`, `docs/artifact_ownership_matrix.md`, `docs/operational_health_model.md`, `docs/operational_health_examples.md`, `docs/freshness_semantics.md`, `docs/freshness_examples.md`. | Governance and telemetry semantics only; no runtime producers changed. |
| 2026-05-26 | FR-019 | Markdown review, governance consistency review, and diff check. | `docs/artifact_retention_policy.md`, `docs/artifact_governance.md`, active backlog and registry updates. | Docs-only retention and backup policy; no cleanup automation, runtime producers, cron, broker, or execution behavior changed. |
| 2026-05-26 | FR-020 | Markdown review, governance consistency review, and diff check. | `docs/validation_isolation_policy.md`, `docs/artifact_governance.md`, `docs/artifact_ownership_matrix.md`, active backlog and registry updates. | Docs-only validation isolation policy; no test harness, runtime producers, cron, broker, or execution behavior changed. |
| 2026-05-22 | FR-023, FR-024, FR-025, FR-026, FR-027 | `Tests/test_research_clarity_wave.py`, py_compile, diff check, bounded `/private/tmp` artifact validation. | `docs/research_clarity_wave.md`, `docs/documentation_taxonomy.md`, generated research clarity artifacts. | Additive research telemetry wave; no accounting, timing, broker, cron, dashboard, or promotion changes. |
| 2026-05-23 | FR-030 | `Tests/test_daily_research_packet.py`, `Tests/test_research_clarity_wave.py`, py_compile, diff check, bounded packet generation. | `docs/daily_research_packet.md`, `docs/operator_review_workflow.md`, packet outputs under `outputs/research_packets/<DATE>/`. | Daily packet is telemetry consumption only, not promotion logic or execution automation. |
| 2026-05-23 onward | FR-030 follow-ups | Targeted packet tests, Orion launcher preflight tests, py_compile, shell syntax, Orion no-open validation. | `scripts/open_shadow_comparison_latest.command`, `scripts/research/build_daily_research_packet.py`, packet docs. | Orion.command became the primary FR-030 review launcher; source-readiness guardrails block misleading incomplete packets by default. |
| 2026-05-26 | Source readiness observability | Source-readiness tests, hydration-health tests, Orion preflight tests, py_compile, shell syntax, VM diagnostics. | `scripts/research/check_research_source_readiness.py`, `scripts/research/check_price_hydration_health.py`, `docs/research_source_readiness.md`, `docs/price_hydration_health.md`. | Read-only diagnostics distinguish `READY`, `INCOMPLETE`, `waiting_for_post_close`, `stale_but_recoverable`, `partial`, and `structurally_broken`; no hydration, cron, broker, execution, or workflow behavior changed. |
| 2026-05-26 | Interpretation layer planning | Markdown review, governance consistency review, and diff check. | `docs/weekly_research_synthesis.md`, `docs/operator_review_workflow.md`, strategic backlog update. | Weekly CIO-style synthesis boundary documented as planning only; no generator, dashboard, cron, promotion, broker, execution, or workflow behavior changed. |

## Registry Rules

- Preserve rollback references even after an FR is fully deployed.
- Preserve deferred rationale; do not reclassify deferred work as active without
  a new readiness review.
- Do not invent observation results. Record only evidence that exists.
- Keep implementation detail concise; link to owning docs for long specs.
