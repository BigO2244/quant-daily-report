# Phoenix Phase C Liquidity & Capacity Validation

Date: `2026-06-17`

RESEARCH_ONLY / NO_RUNTIME_CHANGE

Output: `PENDING_LIQUIDITY`
Shadow ready: `False`
Reason codes: `['pit_volume_source_missing', 'capacity_not_decision_grade']`

## Source Inventory

- Observed columns: `['close', 'closeadj', 'date']`
- Missing liquidity fields: `['volume', 'dollar_volume', 'adv_20d', 'adv_60d']`
- Sampled files: `500`

## Interpretation

Phase B found risk-shaped Phoenix candidates, but ADV participation, capacity, crisis liquidity degradation, slippage, and implementation shortfall cannot be measured from the repo-local close-only PIT cache.
