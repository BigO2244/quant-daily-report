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

- Orion is the current PAPER-only execution authority under the owner decision
  recorded 2026-08-08. Decision consumes its governed shadow snapshot from the
  current or immediately preceding XNYS session while preserving the exact
  source date/path/hash. Risk may only constrain, and Trader consumes only the
  approved package.
- Polaris remains the historical research baseline and daily shadow comparison control.
- Lyra is the shadow challenger.
- SPY is the benchmark anchor.
- Orion sends PAPER orders only after immutable package validation. The `$500`
  FR-104 live-pilot lane remains blocked and separately governed; no live
  credential, approval, or kill-switch state is changed by the PAPER promotion.
- Execution integrity and target attainment are in observation after the June
  12 FR-070 remediation. New FR-070 implementation work requires classified
  evidence from the next run.
- Shadow NAV recovery is resolved under the owner-approved
  `dated_same_day_close_to_close_v1` operational observation methodology.
  The canonical Shadow observation window begins on 2026-05-12; legacy
  mixed-convention Shadow history is superseded and non-decision-grade for
  promotion or retirement evidence.
- FR-069 research architecture is the next major architecture workstream and
  is moving toward a modular sleeve model aligned with the Caerus Investment
  Doctrine. It remains research-only; Phase C requires separate approval.
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

- `docs/governance/caerus_investment_doctrine.md`
- `docs/governance/CURRENT_RESEARCH_ROADMAP.md`
- `docs/governance/fr_active_backlog.md`
- `docs/governance/fr_registry.md`
- `docs/governance/fr_governance_model.md`
- `docs/governance/CODEX_TASK_TEMPLATE.md`
- `docs/governance/STRATEGIC_ESCALATION_POLICY.md`
- `config/research/strategy_registry.json`

## Current Priority Stack

1. Observe the first governed Orion PAPER convergence run and require clean
   fills/rejections, reconciliation, 2% target attainment, immutable lineage,
   and universal GREEN health before declaring migration complete.
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
