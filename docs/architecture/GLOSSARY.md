# Caerus Architecture Glossary

Status: architecture-pack finalization draft
Scope: documentation only
Last reviewed: 2026-06-26

Canonical status: Needs Repository Verification until this architecture pack is
committed and reconciled with `origin/main`.

## Document Contract

| Field | Value |
|---|---|
| Purpose | Define architecture and execution terms used across the architecture pack. |
| Owner | Not named in repository; architecture/governance ownership requires repository verification. |
| Inputs | Execution contracts, broker model, governance docs, candidate lifecycle fixture, system map. |
| Outputs | Shared vocabulary for engineers and AI agents. |
| Related Documents | `docs/architecture/SYSTEM_MAP.md`, `docs/architecture/CAERUS_TECHNICAL_ARCHITECTURE_AND_OPERATING_MANUAL.md`. |
| Related Tests | `Tests/test_candidate_trade_lifecycle.py`, execution and broker tests listed in `DOCUMENT_INDEX.md`. |
| Related Implementation | `paper/paper_broker.py`, `scripts/run_precomputed_alpaca_execution.py`, `core/candidate_trade_lifecycle.py`. |
| Related Artifacts | `outputs/precompute/<date>/planned_execution_payload.json`, `outputs/runs/<run_id>/`. |
| Known Gaps | Complete systemwide recommendation provenance remains `Needs Repository Verification`. |

## Terms

| Term | Meaning | Source / authority |
|---|---|---|
| Accepted | Broker accepted or reported active/filled status for a submitted order. | Broker artifacts, `core/candidate_trade_lifecycle.py`. |
| Alpha Stack | Caerus multi-layer quantitative investment architecture. | `docs/Alpha_Stack_Architecture_Reference.md`. |
| Alpaca | Broker used for paper trading and the separately governed live-pilot evidence lane. | Broker model, FR-104 docs. |
| Artifact | Persisted repository output used as evidence, audit material, or generated operator context. | `docs/artifact_governance.md`. |
| Broker-authoritative | Broker state is truth for actual cash, buying power, positions, account status, and order state. | `specs/broker_authoritative_execution_model.md`. |
| Candidate | A trade row or name being evaluated for execution, clipping, suppression, or broker submission. | Candidate lifecycle fixture and execution artifacts. |
| Candidate lifecycle | Audit surface explaining a candidate's path through planning, filtering, intended orders, rebudgeting, submission, broker response, and reconciliation. | `core/candidate_trade_lifecycle.py`, current-worktree evidence. |
| Clipped | Candidate was reduced before submission, usually by post-sell rebudgeting or capital constraints. | `Tests/test_candidate_trade_lifecycle.py`, current-worktree evidence. |
| FR | Functional Review; governed work item tracked by registry/backlog/docs. | `docs/governance/fr_governance_model.md`, `docs/governance/fr_registry.md`. |
| Filled | Broker state or posttrade reconciliation shows executed quantity. | Broker artifacts and reconciliation code. |
| Partially filled | Broker state shows less filled quantity than submitted quantity. | `paper/paper_broker.py`, broker reconciliation tests. |
| Planned | Present in `outputs/precompute/<date>/planned_execution_payload.json`. | `docs/execution_contract.md`. |
| Recommendation | Model or research output that may explain why a name is proposed, held, excluded, or changed. | Full systemwide provenance remains `Needs Repository Verification`. |
| Reconciled | A reconciliation surface matched the specific question it is designed to answer. Broker reconciliation and target attainment are separate questions. | `docs/execution_integrity_contract.md`. |
| Rejected | Broker response or reconciliation records order rejection or failure. | Broker artifacts and broker rejection tests. |
| Research-only | Work that can produce docs, tests, and artifacts but does not change production behavior. | Governance docs and FR rows. |
| Shadow | Artifact-only strategy observation without capital. | Roadmap, strategy registry, shadow docs. |
| Submitted | Sent to Alpaca or represented as submitted in broker-order artifacts. | `paper/paper_broker.py`, broker artifacts. |
| Suppressed | Candidate was not submitted and carries an explicit reason. | Candidate lifecycle fixture, current-worktree evidence. |
| Target attainment | Read-only analysis of actual portfolio state versus risk-adjusted intended target. | `research/target_attainment.py`, target-attainment tests. |
| VM cron | Current production-like scheduler described by `scripts/crontab.txt`. | `scripts/crontab.txt`. |

## June 25 Fixture Vocabulary

The 8 planned / 2 submitted case is a deterministic regression fixture in
`Tests/test_candidate_trade_lifecycle.py`, not broker runtime history unless a
broker-authoritative run bundle is provided and reconciled.

## Authoritative References

- `docs/execution_contract.md`
- `docs/execution_integrity_contract.md`
- `specs/broker_authoritative_execution_model.md`
- `docs/architecture/SYSTEM_MAP.md`
