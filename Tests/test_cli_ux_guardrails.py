from pathlib import Path
import importlib.util

import pandas as pd
import pytest

import daily_quant_report as dqr


def test_report_date_placeholder_raises_clear_error(monkeypatch):
    monkeypatch.setenv("REPORT_DATE", "YYYY-MM-DD")
    with pytest.raises(ValueError) as exc:
        dqr._infer_report_date(
            sleeve_details=[],
            fallback=pd.Timestamp("2026-02-24"),
        )
    msg = str(exc.value)
    assert "REPORT_DATE" in msg
    assert "YYYY-MM-DD" in msg
    assert "2026-02-24" in msg


def test_alpaca_smoke_shim_exists():
    shim_path = Path("alpaca_smoke_test.py")
    assert shim_path.exists()

    spec = importlib.util.spec_from_file_location("alpaca_smoke_shim", shim_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "main")
