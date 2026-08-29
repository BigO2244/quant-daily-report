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

Caerus is a quantitative investment platform with separately governed Live,
PAPER, and Shadow lanes, deterministic
artifacts and an explicit research-to-production promotion ladder.

- Lyra Live is active, funded, recurring, and separately governed from Orion PAPER.
- Orion is the active PAPER capital sleeve. Polaris, Orion, and Lyra also run in
  Shadow for modeled comparison; Shadow status does not negate capital authority.

- The owner-approved 2026-08-14 portfolio operating-model migration is
  implemented. Precompute admits one immutable session, produces one terminal
  daily decision for every registered non-frozen sleeve, and applies one
  configured account allocator. Orion currently receives 100% of sleeve risk
  budget; adding sleeves is a complete registry/policy change, not a new lane.
- The session, decisions, allocation, sealed target, exact plan, fills, causal
  ownership, valuation, and daily audit retain immutable hash lineage. Risk may
  only constrain. Recovery target substitution and downstream strategy
  reconstruction are rejected. Legacy planner output is quarantined research.
- The first scheduled schema-3 PAPER session ran on 2026-08-17. Its sealed
  target was valid, but the first 09:35 exact plan incorrectly sized against a
  synthetic $10,000 planning basis while broker-authoritative NAV was
  $11,822.55. A governed correction reconciled the final portfolio. Commit
  `5c0a2cd` now rejects any shrinking PAPER cap and independently validates the
  full-account basis before WAL or broker submission. The original artifact is
  retained as incident evidence.
- Actual PAPER accounting comes directly from Alpaca at 19:15 ET. Broker fills
  are mapped to exact decisions, positions reconcile to causal ownership, and
  account/position valuation shares one `pulled_at_utc`. The strict 19:45 build
  has no model fallback and writes the end-of-day portfolio audit.
- Polaris remains the historical research baseline and daily shadow comparison control.
- Lyra is the Shadow challenger and independently operates the owner-approved
  Live weekly portfolio.
- Adaptive Shadow v1 candidate hash `0ee486...` is owner-approved for
  observation only across Polaris and Lyra. All preregistered readiness gates
  remain binding; the first enabled readiness result fails closed to static
  Polaris because governed decision, signal, history, membership, and
  constraint inputs are incomplete. It grants no PAPER/Live or execution
  authority.
- SPY is the benchmark anchor.
- Orion sends PAPER orders only after immutable package validation. The legacy
  FR-104 live-pilot lane remains blocked and separately governed; that fact
  does not disable Lyra Live.
- Execution integrity and target attainment are in observation after the June
  12 FR-070 remediation. New FR-070 implementation work requires classified
  evidence from the next run.
- Shadow NAV recovery is resolved under the owner-approved
  `dated_same_day_close_to_close_v1` operational observation methodology.
  The canonical Shadow observation window begins on 2026-05-12; legacy
  mixed-convention Shadow history is superseded and non-decision-grade for
  promotion or retirement evidence.
- The production control plane is now modular across sleeves. FR-069 remains a
  research/model-design workstream; research promotion still requires explicit
  approval before a sleeve is added to the capital allocation policy.
- The live dashboard is nginx-protected with basic auth; recovery uses
  `scripts/reset_dashboard_auth.sh` and does not change trading, execution,
  allocation, or cron behavior.

## Infrastructure Assumptions

- Canonical host: `alpha-stack-scheduler`
- Canonical project: `alpha-stack-490922`
- Canonical zone: `us-central1-a`
- Canonical access method for Codex tasks: `ssh caerus-vm`
- Do not use `gcloud compute ssh` when the `caerus-vm` SSH alias is available.
- Static IPs are non-authoritative.
- Canonical non-interactive VM validation:
  `ssh caerus-vm 'cd ~/quant-daily-report && ./scripts/ops/run_vm_validation.sh'`
- Canonical VM deployment:
  `ssh caerus-vm 'cd ~/quant-daily-report && ./scripts/deploy.sh'`
- A raw VM `git pull` or `git merge` is not a completed deployment because it
  does not produce the validated full-SHA attestation required by live execution.

## Canonical Governance References

- `docs/governance/WORKFLOW_AUTHORITY_REGISTRY.md`
- `docs/governance/caerus_investment_doctrine.md`
- `docs/governance/CURRENT_RESEARCH_ROADMAP.md`
- `docs/governance/fr_active_backlog.md`
- `docs/governance/fr_registry.md`
- `docs/governance/fr_governance_model.md`
- `docs/governance/CODEX_TASK_TEMPLATE.md`
- `docs/governance/STRATEGIC_ESCALATION_POLICY.md`
- `config/research/strategy_registry.json`

## Cross-Authority Workflow Rule

Workflow truth is resolved across Codex automations, workspace/project
operating files, deployed VM state, and GitHub. Repository cron alone cannot
prove that a research workflow is absent. The owner-approved Alpha Lab weekend
research cycle runs through Codex Sunday at 00:05 America/New_York and remains
research-only. Its presence does not grant automatic experiment, Shadow,
Paper, Live, allocation, or execution authority.

## Current Priority Stack

1. Keep execution semantics frozen while observing the next scheduled
   post-remediation schema-3 PAPER session. Require the full-account invariant,
   clean fills/rejections, reconciliation, causal ownership, same-as-of
   valuation, daily audit, and universal GREEN health before expanding work.
2. FR-069 research lab modular sleeve architecture is the next major
   architecture workstream; it remains research-only unless a separately
   governed implementation phase is approved.
3. Continued Polaris/Orion/Lyra research comparison before any retirement decision;
   PAPER execution authority does not settle long-horizon research viability.
4. FR-063 remains active supporting differentiation evidence under FR-069; no
   retirement decision is approved, and any Orion/Lyra disposition belongs
   inside FR-069 after sufficient canonical new-series evidence exists.
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
