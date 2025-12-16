"""
Compatibility shim.

Legacy scripts used `from quant_report import ...` when quant_report.py lived at repo root.
We moved the legacy implementation to legacy/quant_report_legacy.py to remove multiple entrypoints.
This file preserves the import path while we refactor cleanly.
"""
from legacy.quant_report_legacy import *  # noqa: F401,F403
