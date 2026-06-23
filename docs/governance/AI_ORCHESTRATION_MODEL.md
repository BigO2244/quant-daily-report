# AI Orchestration Model

## Purpose

This model defines how Brett, ChatGPT, and Codex coordinate Caerus work without
blurring strategic ownership, implementation responsibility, or runtime safety.
The detailed stop-and-escalate rule set lives in
`docs/governance/STRATEGIC_ESCALATION_POLICY.md`.

## Stage 1: Manual Orchestration

Brett and ChatGPT decide priorities, review evidence, and write scoped Codex
tasks manually. Codex executes one task at a time, reports validation, and does
not deploy unless instructed.

Use this stage for FR-070 execution integrity / target attainment, FR-069
modular sleeve architecture, Orion/Lyra evaluation, and any work with strategic
ambiguity.

## Stage 2: Managed Delegation

ChatGPT breaks approved priorities into smaller Codex tasks using
`docs/governance/CODEX_TASK_TEMPLATE.md`. Codex can run audits, produce
diagnostics, add tests, and patch scoped implementation details while preserving
the boundaries in `docs/governance/ORCHESTRATOR_CONTEXT.md`.

This stage is appropriate for read-only governance tooling, MCP reporting,
artifact diagnostics, documentation, test hardening, and narrowly approved
execution-adjacent fixes.

## Stage 3: Operational Autonomy

Codex may eventually execute recurring low-risk operational tasks from
pre-approved templates, such as read-only governance hygiene checks, artifact
sanity checks, and documentation consistency audits.

Operational autonomy does not include strategy promotion, sleeve allocation
changes, trading behavior changes, broker behavior changes, production cron
changes, or deployment without explicit approval.

## Escalation Policy

Strategic escalation is required before work that changes:

- Trading, execution, allocation, broker, cron, or production runtime behavior.
- Strategy or sleeve promotion, retirement, naming, or capital routing.
- Risk posture, target cash, position limits, eligibility gates, or portfolio
  construction.
- The interpretation of Orion or Lyra as retain, retire, promote, or demote.
- Research-only outputs into production decisions.

When in doubt, Codex should stop at audit findings and ask for approval before
patching behavior.

## Worktree And Parallel Task Guidance

- Keep each Codex task scoped to one branch or clean patch set.
- Check `git status --short` before edits.
- Preserve unrelated user changes.
- Do not stage unrelated files.
- Use parallel worktrees only when two tasks must proceed independently and
  their file sets are likely to conflict.
- Avoid mixing governance/docs-only work with runtime patches in the same
  commit.

## Review, Merge, And Deploy Flow

1. ChatGPT or Brett scopes the task with the Codex task template.
2. Codex patches the smallest necessary file set.
3. Codex runs targeted validation and reports residual risk.
4. ChatGPT reviews the result against strategic intent and safety boundaries.
5. Brett approves merge or deployment when needed.
6. VM fast-forward or production deployment happens only after explicit
   instruction.
