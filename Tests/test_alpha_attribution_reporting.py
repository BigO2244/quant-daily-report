import pandas as pd

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
    assert result["reason"] == "Need >=5 overlapping days; have 1."
