# Caerus Documentation Gaps

Status: architecture-pack backlog draft
Last reviewed: 2026-06-26

Canonical status: Needs Repository Verification until this architecture pack is
committed and reconciled with `origin/main`.

This file is the documentation architecture backlog. It does not authorize
runtime, code, scheduler, broker, allocation, configuration, or test changes.

## Document Contract

| Field | Value |
|---|---|
| Purpose | Track verified documentation debt and repository-verification caveats. |
| Owner | Not named in repository; architecture/governance ownership requires repository verification. |
| Inputs | Repository discovery, reviewer findings, architecture pack, governance docs, tests-as-docs. |
| Outputs | Typed documentation backlog for future architecture work. |
| Related Documents | `docs/architecture/DOCUMENTATION_GOVERNANCE.md`, `docs/architecture/DOCUMENT_INDEX.md`, `docs/architecture/DOCUMENT_INVENTORY.md`. |
| Related Tests | Documentation governance and subsystem tests referenced by backlog rows. |
| Related Implementation | Cross-system; each row names implementation when relevant. |
| Related Artifacts | Run bundles, generated reports, dashboard payloads, governance outputs. |
| Known Gaps | The backlog itself remains architecture-pack draft until committed. |

## Gap Categories

Every unresolved gap must use one of these categories:

- Verified Gap
- Repository Verification Required
- Historical Artifact
- Duplicate Documentation
- Architecture Drift

## Resolved During DAP Finalization

| Category | Prior gap | Resolution | Residual caveat |
|---|---|---|---|
| Verified Gap | No stable `docs/architecture` system index existed for the whole repository. | Added and cross-linked `README.md`, `SYSTEM_MAP.md`, `DOCUMENT_INDEX.md`, `DOCUMENT_INVENTORY.md`, `KNOWLEDGE_GRAPH.md`, the Technical Architecture and Operating Manual, and companion docs. | Commit separately from unrelated dirty-worktree changes. |
| Verified Gap | Companion architecture governance files were missing. | Added `ARCHITECTURE_PRINCIPLES.md`, `GLOSSARY.md`, `CONTRIBUTING_ARCHITECTURE.md`, `DOCUMENTATION_GOVERNANCE.md`, `OPERATOR_RUNBOOK.md`, and `ENGINEERING_DECISION_INDEX.md`. | Canonical adoption still needs repository verification. |
| Architecture Drift | The 8 planned / 2 submitted lifecycle case was duplicated across system map and the Technical Architecture and Operating Manual. | The manual owns the detailed fixture table; `SYSTEM_MAP.md` now links/summarizes only. | Runtime broker proof is still missing. |
| Architecture Drift | RDP and architecture-lineage front matter claimed canonical status while inventory marked them unverified. | Set front matter canonical status to `Needs Repository Verification` and aligned RDP prose. | Commit/reconciliation still required. |
| Architecture Drift | Research MCP architecture front matter and title could be read as current implementation truth despite its status banner. | Marked it as aspirational design intent, added a document contract, and kept current-state claims routed to the point-in-time/current-code evidence path. | Fresh current MCP inventory remains open. |
| Historical Artifact | The 2026-05-29 MCP current-state snapshot was easy to read as current. | Added historical-status warning and changed "canonical snapshot" wording to point-in-time snapshot. | Fresh current MCP inventory remains open. |
| Verified Gap | Semantics README referenced a missing research-integrity hardening document path. | Replaced the missing path with registry/backlog references for FR-024..029 routing. | Semantics docs should still be link-checked periodically. |

## Backlog

| Category | Gap | Evidence | Impact | Recommended resolution |
|---|---|---|---|---|
| Repository Verification Required | Architecture pack is untracked/local and branch is ahead/behind `origin/main`. | `git status --short --branch` reports branch divergence and untracked architecture-pack files. | Pack is suitable as local architecture context but not confirmed remote-main truth. | Commit/reconcile the architecture docs separately from unrelated worktree changes. |
| Repository Verification Required | Candidate lifecycle code/test are current-worktree evidence. | `core/candidate_trade_lifecycle.py` and `Tests/test_candidate_trade_lifecycle.py` are untracked. | June 25 lifecycle fixture is not durable repository truth until promoted. | Commit or otherwise promote the lifecycle implementation/test before treating as canonical evidence. |
| Repository Verification Required | June 25 8 planned / 2 submitted fixture lacks a checked-in broker run bundle. | No `outputs/runs/2026-06-25T093508-0400_7b9af94` broker-authoritative bundle found. | Case cannot be cited as runtime broker history. | Preserve as deterministic regression fixture unless an operator provides and catalogs the actual run bundle. |
| Repository Verification Required | FR-105 optimizer/provenance docs, tests, scripts, and artifacts are current-worktree evidence. | FR-105 docs/tests/research scripts are untracked or dirty. | Architecture can route to FR-105 but must not imply production behavior or merged truth. | Verify commit state and keep research-only caveat attached. |
| Repository Verification Required | RDP and related Phase 2 docs are current-worktree or dirty evidence. | `docs/architecture/research_data_platform.md`, Phase 2 docs, and FR-DV docs are untracked/dirty. | RDP architecture should be treated as local evidence until committed. | Confirm intended canonical status and align front matter/inventory after commit. |
| Repository Verification Required | FR-104, registry, backlog, and roadmap docs are dirty locally. | `docs/governance/fr_active/fr_104_live_pilot_unlock_program.md`, `docs/governance/fr_registry.md`, `docs/governance/fr_active_backlog.md`, `docs/governance/CURRENT_RESEARCH_ROADMAP.md`, and `docs/governance/Strategy_Roadmap_And_Research_Backlog.md` are modified. | Architecture can cite them only with verification caveats for remote-main/deployment claims. | Reconcile branch and verify governance docs before audit closure. |
| Architecture Drift | Scheduler docs still include older GitHub Actions design lineage while current scheduler evidence is VM cron. | `.github/workflows/daily-alpaca-paper.yml` is a deprecated wrapper; `scripts/crontab.txt` is current schedule evidence. | Readers can mistake historical specs for current scheduler authority. | Continue routing scheduler claims to `scripts/crontab.txt`; mark older specs historical. |
| Architecture Drift | Dashboard source ownership is split across specs, builders, web files, and generated payloads. | Dashboard docs list v1/v2/refresh/quant dashboard sources and multiple payload names. | Served dashboard truth can be overstated without live build/VM proof. | Add a dashboard source-of-truth manifest after served-file verification. |
| Architecture Drift | Research MCP current-state snapshot may lag implementation. | `research_mcp_current_state_2026-05-29.md` is point-in-time; tests/code have continued to evolve. | Readers can confuse historical capability counts with current capabilities. | Generate a refreshed MCP capability inventory from code/tests. |
| Architecture Drift | Execution lifecycle observability is ahead of committed documentation. | Current-worktree lifecycle, email, summary, and trading-day changes add candidate disposition fields. | Older docs underreport planned/intended/submitted/accepted/filled/clipped/suppressed/rejected states. | Promote lifecycle docs after implementation/test commit and run-bundle linkage. |
| Duplicate Documentation | Ignored duplicate local docs with ` 2.md` suffix exist in governance areas. | Repository discovery reported duplicate local governance docs. | Readers and agents can pick stale duplicate files. | Remove or quarantine duplicates through a docs-only cleanup with link validation. |
| Historical Artifact | Generated reports and outputs contain historical snapshots and duplicate markdown. | `reports/` and `outputs/` include generated evidence across incidents and research. | Generated artifacts may be mistaken for operator instructions. | Keep generated reports as evidence unless governance promotes them. |
| Historical Artifact | Root onboarding docs contain older strategy/technical-debt state. | Root `README.md` and `QUICK_START.md` are older than current roadmap/governance docs. | New readers may treat older overview text as current strategy state. | Route strategy status to doctrine, roadmap, registry, and strategy registry. |
| Verified Gap | Documentation taxonomy is proposed but not fully implemented in tree layout. | `docs/documentation_taxonomy.md` says the flat docs tree still mixes categories. | Canonical, generated, historical, and diagnostic material remain colocated. | Migrate only under a future FR with link validation. |
| Verified Gap | Full systemwide recommendation provenance is incomplete. | Candidate lifecycle explains execution candidates; upstream research/portfolio provenance remains partial/current-worktree. | "Explain every recommendation" is not yet fully repository-verified. | Extend provenance only through research-only/FR-governed work until approved. |
