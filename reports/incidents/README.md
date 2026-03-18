# Trade Incident Scaffold

## Naming Convention

Create one folder per incident under `reports/incidents/` using a deterministic identifier.

Recommended pattern:

`YYYY-MM-DD_<mode>_<short_cause>`

Examples:

- `2026-03-16_alpaca_paper_broker_reject_pdt`
- `2026-04-02_shadow_workflow_skip`
- `2026-04-11_alpaca_paper_summary_misreport`

Keep names lowercase and use underscores only.

## Numbered Files

Each incident folder should contain these files in order:

- `01_spec.md`
  The task framing for the audit. This should state scope, constraints, evidence targets, and required deliverable structure.
- `02_audit_report.md`
  The completed diagnostic report with causal chain, root cause, evidence, and confidence.
- `03_patch_summary.md`
  A narrow implementation note if a fix or hardening change was applied. State exact files touched and why.
- `04_validation.md`
  Commands run, results, and any dry-run or replay validation used to prove the diagnosis or patch.

## How To Use The Scaffold

1. Copy the structure from `example_trade_incident_audit/`.
2. Rename the folder using the incident naming convention.
3. Fill `01_spec.md` before making changes.
4. Produce `02_audit_report.md` from direct evidence, not memory.
5. Only add `03_patch_summary.md` content if a narrow fix is justified.
6. Record validation commands and outcomes in `04_validation.md`.

## What Good Incident Documentation Looks Like

Good incident documentation is:

- specific about the exact stop point in control flow
- explicit about whether execution was attempted
- clear about what was accepted, rejected, filtered, or skipped
- backed by artifact paths, log lines, and JSON fields
- deterministic enough that another operator can reproduce the reasoning
- honest about ambiguity and what remains unproven

Poor incident documentation:

- says only "no trades occurred"
- omits the governing gate or workflow condition
- relies on a summary artifact without checking raw logs
- mixes true execution failure with reporting failure
