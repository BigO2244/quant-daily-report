# Architecture Documentation Governance

Status: architecture-pack finalization draft
Scope: documentation only
Last reviewed: 2026-06-26

Canonical status: Needs Repository Verification until this architecture pack is
committed and reconciled with `origin/main`.

## Document Contract

| Field | Value |
|---|---|
| Purpose | Define governance standards for the architecture pack. |
| Owner | Not named in repository; architecture/governance ownership requires repository verification. |
| Inputs | Existing documentation governance, canonical hierarchy, artifact governance, FR governance. |
| Outputs | Architecture-pack metadata, status, gap, and update standards. |
| Related Documents | `docs/documentation_governance.md`, `docs/documentation/canonical_hierarchy.md`, `docs/architecture/CONTRIBUTING_ARCHITECTURE.md`. |
| Related Tests | `Tests/test_documentation_governance.py`, `Tests/test_governance_hygiene_agent.py`. |
| Related Implementation | `core/documentation/`, `scripts/governance_hygiene_agent.py`, `scripts/validate_documentation_governance.py`. |
| Related Artifacts | `outputs/governance_hygiene/<date>/`, documentation validation output. |
| Known Gaps | Architecture-pack canonical status depends on commit/branch reconciliation. |

## Required Document Contract

Architecture-pack documents should include:

- Purpose
- Owner
- Inputs
- Outputs
- Related Documents
- Related Tests
- Related Implementation
- Related Artifacts
- Known Gaps

If owner or evidence is not verified from the repository, write
`Needs Repository Verification` rather than inferring it.

## Authority Order

Use this order when documents disagree:

1. Machine-readable runtime state, broker state, generated run artifacts, and
   committed code for the specific runtime fact.
2. `docs/governance/caerus_investment_doctrine.md` for strategic doctrine.
3. `docs/governance/fr_registry.md`, `docs/governance/fr_active_backlog.md`,
   and `docs/governance/CURRENT_RESEARCH_ROADMAP.md` for FR/research state.
4. Execution contracts and operator runbooks for operating procedures.
5. Architecture-pack docs for navigation, relationships, and context.
6. Older design specs, generated reports, and historical markdown as evidence
   only unless governance promotes them.

## Document Roles

| Document | Role |
|---|---|
| `README.md` | Entry point and authority pointer for the architecture pack. |
| `SYSTEM_MAP.md` | Fast subsystem navigation and protected-surface map. |
| `DOCUMENT_INDEX.md` | Source-of-truth routing by need and subsystem. |
| `DOCUMENT_INVENTORY.md` | Catalog of documents, tests, artifacts, status, and owners where known. |
| `DOCUMENT_GAPS.md` | Typed documentation backlog. |
| `KNOWLEDGE_GRAPH.md` | Relationship map linking docs, code, tests, artifacts, FRs, and gaps. |
| `CAERUS_TECHNICAL_ARCHITECTURE_AND_OPERATING_MANUAL.md` | End-to-end operating narrative. |
| Companion docs | Principles, glossary, contribution rules, operator routing, governance bridge, and engineering decisions. |

## Status Labels

| Label | Meaning |
|---|---|
| Authoritative | Primary current source for the stated scope. |
| Canonical link target | Stable document that architecture docs can route readers to. |
| Architecture-pack draft | New architecture-pack document pending commit/reconciliation. |
| Design / historical | Useful context, not current runtime authority. |
| Generated evidence | Output evidence, not canonical operator instructions. |
| Needs Repository Verification | Current-worktree, dirty, untracked, or conflicting source. |

## Gap Categories

`DOCUMENT_GAPS.md` uses only these backlog categories:

- Verified Gap
- Repository Verification Required
- Historical Artifact
- Duplicate Documentation
- Architecture Drift

## Update Rules

- New architecture updates should preserve deterministic file names and tables.
- New FRs should update `DOCUMENT_INDEX.md`, `DOCUMENT_INVENTORY.md`,
  `KNOWLEDGE_GRAPH.md`, and `ENGINEERING_DECISION_INDEX.md`.
- New subsystems should update `SYSTEM_MAP.md`.
- New operator procedures should update `OPERATOR_RUNBOOK.md` and link the
  source runbook instead of duplicating it.
- New execution terminology should update `GLOSSARY.md`.
- Unresolved documentation debt belongs in `DOCUMENT_GAPS.md`.

## Promotion Rules

- Generated reports and outputs remain evidence until governance promotes them.
- Current-worktree docs/code/tests remain `Needs Repository Verification` until
  committed and reconciled with the intended branch/deployment target.
- Architecture-pack docs become canonical context only after a docs-only commit
  or an explicit governance decision accepts them.
- Architecture docs may route to an FR, but they do not change FR status.

## Authoritative References

- `docs/documentation_governance.md`
- `docs/documentation/canonical_hierarchy.md`
- `docs/documentation_taxonomy.md`
- `docs/governance/fr_governance_model.md`
