# Repository Artifact Policy

## Purpose

This policy separates deployable source from generated operational state so
Caerus commits remain reviewable, rollback-safe, and operationally auditable.

## Canonical Source

Canonical source belongs in version control. This includes:

- application and library code
- tests and deterministic test fixtures
- configuration templates and policy files
- deployment templates under `deploy/`
- operator, architecture, governance, and runbook documentation
- explicit dependency input and lock/baseline files

Canonical source must be reviewable in isolated commits with a clear validation
and rollback path.

## Runtime Logs

Runtime logs are operational evidence, not source. Examples include:

- `logs/*.log`
- `logs/*.err`
- ad hoc scheduler or launchd stderr captures

Logs should be preserved on the producing host when they are needed for incident
review, but they should not be committed to source control.

## Generated Dashboard Payloads

Dashboard JSON/JS payloads are generated operational state unless explicitly
placed under a fixture path. Examples include:

- `web/dashboard/dashboard-data.json`
- `web/dashboard/dashboard-data.js`
- `web/dashboard/trading_day_summary.json`
- `web/dashboardDEV/dashboard-data.json`
- `web/dashboardDEV/dashboard-data.js`
- `web/dashboardDEV/dashboard_data.json`

Tracked legacy dashboard payloads should not be removed casually because current
deployment scripts may still copy them. Any later change to stop tracking those
files must be paired with a deployment-script and dashboard-runtime review.

## Trading Outputs

Trading outputs are runtime evidence and belong under `outputs/` or the
corresponding VM runtime path. They include:

- precompute bundles
- execution run artifacts
- broker snapshots
- reconciliation artifacts
- workflow status files
- recovery run artifacts

These files should not be committed by default. Preserve them as operational
evidence; do not delete them to make a state look clean.

## Deployment Artifacts

Deployment source templates belong in version control, including:

- service and timer templates
- nginx templates
- deployment scripts
- static source assets required by the dashboard UI

Installed VM files and generated deployed payloads are deployment state, not
canonical source. The canonical deployment path remains git-based source
promotion followed by VM fast-forward.

## Recovery Fixtures

Recovery fixtures may be version controlled when they are deterministic,
sanitized, and used by tests. Preferred location:

- `Tests/fixtures/interrupted_runs/<date>/`

Recovery fixtures must not contain secrets, live credentials, or mutable broker
state that cannot be replayed deterministically.

## Generated Operational State

Generated operational state should be ignored unless explicitly promoted to a
fixture or documented baseline. Examples include:

- dashboard payloads
- diagnostics outputs
- local validation reports
- generated markdown reports
- cache files
- hydrated price panels

If generated state is needed for a review, prefer attaching or referencing the
runtime artifact path instead of committing it.

## Generated Research Reports

Weekly or ad hoc research markdown outputs are generated reports unless a
separate archive policy designates them as canonical history. Until such a
policy exists, generated research reports should remain out of source control.

## Commit Hygiene Rules

- Do not mix generated artifacts with source changes.
- Do not combine dependency baselines with runtime or docs cleanup.
- Do not commit logs.
- Do not commit dashboard payload refreshes as a side effect of frontend work.
- Do not delete runtime evidence as rollback.
- Use deterministic fixtures only when tests require them.
