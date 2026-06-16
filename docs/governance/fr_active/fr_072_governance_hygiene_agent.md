# FR-072 — Governance Hygiene Agent

Status: DEPLOYED_OBSERVING (read-only audit; proposed findings only)
Owner: Caerus Research Program
Last Updated: 2026-06-16
Governance Label: GOVERNANCE_AUTOMATION / OPERATIONAL_RISK_REDUCTION
Execution Impact: NON_EXECUTIONAL (this FR does not change trading, broker,
cron, allocation, strategy-selection, or paper/live behavior)

## 0. Purpose and Boundary

Governance drift is now a program risk. The doctrine, registry, active backlog,
active/archive FR specs, roadmap, and AGENTS instructions all need to stay in
sync without relying on Brett as a manual reconciliation layer.

FR-072 creates a **read-only governance hygiene agent** that audits those
documents, inspects repo state, and emits deterministic review artifacts. The
agent proposes findings only. It does not patch files, commit changes, push
changes, or install cron. Future phases may generate patch proposals, but those
proposals must remain un-applied until an explicit operator approval step.

**Phase B scheduling is intentionally out of scope for this FR.** Any cron or
scheduled install requires a separate, explicit approval after the read-only
auditor is proven stable.

## 1. Problem Statement

Manual governance maintenance has become fragile because the authoritative
governance sources now span several documents and directories:

- `docs/governance/caerus_investment_doctrine.md`
- `docs/governance/fr_registry.md`
- `docs/governance/fr_active_backlog.md`
- `docs/governance/fr_active/`
- `docs/governance/fr_archive/`
- `docs/governance/CURRENT_RESEARCH_ROADMAP.md`
- `docs/governance/README.md`
- `AGENTS.md`

Without a deterministic audit, stale references, missing registry coverage,
duplicate FR numbers, and doctrine drift can remain hidden until a human notices
them during review.

## 2. Operating Mode

This FR is read-only first:

- inspect governance docs and recent repo state
- produce proposed findings only
- write its own artifacts only
- never auto-patch governance files
- never auto-commit or auto-push
- never modify execution, broker, cron, strategy, or allocation behavior

The output is advisory telemetry, not an enforcement engine.

## 3. Scope

### Read

- `docs/governance/caerus_investment_doctrine.md`
- `docs/governance/fr_registry.md`
- `docs/governance/fr_active_backlog.md`
- `docs/governance/CURRENT_RESEARCH_ROADMAP.md`
- `docs/governance/README.md`
- `AGENTS.md` if present
- `docs/governance/fr_active/*.md`
- `docs/governance/fr_archive/*.md`
- recent git state (`HEAD`, `git status --short`, recent commits, and recent
  docs/governance paths changed in history)

### Emit

- deterministic JSON and markdown review artifacts
- proposed findings with severity, category, file, line, and suggested action
- no source-file mutation beyond its own output directory

## 4. Audit Targets

The hygiene agent should detect, at minimum:

- active FR files missing from the registry
- archive FR files missing from the registry
- registry paths that do not exist
- backlog FRs missing from the registry
- registry FRs marked active/proposed but not represented in the backlog
- archive FRs whose registry status is still active/proposed without an explicit
  navigational-only note
- active FRs whose registry status is closed/completed/superseded
- stale references to moved FR filenames
- missing doctrine references in the canonical governance docs
- duplicate FR numbers in active/archive files
- duplicate FR numbers in registry tables
- broken relative links within governance markdown where feasible
- active backlog entries missing rollback references, blast radius, or status
- suspicious `IN_PROGRESS` rows whose text says deployed/observing
- proposed or high-blast-radius execution-adjacent FRs without a deployment
  window constraint
- simple doctrine conflicts in new or revised docs

## 5. Artifacts

Outputs are written under:

`outputs/governance_hygiene/<YYYY-MM-DD>/`

Files:

- `governance_hygiene.json`
- `governance_hygiene.md`

The JSON artifact is the machine-readable source for follow-up review. The
markdown artifact is operator-readable and should support daily review before
any suggested change is considered.

## 6. CLI Contract

Default command writes the current-day report. The script should support:

- `--date YYYY-MM-DD`
- `--output-dir outputs/governance_hygiene`
- `--fail-on-warn`
- `--fail-on-fail`
- `--json-only`

Exit behavior:

- `0` for `OK`
- `0` for `WARN` unless `--fail-on-warn` is set
- non-zero for `FAIL` when `--fail-on-fail` is set

## 7. Success Criteria

1. The auditor runs deterministically against the same repo state.
2. The auditor emits review artifacts without mutating governance sources.
3. The report highlights governance drift early enough for daily review.
4. Brett is no longer the manual reconciliation bottleneck for governance
   consistency checks.
5. Phase B scheduling remains explicit and separately approved.

## 8. Implementation Evidence

Implementation files:

- `scripts/governance_hygiene_agent.py`
- `Tests/test_governance_hygiene_agent.py`

The tests cover registry/backlog gaps, duplicate FR IDs, path drift,
deterministic output, no source mutation, and explicit fail-on-fail behavior.
This remains non-executional and unscheduled.

## 9. Rollback

Delete `scripts/governance_hygiene_agent.py`, remove the FR-072 spec and backlog
references, and delete generated `outputs/governance_hygiene/<date>/` artifacts.
No execution or trading rollback is needed because this FR is read-only.
