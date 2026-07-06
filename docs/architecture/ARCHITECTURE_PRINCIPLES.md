# Caerus Architecture Principles

Status: architecture-pack finalization draft
Scope: documentation only
Last reviewed: 2026-06-26

Canonical status: Needs Repository Verification until this architecture pack is
committed and reconciled with `origin/main`.

## Document Contract

| Field | Value |
|---|---|
| Purpose | State the durable engineering principles that govern Caerus architecture documentation and change review. |
| Owner | Not named in repository; architecture/governance ownership requires repository verification. |
| Inputs | `AGENTS.md`, governance docs, execution contracts, runbooks, artifact-governance docs, architecture pack. |
| Outputs | Principles used by the Technical Architecture and Operating Manual, system map, document index, and future architecture PRs. |
| Related Documents | `docs/architecture/CAERUS_TECHNICAL_ARCHITECTURE_AND_OPERATING_MANUAL.md`, `docs/architecture/DOCUMENTATION_GOVERNANCE.md`, `docs/governance/ORCHESTRATOR_CONTEXT.md`. |
| Related Tests | Documentation governance tests and subsystem behavioral tests referenced by `docs/architecture/DOCUMENT_INDEX.md`. |
| Related Implementation | Cross-system; no runtime implementation owned by this document. |
| Related Artifacts | Generated artifacts named in `docs/architecture/KNOWLEDGE_GRAPH.md`. |
| Known Gaps | Owner and committed-canonical status need repository verification. |

## Principles

1. Broker truth outranks local replay.
   Alpaca is authoritative for actual cash, buying power, positions, account
   status, and order state. Local artifacts are audit and replay surfaces.

2. Governance permission is separate from implementation possibility.
   Research-only docs, tests, and artifacts do not authorize allocation,
   execution, broker, scheduler, promotion, or runtime behavior changes.

3. Execution fails closed when evidence is contradictory or missing.
   Nonempty planned payloads, submitted orders, sell fills, rebudgeting,
   reconciliation, and operator reporting must expose explicit reasons rather
   than degrade to silent success.

4. Generated artifacts are evidence, not instructions.
   `outputs/`, `reports/`, dashboard payloads, and generated markdown support
   auditability. They become operator guidance only when promoted by governance.

5. Documentation routes to sources of truth.
   Architecture docs should identify ownership, context, and link targets. They
   should not copy long canonical docs or hide stale/conflicting evidence.

6. Tests are behavioral documentation.
   Tests can document expected behavior, but synthetic tests do not replace
   broker-authoritative runtime evidence or VM validation.

7. Dirty-worktree evidence is not durable truth.
   Current-worktree docs/code/tests can be useful evidence, but they must be
   marked `Needs Repository Verification` until committed, reconciled, and
   verified against the intended branch/deployment target.

8. New architecture requires navigation updates.
   New subsystems, FRs, runbooks, or artifact families should update
   `SYSTEM_MAP.md`, `DOCUMENT_INDEX.md`, `DOCUMENT_INVENTORY.md`,
   `KNOWLEDGE_GRAPH.md`, and `DOCUMENT_GAPS.md` when relevant.

## Protected Surfaces

Do not change these casually or as part of docs-only work:

- Execution submission, sell/buy sequencing, and fail-closed behavior.
- Broker adapters, broker truth precedence, and reconciliation semantics.
- Scheduler/cron timing and runtime configuration.
- Allocation, sleeve promotion, strategy naming, retirement, and capital policy.
- Live-pilot controls, caps, approval gates, and rollback paths.
- Documentation authority order and FR status routing.

## Subsystem-Specific Principle Sources

| Subsystem | Principle source |
|---|---|
| Investment Doctrine | `docs/governance/caerus_investment_doctrine.md` |
| Execution | `docs/execution_contract.md`, `docs/execution_integrity_contract.md` |
| Broker | `specs/broker_authoritative_execution_model.md` |
| Governance | `docs/governance/fr_governance_model.md`, `docs/governance/fr_registry.md` |
| Research data | `docs/governance/fr_active/data_hydration/fr_dh_000_data_hydration_index.md`, `docs/architecture/research_data_platform.md` |
| Documentation | `docs/documentation_governance.md`, `docs/architecture/DOCUMENTATION_GOVERNANCE.md` |

## Authoritative References

- `docs/governance/caerus_investment_doctrine.md`
- `docs/governance/fr_governance_model.md`
- `docs/execution_contract.md`
- `docs/execution_integrity_contract.md`
- `specs/broker_authoritative_execution_model.md`
- `docs/artifact_governance.md`
- `docs/documentation_governance.md`
