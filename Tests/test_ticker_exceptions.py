from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from research.flow_detection.data import ensure_price_panel
from scripts import hydrate_price_cache_only as hydrator


def _write_exceptions(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _panel(ticker: str, date: str = "2026-05-04") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": pd.Timestamp(date),
                "ticker": ticker,
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 1000,
            }
        ]
    )


def test_ignored_ticker_is_not_downloaded(tmp_path: Path, monkeypatch, caplog) -> None:
    exceptions = tmp_path / "ticker_exceptions.json"
    cache = tmp_path / "price_panel.parquet"
    _write_exceptions(exceptions, {"ignore": ["MMC"], "aliases": {}, "notes": {"MMC": "bad yahoo symbol"}})
    calls = []

    def fake_download_price_panel(*, symbols, start_date, end_date, chunk_size=25, pause_seconds=0.0):
        calls.append(list(symbols))
        return _panel("AAA", end_date)

    monkeypatch.setattr("research.flow_detection.data.download_price_panel", fake_download_price_panel)

    with caplog.at_level(logging.INFO):
        _panel_out, meta = ensure_price_panel(
            symbols=["AAA", "MMC"],
            start_date="2026-05-01",
            end_date="2026-05-04",
            cache_path=cache,
            prefer_local=False,
            allow_download=True,
            ticker_exceptions_path=exceptions,
        )

    assert calls == [["AAA"]]
    assert meta["ignored_tickers"] == ["MMC"]
    assert "MMC" not in meta["download_failed_symbols"]
    assert "Skipping ignored ticker: MMC" in caplog.text


def test_ignored_ticker_appears_in_status_artifact(tmp_path: Path, monkeypatch) -> None:
    universe = tmp_path / "universe.csv"
    universe.write_text("ticker\nAAA\nMMC\n", encoding="utf-8")
    cache = tmp_path / "price_panel.parquet"
    status_dir = tmp_path / "status"
    exceptions = tmp_path / "ticker_exceptions.json"
    _write_exceptions(exceptions, {"ignore": ["MMC"], "aliases": {}, "notes": {"MMC": "bad yahoo symbol"}})

    def fake_ensure_price_panel(**kwargs):
        _panel("AAA").to_parquet(Path(kwargs["cache_path"]), index=False)
        return pd.DataFrame(), {
            "download_performed": True,
            "ignored_tickers": ["MMC"],
            "aliased_tickers": {},
        }

    monkeypatch.setattr(hydrator, "ensure_price_panel", fake_ensure_price_panel)
    monkeypatch.setattr(hydrator, "resolve_completed_trading_day", lambda explicit_trade_date=None: "2026-05-04")

    rc = hydrator.main(
        [
            "--trade-date",
            "2026-05-04",
            "--universe-path",
            str(universe),
            "--cache-path",
            str(cache),
            "--status-dir",
            str(status_dir),
            "--ticker-exceptions-path",
            str(exceptions),
        ]
    )

    payload = json.loads((status_dir / "2026-05-04" / "status.json").read_text())
    assert rc == 0
    assert payload["ignored_tickers"] == ["MMC"]
    assert payload["aliased_tickers"] == {}


def test_alias_mapping_replaces_ticker_for_download_and_maps_back(tmp_path: Path, monkeypatch) -> None:
    exceptions = tmp_path / "ticker_exceptions.json"
    cache = tmp_path / "price_panel.parquet"
    _write_exceptions(exceptions, {"ignore": [], "aliases": {"OLD": "NEW"}, "notes": {}})
    calls = []

    def fake_download_price_panel(*, symbols, start_date, end_date, chunk_size=25, pause_seconds=0.0):
        calls.append(list(symbols))
        return pd.concat(
            [_panel("NEW", start_date), _panel("NEW", end_date)],
            ignore_index=True,
        )

    monkeypatch.setattr("research.flow_detection.data.download_price_panel", fake_download_price_panel)

    panel, meta = ensure_price_panel(
        symbols=["OLD"],
        start_date="2026-05-01",
        end_date="2026-05-04",
        cache_path=cache,
        prefer_local=False,
        allow_download=True,
        ticker_exceptions_path=exceptions,
    )

    assert calls == [["NEW"]]
    assert meta["aliased_tickers"] == {"OLD": "NEW"}
    assert set(panel["ticker"]) == {"OLD"}
    assert set(pd.read_parquet(cache)["ticker"]) == {"OLD"}


def test_missing_config_preserves_current_download_behavior(tmp_path: Path, monkeypatch) -> None:
    missing_config = tmp_path / "missing_ticker_exceptions.json"
    cache = tmp_path / "price_panel.parquet"
    calls = []

    def fake_download_price_panel(*, symbols, start_date, end_date, chunk_size=25, pause_seconds=0.0):
        calls.append(list(symbols))
        return pd.concat([_panel(symbol, end_date) for symbol in symbols], ignore_index=True)

    monkeypatch.setattr("research.flow_detection.data.download_price_panel", fake_download_price_panel)

    _panel_out, meta = ensure_price_panel(
        symbols=["AAA", "MMC"],
        start_date="2026-05-01",
        end_date="2026-05-04",
        cache_path=cache,
        prefer_local=False,
        allow_download=True,
        ticker_exceptions_path=missing_config,
    )

    assert calls == [["AAA", "MMC"]]
    assert meta["ignored_tickers"] == []
    assert meta["aliased_tickers"] == {}


def test_empty_config_behaves_like_current_system(tmp_path: Path, monkeypatch) -> None:
    exceptions = tmp_path / "ticker_exceptions.json"
    cache = tmp_path / "price_panel.parquet"
    _write_exceptions(exceptions, {"ignore": [], "aliases": {}, "notes": {}})
    calls = []

    def fake_download_price_panel(*, symbols, start_date, end_date, chunk_size=25, pause_seconds=0.0):
        calls.append(list(symbols))
        return pd.concat([_panel(symbol, end_date) for symbol in symbols], ignore_index=True)

    monkeypatch.setattr("research.flow_detection.data.download_price_panel", fake_download_price_panel)

    _panel_out, meta = ensure_price_panel(
        symbols=["AAA", "MMC"],
        start_date="2026-05-01",
        end_date="2026-05-04",
        cache_path=cache,
        prefer_local=False,
        allow_download=True,
        ticker_exceptions_path=exceptions,
    )

    assert calls == [["AAA", "MMC"]]
    assert meta["download_failed_symbols"] == []
    assert meta["ignored_tickers"] == []
