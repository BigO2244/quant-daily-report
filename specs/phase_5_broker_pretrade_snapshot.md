Phase 5: Artifact / Dashboard / Email Harness Updates

  Objective: Surface the new artifacts in the dashboard, morning report, and operator summary.

  Changes:
  - scripts/research/build_quant_dashboard.py: consume pretrade/posttrade snapshots, surface delta, display broker trust level
  - core/operator_summary.py: add broker_pretrade_snapshot_ok, broker_posttrade_snapshot_ok
  - core/trading_day_summary.py: include pretrade/posttrade context
  - web/dashboard/: update dashboard JS/HTML to show broker-authoritative fields
  - GitHub Actions: ensure all new broker/ artifacts are uploaded

  Acceptance Criteria:
  - Dashboard correctly shows "today's run used broker-authoritative state"
  - Morning report includes pre/post-trade position counts and cash
  - Operator summary includes broker snapshot status

  Rollback: Dashboard and email changes are display-only. Safe to roll back independently.
  