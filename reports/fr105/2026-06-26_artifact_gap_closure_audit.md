# FR-105 Artifact Gap Closure Audit - 2026-06-26

## Summary

VM `main` is synced to PR #131 merge commit `f1cbb7bb4c59f3e26e433cc1b7e575e3e36e198c`. The generated FR-105 artifacts for `2026-06-26` are correctly blocked:

- Phase 0/1 completeness: `BLOCKED_ARTIFACT_GAPS`
- Phase 2 global top-N frontier: `BLOCKED_ARTIFACT_GAPS`
- Phase 3 holding-count frontier: `BLOCKED_ARTIFACT_GAPS`
- Shadow Alpha Chase framework/comparison: disabled, default-off, no paper/live influence

The immediate canonical blocker is that Phase 0 and Phase 1 source artifacts were not generated before the completeness check:

- `outputs/research/fr_105/2026-06-26/global_optimizer_replay_contract.json`: missing
- `outputs/research/fr_105/2026-06-26/phase1_current_policy_baseline.json`: missing

I ran the existing research-only FR-105 generators into `/tmp/fr105_gap_audit` on the VM to distinguish generator-ordering gaps from true evidence gaps. That temp run proved that running Phase 0 and Phase 1 would close `active_constraints`, `current_holdings`, and `provenance_availability`, but Phase 0/1 would still remain blocked on 11 unavailable evidence fields until existing precompute/run artifacts are wired into the replay contract and/or new reporting-only provenance artifacts are added.

No broker, order, live-pilot, scheduler, cron, optimizer, sizing, paper/live allocation, or execution scripts were run for this audit.

## Evidence Reviewed

- VM artifact: `outputs/research/fr_105/2026-06-26/phase01_artifact_completeness.json`
- VM artifact: `outputs/research/fr_105/2026-06-26/phase2_global_topn_frontier.json`
- VM artifact: `outputs/research/fr_105/2026-06-26/phase3_optimizer_derived_holding_count.json`
- VM artifact: `outputs/research/fr_105/2026-06-26/shadow_alpha_chase_comparison.json`
- VM artifact: `outputs/research/fr_105/shadow_alpha_chase_framework.json`
- VM run bundle: `outputs/runs/2026-06-26T093512-0400_109386d/`
- Precompute artifacts: `outputs/precompute/2026-06-26/signals.json`, `daily_snapshot.json`, `planned_execution_payload.json`, `contract.json`
- Run artifacts: `execution_payload.json`, `execution_results.json`, `broker/intended_orders_2026-06-26.json`, `broker/post_sell_rebudget_2026-06-26.json`, `broker/posttrade_positions.json`, `broker/posttrade_account_snapshot.json`
- Audit artifacts: `audit/risk_controls.json`, `audit/execution_target_attainment_2026-06-26.json`, `audit/execution_integrity.json`
- Shadow artifacts: `outputs/shadow_candidates/2026-06-26/*.json`
- Source modules: `research/fr105_phase01_completeness.py`, `research/fr105_replay_contract.py`, `research/fr105_phase1_baseline.py`

## Current Canonical Completeness State

The canonical completeness artifact has `found_count=0`, `missing_count=0`, and `unavailable_count=14` because both source artifacts are absent:

| Source artifact | Expected path | Current status | Effect |
| --- | --- | --- | --- |
| Phase 0 replay contract | `outputs/research/fr_105/2026-06-26/global_optimizer_replay_contract.json` | `MISSING` | Completeness receives an empty contract; all Phase 0-derived evidence becomes unavailable |
| Phase 1 current-policy baseline | `outputs/research/fr_105/2026-06-26/phase1_current_policy_baseline.json` | `MISSING` | Completeness receives an empty baseline; positions, PIT controls, and baseline metrics are unavailable |

Temp research-only generation into `/tmp/fr105_gap_audit` produced:

- Phase 0 replay contract: `PASS`
- Phase 1 current-policy baseline: `PASS`
- Phase 0/1 completeness: still `INCOMPLETE`, `BLOCKED_ARTIFACT_GAPS`
- Found after temp generation: `active_constraints`, `current_holdings`, `provenance_availability`
- Still unavailable after temp generation: `candidate_pool`, `candidate_universe`, `current_weights`, `execution_residuals`, `lifecycle_artifact`, `pit_lineage`, `score_source`, `sleeve_source`, `suppression_reasons`, `target_portfolio`, `target_weights`

## Gap Table

| Requirement | Current canonical status | Expected source | Current source if available | Exists elsewhere under another name? | Gap class | Safe closure path |
| --- | --- | --- | --- | --- | --- | --- |
| `candidate_universe` | `UNAVAILABLE` | `global_optimizer_replay_contract.universe_snapshot` | None in FR-105 contract; temp contract has `status=unavailable`, no `universe_id`, no `ticker_count`, no source path | Partial candidates/rank tables exist in `outputs/shadow_candidates/2026-06-26/*.json`; selected production names exist in `outputs/precompute/2026-06-26/signals.json`; no canonical current-policy universe snapshot found | Missing generator / data absence | Add a reporting-only candidate universe snapshot builder if a canonical universe source can be identified; otherwise keep unavailable |
| `candidate_pool` | `UNAVAILABLE` | `global_optimizer_replay_contract.sleeve_candidates` | Temp replay contract has `sleeve_candidates=[]` because no lifecycle artifact was found | Selected target rows exist in `outputs/precompute/2026-06-26/signals.json`; signal-store rows exist in `outputs/signal_store/signals_2026-06-26.json`; shadow sleeve `rank_table` rows exist, but these are not the current sleeve-merge production candidate pool | Schema mismatch / missing generator | Safe artifact-only patch can ingest precompute selected rows as `selected_target_candidates`; full candidate pool still needs upstream precompute candidate tape |
| `pit_lineage` | `UNAVAILABLE` | `phase1.pit_controls` plus Phase 0 universe/candidate as-of fields | Temp baseline has `data_asof=null`, `price_asof=null`, `universe_asof=null`; completeness can only infer `universe_asof=2026-06-26` from sparse contract | `signals.json.meta.asof_date=2026-06-25`; `planned_execution_payload.json.pricing_asof=2026-06-25`; `daily_snapshot.json.asof=2026-06-26`; `risk_adjusted_2026-06-26.json.meta.asof_date=2026-06-25` | Artifact-to-FR105 wiring gap plus missing universe lineage | Safe patch can wire data/price as-of from precompute and risk-adjusted artifacts; true universe lineage remains unavailable until a universe source is emitted |
| `score_source` | `UNAVAILABLE` | Candidate rows with `score`, `conviction_score`, or `expected_alpha` and non-weight-derived source | Temp replay contract has no candidates | `outputs/precompute/2026-06-26/signals.json` has `raw_score`, but for production rows it equals target weight, so it must not be treated as alpha score; `outputs/signal_store/signals_2026-06-26.json` has `signal_strength`; shadow rank tables have `momentum_score` but are shadow, not current production allocation | Score provenance missing / unsafe score semantics | Keep production `score_source` unavailable unless a source explicitly identifies non-weight-derived alpha/conviction scores. Shadow scores can be reported separately as shadow-only |
| `sleeve_source` | `UNAVAILABLE` | `global_optimizer_replay_contract.sleeve_candidates[*].sleeve_id/strategy_id/source_model` | Temp replay contract has no candidates | `signals.json.signals[*].sleeve`, `signal_store[*].sleeve_source`, and `risk_controls.result.weights[*].sleeve` exist | Schema mismatch | Safe artifact-only patch can map explicit sleeve fields into Phase 0 candidates without changing trading |
| `target_portfolio` | `UNAVAILABLE` | `source_artifacts.target_portfolio_path` pointing at a canonical target portfolio artifact | Temp replay contract has `target_portfolio_path=null`; no `target_portfolio.json` found at configured search paths | Final risk-adjusted targets exist in `outputs/runs/2026-06-26T093512-0400_109386d/snapshots/risk_adjusted_2026-06-26.json`; risk-control weights/actions exist in `audit/risk_controls.json`; pre-risk signals exist in `outputs/precompute/2026-06-26/signals.json` | Naming mismatch / missing canonical artifact | Safe patch can produce or reference a research-only target portfolio artifact from risk-adjusted targets and risk-control actions |
| `current_holdings` | `UNAVAILABLE` canonically; `FOUND` after temp Phase 0/1 generation | `phase1.baseline_positions` or `phase0.current_portfolio` | Temp replay contract reads `broker/posttrade_positions.json` and finds 25 positions | Exists at `outputs/runs/2026-06-26T093512-0400_109386d/broker/posttrade_positions.json` | Generator-ordering gap | Safe to close by running/wiring Phase 0 and Phase 1 generation before completeness |
| `current_weights` | `UNAVAILABLE` | Current positions with `current_weight` or `weight` | Temp replay contract has positions and market values, but `current_weight=null` for all 25 rows | Posttrade account equity exists in `broker/posttrade_account_snapshot.json`; posttrade position market values exist in `broker/posttrade_positions.json` | Reporting calculation missing | Safe artifact-only patch can compute current weights as market value divided by same-snapshot equity, with source fields recorded. Do not use these as scores |
| `target_weights` | `UNAVAILABLE` | `global_optimizer_replay_contract.sleeve_candidates[*].target_weight` | Temp replay contract has no candidates, so `target_weight_count=0` | Risk-adjusted final target weights exist in `snapshots/risk_adjusted_2026-06-26.json`; pre-risk target weights exist in `outputs/precompute/2026-06-26/signals.json`; `audit/risk_controls.json` has post-risk weights | Schema mismatch | Safe artifact-only patch can wire risk-adjusted target rows into the replay contract as target weights, clearly marked as target weights, not alpha scores |
| `suppression_reasons` | `UNAVAILABLE` | `global_optimizer_replay_contract.execution_residuals.suppression_reason_counts` and candidate lifecycle decision reasons | Temp replay contract has empty suppression counts because lifecycle is absent | `audit/execution_target_attainment_2026-06-26.json.missing_intended_buys`; `audit/execution_integrity.json.missing_buy_orders`; `broker/post_sell_rebudget_2026-06-26.json.skipped_buy_orders`; `planned_execution_payload.json.filter_stats`; `daily_snapshot.json.skipped_trades` | Schema mismatch / missing lifecycle artifact | Safe patch can add a reporting-only residual/suppression summary from existing audit artifacts. Full candidate suppression still needs a canonical candidate lifecycle artifact |
| `active_constraints` | `UNAVAILABLE` canonically; `FOUND` after temp Phase 0/1 generation | `global_optimizer_replay_contract.constraints_snapshot` | Temp replay contract found max single-name, min trade, turnover, cash target, buying power, rebudget policy | Additional active risk controls/actions exist in `audit/risk_controls.json` with sector-cap and exposure-cap actions | Generator-ordering gap plus partial wiring | Safe to close by running/wiring Phase 0 and Phase 1 generation; next patch should also include risk-control actions for better constraint provenance |
| `lifecycle_artifact` | `UNAVAILABLE` | `source_artifacts.candidate_trade_lifecycle_path` | Temp replay contract has `candidate_trade_lifecycle_path=null` | No `audit/candidate_trade_lifecycle_2026-06-26.json` found in the VM run bundle; partial lifecycle evidence exists in `intended_orders`, `execution_payload`, `execution_results.order_lifecycle`, `execution_integrity`, and `execution_target_attainment` | Missing generator | Safe reporting-only generator can build candidate lifecycle from existing artifacts if it remains artifact-only and defaults unknowns to unavailable |
| `execution_residuals` | `UNAVAILABLE` | `global_optimizer_replay_contract.execution_residuals` | Temp replay contract has all residual counts null/empty because lifecycle is absent | Residual-like evidence exists in target-attainment and integrity artifacts: missing intended buys, skipped/deferred buy notional, submitted/fill counts, explicit defer/block reasons | Schema mismatch / missing lifecycle artifact | Safe patch can map existing target-attainment and integrity fields into execution residuals; full residuals need candidate lifecycle |
| `provenance_availability` | `UNAVAILABLE` canonically; `FOUND` after temp Phase 0/1 generation | `global_optimizer_replay_contract.provenance_schema_version` and validation status | Temp replay contract provides `fr105_candidate_provenance.v1` and validation `PASS` | Exists once Phase 0 replay contract is generated | Generator-ordering gap | Safe to close by running/wiring Phase 0 generation before completeness |

## Safe To Fix Now

These can be closed with reporting/artifact-only work and no trading behavior change:

1. Add an orchestrator or checklist script that runs the existing research-only sequence:
   - `scripts/research/build_fr105_replay_contract.py --trade-date <date>`
   - `scripts/research/run_fr105_phase1_baseline.py --trade-date <date>`
   - `scripts/research/check_fr105_phase01_artifact_completeness.py --trade-date <date>`
   - Phase 2/3 only after Phase 0/1 readiness is evaluated
2. Extend `research/fr105_replay_contract.py` source discovery to read existing artifact-backed sources:
   - precompute target signals: `outputs/precompute/<date>/signals.json`
   - risk-adjusted final targets: run `snapshots/risk_adjusted_<date>.json`
   - risk-control actions: run `audit/risk_controls.json`
   - target-attainment residuals: run `audit/execution_target_attainment_<date>.json`
   - execution integrity residuals: run `audit/execution_integrity.json`
   - posttrade account equity for current weights: run `broker/posttrade_account_snapshot.json`
   - posttrade positions for current holdings/weights: run `broker/posttrade_positions.json`
3. Add a reporting-only canonical target portfolio artifact, or explicitly allow FR-105 to use `risk_adjusted_<date>.json` as the target portfolio source.
4. Add tests proving that:
   - target weights are not treated as `raw_score`, `conviction_score`, or `expected_alpha`
   - current weights are calculated only from same-snapshot market value/equity and are not scores
   - missing lifecycle/candidate pool fields remain `UNAVAILABLE`
   - sparse evidence still blocks Phase 2/3 with `BLOCKED_ARTIFACT_GAPS`

## Not Safe Yet / Requires Architecture Or Precompute Changes

These should not be paper/live-affecting, but they need upstream artifact design before Phase 0/1 can become fully READY:

1. Canonical current-policy candidate universe:
   - Need an artifact that records all securities eligible/considered for the current sleeve-merge run, not just selected targets.
2. Canonical candidate pool and lifecycle:
   - Need per-candidate lifecycle across precompute, executable filter, intended orders, post-sell rebudget, submitted orders, fills, skips, clips, and suppressions.
3. Score source provenance:
   - Existing production `raw_score` in `signals.json` appears weight-like for selected targets. FR-105 must not treat weight-derived values as alpha scores.
   - A future precompute artifact should emit explicit `score`, `score_source`, and score semantics if Alpha Chase evaluation needs score-ranked current-policy candidates.
4. PIT universe lineage:
   - Existing artifacts expose data/pricing as-of fields, but not a canonical universe id, universe source artifact, or ticker count for the current-policy candidate universe.
5. Full suppression reasons:
   - Current execution artifacts explain missing intended buys and rebudget skips, but not every upstream unselected candidate. Full suppression requires a candidate lifecycle or decision tape.

## Smallest Safe Next Patch

Recommended next patch: **FR-105 Phase 0/1 source wiring and residual summary**, reporting/artifact-only.

Proposed files:

- `research/fr105_replay_contract.py`
- `research/fr105_phase1_baseline.py`
- `research/fr105_phase01_completeness.py`
- `scripts/research/run_fr105_phase01_readiness.py` or similar thin orchestrator
- `Tests/test_fr105_replay_contract.py`
- `Tests/test_fr105_phase1_baseline.py`
- `Tests/test_fr105_phase01_completeness.py`
- `Tests/test_fr105_phase01_readiness.py` if an orchestrator is added

Implementation boundaries:

- Do not call broker/order/live-pilot modules.
- Do not change optimizer, sizing, paper/live allocation, cron, scheduler, or execution behavior.
- Do not infer alpha scores from target weights, allocation weights, or current weights.
- Use explicit score fields only when an artifact says what the score source is.
- Keep missing upstream universe/candidate-lifecycle evidence as `UNAVAILABLE`.
- Keep Alpha Chase disabled/default-off.

Expected effect:

- Phase 0/1 should move from 14 unavailable fields to a smaller blocked set.
- It should probably still remain blocked until a canonical candidate universe, full candidate pool, candidate lifecycle, and non-weight-derived score provenance exist.
- Phase 2/3 should continue to emit `BLOCKED_ARTIFACT_GAPS` until Phase 0/1 is genuinely complete.

## Validation Commands

Safe local/VM validation for the next patch:

```bash
git status --short
git diff --check
PY=${PY:-.venv/bin/python}
$PY -m py_compile \
  research/fr105_replay_contract.py \
  research/fr105_phase1_baseline.py \
  research/fr105_phase01_completeness.py \
  research/fr105_phase2_topn_frontier.py \
  research/fr105_phase3_holding_count.py \
  scripts/research/build_fr105_replay_contract.py \
  scripts/research/run_fr105_phase1_baseline.py \
  scripts/research/check_fr105_phase01_artifact_completeness.py
$PY -m pytest \
  Tests/test_fr105_replay_contract.py \
  Tests/test_fr105_phase1_baseline.py \
  Tests/test_fr105_phase01_completeness.py \
  Tests/test_fr105_phase2_topn_frontier.py \
  Tests/test_fr105_phase3_holding_count.py \
  Tests/test_shadow_alpha_framework.py \
  -q
```

Safe evidence-generation validation using a temp output root:

```bash
rm -rf /tmp/fr105_gap_audit
$PY scripts/research/build_fr105_replay_contract.py \
  --trade-date 2026-06-26 \
  --output-root /tmp/fr105_gap_audit
$PY scripts/research/run_fr105_phase1_baseline.py \
  --trade-date 2026-06-26 \
  --output-root /tmp/fr105_gap_audit
$PY scripts/research/check_fr105_phase01_artifact_completeness.py \
  --trade-date 2026-06-26 \
  --output-root /tmp/fr105_gap_audit
```

## Safety Confirmation

This audit touched reporting only. It did not run broker execution, submit orders, run live-pilot execution, alter optimizer behavior, change sizing, change paper/live allocation, change cron, change scheduler, or modify order-submission behavior. Alpha Chase remains disabled/default-off, and sparse evidence remains blocked with `BLOCKED_ARTIFACT_GAPS`.
