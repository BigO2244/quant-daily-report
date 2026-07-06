# CIO Review - Alpha Concentration / Portfolio Construction Audit

Audit date: 2026-06-26

Scope: Review only. No optimizer behavior, target generation, sizing, broker submission, paper execution, live-pilot, cron, or promotion behavior was changed.

## Summary

The current production path is a sleeve-merge portfolio constructor, not a global alpha-chase optimizer. Signals are generated inside multiple sleeves, each sleeve contributes local winners, regime/sleeve strengths scale those sleeve books, allocator and execution constraints cap or reshape weights, and the paper broker converts targets into trades subject to deadband, min-notional, turnover, trade-count, budget, price, market, and broker gates.

That architecture naturally remains broad. It does not appear to have an active production "alpha concentration" mode that ranks all candidates globally and concentrates capital into the best names. FR-105 is present as a research-only, non-executional framework, but the local 2026-06-25 FR-105 artifacts are sparse and cannot support promotion or concentration claims.

No local 2026-06-26 same-day signals, precompute, execution, or order artifacts were found. Therefore, constraints active on June 26 are divided into:

- Proven active by code/config path: constraints that current code/config would apply when those paths run.
- Not proven active for June 26: any same-day broker, PDT, capital, target, or submitted-order constraint that requires absent canonical artifacts.

## Evidence Reviewed

- Construction code in `daily_quant_report.py`, `core/portfolio_alloc.py`, `regime/regime_config.py`, and `paper/signals_io.py`
- Execution conversion code in `paper/paper_broker.py` and `scripts/run_precomputed_alpaca_execution.py`
- Risk controls in `core/risk_controls.py` and `paper/config_paper.json`
- Candidate lifecycle audit module `core/candidate_trade_lifecycle.py`
- Live-pilot plan builder and executor for isolated live-pilot behavior
- FR-105 research docs/modules/artifacts
- Local `outputs/` inventory for 2026-06-26, 2026-06-25 FR-105 artifacts, and stale older execution traces

## Files/Modules Inspected

| Module | Construction role |
| --- | --- |
| `daily_quant_report.py` | Builds sleeve candidate books and applies live construction policy/sleeve resizing. |
| `regime/regime_config.py` | Maps regimes to sleeve allocations/strengths. |
| `core/portfolio_alloc.py` | Merges sleeve outputs, normalizes sleeve allocations, applies max position, turnover, gross exposure, residual cash handling. |
| `paper/signals_io.py` | Writes signal snapshot, cash row, target weights, and inferred raw score fields. |
| `paper/config_paper.json` | Paper execution constraints: min trade dollars, cash buffer, max turnover, max trades, max position, max position change, deadband. |
| `core/risk_controls.py` | Pre-execution risk overlay: circuit breaker, position cap, sector cap, net/gross exposure cap. |
| `paper/paper_broker.py` | Converts target weights to trades; applies deadband, min notional, cash, turnover, trade-count, position-change, PDT/capital, security, and broker gates. |
| `scripts/run_precomputed_alpaca_execution.py` | Chooses exact precomputed payload vs rebuilt signals, applies pre-execution risk controls, writes execution results, target-attainment, reliability. |
| `core/candidate_trade_lifecycle.py` | Audit-only reconstruction of score/candidate/trade/submission lifecycle. |
| `research/fr105_*` | Research-only global optimizer/concentration replay and top-N frontier. |
| `docs/governance/fr_active/fr_105_global_portfolio_optimizer_and_decision_provenance.md` | Governance statement that FR-105 is active research and non-executional. |

## Score To Order Lifecycle

1. Model/sleeve signals are produced in sleeve-local books.
   - Trend, value, quality, mean-reversion, and defensive ETF sleeves each build candidate sets.
   - Local candidate selection commonly uses top-N style selection before global portfolio construction.

2. Sleeve candidates become sleeve target weights.
   - `create_sleeve_output()` normalizes each sleeve's internal `target_weight` to sum to 1.0 when positive.
   - This means final capital allocation can reflect sleeve membership and normalization, not just raw model conviction.

3. Regime and sleeve policy assign sleeve-level capital.
   - Active sleeves receive allocations by strength; if total strength is non-positive, active sleeves can be equally weighted.
   - Live construction policy can resize sleeve holdings before allocator merge.

4. `PortfolioAllocator.allocate()` merges sleeves into a combined target portfolio.
   - Applies sleeve capacity/headroom, max position caps, optional turnover constraints, minimum gross exposure boosting, and residual cash.
   - Position caps are applied without renormalizing all excess back into remaining names; residual can become cash.

5. `paper/signals_io.write_signals_snapshot()` persists targets for execution.
   - Equity weights are normalized around the cash target.
   - A `CASH` row can be included.
   - `raw_score` may be inferred from several fields, including final signal/score fields or fallback target-like values. That is a transparency risk if displayed as alpha without provenance.

6. `paper_broker.load_targets()` loads and normalizes target weights.
   - Non-cash targets are normalized to investable weight after cash target is parsed.

7. `paper_broker.build_rebalance_trades()` turns target weights into trade rows.
   - Computes target dollars, current dollars, deltas, removed names, buy/sell rows.
   - Applies deadband and min-trade-dollar suppression.
   - Prioritizes buys by target weight, not necessarily score.

8. Execution guards mutate or suppress trades before broker submission.
   - Buy turnover cap, max trades per day, max position change, PDT, capital budget, missing price, blocked ticker, open-window, security master, idempotency, and min-notional filters can all affect the final intended/submitted orders.

9. Post-sell rebudgeting can rebuild buys.
   - It rebuilds buy candidates using current holdings, targets, prices, and budget after sells.
   - It can clip or suppress names for insufficient buying power, remaining max-buy slots, and min notional.

10. `core/candidate_trade_lifecycle.py` can reconstruct the lifecycle from artifacts.
    - It is audit-only and should not affect order generation.

## Constraints That Change Weights Or Orders

| Constraint | Where applied | Effect | 2026-06-26 status |
| --- | --- | --- | --- |
| Sleeve-local top-N selection | `daily_quant_report.py` | Limits each sleeve to local winners before global merge. | Inferred from code; same-day artifact absent. |
| Sleeve internal normalization | `core/portfolio_alloc.py` | Normalizes sleeve positions to 100% of sleeve allocation. | Inferred active in allocator path. |
| Regime sleeve allocation/strength | `regime/regime_config.py`, `daily_quant_report.py` | Scales sleeve books by regime/strength. | Inferred active; same-day regime artifact absent. |
| Equal allocation fallback | `core/portfolio_alloc.py` | Active sleeves can receive equal weights if strengths sum to zero. | Inferred possible; not proven for June 26. |
| Max position cap | `core/portfolio_alloc.py`, `core/risk_controls.py`, `paper/config_paper.json` | Clips single-name target or order exposure. | Config shows paper max position 20%; risk controls default 10% unless overridden. Same-day path not proven. |
| Sector cap | `core/risk_controls.py` | Scales names in capped sectors and moves excess to cash. | Code default 30%; same-day artifact not found. |
| Net/gross exposure cap | `core/risk_controls.py` | Scales targets when exposure exceeds caps. | Code default net long 95%, gross 100%; same-day artifact not found. |
| Circuit breaker | `core/risk_controls.py` | Scales targets during drawdown. | Code exists; same-day activation not proven. |
| Min gross exposure boost | `core/portfolio_alloc.py` | Increases eligible weights up to gross target/headroom. | Inferred active by allocator default; exact June 26 unknown. |
| Residual cash handling | `core/portfolio_alloc.py` | Unused capacity and caps become cash. | Inferred active. |
| Cash target/CASH row normalization | `paper/signals_io.py`, `paper/paper_broker.py` | Rescales equity targets around cash target. | Inferred active when signals are used. |
| Signal date mismatch halt | `paper/paper_broker.py` | Blocks execution when signal date is stale. | Code path exists; same-day not proven. |
| Missing price/blocked ticker filters | `paper/paper_broker.py` | Drops or blocks non-executable targets; may renormalize remaining targets. | Code path exists; same-day not proven. |
| Rebalance deadband | `paper/config_paper.json`, `paper/paper_broker.py` | Suppresses small target-current deltas. | Config shows 1% equity deadband; same-day not proven. |
| Min trade dollars | `paper/config_paper.json`, `paper/paper_broker.py` | Suppresses orders below $100. | Config-proven. |
| Cash buffer | `paper/config_paper.json` | Preserves cash buffer during sizing. | Config-proven. |
| Buy turnover cap | `paper/config_paper.json`, `paper/paper_broker.py` | Scales buys if buy notional exceeds cap. | Config shows 95% max turnover; same-day not proven. |
| Max trades per day | `paper/config_paper.json`, `paper/paper_broker.py` | Truncates or filters number of orders. | Config shows 25; same-day not proven. |
| Max position change | `paper/config_paper.json`, `paper/paper_broker.py` | Scales large single-trade changes. | Config shows 25%; same-day not proven. |
| PDT and capital budget | `paper/paper_broker.py` | Can suppress buys or all orders. | Broker/run-specific; not proven without June 26 artifacts. |
| Exact planned payload | `scripts/run_precomputed_alpaca_execution.py` | Preserves precomputed trade plan and bypasses some rebuild/mutation paths. | Code path exists; same-day payload absent. |
| Candidate lifecycle wiring | `scripts/run_precomputed_alpaca_execution.py`, `core/candidate_trade_lifecycle.py` | Reconstructs reasons; should not change trades. | Existing dirty-tree behavior must be validated as non-mutating. |

## Findings

### Finding 1: Broad holdings are structural, not accidental

Severity: High

The production architecture is multi-sleeve. Each sleeve contributes names, then allocator/risk/execution constraints trim or scale the merged book. This produces a diversified portfolio even when each sleeve is doing local selection.

Proposed fix: Start with reporting-only construction tracing so the CIO can see where broadness enters: sleeve source, local rank/score, pre-cap weight, regime strength, final target weight, trade row, intended order, submitted order, and suppression reason.

Risk classification: Safe reporting-only.

Validation required: Candidate lifecycle and email/report tests proving no target/trade/order mutation.

### Finding 2: No active production alpha-chase mode was found

Severity: High

FR-105 exists as research-only and explicitly non-executional. The local 2026-06-25 FR-105 artifacts show sparse inputs: no candidate pool, no selected top-N variant, no current policy positions, and no execution payload. That cannot justify promotion or a CIO claim that an alpha-concentration optimizer is active.

Proposed fix: Keep FR-105 as backtest/shadow-only until it has point-in-time candidate provenance, current policy comparison, selected variants, turnover/liquidity constraints, and governance approval.

Risk classification: Backtest/shadow-only.

Validation required: FR-105 replay, top-N frontier, and holding-count tests with sparse-artifact handling and ex-ante provenance checks.

### Finding 3: Current config still allows broad books

Severity: Medium

`paper/config_paper.json` shows constraints consistent with a broad allocator: `max_position_pct=0.20`, `min_position_pct=0.05`, `max_turnover_pct=0.95`, `max_trades_per_day=25`, `rebalance_deadband_pct=0.01`, `min_trade_dollars=100`, `cash_buffer_bps=10`. These do not force a small top-N book.

Proposed fix: If the desired behavior is fewer holdings under the existing allocator, propose config changes separately after reporting shows actual breadth drivers.

Risk classification: Paper-only or execution-impacting depending on environment and path.

Validation required: Backtest/shadow comparison, paper dry run, execution diff proving intended orders differ only as approved.

### Finding 4: Score transparency is not yet allocation transparency

Severity: Medium

Candidate lifecycle can carry rank/score aliases, and signals can carry `raw_score`, but final target weights can reflect sleeve normalization, regime scaling, caps, and cash normalization. Reporting a final target weight or inferred raw score as model alpha would be misleading.

Proposed fix: Source-label score fields and add a construction trace that separates model score, sleeve weight, allocator weight, risk-adjusted target, trade delta, and submitted order.

Risk classification: Safe reporting-only.

Validation required: Fixture with score present and absent; email must render unavailable when score source is missing.

### Finding 5: Execution preserves a broad precomputed plan when exact payload mode is used

Severity: Medium

When exact precomputed execution is used, execution should preserve the reviewed planned payload rather than silently reconcentrate at runtime. That is desirable for execution safety, but it means alpha concentration must be solved upstream in construction/governance, not by broker execution.

Proposed fix: Do not change execution to chase concentration. Build concentration in a governed precompute/shadow/paper mode and then review exact planned orders before execution.

Risk classification: Execution-impacting if changed; no change recommended in this review.

Validation required: Equality gate and exact payload tests.

## Why Holdings Remain Broad

The most likely reasons are:

1. Sleeve-local candidate selection creates multiple local winners before any global ranking.
2. Sleeve allocations preserve exposure to several sleeves by design.
3. Per-sleeve normalization and regime strength scaling can turn local sleeve winners into broad final holdings.
4. Position caps prevent outsized concentration.
5. Min gross exposure and cash handling can keep more names active rather than only a concentrated top-N.
6. Deadband and min-trade logic can retain existing holdings if the optimizer does not generate large enough deltas.
7. Buy prioritization by target weight, not global expected alpha, means score conviction is not necessarily the final ordering key.
8. Alpha/challenger concentration research is not promoted into paper/live allocation.

What is not proven locally:

- June 26 score dispersion.
- June 26 final target weights.
- June 26 exact constraints triggered by broker capital, PDT, market guard, or post-sell rebudget.
- June 26 target-vs-actual achieved allocation.

## Recommended Change Type

| Option | Recommendation |
| --- | --- |
| 1. Reporting-only changes | Yes. First priority. Add construction trace and CIO before/after allocation reporting. |
| 2. Config changes | Possible later. Do not change until reporting identifies which constraints drove broadness. |
| 3. Optimizer objective changes | Likely required if the mandate is true global alpha chase. Must be shadow/backtest first. |
| 4. Promotion/governance changes | Required before FR-105 or any challenger sleeve can influence paper/live allocation. |
| 5. New alpha-concentration mode | Recommended if CIO mandate is explicitly alpha chase. It should be a gated mode, not a silent modification of the current constructor. |

## Proposed Fix

Minimal next steps:

1. Build a construction trace artifact.
   - Fields: ticker, sleeve, rank, score source, score value, pre-cap weight, sleeve allocation, allocator weight, risk-adjusted target, current weight, trade delta, intended order, submitted order, final status, reason.

2. Add score dispersion diagnostics.
   - Only use ex-ante score/rank fields.
   - Report whether top scores are clustered or separable.

3. Keep FR-105 non-executional.
   - Fill sparse inputs first.
   - Add point-in-time provenance checks.
   - Compare top-N frontier to current policy in shadow.

4. Draft alpha-concentration mode design.
   - Inputs: candidate pool with ex-ante scores.
   - Objective: maximize expected alpha/conviction subject to max single-name, effective-N floor, sector cap, turnover, liquidity, cash, min-notional, and broker residual constraints.
   - Controls: explicit config flag, dry-run first, paper-only gate, promotion memo, rollback.

## Risk Classification

- Safe reporting-only: construction trace, score provenance labels, breadth explanation, missing-source handling.
- Backtest/shadow-only: score dispersion, FR-105 top-N frontier, alpha-concentration candidate selection.
- Paper-only: changing `top_n`, max position, deadband, max trades, or cash settings in paper.
- Live-pilot-impacting: using concentration output to choose live-pilot orders.
- Execution-impacting: changing allocator objective, target weights, risk controls, rebudgeting, or broker order selection.
- Requires explicit operator approval: any promotion from shadow/research to paper/live or any execution-path change.

## Validation Required

```bash
git status --short
.venv/bin/python -m py_compile daily_quant_report.py core/portfolio_alloc.py paper/signals_io.py paper/paper_broker.py core/risk_controls.py scripts/run_precomputed_alpaca_execution.py core/candidate_trade_lifecycle.py research/fr105_replay_contract.py research/fr105_phase1_baseline.py research/fr105_phase2_topn_frontier.py research/fr105_phase3_holding_count.py
.venv/bin/pytest Tests/test_candidate_trade_lifecycle.py -q
.venv/bin/pytest Tests/test_latest_execution_timeline_status.py -q
```

Additional recommended concentration validation before any behavior change:

```bash
.venv/bin/pytest Tests/test_fr105_replay_contract.py Tests/test_fr105_phase1_baseline.py Tests/test_fr105_phase2_topn_frontier.py Tests/test_fr105_phase3_holding_count.py -q
```

## Open Questions

1. Where are the canonical June 26 precompute/signals/execution artifacts?
2. Is "alpha chase" intended to mean fewer names, higher max single-name weights, higher turnover, or a global score objective?
3. What concentration guardrails should be non-negotiable: max single-name, effective-N floor, sector caps, liquidity, cash, turnover?
4. Should current production remain sleeve-merge while FR-105 runs in shadow, or should an explicit paper-only alpha-concentration mode be added?
5. Which score field is the canonical ex-ante alpha input for concentration: `conviction_score`, `score`, `expected_alpha`, rank, or a new promoted field?
