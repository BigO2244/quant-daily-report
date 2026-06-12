# Orchestrator Context

## Purpose

This document is durable context for AI-assisted Caerus work. Codex should read
it before editing, and ChatGPT should use it as the operating frame for
strategy, review, and task delegation.

## Roles

- Brett is the CIO and product owner. Brett approves strategic direction,
  production trading changes, strategy promotion or retirement, and major
  allocation decisions.
- ChatGPT is the strategic orchestrator and reviewer. It frames priorities,
  decomposes work, challenges assumptions, reviews evidence, and decides when a
  Codex task is scoped enough to execute.
- Codex is the implementation agent. It executes scoped tasks, patches files,
  runs validation, reports results, and preserves repository and runtime safety
  boundaries.

## Current System State

Caerus is a paper-traded quantitative investment platform with deterministic
artifacts and an explicit research-to-production promotion ladder.

- Polaris is the current paper execution baseline.
- Orion is the primary shadow candidate.
- Lyra is the shadow challenger.
- SPY is the benchmark anchor.
- Only Polaris sends orders. Orion and Lyra produce artifacts only.
- Execution integrity and target attainment are in observation after the June
  12 FR-070 remediation. New FR-070 implementation work requires classified
  evidence from the next run.
- FR-069 research architecture is now the primary active workstream and is
  moving toward a modular sleeve model aligned with the Caerus Investment
  Doctrine.
- The live dashboard is nginx-protected with basic auth; recovery uses
  `scripts/reset_dashboard_auth.sh` and does not change trading, execution,
  allocation, or cron behavior.

## Infrastructure Assumptions

- Canonical host: `alpha-stack-scheduler`
- Canonical project: `alpha-stack-490922`
- Canonical zone: `us-central1-a`
- Canonical access method: `gcloud compute ssh brettolson@alpha-stack-scheduler --zone us-central1-a`
- Static IPs are non-authoritative. Resolve the current external IP only when
  a direct SSH path is unavoidable.

## Canonical Governance References

- `docs/governance/caerus_investment_doctrine.md`
- `docs/governance/CURRENT_RESEARCH_ROADMAP.md`
- `docs/governance/fr_active_backlog.md`
- `docs/governance/fr_registry.md`
- `docs/governance/fr_governance_model.md`
- `docs/governance/CODEX_TASK_TEMPLATE.md`
- `docs/governance/STRATEGIC_ESCALATION_POLICY.md`
- `config/research/strategy_registry.json`

## Current Priority Stack

1. FR-069 research lab modular sleeve architecture, design/Phase A only.
2. FR-070 execution integrity / target attainment observation.
3. Continued Orion/Lyra evaluation before any retirement decision.
4. FR-063 remains deprioritized behind FR-069; no retirement decision is
   approved, and any Orion/Lyra disposition belongs inside FR-069.
5. Dashboard auth cleanup remains operational hygiene, not a research priority.

## Operational vs Strategic Changes

Operational changes improve observability, artifact correctness, validation,
runbooks, reliability, or diagnostics without changing trading decisions.

Strategic changes affect strategy selection, sleeve design, capital allocation,
promotion or retirement decisions, risk posture, benchmark framing, or the
economic behavior of the portfolio.

Sleeve allocation changes may be automated later inside approved governance.
Major strategic trading changes still require Brett approval before
implementation or deployment.

## Strategic Escalation Rules

These strategic escalation rules define when Codex must stop and route the
decision back to Brett through ChatGPT.

Escalate to Brett before implementing when a task would:

- Change trading, execution, allocation, broker, cron, or production runtime
  behavior.
- Promote, retire, rename, or materially alter Polaris, Orion, Lyra, or another
  strategy or sleeve.
- Change risk posture, cash targets, position caps, eligibility gates, or
  portfolio construction rules.
- Reinterpret FR-070 or FR-069 priorities in a way that changes execution order
  or strategic intent.
- Convert research-only outputs into production behavior.
- Resolve ambiguous evidence with a strategic recommendation rather than a
  diagnostic conclusion.

Codex may proceed without escalation for clearly scoped documentation,
read-only diagnostics, tests, artifact readers, and templates that preserve
runtime behavior.
