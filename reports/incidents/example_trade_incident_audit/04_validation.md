# Validation

## Commands Run

```bash
python3 -m py_compile daily_quant_report.py scripts/execute_alpaca_orders.py core/execution_audit.py
pytest -q Tests/test_broker_reject_classification.py Tests/test_trading_day_summary.py Tests/test_execution_audit_safe_names.py
find reports/incidents/example_trade_incident_audit -maxdepth 1 -type f | sort
```

## Results

- compile validation passed
- targeted tests passed
- example incident folder contains all four numbered files

## Dry-Run Proof Goal

Use a preserved failed run artifact set and confirm:

- planner proposed nonzero trades
- pretrade passed
- broker submission attempted
- partial submission counts remain visible after abort
- downstream summaries no longer report a false zero-execution outcome

## Remaining Limits

Validation confirms reporting behavior and control-flow classification. It does not prove historical broker fills unless authoritative broker order history is also preserved.
