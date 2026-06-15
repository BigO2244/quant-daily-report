# Final Validation

## Commands

- `python3 -m aiops run-all --spec specs/2026-06-15_fr070_hotfix_execution_fill_observation.md --mode HARDEN`
  - Failed at AIOPS dispatch after plan creation; recorded in `reports/ai_runs/20260615_121537_eccf91a/`.
- `.venv/bin/python -m pytest Tests/test_execution_asset_cash_gating.py -q`
  - Passed: 11 tests.
- `git diff --check`
  - Passed.
- `.venv/bin/python -m py_compile paper/paper_broker.py Tests/test_execution_asset_cash_gating.py`
  - Passed.
- `.venv/bin/python -m pytest Tests/test_execution_asset_cash_gating.py Tests/test_confirmation_email_reconciled.py Tests/test_target_attainment.py -q`
  - Passed: 20 tests.
- `.venv/bin/python -m pytest Tests/test_execution_integrity.py Tests/test_execution_lifecycle_timeline.py Tests/test_recon_posttrade_refresh.py Tests/test_run_precomputed_alpaca_execution.py -q`
  - Passed: 23 tests.

## Not Yet Run

- VM validation/deployment
- Full repository test suite
