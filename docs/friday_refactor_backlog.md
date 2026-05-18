# Caerus Friday Refactor Backlog

## Purpose

This document is the canonical Friday Refactor (FR) register after the 2026-05-15
Wave 1-3 deployment cycle. It separates deployed operational history from future
work so the backlog reads as an operations handbook, not a staging scratchpad.

FR work remains maintenance-window work by default. Changes that touch cron,
execution, reconciliation, broker state, deployment, or artifact contracts must
be audited, validated, and rolled out with explicit rollback plans.

## Governance Model

Preferred promotion flow:

```text
BACKLOG -> READY -> READY_VALIDATED -> IN_PROGRESS -> PROMOTION_READY -> DEPLOYED -> DEPLOYED_OBSERVING -> DEPLOYED
```

Status meanings:

- `BACKLOG`: useful work, not ready for implementation.
- `READY`: scope, dependencies, rollback path, and validation plan are clear.
- `READY_VALIDATED`: pre-work audit confirms source ownership, runtime exposure,
  and dependencies are safe enough to begin.
- `IN_PROGRESS`: implementation is underway.
- `PROMOTION_READY`: locally committed and validated, but not deployed.
- `DEPLOYED_OBSERVING`: deployed and healthy, with an explicit observation window.
- `DEPLOYED`: deployed, validated, and no special observation window remains.
- `REVIEWED_DEFERRED`: reviewed but intentionally not promoted.

Blast-radius framework:

- `LOW`: docs, tests, or isolated reporting with no scheduler/runtime effect.
- `MEDIUM`: research/reporting code, generated artifacts, dashboard rendering,
  CI hardening, or non-blocking shadow behavior.
- `HIGH`: cron, deployment, execution, reconciliation, broker state, order
  submission, or canonical runtime state.

Validation expectations:

- Select targeted validation before implementation.
- Prefer simulation before promotion for recovery, scheduler, and execution-adjacent work.
- Do not run trading workflows or regenerate broker artifacts as validation
  unless explicitly required and approved.
- Document rollback and observation surfaces before deployment.

## Deployed Wave Summary

| Wave | FRs | Status | Operational Result |
|---|---|---|---|
| Wave 1 | FR-004, FR-006, FR-009, FR-011, FR-013 | Deployed | Reporting resilience, advisory dependency monitoring, SHA-pinned Actions, minimized workflow permissions. |
| Wave 2 | FR-001, FR-012 | Deployed observing | Shadow wrapper step observability and repository-scoped CI cache keys. |
| Wave 3 | FR-005 | Deployed observing | Self-heal recovery now fails closed unless full precompute bundle validation passes. |

## Phase 4 Framework: Artifact Governance + Operational Telemetry

Phase 4 is the next operational hardening phase after Waves 1-3. It is a
non-trading, non-execution phase focused on state governance, artifact clarity,
operator trust surfaces, and additive observability.

Phase 4 must not change broker submission, strategy selection, portfolio
construction, cron timing, or production execution behavior unless a later FR is
explicitly promoted with a separate HIGH blast-radius review.

Phase 4 principles:

- Keep changes additive and read-only first.
- Prefer deterministic metadata over inferred state.
- Make canonical, derived, diagnostic, and generated artifacts explicit.
- Eliminate stale `latest` ambiguity through freshness metadata and manifests.
- Improve operator clarity before increasing scheduler or infrastructure complexity.
- Preserve rollback clarity: new telemetry can be ignored or disabled without
  altering trading-critical flows.

Current platform maturity note:

Caerus is transitioning from execution automation toward operational state
governance, artifact governance, resilience telemetry, and operator-grade
observability. The current bottleneck is operational clarity, not distributed
compute scale. Phase 4 should not introduce microservices, Kubernetes, Airflow,
or broad scheduler rewrites.

Phase 4 priority order:

| Order | FR | Theme | Why first/next |
|---:|---|---|---|
| 1 | FR-015 | Artifact registry | Establishes ownership and taxonomy before downstream telemetry depends on artifact interpretation. |
| 2 | FR-017 | Health aggregator | Gives operators one synthesis surface while remaining read-only and additive. |
| 3 | FR-018 | Latest freshness manifests | Reduces the highest recurring ambiguity: whether `latest` means current, stale, partial, or diagnostic-only. |
| 4 | FR-023 | Documentation taxonomy | Prevents governance docs, generated reports, and operator notes from continuing to blur together. |
| 5 | FR-019 | Retention and backup policy | Uses the artifact taxonomy before defining cleanup, archive, and safe-to-delete rules. |
| 6 | FR-020 | Validation isolation | Stops tests and smoke flows from polluting runtime evidence after ownership rules are clear. |
| 7 | FR-016 | Semantic bundle validation | Builds on artifact ownership to add deeper contract validation without broad execution changes. |
| 8 | FR-021 | Partial execution normalization | Important but execution-adjacent; schedule after lower-risk observability and artifact work. |
| 9 | FR-022 | Dependency hash enforcement | Deferred until dependency baselines, clean installs, and rollback procedures are proven. |
| 10 | FR-014 | Shadow learning reliability | Remains useful, but should inherit Phase 4 artifact taxonomy and health aggregation conventions. |

Phase 4 output taxonomy recommendation:

```text
docs/
  governance/
  architecture/
  deployment/
  operations/
  recovery/
  runbooks/
  historical/

outputs/
  reports/
  diagnostics/
  research/
  workflow/
  operations/
```

Generated markdown should not live beside canonical operator documentation. If a
report is generated from runtime state, it belongs under `outputs/` or a clearly
marked generated-report area, not beside source-of-truth governance docs.

Phase 4 foundation documents:

- `docs/artifact_governance.md` — artifact taxonomy, ownership semantics,
  initial registry, freshness semantics, latest publication rules, and retention
  classes.
- `docs/operational_health_aggregator.md` — read-only daily health synthesis
  design and proposed `outputs/operations/` artifacts.
- `docs/documentation_taxonomy.md` — canonical documentation vs generated
  artifact separation rules and future directory taxonomy.

## Current FR Register

### FR-001

**Title:** Split shadow wrapper responsibilities
**Category:** Shadow / Workflow
**Priority:** HIGH
**Status:** DEPLOYED_OBSERVING
**Blast Radius:** MEDIUM

**Deployed behavior:** `scripts/run_shadow_candidates_daily.sh` now separates
generation, latest publication, and live-vs-shadow reconciliation into helper
steps. Each helper writes a status artifact under `outputs/workflow/<date>/`.
The wrapper remains best-effort and cannot block precompute or execution.

**Validation summary:** `Tests/test_shadow_daily_wrapper.py`,
`Tests/test_execution_pipeline_integration.py`, shell syntax, operational
validator, and local shadow smoke testing.

**Observation focus:** `shadow_generate.json`, `shadow_latest.json`,
`shadow_reconciliation.json`, `shadow.json`, and `logs/shadow_<date>.log`.

**Rollback reference:** Revert the Wave 2 shadow wrapper commit to restore the
previous inline wrapper body.

### FR-002

**Title:** Add price cache coverage metadata sidecar
**Category:** Data / Hydration
**Priority:** MEDIUM
**Status:** BACKLOG
**Blast Radius:** MEDIUM

**Intent:** Add an advisory coverage sidecar so freshness checks do not need to
inspect the full `price_panel.parquet` for max date and symbol coverage.

**Rollback reference:** Stop writing/reading the sidecar and fall back to current
parquet inspection. The parquet remains canonical until a replacement is proven.

### FR-003

**Title:** Add managed bad ticker / ticker exceptions
**Category:** Data Quality
**Priority:** MEDIUM
**Status:** REVIEWED_DEFERRED
**Blast Radius:** MEDIUM

**Current state:** Local WIP exists outside Waves 1-3. Do not treat it as
deployed until it receives an isolated promotion package.

### FR-004

**Title:** Create feedback-loop rolling index
**Category:** Learning / Reporting
**Priority:** MEDIUM
**Status:** DEPLOYED
**Blast Radius:** LOW

**Deployed behavior:** Compact daily learning/performance rows are written under
`outputs/shadow_candidates/performance/` while dated JSON artifacts remain
canonical.

**Validation summary:** `Tests/test_feedback_loop_artifacts.py`,
`Tests/test_portfolio_learning_report.py`, combined Wave 1 validation, and
operational validator.

**Rollback reference:** Revert the Wave 1 reporting commit or stop reading the
additive index; existing dated artifacts remain the source of truth.

### FR-005

**Title:** Self-heal-only precompute mode and recovery integrity
**Category:** Execution Safety / Scheduler
**Priority:** HIGH
**Status:** DEPLOYED_OBSERVING
**Blast Radius:** HIGH

**Deployed behavior:** When execution sees an invalid or missing precompute
bundle, it invokes precompute with `SELF_HEAL_PRECOMPUTE_ONLY=1`. Self-heal
suppresses precompute email, shadow generation, latest shadow publication, and
shadow reconciliation. Execution continues only after the full bundle validator
confirms:

- `contract.json`
- `daily_snapshot.json`
- `signals.json`
- `planned_execution_payload.json`

Partial recovery output fails closed and writes degraded-state observability.

**Validation summary:** `Tests/test_execution_pipeline_integration.py`,
`Tests/test_precompute_bundle_validation.py`, shell syntax, operational
validator, and controlled degraded-state simulations.

**Observation focus:** `execution_self_heal.json`,
`execution_bundle_validation.json`, `precompute_self_heal.json`, and
`precompute_bundle_validation.json`.

**Rollback reference:** Revert the Wave 3 FR-005 commit. Post-rollback, inspect
any existing self-heal artifacts as evidence and do not delete runtime outputs as
part of rollback.

### FR-006

**Title:** Separate required vs optional artifact health in portfolio learning report
**Category:** Reporting
**Priority:** LOW
**Status:** DEPLOYED
**Blast Radius:** LOW

**Deployed behavior:** Required scoreboard artifacts, optional learning
artifacts, and diagnostic-only artifacts are classified separately so missing
optional artifacts remain visible without making the core report unavailable.

**Validation summary:** `Tests/test_portfolio_learning_report.py`, combined Wave
1 validation, and operational validator.

**Rollback reference:** Revert the Wave 1 reporting commit to restore the prior
single artifact-health classification.

### FR-007

**Title:** Revisit full parquet read/write scaling
**Category:** Data Engineering
**Priority:** LOW
**Status:** REVIEWED_DEFERRED
**Blast Radius:** LOW

**Current state:** Advisory review only. The single parquet remains canonical.
Prefer compact coverage/index sidecars before partitioning or migration.

### FR-008

**Title:** Clean git/VM deployment workflow
**Category:** Operations
**Priority:** HIGH
**Status:** DEPLOYED
**Blast Radius:** HIGH

**Deployed behavior:** `origin/main` is canonical deployable source and the VM is
a fast-forward deploy target. Standard deployment is:

```text
local validation -> isolated commit -> push -> VM git pull --ff-only -> validation -> observation
```

SCP is exception-only and must be reconciled back through git.

**Rollback reference:** Prefer `git revert`, push, and VM fast-forward. Preserve
drift evidence before mutation and avoid destructive reset/clean behavior.

### FR-009

**Title:** GitHub Actions SHA pinning
**Category:** CI/CD Security
**Priority:** HIGH
**Status:** DEPLOYED
**Blast Radius:** MEDIUM

**Deployed behavior:** Workflow `uses:` references are pinned to immutable
40-character SHAs.

**Validation summary:** Workflow YAML parsing and operational validator.

**Rollback reference:** Restore prior tag references only if a pinned SHA is
invalid or unavailable.

### FR-010

**Title:** Deterministic Python dependency and lockfile governance
**Category:** Supply Chain / Dependency Management
**Priority:** HIGH
**Status:** REVIEWED_DEFERRED
**Blast Radius:** MEDIUM

**Current state:** Not deployed in Waves 1-3. Do not mix dependency pinning,
constraints, hash enforcement, or requirements rewrites into operational
governance commits.

**Future validation:** Clean environment install, VM install validation,
workflow install validation, dependency audit, and rollback plan.

### FR-011

**Title:** GitHub workflow permission minimization
**Category:** CI/CD Security
**Priority:** MEDIUM
**Status:** DEPLOYED
**Blast Radius:** MEDIUM

**Deployed behavior:** Workflow-scope `contents: write` has been removed.
Workflows default to read permissions with job-level elevation only where needed.

**Validation summary:** Operational validator workflow permission check.

**Rollback reference:** Restore prior permission blocks only if a workflow write
path fails and the failure is confirmed to be permission-related.

### FR-012

**Title:** CI cache namespace isolation
**Category:** CI/CD Security
**Priority:** MEDIUM
**Status:** DEPLOYED_OBSERVING
**Blast Radius:** MEDIUM

**Deployed behavior:** Canonical model snapshot and precompute cache keys include
`${{ github.repository_id }}` to isolate cache namespaces.

**Observation focus:** First post-deploy runs may miss existing caches and
regenerate under the repository-scoped namespace. This is expected.

**Rollback reference:** Revert the Wave 2 cache namespace commit if cache misses
create unacceptable workflow instability.

### FR-013

**Title:** Dependency monitoring and automated security governance
**Category:** Security / Operations
**Priority:** LOW
**Status:** DEPLOYED
**Blast Radius:** LOW

**Deployed behavior:** Dependabot advisory monitoring covers pip and GitHub
Actions without auto-merge.

**Observation focus:** Dependabot PR noise and advisory cadence.

**Rollback reference:** Disable or remove `.github/dependabot.yml` if advisory
noise becomes operationally unacceptable.

### FR-014

**Title:** Shadow artifact reliability and feedback loop integrity
**Category:** Shadow / Learning / Observability
**Priority:** MEDIUM
**Status:** BACKLOG
**Blast Radius:** MEDIUM

**Intent:** Further separate trading-critical health from shadow-learning health
by documenting required vs optional shadow artifacts and improving visibility
for partial learning-layer degradation.

**Constraint:** Shadow or learning failures must not block trading-critical
workflows.

### FR-015

**Title:** Artifact registry and ownership matrix
**Category:** Artifact Governance / Operations
**Priority:** HIGH
**Status:** IN_PROGRESS
**Blast Radius:** LOW

**Purpose:** Define formal artifact classification, ownership metadata, producer
and consumer relationships, freshness semantics, and retention metadata.

**Scope:**

- Classify artifacts as canonical, derived, diagnostic, generated report,
  runtime evidence, cache, backup, or local-only scratch.
- Document owners for precompute, workflow, shadow, reconciliation, broker,
  dashboard, hydration, diagnostics, reports, and research artifacts.
- Define `latest` publication semantics, including when latest artifacts are
  trustworthy, stale, partial, or diagnostic-only.
- Propose an artifact manifest structure that can be adopted incrementally.
- Produce `docs/artifact_governance.md` as the canonical artifact taxonomy.

**Rationale:** The largest remaining operational risk after Waves 1-3 is not
order submission logic; it is ambiguity around which artifacts are authoritative,
which are derived, which can be stale, and which can safely be ignored.

**Dependencies:** None. This should be the first Phase 4 FR because later
telemetry and retention work should depend on a shared artifact vocabulary.

**Rollout guidance:** Start as documentation and read-only inventory. Do not
move files or change producers in the first pass.

**Rollback reference:** Revert or ignore the documentation and registry proposal.
No runtime behavior should depend on FR-015 until a later implementation FR
explicitly opts in.

**Observation focus:** Operator review confirms that artifact classes, owners,
freshness rules, and retention hints are understandable and complete enough to
guide later work.

**Foundation progress:** Initial governance foundation exists in
`docs/artifact_governance.md`. No producers or runtime artifacts have been
changed.

### FR-016

**Title:** Semantic precompute contract validation
**Category:** Execution Safety / Artifact Contracts
**Priority:** HIGH
**Status:** BACKLOG
**Blast Radius:** MEDIUM

**Purpose:** Extend precompute validation beyond file existence, JSON
parseability, and trade-date checks.

**Scope:**

- Validate schema version and artifact type.
- Validate strategy identity metadata: Polaris remains paper baseline, Orion and
  Lyra remain shadow-only, and SPY remains benchmark.
- Validate execution mode and paper-only assumptions.
- Validate planner provenance, source run id, and workflow stage.
- Validate payload integrity semantics such as expected sections, count
  consistency, signal path references, and execution eligibility fields.
- Keep validation deterministic, local, and lightweight.

**Rationale:** Current bundle validation is an essential fail-closed guard, but
it is still shallow. A bundle can be present and parseable while carrying
semantic drift that should block execution.

**Dependencies:** Prefer FR-015 first so contract fields map to formal artifact
ownership and trust semantics.

**Rollout guidance:** Introduce semantic validation in advisory/reporting mode
first, then promote specific failures to blocking only after tests and degraded
simulations prove the behavior.

**Rollback reference:** Disable semantic-only checks or revert to the current
file-level validator. File presence and parseability checks must remain intact.

**Observation focus:** `precompute_bundle_validation.json`,
`execution_bundle_validation.json`, degraded-state simulations, and any advisory
semantic warnings.

### FR-017

**Title:** Operational health aggregator
**Category:** Observability / Operations
**Priority:** HIGH
**Status:** IN_PROGRESS
**Blast Radius:** LOW

**Purpose:** Create a single operator-grade daily health synthesis surface.

**Scope:**

- Read existing artifacts only.
- Summarize precompute health, execution health, shadow health, hydration,
  dashboard freshness, recovery attempts, stale latest detection, validator
  outputs, and dependency warnings.
- Proposed outputs:
  - `outputs/operations/daily_health_summary.json`
  - `outputs/operations/daily_health_summary.md`
- Include explicit status levels, evidence paths, and operator recommendations.

**Rationale:** Operational telemetry is currently fragmented across logs,
workflow artifacts, shadow status files, hydration status, dashboard payloads,
and emails. Operators need one synthesis surface that preserves the underlying
evidence paths.

**Dependencies:** FR-015 is preferred but not strictly required. FR-017 can start
with current artifacts and adopt the registry later.

**Rollout guidance:** Build read-only, artifact-only, and non-blocking. Do not
trigger precompute, execution, hydration, shadow generation, broker calls, or
dashboard refreshes.

**Rollback reference:** Stop producing or ignore the summary artifacts. Existing
underlying artifacts remain canonical.

**Observation focus:** Daily summary correctness, evidence-path completeness,
false positive rate, repeated recovery visibility, and stale/latest detection.

**Foundation progress:** Initial read-only design exists in
`docs/operational_health_aggregator.md`. No telemetry producer has been built.

### FR-018

**Title:** Latest publication freshness manifest
**Category:** Artifact Governance / Freshness
**Priority:** HIGH
**Status:** IN_PROGRESS
**Blast Radius:** LOW

**Purpose:** Eliminate stale `latest` ambiguity by requiring publication
metadata for latest-style artifacts.

**Scope:**

- Define a freshness manifest format with:
  - `source_trade_date`
  - `published_at`
  - `producer`
  - `freshness_status`
  - `staleness_policy`
  - `source_artifact_path`
  - `publication_status`
  - `partial_reason`
- Define interpretation rules for fresh, stale, suppressed, partial, diagnostic,
  and missing latest publications.
- Start with shadow latest artifacts, then extend to dashboard, broker snapshot,
  options review, health check, and report latest pointers where appropriate.

**Rationale:** Latest files are convenient but operationally ambiguous. A stale
latest artifact can look healthy unless consumers also inspect adjacent status
files.

**Dependencies:** FR-015 should define artifact classes and ownership first.

**Rollout guidance:** Add manifests beside existing latest outputs. Do not remove
or rename existing latest files in the initial rollout.

**Rollback reference:** Ignore or stop writing manifests. Existing latest
publication behavior remains unchanged.

**Observation focus:** Manifest freshness status, source/date alignment, partial
publication reasons, and downstream health aggregator consumption.

**Foundation progress:** Freshness vocabulary and manifest examples are defined
in `docs/artifact_governance.md`. Existing latest artifacts have not been
mutated.

### FR-019

**Title:** Runtime artifact retention and backup policy
**Category:** Artifact Lifecycle / Operations
**Priority:** MEDIUM
**Status:** BACKLOG
**Blast Radius:** LOW

**Purpose:** Formalize retention windows, archive rules, backup boundaries,
cleanup rules, and safe-to-delete semantics for runtime artifacts.

**Scope:**

- Define retention classes for workflow artifacts, precompute bundles, broker
  snapshots, reconciliation evidence, reports, diagnostics, research outputs,
  recovery backups, logs, caches, dashboard payloads, and generated emails.
- Address `outputs/` growth and operator expectations for archive vs deletion.
- Define backup philosophy for critical state and recovery evidence.
- Define what must never be deleted as a rollback shortcut.
- Keep cleanup policy separate from any cleanup implementation.

**Rationale:** `outputs/` growth and mixed artifact purposes make operational
review noisy and increase the risk of deleting evidence that should be retained.

**Dependencies:** FR-015 should define artifact ownership first. FR-018 should
clarify latest publication semantics before cleanup rules are automated.

**Rollout guidance:** Start with documentation and dry-run inventory. Any actual
cleanup command should be a later, explicit FR with dry-run, manifest, backup,
and rollback behavior.

**Rollback reference:** Ignore the policy document. Do not delete artifacts as
part of rolling back the policy.

**Observation focus:** Operator agreement on retention classes, backup
boundaries, and safe-to-delete categories.

### FR-020

**Title:** Read-only validation isolation
**Category:** Testing / Operational Hygiene
**Priority:** MEDIUM
**Status:** BACKLOG
**Blast Radius:** MEDIUM

**Purpose:** Prevent tests, smoke flows, and read-only validation from mutating
repo-level runtime `outputs/` and `logs/`.

**Scope:**

- Identify tests and smoke commands that write to repo-level `outputs/` or
  `logs/`.
- Move those tests to isolated temporary directories or injectable output roots.
- Add deterministic cleanup for test-only artifacts where needed.
- Preserve runtime/test separation so operational evidence cannot be confused
  with validation residue.

**Rationale:** Runtime evidence should be trustworthy. Tests that write into the
same ignored artifact tree make local audits noisy and weaken confidence in
read-only validation claims.

**Dependencies:** FR-015 should classify runtime vs test artifacts. FR-017 can
later flag test residue if needed.

**Rollout guidance:** Start with non-trading tests and shadow wrapper tests.
Avoid changing production output paths while introducing test-only output root
overrides.

**Rollback reference:** Revert test harness changes. Production behavior should
remain unchanged.

**Observation focus:** Clean `git status`, clean ignored runtime tree after
targeted validation, and no loss of test coverage.

### FR-021

**Title:** Partial execution state normalization
**Category:** Execution Observability / Operator Semantics
**Priority:** HIGH
**Status:** BACKLOG
**Blast Radius:** HIGH

**Purpose:** Clarify partial-success semantics when one execution phase succeeds
but a later execution-adjacent stage fails.

**Scope:**

- Define normalized operator states for cases such as:
  - Equity orders accepted but options stage fails.
  - Orders submitted but post-submit artifact writing fails.
  - Execution succeeds but reconciliation/reporting confirmation fails.
- Separate broker-side submission truth from workflow exit code.
- Add explicit partial-success artifacts and operator recommendations.
- Avoid changing order-submission behavior in the first pass.

**Rationale:** A phase can be operationally failed while broker-side orders were
already accepted. Operators need precise semantics so failure handling does not
accidentally imply no trades occurred.

**Dependencies:** FR-017 should provide the health surface and FR-015 should
define artifact ownership. This is intentionally later because it is
execution-adjacent.

**Rollout guidance:** Start as reporting/summary normalization. Do not alter
broker submission, retry behavior, or options allowlists without a separate
HIGH blast-radius promotion.

**Rollback reference:** Revert reporting/summary semantics while preserving raw
broker and execution artifacts.

**Observation focus:** Operator summaries, execution results, confirmation
emails, broker snapshots, and any partial-state daily health fields.

### FR-022

**Title:** Dependency hash enforcement
**Category:** Supply Chain / Dependency Management
**Priority:** HIGH
**Status:** REVIEWED_DEFERRED
**Blast Radius:** MEDIUM

**Purpose:** Move from advisory dependency baselines toward deterministic,
hash-enforced installs when operational prerequisites are met.

**Deferred rationale:** Premature hash enforcement can break VM recovery,
GitHub workflow installs, research-agent installs, or emergency dependency
patches if clean-install validation, rollback procedures, and exception handling
are not ready.

**Prerequisites before promotion:**

- Clean environment install validation for local, VM, and GitHub Actions.
- Resolution of the documented APScheduler dependency exception.
- Agreement on whether workflows install with `constraints.txt`.
- Advisory `pip-audit` or equivalent review before hard gates.
- Emergency dependency update and rollback procedure.

**Rollback reference:** Keep current dependency install behavior unchanged until
hash enforcement is explicitly promoted. If promoted later, rollback by
restoring non-hash install commands and preserving the advisory lock artifacts
for review.

**Observation focus:** Dependency drift, Dependabot advisory noise, clean install
results, and VM/GitHub parity.

### FR-023

**Title:** Documentation and generated artifact separation
**Category:** Documentation Governance / Artifact Hygiene
**Priority:** MEDIUM
**Status:** IN_PROGRESS
**Blast Radius:** LOW

**Purpose:** Separate canonical documentation from generated markdown, runtime
reports, diagnostics, research outputs, and operator notes.

**Scope:**

- Propose a documentation taxonomy:
  - `docs/governance/`
  - `docs/architecture/`
  - `docs/deployment/`
  - `docs/operations/`
  - `docs/recovery/`
  - `docs/runbooks/`
  - `docs/historical/`
- Propose an output taxonomy:
  - `outputs/reports/`
  - `outputs/diagnostics/`
  - `outputs/research/`
  - `outputs/workflow/`
  - `outputs/operations/`
- Establish that generated markdown should not live beside canonical operator
  docs unless it is clearly historical or explicitly checked in as source.

**Rationale:** Documentation drift is operational risk. Mixing canonical docs,
generated reports, diagnostics, and historical notes makes operator guidance
harder to trust.

**Dependencies:** FR-015 should define artifact classes first. FR-023 can then
apply those classes to docs and generated reports.

**Rollout guidance:** Start with a taxonomy proposal and migration map. Do not
move many files in the first pass unless each move has redirect/update coverage.

**Rollback reference:** Revert documentation moves or keep compatibility links.
Do not delete historical docs as cleanup.

**Observation focus:** Reduced doc ambiguity, clear canonical owner per doc, and
no broken runbook/deployment references.

**Foundation progress:** Initial taxonomy proposal exists in
`docs/documentation_taxonomy.md`. No files have been moved.

## Operational Lessons Learned

- Wave-based promotion made blast radius and rollback boundaries explicit.
- Runtime smoke testing found orchestration issues that static validation would
  not prove.
- Degraded-state simulation identified the FR-005 fail-open risk before
  deployment.
- Additive status artifacts improved observability without deleting or
  overwriting canonical outputs.
- Governance-driven release management is now the default: implementation speed
  is useful, but deployment speed is not the goal.
- Phase 4 should make operational state easier to trust before adding scheduler
  or infrastructure complexity.

## Maintenance Checklist

- [ ] Market/execution window risk reviewed.
- [ ] Current local and VM source ownership clear.
- [ ] Blast radius classified.
- [ ] Targeted validation selected before mutation.
- [ ] Rollback path identified.
- [ ] Runtime artifact impact documented.
- [ ] Deployment plan uses FR-008 git fast-forward governance.
- [ ] Observation surfaces identified for `DEPLOYED_OBSERVING` changes.
