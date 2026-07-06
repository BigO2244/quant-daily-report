# Reporting Integration Plan

Audit date: 2026-06-26

Scope: Reporting plan only. No email, dashboard, summary, optimizer, execution, sizing, broker, live-pilot, cron, or order-submission behavior was changed in this pass.

## Summary

FR-105 / Alpha Chase reporting should be added only after Phase 0/1 artifact completeness is reliable. The first integration should not render a full ticker book in emails. It should render concise status, source paths, current-vs-shadow summary metrics, and top reasons, with a link/path to the full deterministic artifact.

Until Phase 0/1 completeness passes, reporting should show `SPARSE_INPUT`, `MISSING_SOURCE_ARTIFACTS`, or `unavailable`, not imply that Alpha Chase made a usable decision.

## Evidence Reviewed

- Existing email paths from the prior CIO audit.
- `core/email_reporting_sections.py`
- `paper/build_execution_email.py`
- `scripts/send_trading_confirmation_email.py`
- `scripts/format_precompute_email.py`
- `core/dynamic_daily_email.py`
- `core/trading_day_summary.py`
- `core/execution_summary.py`
- `scripts/latest_execution_timeline_status.py`
- `scripts/research/build_dashboard_v1.py`
- `web/dashboard/quant_daily_executive.js`
- Existing FR-105 research artifacts.

## Files/Modules Inspected

| Surface | Files/modules |
| --- | --- |
| Precompute email | `scripts/format_precompute_email.py`, `scripts/send_precompute_email.py` |
| Execution email | `daily_trade_execution_email.py`, `paper/build_execution_email.py` |
| Confirmation email | `scripts/send_trading_confirmation_email.py` |
| Shared email sections | `core/email_reporting_sections.py` |
| Dynamic email sections | `core/dynamic_daily_email.py` |
| Dashboard | `scripts/research/build_dashboard_v1.py`, `web/dashboard/quant_daily_executive.js` |
| Daily summary | `core/trading_day_summary.py`, `core/execution_summary.py`, `scripts/latest_execution_timeline_status.py` |
| FR-105 artifacts | `outputs/research/fr_105/<date>/*` |

## Proposed Reporting Fields

Common compact fields:

- FR-105 status.
- Phase 0/1 completeness status.
- Phase 4 shadow comparison status, once implemented.
- Artifact path.
- Source artifact status summary.
- Current policy position count.
- Alpha Chase shadow position count.
- Current HHI / effective N.
- Alpha Chase HHI / effective N.
- Max single-name weight comparison.
- Estimated turnover from current policy.
- Score-backed candidate count.
- Unavailable score count.
- Top missing sources.
- Top blocked/suppressed high-ranked candidates.
- Selected shadow variant, if any.
- Reason no shadow variant was selected.

Fields that must stay out of email until artifact-backed:

- Portfolio score.
- Expected alpha by ticker.
- Forward return comparison.
- Promotion recommendation.
- Paper/live allocation recommendation.

## Surface-by-Surface Plan

### Precompute Email

Placement: After planned trade summary and before execution readiness.

Source artifacts:

- Phase 0 replay contract.
- Phase 1 baseline.
- Phase 0/1 completeness report.
- Optional construction provenance.

Fields:

- Phase 0/1 completeness status.
- Candidate universe count.
- Score-backed candidate count.
- Target/source artifact status.
- Current-policy baseline status.
- Blocking missing sources.

Missing behavior:

- If Phase 0/1 artifacts are absent, show `FR-105: unavailable` and expected artifact path.
- Do not fail precompute email send.

Risk classification: Reporting-only.

### Execution Email

Placement: Existing reporting artifact card area after construction provenance.

Source artifacts:

- Construction provenance.
- Candidate lifecycle.
- Execution reliability and target attainment.
- Phase 0/1 completeness.
- Phase 4 shadow comparison when implemented.

Fields:

- Current sleeve-merge status.
- Shadow Alpha Chase status.
- Top current-vs-shadow deltas.
- Constraint trace count.
- Suppression reason summary.

Missing behavior:

- Show `SPARSE_INPUT` or `MISSING` with artifact path.
- Do not render full ticker table in email.

Risk classification: Reporting-only.

### Confirmation Email

Placement: After execution reliability / target attainment and before dynamic sections.

Source artifacts:

- Execution results.
- Candidate lifecycle.
- Target attainment.
- Construction provenance.
- Phase 4 shadow comparison if generated after execution.

Fields:

- Current execution outcome.
- Whether Alpha Chase artifact was generated.
- Whether artifact is sparse.
- Top opportunity-cost candidates only if rank/score are artifact-backed.

Missing behavior:

- `Alpha Chase shadow: unavailable`.
- Do not use broker fallback payloads to make portfolio-construction claims.

Risk classification: Reporting-only.

### Dashboard

Placement: New research/shadow panel, not in broker or live-pilot panels.

Source artifacts:

- `outputs/research/fr_105/<date>/phase01_artifact_completeness.json`
- `outputs/research/fr_105/<date>/phase4_shadow_alpha_chase_comparison.json`
- Existing `outputs/shadow_candidates/<date>/shadow_evaluation.json` for comparison context only.

Fields:

- Phase status.
- Current versus Alpha Chase position count.
- HHI/effective-N comparison.
- Selected shadow variant.
- Source completeness.
- Blocking gaps.

Missing behavior:

- Empty state: "No FR-105 shadow Alpha Chase artifact available."
- Source path visible.
- No dashboard headline should imply Alpha Chase is trading.

Risk classification: Reporting-only / dashboard-only.

### Daily Summary / Latest Status

Placement: Research/governance section, not execution status.

Source artifacts:

- Phase 0/1 completeness.
- Phase 4 comparison.

Fields:

- `fr105_status`.
- `fr105_completeness_status`.
- `alpha_chase_shadow_status`.
- `alpha_chase_shadow_selected_variant`.
- `alpha_chase_shadow_blocking_reason`.

Missing behavior:

- `unavailable`.
- Do not alter execution health or order status.

Risk classification: Reporting-only.

## Findings

### Finding 1: Reporting should start with completeness, not performance

Severity: High

Current local FR-105 outputs are sparse. The first reporting field should be artifact completeness, not Alpha Chase performance or selection.

Proposed fix: Add Phase 0/1 completeness section before Phase 4 performance comparison.

Risk classification: Reporting-only / artifact-only.

### Finding 2: Emails need concise summaries only

Severity: Medium

Full ticker-level Alpha Chase comparison belongs in JSON and optional markdown, not email bodies.

Proposed fix: Render counts, key deltas, status, missing sources, and artifact path.

Risk classification: Reporting-only.

### Finding 3: Dashboard should keep Alpha Chase in research/shadow lane

Severity: Medium

Dashboard placement matters. Putting Alpha Chase beside broker/live-pilot controls could imply it affects orders.

Proposed fix: Add it under research/shadow/governance, with `shadow_only` and `not_trading` labels.

Risk classification: Reporting-only.

## Validation Required

- Email fixture with present Phase 0/1 completeness artifact.
- Email fixture with missing Phase 0/1 artifact.
- Email fixture with sparse Phase 4 artifact.
- Dashboard data fixture with missing artifact.
- Date-fencing fixture to prevent stale artifact claims.
- Tests proving no broker/execution imports in FR-105 reporting helpers.

## Open Questions

1. Should FR-105 status appear in precompute email before Phase 4 exists?
2. Should confirmation email include Alpha Chase only if same-date artifact exists?
3. Should dashboard use latest FR-105 artifact fallback, or require exact trade date?
4. What is the maximum number of top missing sources or top deltas to show in email?
5. Should `SPARSE_INPUT` be a warning or informational status in dashboard reporting?
