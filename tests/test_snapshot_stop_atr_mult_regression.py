import daily_quant_report as dqr

def test_snapshot_risk_value_falls_back_to_default_on_missing_and_invalid(monkeypatch):
    monkeypatch.delenv("STOP_ATR_MULT", raising=False)
    assert dqr._snapshot_risk_value("STOP_ATR_MULT", 2.0) == 2.0
    monkeypatch.setenv("STOP_ATR_MULT", "bad")
    assert dqr._snapshot_risk_value("STOP_ATR_MULT", 2.0) == 2.0
    monkeypatch.setenv("STOP_ATR_MULT", "2.75")
    assert dqr._snapshot_risk_value("STOP_ATR_MULT", 2.0) == 2.75
