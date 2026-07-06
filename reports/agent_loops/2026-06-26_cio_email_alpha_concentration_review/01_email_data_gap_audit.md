# CIO Review - Email Data Gap Audit

Audit date: 2026-06-26

Scope: Review only. No production behavior, execution logic, optimizer logic, broker state, cron behavior, or live-pilot behavior was changed.

## Summary

The current email stack is materially better at explaining execution status than portfolio decision quality. Precompute, execution, confirmation, and dynamic sections expose order counts, lifecycle counts, some risk notes, and live-pilot account state, but they do not yet provide a CIO-grade before/after portfolio package.

The largest gaps are not necessarily missing data. Many fields already exist in execution artifacts, paper broker summaries, target-attainment artifacts, reliability artifacts, candidate lifecycle artifacts, precompute snapshots, live-pilot plan/run artifacts, or can be deterministically derived from those sources. The wiring from artifact to email is incomplete.

Same-day 2026-06-26 canonical precompute, execution, signals, and order artifacts were not present in the local checkout during this audit. The successful trade reported by the operator is therefore treated as external context, not local artifact evidence. All field availability below is based on current code paths and local artifact shape, with June 26 run-specific values marked unavailable unless the artifact source was found.

## Evidence Reviewed

- Local repo: `/Users/brettolson/Documents/Caerus/quant-daily-report-main`
- Local artifact inventory under `outputs/`
- Email and reporting code paths for precompute, execution, confirmation, dynamic sleeve/live-pilot sections, morning summary, execution summary, and latest execution timeline
- Candidate lifecycle reconstruction code and execution payload normalization code
- Live-pilot plan and execution artifact producers
- FR-105 research artifacts under `outputs/research/fr_105/2026-06-25/`
- Stale summary check: `outputs/trading_day_summary.json` has `trade_date=2024-01-15`

## Files/Modules Inspected

| Module | Role in current reporting |
| --- | --- |
| `scripts/format_precompute_email.py` | Builds precompute email body from `outputs/precompute/<date>/planned_execution_payload.json` and `daily_snapshot.json`. |
| `scripts/send_precompute_email.py` | Sends precompute email by shelling through the formatter. |
| `daily_trade_execution_email.py` | Resolves canonical execution payloads and broker fallback payloads for execution/pretrade email. |
| `paper/build_execution_email.py` | Renders execution email text and HTML, including lifecycle counts and dynamic sections. |
| `scripts/send_trading_confirmation_email.py` | Resolves execution results and renders confirmation email. |
| `core/dynamic_daily_email.py` | Appends dynamic sleeve inventory and live-pilot account/lifecycle sections. |
| `core/execution_summary.py` | Builds execution history/latest summaries with lifecycle counts, cash, NAV, turnover, and status. |
| `scripts/latest_execution_timeline_status.py` | Non-email status surface with lifecycle, cash, asset validation, and timeline diagnostics. |
| `core/candidate_trade_lifecycle.py` | Audit-only candidate lifecycle reconstruction with stage-level reasons, suppression, clipping, and provenance aliases. |
| `core/execution_payload.py` | Canonical execution payload normalization. |
| `core/execution_target_attainment.py` | Target-attainment, cash drift, missing intended buys, and warnings. |
| `core/operational_invariants.py` | Execution reliability report/classification and recommended operator actions. |
| `scripts/live_pilot_build_plan_from_precompute.py` | Live-pilot plan, selected/rejected orders, sleeve source, cap, approval command. |
| `scripts/live_pilot_execute.py` | Live-pilot run artifacts, preflight, submitted orders, evidence, reconciliation, lifecycle. |
| `scripts/morning_report.py` and `core/trading_day_summary.py` | Morning/CIO summary using `outputs/trading_day_summary.json`. |

## Findings

### Current Email Paths

1. Precompute email: `scripts/send_precompute_email.py` -> `scripts/format_precompute_email.py`
2. Execution/pretrade email: `daily_trade_execution_email.py` -> `paper/build_execution_email.py`
3. Confirmation email: `scripts/send_trading_confirmation_email.py`
4. Dynamic appended sections: `core/dynamic_daily_email.py`
5. Morning/CIO console email/report: `scripts/morning_report.py` -> `core/trading_day_summary.py`
6. Live-pilot sources: `scripts/live_pilot_build_plan_from_precompute.py` and `scripts/live_pilot_execute.py`

### CIO Data Gap Matrix

| CIO field | Current email state | Artifact/source status | Wiring gap | Severity | Risk classification | Proposed fix |
| --- | --- | --- | --- | --- | --- | --- |
| Portfolio before vs after | Not rendered as a portfolio table. | Partly exists in broker position snapshots, paper broker return payloads, `position_reconciliation`, and posttrade snapshots when available. June 26 local artifacts absent. | No deterministic before/after portfolio summary is generated for emails. | High | Safe reporting-only | Build read-only CIO decision summary from pre/post positions and render top-level delta. |
| Cash before vs after | Partial cash fields appear in execution context and live-pilot account section. | `cash_gate_diagnostics`, account snapshots, `target_cash_weight`, `achieved_cash_weight`, target-attainment artifacts. | Not normalized into before/after cash dollars and weights by stage. | High | Safe reporting-only | Add cash before/after block with source path and stage labels. |
| Position count before vs after | Precompute can show current position count; live-pilot shows live positions count. | Derivable from positions snapshots and target/achieved positions. | Not rendered as before/after delta. | Medium | Safe reporting-only | Add position count delta to CIO summary. |
| Sector allocation before vs after | Not rendered. | Derivable from holdings/targets plus security master/universe sector map; risk controls may have target sector metrics. | No email computation or artifact field. | Medium | Safe reporting-only | Compute sector before/after in report artifact; render top sectors and cap usage. |
| Top position weights before vs after | Not rendered. | `position_reconciliation` can carry target and achieved weights; broker snapshots can provide actual weights. | Not exposed in email. | High | Safe reporting-only | Render top 10 before/after weights with target/actual deltas. |
| Concentration metrics before vs after | Not rendered for production/paper allocation. | FR-105 research computes HHI/effective-N when inputs exist, but 2026-06-25 artifacts are sparse. Dynamic shadow sections have sleeve-level concentration only. | No production email concentration metrics. | High | Safe reporting-only | Add deterministic HHI/effective-N from before/after weights. Mark unavailable if source positions missing. |
| Target weights vs actual weights | Mostly absent. | `paper_broker` return payload includes `position_reconciliation`; canonical email payload may not preserve full table. | Artifact-to-email field loss. | High | Safe reporting-only | Persist optional target-vs-actual table into a reporting artifact and render largest deltas. |
| Retained, added, reduced, removed | Not rendered. | Derivable from previous holdings, final target, actual positions, and trade list. | No holdings change classifier. | High | Safe reporting-only | Add holdings action classifier to CIO summary. |
| Expected alpha / score contribution by ticker | Not rendered. | Candidate lifecycle provenance aliases include rank/score fields if source rows contain them; signals snapshot may infer `raw_score`; shadow/FR-105 may contain scores. | No source-labeled score table; `raw_score` can be ambiguous when inferred from weights. | Medium | Safe reporting-only first; backtest/shadow for score policy | Render only source-labeled score/rank fields. Do not claim model explanation if provenance missing. |
| Portfolio score before vs after | Not available in production email. | No canonical production portfolio score found. FR-105 artifacts were sparse. | Data absent. | Medium | Backtest/shadow-only until defined | Define optional research score artifact; emails should show unavailable unless canonical artifact exists. |
| Active constraints shaping trades | Partially implied by risk note, risk meta, candidate lifecycle reason counts, execution filters. | `risk_meta`, `capital_budget`, `pdt_pretrade`, `open_window_validation`, `execution_filter`, `post_sell_rebudget`, `risk_controls.json`, candidate lifecycle. | No consolidated constraint trace. | High | Safe reporting-only | Build constraint trace section: constraint, source, affected tickers, before/after effect, status. |
| Suppressed candidates and why | Execution email has compressed lifecycle counts/reasons. | Candidate lifecycle rows and post-sell rebudget skipped buy orders have reasons. | Email lacks ranked, ticker-level suppressed candidates. | High | Safe reporting-only | Render top suppressed candidates by rank/score/notional with exact reason. |
| Top-ranked candidates not bought | Not rendered. | Candidate lifecycle can support this if rank/score fields exist. June 26 local artifacts absent. | No top-not-bought table. | High | Safe reporting-only | Render from lifecycle rows; mark rank unavailable when source lacks rank. |
| Turnover impact | Partly shown as turnover or risk note. | `turnover_pct`, `turnover_notional`, `risk_meta`, target-attainment. | Not tied to holdings changes or constraints. | Medium | Safe reporting-only | Add turnover block with before/after and cap utilization. |
| Rebalance reason by ticker | Sell notes and some order reasons appear; buy rationale is limited. | Trade rows, lifecycle rows, target deltas, rebudget skipped orders. | No unified ticker-level rebalance reason. | Medium | Safe reporting-only | Add ticker action table: action, reason, source module, stage. |
| Risk/exposure summary | Partial risk summary is rendered when payload provides it. | Gross/net exposure, cash target, sector weights, risk controls, account snapshots. | No before/after risk/exposure package. | Medium | Safe reporting-only | Render exposure before/after, cash, gross/net, sector cap utilization. |
| Live-pilot lifecycle fields | Dynamic/confirmation sections cover account, open/filled orders, approved/submitted/unfilled/escalated counts, policy fields. | Plan/run artifacts include selected order, rejected orders, cap, sleeve source, preflight, approval mode, reconciliation, rollback context. | Plan details and preflight/rejected-order fields are not fully surfaced. | High | Safe reporting-only, live-pilot-impacting if semantics change | Render existing live-pilot plan/run controls with source date and run id. |
| Stale artifact source problems | Some statuses show fallback, but not prominent. | `core/dynamic_daily_email.py` falls back to latest shadow date; live-pilot plan/run selection is mtime-based; morning summary artifact is stale locally. | Email can bury stale or unrelated sources. | High | Safe reporting-only | Date-fence dynamic sources or render a prominent stale-source banner. |
| Misleading `MISSING` / `UNAVAILABLE` for inactive research sleeves | Manifest-only sleeves can show unavailable even when expected inactive. | Registry/manifest lifecycle state can distinguish inactive from broken. | Status label lacks intent. | Low | Safe reporting-only | Add `INACTIVE_NOT_EXPECTED` / `RESEARCH_INACTIVE` labels where registry says inactive. |
| Reliability score / operator action | Not consistently rendered in execution/confirmation emails. | `execution_reliability_*` fields and recommended actions exist. | Artifact-to-email wiring gap. | High | Safe reporting-only | Add reliability block to execution and confirmation emails. |
| Target-attainment / cash drift | Not consistently rendered. | `execution_target_attainment_*` fields and artifact path exist. | Artifact-to-email wiring gap. | High | Safe reporting-only | Add target-attainment block with missing intended buys and warnings. |

## Severity

- High: Missing before/after portfolio, target-vs-actual, concentration, constraints, suppressed candidates, top-not-bought, stale-source labeling, reliability/target-attainment blocks.
- Medium: Sector allocation, turnover context, rebalance reason by ticker, portfolio score definition, raw score provenance.
- Low: Inactive research sleeve labels where unavailable is technically accurate but operator-hostile.

## Proposed Fix

Recommended minimal patch set, reporting-only:

1. Add a read-only CIO decision summary artifact builder.
   - Inputs: precompute snapshot, planned payload, execution payload/results, candidate lifecycle artifact, broker/pre/post position snapshots, risk controls artifact, target-attainment artifact, reliability artifact, live-pilot plan/run artifacts.
   - Output: optional deterministic artifact, for example `outputs/runs/<run_id>/reports/cio_decision_summary.json`.
   - Rule: if a field cannot be sourced, emit `unavailable` with a missing source path. Do not infer from model internals.

2. Render compact sections in precompute, execution, and confirmation emails.
   - Portfolio before/after
   - Cash before/after
   - Position count, top weights, sector allocation, HHI/effective-N
   - Target vs actual largest deltas
   - Holdings retained/added/reduced/removed
   - Constraint trace
   - Suppressed/top-not-bought candidates
   - Reliability and target-attainment

3. Date-fence dynamic sections.
   - Shadow candidate fallback should show a prominent stale-source warning when using a prior date.
   - Live-pilot plan/run sections should match email trade date or clearly state source date/run id.
   - Morning report should refuse or warn on stale `outputs/trading_day_summary.json`.

4. Source-label score fields.
   - Show `rank`, `score`, `conviction_score`, or `expected_alpha` only when source artifacts provide those fields.
   - Avoid treating normalized target weight or inferred `raw_score` as model alpha.

5. Fix inactive sleeve labeling.
   - Differentiate expected inactive research sleeves from broken/missing artifacts.

## Risk Classification

- Safe reporting-only: adding read-only artifact summary, email rendering of existing artifact fields, stale-source warnings, inactive sleeve labels, reliability/target-attainment blocks.
- Backtest/shadow-only: defining portfolio score, score contribution, alpha-rank policies, and score quality analysis.
- Paper-only: changing what fields are persisted by paper broker if that requires touching execution path; should be guarded by tests proving no order mutation.
- Live-pilot-impacting: any change that alters live-pilot status semantics, selected order, order type, cap, preflight, or reconciliation.
- Execution-impacting: any change to optimizer, targets, trade sizing, order filters, broker submission, cash rebudgeting, or risk controls.
- Requires explicit operator approval: any live-pilot or execution-impacting behavior change.

## Validation Required

Safe reporting validation:

```bash
git status --short
.venv/bin/python -m py_compile core/dynamic_daily_email.py core/execution_summary.py core/execution_target_attainment.py core/operational_invariants.py paper/build_execution_email.py scripts/format_precompute_email.py scripts/send_trading_confirmation_email.py daily_trade_execution_email.py
.venv/bin/pytest Tests/test_execution_email.py -q
.venv/bin/pytest Tests/test_candidate_trade_lifecycle.py -q
.venv/bin/pytest Tests/test_latest_execution_timeline_status.py -q
.venv/bin/pytest Tests/test_dashboard_ui_status.py -q
```

Additional recommended reporting tests:

```bash
.venv/bin/pytest Tests/test_format_precompute_email.py Tests/test_dynamic_daily_email.py Tests/test_confirmation_email_reconciled.py Tests/test_execution_summary.py -q
```

Any new email claim must have a fixture proving the source artifact path and the unavailable behavior when the source is absent.

## Open Questions

1. Where are the canonical 2026-06-26 precompute/execution/order artifacts for the successful trade?
2. Should the CIO email be allowed to use broker-fallback payloads, or should it fail closed when canonical decision artifacts are missing?
3. What is the canonical sector taxonomy for before/after sector allocation?
4. Should portfolio score be defined as a research-only score first, or should production emails omit it until FR-105 creates a promoted artifact?
5. Which research sleeves are expected inactive and should be labeled as such rather than `UNAVAILABLE`?
