# Recommended Governance Updates

Generated: `2026-06-14T14:03:31Z`

This is the implementation plan for Prompt 2. It intentionally excludes runtime
behavior, execution, broker, cron, allocation, model, strategy, promotion,
retirement, and registry lifecycle changes.

## Go Recommendation

Proceed with a minimal governance and presentation patch on
`codex/active-fr-governance-reconciliation`.

Allowed high-confidence scope:

- Governance wording updates in roadmap/backlog/registry/active specs.
- Incident/recovery report closure wording where stale.
- Artifact governance wording for the canonical Shadow observation method.
- Scorecard presentation-only label change from YTD to Since Observation
  Inception when the operational Shadow observation window starts on
  `2026-05-12`.
- Tests for presentation wording if scorecard code changes.

Do not merge, deploy, or start FR-069 Phase C in Prompt 2.

## High-Confidence Patch Set

### 1. Current State and Last Updated Language

Files:

- `docs/governance/CURRENT_RESEARCH_ROADMAP.md`
- `docs/governance/ORCHESTRATOR_CONTEXT.md`
- `docs/governance/fr_active_backlog.md`
- `docs/governance/fr_registry.md`

Changes:

- Update verified-state language to current evidence:
  - local `HEAD`: `e1792cde79b8d7f2dcd8324451b2258910824bd0`
  - `origin/main`: `e1792cde79b8d7f2dcd8324451b2258910824bd0`
  - VM `HEAD`: `e1792cde79b8d7f2dcd8324451b2258910824bd0`
  - scorecard data health: Fresh
  - NAV integrity: OK
- Remove stale current-state references to `216ac5f` and `as of 2026-06-08`.
- Preserve historical SHAs inside incident/recovery reports when they describe
  the time of that recovery.

### 2. Shadow NAV Recovery Canon

Files:

- `docs/governance/CURRENT_RESEARCH_ROADMAP.md`
- `docs/governance/fr_active_backlog.md`
- `docs/governance/fr_registry.md`
- `docs/artifact_registry.md`
- `docs/artifact_governance.md`
- `docs/artifact_ownership_matrix.md`
- `reports/incidents/2026-06-12_shadow_nav_scorecard_corruption.md`
- `reports/agent_loops/2026-06-13_shadow_nav_same_day_restatement/05_final_summary.md`

Changes:

- Record canonical operational Shadow methodology:
  `dated_same_day_close_to_close_v1`.
- Record canonical observation inception: `2026-05-12`.
- Record recovery observation count: `23` NAV rows.
- State legacy mixed-convention Shadow history is superseded and
  non-decision-grade for promotion/retirement evidence.
- Make clear recovered Shadow NAV is separate from FR-066 canonical portfolio
  NAV and separate from FR-070 execution-target observation.

### 3. Scorecard Presentation Label

Files:

- `scripts/send_shadow_cio_report.py`
- `Tests/test_shadow_cio_report.py`
- Possibly recovery/incident reports if they present current scorecard wording.

Changes:

- Replace operator-facing `YTD (from 2026-05-12)` with
  `Since Observation Inception (2026-05-12)` or equivalent canonical wording
  when the return window starts after January because the canonical operational
  Shadow observation series begins on `2026-05-12`.
- Preserve underlying calculations, rankings, return windows, and promotion
  logic.
- Update tests that currently assert `YTD (from 2026-01-02)` only where the
  code path is intended to display the new label.
- Keep true calendar-year YTD labeling only when the series genuinely covers
  calendar-year YTD under a compatible convention.

Recommended extra presentation note:

- If the report prints `PROMOTE_CANDIDATE`, add or preserve language that this
  is an advisory signal only and no model promotion, retirement, allocation, or
  lifecycle decision is authorized by the scorecard.
- Do not change promotion logic in Prompt 2 unless explicitly scoped.

### 4. FR-028 Reconciliation

Files:

- `docs/governance/fr_active_backlog.md`
- `docs/governance/fr_registry.md`
- `docs/governance/CURRENT_RESEARCH_ROADMAP.md`

Changes:

- Keep `DEPLOYED_OBSERVING`.
- Add that observation windows use only the canonical operational Shadow series:
  `dated_same_day_close_to_close_v1`, inception `2026-05-12`.
- Add that `23` observations existed at recovery.
- Add that legacy mixed-convention series cannot count toward promotion
  thresholds or model-retirement evidence.

### 5. FR-032 and FR-070 Reconciliation

Files:

- `docs/governance/fr_active_backlog.md`
- `docs/governance/fr_registry.md`
- `docs/governance/CURRENT_RESEARCH_ROADMAP.md`
- `docs/governance/fr_active/fr_070_cash_gating_post_sell_budget_reconciliation.md`

Changes:

- Keep FR-070 `DEPLOYED_OBSERVING`.
- Keep FR-070 as the highest immediate operational observation priority.
- Update FR-070 active spec tail; remove “Research not started.”
- State June 12 execution cash discrepancy was `ARTIFACT_TIMING_FAILURE`, not
  failed sell-first rebudgeting.
- State post-buy timing remediation is deployed, but observation remains open
  until next live run validates:
  - `buy_phase_status=BUY_PHASE_COMPLETED` or a classified terminal state
  - `posttrade_snapshot_stage=post_buy` when buys fill
  - `pending_buy_count=0` when buys fill
  - `achieved_cash_weight` within tolerance of `target_cash_weight`
  - MCP target-attainment status `OK_TARGET_ATTAINED` or properly classified
    warning
- Explicitly separate this from the resolved Shadow NAV incident.

### 6. MCP FR Reconciliation

Files:

- `docs/governance/fr_active_backlog.md`
- `docs/governance/fr_registry.md`
- `docs/mcp_server.md`
- `docs/operator/research_mcp_operator_guide.md`, if referenced by the patch.

Changes:

- Update FR-036 tool count from stale `20` to current `27`, if the document
  presents the count as current.
- Keep FR-036 `DEPLOYED_OBSERVING`.
- Keep FR-036a `BACKLOG`.
- Move FR-036b, FR-036c, FR-036d out of not-started backlog language:
  - `attribution_analysis` is implemented/deployed.
  - `stable_window_evaluation` is implemented/deployed.
  - strategy-aware `promotion_readiness` input is implemented/deployed.
- Tie these back to FR-036 rather than creating new FRs.

### 7. FR-055 Through FR-060

Files:

- `docs/governance/fr_active_backlog.md`
- `docs/governance/fr_registry.md`

Changes:

- Normalize FR-055 detailed section status from `IN_PROGRESS` to
  `DEPLOYED_OBSERVING` because both summary and current-state evidence say it is
  deployed and observing.
- Keep FR-056 `DEPLOYED_OBSERVING`.
- Keep FR-057 `IN_PROGRESS`; do not claim deployment.
- Keep FR-058 `DEPLOYED_OBSERVING`.
- Keep FR-059 `IN_PROGRESS`; do not claim deployment.
- Keep FR-060 `IN_PROGRESS`; do not claim deployment.

### 8. FR-063

Files:

- `docs/governance/fr_active_backlog.md`
- `docs/governance/fr_registry.md`
- `docs/governance/CURRENT_RESEARCH_ROADMAP.md`

Changes:

- Normalize FR-063 to `ACTIVE_RESEARCH`.
- Describe FR-063 as supporting differentiation evidence under FR-069, not as a
  retirement action.
- State sufficient canonical new-series history is required before Orion/Lyra
  retirement conclusions.
- Preserve Orion/Lyra continued evaluation.
- Do not retire, rename, promote, demote, or reweight any strategy.

### 9. FR-066

Files:

- `docs/governance/fr_active_backlog.md`
- `docs/governance/fr_registry.md`
- `docs/governance/CURRENT_RESEARCH_ROADMAP.md`

Changes:

- Keep `DEPLOYED_OBSERVING`.
- Clarify FR-066 is canonical portfolio NAV / broker-authoritative portfolio
  history work.
- Clarify Shadow NAV same-day recovery is operational Shadow observation, not
  FR-066 portfolio NAV.

### 10. FR-067 and FR-068

Files:

- `docs/governance/CURRENT_RESEARCH_ROADMAP.md`
- `docs/governance/fr_active_backlog.md`
- `docs/governance/fr_registry.md`

Changes:

- Keep FR-067 `CLOSED_PASS`.
- Remove stale roadmap wording that says Sharadar is pending a trial key.
- Keep FR-068 `PHASES_1_3_COMPLETE`.
- Preserve Orion/Lyra PIT rebaseline pending status.
- Do not retire Orion or Lyra.

### 11. FR-069

Files:

- `docs/governance/fr_active/fr_069_phase_a_architecture_package.md`
- `docs/governance/fr_active/fr_069_phase_b_scaffolding.md`
- `docs/governance/fr_active/fr_069_research_lab_modular_sleeve_architecture.md`
- `docs/governance/fr_active_backlog.md`
- `docs/governance/fr_registry.md`
- `docs/governance/CURRENT_RESEARCH_ROADMAP.md`
- `docs/governance/ORCHESTRATOR_CONTEXT.md`

Changes:

- Keep `PHASE_B_IMPLEMENTED_RESEARCH_ONLY`.
- Update Phase A stale `ACTIVE_PHASE_A` language.
- Record that manifest and MCP tests passed on VM for the deployed scaffold:
  `33 passed in 4.59s`.
- Keep Phase C blocked until a separate owner-approved Phase C task, even
  though manifest/validator/MCP tests now pass.
- Do not implement Phase C.

## Medium-Confidence Items To Leave Open

| Item | Reason To Leave Open |
|---|---|
| FR-034 supersession | Evidence suggests FR-070 absorbed the June 12 cash-drift issue, but FR-034 may still have broader audit scope. Do not close without owner confirmation. |
| Scorecard `PROMOTE_CANDIDATE` logic | Presentation can clarify advisory status, but changing promotion logic would alter research decision surfaces and is beyond Prompt 2 unless explicitly approved. |
| Scorecard valid-day count `39` vs canonical NAV rows `23` | Needs separate evidence-model review. Do not silently change calculation inputs. |
| Health checker WARN/FAIL despite Fresh+OK | Potential diagnostics semantics issue; code change beyond presentation should be separate unless owner authorizes. |
| Old May `DEPLOYED_OBSERVING` rows | Closure requires objective observation evidence. Do not archive by age alone. |

## Validation Plan For Prompt 2

Run:

- `git diff --check`
- `python3 -m py_compile scripts/send_shadow_cio_report.py` if changed
- `python3 -m pytest Tests/test_shadow_cio_report.py Tests/test_shadow_scorecard_health.py -q`
- `python3 scripts/research/validate_sleeve_manifest.py --inventory`
- VM or local MCP tests if local dependencies are available:
  `python3 -m pytest Tests/test_sleeve_manifest.py Tests/test_research_registry_mcp_server.py -q`
- Governance grep checks:
  - `rg -n "YTD \\(from 2026-05-12\\)|YTD from 2026-05-12|216ac5f|as of 2026-06-08|Research not started|BACKLOG_REVIEW|status_review_needed" docs reports scripts Tests`
  - `rg -n "dated_same_day_close_to_close_v1|Since Observation Inception|FR-063|FR-069|FR-070|FR-036b|FR-036c|FR-036d" docs reports scripts Tests`
- Secret scan over staged diff before commit.

Also verify:

- No cron file changed unless explicitly intended for documentation text only.
- No execution/broker/allocation/model/strategy logic changed.
- No strategy registry lifecycle status changed.
- No new FR number introduced.

## Stop/Go Recommendation

Go for Prompt 2 high-confidence docs/presentation patch.

Stop before commit if:

- Any patch requires changing execution, broker, allocation, model, strategy,
  cron, registry lifecycle, promotion, or retirement behavior.
- Any FR status change lacks objective evidence.
- FR-034 closure is attempted without owner approval.
- Scorecard logic changes beyond presentation labeling/caveat.
- Tests show scorecard calculations changed rather than labels only.
