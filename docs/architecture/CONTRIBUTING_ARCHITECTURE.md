# Contributing Architecture Documentation

Status: architecture-pack finalization draft
Scope: documentation only
Last reviewed: 2026-06-26

Canonical status: Needs Repository Verification until this architecture pack is
committed and reconciled with `origin/main`.

## Document Contract

| Field | Value |
|---|---|
| Purpose | Define how future architecture changes should update the architecture pack. |
| Owner | Not named in repository; architecture/governance ownership requires repository verification. |
| Inputs | Architecture pack, documentation governance docs, FR governance docs. |
| Outputs | Contribution checklist for architecture PRs. |
| Related Documents | `docs/architecture/DOCUMENTATION_GOVERNANCE.md`, `docs/architecture/DOCUMENT_INDEX.md`, `docs/documentation_governance.md`. |
| Related Tests | Documentation governance tests when documentation validation is needed. |
| Related Implementation | None directly; this is process documentation. |
| Related Artifacts | Documentation validation output, governance hygiene output. |
| Known Gaps | No repository-verified architecture owner is named. |

## Contribution Rules

1. Update the smallest document that owns the change.
2. Link canonical sources instead of copying long sections.
3. Mark unverified or dirty-worktree evidence as `Needs Repository Verification`.
4. Keep generated reports under generated-output roots unless governance
   promotes them into canonical docs.
5. Do not change runtime, tests, broker, scheduler, or configuration behavior as
   part of architecture documentation work.

## Before Editing

- Check `git status --short`.
- Read `AGENTS.md` and `docs/governance/ORCHESTRATOR_CONTEXT.md`.
- Identify whether the task is docs-only, research-only, operational, or
  runtime-adjacent.
- Preserve unrelated user changes.
- Decide which architecture document owns the update before editing.

## Allowed Scope

Architecture maintenance should normally stay under `docs/architecture/`.
Updating a non-architecture doc is appropriate only when that document is the
source of truth for the claim being corrected.

## Required Updates By Change Type

| Change type | Required architecture update |
|---|---|
| New subsystem | `SYSTEM_MAP.md`, `KNOWLEDGE_GRAPH.md`, `DOCUMENT_INDEX.md`, Technical Architecture and Operating Manual section if operator-facing. |
| New FR | `DOCUMENT_INDEX.md`, `DOCUMENT_INVENTORY.md`, `ENGINEERING_DECISION_INDEX.md`, and `DOCUMENT_GAPS.md` if unresolved. |
| New runbook | `DOCUMENT_INDEX.md`, `DOCUMENT_INVENTORY.md`, `OPERATOR_RUNBOOK.md`. |
| New artifact family | `KNOWLEDGE_GRAPH.md`, `DOCUMENT_INVENTORY.md`, `DOCUMENTATION_GOVERNANCE.md`. |
| New execution reason/state | `GLOSSARY.md`, Technical Architecture and Operating Manual execution section, relevant runbook/index rows. |
| Deprecated document | `DOCUMENT_INVENTORY.md`, `DOCUMENT_GAPS.md`, and source document deprecation notice if appropriate. |

## Review Checklist

- Purpose is explicit.
- Owner is named or marked not repository-verified.
- Inputs and outputs are listed.
- Related docs, tests, implementation, artifacts, and gaps are listed.
- Claims are backed by repository paths or marked `Needs Repository Verification`.
- The change does not imply runtime authorization.

## Validation

For docs-only changes, run:

```bash
git diff --check
git diff --stat
git diff --name-only
```

When documentation governance behavior itself changes, run the targeted
documentation governance tests from the repo venv.

## Do Not Duplicate

- Do not copy the FR registry into architecture docs.
- Do not copy long runbook procedures into the Technical Architecture and Operating Manual.
- Do not copy generated reports into canonical docs.
- Do not make the architecture pack the source of truth for broker state,
  scheduler installation, strategy promotion, or FR status.

## Authoritative References

- `docs/documentation_governance.md`
- `docs/documentation/canonical_hierarchy.md`
- `docs/governance/fr_governance_model.md`
- `docs/architecture/DOCUMENTATION_GOVERNANCE.md`
