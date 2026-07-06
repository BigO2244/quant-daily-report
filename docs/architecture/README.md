# Caerus Architecture Documentation

Status: architecture-pack index draft
Scope: documentation only
Last reviewed: 2026-06-26

Canonical status: Needs Repository Verification until these docs are committed
and the branch is reconciled with `origin/main`.

This directory is the entrypoint for Caerus architecture context. It is designed
for operators, engineers, and AI agents that need to understand how the system
works without changing runtime behavior.

## Document Contract

| Field | Value |
|---|---|
| Purpose | Entry point for the architecture pack and its authority order. |
| Owner | Not named in repository; architecture/governance ownership requires repository verification. |
| Inputs | Existing docs, governance docs, runbooks, tests-as-docs, artifact patterns, architecture pack. |
| Outputs | Reading path and source-of-truth routing for Caerus architecture context. |
| Related Documents | `docs/architecture/DOCUMENT_INDEX.md`, `docs/architecture/SYSTEM_MAP.md`, `docs/architecture/DOCUMENTATION_GOVERNANCE.md`. |
| Related Tests | Documentation governance tests listed in `DOCUMENT_INDEX.md` when validation is needed. |
| Related Implementation | None directly; routes to implementation references. |
| Related Artifacts | Generated artifact families listed in `DOCUMENT_INDEX.md` and `KNOWLEDGE_GRAPH.md`. |
| Known Gaps | Canonical adoption depends on commit and branch reconciliation. |

## Documents

| Document | Purpose |
|---|---|
| `docs/architecture/SYSTEM_MAP.md` | Fast orientation map for subsystem ownership, canonical entry points, and execution vocabulary. |
| `docs/architecture/DOCUMENT_INDEX.md` | Routing map for canonical docs, source hierarchy, and when to use each reference. |
| `docs/architecture/DOCUMENT_INVENTORY.md` | Inventory of architecture, governance, runbook, research, dashboard, execution, broker, scheduler, test, and artifact documentation. |
| `docs/architecture/DOCUMENT_GAPS.md` | Verified documentation gaps, conflicts, and repository-verification caveats. |
| `docs/architecture/KNOWLEDGE_GRAPH.md` | Subsystem map linking docs, implementation files, tests, artifacts, FRs, and gaps. |
| `docs/architecture/CAERUS_TECHNICAL_ARCHITECTURE_AND_OPERATING_MANUAL.md` | Technical Architecture and Operating Manual. |
| `docs/architecture/ARCHITECTURE_PRINCIPLES.md` | Durable architecture principles and protected surfaces. |
| `docs/architecture/GLOSSARY.md` | Shared architecture and execution vocabulary. |
| `docs/architecture/CONTRIBUTING_ARCHITECTURE.md` | Maintenance checklist for future architecture documentation changes. |
| `docs/architecture/DOCUMENTATION_GOVERNANCE.md` | Architecture-pack governance bridge and document standards. |
| `docs/architecture/OPERATOR_RUNBOOK.md` | Architecture-level route map for operator questions. |
| `docs/architecture/ENGINEERING_DECISION_INDEX.md` | Compact index of durable engineering decisions and evidence lanes. |

## Entry Point Roles

| Role | Document |
|---|---|
| First architecture stop | `docs/architecture/README.md` |
| Fast system navigation | `docs/architecture/SYSTEM_MAP.md` |
| Source-of-truth routing by need/subsystem | `docs/architecture/DOCUMENT_INDEX.md` |
| Operating explanation | `docs/architecture/CAERUS_TECHNICAL_ARCHITECTURE_AND_OPERATING_MANUAL.md` |
| Full subsystem linkage | `docs/architecture/KNOWLEDGE_GRAPH.md` |
| Inventory and status labels | `docs/architecture/DOCUMENT_INVENTORY.md` |
| Verified open gaps | `docs/architecture/DOCUMENT_GAPS.md` |
| Principles | `docs/architecture/ARCHITECTURE_PRINCIPLES.md` |
| Vocabulary | `docs/architecture/GLOSSARY.md` |
| Contribution workflow | `docs/architecture/CONTRIBUTING_ARCHITECTURE.md` |
| Documentation governance | `docs/architecture/DOCUMENTATION_GOVERNANCE.md` |
| Operator routing | `docs/architecture/OPERATOR_RUNBOOK.md` |
| Decision routing | `docs/architecture/ENGINEERING_DECISION_INDEX.md` |

## Authority Order

Use `docs/architecture/DOCUMENTATION_GOVERNANCE.md` for architecture-pack
authority order and update rules.

If a claim cannot be verified from the repository, write
`Needs Repository Verification` rather than filling the gap.

## Scope Boundaries

This architecture pack does not modify production code, runtime behavior,
tests, broker behavior, scheduler behavior, allocation logic, or promotion
state. It catalogs and links existing evidence.

Future architecture documentation should be added here when it is stable enough
to serve as reusable context. Generated reports and daily artifacts should stay
under their generated-output roots unless explicitly promoted by governance.
