# Shadow Testing: Caerus Orion and Caerus Lyra

## Purpose
- Create a DEV-only side-by-side shadow lane for the new momentum variants.
- Keep Caerus Polaris as the production paper control and comparison anchor.
- Track Caerus Orion and Caerus Lyra without sending any orders.

## Strategy Roles
- `Caerus Polaris` / `caerus_polaris`: current baseline momentum control.
- `Caerus Orion` / `caerus_orion`: primary shadow candidate, H2 rank-decay exit + H6 top-5 concentration.
- `Caerus Lyra` / `caerus_lyra`: secondary shadow challenger, H1 weekly rebalance + H6 top-5 concentration.
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

## Methodology
- Use the same research price panel and momentum signal frame as Alpha Lab.
- Build deterministic target portfolios only; do not submit orders.
- Compare Orion and Lyra against Polaris and against the SPY benchmark.
- Track daily model returns through the DEV-only backtest engine.
- A non-blocking daily wrapper runs automatically after successful precompute via `scripts/cron_precompute.sh`.
- Automatic scheduling calls `scripts/run_shadow_candidates_daily.sh`, which logs to `logs/shadow_YYYY-MM-DD.log`.
- Shadow generation remains best-effort and must never affect production execution success.
- If broker context appears in the comparison artifact, it is informational only and does not drive target generation.

## Operating Rules
- Polaris remains the operational paper control.
- Orion is the lead shadow candidate.
- Lyra is the secondary challenger.
- SPY remains the benchmark.
- This shadow lane must not write to `outputs/paper_state/`, broker state, or execution payloads.

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
