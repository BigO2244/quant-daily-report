# Shadow Testing: Caerus Orion and Caerus Lyra

## Purpose
- Create a DEV-only side-by-side shadow lane for the new momentum variants.
- Keep Caerus Polaris as the historical Shadow comparison control.
- Track Caerus Orion and Caerus Lyra in this Shadow workflow without sending
  orders. This workflow does not describe their separate capital lanes.

## Strategy Roles
- `Caerus Polaris` / `caerus_polaris`: historical Shadow baseline momentum control.
- `Caerus Orion` / `caerus_orion`: Shadow-observed and the sole PAPER capital sleeve; H2 rank-decay exit + H6 top-5 concentration.
- `Caerus Lyra` / `caerus_lyra`: Shadow-observed and separately Live-authorized; H1 weekly rebalance + H6 top-5 concentration.
- `SPY` / `spy_benchmark`: benchmark only. The benchmark symbol remains `SPY`.

## Artifact Paths
- Daily target books:
  - `outputs/shadow_candidates/YYYY-MM-DD/caerus_polaris.json`
  - `outputs/shadow_candidates/YYYY-MM-DD/caerus_orion.json`
  - `outputs/shadow_candidates/YYYY-MM-DD/caerus_lyra.json`
  - `outputs/shadow_candidates/YYYY-MM-DD/comparison.json`
  - `outputs/shadow_candidates/YYYY-MM-DD/comparison.md`
- Model performance tracking:
  - `outputs/shadow_candidates/performance/shadow_nav_series.csv`
  - `outputs/shadow_candidates/performance/shadow_summary.json`
- FR-028 Phase C promotion-readiness sidecars:
  - `outputs/shadow_candidates/YYYY-MM-DD/longitudinal_metrics.json`
  - `outputs/shadow_candidates/YYYY-MM-DD/stability_surface.json`
  - `outputs/shadow_candidates/YYYY-MM-DD/promotion_readiness.json`
  - `outputs/shadow_candidates/YYYY-MM-DD/promotion_readiness.md`

## Methodology
- Use the same research price panel and momentum signal frame as Alpha Lab.
- Build deterministic target portfolios only; do not submit orders.
- Compare Orion and Lyra against Polaris and against the SPY benchmark.
- Track daily model returns through the DEV-only backtest engine.
- A non-blocking daily wrapper runs automatically after successful precompute via `scripts/cron_precompute.sh`.
- Automatic scheduling calls `scripts/run_shadow_candidates_daily.sh`, which logs to `logs/shadow_YYYY-MM-DD.log`.
- Shadow generation remains best-effort and must never affect production execution success.
- If broker context appears in the comparison artifact, it is informational only and does not drive target generation.
- FR-028 Phase C computes rolling 5D/10D/20D/cumulative excess return versus
  Polaris and SPY, realized volatility, max drawdown, downside-volatility proxy,
  drawdown recovery speed, turnover, constituent changes, top-3/top-5
  concentration, average position size, continuity score, missing-data penalty,
  stability score, readiness state, confidence, and reason codes.
- Phase C metrics use only dated artifacts at or before the evaluated trade
  date. Future dated folders are ignored to preserve no-look-ahead behavior.
- Phase C artifacts are deterministic sidecars. They do not rewrite prior days
  and do not create hidden state.

## Operating Rules
- Polaris remains the historical Shadow control.
- Orion remains a Shadow comparison and the sole PAPER capital sleeve.
- Lyra remains a Shadow comparison and separately operates in Live.
- Shadow artifacts never establish or alter PAPER or Live authority; see
  `docs/CURRENT_OPERATING_STATE.md`.
- SPY remains the benchmark.
- This shadow lane must not write to `outputs/paper_state/`, broker state, or execution payloads.
- Phase C artifacts are labeled `RESEARCH_ONLY` and `NON_EXECUTIONAL`. They must
  not trigger broker orders, strategy switching, scheduler changes, dashboard
  publishing, or automatic promotion.

## Operator Guidance
- Review `outputs/shadow_candidates/YYYY-MM-DD/comparison.md` first each day.
- Use `caerus_polaris.json`, `caerus_orion.json`, and `caerus_lyra.json` when you need the full target-book detail.
- Read `outputs/shadow_candidates/performance/shadow_summary.json` for cumulative model tracking.
- Treat the broker appendix in `comparison.json` / `comparison.md` as informational only.
- Shadow remains model-portfolio based even when broker overlap is shown.

## Promotion Criteria
- Stable daily artifact generation.
- No pipeline inconsistencies or shadow-run failures.
- Coherent turnover and holdings behavior.
- Acceptable drawdown behavior during shadow tracking.
- Continued advantage versus Polaris, with clear awareness of SPY behavior.
- Explicit human review before any shadow-to-paper promotion.

## FR-028 Phase C Readiness Semantics

Readiness states are conservative:

- `OBSERVE`: early evidence only, typically fewer than five valid observations.
- `CONTINUE_SHADOW`: enough evidence to keep watching, but history,
  performance, stability, turnover, concentration, or drawdown is not yet
  promotion-grade.
- `EMERGING_CANDIDATE`: positive excess return and stable behavior with a
  meaningful observation window, but not enough evidence for capital review.
- `CANDIDATE_FOR_CAPITAL`: high-confidence research candidate requiring explicit
  human review. This state does not promote, allocate, or execute.
- `NOT_READY`: broken or materially deficient evidence.

Confidence levels:

- `LOW`: insufficient history or incomplete evidence.
- `MODERATE`: meaningful artifact-backed evidence with remaining observation
  requirements or non-blocking concerns.
- `HIGH`: long observation window with positive excess return and stable
  operational/risk metrics.

Common reason codes include `insufficient_history`, `excessive_turnover`,
`unstable_performance`, `concentration_risk`, `drawdown_risk`,
`insufficient_excess_return`, `missing_data_penalty`, and
`healthy_progression`.

Known blind spots:

- Metrics depend on available shadow artifacts and their target-weight fields.
- Downside volatility and drawdown recovery speed are proxies, not a full risk
  model.
- SPY comparison uses the persisted benchmark series and does not call external
  market data.
- Phase C does not certify paper promotion; it creates review evidence only.

## Local Run
```bash
python3 -m research.shadow_tracking.run \
  --trade-date YYYY-MM-DD \
  --start-date 2014-01-01 \
  --end-date YYYY-MM-DD \
  --output-dir outputs/shadow_candidates
```

## Daily Automation
```bash
bash scripts/run_shadow_candidates_daily.sh --trade-date YYYY-MM-DD
```
