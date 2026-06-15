# Broker State Audit

Roles: Broker adapter / order-state auditor

## Findings

- Alpaca adapter exposes `get_order` by stable broker order id.
- Submit path stores `alpaca_order_id` and immediately refreshes lifecycle state.
- Sell polling calls `get_order` for each submitted sell; it does not rely only on `list_orders(status="open")`.
- `FILLED` is already in the terminal status set and `_order_filled_quantity` treats filled orders as positive-fill terminal states.

## Defect Surface

The defect is not primarily an open-orders-only bug in current code. The high-confidence defect is a sell observation boundary problem:

- Primary sell timeout was 90 seconds.
- Incident final fill occurred at about 190 seconds.
- There was no bounded recovery refresh window before the lifecycle could make buy suppression/reporting decisions.

## Patch Direction

Add bounded post-timeout recovery refresh using stable broker order ids and keep unresolved sell states from advancing to buy submission.

