hase 3: Sell-First / Buy-Second Sequencing Hardening

  Objective: Make the sell → confirm cash → buy sequence explicit and auditable. Add the post-sell cash refresh step.

  Changes:
  - paper/paper_broker.py: split execution into execute_sells() and execute_buys() with explicit cash confirmation step between
  - Add fetch_postsell_account_state() call between sell and buy phases
  - Write broker/postsell_account_snapshot.json
  - Enforce buy_budget from confirmed postsell cash, not estimated cash
  - execution_results.json: add postsell_cash_confirmed and buy_budget_computed fields

  Acceptance Criteria:
  - Execution logs explicitly separate sell phase from buy phase
  - postsell_account_snapshot.json present in every run that reached execution
  - Buy budget computed from confirmed post-sell cash
  - No regression in no_action and halted runs

  Rollback: Consolidate back to single execute call. The new functions can be composed into the old path.

  Test Strategy: Unit test sell→confirm→buy sequencing; test that buy budget is reduced when sell proceeds are below expected.

  