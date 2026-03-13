  Phase 1: Broker-Authoritative Pre-Trade State Capture

  Objective: Capture authoritative Alpaca state at run start and write it as an immutable per-run artifact. Do not change execution logic yet.

  Changes:
  - daily_quant_report.py: add pre-trade broker query block before run_paper_day()
  - paper/paper_broker.py or new broker/alpaca_snapshot.py: implement fetch_pretrade_snapshot()
  - Write broker/pretrade_account_snapshot.json and broker/pretrade_positions.json
  - Update GitHub Actions artifact upload to include broker/ directory

  Acceptance Criteria:
  - Every run produces pretrade_account_snapshot.json and pretrade_positions.json
  - These files are present in run artifacts for both success and failure runs
  - No change to execution behavior

  Rollback: Delete the two write calls. Zero risk.

  Test Strategy: Integration test that verifies artifacts exist after a run; unit test the snapshot serialization.

  