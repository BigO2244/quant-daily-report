#!/usr/bin/env bash
# One-shot deploy: commit all changes, push to GitHub, pull on GCP VM.
# Run from repo root on your local machine:
#   bash scripts/deploy_20260411.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

VM_HOST="brettolson@34.61.147.38"
VM_REPO="/home/brettolson/quant-daily-report"

echo "=== Step 1: Stage all changes ==="
# New files (critical for six-leak fix)
git add \
  core/ic_throttle.py \
  Tests/__init__.py \
  Tests/test_ic_throttle.py \
  Tests/test_allocator_cash_drag_redistribution.py \
  outputs/research/backtest_vs_live_diagnosis.md \
  outputs/research/backtest_vs_live_fix_onepager.md \
  outputs/research/last_24h_onepager.md \
  outputs/research/sleeve1_cadence_sweep/

# New files (supporting work — options, dashboard, defensive ETF, etc.)
git add \
  config/ \
  deploy/ \
  core/live_regime_review.py \
  core/options_execution.py \
  core/options_overlay_paper.py \
  core/options_overlay_shadow.py \
  core/options_smoke_session.py \
  docs/dashboard_refresh_spec.md \
  docs/dashboard_v2_spec.md \
  reports/agents/ \
  scripts/analyze_trade_day_pnl.py \
  scripts/build_nightly_findings.py \
  scripts/build_quant_dashboard.py \
  scripts/deploy_dashboard_vm.sh \
  scripts/execute_options_overlay.py \
  scripts/options_smoke_session.py \
  scripts/refresh_quant_dashboard.py \
  scripts/research/build_engine_evaluation.py \
  scripts/research/build_ma_vol_hypothesis_test.py \
  scripts/research/research_backtest_sleeve1_cadence_sweep.py \
  scripts/update_agents_md.py \
  sleeves/sleeve_defensive_etf/ \
  weekly_quant_research/ \
  .github/workflows/nightly-agents-refresh.yml

# New test files
git add \
  Tests/test_analyze_trade_day_pnl.py \
  Tests/test_build_engine_evaluation.py \
  Tests/test_build_ma_vol_hypothesis_test.py \
  Tests/test_build_nightly_findings.py \
  Tests/test_defensive_etf_sleeve.py \
  Tests/test_ic_monitor_alerts.py \
  Tests/test_ic_monitor_backfill.py \
  Tests/test_ic_monitor_per_sleeve.py \
  Tests/test_live_regime_allocation.py \
  Tests/test_live_regime_review.py \
  Tests/test_options_execution.py \
  Tests/test_options_overlay_paper.py \
  Tests/test_options_overlay_shadow.py \
  Tests/test_options_smoke_session.py \
  Tests/test_signal_snapshot_atomic_write.py \
  Tests/test_sleeve1_selection.py \
  Tests/test_update_agents_md.py

# Modified files
git add \
  alpha_stack/config/alpha_stack.yaml \
  backtests/sleeve1_robustness.py \
  core/portfolio_alloc.py \
  daily_quant_report.py \
  paper/config_paper.json \
  paper/paper_broker.py \
  paper/signals_io.py \
  paper/build_execution_email.py \
  paper/perf_artifact_producers.py \
  brokers/alpaca_broker.py \
  core/operator_summary.py \
  core/trading_day_summary.py \
  daily_trade_execution_email.py \
  regime/regime_config.py \
  research/ic_monitor.py \
  sleeves/sleeve_1/indicators.py \
  sleeves/sleeve_1/selection.py \
  sleeves/sleeve_trend/selection.py \
  scripts/alpaca_smoke_test.py \
  scripts/cron_confirm.sh \
  scripts/cron_execute.sh \
  scripts/export_alpaca_broker_snapshot.py \
  scripts/research/build_quant_dashboard.py \
  scripts/send_trading_confirmation_email.py \
  .github/workflows/export-broker-snapshot.yml \
  .github/workflows/research-digest.yml \
  AGENTS.md \
  docs/MODEL_CHANGES.md \
  docs/alpha_stack/regime_allocator_spec.md \
  docs/alpha_stack/sleeve_specifications.md \
  docs/model_strategy.md \
  docs/quant_dashboard.md \
  web/dashboard/dashboard_data.json \
  web/dashboard/quant_daily_executive.css \
  web/dashboard/quant_daily_executive.js

# Modified tests
git add \
  Tests/test_alpha_attribution_reporting.py \
  Tests/test_build_quant_dashboard.py \
  Tests/test_daily_trade_execution_email.py \
  Tests/test_dashboard_ui_status.py \
  Tests/test_execution_pipeline_integration.py \
  Tests/test_export_alpaca_broker_snapshot.py \
  Tests/test_perf_artifact_producers.py \
  Tests/test_selection.py

# Intentionally skip: .claude/ (local tooling), CLAUDE.md deletion
echo "Staged. Checking status..."
git status --short | head -20

echo ""
echo "=== Step 2: Commit ==="
git commit -m "$(cat <<'EOF'
fix(paper): close 6 backtest-vs-live leaks + supporting work

Six leak fixes (see outputs/research/last_24h_onepager.md):
1. 1% drift deadband on rebalance trades
2. Shadow-vs-live attribution: persisted ticker-sleeve map
3. Signal snapshot: always propagate cash_target_weight
4. Cost model unified at 25 bps one-way everywhere
5. IC-gated auto-throttle for quality sleeve (core/ic_throttle.py)
6. Allocator cash drag: redistribute uncappable sleeve budget

Also includes: options overlay scaffolding, defensive ETF sleeve,
dashboard refresh, IC monitor enhancements, nightly findings,
engine evaluation scripts, and supporting test coverage.

12 new tests for leak fixes, all passing.
4 pre-existing test failures in phase3/paper_reset are known and
will be addressed in a separate test-hygiene follow-up.
EOF
)"

echo ""
echo "=== Step 3: Push to GitHub ==="
git push origin main

echo ""
echo "=== Step 4: Pull on GCP VM ==="
ssh "${VM_HOST}" "cd ${VM_REPO} && git pull origin main"

echo ""
echo "=== Step 5: Verify critical files on VM ==="
ssh "${VM_HOST}" bash -c "'
  cd ${VM_REPO}
  echo \"--- Deadband (paper_broker.py) ---\"
  grep -n \"rebalance_deadband_pct\" paper/paper_broker.py | head -3

  echo \"\"
  echo \"--- Cost model (config_paper.json) ---\"
  python3 -c \"import json; c=json.load(open('paper/config_paper.json')); print('slippage_bps:', c.get('constraints',{}).get('slippage_bps', 'MISSING'))\"

  echo \"\"
  echo \"--- IC throttle exists ---\"
  ls -la core/ic_throttle.py

  echo \"\"
  echo \"--- Allocator redistribution ---\"
  grep -c \"_redistribute_uncappable_sleeve_budget\" core/portfolio_alloc.py

  echo \"\"
  echo \"--- Tests/__init__.py exists ---\"
  ls -la Tests/__init__.py

  echo \"\"
  echo \"--- Quick test run ---\"
  cd ${VM_REPO}
  python3 -m pytest Tests/test_ic_throttle.py Tests/test_allocator_cash_drag_redistribution.py -v --tb=short 2>&1 | tail -20
'"

echo ""
echo "=== DONE ==="
echo "All 6 fixes are committed, pushed, and deployed to VM."
echo "Cron will pick them up at 7:00 AM ET tomorrow."
echo ""
echo "Post-deploy checklist:"
echo "  [ ] Verify crontab is still active:  ssh ${VM_HOST} 'crontab -l'"
echo "  [ ] Check .env is intact:            ssh ${VM_HOST} 'ls -la ${VM_REPO}/.env'"
echo "  [ ] Monitor 7:00 AM precompute log:  ssh ${VM_HOST} 'tail -f ${VM_REPO}/logs/precompute_\$(date +%F).log'"
