import pandas as pd

from daily_quant_report import create_snapshot_email
from paper.alpha import compute_alpha_attribution


def test_alpha_attribution_ok_with_overlapping_data_and_cumulative_math():
    dates = pd.date_range("2026-01-01", periods=8, freq="D")
    port_eq = pd.Series([100, 101, 102, 103, 102, 104, 106, 107], index=dates)
    spy_px = pd.Series([500, 502, 503, 505, 504, 506, 507, 508], index=dates)

    result = compute_alpha_attribution(port_eq, spy_px, min_overlap_days=5, last_n=10)

    assert result["ok"] is True
    assert result["overlap_days"] == 7
    assert result["overlap_start"] == "2026-01-02"
    assert result["overlap_end"] == "2026-01-08"

    port_cum = result["summary"]["cumulative_port_return"]
    spy_cum = result["summary"]["cumulative_spy_return"]
    alpha_cum = result["summary"]["cumulative_alpha"]
    assert abs(alpha_cum - (port_cum - spy_cum)) < 1e-12
    assert len(result["rows"]) == 7


def test_alpha_attribution_insufficient_overlap_returns_explicit_reason():
    port_dates = pd.date_range("2026-01-01", periods=4, freq="D")
    spy_dates = pd.date_range("2026-01-03", periods=4, freq="D")

    port_eq = pd.Series([100, 101, 102, 103], index=port_dates)
    spy_px = pd.Series([500, 501, 502, 503], index=spy_dates)

    result = compute_alpha_attribution(port_eq, spy_px, min_overlap_days=5)

    assert result["ok"] is False
    assert result["reason"] == "Need >=5 overlapping days; have 1 (2026-01-04 → 2026-01-04)"


def test_snapshot_email_uses_explicit_alpha_reason_when_unavailable():
    snapshot = {
        "asof": "2026-02-11",
        "allocations": {"cash": 0.2, "sleeves": {"sleeve_trend": 0.4, "sleeve_2": 0.3, "charlie_munger": 0.1}},
        "performance_summary": {"wtd": 0.0, "mtd": 0.0, "total_return": 0.0},
        "performance_diagnostics": {"current_equity": 10000, "day_return": 0.0},
        "alpha_attribution": {
            "ok": False,
            "reason": "Need >=5 overlapping days; have 1 (2026-02-09 → 2026-02-09)",
        },
        "charlie_munger": {"meta": {"near_ma_candidates": 0}, "selected": []},
    }

    _, body = create_snapshot_email(snapshot, execution_payload={"trades": []})

    assert "ALPHA ATTRIBUTION VS SPY" in body
    assert "• Status: Pending — Need >=5 overlapping days; have 1 (2026-02-09 → 2026-02-09)" in body
