# Caerus Document Inventory

Status: architecture-pack inventory draft
Last reviewed: 2026-06-26

Canonical status: Needs Repository Verification until this architecture pack is
committed and reconciled with `origin/main`.

This inventory catalogs documents and document families that matter for
architecture, operations, governance, research, dashboard, execution, broker,
scheduler, tests-as-docs, and generated artifacts.

For per-FR status, do not copy rows from the registry. Use
`docs/governance/fr_registry.md` as the authoritative source and this inventory
as a routing layer.

## Document Contract

| Field | Value |
|---|---|
| Purpose | Catalog important documents, tests, and artifact families with status and ownership caveats. |
| Owner | Not named in repository; architecture/governance ownership requires repository verification. |
| Inputs | Repository docs, tests, generated artifact patterns, governance registry/backlog, architecture pack. |
| Outputs | Inventory table used by agents to find authoritative and supporting documentation. |
| Related Documents | `docs/architecture/DOCUMENT_INDEX.md`, `docs/architecture/DOCUMENT_GAPS.md`, `docs/architecture/DOCUMENTATION_GOVERNANCE.md`. |
| Related Tests | Documentation governance and governance hygiene tests. |
| Related Implementation | Cross-system implementation paths listed by row. |
| Related Artifacts | Artifact patterns listed by row. |
| Known Gaps | Owner fields are marked `Not named in repository` unless the repository explicitly names one. |

Status labels:

- `Authoritative`: primary current source for its scope.
- `Canonical link target`: stable enough to link from architecture docs.
- `Design / historical`: useful context, not current runtime authority.
- `Generated evidence`: evidence or report output, not canonical operator docs.
- `Needs Repository Verification`: current-worktree or conflicting source.

## Core Entrypoints

| Path | Title | Purpose | Subsystem | Owner | Status | Authoritative? | Related implementation | Related tests | Related artifacts | Gaps or conflicts |
|---|---|---|---|---|---|---|---|---|---|---|
| `README.md` | Alpha Stack | Root overview, named strategy state, seven-layer architecture, setup, known issues. | Whole system | Not named in repository | Canonical link target | Yes for high-level overview; defer status to governance when newer. | `daily_quant_report.py`, `core/portfolio_alloc.py`, `sleeves/` | Broad suite | `outputs/**` | Some technical-debt and sleeve-status statements may be older than current roadmap. |
| `AGENTS.md` | AI Orchestration Instructions | Durable rules for Codex/agent work, VM policy, scheduler summary, safety boundaries. | AI workflow / operations | Not named in repository | Authoritative | Yes for agent behavior. | `scripts/ops/run_vm_validation.sh`, cron scripts | `Tests/test_update_agents_md.py`, AIOps tests | VM validation output | Duplicates some operational truth from runbooks. |
| `QUICK_START.md` | Quick Start | Setup and run guidance. | Developer onboarding | Not named in repository | Canonical link target | Partial | Runtime entrypoints | Smoke tests | Local outputs | Needs freshness review against current VM-cron posture. |
| `README_PROD.md` | Production README | Production-oriented setup/context. | Operations | Not named in repository | Canonical link target | Partial | Runtime scripts | Operational tests | VM outputs | Needs review before treating as current deployment truth. |

## Documentation Governance

| Path | Title | Purpose | Subsystem | Owner | Status | Authoritative? | Related implementation | Related tests | Related artifacts | Gaps or conflicts |
|---|---|---|---|---|---|---|---|---|---|---|
| `docs/documentation/canonical_hierarchy.md` | Canonical Hierarchy | Defines documentation hierarchy and authority. | Documentation governance | Not named in repository | Authoritative | Yes | `core/documentation/` | `Tests/test_documentation_governance.py` | None | Flat docs tree still mixes categories. |
| `docs/documentation_governance.md` | Documentation Governance | Rules for doc creation and drift control. | Documentation governance | Not named in repository | Authoritative | Yes | `scripts/validate_documentation_governance.py` | `Tests/test_documentation_governance.py` | Validation output | Needs continued enforcement. |
| `docs/documentation_taxonomy.md` | Documentation Taxonomy | Separates canonical docs, generated reports, diagnostics, and research outputs. | Documentation governance | Not named in repository | Canonical link target | Yes as taxonomy proposal | Documentation validators | `Tests/test_documentation_governance.py` | Proposed future structure | Explicitly says migration is not yet done. |
| `docs/documentation/metadata_standard.md` | Metadata Standard | Front-matter and metadata guidance. | Documentation governance | Not named in repository | Canonical link target | Yes | Documentation validators | Documentation tests | None | Needs consistent adoption. |
| `docs/documentation/agents_hardening.md` | Agents Hardening | Agent-facing documentation hardening. | AI workflow | Not named in repository | Canonical link target | Partial | Agent workflows | AIOps tests | Reports/agent loops | Needs freshness review. |

## Artifact Governance

| Path | Title | Purpose | Subsystem | Owner | Status | Authoritative? | Related implementation | Related tests | Related artifacts | Gaps or conflicts |
|---|---|---|---|---|---|---|---|---|---|---|
| `docs/artifact_registry.md` | Artifact Registry | Catalog of important artifacts. | Artifact governance | Not named in repository | Canonical link target | Yes | Artifact producers | `Tests/test_artifact_coverage_matrix.py` | `outputs/**` | Latest-pointer freshness discipline remains a gap. |
| `docs/artifact_ownership_matrix.md` | Artifact Ownership Matrix | Ownership and trust boundaries for artifacts. | Artifact governance | Not named in repository | Canonical link target | Yes | Runtime producers | Artifact coverage tests | `outputs/precompute`, `outputs/runs`, `outputs/research` | Needs regular update as new artifacts are added. |
| `docs/artifact_governance.md` | Artifact Governance | Artifact trust, retention, and generated-vs-source rules. | Artifact governance | Not named in repository | Authoritative | Yes | Producers across repo | Artifact tests | `outputs/**`, `web/dashboard/**` | Dashboard mirrors and generated markdown need clearer manifests. |
| `docs/artifact_retention_policy.md` | Artifact Retention Policy | Retention guidance. | Artifact governance | Not named in repository | Canonical link target | Partial | Output writers | Artifact tests | `outputs/**` | Needs enforcement proof. |

## Governance And FR Docs

| Path | Title | Purpose | Subsystem | Owner | Status | Authoritative? | Related implementation | Related tests | Related artifacts | Gaps or conflicts |
|---|---|---|---|---|---|---|---|---|---|---|
| `docs/governance/README.md` | Governance Index | Source-of-truth routing for governance docs. | Governance | Not named in repository | Authoritative | Yes | Governance scripts | `Tests/test_governance_hygiene_agent.py` | `outputs/governance_hygiene/` | Registry status outranks folder location. |
| `docs/governance/caerus_investment_doctrine.md` | Caerus Investment Doctrine | Strategic doctrine for objective, sleeves, promotion, allocation. | Investment doctrine | Not named in repository | Authoritative | Yes | Strategy registry | Promotion/governance tests | Research outputs | Amend only through explicit governance. |
| `docs/governance/CURRENT_RESEARCH_ROADMAP.md` | Current Research Roadmap | Current research reconciliation/index layer. | Research governance | Not named in repository | Needs Repository Verification | Yes for current research direction when verified | `config/research/strategy_registry.json`, research scripts | Research registry tests | `outputs/research/**` | Modified locally; verify before remote-main claims. |
| `docs/governance/Strategy_Roadmap_And_Research_Backlog.md` | Strategy Roadmap And Research Backlog | Narrative roadmap intent. | Research governance | Not named in repository | Needs Repository Verification | Yes for roadmap intent when verified | Strategy registry | Governance tests | Research outputs | Modified locally during build. |
| `docs/governance/fr_registry.md` | FR Registry | Authoritative FR status table. | FR governance | Not named in repository | Needs Repository Verification | Yes for FR status when verified | Governance hygiene agent | `Tests/test_governance_hygiene_agent.py` | `outputs/governance_hygiene/` | Dirty local changes need verification. |
| `docs/governance/fr_active_backlog.md` | Active FR Backlog | Prioritized active work queue. | FR governance | Not named in repository | Needs Repository Verification | Yes for backlog when verified | Governance hygiene agent | `Tests/test_governance_hygiene_agent.py` | `outputs/governance_hygiene/` | Dirty local changes need verification. |
| `docs/governance/fr_governance_model.md` | FR Governance Model | Status semantics and governance process. | FR governance | Not named in repository | Authoritative | Yes | Governance hygiene agent | Governance tests | Governance hygiene outputs | Keep status names consistent. |
| `docs/governance/operational_lessons.md` | Operational Lessons | Incident learnings and durable guardrails. | Governance / operations | Not named in repository | Canonical link target | Yes for lessons | Runtime modules referenced by lessons | Incident/regression tests | Incident artifacts | Can become stale if not updated with each incident. |
| `docs/governance/repo_artifact_policy.md` | Repo Artifact Policy | Source/generated artifact policy. | Artifact governance | Not named in repository | Canonical link target | Yes | Output producers | Artifact tests | `outputs/**` | Needs link to taxonomy. |
| `docs/governance/change_lineage_standard.md` | Change Lineage Standard | Change lineage expectations. | Governance | Not named in repository | Canonical link target | Yes | Git/process | Governance tests | Reports | Needs adoption checks. |
| `docs/governance/ORCHESTRATOR_CONTEXT.md` | Orchestrator Context | Durable AI-assisted operating frame. | AI workflow / governance | Not named in repository | Authoritative | Yes | Agent workflows | AIOps tests | Reports/agent loops | Must be read before edits. |
| `docs/governance/STRATEGIC_ESCALATION_POLICY.md` | Strategic Escalation Policy | Stop/approval rules for strategic changes. | Governance | Not named in repository | Authoritative | Yes | Agent workflows | Governance tests | None | Runtime changes need explicit authorization. |
| `docs/governance/AI_ORCHESTRATION_MODEL.md` | AI Orchestration Model | ChatGPT/Codex orchestration model. | AI workflow | Not named in repository | Canonical link target | Yes | `aiops/` | `Tests/test_aiops_*.py` | AIOps outputs | Needs synchronization with AGENTS.md. |

## Functional Review Docs

| Path | Title | Purpose | Subsystem | Owner | Status | Authoritative? | Related implementation | Related tests | Related artifacts | Gaps or conflicts |
|---|---|---|---|---|---|---|---|---|---|---|
| `docs/governance/fr_active/fr_069_research_lab_modular_sleeve_architecture.md` | FR-069 Research Lab Modular Sleeve Architecture | Research-only modular sleeve architecture. | Sleeve lifecycle / research | Not named in repository | Authoritative for FR-069 | Yes | `research_registry/sleeves/`, validators | `Tests/test_sleeve_manifest.py`, `Tests/test_sleeve_evidence.py` | FR-069 research outputs | No production refactor authorized. |
| `docs/governance/fr_active/fr_070_cash_gating_post_sell_budget_reconciliation.md` | FR-070 Cash Gating and Post-Sell Buy Budget Reconciliation | Execution integrity observation and future reopening rules. | Execution | Not named in repository | Authoritative for FR-070 | Yes | `paper/paper_broker.py`, execution runner | Execution/cash-gating tests | Broker/rebudget artifacts | Future implementation requires classified evidence. |
| `docs/governance/fr_active/fr_074_execution_reliability_framework.md` | FR-074 Execution Reliability Framework | Observe-first reliability artifact and invariant contract. | Execution reliability | Not named in repository | Authoritative for FR-074 | Yes | `core/operational_invariants.py` | `Tests/test_operational_invariants.py` | `outputs/runs/<run_id>/audit/execution_reliability_report_<date>.json` | Phase B is future work. |
| `docs/governance/fr_active/fr_104_live_pilot_unlock_program.md` | FR-104 Live Pilot Unlock Program | Disabled-by-default capped live-pilot evidence lane. | Live pilot / broker | Not named in repository | Needs Repository Verification | Yes for FR-104 when verified | `scripts/live_pilot_execute.py`, live pilot guardrails | `Tests/test_live_pilot_*.py` | `outputs/live_pilot/**` | Modified locally; does not approve cron/live production. |
| `docs/governance/fr_active/fr_105_global_portfolio_optimizer_and_decision_provenance.md` | FR-105 Global Portfolio Optimizer and Decision Provenance | Research-only optimizer and provenance program. | Portfolio research | Not named in repository | Needs Repository Verification | Current-worktree yes only after verification | `research/fr105_*`, `scripts/research/*fr105*` | `Tests/test_fr105_*.py` | `outputs/research/fr_105/` | Untracked during build; no production change authorized. |
| `docs/governance/fr_active/data_hydration/fr_dh_000_data_hydration_index.md` | FR-DH Data Hydration Index | Data hydration governance package index. | Research data | Not named in repository | Authoritative for FR-DH | Yes | `research_data/`, `scripts/data_hydration/` | `Tests/test_data_hydration_*.py` | `outputs/data_trust/`, data manifests | Observe-only boundary. |
| `docs/governance/fr_active/data_hydration/fr_dh_013_canonical_research_data_catalog.md` | Canonical Research Data Catalog | Master catalog rule for research datasets. | Research data | Not named in repository | Authoritative | Yes | `data/manifests/research_data_catalog.json`, validators | `Tests/test_data_hydration_catalog.py` | Catalog and data trust artifacts | Future migration cannot depend on uncataloged datasets. |
| `docs/governance/fr_archive/fr_050_phoenix_research_spec.md` | Phoenix Research Spec | Crisis reversal strategy research spec. | Research | Not named in repository | Historical/canonical per-strategy spec | Yes for Phoenix lineage | Phoenix research modules | Phoenix tests | Phoenix research outputs | Current status must be read from roadmap/registry. |
| `docs/governance/fr_archive/fr_051_cygnus_research_spec.md` | Cygnus Research Spec | Earnings drift research spec. | Research | Not named in repository | Historical/canonical per-strategy spec | Yes for Cygnus lineage | `research/cygnus/` | Cygnus tests | Cygnus outputs | Current status must be read from roadmap/registry. |
| `docs/governance/fr_archive/fr_052_cassiopeia_research_spec.md` | Cassiopeia Research Spec | Event-driven research spec. | Research | Not named in repository | Historical/canonical per-strategy spec | Yes for Cassiopeia lineage | Cassiopeia scripts | Cassiopeia tests | Cassiopeia outputs | Current status must be read from roadmap/registry. |
| `docs/governance/fr_archive/fr_053_argo_research_spec.md` | Argo Research Spec | Regime/model-selection research spec. | Research | Not named in repository | Historical/canonical per-strategy spec | Yes for Argo lineage | Argo research modules | Argo tests | Argo outputs | Current status must be read from roadmap/registry. |
| `docs/governance/fr_archive/fr_063_strategy_differentiation_deep_dive.md` | Strategy Differentiation Deep Dive | Orion/Lyra differentiation lineage. | Research | Not named in repository | Historical/canonical link target | Partial | Differentiation scripts | Differentiation tests | Differentiation outputs | No immediate retirement decision authorized. |

## Architecture Docs

| Path | Title | Purpose | Subsystem | Owner | Status | Authoritative? | Related implementation | Related tests | Related artifacts | Gaps or conflicts |
|---|---|---|---|---|---|---|---|---|---|---|
| `docs/architecture/README.md` | Architecture Documentation | Entry point for architecture context and authority order. | Architecture | Not named in repository | Architecture-pack draft | Yes for routing once committed | Cross-system | N/A | N/A | New in this build; commit separately from unrelated dirty worktree changes. |
| `docs/architecture/SYSTEM_MAP.md` | Caerus System Map | Fast subsystem navigation, primary entry points, protected surfaces, and execution vocabulary. | Architecture | Not named in repository | Architecture-pack draft | Yes for routing once committed | Cross-system | N/A | N/A | New in this refinement; source claims defer to linked source-of-truth docs. |
| `docs/architecture/DOCUMENT_INDEX.md` | Caerus Document Index | Source-of-truth routing by need and subsystem. | Architecture | Not named in repository | Architecture-pack draft | Yes for routing once committed | Cross-system | N/A | N/A | New in this build; must stay link-oriented. |
| `docs/architecture/DOCUMENT_INVENTORY.md` | Caerus Document Inventory | Full catalog of documents, tests, artifacts, status, owner caveats, and related systems. | Architecture | Not named in repository | Architecture-pack draft | Yes for inventory routing once committed | Cross-system | Documentation governance tests when needed | N/A | Owner fields remain unverified unless named in repository. |
| `docs/architecture/CAERUS_TECHNICAL_ARCHITECTURE_AND_OPERATING_MANUAL.md` | Technical Architecture And Operating Manual | Operational architecture narrative with source links and caveats. | Architecture | Not named in repository | Architecture-pack draft | Partial; source docs remain authoritative | Cross-system | N/A | N/A | Must not replace FR registry, execution contracts, or runbooks. |
| `docs/architecture/KNOWLEDGE_GRAPH.md` | Caerus Knowledge Graph | Subsystem-to-doc/code/test/artifact/FR map. | Architecture | Not named in repository | Architecture-pack draft | Yes for graph routing once committed | Cross-system | N/A | N/A | Keep generated artifact patterns generic. |
| `docs/architecture/DOCUMENT_GAPS.md` | Caerus Documentation Gaps | Verified open documentation gaps and repository-verification caveats. | Architecture | Not named in repository | Architecture-pack draft | Yes for gaps found in this build once committed | Cross-system | N/A | N/A | Does not authorize runtime work. |
| `docs/architecture/ARCHITECTURE_PRINCIPLES.md` | Architecture Principles | Durable engineering principles and protected surfaces. | Architecture | Not named in repository | Architecture-pack draft | Yes for architecture-pack principles once committed | Cross-system | N/A | N/A | Source subsystem principles remain in linked canonical docs. |
| `docs/architecture/GLOSSARY.md` | Architecture Glossary | Shared architecture and execution vocabulary. | Architecture | Not named in repository | Architecture-pack draft | Yes for terms once committed | Cross-system | `Tests/test_candidate_trade_lifecycle.py` and execution tests | N/A | Complete recommendation provenance remains unverified. |
| `docs/architecture/CONTRIBUTING_ARCHITECTURE.md` | Contributing Architecture Documentation | Maintenance checklist for architecture docs. | Documentation governance | Not named in repository | Architecture-pack draft | Yes for this pack once committed | N/A | Documentation governance tests when needed | N/A | Does not replace global documentation governance. |
| `docs/architecture/DOCUMENTATION_GOVERNANCE.md` | Architecture Documentation Governance | Architecture-pack document contracts, status labels, and gap categories. | Documentation governance | Not named in repository | Architecture-pack draft | Yes for this pack once committed | `core/documentation/`, governance scripts | Documentation governance tests | Governance hygiene outputs | Global hierarchy remains in `docs/documentation/`. |
| `docs/architecture/OPERATOR_RUNBOOK.md` | Architecture Operator Runbook | Route operator questions to canonical runbooks and artifacts. | Operations | Not named in repository | Architecture-pack draft | Routing only once committed | Cron, execution, broker, dashboard implementation paths | Operational tests by subsystem | `outputs/workflow/`, `outputs/runs/`, VM logs | Not a replacement for source runbooks. |
| `docs/architecture/ENGINEERING_DECISION_INDEX.md` | Engineering Decision Index | Durable architecture decisions and evidence lanes. | Architecture governance | Not named in repository | Architecture-pack draft | Routing only once committed | Cross-system | Tests referenced by decision row | FR and run artifacts | FR registry/backlog remain authoritative for status. |
| `docs/architecture/architecture_lineage.md` | Architecture Lineage | Topic-level lineage and architecture record pointers. | Architecture | Not named in repository | Needs Repository Verification | Partial | Cross-system | N/A | N/A | Modified before this build. |
| `docs/Alpha_Stack_Architecture_Reference.md` | Alpha Stack Architecture Reference | Seven-layer architecture, current sleeve definitions, technical debt. | Alpha stack | Not named in repository | Canonical link target | Yes for architecture lineage | `daily_quant_report.py`, `core/portfolio_alloc.py`, `sleeves/` | Allocation/sleeve tests | Precompute outputs | Older status may conflict with roadmap. |
| `docs/alpha_stack/README.md` | Alpha Stack Docs Index | Alpha-stack document index. | Alpha stack | Not named in repository | Canonical link target | Partial | Alpha stack modules | Alpha stack tests | Shadow outputs | Needs freshness review. |
| `docs/alpha_stack/architecture_overview.md` | Alpha Stack Architecture Overview | Compact seven-layer overview. | Alpha stack | Not named in repository | Canonical link target | Partial | Alpha stack modules | Alpha tests | Precompute outputs | Older than current FR-069/RDP state. |
| `docs/alpha_stack/sleeve_specifications.md` | Sleeve Specifications | Sleeve-level specification notes. | Sleeves | Not named in repository | Canonical link target | Partial | `sleeves/`, research registry | Sleeve tests | Sleeve artifacts | Planned sleeves must not be treated as implemented. |
| `docs/alpha_stack/regime_allocator_spec.md` | Regime Allocator Spec | Regime allocator design. | Portfolio / regime | Not named in repository | Design / historical | No for runtime | Regime/research modules | Regime tests | Research outputs | Needs implementation verification. |
| `docs/architecture/research_data_platform.md` | Research Data Platform | RDP architecture retrospective and observe-only migration evidence. | Research data | Not named in repository | Needs Repository Verification | Not until promoted/committed | `research_data/`, data scripts | Data hydration tests | Data trust outputs | Untracked during build. |
| `docs/architecture/semantics/README.md` | Semantic Architecture Index | Semantic standards index. | Semantics / research MCP | Not named in repository | Authoritative for semantic docs | Yes | `research_registry/` | Research registry tests | MCP outputs | Verify exact current capabilities from code. |
| `docs/architecture/caerus_research_mcp_architecture.md` | Caerus Research MCP Architecture | Aspirational Research MCP architecture and read-only boundary. | Research MCP | `architecture` in front matter | Design / aspirational | Partial for design intent; no for current capability count | `research_registry/`, MCP scripts | MCP tests | `outputs/research_mcp/` | Current capability claims require current-state verification. |
| `docs/architecture/research_mcp_current_state_2026-05-29.md` | Research MCP Current State | Point-in-time MCP state. | Research MCP | Not named in repository | Design / historical | No for latest capability count | MCP code | MCP tests | MCP outputs | Needs refresh against current tests. |
| `docs/architecture/research_registry_v1_foundation.md` | Research Registry V1 Foundation | Research registry foundation. | Research registry | Not named in repository | Canonical link target | Partial | `research_registry/` | Registry tests | Registry artifacts | Needs current-state refresh. |
| `docs/architecture/research_registry_query_layer.md` | Research Registry Query Layer | Query-layer design. | Research registry | Not named in repository | Canonical link target | Partial | Query tools | Query tests | MCP outputs | Needs current-state refresh. |

## Execution, Broker, Reconciliation, And Runbooks

| Path | Title | Purpose | Subsystem | Owner | Status | Authoritative? | Related implementation | Related tests | Related artifacts | Gaps or conflicts |
|---|---|---|---|---|---|---|---|---|---|---|
| `docs/execution_contract.md` | Execution Source Contract | Source taxonomy, exact planned-payload contract, freshness semantics. | Execution | Not named in repository | Authoritative | Yes | `scripts/run_precomputed_alpaca_execution.py` | Execution tests | `outputs/runs/<run_id>/execution_payload.json`, `outputs/runs/<run_id>/execution_timeline.*` | Keep current with planned-payload changes. |
| `docs/execution_integrity_contract.md` | Execution Integrity Contract | FR-031 integrity audit, post-sell rebudgeting, target attainment. | Execution / reconciliation | Not named in repository | Authoritative | Yes | `core/execution_integrity.py`, `paper/paper_broker.py`, `research/target_attainment.py` | Execution integrity, target-attainment tests | `outputs/runs/<run_id>/audit/execution_integrity.json`, `outputs/runs/<run_id>/broker/post_sell_rebudget_<date>.json`, target-attainment outputs | Does not by itself prove target attainment. |
| `docs/execution_integrity_runbook.md` | Execution Integrity Runbook | Operator response guide for execution-integrity incidents. | Execution runbook | Not named in repository | Authoritative | Yes | Execution artifacts and helpers | Execution tests | Broker/recon/rebudget artifacts | Needs update when new reason codes stabilize. |
| `docs/execution_summary.md` | Execution Summary And History Export | Summary CSV/text artifact behavior. | Reporting / execution | Not named in repository | Canonical link target, partially stale | Partial | `core/execution_summary.py` | `Tests/test_execution_summary.py` | `outputs/execution_history.csv`, summaries | Current lifecycle fields in dirty code are not reflected fully. |
| `specs/broker_authoritative_execution_model.md` | Broker-Authoritative Execution Model | Broker authority design and source-of-truth contract. | Broker / execution | Not named in repository | Design spec with current relevance | Partial | `paper/paper_broker.py`, `brokers/alpaca_broker.py` | Broker authoritative tests | Broker snapshots and recon | Design language should be checked against current implementation. |
| `specs/daily_alpaca_paper_run_0935_et.md` | Daily Alpaca Paper Run 09:35 ET | Scheduled paper run design/acceptance. | Scheduler / execution | Not named in repository | Design / historical | Not current scheduler authority | GitHub workflow, cron scripts | Schedule tests | CI/VM artifacts | Conflicts with deprecated GitHub wrapper; VM cron is current. |
| `docs/runbook.md` | Runbook | Operator runbook. | Operations | Not named in repository | Authoritative | Yes | Cron scripts, execution scripts | Operational tests | VM outputs | Needs periodic freshness review. |
| `docs/OPERATIONS.md` | Operations | Operational procedures. | Operations | Not named in repository | Authoritative | Yes | Runtime scripts | Operational tests | VM outputs | Needs periodic freshness review. |
| `docs/deployment_workflow.md` | Deployment Workflow | Deployment process and validation expectations. | Infrastructure | Not named in repository | Authoritative | Yes | `scripts/deploy_dashboard_vm.sh`, `scripts/ops/run_vm_validation.sh` | VM/deploy tests | VM validation output | VM truth requires live SSH validation. |
| `docs/operator_review_workflow.md` | Operator Review Workflow | Human review workflow. | Operations | Not named in repository | Canonical link target | Partial | Operator artifacts | Workflow tests | Operator summaries | Needs reason-code alignment. |

## Dashboard And Reporting Docs

| Path | Title | Purpose | Subsystem | Owner | Status | Authoritative? | Related implementation | Related tests | Related artifacts | Gaps or conflicts |
|---|---|---|---|---|---|---|---|---|---|---|
| `docs/dashboard_v1_spec.md` | Dashboard V1 Spec | Truthful foundation for dashboard restart. | Dashboard | Not named in repository | Canonical link target | Yes for V1 contract | `scripts/research/build_dashboard_v1.py` | Dashboard V1 tests | Dashboard payloads | Verify served-file parity for VM claims. |
| `docs/dashboard_v1_source_map.md` | Dashboard V1 Source Map | Source mapping for dashboard data. | Dashboard | Not named in repository | Canonical link target | Yes for source mapping | Dashboard builders | Dashboard tests | Dashboard JSON/JS | Needs update as data contracts evolve. |
| `docs/dashboard_v2_spec.md` | Dashboard V2 Spec | Implementation-grade dashboard contract. | Dashboard | Not named in repository | Design / contract | Partial | Dashboard builders, web files | Dashboard tests | Dashboard payloads | Needs current served schema verification. |
| `docs/dashboard_refresh_spec.md` | Dashboard Refresh Spec | KPI and layout goals. | Dashboard | Not named in repository | Canonical link target | Partial | Dashboard builders | Dashboard tests | Dashboard payloads | Contains open data gaps. |
| `docs/quant_dashboard.md` | Quant Dashboard | Dashboard purpose, schema, build, VM access. | Dashboard | Not named in repository | Canonical link target, partially stale | Partial | `scripts/research/build_quant_dashboard.py`, `web/dashboard/` | `Tests/test_build_quant_dashboard.py` | `web/dashboard/dashboard_data.json` | Builder/artifact drift possible. |
| `reports/dashboard_lineage_audit.md` | Dashboard Lineage Audit | Dashboard data lineage evidence. | Dashboard | Not named in repository | Generated evidence | No | Dashboard builders | Dashboard tests | Dashboard payloads | Use as evidence, not canonical instructions. |
| `docs/trading_email_governance.md` | Trading Email Governance | Email/reporting governance. | Email / reporting | Not named in repository | Canonical link target | Yes | `paper/build_execution_email.py` | Email tests | `outputs/execution_email/` | Needs lifecycle field alignment. |
| `docs/shadow_scoreboard_email.md` | Shadow Scoreboard Email | Shadow email behavior. | Shadow reporting | Not named in repository | Canonical link target | Partial | `scripts/send_shadow_cio_report.py` | Shadow email tests | Shadow report outputs | Needs current-state review. |

## Research Docs And Reports

| Path | Title | Purpose | Subsystem | Owner | Status | Authoritative? | Related implementation | Related tests | Related artifacts | Gaps or conflicts |
|---|---|---|---|---|---|---|---|---|---|---|
| `docs/research_review_packet.md` | Research Review Packet | Research packet construction. | Research | Not named in repository | Canonical link target | Partial | `scripts/build_research_review_packet.py` | Research review tests | Research packet outputs | Generated packets remain evidence. |
| `docs/research_source_readiness.md` | Research Source Readiness | Source-readiness interpretation. | Research data | Not named in repository | Canonical link target | Partial | Source readiness scripts | Source readiness tests | Research outputs | Needs RDP alignment. |
| `research/pit_universe_architecture_2026-06-10.md` | PIT Universe Architecture | PIT universe architecture report. | Research / PIT universe | Not named in repository | Historical evidence | No for current status | PIT universe scripts | PIT universe tests | PIT outputs | Current status must use roadmap. |
| `reports/decision_grade_pit_program_final_2026-06-22.md` | Decision-Grade PIT Program Final | PIT program evidence. | Research / PIT | Not named in repository | Generated evidence | No | PIT scripts | PIT tests | Research outputs | Evidence only. |
| `reports/fr068_*.md` | FR-068 Reports | FR-068 audit and blocker reports. | Research / PIT | Not named in repository | Generated evidence | No | FR-068 scripts | FR-068 tests | Research outputs | Duplicate filenames and historical snapshots exist. |
| `reports/incidents/README.md` | Incidents README | Incident report routing. | Incident history | Not named in repository | Canonical link target | Partial | Incident templates | Incident tests | Incident reports | Generated incident packets are evidence. |
| `reports/agent_loops/` | Agent Loop Reports | Historical agent-loop implementation/evidence. | AI workflow / incidents | Not named in repository | Generated evidence | No | Various | Various | Agent-loop outputs | Do not treat as current operator docs. |
| `weekly_quant_research/*.md` | Weekly Quant Research Briefs | Weekly research generated summaries. | Research reporting | Not named in repository | Generated evidence | No | Research scripts | Research tests | Weekly outputs | Evidence only. |

## Scheduler Docs And Scripts

| Path | Title | Purpose | Subsystem | Owner | Status | Authoritative? | Related implementation | Related tests | Related artifacts | Gaps or conflicts |
|---|---|---|---|---|---|---|---|---|---|---|
| `scripts/crontab.txt` | GCP VM Cron Schedule | Current VM cron schedule and phase times. | Scheduler | Not named in repository | Authoritative | Yes | `scripts/cron_*.sh` | `Tests/test_daily_alpaca_workflow_schedule.py`, `Tests/test_cron_command_validation.py` | `logs/cron_*.log`, `outputs/workflow/` | VM install state still requires live SSH verification. |
| `.github/workflows/daily-alpaca-paper.yml` | Deprecated Wrapper | Deprecated CI wrapper that points to VM cron. | Scheduler | Not named in repository | Authoritative only for deprecation | Yes for deprecation status | GitHub Actions | Schedule tests | CI logs | Do not cite as active scheduler. |
| `.github/workflows/nightly-agents-refresh.yml` | Nightly Agents Refresh | Workflow for nightly agent refresh. | Automation | Not named in repository | Canonical link target | Partial | scripts | Workflow tests | CI artifacts | Needs current usage verification. |

## Tests As Documentation

| Path | Title | Purpose | Subsystem | Owner | Status | Authoritative? | Related implementation | Related tests | Related artifacts | Gaps or conflicts |
|---|---|---|---|---|---|---|---|---|---|---|
| `Tests/test_candidate_trade_lifecycle.py` | Candidate Trade Lifecycle Regression | 8 planned / 2 submitted regression fixture and lifecycle reason coverage. | Execution | Not named in repository | Needs Repository Verification | Current-worktree yes; not runtime proof | `core/candidate_trade_lifecycle.py` | Self | Lifecycle artifact fixture | Untracked during build; no persisted broker bundle found. |
| `Tests/test_run_precomputed_alpaca_execution.py` | Precomputed Execution Tests | Runner behavior and fail-closed execution. | Execution | Not named in repository | Behavioral documentation | Yes for expected behavior | `scripts/run_precomputed_alpaca_execution.py` | Self | Synthetic run artifacts | Does not replace live artifact validation. |
| `Tests/test_execution_integrity.py` | Execution Integrity Tests | Integrity audit expected behavior. | Execution | Not named in repository | Behavioral documentation | Yes | `core/execution_integrity.py` | Self | Synthetic audits | Keep aligned with runbook. |
| `Tests/test_operational_invariants.py` | Operational Invariants Tests | FR-074 reliability incident classes. | Execution reliability | Not named in repository | Behavioral documentation | Yes | `core/operational_invariants.py` | Self | Synthetic reliability outputs | Scenario fixtures should preserve named regressions. |
| `Tests/test_broker_authoritative_phase3.py`, `Tests/test_broker_authoritative_phase4.py` | Broker Authoritative Tests | Broker-state refresh and reconciliation behavior. | Broker / reconciliation | Not named in repository | Behavioral documentation | Yes | `paper/paper_broker.py`, broker modules | Self | Synthetic broker artifacts | Broker API truth still requires real broker validation. |
| `Tests/test_build_dashboard_v1.py`, `Tests/test_build_quant_dashboard.py`, `Tests/test_dashboard_ui_status.py` | Dashboard Tests | Dashboard builder and UI expectations. | Dashboard | Not named in repository | Behavioral documentation | Yes | Dashboard builders/web files | Self | Dashboard payloads | Served-file parity needs VM/browser validation. |
| `Tests/test_governance_hygiene_agent.py` | Governance Hygiene Tests | Governance auditor behavior. | Governance | Not named in repository | Behavioral documentation | Yes | `scripts/governance_hygiene_agent.py` | Self | Governance hygiene outputs | Auditor is read-only; does not auto-fix. |
| `Tests/test_data_hydration_*.py` | Data Hydration Tests | RDP catalog, freshness, normalization, observability. | Research data | Not named in repository | Behavioral documentation | Yes | `research_data/`, data scripts | Self | Data trust outputs | Observe-only; not production sleeve migration. |
| `Tests/test_fr105_*.py` | FR-105 Tests | Optimizer/provenance research artifact contracts. | Portfolio research | Not named in repository | Needs Repository Verification | Current-worktree yes | `research/fr105_*`, scripts | Self | `outputs/research/fr_105/` | Untracked/dirty during build. |

## Generated Artifact Patterns

| Pattern | Title | Purpose | Subsystem | Owner | Status | Authoritative? | Related implementation | Related tests | Related artifacts | Gaps or conflicts |
|---|---|---|---|---|---|---|---|---|---|---|
| `outputs/precompute/<date>/` | Precompute Bundle | Daily contract, snapshot, signals, planned execution payload. | Precompute / execution | Not named in repository | Generated evidence | Yes as run-specific evidence | `scripts/cron_precompute.sh`, precompute modules | Precompute tests | Bundle files | Must pass bundle validation before execution. |
| `outputs/workflow/<date>/` | Workflow Status Artifacts | Phase status, self-heal, shadow status. | Scheduler / workflow | Not named in repository | Generated evidence | Yes as run-specific evidence | Cron scripts | Workflow tests | Workflow JSON | Latest/current date must be verified. |
| `outputs/runs/<run_id>/` | Run Root | Per-run execution, broker, audit, summary artifacts. | Execution | Not named in repository | Generated evidence | Yes as run-specific evidence | Execution runner | Execution tests | Run artifacts | Missing run bundle blocks runtime claims. |
| `outputs/runs/<run_id>/broker/` | Broker Run Artifacts | Intended orders, submissions, broker snapshots, recon, rebudget. | Broker / reconciliation | Not named in repository | Generated evidence | Broker truth when populated from broker | Broker modules | Broker tests | Broker JSON/CSV | Artifact timing matters. |
| `outputs/runs/<run_id>/audit/` | Run Audit Artifacts | Execution integrity, reliability, candidate lifecycle. | Execution audit | Not named in repository | Generated evidence | Yes as audit evidence | Audit modules | Audit tests | Audit JSON | Lifecycle artifact current-worktree only until committed. |
| `outputs/target_attainment/<date>/` | Target Attainment | Actual vs intended/risk-adjusted portfolio reconciliation. | Reconciliation | Not named in repository | Generated evidence | Yes for target-attainment question | `research/target_attainment.py` | Target-attainment tests | Target-attainment JSON | Distinct from broker position reconciliation. |
| `outputs/research/**` | Research Outputs | Research artifacts and generated reports. | Research | Not named in repository | Generated evidence | No for production authorization | Research scripts | Research tests | Research JSON/CSV/MD | Generated evidence only. |
| `outputs/research/fr_105/<date>/` | FR-105 Research Artifacts | Optimizer/provenance research contracts and baselines. | Portfolio research | Not named in repository | Needs Repository Verification | Current-worktree evidence | FR-105 scripts | FR-105 tests | FR-105 JSON | Null lifecycle paths show missing execution bundle linkage. |
| `outputs/governance_hygiene/<date>/` | Governance Hygiene Output | Read-only governance audit results. | Governance | Not named in repository | Generated evidence | Evidence only | `scripts/governance_hygiene_agent.py` | Governance tests | JSON/MD reports | Review before applying suggestions. |
| `web/dashboard*/dashboard_data.json` | Dashboard Generated Data | Static dashboard payload. | Dashboard | Not named in repository | Generated evidence | Only for served snapshot | Dashboard builders | Dashboard tests | Dashboard JSON | Served-file parity requires filesystem hash/VM validation. |
