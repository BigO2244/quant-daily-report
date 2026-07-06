# Caerus Knowledge Graph

Status: architecture-pack subsystem map draft
Last reviewed: 2026-06-26

Canonical status: Needs Repository Verification until this architecture pack is
committed and reconciled with `origin/main`.

This graph links subsystems to primary documentation, implementation, tests,
generated artifacts, FRs, and known gaps. It is a routing aid, not a duplicate
of canonical documents.

For fast orientation, read `docs/architecture/SYSTEM_MAP.md` first. This file is
the expanded graph for agents and engineers who need implementation, test,
artifact, and FR pointers in one place.

## Document Contract

| Field | Value |
|---|---|
| Purpose | Map relationships between Caerus subsystems, docs, implementation, tests, artifacts, FRs, and gaps. |
| Owner | Not named in repository; architecture/governance ownership requires repository verification. |
| Inputs | `SYSTEM_MAP.md`, `DOCUMENT_INDEX.md`, repository docs, tests, implementation paths, artifact patterns. |
| Outputs | Relationship graph for future engineers and AI agents. |
| Related Documents | `docs/architecture/SYSTEM_MAP.md`, `docs/architecture/DOCUMENT_INDEX.md`, `docs/architecture/DOCUMENT_GAPS.md`. |
| Related Tests | Tests listed per subsystem below. |
| Related Implementation | Implementation paths listed per subsystem below. |
| Related Artifacts | Artifact families listed per subsystem below. |
| Known Gaps | Graph status follows repository verification caveats in `DOCUMENT_GAPS.md`. |

## System Navigation Summary

| Question | First document | Then verify with |
|---|---|---|
| How does Caerus work end to end? | `docs/architecture/SYSTEM_MAP.md` | `docs/architecture/CAERUS_TECHNICAL_ARCHITECTURE_AND_OPERATING_MANUAL.md` |
| Which source is authoritative? | `docs/architecture/DOCUMENT_INDEX.md` | Canonical source document named in the index. |
| What code/tests/artifacts support a subsystem? | `docs/architecture/KNOWLEDGE_GRAPH.md` | Implementation files and tests listed below. |
| What remains unverified? | `docs/architecture/DOCUMENT_GAPS.md` | Current `git status`, run bundles, and canonical governance docs. |
| What does a term mean? | `docs/architecture/GLOSSARY.md` | Execution, broker, and governance source docs. |
| How do I update architecture docs? | `docs/architecture/CONTRIBUTING_ARCHITECTURE.md` | `docs/architecture/DOCUMENTATION_GOVERNANCE.md`. |
| Where is a decision recorded? | `docs/architecture/ENGINEERING_DECISION_INDEX.md` | FR registry, roadmap, and source docs. |

## Relationship Flow

```text
Investment Doctrine
  -> Research Registry
  -> Research Sleeves
  -> Portfolio Construction
  -> Execution Planning
  -> Broker Integration
  -> Reconciliation
  -> Reporting / Email
  -> Dashboard
  -> Operator
```

Cross-cutting controls:

- Governance gates strategy, FR state, promotion, and runtime permission.
- Scheduler and infrastructure run the daily phases and deployment surfaces.
- Testing encodes behavioral expectations but does not replace broker or VM
  proof.
- AI workflow preserves scope, evidence discipline, and dirty-worktree safety.

## Node Relationships

| From | To | Relationship | Evidence |
|---|---|---|---|
| Investment Doctrine | Research Registry | Doctrine defines what research should prove before promotion. | `docs/governance/caerus_investment_doctrine.md`, `docs/governance/CURRENT_RESEARCH_ROADMAP.md`. |
| Research Registry | Research Sleeves | Registry and MCP surfaces expose sleeve evidence and readiness. | `research_registry/`, FR-069 docs. |
| Research Sleeves | Portfolio Construction | Sleeve evidence can inform target intent only through governed promotion/allocation paths. | FR-069, doctrine, allocation tests. |
| Portfolio Construction | Execution Planning | Targets and planned payloads become executable candidates only after validation and filters. | `outputs/precompute/<date>/planned_execution_payload.json`, `docs/execution_contract.md`. |
| Execution Planning | Broker Integration | Intended/submitted orders bridge model intent to Alpaca. | `paper/paper_broker.py`, broker artifacts. |
| Broker Integration | Reconciliation | Broker state is refreshed and reconciled after submission/fill observation. | Broker model, execution integrity contract. |
| Reconciliation | Reporting / Email | Reconciliation and reliability state feed summaries and operator email. | execution summary/email docs and tests. |
| Reporting / Email | Dashboard | Generated summaries and payloads inform static dashboard/operator views. | dashboard source map and web payloads. |
| Dashboard | Operator | Operator consumes status, warnings, blockers, and evidence links. | dashboard docs, runbooks. |

| Subsystem | Primary documentation | Implementation files | Behavioral tests | Generated artifacts | Related FRs | Known gaps |
|---|---|---|---|---|---|---|
| Investment Doctrine | `docs/governance/caerus_investment_doctrine.md`, `docs/governance/CURRENT_RESEARCH_ROADMAP.md` | `config/research/strategy_registry.json` | `Tests/test_strategy_registry.py`, `Tests/test_promotion_governance.py` | `outputs/research/**` | FR-063, FR-068, FR-069, FR-105 | Strategy status must defer to current roadmap and registry when older alpha docs disagree. |
| Research | `docs/research_review_packet.md`, `docs/research_source_readiness.md`, roadmap FR rows | `research/`, `research_registry/research/`, `scripts/research/` | `Tests/test_research_registry_*.py`, `Tests/test_phoenix_*.py`, `Tests/test_cassiopeia_*.py`, `Tests/test_argo_*.py` | `outputs/research/**`, `reports/*.md` | FR-050 through FR-069, FR-DH, FR-105 | Generated research reports are evidence, not production authorization. |
| Research Data Platform | `docs/governance/fr_active/data_hydration/fr_dh_000_data_hydration_index.md`, `docs/governance/fr_active/data_hydration/fr_dh_013_canonical_research_data_catalog.md`, `docs/architecture/research_data_platform.md` | `research_data/`, `scripts/data_hydration/` | `Tests/test_data_hydration_*.py`, `Tests/test_sleeve_migration_readiness.py`, `Tests/test_sleeve_parity.py` | `outputs/data_trust/`, data manifests, hydration outputs | FR-DH, FR-068, FR-069 | Observe-only; production sleeve migration requires separate approval and repository verification. |
| Sleeve Lifecycle | `docs/governance/fr_active/fr_069_research_lab_modular_sleeve_architecture.md`, `docs/alpha_stack/README.md` | `research_registry/sleeves/`, `sleeves/`, `daily_quant_report.py` | `Tests/test_sleeve_manifest.py`, `Tests/test_sleeve_evidence.py`, `Tests/test_sleeve_migration_readiness.py` | `outputs/research/*sleeve*`, `outputs/shadow_candidates/` | FR-069, FR-082, FR-DH | Phase C / production sleeve migration requires separate approval. |
| Portfolio Construction | `docs/governance/caerus_investment_doctrine.md`, `docs/Alpha_Stack_Architecture_Reference.md` | `core/portfolio_alloc.py`, `core/growth_engine_v4.py`; `research/fr105_*` is current-worktree evidence and Needs Repository Verification | `Tests/test_allocation.py`, `Tests/test_allocator_cash_drag_redistribution.py`; `Tests/test_fr105_*.py` is current-worktree evidence and Needs Repository Verification | `outputs/precompute/<date>/planned_execution_payload.json`; `outputs/research/fr_105/` is current-worktree evidence | FR-068, FR-069, FR-105 | Global optimizer is research-only; no allocator replacement is authorized. |
| Execution | `docs/execution_contract.md`, `docs/execution_integrity_contract.md`, `docs/execution_integrity_runbook.md` | `scripts/run_precomputed_alpaca_execution.py`, `paper/paper_broker.py`, `core/operational_invariants.py`; `core/candidate_trade_lifecycle.py` is current-worktree evidence and Needs Repository Verification | `Tests/test_run_precomputed_alpaca_execution*.py`, `Tests/test_execution_integrity.py`, `Tests/test_operational_invariants.py`; `Tests/test_candidate_trade_lifecycle.py` is current-worktree evidence and Needs Repository Verification | `outputs/runs/<run_id>/execution_payload.json`, `outputs/runs/<run_id>/execution_results.json`, `outputs/runs/<run_id>/audit/*` | FR-031, FR-070, FR-074, FR-105 | Candidate lifecycle is current-worktree evidence; runtime 8/2 broker bundle not found. |
| Broker | `specs/broker_authoritative_execution_model.md`, `docs/execution_integrity_runbook.md` | `brokers/alpaca_broker.py`, `brokers/alpaca_snapshot.py`, `paper/paper_broker.py`, `scripts/export_alpaca_broker_snapshot.py` | `Tests/test_broker_authoritative_phase3.py`, `Tests/test_broker_authoritative_phase4.py`, `Tests/test_export_alpaca_broker_snapshot.py`, `Tests/test_broker_reject_classification.py` | `outputs/runs/<run_id>/broker/`, `outputs/broker/` | FR-070, FR-080, FR-104 | Alpaca is broker truth; local artifacts can be stale unless refreshed from broker. |
| Reconciliation | `docs/execution_integrity_contract.md`, `docs/execution_integrity_runbook.md` | `reconciliation.py`, `core/execution_integrity.py`, `core/execution_lifecycle_timeline.py`, `research/target_attainment.py` | `Tests/test_reconciliation.py`, `Tests/test_recon_posttrade_refresh.py`, `Tests/test_target_attainment.py`, `Tests/test_execution_lifecycle_timeline.py` | `recon_posttrade_<date>.json`, `target_attainment_<date>.json`, `execution_timeline.*` | FR-031, FR-070, FR-074 | Broker reconciliation and target-attainment answer different questions. |
| Dashboard | `docs/dashboard_v1_source_map.md`, `docs/dashboard_refresh_spec.md`, `docs/quant_dashboard.md` | `scripts/research/build_dashboard_v1.py`, `scripts/research/build_quant_dashboard.py`, `scripts/refresh_quant_dashboard.py`, `web/dashboard/` | `Tests/test_build_dashboard_v1.py`, `Tests/test_build_quant_dashboard.py`, `Tests/test_dashboard_ui_status.py` | `web/dashboard/dashboard_data.json`, `web/dashboard/dashboard-data.json`, `web/dashboard/dashboard-data.js` | FR-065, FR-DH dashboard visibility items | Current served payload/schema should be verified before operational claims. |
| Email / Reporting | `docs/trading_email_governance.md`, `docs/execution_summary.md`, `docs/shadow_scoreboard_email.md` | `paper/build_execution_email.py`, `scripts/send_trading_confirmation_email.py`, `scripts/send_shadow_cio_report.py`, `core/execution_summary.py`, `core/trading_day_summary.py` | `Tests/test_execution_email.py`, `Tests/test_daily_trade_execution_email.py`, `Tests/test_email_nonblocking_execution.py`, `Tests/test_trading_day_summary.py` | `outputs/execution_email/`, `outputs/latest_execution_summary.txt`, `outputs/execution_history.csv` | FR-074, FR-105 | Lifecycle reporting fields are ahead of older docs in current worktree. |
| Governance | `docs/governance/README.md`, `docs/governance/fr_governance_model.md`, `docs/governance/fr_registry.md`, `docs/governance/fr_active_backlog.md` | `scripts/governance_hygiene_agent.py`, `core/documentation/` | `Tests/test_governance_hygiene_agent.py`, `Tests/test_documentation_governance.py` | `outputs/governance_hygiene/<date>/` | All FRs | Dirty registry/backlog changes need verification before remote-main claims. |
| Infrastructure | `AGENTS.md`, `docs/deployment_workflow.md`, `docs/OPERATIONS.md` | `scripts/deploy_dashboard_vm.sh`, `scripts/ops/run_vm_validation.sh`, deploy service files | `Tests/test_vm_validation_helper.py`, `Tests/test_cron_command_validation.py` | VM logs, dashboard served files, `/var/www/...` copies | Operational controls FRs | VM truth requires direct `ssh caerus-vm` validation. |
| Scheduler | `scripts/crontab.txt`, `docs/runbook.md` | `scripts/cron_precompute.sh`, `scripts/cron_execute.sh`, `scripts/cron_confirm.sh`, `scripts/cron_overnight.sh`, `scripts/cron_research.sh` | `Tests/test_daily_alpaca_workflow_schedule.py`, `Tests/test_cron_command_validation.py`, `Tests/test_cron_confirm.py` | `logs/cron_*.log`, `outputs/workflow/<date>/` | FR-066, FR-070, FR-DH | GitHub paper workflow is deprecated; VM cron is current scheduler evidence. |
| Testing | `AGENTS.md`, `docs/validation_isolation_policy.md`, `docs/operational_validation.md` | `Tests/`, `.venv/` test runner conventions | All `Tests/test_*.py` | pytest cache and generated fixtures | Cross-cutting | Tests are behavioral docs but may create cache/temp files. |
| AI Agent Workflow | `AGENTS.md`, `docs/governance/ORCHESTRATOR_CONTEXT.md`, `docs/governance/AI_ORCHESTRATION_MODEL.md`, `docs/governance/CODEX_TASK_TEMPLATE.md` | `aiops/`, `scripts/update_agents_md.py` | `Tests/test_aiops_*.py`, `Tests/test_update_agents_md.py` | `reports/agent_loops/`, `outputs/research_mcp/questions/` | Governance and AIOps FRs | Agents must preserve runtime boundaries unless explicitly authorized. |
