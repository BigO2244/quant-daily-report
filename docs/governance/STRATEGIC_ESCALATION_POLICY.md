# Strategic Escalation Policy

## Purpose

This policy defines when AI-assisted Caerus work must stop at analysis and
escalate to Brett through ChatGPT instead of being executed directly by Codex.

## Policy

Escalate when a task would:

- Change trading, execution, allocation, broker, cron, or production runtime
  behavior.
- Promote, retire, rename, or materially alter Polaris, Orion, Lyra, or any
  other strategy or sleeve.
- Change risk posture, cash targets, position limits, eligibility gates, or
  portfolio-construction rules.
- Convert a research-only artifact or diagnostic into production behavior.
- Reinterpret FR-070 or FR-069 in a way that changes execution order or
  strategic intent.
- Require a recommendation that is strategic rather than diagnostic.

## Allowed Without Escalation

Codex may proceed without escalation for:

- Documentation-only updates.
- Read-only diagnostics and artifact readers.
- Scoped tests and validation.
- Templates and governance text that preserve runtime behavior.

## Operating Rule

When uncertain, stop at the smallest defensible diagnostic conclusion and route
the decision back to Brett.
