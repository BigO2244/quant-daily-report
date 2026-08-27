from __future__ import annotations

import pandas as pd

from core.orion_decision_lineage import build_decision_lineage, canonical_hash
from research.alpha_lab_v1.signals import build_alpha_lab_signal_frame
from research.alpha_lab_v2.engine import StrategySpec, build_target_snapshot
from research.flow_detection.data import ensure_price_panel, validate_symbol_coverage
from paper.trading_calendar import is_trading_day


def _panel(*, periods: int = 260) -> pd.DataFrame:
    dates = [
        date for date in pd.date_range("2024-12-01", "2026-12-31", freq="D")
        if is_trading_day(str(date.date()))
    ][:periods]
    dates = pd.DatetimeIndex(dates)
    rows = []
    for ticker, slope in (("AAA", 0.003), ("BBB", 0.002), ("CCC", 0.001), ("SPY", 0.0015)):
        for index, date in enumerate(dates):
            close = 100.0 * (1.0 + slope) ** index
            rows.append(
                {
                    "date": date,
                    "ticker": ticker,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1_000_000,
                }
            )
    return pd.DataFrame(rows)


def _spec() -> StrategySpec:
    return StrategySpec(
        name="Orion",
        hypothesis_id="H2_H6",
        description="test",
        top_n=2,
        use_rank_decay_exit=True,
        exit_rank_multiple=2.0,
    )


def _lineage(panel: pd.DataFrame) -> tuple[dict, dict]:
    signals = build_alpha_lab_signal_frame(panel)
    trade_date = str(panel["date"].max().date())
    snapshot = build_target_snapshot(signals, _spec(), trade_date=trade_date)
    coverage = validate_symbol_coverage(
        panel,
        symbols=["AAA", "BBB", "CCC", "SPY"],
        current_session=trade_date,
        required_history_offsets=[1, 3, 21, 126, 252],
    )
    lineage = build_decision_lineage(
        panel=panel,
        signals=signals,
        snapshot=snapshot,
        model_version="orion_test_v1",
        source_variant="orion_test_v1",
        generated_at_utc="2026-08-27T22:00:00Z",
        coverage=coverage,
    )
    return lineage, snapshot


def test_global_max_date_cannot_mask_partial_symbol_or_missing_feature_anchor() -> None:
    panel = _panel()
    current = panel["date"].max()
    prior_252 = sorted(panel.loc[panel["ticker"] == "SPY", "date"].unique())[-253]
    partial = panel[~((panel["ticker"] == "BBB") & (panel["date"] == current))].copy()
    partial = partial[~((partial["ticker"] == "CCC") & (partial["date"] == prior_252))].copy()

    coverage = validate_symbol_coverage(
        partial,
        symbols=["AAA", "BBB", "CCC", "SPY"],
        current_session=str(current.date()),
        required_history_offsets=[1, 3, 21, 126, 252],
    )

    assert partial["date"].max() == current
    assert coverage["status"] == "INCOMPLETE"
    assert coverage["missing_current_session_symbols"] == ["BBB"]
    assert coverage["missing_required_anchor_symbols"][str(pd.Timestamp(prior_252).date())] == ["CCC"]


def test_successful_empty_batch_retries_omitted_symbol_individually(tmp_path, monkeypatch) -> None:
    date = pd.Timestamp("2026-08-26")
    calls: list[list[str]] = []

    def fake_download(*, symbols, start_date, end_date, chunk_size=25, pause_seconds=0.0):
        del start_date, end_date, chunk_size, pause_seconds
        symbols = list(symbols)
        calls.append(symbols)
        returned = ["AAA"] if len(symbols) > 1 else symbols
        if returned == ["BBB"]:
            returned = ["BBB"]
        return pd.DataFrame(
            [
                {"date": date, "ticker": ticker, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
                for ticker in returned
            ]
        )

    monkeypatch.setattr("research.flow_detection.data.download_price_panel", fake_download)
    panel, meta = ensure_price_panel(
        symbols=["AAA", "BBB"],
        start_date="2026-08-26",
        end_date="2026-08-26",
        cache_path=tmp_path / "panel.parquet",
        prefer_local=False,
        allow_download=True,
    )

    assert calls == [["AAA", "BBB"], ["BBB"]]
    assert set(panel["ticker"]) == {"AAA", "BBB"}
    assert meta["download_failed_symbols"] == []


def test_changed_panel_changes_feature_and_rank_lineage_but_can_keep_target() -> None:
    panel = _panel()
    first, first_snapshot = _lineage(panel)
    changed = panel.copy()
    mask = (changed["ticker"] == "AAA") & (changed["date"] == changed["date"].max())
    changed.loc[mask, "close"] *= 1.001
    second, second_snapshot = _lineage(changed)

    assert first["normalized_panel_hash"] != second["normalized_panel_hash"]
    assert first["feature_hash"] != second["feature_hash"]
    assert first["rank_table_hash"] != second["rank_table_hash"]
    assert first["target_weights_hash"] == second["target_weights_hash"]
    assert list(first_snapshot["weights"].index) == list(second_snapshot["weights"].index)
    assert first["market_data_hash"] != canonical_hash([])
    assert first["trade_date"] == first["effective_trade_date"]
    assert first["parent_artifact_hashes"]["features"] == first["normalized_panel_hash"]
    assert first["parent_artifact_hashes"]["target_weights"] == first["rank_table_hash"]
    diagnostics = first["stage_diagnostics"]
    assert set(diagnostics) == {
        "market_data", "normalized_panel", "features", "full_rank_history",
        "current_rank_table", "target_weights",
    }
    assert diagnostics["normalized_panel"]["row_count"] == len(panel)
    assert diagnostics["normalized_panel"]["symbol_count"] == 4
    assert diagnostics["features"]["max_market_timestamp"] == first["trade_date"]
    assert diagnostics["target_weights"]["source_identity"] == (
        "research.alpha_lab_v2.engine.build_target_snapshot"
    )


def test_rank_decay_selection_trace_records_keep_exit_and_fill() -> None:
    dates = pd.to_datetime(["2026-08-25", "2026-08-26"])
    rows = []
    ranks = {
        dates[0]: [("AAA", 3.0, 1), ("BBB", 2.0, 2), ("CCC", 1.0, 3)],
        dates[1]: [("AAA", 3.0, 1), ("CCC", 2.0, 2), ("BBB", 1.0, 3)],
    }
    for date, values in ranks.items():
        for ticker, score, rank in values:
            rows.append(
                {"date": date, "ticker": ticker, "close": 100.0, "signal_ready": True,
                 "momentum_score": score, "momentum_rank": rank}
            )
    spec = StrategySpec(
        name="Orion", hypothesis_id="H2", description="trace", top_n=2,
        use_rank_decay_exit=True, exit_rank_multiple=1.0,
    )
    snapshot = build_target_snapshot(pd.DataFrame(rows), spec, trade_date="2026-08-26")
    actions = {(row["ticker"], row["action"]) for row in snapshot["selection_trace"]}

    assert ("AAA", "KEEP") in actions
    assert ("BBB", "EXIT") in actions
    assert ("CCC", "FILL") in actions
