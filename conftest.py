import pytest
import sys


collect_ignore_glob = ["Tests/* 2.py"]


@pytest.fixture(autouse=True)
def _clear_runtime_run_output_root(monkeypatch):
    for name in ("RUN_OUTPUT_ROOT", "MODE", "TRADING_MODE", "PLAN_ONLY"):
        monkeypatch.delenv(name, raising=False)
    dqr = sys.modules.get("daily_quant_report")
    if dqr is not None:
        dqr._RUN_CONTEXT = None
        dqr._RUN_CONTEXT_FINALIZED = False
