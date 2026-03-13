Phase 4: Post-Trade Broker-Refresh Canonicalization

  Objective: Write canonical_positions.json exclusively from Alpaca post-trade data. Remove any path that writes canonical positions from internal ledger computations.

  Changes:
  - reconciliation.py / daily_quant_report.py: ensure canonical_positions.json write always uses posttrade_positions from Alpaca
  - Remove (or clearly gate) any code path that writes canonical from the execution payload or the ledger
  - Write broker/posttrade_positions.json and broker/posttrade_account_snapshot.json
  - Write broker/recon_posttrade.json comparing target vs actual

  Acceptance Criteria:
  - canonical_positions.json always matches posttrade_positions.json (they are written from the same source)
  - recon_posttrade.json produced on every execution run
  - No code path writes canonical from execution_payload

  Rollback: Re-enable old canonical write path via env var. The post-trade Alpaca query is additive.

  Test Strategy: Assert canonical positions == posttrade positions in integration test; unit test posttrade recon comparison.
