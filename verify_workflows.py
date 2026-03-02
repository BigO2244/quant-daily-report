#!/usr/bin/env python3
"""Quick verification that workflows are in place."""

from pathlib import Path

spec = Path("specs/daily_alpaca_paper_run_0935_et.md")
premarket = Path(".github/workflows/daily_quant_report_premarket.yml")
execution = Path(".github/workflows/alpaca_paper_execute_open.yml")

print("=== Workflow Files Status ===")
print(f"✓ Premarket workflow exists: {premarket.exists()}")
print(f"✓ Execution workflow exists: {execution.exists()}")

# Verify YAML syntax
import yaml
try:
    yaml.safe_load(premarket.read_text())
    print(f"✓ Premarket YAML is valid")
except Exception as e:
    print(f"✗ Premarket YAML invalid: {e}")

try:
    yaml.safe_load(execution.read_text())
    print(f"✓ Execution YAML is valid")
except Exception as e:
    print(f"✗ Execution YAML invalid: {e}")

# Verify files are in spec
spec_content = spec.read_text()
has_premarket_in_spec = "daily_quant_report_premarket.yml" in spec_content
has_execution_in_spec = "alpaca_paper_execute_open.yml" in spec_content

print(f"\n=== FILES Section Verification ===")
print(f"✓ Premarket listed in spec: {has_premarket_in_spec}")
print(f"✓ Execution listed in spec: {has_execution_in_spec}")

if all([premarket.exists(), execution.exists(), has_premarket_in_spec, has_execution_in_spec]):
    print(f"\n✓✓✓ All workflow files are in place ✓✓✓")
    print(f"\nWorkflows are ready for:")
    print(f"  - Daily pre-market report generation (08:00 ET / 13:00 UTC)")
    print(f"  - Alpaca paper execution (09:35 ET / 14:35 UTC)")
else:
    print(f"\n✗ Some files are missing")
