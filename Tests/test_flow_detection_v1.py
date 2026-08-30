from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from research.flow_detection.analysis import attach_forward_returns
from research.flow_detection.backtest import FlowBacktestConfig, run_strategy_backtest
from research.flow_detection.data import ensure_price_panel
from research.flow_detection.random_windows import sample_randomized_windows
from research.flow_detection.run import _write_artifacts, build_summary
from research.flow_detection.signals import build_flow_signals


def _make_panel() -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=320, freq="B")
    rows = []
    for ticker, slope, flow_day in (("AAA", 0.004, 260), ("BBB", 0.002, 270), ("SPY", 0.0015, 9999)):
        price = 100.0
        for i, dt in enumerate(dates):
            price *= 1.0 + slope
            volume = 1_000_000 + (50_000 * (i % 5))
            if i == flow_day:
                volume = 5_000_000
                price *= 1.03
            rows.append(
                {
                    "date": dt,
                    "ticker": ticker,
                    "open": price * 0.99,
                    "high": price * 1.01,
                    "low": price * 0.98,
                    "close": price,
                    "volume": volume,
                    "sector": "Tech",
                }
            )
    return pd.DataFrame(rows)


def test_volume_z_and_flow_flagging() -> None:
    panel = _make_panel()
    signals = build_flow_signals(panel)
    row = signals[(signals["ticker"] == "AAA") & (signals["date"] == pd.Timestamp("2024-12-30"))].iloc[0]
    assert row["volume_z"] > 1.5
    assert bool(row["flow_active"]) is True


def test_no_lookahead_in_volume_window() -> None:
    panel = _make_panel()
    signals = build_flow_signals(panel)
    row = signals[(signals["ticker"] == "AAA") & (signals["date"] == pd.Timestamp("2024-12-30"))].iloc[0]
    hist = panel[(panel["ticker"] == "AAA") & (panel["date"] < pd.Timestamp("2024-12-30"))].sort_values("date").tail(20)
    expected_mean = hist["volume"].mean()
    assert abs(float(row["vol_mean_20"]) - float(expected_mean)) < 1e-9


def test_forward_return_alignment() -> None:
    panel = _make_panel()
    signals = attach_forward_returns(build_flow_signals(panel), horizons=(1, 3))
    row = signals[(signals["ticker"] == "AAA")].sort_values("date").iloc[250]
    ticker_rows = signals[signals["ticker"] == "AAA"].sort_values("date").reset_index(drop=True)
    idx = ticker_rows.index[ticker_rows["date"] == row["date"]][0]
    expected = ticker_rows.loc[idx + 1, "close"] / ticker_rows.loc[idx, "close"] - 1.0
    assert abs(float(row["fwd_1d"]) - float(expected)) < 1e-12


def test_random_window_sampler_validity() -> None:
    dates = pd.date_range("2010-01-01", periods=4000, freq="B")
    windows = sample_randomized_windows(dates, horizon_years=2, num_samples=10, seed=11)
    assert len(windows) == 10
    for sample in windows:
        assert sample.start_date < sample.end_date


def test_artifact_writing(tmp_path: Path) -> None:
    panel = _make_panel()
    signals = attach_forward_returns(build_flow_signals(panel))
    config = FlowBacktestConfig(top_n=2)
    baseline = run_strategy_backtest(signals, strategy="baseline", config=config)
    flow = run_strategy_backtest(signals, strategy="flow_filtered", config=config)
    summary = build_summary(
        panel_meta={"coverage": {"start_date": "2024-01-01", "end_date": "2025-03-31"}},
        signals=signals,
        baseline=baseline["summary"],
        flow=flow["summary"],
        event_study_summary=[],
        randomized_window_summary={"windows": []},
        use_efficiency_filter=False,
    )
    _write_artifacts(
        output_dir=tmp_path,
        summary=summary,
        signals=signals,
        event_study_rows=signals,
        event_study_summary=[],
        baseline=baseline,
        flow=flow,
        window_results=pd.DataFrame(),
        window_summary={"windows": []},
    )
    for name in (
        "summary.json",
        "signals.parquet",
        "event_study.csv",
        "backtest_baseline.json",
        "backtest_flow_filtered.json",
        "randomized_window_results.csv",
        "randomized_window_summary.json",
        "report.md",
    ):
        assert (tmp_path / name).exists(), name
    payload = json.loads((tmp_path / "summary.json").read_text())
    assert payload["schema_version"] == "flow_detection_v1"


def test_backtest_summary_includes_benchmark_comparison() -> None:
    panel = _make_panel()
    signals = attach_forward_returns(build_flow_signals(panel))
    config = FlowBacktestConfig(top_n=2)
    baseline = run_strategy_backtest(signals, strategy="baseline", config=config)
    assert "benchmark_cumulative_return" in baseline["summary"]
    assert "excess_return_vs_spy" in baseline["summary"]


def test_ensure_price_panel_filters_cache_to_requested_window(tmp_path: Path) -> None:
    panel = _make_panel()
    cache_path = tmp_path / "price_panel.parquet"
    panel.to_parquet(cache_path, index=False)

    filtered, meta = ensure_price_panel(
        symbols=["AAA", "SPY"],
        start_date="2024-06-03",
        end_date="2024-06-10",
        cache_path=cache_path,
        prefer_local=False,
        allow_download=False,
    )

    assert not filtered.empty
    assert set(filtered["ticker"].unique()) == {"AAA", "SPY"}
    assert filtered["date"].min() >= pd.Timestamp("2024-06-03")
    assert filtered["date"].max() <= pd.Timestamp("2024-06-10")
    assert meta["coverage"]["start_date"] == "2024-06-03"
    assert meta["coverage"]["end_date"] == "2024-06-10"


def test_ensure_price_panel_downloads_only_missing_tail_for_stale_cache(tmp_path: Path, monkeypatch) -> None:
    panel = _make_panel()
    stale = panel[panel["date"] <= pd.Timestamp("2024-06-28")].copy()
    cache_path = tmp_path / "price_panel.parquet"
    stale.to_parquet(cache_path, index=False)
    calls = []

    def fake_download_price_panel(*, symbols, start_date, end_date, chunk_size=25, pause_seconds=0.0):
        calls.append({"symbols": list(symbols), "start_date": start_date, "end_date": end_date})
        return panel[
            (panel["ticker"].isin(symbols))
            & (panel["date"] >= pd.Timestamp(start_date))
            & (panel["date"] <= pd.Timestamp(end_date))
        ].copy()

    monkeypatch.setattr("research.flow_detection.data.download_price_panel", fake_download_price_panel)

    filtered, meta = ensure_price_panel(
        symbols=["AAA", "BBB", "SPY"],
        start_date="2024-01-01",
        end_date="2024-07-05",
        cache_path=cache_path,
        prefer_local=False,
        allow_download=True,
    )

    assert calls == [
        {
            "symbols": ["AAA", "BBB", "SPY"],
            "start_date": "2024-06-29",
            "end_date": "2024-07-05",
        }
    ]
    assert meta["download_start_date"] == "2024-06-29"
    assert filtered["date"].max() == pd.Timestamp("2024-07-05")


def test_ensure_price_panel_splits_missing_and_stale_download_windows(tmp_path: Path, monkeypatch) -> None:
    panel = _make_panel()
    stale = panel[
        (panel["ticker"].isin(["AAA", "SPY"]))
        & (panel["date"] <= pd.Timestamp("2024-06-28"))
    ].copy()
    cache_path = tmp_path / "price_panel.parquet"
    stale.to_parquet(cache_path, index=False)
    calls = []

    def fake_download_price_panel(*, symbols, start_date, end_date, chunk_size=25, pause_seconds=0.0):
        calls.append({"symbols": list(symbols), "start_date": start_date, "end_date": end_date})
        return panel[
            (panel["ticker"].isin(symbols))
            & (panel["date"] >= pd.Timestamp(start_date))
            & (panel["date"] <= pd.Timestamp(end_date))
        ].copy()

    monkeypatch.setattr("research.flow_detection.data.download_price_panel", fake_download_price_panel)

    filtered, meta = ensure_price_panel(
        symbols=["AAA", "BBB", "SPY"],
        start_date="2024-01-01",
        end_date="2024-07-05",
        cache_path=cache_path,
        prefer_local=False,
        allow_download=True,
    )

    assert calls == [
        {"symbols": ["BBB"], "start_date": "2024-01-01", "end_date": "2024-07-05"},
        {"symbols": ["AAA", "SPY"], "start_date": "2024-06-29", "end_date": "2024-07-05"},
    ]
    assert meta["download_start_by_symbol"]["BBB"] == "2024-01-01"
    assert meta["download_start_by_symbol"]["AAA"] == "2024-06-29"
    assert set(filtered["ticker"].unique()) == {"AAA", "BBB", "SPY"}


def test_ensure_price_panel_records_failed_symbol_without_crashing(tmp_path: Path, monkeypatch) -> None:
    panel = _make_panel()
    calls = []

    def fake_download_price_panel(*, symbols, start_date, end_date, chunk_size=25, pause_seconds=0.0):
        symbols = list(symbols)
        calls.append({"symbols": symbols, "start_date": start_date, "end_date": end_date})
        if "MMC" in symbols:
            raise RuntimeError("MMC failed download")
        return panel[
            (panel["ticker"].isin(symbols))
            & (panel["date"] >= pd.Timestamp(start_date))
            & (panel["date"] <= pd.Timestamp(end_date))
        ].copy()

    monkeypatch.setattr("research.flow_detection.data.download_price_panel", fake_download_price_panel)

    filtered, meta = ensure_price_panel(
        symbols=["AAA", "MMC", "SPY"],
        start_date="2024-01-01",
        end_date="2024-07-05",
        cache_path=tmp_path / "price_panel.parquet",
        prefer_local=False,
        allow_download=True,
        ticker_exceptions_path=tmp_path / "missing_ticker_exceptions.json",
    )

    assert {"AAA", "SPY"} <= set(filtered["ticker"].unique())
    assert "MMC" in meta["download_failed_symbols"]
    assert "MMC failed download" in meta["download_errors"]["MMC"]
    assert meta["download_start_date"] == "2024-01-01"
    assert calls[0]["symbols"] == ["AAA", "MMC", "SPY"]
    assert ["MMC"] in [call["symbols"] for call in calls]


def _tail_cache_rows(date: str, symbols: tuple[str, ...] = ("AAA", "BBB")) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": pd.Timestamp(date),
                "ticker": ticker,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1000,
            }
            for ticker in symbols
        ]
    )


def test_systemic_empty_provider_uses_bounded_group_retries_without_symbol_fanout(
    tmp_path: Path, monkeypatch
) -> None:
    cache_path = tmp_path / "price_panel.parquet"
    _tail_cache_rows("2026-08-21").to_parquet(cache_path, index=False)
    before = cache_path.read_bytes()
    calls: list[list[str]] = []

    def empty_download(*, symbols, **_kwargs):
        calls.append(list(symbols))
        return pd.DataFrame(columns=["date", "ticker", "open", "high", "low", "close", "volume"])

    monkeypatch.setattr("research.flow_detection.data.download_price_panel", empty_download)
    panel, meta = ensure_price_panel(
        symbols=["AAA", "BBB"],
        start_date="2026-08-21",
        end_date="2026-08-24",
        cache_path=cache_path,
        prefer_local=False,
        allow_download=True,
        provider_group_attempts=3,
        provider_retry_backoff_seconds=0,
    )

    assert calls == [["AAA", "BBB"], ["AAA", "BBB"], ["AAA", "BBB"]]
    assert cache_path.read_bytes() == before
    assert panel["date"].max() == pd.Timestamp("2026-08-21")
    assert meta["cache_publish"]["status"] == "BLOCKED_UNCHANGED"
    assert set(meta["download_failed_symbols"]) == {"AAA", "BBB"}
    assert {attempt["result"] for attempt in meta["download_attempts"]} == {"EMPTY"}
    assert all(attempt["scope"] == "GROUP" for attempt in meta["download_attempts"])


def test_missing_session_in_catchup_range_blocks_canonical_publication(
    tmp_path: Path, monkeypatch
) -> None:
    cache_path = tmp_path / "price_panel.parquet"
    _tail_cache_rows("2026-08-24").to_parquet(cache_path, index=False)
    before = cache_path.read_bytes()

    def gap_download(*, symbols, **_kwargs):
        return _tail_cache_rows("2026-08-26", tuple(symbols))

    monkeypatch.setattr("research.flow_detection.data.download_price_panel", gap_download)
    panel, meta = ensure_price_panel(
        symbols=["AAA", "BBB"],
        start_date="2026-08-24",
        end_date="2026-08-26",
        cache_path=cache_path,
        prefer_local=False,
        allow_download=True,
        provider_retry_backoff_seconds=0,
    )

    assert panel["date"].max() == pd.Timestamp("2026-08-26")
    assert meta["coverage_validation"]["status"] == "OK"
    assert meta["catchup_validation"]["status"] == "INCOMPLETE"
    assert meta["catchup_validation"]["missing_sessions_by_symbol"] == {
        "AAA": ["2026-08-25"],
        "BBB": ["2026-08-25"],
    }
    assert meta["cache_publish"]["status"] == "BLOCKED_UNCHANGED"
    assert "catchup_session_coverage_incomplete" in meta["cache_publish"]["reason_codes"]
    assert cache_path.read_bytes() == before


def test_complete_catchup_is_atomically_published_with_hash_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    cache_path = tmp_path / "price_panel.parquet"
    _tail_cache_rows("2026-08-24").to_parquet(cache_path, index=False)
    before = cache_path.read_bytes()

    def complete_download(*, symbols, **_kwargs):
        return pd.concat(
            [
                _tail_cache_rows("2026-08-25", tuple(symbols)),
                _tail_cache_rows("2026-08-26", tuple(symbols)),
            ],
            ignore_index=True,
        )

    monkeypatch.setattr("research.flow_detection.data.download_price_panel", complete_download)
    panel, meta = ensure_price_panel(
        symbols=["AAA", "BBB"],
        start_date="2026-08-24",
        end_date="2026-08-26",
        cache_path=cache_path,
        prefer_local=False,
        allow_download=True,
        provider_retry_backoff_seconds=0,
    )

    published = pd.read_parquet(cache_path)
    assert cache_path.read_bytes() != before
    assert panel["date"].max() == pd.Timestamp("2026-08-26")
    assert published["date"].max() == pd.Timestamp("2026-08-26")
    assert meta["catchup_validation"]["status"] == "OK"
    assert meta["cache_publish"]["status"] == "PUBLISHED"
    assert meta["cache_publish"]["before_sha256"]
    assert meta["cache_publish"]["staged_sha256"] == meta["cache_publish"]["canonical_sha256"]
    assert meta["download_attempts"][0]["result"] == "COMPLETE"


def test_cli_smoke(tmp_path: Path, monkeypatch) -> None:
    panel = _make_panel()
    panel_path = tmp_path / "price_panel.parquet"
    panel.to_parquet(panel_path, index=False)
    out_dir = tmp_path / "out"
    monkeypatch.chdir(Path.cwd())
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "research.flow_detection.run",
            "--start-date",
            "2024-01-01",
            "--end-date",
            "2025-03-31",
            "--num-sims",
            "2",
            "--window-years",
            "2",
            "--price-cache-path",
            str(panel_path),
            "--output-dir",
            str(out_dir),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0
    assert (out_dir / "summary.json").exists()
