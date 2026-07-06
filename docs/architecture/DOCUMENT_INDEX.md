# Caerus Document Index

Status: architecture-pack routing draft
Last reviewed: 2026-06-26

Canonical status: Needs Repository Verification until this architecture pack is
committed and reconciled with `origin/main`.

This index routes readers to the most authoritative existing document instead
of duplicating source material.

## Document Contract

| Field | Value |
|---|---|
| Purpose | Route readers from questions/subsystems to authoritative source documents. |
| Owner | Not named in repository; architecture/governance ownership requires repository verification. |
| Inputs | Repository docs, governance docs, runbooks, architecture pack, tests-as-docs. |
| Outputs | Source-of-truth routing tables for engineers and AI agents. |
| Related Documents | `docs/architecture/SYSTEM_MAP.md`, `docs/architecture/DOCUMENT_INVENTORY.md`, `docs/architecture/DOCUMENTATION_GOVERNANCE.md`. |
| Related Tests | Tests listed in the tests-as-behavioral-documentation section. |
| Related Implementation | Implementation paths listed by subsystem. |
| Related Artifacts | Generated artifact patterns listed below. |
| Known Gaps | Dirty-worktree evidence and stale docs are routed to `DOCUMENT_GAPS.md`. |

## Architecture Pack Entry Points

| Need | Use | Role |
|---|---|---|
| First stop for this pack | `docs/architecture/README.md` | Describes the architecture pack and authority order. |
| Fast single-system orientation | `docs/architecture/SYSTEM_MAP.md` | Maps subsystems, primary entry points, protected surfaces, and execution vocabulary. |
| Routing to canonical docs | `docs/architecture/DOCUMENT_INDEX.md` | Selects the right source-of-truth document; the linked document remains authoritative. |
| Operating explanation | `docs/architecture/CAERUS_TECHNICAL_ARCHITECTURE_AND_OPERATING_MANUAL.md` | Explains system behavior while linking back to source documents. |
| Detailed subsystem graph | `docs/architecture/KNOWLEDGE_GRAPH.md` | Links docs, code, tests, generated artifacts, FRs, and gaps. |
| Full catalog | `docs/architecture/DOCUMENT_INVENTORY.md` | Captures status and caveats for docs, tests, and artifact patterns. |
| Open backlog | `docs/architecture/DOCUMENT_GAPS.md` | Lists verified unresolved documentation gaps. |
| Principles | `docs/architecture/ARCHITECTURE_PRINCIPLES.md` | Defines durable architecture principles and protected surfaces. |
| Vocabulary | `docs/architecture/GLOSSARY.md` | Owns shared architecture and execution terminology. |
| Contribution workflow | `docs/architecture/CONTRIBUTING_ARCHITECTURE.md` | Explains when and how to update architecture docs. |
| Documentation governance | `docs/architecture/DOCUMENTATION_GOVERNANCE.md` | Defines architecture-pack document contracts, status labels, and gap categories. |
| Operator routing | `docs/architecture/OPERATOR_RUNBOOK.md` | Routes operator questions to canonical runbooks and artifacts. |
| Engineering decisions | `docs/architecture/ENGINEERING_DECISION_INDEX.md` | Indexes durable decisions and evidence lanes. |

## Required First Reads

| Need | Canonical document | Notes |
|---|---|---|
| AI/Codex operating context | `AGENTS.md`, `docs/governance/ORCHESTRATOR_CONTEXT.md` | Read before scoped implementation or review work. |
| Strategic doctrine | `docs/governance/caerus_investment_doctrine.md` | Highest-level strategy, sleeve, promotion, and portfolio-construction doctrine. |
| Current research roadmap | `docs/governance/CURRENT_RESEARCH_ROADMAP.md` | Reconciliation/index layer for research state and blockers. |
| FR status | `docs/governance/fr_registry.md` | Authoritative FR status table. |
| Active work queue | `docs/governance/fr_active_backlog.md` | Prioritized work queue. |
| Governance process | `docs/governance/fr_governance_model.md`, `docs/governance/README.md` | Status semantics, file routing, and extension rules. |
| Architecture governance | `docs/architecture/DOCUMENTATION_GOVERNANCE.md` | Architecture-pack document standards and update rules. |
| Architecture decisions | `docs/architecture/ENGINEERING_DECISION_INDEX.md` | Decision routing; source FR docs remain authoritative. |

## Authoritative Subsystem Entry Points

| Subsystem | Authoritative entry point | Supporting context | Caveat |
|---|---|---|---|
| Investment Doctrine | `docs/governance/caerus_investment_doctrine.md` | `docs/governance/CURRENT_RESEARCH_ROADMAP.md` | Strategy changes require governance. |
| Research | `docs/governance/CURRENT_RESEARCH_ROADMAP.md` | `docs/research_review_packet.md`, generated research reports | Generated reports are evidence, not authorization. |
| Sleeve Lifecycle | `docs/governance/fr_active/fr_069_research_lab_modular_sleeve_architecture.md` | `docs/alpha_stack/README.md` | Research-only unless separately approved. |
| Portfolio Construction | `docs/governance/caerus_investment_doctrine.md` | `docs/Alpha_Stack_Architecture_Reference.md`, FR-105 docs | FR-105 remains `Needs Repository Verification`. |
| Execution | `docs/execution_contract.md` | `docs/execution_integrity_contract.md`, `docs/execution_integrity_runbook.md` | Planned-payload behavior is execution-critical. |
| Broker | `specs/broker_authoritative_execution_model.md` | Broker tests and broker artifacts | Broker truth outranks stale local artifacts. |
| Reconciliation | `docs/execution_integrity_contract.md` | `docs/execution_integrity_runbook.md`, target-attainment tests | Broker reconciliation and target attainment answer different questions. |
| Dashboard | `docs/dashboard_v1_source_map.md` | `docs/dashboard_refresh_spec.md`, `docs/quant_dashboard.md` | Served payload/schema still needs live verification. |
| Email / Reporting | `docs/trading_email_governance.md` | `docs/execution_summary.md` | Lifecycle fields are ahead of older docs in current worktree. |
| Governance | `docs/governance/README.md`, `docs/governance/fr_registry.md` | `docs/governance/fr_active_backlog.md` | Registry status outranks folder location. |
| Infrastructure | `docs/deployment_workflow.md` | `docs/OPERATIONS.md`, `AGENTS.md` | VM truth requires direct validation. |
| Scheduler | `scripts/crontab.txt` | `.github/workflows/daily-alpaca-paper.yml` for deprecation only | VM cron is current scheduler evidence. |
| Testing | `AGENTS.md` | `Tests/test_*.py` | Tests are behavioral docs, not runtime proof by themselves. |
| AI Agent Workflow | `AGENTS.md`, `docs/governance/ORCHESTRATOR_CONTEXT.md` | `docs/governance/AI_ORCHESTRATION_MODEL.md` | Preserve explicit task scope and dirty worktree boundaries. |

## Architecture

| Topic | Primary reference | Secondary references |
|---|---|---|
| System overview | `README.md` (repo root) | `AGENTS.md`, `docs/Alpha_Stack_Architecture_Reference.md` |
| Seven-layer Alpha Stack | `docs/Alpha_Stack_Architecture_Reference.md` | `docs/alpha_stack/architecture_overview.md` |
| Research lab / modular sleeves | `docs/governance/fr_active/fr_069_research_lab_modular_sleeve_architecture.md` | `docs/governance/fr_active/fr_069_phase_a_architecture_package.md`, `docs/governance/fr_active/fr_069_phase_b_scaffolding.md` |
| Research data platform | `docs/governance/fr_active/data_hydration/fr_dh_000_data_hydration_index.md` | `docs/governance/fr_active/data_hydration/fr_dh_013_canonical_research_data_catalog.md`; `docs/architecture/research_data_platform.md` is current-worktree evidence and Needs Repository Verification. |
| Research MCP / semantic layer | `docs/architecture/semantics/README.md` | `docs/architecture/caerus_research_mcp_architecture.md`, `docs/operator/research_mcp_operator_guide.md` |

## Operations

| Topic | Primary reference | Related implementation |
|---|---|---|
| Daily VM schedule | `scripts/crontab.txt` | `scripts/cron_*.sh` |
| Daily operations | `docs/runbook.md`, `docs/OPERATIONS.md` | `daily_quant_report.py`, `scripts/run_precomputed_alpaca_execution.py` |
| Deployment workflow | `docs/deployment_workflow.md` | `scripts/deploy_dashboard_vm.sh`, `scripts/ops/run_vm_validation.sh` |
| Execution source contract | `docs/execution_contract.md` | `scripts/run_precomputed_alpaca_execution.py` |
| Execution integrity | `docs/execution_integrity_contract.md`, `docs/execution_integrity_runbook.md` | `core/execution_integrity.py`, `core/operational_invariants.py` |
| Broker-authoritative model | `specs/broker_authoritative_execution_model.md` | `paper/paper_broker.py`, `brokers/alpaca_broker.py` |

## Reporting And Dashboard

| Topic | Primary reference | Related implementation |
|---|---|---|
| Dashboard current source map | `docs/dashboard_v1_source_map.md` | `scripts/research/build_dashboard_v1.py`, `web/dashboard/` |
| Dashboard redesign | `docs/dashboard_refresh_spec.md` | `docs/dashboard_v1_spec.md`, `docs/dashboard_v2_spec.md` |
| Quant dashboard operations | `docs/quant_dashboard.md` | `scripts/research/build_quant_dashboard.py`, `scripts/refresh_quant_dashboard.py` |
| Trading email governance | `docs/trading_email_governance.md` | `paper/build_execution_email.py`, `scripts/send_trading_confirmation_email.py` |
| Execution summary | `docs/execution_summary.md` | `core/execution_summary.py`, `Tests/test_execution_summary.py` |

## Research And FR Evidence

| Topic | Primary reference | Notes |
|---|---|---|
| PIT universe and survivorship remediation | `docs/governance/CURRENT_RESEARCH_ROADMAP.md` FR-068 row | Do not treat current-universe backtests as promotion-grade unless current docs say so. |
| FR-069 modular sleeve architecture | `docs/governance/fr_active/fr_069_research_lab_modular_sleeve_architecture.md` | Research-only unless separately approved. |
| FR-070 execution integrity observation | `docs/governance/fr_active/fr_070_cash_gating_post_sell_budget_reconciliation.md` | Future execution behavior changes require classified evidence and approval. |
| FR-074 reliability framework | `docs/governance/fr_active/fr_074_execution_reliability_framework.md` | Observe-first invariant reporting. |
| FR-104 live pilot | `docs/governance/fr_active/fr_104_live_pilot_unlock_program.md` | Manual, disabled-by-default, capped evidence lane. |
| FR-105 optimizer/provenance | `docs/governance/fr_active/fr_105_global_portfolio_optimizer_and_decision_provenance.md` | Current worktree evidence; Needs Repository Verification before treating as merged canonical state. |

## Tests As Behavioral Documentation

Use focused tests to understand expected behavior:

| Behavior | Tests | Caveat |
|---|---|---|
| Allocation | `Tests/test_allocation.py`, `Tests/test_allocator_cash_drag_redistribution.py` | Behavioral documentation only. |
| Precomputed execution | `Tests/test_run_precomputed_alpaca_execution.py`, `Tests/test_run_precomputed_alpaca_execution_fast.py` | Does not replace live/broker artifact validation. |
| Candidate lifecycle | `Tests/test_candidate_trade_lifecycle.py` | Current-worktree evidence; Needs Repository Verification; not runtime proof. |
| Execution integrity and reliability | `Tests/test_execution_integrity.py`, `Tests/test_operational_invariants.py`, `Tests/test_execution_lifecycle_timeline.py` | Verify against run artifacts before incident closure. |
| Broker reconciliation | `Tests/test_broker_authoritative_phase3.py`, `Tests/test_broker_authoritative_phase4.py`, `Tests/test_recon_posttrade_refresh.py` | Broker API truth still outranks synthetic tests. |
| Dashboard | `Tests/test_build_dashboard_v1.py`, `Tests/test_build_quant_dashboard.py`, `Tests/test_dashboard_ui_status.py` | Served-file parity still needs VM/browser verification. |
| Governance | `Tests/test_governance_hygiene_agent.py`, `Tests/test_documentation_governance.py` | Auditor output is review evidence, not an auto-fix. |
| Research data | `Tests/test_data_hydration_catalog.py`, `Tests/test_data_hydration_swarm.py`, `Tests/test_sleeve_migration_readiness.py` | Observe-only unless governance promotes consumers. |

## Generated Artifact Patterns

Architecture docs should link these patterns, not copy daily payloads:

- `outputs/precompute/<date>/`
- `outputs/workflow/<date>/`
- `outputs/runs/<run_id>/`
- `outputs/runs/<run_id>/audit/`
- `outputs/runs/<run_id>/broker/`
- `outputs/broker/`
- `outputs/target_attainment/<date>/`
- `outputs/execution_email/<date>.json`
- `outputs/shadow_candidates/<date>/`
- `outputs/research/**`
- `outputs/research/fr_105/<date>/`
- `outputs/governance_hygiene/<date>/`
- `outputs/research_mcp/questions/<timestamp>/`
- `web/dashboard*/dashboard_data.json`
