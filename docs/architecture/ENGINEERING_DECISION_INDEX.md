# Engineering Decision Index

Status: architecture-pack finalization draft
Scope: documentation only
Last reviewed: 2026-06-26

Canonical status: Needs Repository Verification until this architecture pack is
committed and reconciled with `origin/main`.

## Document Contract

| Field | Value |
|---|---|
| Purpose | Route major engineering decisions to their source documents and evidence lanes. |
| Owner | Not named in repository; governance ownership requires repository verification. |
| Inputs | FR registry, roadmap, architecture docs, execution contracts, incident evidence. |
| Outputs | Decision index for future engineers and AI agents. |
| Related Documents | `docs/governance/fr_registry.md`, `docs/governance/CURRENT_RESEARCH_ROADMAP.md`, `docs/architecture/DOCUMENT_INDEX.md`. |
| Related Tests | Governance, execution, broker, research, and dashboard tests listed in the rows below. |
| Related Implementation | Cross-system; each row identifies the affected implementation surface. |
| Related Artifacts | FR artifacts, execution run bundles, research outputs, governance hygiene outputs. |
| Known Gaps | Local registry/backlog changes need repository verification before remote-main claims. |

## Decision Rows

| Decision area | Current routing | Implementation surface | Tests / evidence | Status caveat |
|---|---|---|---|---|
| Investment doctrine | `docs/governance/caerus_investment_doctrine.md` | Strategy registry, allocation, promotion gates | Promotion/governance tests | Amend only through explicit governance. |
| Current research state | `docs/governance/CURRENT_RESEARCH_ROADMAP.md` | `research/`, `research_registry/`, strategy registry | Research registry tests, research outputs | Dirty local roadmap needs repository verification. |
| Modular sleeve architecture | FR-069 docs | `research_registry/sleeves/`, `sleeves/` | Sleeve manifest/evidence tests | Research-only unless separately approved. |
| Execution integrity | FR-031, FR-070, FR-074 docs and execution contracts | `scripts/run_precomputed_alpaca_execution.py`, `paper/paper_broker.py`, `core/operational_invariants.py` | Execution, broker, reliability tests | Behavior changes require explicit evidence and approval. |
| Broker-authoritative model | `specs/broker_authoritative_execution_model.md` | Broker adapters, paper broker, broker snapshots | Broker authoritative/reconciliation tests | Broker API truth outranks stale artifacts. |
| FR-104 live pilot | `docs/governance/fr_active/fr_104_live_pilot_unlock_program.md` | `scripts/live_pilot_execute.py`, live-pilot plan/build surfaces | Live-pilot tests, live-pilot artifacts | Dirty local FR-104 needs repository verification; no cron/live production approval. |
| FR-105 optimizer/provenance | `docs/governance/fr_active/fr_105_global_portfolio_optimizer_and_decision_provenance.md` | `research/fr105_*`, `scripts/research/*fr105*` | `Tests/test_fr105_*.py`, `outputs/research/fr_105/` | Current-worktree evidence; research-only. |
| RDP / FR-DH | FR-DH index and `docs/architecture/research_data_platform.md` | `research_data/`, data hydration scripts | Data hydration and sleeve readiness tests | Observe-only; no production sleeve migration. |
| Dashboard source truth | `docs/dashboard_v1_source_map.md` and dashboard docs | Dashboard builders, `web/dashboard/` | Dashboard builder/UI tests | Served payload requires build/VM verification. |
| Documentation architecture | This architecture pack | `docs/architecture/` | `git diff --check`, documentation governance tests when needed | Needs commit and branch reconciliation. |

## Open Decisions

| Decision | Current state | Required evidence |
|---|---|---|
| Candidate lifecycle canonicalization | Current-worktree evidence only. | Commit lifecycle code/test and link real run artifacts when available. |
| Dashboard source-of-truth manifest | Source ownership split across docs/builders/payloads. | Current build and served-file/VM parity proof. |
| Research MCP current capability inventory | Point-in-time docs may lag implementation. | Regenerate capability/tool inventory from current code and tests. |
| RDP production consumer promotion | RDP is observe-only. | Governance approval plus sleeve-consumer migration evidence. |
| Architecture pack canonical adoption | Draft/untracked. | Docs-only commit and branch reconciliation. |

## Historical / Superseded

| Historical item | Current treatment |
|---|---|
| GitHub daily paper workflow | Deprecated wrapper; VM cron is current scheduler evidence. |
| Older Alpha Stack status text | Lineage only when roadmap/registry/strategy registry are newer. |
| Generated reports under `reports/` and `outputs/` | Evidence only unless promoted. |
| `research_mcp_current_state_2026-05-29.md` | Point-in-time snapshot; verify current MCP state from code/tests. |

## Authoritative References

- `docs/governance/fr_registry.md`
- `docs/governance/fr_active_backlog.md`
- `docs/governance/CURRENT_RESEARCH_ROADMAP.md`
- `docs/architecture/DOCUMENT_INDEX.md`
- `docs/architecture/DOCUMENT_GAPS.md`
