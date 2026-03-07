# Canonical Performance Runbook

## Purpose

This runbook describes how to operate the real-data canonical performance pipeline and interpret output quality.

## Upstream Artifacts

Required in real-data mode:

- `outputs/perf/nav_timeseries.csv`

Optional but recommended:

- `outputs/perf/inception_nav_*.csv`
- `outputs/perf/benchmark_close_history.csv` — auto-produced daily by perf artifact producers
- `outputs/perf/vix_close_history.csv` — auto-produced daily by perf artifact producers (NEW)
- `outputs/perf/premarket_analyzer_scores.csv` — auto-produced daily by perf artifact producers
- `outputs/vix_regime/regime_history.csv`
- `outputs/perf/holdings_mtm_*.csv`
- `outputs/ledger/trades.csv`
- `signals/*.json`

Expected schema contracts:

- Benchmark close history: `date,spy_close[,spy_return]` — source: `outputs/perf/benchmark_close_history.csv`
- VIX close history: `date,vix_close[,vix_return]` — source: `outputs/perf/vix_close_history.csv`
- Premarket analyzer: `date,premarket_score,bearish_flag,signal_bucket,analyzer_version,notes` — source: `outputs/perf/premarket_analyzer_scores.csv`
- Optional analyzer components: `vix_component,trend_component,realized_vol_component,gap_risk_component,breadth_component,macro_component`

## Build Commands

```bash
/Users/brettolson/Documents/Caerus/quant-daily-report-main/quant_research_agent/.venv/bin/python -m research.alpha_assessment.build_alpha_assessment --rebuild-canonical
/Users/brettolson/Documents/Caerus/quant-daily-report-main/quant_research_agent/.venv/bin/python -m research.overlay_engine.build_overlay_engine
```

Producer-only rebuild (benchmark + VIX + analyzer):

```bash
/Users/brettolson/Documents/Caerus/quant-daily-report-main/quant_research_agent/.venv/bin/python -m paper.perf_artifact_producers --asof-date 2026-03-06
```

Producers are also auto-invoked by canonical build:
1. `build_alpha_assessment.py` refreshes benchmark and VIX before assembling canonical_performance.csv
2. `daily_quant_report.py` refreshes all three producers at end of each run

Optional explicit synthetic fallback:

```bash
/Users/brettolson/Documents/Caerus/quant-daily-report-main/quant_research_agent/.venv/bin/python -m research.alpha_assessment.build_alpha_assessment --rebuild-canonical --allow-synthetic
```

## Output Files

- `outputs/alpha_assessment/canonical_performance.csv`
- `outputs/alpha_assessment/canonical_performance.json`
- `outputs/alpha_assessment/real_data_integration_report.md`
- `outputs/alpha_assessment/canonical_field_coverage.csv`
- `outputs/overlay_engine/overlay_backtest.csv`

## Interpreting Null-Heavy Outputs

Check `outputs/alpha_assessment/real_data_integration_report.md` first.

- `notes_source_flags=ok`: row has all required/optional fields present.
- `missing_required=...`: required canonical fields are missing for that row.
- `missing_optional=...`: optional fields are missing for that row.

Use `canonical_field_coverage.csv` to identify low fill-rate columns and their source contract.

## If Benchmark/Analyzer Inputs Are Missing

If benchmark close is missing:

1. Add `outputs/perf/benchmark_close_history.csv` with `date,spy_close[,spy_return]`.
2. Rebuild canonical outputs.
3. Confirm `spy_close` fill rate improved in `canonical_field_coverage.csv`.

If analyzer score is missing:

1. Add `outputs/perf/premarket_analyzer_scores.csv` with `date,premarket_score`.
2. Rebuild canonical outputs.
3. Confirm `premarket_score` fill rate improved in `canonical_field_coverage.csv`.

Automation notes:

- `daily_quant_report.py` refreshes both producer artifacts each run.
- Alpha assessment builder refreshes both producer artifacts before canonical assembly.
- Analyzer payload is generated in `daily_quant_report.py` from active risk controls (`vix_regime`, breaker exposure, `risk_off`) and persisted into signal/execution artifacts.
- Historical backfill is only applied where reproducible data exists in archived snapshots; unavailable historical components remain null.

## Known Limitations

- Canonical quality depends on upstream producer completeness.
- Benchmark close and analyzer score remain null when upstream contracts are not populated.
- Overlay backtest assumes one-day lag unless `--no-lag` is explicitly used for diagnostics.
