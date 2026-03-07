# Alpha Assessment

## Canonical Performance Layer v1

Build command:

```bash
python -m research.alpha_assessment.build_alpha_assessment --rebuild-canonical
```

Primary output artifacts:

- `outputs/alpha_assessment/canonical_performance.csv`
- `outputs/alpha_assessment/canonical_performance.json`
- `outputs/alpha_assessment/real_data_integration_report.md`
- `outputs/alpha_assessment/canonical_field_coverage.csv`

Schema columns:

- `date`
- `strategy_nav`
- `strategy_return`
- `spy_close`
- `spy_return`
- `excess_return`
- `vix_close`
- `vix_regime`
- `gross_exposure`
- `net_exposure`
- `cash_weight`
- `turnover`
- `holdings_count`
- `realized_pnl`
- `unrealized_pnl`
- `premarket_score`
- `overlay_signal`
- `active_overlay`
- `notes_source_flags`

### Authoritative Source Priority

1. Strategy NAV/returns/exposure/turnover: `outputs/perf/nav_timeseries.csv`
2. Benchmark alignment: latest `outputs/perf/inception_nav_*.csv`
3. Benchmark close history (preferred): `outputs/perf/benchmark_close_history.csv`
4. Benchmark close fallback: `outputs/perf/holdings_mtm_*.csv` (`ticker=SPY`, `mtm_price`)
5. VIX and regime: `outputs/vix_regime/regime_history.csv`
6. Holdings and PnL: `outputs/perf/holdings_mtm_*.csv`
7. Fills/executions and fallback turnover: `outputs/ledger/trades.csv`
8. Premarket analyzer (preferred): `outputs/perf/premarket_analyzer_scores.csv`
9. Overlay and analyzer fallback surface: `signals/*.json`

Producer automation paths:

- `daily_quant_report.py` refreshes `benchmark_close_history.csv`, `vix_close_history.csv`, and `premarket_analyzer_scores.csv` each run.
- `python -m research.alpha_assessment.build_alpha_assessment --rebuild-canonical` also refreshes all three producer artifacts before canonical assembly.
- Manual producer rebuild: `python -m paper.perf_artifact_producers --asof-date YYYY-MM-DD` (generates benchmark + VIX + analyzer artifacts)

Analyzer generation source:

- Structured `market_analyzer` payload is generated in `daily_quant_report.py` via `_build_market_analyzer_payload(...)`.
- Payload is persisted into `signals/YYYY-MM-DD.json` and propagated into execution payload artifacts.
- `paper/perf_artifact_producers.py` ingests these payloads into `outputs/perf/premarket_analyzer_scores.csv`.

### Required vs Optional Upstream Artifacts

Required for real-data mode:

- `outputs/perf/nav_timeseries.csv`

Optional but strongly recommended:

- `outputs/perf/inception_nav_*.csv`
- `outputs/perf/benchmark_close_history.csv`
- `outputs/perf/premarket_analyzer_scores.csv`
- `outputs/vix_regime/regime_history.csv`
- `outputs/perf/holdings_mtm_*.csv`
- `outputs/ledger/trades.csv`
- `signals/*.json`

Expected optional contract formats:

- Benchmark close history: `date,spy_close[,spy_return]`
- Premarket analyzer: `date,premarket_score`

Current analyzer status:

- Stable contract fields: `date,premarket_score,bearish_flag,signal_bucket,analyzer_version,notes`.
- Component fields are persisted when available: `vix_component,trend_component,realized_vol_component,gap_risk_component,breadth_component,macro_component`.
- Historical backfill is limited to fields recoverable from existing snapshots/execution artifacts; missing historical components remain null.

### Ambiguities and Degradation

- `spy_close`: sourced from `benchmark_close_history.csv` when available, otherwise fallback from `holdings_mtm` SPY marks; still null when both are missing.
- `premarket_score`: sourced from `premarket_analyzer_scores.csv` first, then fallback to `signals/*.json` `market_analyzer.*` keys.
- Missing fields are represented as null and flagged in `notes_source_flags` via `missing_required=` and `missing_optional=` sections.
- `real_data_integration_report.md` now includes a field-level data quality coverage table.

### Synthetic Mode

Synthetic fallback is disabled by default. Use `--allow-synthetic` to enable explicit fallback.

### Coverage Improvement Tips

- Add/refresh `outputs/perf/benchmark_close_history.csv` to improve `spy_close` and `spy_return` continuity.
- Add/refresh `outputs/perf/premarket_analyzer_scores.csv` to improve `premarket_score` continuity.
- Ensure `outputs/perf/nav_timeseries.csv` is produced daily to avoid partial `strategy_nav` gaps.

### Future Extension Points

- Factor data adapters can attach new columns without changing canonical source precedence.
- Analyzer expansion can add richer diagnostics while preserving `premarket_score` compatibility.
- Real hedge instrument data can be introduced by extending benchmark/overlay source tables.
