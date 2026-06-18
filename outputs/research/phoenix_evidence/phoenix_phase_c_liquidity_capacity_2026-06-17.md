# Phoenix Phase C Liquidity & Capacity Validation

Date: `2026-06-17`

RESEARCH_ONLY / NO_RUNTIME_CHANGE

Output: `NOT_VIABLE`
Shadow ready: `False`
Reason codes: `['capacity_below_5pct_adv_policy']`

## Source Inventory

- Observed columns: `['ADV_20', 'ADV_60', 'close', 'closeadj', 'date', 'dollar_ADV_20', 'dollar_ADV_60', 'dollar_volume', 'high', 'low', 'open', 'ticker', 'volume']`
- Missing liquidity fields: `[]`
- Panel rows: `7845012`

## Measurements

- Candidate rows measured: `80` / `80`
- Measurement coverage: `1.0`
- Max dollar ADV participation: `0.6705942337`
- Minimum capacity at 5% ADV: `74560.7365625001`
- Minimum capacity at 10% ADV: `149121.4731250002`
- Max implementation shortfall proxy bps: `50.9449091375`

## Interpretation

Measured PIT liquidity indicates at least one selected Phoenix position cannot support the reference capital at 5% ADV.
