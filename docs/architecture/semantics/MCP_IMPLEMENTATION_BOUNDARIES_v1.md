---
last_reviewed: 2026-05-21
owner: architecture
category: architecture
criticality: critical
canonical: true
related_systems: [mcp, research, governance, broker, execution]
spec_id: SEM-MCP-BOUNDS-v1
spec_version: v1
supersedes: null
governs: [SEM-001, SEM-002, SEM-003, SEM-004, SEM-006, SEM-007]
---

# MCP Implementation Boundaries v1 — Caerus Research MCP

**Spec ID:** SEM-MCP-BOUNDS-v1
**Version:** v1
**Date:** 2026-05-21
**Status:** Canonical — **CONSTITUTIONAL**
**Normative Language:** RFC 2119
**Frozen Under:** `SEMANTIC_FREEZE_v1.md`

---

## 1. Purpose

This document defines the **constitutional boundaries** of the Caerus
Research MCP. It is the hardest, narrowest, most consequential
specification in the Semantic Contract Layer.

The MCP exists in the research plane. It MUST NOT cross into the
execution plane. It MUST NOT mutate operational state. It MUST NOT
trigger workflows or orchestrate agents. It MUST NOT access broker
credentials. These are not engineering preferences — they are
structural firewalls against the failure modes that destroy
institutional research over a multi-year horizon.

The boundaries here are stated as absolute prohibitions and absolute
permissions. There is no override path for prohibitions in this
document. If a capability is needed that this document forbids, the
correct response is **not to add it to the MCP**; the correct response
is to build it in a different system on the other side of the
firewall.

---

## 2. The MCP IS

The Caerus Research MCP IS, and only IS, the following.

### 2.1 A Research Cognition Layer

The MCP IS a layer that reasons over the Caerus research corpus — its
provenance, confidence, surface, governance, temporal validity, and
lineage. Reasoning means **answering structured questions** against
structured objects, returning structured envelopes with full
provenance and confidence chains.

### 2.2 A Read-Only Index Over the Research Substrate

The MCP IS a read-only index. Its only persistent state is the metadata
registry (architecture §10), which is a **derived artifact** rebuildable
from source. The research substrate (`outputs/`, `data/`, `docs/`,
`regime/`, `alpha_stack/`, `logs/`, `config/`, `signals_store/`) is
authoritative; the registry is a projection.

### 2.3 A Provenance-Bearing Query Surface

Every response the MCP returns IS a research object envelope (SEM-001)
or a structured reply containing envelopes. The MCP IS the
implementation of the semantic contract over query responses.

### 2.4 A Conformance Enforcer

The MCP IS the institutional enforcer of SEM-001..008 at the read
boundary. Producers stamp envelopes; the MCP MUST re-validate.
Producers compute confidence; the MCP MUST re-derive at hydration.
Producers declare governance; the MCP MUST reconcile against the FR
registry.

### 2.5 A Temporal-Honesty Surface

The MCP IS the system that answers "what did Caerus believe at T?" It
IS the operational implementation of SEM-006.

### 2.6 An Auditable System

The MCP IS auditable: every response is reproducible, every operation
is logged, every finding is recorded, every replay is documented as a
`ReplayRun` object.

---

## 3. The MCP IS NOT

The MCP IS NOT, and MUST NEVER become, the following.

### 3.1 Not an Execution System

The MCP IS NOT an order management system, an execution engine, a
broker client, a fill router, a portfolio rebalancer, or any other
operational artifact. It MUST NOT submit, modify, cancel, or simulate
orders.

### 3.2 Not a Deployment System

The MCP IS NOT a deployment orchestrator, CI/CD trigger, infrastructure
manager, container runtime, or change-promoter. It MUST NOT push code,
update services, or modify operational configuration.

### 3.3 Not an Autonomous Agent

The MCP IS NOT an autonomous decision-maker. It does not select
strategies for promotion. It does not transition FR lifecycle states.
It does not approve audit findings. It does not initiate rollbacks. It
reports state; humans (or governance processes) decide.

### 3.4 Not a Workflow Orchestrator

The MCP IS NOT a scheduler, cron, queue, or pipeline runner. It does
not trigger producers, pipelines, scripts, or notebooks. Producers run
on their own schedules under FR/OPS governance; the MCP indexes their
outputs after the fact.

### 3.5 Not a Writable Substrate

The MCP IS NOT a write surface for the storage substrate. It does not
edit, replace, or delete files in `outputs/`, `data/`, `docs/`, or any
other research path. It does not produce research artifacts; it
indexes those produced by governed pipelines.

### 3.6 Not a Credential Holder

The MCP IS NOT a holder of broker credentials, deployment SSH keys,
GitHub tokens with write scope, GCP service-account keys with mutation
authority, or any other credential whose use mutates state. The MCP's
environment MUST exclude these credentials by construction.

### 3.7 Not a Truth Source for Operational State

The MCP IS NOT the source of truth for broker positions, broker
balances, live orders, or any operational state. The broker is
authoritative. The MCP reads broker-snapshot artifacts after the fact
and indexes them; it does not query the broker directly for
operational state.

### 3.8 Not a Substitute for Human Judgement

The MCP IS NOT a replacement for analyst, operator, or auditor
judgement. It surfaces facts with full provenance and lets the human
decide. A confidence ceiling does not mean "do this"; it means "this
is what we know, with these limits."

---

## 4. The MCP MAY DO

The MCP MAY perform the following operations.

### 4.1 Read Operations

- MAY read from the storage substrate at paths declared in its
  configuration scope.
- MAY parse JSON, CSV, Parquet, Markdown front-matter, and other
  declared formats.
- MAY traverse the lineage DAG upstream and downstream from any
  object.
- MAY hydrate, validate, and serve any conformant envelope.

### 4.2 Index Operations

- MAY build, maintain, rebuild, and shadow-rebuild its own registry
  index.
- MAY cache hydrated envelopes per SEM-REGISTRY-v1 §3.3.
- MAY compute and store derived metrics (governance coverage trends,
  staleness summaries, orphan counts) provided every metric is
  reproducible from source.

### 4.3 Reasoning Operations

- MAY answer queries that synthesise across multiple objects, provided
  every synthesis respects the compatibility matrix (SEM-002) and the
  confidence algebra (SEM-007).
- MAY produce `ReplayRun` records, `AuditFinding` records (for
  conformance violations), and other first-class governance-bearing
  objects whose production is itself governed by the semantic
  contract.
- MAY compute point-in-time reconstructions under SEM-006.

### 4.4 Reporting Operations

- MAY emit findings, governance health reports, coverage trends, and
  replay audits.
- MAY emit observability signals (metrics, logs, traces) describing
  its own behaviour.
- MAY respond to interactive queries from authorised consumers (CLI,
  IDE integration, web client, programmatic clients).

### 4.5 Conformance Refusal

- MAY (and MUST) refuse to serve non-conformant envelopes.
- MAY (and MUST) refuse cross-surface combinations that violate the
  compatibility matrix.
- MAY (and MUST) refuse point-in-time queries that would require
  post-anchor inputs.
- MAY (and MUST) refuse confidence upgrades absent a covering
  `ConfidenceAssessment`.

Refusal is a legitimate, expected, often-required MCP response.
Refusal is not an error; it is conformance in action.

---

## 5. The MCP MUST NEVER DO

The MCP MUST NEVER perform any operation in this section. There is no
override path. These prohibitions are absolute under v1 and survive any
amendment that does not constitute a successor freeze.

### 5.1 MUST NEVER Mutate Execution State

The MCP MUST NEVER:

- Submit, cancel, modify, or simulate orders against any broker.
- Modify broker account state, positions, balances, or risk parameters.
- Trigger order execution via any indirect path (queue, file drop,
  webhook).
- Create or modify any artifact whose existence would, by downstream
  consumption, cause execution to occur.

### 5.2 MUST NEVER Mutate Deployment State

The MCP MUST NEVER:

- Push to any git remote.
- Trigger any CI/CD pipeline.
- Modify any deployment configuration, service definition, container
  image, or infrastructure resource.
- Restart, stop, start, or otherwise control any operational service.

### 5.3 MUST NEVER Access Broker Credentials

The MCP MUST NEVER:

- Read broker API keys, secrets, or tokens.
- Be deployed in an environment where broker credentials are
  accessible via filesystem, environment variable, or in-process
  memory.
- Hold a network route to any broker endpoint that could be used to
  submit orders.

### 5.4 MUST NEVER Operate as an Autonomous Orchestrator

The MCP MUST NEVER:

- Decide which research pipeline to run.
- Trigger any producer module (script, notebook, pipeline) as a
  consequence of any internal decision.
- Sequence research operations via internal scheduling.
- Self-promote findings into operational governance actions.

### 5.5 MUST NEVER Trigger Workflows

The MCP MUST NEVER:

- Invoke external workflow runners (Airflow, Prefect, GitHub Actions,
  cloud-functions, etc.).
- Create webhook calls that cause workflow execution.
- Write to a "command queue" file or table consumed by workflow
  runners.
- Communicate with any system whose purpose is workflow orchestration.

### 5.6 MUST NEVER Self-Modify

The MCP MUST NEVER:

- Modify its own source code at runtime.
- Modify the Semantic Contract Layer documents.
- Modify its own configuration in a way that alters its semantic
  surface.
- Introduce new tool capabilities at runtime that were not present at
  startup.

Self-extension via configuration is permitted only within the closed
capability set declared at architecture time. The MCP's surface area
is fixed at build; runtime adapts behaviour, not capabilities.

### 5.7 MUST NEVER Perform Hidden Writes

The MCP MUST NEVER:

- Write to any path outside its declared registry directory.
- Write to its registry directory anything not derivable from source.
- Write to logs, traces, or metrics in a way that injects information
  into the substrate.
- Open any file in write mode on the storage substrate, ever.

Hidden writes — writes that occur outside the MCP's declared write
surface or whose purpose is not the registry index — are forbidden by
construction. The MCP's process MUST be filesystem-isolated such that
hidden writes are impossible, not merely prohibited.

### 5.8 MUST NEVER Permit Silent Confidence Upgrades

The MCP MUST NEVER serve a confidence value that exceeds the
recomputed floor (SEM-007 §3) absent an active, valid
`ConfidenceAssessment` (SEM-007 §6). Silent upgrade is forbidden in
the storage path, the retrieval path, the display path, and any tool
response path.

### 5.9 MUST NEVER Strip Provenance

The MCP MUST NEVER:

- Re-emit an envelope with fields omitted (SEM-001 §9).
- Aggregate envelopes in a way that loses the source set.
- Cache a response in a form that cannot reproduce the full chain.
- Present any research conclusion without the underlying
  envelope-bearing evidence.

### 5.10 MUST NEVER Permit Future-Information Contamination

The MCP MUST NEVER:

- Consume an artifact whose `as_of > T_anchor` in an as-of-`T_anchor`
  view.
- Consume an artifact whose `indexed_at > T_anchor` in an
  as-of-`T_anchor` view.
- Project current state onto past objects in any reply.
- Allow hybrid reconstruction to be served as if it were canonical.

Future-information contamination is the single most institutionally
destructive failure mode the MCP can commit. It MUST be structurally
impossible — enforced at the retrieval layer, verified by audit,
detected by replay divergence.

### 5.11 MUST NEVER Override Governance

The MCP MUST NEVER:

- Transition an FR's status.
- Approve, close, or modify an `AuditFinding`.
- Initiate a `Rollback` or `Deployment`.
- Mark a `PromotionAssessment` as `governance_readiness = true` outside
  governance process.

The MCP observes, indexes, and reports governance state. It does not
*act* on governance.

### 5.12 MUST NEVER Bypass FR Governance

The MCP MUST NEVER:

- Hand-code governance state assertions that contradict the FR
  registry.
- Compute coverage using its own opinions of what should govern
  rather than the FR registry's declarations.
- Treat its own findings as governance authority (findings are
  *inputs* to governance, not outputs).

### 5.13 MUST NEVER Treat Itself as a Producer of Operational Truth

The MCP MUST NEVER claim that any reconstructed view is operational
truth. Reconstructions are *views* (SEM-006 §4.2); canonical truth
lives in the corpus. Aggregations are *projections*; the underlying
envelopes are the records.

---

## 6. Research-Plane Boundaries

The MCP operates in the **research plane**. The research plane is
defined by:

- Read-only access to the storage substrate.
- Read-only access to the FR registry and governance source.
- Read-only access to schema definitions and the Semantic Contract
  Layer.
- Write access **only** to the MCP's own registry directory.
- Network access **only** to local-host and explicitly authorised
  research surfaces (e.g., interactive clients).

The research plane MUST be isolated from the **execution plane**,
which is defined by:

- Broker credential access.
- Order submission paths.
- Deployment authority.
- Operational service control.

The two planes MUST NEVER overlap. They MUST be separated by
filesystem permissions, network policy, credential isolation, and
process boundary. If a system needs to span both planes, it MUST be
two systems with explicit, governed communication — not one MCP.

---

## 7. Execution-Plane Separation

Execution-plane separation is enforced by construction and by
operations. The MCP's deployment topology MUST satisfy:

### 7.1 Filesystem Isolation

- The MCP process MUST run under a UID with read-only permission on
  the storage substrate.
- The MCP MUST have write permission only on its registry directory.
- The MCP MUST NOT have any permission on broker-credential paths,
  deployment-key paths, or operational-config paths.

### 7.2 Network Isolation

- The MCP MUST NOT have egress to broker endpoints.
- The MCP MUST NOT have egress to CI/CD APIs (GitHub Actions, etc.)
  with mutation scope.
- The MCP MUST NOT have egress to deployment APIs (GCP compute, IAM,
  storage write).
- Local-host bindings for interactive clients are permitted.

### 7.3 Credential Isolation

- Broker credentials MUST NOT exist in the MCP's environment.
- Deployment credentials MUST NOT exist in the MCP's environment.
- The MCP's environment variables MUST be enumerable and limited to
  read-only, research-plane scope (paths, log levels, registry
  configuration).

### 7.4 Process Isolation

- The MCP process MUST run independently of any execution-bearing
  process.
- The MCP MUST NOT share IPC channels with execution processes.
- The MCP MUST NOT be invoked as a subprocess of an execution
  pipeline; it is invoked by consumers of research intelligence, not
  by producers of operational state.

---

## 8. Required Read-Only Enforcement

Read-only enforcement is not a configuration toggle; it is a
structural requirement.

- The MCP's storage-substrate access MUST be enforced at the OS level
  (mount options, ACLs, or equivalent), not by application-level
  convention.
- The MCP's process MUST refuse to start if it detects write
  permission on the storage substrate.
- The MCP's audit subsystem MUST periodically verify and report
  read-only enforcement (e.g., by attempting and confirming refusal of
  a test write).
- A write succeeding on any substrate path during MCP operation MUST
  trigger immediate process termination and a `READONLY_BREACH`
  finding of severity `CRITICAL`.

---

## 9. Required Credential Isolation

Credential isolation is enforced by construction.

- The MCP MUST be deployed in an environment whose credential surface
  contains **only** credentials needed for the research plane (e.g.,
  read scopes on storage, identity for interactive clients).
- The MCP MUST refuse to start if it detects any credential matching
  patterns associated with broker, deployment, or mutation scope
  (heuristic detection is acceptable; the principle is fail-closed).
- Audit MUST verify credential isolation on every deployment and
  report any drift.

The MCP architecture document §14 specifies the security model; this
document hardens that specification to a constitutional invariant.

---

## 10. Enforcement Mechanisms

The boundaries in this document are enforced by a combination of
structural (impossible-by-construction) and operational
(verified-by-audit) controls.

### 10.1 Structural Enforcement

| Boundary | Structural Mechanism |
|---|---|
| Read-only substrate | OS-level mount or ACL. |
| No broker access | Network policy denying broker egress. |
| No deployment authority | Service-account scope. |
| No credential access | Environment-variable allowlist; secret-manager exclusion. |
| No hidden writes | Filesystem write permission limited to registry directory. |
| No self-modification | Source-code path read-only at runtime. |

Structural enforcement is preferred over operational enforcement
because it cannot be silently disabled by a bug.

### 10.2 Operational Enforcement

| Boundary | Operational Mechanism |
|---|---|
| Read-only verification | Periodic audit write-attempt against substrate (expects failure). |
| Egress monitoring | Network connection logs reviewed for unauthorised egress. |
| Privilege drift | Periodic re-verification of process UID and ACLs. |
| Conformance | Continuous Freeze Conformance Report. |
| Replay integrity | Scheduled and on-demand replay audits. |

Audit findings of severity `CRITICAL` arising from boundary violations
MUST suspend the MCP's serving role until remediation. The MCP is not
permitted to continue serving while a constitutional boundary is
demonstrably violated.

---

## 11. Failure Mode Discipline

When the MCP encounters a situation it MUST NOT handle (e.g., a
prohibited write request, an instruction to mutate broker state, an
implicit authorisation request from a consumer), the MCP MUST:

1. **Refuse** the operation.
2. **Emit a finding** documenting the refusal, the requester, and the
   prohibited operation.
3. **Return a structured refusal response** to the requester (e.g.,
   `{"refusal": "OPERATION_OUTSIDE_RESEARCH_PLANE", ...}`).
4. **Continue serving** other requests normally — the refusal is not a
   crash; it is a designed response.

The MCP MUST NEVER:

- Attempt the prohibited operation "with a note."
- Approximate the operation with a "research-plane analogue."
- Suggest to the requester that the prohibition can be lifted by
  configuration.
- Treat the prohibition as a temporary state to be resolved by future
  permission.

The prohibitions are constitutional. They are not negotiable at
runtime.

---

## 12. Relationship to Architecture and Specifications

This document hardens the architecture document's §1 (Design
Philosophy) and §14 (Security Model) into RFC 2119 invariants frozen
under v1.

It restates and tightens the following architectural commitments:

| Source | Commitment |
|---|---|
| Architecture §1.Core Invariants | Read-only, provenance-first, governance-aware, confidence-transparent, temporally honest. |
| Architecture §14 | Least privilege, research plane only, credential isolation. |
| Architecture §18 Risk 6 | Scope creep toward execution is a known risk; this document is the structural countermeasure. |
| SEM-001 | Envelope as non-negotiable surface. |
| SEM-003 | Provenance immutability and DAG integrity. |
| SEM-006 | Temporal honesty and no future-information contamination. |
| SEM-007 | No silent confidence upgrades. |

If a future architectural revision proposes to relax any boundary in
this document, that revision MUST be treated as a successor-freeze
proposal (SEM-FREEZE-v1 §19), not as an architectural amendment.

---

## 13. Errata

*(none at v1)*

---

*SEM-MCP-BOUNDS-v1 — 2026-05-21. Caerus Semantic Contract Layer.*
*Owner: Architecture / Research Infrastructure.*
*Classification: Institutional — Constitutional MCP Boundary.*
