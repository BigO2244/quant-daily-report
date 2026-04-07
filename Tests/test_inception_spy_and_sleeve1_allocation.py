import pandas as pd

import daily_quant_report as dqr
from core.portfolio_alloc import create_sleeve_output


def test_small_account_live_resize_limits_sleeve_to_supported_name_band():
    base_positions = [
        {"ticker": t, "target_weight": 0.10, "reason": "signal", "signal_strength": 1.0}
        for t in ["AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "GOOGL", "AVGO", "JPM", "XOM"]
    ]
    sleeve = create_sleeve_output(base_positions, "sleeve_trend", strength=0.2368)
    ranked = [
        "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "GOOGL", "AVGO", "JPM", "XOM", "UNH", "HD"
    ]

    resized, diag = dqr._resize_sleeve_holdings_for_live_book(
        sleeve,
        target_allocation=0.2368,
        max_position_weight=0.20,
        min_position_weight=0.05,
        ranked_candidates=ranked,
    )

    assert diag["min_required_names"] == 2
    assert diag["max_supported_names"] == 4
    assert diag["selected_names"] == 4
    assert diag["trimmed_names"] == 6
    assert resized.positions_df["ticker"].tolist() == ["AAPL", "MSFT", "NVDA", "AMZN"]
    assert float(resized.positions_df["target_weight"].sum()) == 1.0


def test_benchmark_since_inception_stats_uses_portfolio_window_start():
    inception = pd.Timestamp("2026-01-05")
    end = pd.Timestamp("2026-02-19")
    spy = pd.Series(
        [500.0, 510.0, 520.0],
        index=pd.to_datetime(["2026-01-05", "2026-01-20", "2026-02-19"]),
    )

    stats = dqr._benchmark_since_inception_stats(spy)

    assert stats["start"] == inception.strftime("%Y-%m-%d")
    assert stats["end"] == end.strftime("%Y-%m-%d")
    assert stats["cumulative_return"] is not None


def test_snapshot_email_contains_inception_and_spy_metrics_in_paper_mode():
    snapshot = {
        "asof": "2026-02-19",
        "allocations": {"sleeves": {"sleeve_trend": 1.0}, "cash": 0.0},
        "target_cash_weight": 0.0,
        "performance_summary": {"wtd": 0.01, "mtd": 0.02, "total_return": 0.15},
        "performance_diagnostics": {"current_equity": 10000.0, "day_return": 0.001},
        "alpha_attribution": {"ok": False, "reason": "insufficient overlap"},
        "charlie_munger": {},
        "orders": [],
        "skipped_trades": [],
        "nav_metrics": {"equity": 10000.0, "return_1d": 0.001, "wtd": 0.01, "mtd": 0.02, "si": 0.15},
        "inception_metrics": {"inception_date": "2026-01-05", "spy_return_since_inception": 0.06},
        "allocation_diagnostics": {
            "sleeve_1": {
                "desired_allocation": 1.0,
                "achieved_invested": 0.95,
                "forced_cash": 0.05,
                "selected_names": 10,
                "min_required_names": 10,
                "limiting_constraint": "max_position_weight=10%",
            }
        },
    }

    _, body = dqr.create_snapshot_email(
        snapshot,
        execution_payload={"mode": "SHADOW", "trades": [], "executable_trades_count": 0},
    )

    assert "Inception:" in body
    assert "SPY Since Inception:" in body
    assert "n/a" not in body.split("SPY Since Inception:")[1].split("\n", 1)[0]
