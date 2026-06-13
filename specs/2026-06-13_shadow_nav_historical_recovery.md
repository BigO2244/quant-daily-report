MODE: HARDEN
PROJECT_TYPE: quant-research
RISK_TIER: high
OBJECTIVE: Validate point-in-time Shadow daily returns, stage deterministic NAV recovery from the 2026-06-05 anchor, and replace active VM artifacts only if continuity, lineage, and downstream health gates pass.

# 2026-06-13 Shadow NAV Historical Recovery

## FILES

- `outputs/shadow_candidates/performance/shadow_nav_series.csv`
- `outputs/shadow_candidates/performance/shadow_summary.json`
- `outputs/shadow_candidates/latest/`
- `outputs/shadow_candidates/2026-06-05/`
- `outputs/shadow_candidates/2026-06-08/`
- `outputs/shadow_candidates/2026-06-09/`
- `outputs/shadow_candidates/2026-06-10/`
- `outputs/shadow_candidates/2026-06-11/`
- `outputs/shadow_candidates/2026-06-12/`
- `outputs/price_hydration/2026-06-05/`
- `outputs/price_hydration/2026-06-08/`
- `outputs/price_hydration/2026-06-09/`
- `outputs/price_hydration/2026-06-10/`
- `outputs/price_hydration/2026-06-11/`
- `outputs/price_hydration/2026-06-12/`
- `research/shadow_tracking/run.py`
- `scripts/refresh_shadow_scorecard_artifacts.py`
- `scripts/send_shadow_cio_report.py`
- `scripts/check_shadow_scorecard_health.py`
- `config/research/strategy_registry.json`
- `core/strategy_registry.py`
- `reports/agent_loops/2026-06-13_shadow_nav_historical_recovery/`
- `reports/incidents/2026-06-12_shadow_nav_scorecard_corruption.md`

## ACCEPTANCE_CRITERIA

- VM incident evidence backup exists and its manifest and original artifact hashes match the preserved values.
- The return convention is proven from code and valid pre-incident artifacts before any recovery.
- Daily returns for every repaired trading date are independently reconstructed from dated weights or holdings and point-in-time prices.
- The 2026-06-05 anchor is validated before recompounding any later NAV row.
- No recovery uses future prices, future holdings, interpolation, changed strategy definitions, or a substitute backtest methodology.
- Staged recovery preserves all pre-incident rows unchanged and recompounds only validated affected rows.
- Staged artifacts pass Shadow continuity, health, scorecard, registry, MCP, promotion-readiness, and behavioral differentiation checks.
- Active VM artifacts are replaced only after staging and independent review pass.
- Original corrupt artifacts, backups, hashes, and recovery manifests remain preserved.
- Reports are written under `reports/agent_loops/2026-06-13_shadow_nav_historical_recovery/`.
- No broker, trading, execution, allocation, model, strategy, promotion, retirement, cron, live-capital, or secret behavior changes.
