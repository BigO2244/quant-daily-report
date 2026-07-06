# Caerus System Map

Status: architecture-pack navigation draft
Scope: documentation only
Last reviewed: 2026-06-26

Canonical status: Needs Repository Verification until this architecture pack is
committed and reconciled with `origin/main`.

This map is the shortest route through the Caerus system. It identifies the
current subsystem boundaries, the primary documentation entry points, and the
execution-state vocabulary used by the architecture pack.

Use this file for orientation. Use `docs/architecture/DOCUMENT_INDEX.md` for
source-of-truth routing, `docs/architecture/KNOWLEDGE_GRAPH.md` for full
subsystem links, and
`docs/architecture/CAERUS_TECHNICAL_ARCHITECTURE_AND_OPERATING_MANUAL.md` for
operating context.

## Document Contract

| Field | Value |
|---|---|
| Purpose | Map Caerus subsystems to purpose, implementation, docs, tests, artifacts, FRs, and gaps. |
| Owner | Not named in repository; architecture/governance ownership requires repository verification. |
| Inputs | `DOCUMENT_INDEX.md`, `KNOWLEDGE_GRAPH.md`, governance docs, runbooks, implementation/test discovery. |
| Outputs | Single-system navigation map for engineers and AI agents. |
| Related Documents | `docs/architecture/DOCUMENT_INDEX.md`, `docs/architecture/KNOWLEDGE_GRAPH.md`, `docs/architecture/GLOSSARY.md`. |
| Related Tests | Tests listed per subsystem below. |
| Related Implementation | Implementation files listed per subsystem below. |
| Related Artifacts | Artifact families listed per subsystem below. |
| Known Gaps | Dirty-worktree and current-worktree evidence is marked `Needs Repository Verification`. |

## Architecture Reading Path

1. `AGENTS.md`
2. `docs/governance/ORCHESTRATOR_CONTEXT.md`
3. `docs/architecture/README.md`
4. `docs/architecture/SYSTEM_MAP.md`
5. `docs/architecture/DOCUMENT_INDEX.md`
6. `docs/architecture/CAERUS_TECHNICAL_ARCHITECTURE_AND_OPERATING_MANUAL.md`
7. `docs/architecture/ARCHITECTURE_PRINCIPLES.md`
8. `docs/architecture/KNOWLEDGE_GRAPH.md`
9. `docs/architecture/GLOSSARY.md`
10. `docs/architecture/OPERATOR_RUNBOOK.md`
11. `docs/architecture/ENGINEERING_DECISION_INDEX.md`
12. `docs/architecture/DOCUMENT_GAPS.md`

For FR status, stop at `docs/governance/fr_registry.md`. Architecture docs link
FRs but do not replace the registry.

## Subsystem Navigation

| Subsystem | Purpose | Implementation | Documentation | Tests | Artifacts | FRs | Known gaps |
|---|---|---|---|---|---|---|---|
| Investment Doctrine | Define objective, promotion rules, sleeve philosophy, and allocation doctrine. | `config/research/strategy_registry.json`, allocation consumers | `docs/governance/caerus_investment_doctrine.md`, `docs/governance/CURRENT_RESEARCH_ROADMAP.md` | `Tests/test_strategy_registry.py`, `Tests/test_promotion_governance.py` | `outputs/research/**` | FR-063, FR-068, FR-069, FR-105 | Dirty roadmap/registry state needs verification before remote-main claims. |
| Research Registry | Index research artifacts and answer read-only research/operator questions. | `research_registry/`, `scripts/research_registry_cli.py`, MCP server files | `docs/architecture/semantics/README.md`, `docs/architecture/caerus_research_mcp_architecture.md`, `docs/operator/research_mcp_operator_guide.md` | `Tests/test_research_registry_*.py`, `Tests/test_research_registry_mcp_server.py` | `outputs/research_mcp/questions/`, registry outputs | FR-036, FR-069, FR-DH | Point-in-time MCP docs may lag current implementation. |
| Research Data Platform | Provide canonical research-data catalog, hydration, freshness, normalization, and observe-only migration evidence. | `research_data/`, `scripts/data_hydration/` | `docs/governance/fr_active/data_hydration/fr_dh_000_data_hydration_index.md`, `docs/governance/fr_active/data_hydration/fr_dh_013_canonical_research_data_catalog.md`, `docs/architecture/research_data_platform.md` | `Tests/test_data_hydration_*.py`, `Tests/test_sleeve_migration_readiness.py`, `Tests/test_sleeve_parity.py` | `outputs/data_trust/`, data manifests, hydration outputs | FR-DH, FR-068, FR-069 | Observe-only; no production sleeve consumer migration without approval. |
| Research Sleeves | Govern research-stage sleeve manifests, evidence, and migration readiness. | `research_registry/sleeves/`, `sleeves/`, `daily_quant_report.py` | `docs/governance/fr_active/fr_069_research_lab_modular_sleeve_architecture.md`, `docs/alpha_stack/README.md` | `Tests/test_sleeve_manifest.py`, `Tests/test_sleeve_evidence.py`, `Tests/test_sleeve_migration_readiness.py` | `outputs/research/*sleeve*`, `outputs/shadow_candidates/` | FR-069, FR-082, FR-DH | Production sleeve migration requires separate approval. |
| Portfolio Construction | Convert doctrine and sleeve evidence into target portfolio intent. | `core/portfolio_alloc.py`, `core/growth_engine_v4.py`; `research/fr105_*` current-worktree evidence | `docs/governance/caerus_investment_doctrine.md`, `docs/Alpha_Stack_Architecture_Reference.md`, `docs/governance/fr_active/fr_105_global_portfolio_optimizer_and_decision_provenance.md` | `Tests/test_allocation.py`, `Tests/test_allocator_cash_drag_redistribution.py`, `Tests/test_fr105_*.py` current-worktree evidence | `outputs/precompute/<date>/planned_execution_payload.json`, `outputs/research/fr_105/` | FR-068, FR-069, FR-105 | FR-105 is research-only and Needs Repository Verification. |
| Execution Planning | Validate planned payloads and produce planned/intended execution surfaces. | `scripts/run_precomputed_alpaca_execution.py`, `paper/paper_broker.py`, `core/operational_invariants.py`; `core/candidate_trade_lifecycle.py` current-worktree evidence | `docs/execution_contract.md`, `docs/execution_integrity_contract.md`, `docs/execution_integrity_runbook.md` | `Tests/test_run_precomputed_alpaca_execution*.py`, `Tests/test_execution_integrity.py`, `Tests/test_operational_invariants.py`, `Tests/test_candidate_trade_lifecycle.py` current-worktree evidence | `outputs/precompute/<date>/`, `outputs/runs/<run_id>/execution_payload.json`, `outputs/runs/<run_id>/audit/*` | FR-031, FR-070, FR-074, FR-105 | June 25 8/2 case is fixture-only; no broker run bundle found. |
| Broker Integration | Submit and observe orders while preserving broker-authoritative truth. | `brokers/alpaca_broker.py`, `brokers/alpaca_snapshot.py`, `paper/paper_broker.py`, `scripts/export_alpaca_broker_snapshot.py` | `specs/broker_authoritative_execution_model.md`, `docs/execution_integrity_runbook.md` | `Tests/test_broker_authoritative_phase3.py`, `Tests/test_broker_authoritative_phase4.py`, `Tests/test_broker_reject_classification.py` | `outputs/runs/<run_id>/broker/`, `outputs/broker/` | FR-070, FR-080, FR-104 | Broker API truth must be queried when local artifacts disagree. |
| Reconciliation | Compare intended, expected, broker, and target state without collapsing questions. | `reconciliation.py`, `core/execution_integrity.py`, `core/execution_lifecycle_timeline.py`, `research/target_attainment.py` | `docs/execution_integrity_contract.md`, `docs/execution_integrity_runbook.md` | `Tests/test_reconciliation.py`, `Tests/test_recon_posttrade_refresh.py`, `Tests/test_target_attainment.py`, `Tests/test_execution_lifecycle_timeline.py` | `recon_posttrade_<date>.json`, `target_attainment_<date>.json`, `execution_timeline.*` | FR-031, FR-070, FR-074 | `OK_RECONCILED` does not prove target attainment. |
| Dashboard | Present operator-visible state through static dashboard files. | `scripts/research/build_dashboard_v1.py`, `scripts/research/build_quant_dashboard.py`, `scripts/refresh_quant_dashboard.py`, `web/dashboard/` | `docs/dashboard_v1_source_map.md`, `docs/dashboard_refresh_spec.md`, `docs/quant_dashboard.md` | `Tests/test_build_dashboard_v1.py`, `Tests/test_build_quant_dashboard.py`, `Tests/test_dashboard_ui_status.py` | `web/dashboard/dashboard_data.json`, `web/dashboard/dashboard-data.json`, `web/dashboard/dashboard-data.js` | FR-065, FR-DH dashboard visibility items, FR-104 dashboard surfaces | Served payload/schema needs build and VM verification. |
| Reporting | Summarize execution, trading day, research, and shadow evidence. | `core/execution_summary.py`, `core/trading_day_summary.py`, dashboard/report builders | `docs/execution_summary.md`, `docs/shadow_scoreboard_email.md`, dashboard docs | `Tests/test_execution_summary.py`, `Tests/test_trading_day_summary.py`, report tests | `outputs/latest_execution_summary.txt`, `outputs/execution_history.csv`, research reports | FR-074, FR-105 | Lifecycle reporting fields are ahead of older docs in current worktree. |
| Email | Generate and send operator-facing email summaries. | `paper/build_execution_email.py`, `scripts/send_trading_confirmation_email.py`, `scripts/send_shadow_cio_report.py` | `docs/trading_email_governance.md`, `docs/shadow_scoreboard_email.md` | `Tests/test_execution_email.py`, `Tests/test_daily_trade_execution_email.py`, `Tests/test_email_nonblocking_execution.py` | `outputs/execution_email/`, shadow CIO report outputs | FR-074, FR-105 | Email docs need lifecycle-field alignment after commit. |
| Governance | Control strategy, FR status, backlog, escalation, and doctrine. | `scripts/governance_hygiene_agent.py`, `core/documentation/` | `docs/governance/README.md`, `docs/governance/fr_governance_model.md`, `docs/governance/fr_registry.md`, `docs/governance/fr_active_backlog.md` | `Tests/test_governance_hygiene_agent.py`, `Tests/test_documentation_governance.py` | `outputs/governance_hygiene/<date>/` | All FRs | Local registry/backlog edits need repository verification. |
| Scheduler | Run daily phases on VM cron. | `scripts/cron_precompute.sh`, `scripts/cron_execute.sh`, `scripts/cron_confirm.sh`, `scripts/cron_overnight.sh`, `scripts/cron_research.sh` | `scripts/crontab.txt`, `docs/runbook.md` | `Tests/test_daily_alpaca_workflow_schedule.py`, `Tests/test_cron_command_validation.py`, `Tests/test_cron_confirm.py` | `logs/cron_*.log`, `outputs/workflow/<date>/` | FR-066, FR-070, FR-DH | GitHub paper workflow is deprecated; VM install still needs live verification. |
| Infrastructure | Deploy and validate VM/dashboard/runtime surfaces. | `scripts/deploy_dashboard_vm.sh`, `scripts/ops/run_vm_validation.sh`, deploy service files | `AGENTS.md`, `docs/deployment_workflow.md`, `docs/OPERATIONS.md` | `Tests/test_vm_validation_helper.py`, `Tests/test_cron_command_validation.py` | VM logs, served files, `/var/www/...` copies | Operational controls FRs | VM truth requires direct `ssh caerus-vm` validation. |
| Testing | Encode expected behavior and guard against regressions. | `Tests/`, repo venv conventions | `AGENTS.md`, `docs/validation_isolation_policy.md`, `docs/operational_validation.md` | All `Tests/test_*.py` | pytest cache and synthetic fixtures | Cross-cutting | Tests are behavioral docs, not broker/runtime proof by themselves. |
| AI Workflow | Keep AI-assisted work scoped, evidence-backed, and safe. | `aiops/`, `scripts/update_agents_md.py` | `AGENTS.md`, `docs/governance/ORCHESTRATOR_CONTEXT.md`, `docs/governance/AI_ORCHESTRATION_MODEL.md`, `docs/governance/CODEX_TASK_TEMPLATE.md` | `Tests/test_aiops_*.py`, `Tests/test_update_agents_md.py` | `reports/agent_loops/`, `outputs/research_mcp/questions/` | Governance and AIOps FRs | Agents must preserve explicit scope and dirty-worktree boundaries. |

## Vocabulary

Use `docs/architecture/GLOSSARY.md` for shared terms. The terms
`recommendation`, `candidate`, `planned`, `submitted`, `accepted`, `filled`,
`partially filled`, `clipped`, `suppressed`, `rejected`, `reconciled`, and
`broker-authoritative` are architecture-pack controlled vocabulary.

## June 25 8 Planned / 2 Submitted Regression Fixture

The 8 planned / 2 submitted case is owned by the execution lifecycle section in
`docs/architecture/CAERUS_TECHNICAL_ARCHITECTURE_AND_OPERATING_MANUAL.md` and
summarized in `docs/architecture/GLOSSARY.md`. It is a deterministic
regression fixture with `trade_date=2026-06-25` and
`run_id=2026-06-25T093508-0400_7b9af94` in
`Tests/test_candidate_trade_lifecycle.py`; it is not broker-authoritative
runtime history.

## Authoritative References

- `docs/architecture/DOCUMENT_INDEX.md`
- `docs/architecture/KNOWLEDGE_GRAPH.md`
- `docs/architecture/GLOSSARY.md`
- `docs/architecture/DOCUMENT_GAPS.md`
