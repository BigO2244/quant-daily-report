# Documentation Taxonomy

## Purpose

This document is the Phase 4 foundation for separating canonical documentation
from generated reports, diagnostics, research outputs, runtime artifacts, and
operator notes.

It is a taxonomy proposal only. It does not move files, delete historical
documents, rewrite runbooks, or change producers.

## Core Rule

Generated markdown should never live beside canonical operator docs unless it is
explicitly promoted as source documentation or archived as historical evidence.

If a markdown file is generated from runtime state, backtests, diagnostics, or a
scheduled job, it should live under `outputs/` or a clearly marked generated
report area, not beside source-of-truth governance docs.

## Documentation Categories

| Category | Meaning | Examples |
|---|---|---|
| `canonical_operator_doc` | Current operating guidance used for live/paper operation. | Runbook, operations guide, deployment workflow. |
| `governance_doc` | Process, promotion, rollback, artifact, dependency, or documentation rules. | FR backlog, FR ledger, artifact governance. |
| `architecture_doc` | System design and component boundaries. | Alpha Stack architecture, dashboard specs. |
| `recovery_doc` | Incident response or recovery procedures. | Shadow scorecard recovery, execution integrity runbooks. |
| `generated_report` | Generated markdown/HTML from runtime, diagnostics, research, or reporting jobs. | Shadow comparison markdown, weekly model reports. |
| `historical_doc` | Retained past analysis or superseded guidance. | Old roadmaps, historical model audits. |
| `research_output` | Backtest and research-generated analysis. | Alpha lab reports, randomized window outputs. |

## Proposed Future Structure

```text
docs/
  governance/
    artifact_governance.md
    documentation_taxonomy.md
    dependency_governance.md
    fr_execution_ledger.md
    friday_refactor_backlog.md
    operational_validation.md
  architecture/
    alpha_stack/
    dashboard/
    strategy/
  deployment/
    deployment_workflow.md
  operations/
    OPERATIONS.md
    runbook.md
    operational_health_aggregator.md
  recovery/
    execution_integrity_runbook.md
  runbooks/
    shadow_scorecard_recovery.md
  historical/
    old_audits/
    superseded_specs/

outputs/
  reports/
  diagnostics/
  research/
  workflow/
  operations/
```

This structure is a target taxonomy, not an immediate migration instruction.
Moving files should be a separate FR with link updates and rollback planning.

## Placement Rules

### Canonical Docs

Canonical docs should:

- be tracked source files;
- be stable enough to reference from `AGENTS.md`;
- describe current operating behavior or explicitly mark historical status;
- avoid embedding generated daily data unless used as a static example;
- reference generated artifacts by path pattern, not by copying their content.

### Generated Markdown

Generated markdown should:

- live under `outputs/` or a generated-report directory;
- include generation date, source artifact path, and producer when possible;
- not be linked as canonical guidance unless promoted by review;
- be safe to regenerate from underlying evidence.

### Diagnostics

Diagnostics should:

- preserve evidence paths and generated timestamps;
- be retained according to future retention policy;
- not be mixed with operator runbooks unless summarized manually.

### Historical Research

Historical research should:

- be clearly separated from current operating guidance;
- include enough context to know whether it influenced current strategy state;
- not imply promotion without explicit promotion governance.

## Current Known Mixed Areas

- `docs/` contains current operations, architecture, runbooks, historical audits,
  and governance docs in one flat namespace.
- `outputs/research/` contains generated markdown reports and research artifacts
  that should not be mistaken for operator docs.
- Weekly research markdown exists outside a formal generated-report taxonomy.
- Dashboard payloads exist under `web/dashboard*/` for development and generated
  runtime payload purposes, creating source/generated ambiguity.

## Migration Guidance

When this taxonomy is implemented later:

1. Inventory docs and classify each file before moving anything.
2. Move canonical governance docs first, because they are easiest to validate.
3. Add compatibility links or update references in the same change.
4. Keep historical docs, but mark them historical instead of deleting them.
5. Move generated reports only after their producers have clear target paths.
6. Do not combine taxonomy moves with runtime behavior changes.

## Non-Goals

- No broad repository cleanup in this foundation step.
- No deletion of historical docs.
- No movement of runtime artifacts.
- No changes to dashboard publishing.
- No changes to research generation.
- No changes to cron or workflow behavior.
