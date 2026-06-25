# AI Chief Of Staff Workflow

## Purpose

Caerus uses a lightweight AI Chief of Staff workflow to coordinate Brett,
ChatGPT, Codex, and reviewers without making Brett the message bus.

GitHub Issues are the primary work queue. Pull requests are the execution and
review vehicle. The optional `.aios/` directory may hold supporting metadata or
experiment artifacts, but it is not the user-facing workflow.

## Workflow

1. Brett discusses priority, ambiguity, and risk with ChatGPT.
2. ChatGPT creates or refines a GitHub Issue with objective, scope, safety
   boundaries, likely files, and validation expectations.
3. Brett tells Codex: `Implement Issue #X.`
4. Codex reads the issue, creates a branch, implements the scoped change, runs
   validation, and opens a PR.
5. Brett tells ChatGPT: `Review PR #Y.`
6. ChatGPT reviews the PR against the issue, Caerus governance, evidence, and
   validation results. Claude may be asked for independent challenge review.
7. Brett approves, requests changes, or closes the loop with a new issue.

## Roles

- Brett owns priorities, approvals, product direction, strategic decisions, and
  merges.
- ChatGPT scopes issues, identifies missing context, reviews PRs, and keeps the
  workflow aligned with Caerus governance.
- Codex implements issues, writes tests or docs, records findings in PRs or
  committed artifacts, and reports validation.
- Claude provides independent review and challenges assumptions when requested.
- GitHub stores durable state through issues, PRs, comments, commits, and
  review history.

## Operating Rules

- GitHub Issues are the work queue.
- PRs are the durable implementation, validation, and review record.
- Chat is temporary unless promoted into an issue, PR, commit, or artifact.
- Brett should not need to copy large context packets between tools.
- `.aios/` is optional supporting metadata, not a required queue or platform.
- Custom queue branches are deprecated unless a future issue explicitly revives
  them.
- Existing AIOS smoke-test queue artifacts are historical experiment evidence,
  not the normal operating path.

## Codex Expectations

When Brett says `Implement Issue #X`, Codex should:

- Read the issue before editing.
- Pull only the additional repository context needed to execute safely.
- Preserve unrelated user changes.
- Keep docs-only work separate from runtime changes.
- Open a PR when implementation is complete.
- Put findings, validation commands, and residual risks in the PR body or a
  committed markdown artifact.
- State clearly whether production or trading logic changed.

## Review Expectations

When Brett says `Review PR #Y`, ChatGPT or Claude should:

- Compare the PR to the linked issue.
- Check whether the validation evidence supports the claim.
- Identify any production, trading, broker, cron, allocation, or governance risk.
- Ask for changes when evidence is missing or scope drifted.
- Summarize the approval decision or remaining blockers.

## What This Replaces

The prior AIOS queue-branch experiment proved that AI agents can coordinate
through repository files, but it added too much operational friction. Caerus now
defaults to GitHub Issues and PRs as the durable bus. Queue files under `.aios/`
or queue branches are retained only as experimental history unless a future issue
explicitly scopes their use.
