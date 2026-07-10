"""Hermetic tests for the flag-gated Alpaca price source (CAERUS_PRICE_SOURCE).

No network: the Alpaca client is mocked, yfinance download calls are patched.
Covers schema equivalence, dispatcher defaults, per-symbol fallback, '^' index
routing, chunking, and the shadow-compare artifact (including SKIPPED path).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import core.price_sources as ps
import core.quant_report as qr
import paper.paper_broker as pb
import scripts.price_source_shadow_compare as shadow

REPO_ROOT = Path(__file__).resolve().parents[1]

UTC = dt.timezone.utc


# ---------------------------------------------------------------------------
# Fakes / fixtures
# ---------------------------------------------------------------------------


class FakeBar(SimpleNamespace):
    pass


def _bar(day: str, o: float, h: float, lo: float, c: float, v: int) -> FakeBar:
    # Alpaca stamps daily bars at 04:00 UTC == midnight America/New_York.
    ts = dt.datetime.fromisoformat(day).replace(hour=4, tzinfo=UTC)
    return FakeBar(timestamp=ts, open=o, high=h, low=lo, close=c, volume=float(v))


class FakeBarSet:
    def __init__(self, data):
        self.data = data


class FakeAlpacaClient:
    """Serves canned bars per symbol; records every StockBarsRequest."""

    def __init__(self, bars_by_symbol=None, error=None):
        self.bars_by_symbol = bars_by_symbol or {}
        self.error = error
        self.requests = []

    def get_stock_bars(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        symbols = request.symbol_or_symbols
        if isinstance(symbols, str):
            symbols = [symbols]
        return FakeBarSet(
            {s: self.bars_by_symbol[s] for s in symbols if s in self.bars_by_symbol}
        )


DAYS = ["2026-07-07", "2026-07-08"]


def _default_bars():
    return {
        "AAPL": [_bar(DAYS[0], 100.0, 101.0, 99.0, 100.5, 1000),
                 _bar(DAYS[1], 100.5, 102.0, 100.0, 101.5, 1100)],
        "MSFT": [_bar(DAYS[0], 200.0, 202.0, 198.0, 201.0, 2000),
                 _bar(DAYS[1], 201.0, 203.0, 200.0, 202.0, 2100)],
    }


def _yf_wide_daily(days=DAYS, base=100.0):
    """Wide-form frame shaped like yf.download(single ticker) output."""
    # Real yfinance returns a datetime64[ns] index; pin the fixture to match.
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days], name="Date").astype(
        "datetime64[ns]"
    )
    n = len(days)
    return pd.DataFrame(
        {
            "Open": [base + i for i in range(n)],
            "High": [base + 1 + i for i in range(n)],
            "Low": [base - 1 + i for i in range(n)],
            "Close": [base + 0.5 + i for i in range(n)],
            "Adj Close": [base + 0.5 + i for i in range(n)],
            "Volume": [1000 + i for i in range(n)],
        },
        index=idx,
    )


def _long_form(tickers, days=DAYS, base=100.0):
    rows = []
    for t in tickers:
        for i, d in enumerate(days):
            rows.append(
                {
                    "date": pd.Timestamp(d),
                    "ticker": t,
                    "open": base + i,
                    "high": base + 1 + i,
                    "low": base - 1 + i,
                    "close": base + 0.5 + i,
                    "volume": 1000 + i,
                }
            )
    return pd.DataFrame(rows, columns=ps.DOWNLOAD_PRICE_COLUMNS)


class _FakeYfModule:
    """Stands in for the lazily imported yfinance module in paper_broker."""

    def __init__(self, frame):
        self.frame = frame

    def set_tz_cache_location(self, *_a, **_k):
        pass

    def download(self, *_a, **_k):
        return self.frame


# ---------------------------------------------------------------------------
# 1. Schema equivalence
# ---------------------------------------------------------------------------


def test_download_prices_schema_equivalence(monkeypatch):
    monkeypatch.setattr(qr.yf, "download", lambda *a, **k: _yf_wide_daily())
    yf_df = qr._download_prices_yfinance(["AAPL"], period="1mo", interval="1d")

    client = FakeAlpacaClient({"AAPL": _default_bars()["AAPL"]})
    alp_df = ps.alpaca_download_prices(["AAPL"], period="1mo", interval="1d", client=client)

    assert list(alp_df.columns) == list(yf_df.columns) == ps.DOWNLOAD_PRICE_COLUMNS
    assert dict(alp_df.dtypes.astype(str)) == dict(yf_df.dtypes.astype(str))
    assert isinstance(alp_df.index, pd.RangeIndex) and isinstance(yf_df.index, pd.RangeIndex)
    assert str(alp_df["date"].dtype) == "datetime64[ns]"
    assert alp_df["date"].dt.tz is None
    assert list(alp_df["date"]) == list(yf_df["date"])
    assert list(alp_df["ticker"]) == list(yf_df["ticker"]) == ["AAPL", "AAPL"]
    assert alp_df["volume"].dtype == np.dtype("int64")


def test_open_prices_schema_equivalence(monkeypatch):
    run_date = DAYS[1]
    wide = _yf_wide_daily(days=[run_date])
    monkeypatch.setattr(pb, "_load_yfinance", lambda: _FakeYfModule(wide))
    yf_df = pb._fetch_open_prices_yfinance_impl(["AAPL"], run_date)

    client = FakeAlpacaClient({"AAPL": [_default_bars()["AAPL"][1]]})
    alp_df = ps.alpaca_fetch_open_prices(["AAPL"], run_date, client=client)

    assert list(alp_df.columns) == list(yf_df.columns) == ps.OPEN_PRICE_COLUMNS
    assert dict(alp_df.dtypes.astype(str)) == dict(yf_df.dtypes.astype(str))
    assert alp_df["ticker"].tolist() == yf_df["ticker"].tolist() == ["AAPL"]
    assert alp_df["price_date"].tolist() == yf_df["price_date"].tolist() == [run_date]
    assert isinstance(alp_df["open"].iloc[0], float)


def test_open_prices_uses_iex_feed_and_adjustment_all():
    client = FakeAlpacaClient({"AAPL": [_default_bars()["AAPL"][1]]})
    ps.alpaca_fetch_open_prices(["AAPL"], DAYS[1], client=client)
    req = client.requests[0]
    assert str(getattr(req.feed, "value", req.feed)) == "iex"

    hist_client = FakeAlpacaClient(_default_bars())
    ps.alpaca_download_prices(["AAPL"], period="1mo", client=hist_client)
    hist_req = hist_client.requests[0]
    assert str(getattr(hist_req.adjustment, "value", hist_req.adjustment)) == "all"
    assert hist_req.feed is None  # multi-day history uses the default feed


# ---------------------------------------------------------------------------
# 2. Dispatcher: CAERUS_PRICE_SOURCE
# ---------------------------------------------------------------------------


def test_dispatcher_default_is_yfinance_function_object(monkeypatch):
    monkeypatch.delenv("CAERUS_PRICE_SOURCE", raising=False)
    sentinel = pd.DataFrame({"marker": [1]})
    calls = {}

    def fake_yf(tickers, period="1y", interval="1d"):
        calls["args"] = (tuple(tickers), period, interval)
        return sentinel

    def trap(*_a, **_k):  # pragma: no cover - must never run
        raise AssertionError("alpaca path must not run when env is unset")

    monkeypatch.setattr(qr, "_download_prices_yfinance", fake_yf)
    monkeypatch.setattr(ps, "alpaca_download_prices", trap)

    out = qr.download_prices(["AAPL"], period="6mo", interval="1d")
    assert out is sentinel
    assert calls["args"] == (("AAPL",), "6mo", "1d")


def test_dispatcher_explicit_yfinance(monkeypatch):
    monkeypatch.setenv("CAERUS_PRICE_SOURCE", "yfinance")
    sentinel = pd.DataFrame({"marker": [2]})
    monkeypatch.setattr(qr, "_download_prices_yfinance", lambda *a, **k: sentinel)
    monkeypatch.setattr(
        ps, "alpaca_download_prices",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("alpaca ran")),
    )
    assert qr.download_prices(["AAPL"]) is sentinel


def test_dispatcher_selects_alpaca(monkeypatch):
    monkeypatch.setenv("CAERUS_PRICE_SOURCE", "alpaca")
    sentinel = pd.DataFrame({"marker": [3]})
    calls = {}

    def fake_alpaca(tickers, period="1y", interval="1d", **_k):
        calls["args"] = (tuple(tickers), period, interval)
        return sentinel

    monkeypatch.setattr(ps, "alpaca_download_prices", fake_alpaca)
    monkeypatch.setattr(
        qr, "_download_prices_yfinance",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("yfinance ran")),
    )
    out = qr.download_prices(["AAPL"], period="5y")
    assert out is sentinel
    assert calls["args"] == (("AAPL",), "5y", "1d")


def test_open_prices_dispatcher_default_is_yfinance(monkeypatch):
    monkeypatch.delenv("CAERUS_PRICE_SOURCE", raising=False)
    sentinel = pd.DataFrame({"marker": [4]})
    monkeypatch.setattr(pb, "_fetch_open_prices_yfinance_impl", lambda *a, **k: sentinel)
    monkeypatch.setattr(
        ps, "alpaca_fetch_open_prices",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("alpaca ran")),
    )
    assert pb.fetch_open_prices_yfinance(["AAPL"], "2026-07-08") is sentinel


def test_open_prices_dispatcher_selects_alpaca(monkeypatch):
    monkeypatch.setenv("CAERUS_PRICE_SOURCE", "alpaca")
    sentinel = pd.DataFrame({"marker": [5]})
    calls = {}

    def fake_alpaca(tickers, run_date, **_k):
        calls["args"] = (tuple(tickers), run_date)
        return sentinel

    monkeypatch.setattr(ps, "alpaca_fetch_open_prices", fake_alpaca)
    monkeypatch.setattr(
        pb, "_fetch_open_prices_yfinance_impl",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("yfinance ran")),
    )
    assert pb.fetch_open_prices_yfinance(["AAPL"], "2026-07-08") is sentinel
    assert calls["args"] == (("AAPL",), "2026-07-08")


def test_unknown_source_value_resolves_to_yfinance(monkeypatch):
    monkeypatch.setenv("CAERUS_PRICE_SOURCE", "polygon")
    assert ps.resolve_price_source() == "yfinance"
    monkeypatch.setenv("CAERUS_PRICE_SOURCE", "")
    assert ps.resolve_price_source() == "yfinance"


# ---------------------------------------------------------------------------
# 3. Fail-safe fallback + '^' routing
# ---------------------------------------------------------------------------


def test_per_symbol_fallback_on_alpaca_error(monkeypatch, caplog):
    client = FakeAlpacaClient(error=RuntimeError("alpaca down"))
    fallback_calls = {}

    def fake_yf(tickers, period, interval):
        fallback_calls["tickers"] = list(tickers)
        return _long_form(tickers)

    with caplog.at_level(logging.WARNING):
        out = ps.alpaca_download_prices(
            ["AAPL", "MSFT"], period="1mo", client=client, yf_fallback=fake_yf
        )
    assert sorted(fallback_calls["tickers"]) == ["AAPL", "MSFT"]
    assert sorted(out["ticker"].unique()) == ["AAPL", "MSFT"]
    assert any("[PRICE_SOURCE] alpaca fallback" in r.message for r in caplog.records)


def test_partial_missing_symbols_fall_back(monkeypatch, caplog):
    client = FakeAlpacaClient({"AAPL": _default_bars()["AAPL"]})  # MSFT missing
    fallback_calls = {}

    def fake_yf(tickers, period, interval):
        fallback_calls["tickers"] = list(tickers)
        return _long_form(tickers, base=200.0)

    with caplog.at_level(logging.WARNING):
        out = ps.alpaca_download_prices(
            ["AAPL", "MSFT"], period="1mo", client=client, yf_fallback=fake_yf
        )
    assert fallback_calls["tickers"] == ["MSFT"]
    assert sorted(out["ticker"].unique()) == ["AAPL", "MSFT"]
    assert any("[PRICE_SOURCE] alpaca fallback" in r.message for r in caplog.records)


def test_index_symbols_route_to_yfinance():
    client = FakeAlpacaClient({"SPY": _default_bars()["AAPL"]})
    fallback_calls = {}

    def fake_yf(tickers, period, interval):
        fallback_calls["tickers"] = list(tickers)
        return _long_form(tickers, base=20.0)

    out = ps.alpaca_download_prices(
        ["SPY", "^VIX", "^GSPC"], period="1y", client=client, yf_fallback=fake_yf
    )
    assert sorted(fallback_calls["tickers"]) == ["^GSPC", "^VIX"]
    # Alpaca was only asked for the non-index symbol
    for req in client.requests:
        assert all(not s.startswith("^") for s in req.symbol_or_symbols)
    assert sorted(out["ticker"].unique()) == ["SPY", "^GSPC", "^VIX"]


def test_open_prices_index_routing_and_fallback(caplog):
    client = FakeAlpacaClient({"SPY": [_default_bars()["AAPL"][1]]})  # QQQ missing
    fallback_calls = {}

    def fake_yf(tickers, run_date):
        fallback_calls["tickers"] = list(tickers)
        return pd.DataFrame(
            [{"ticker": t, "open": 10.0, "price_date": run_date} for t in tickers],
            columns=ps.OPEN_PRICE_COLUMNS,
        )

    with caplog.at_level(logging.WARNING):
        out = ps.alpaca_fetch_open_prices(
            ["SPY", "QQQ", "^VIX"], DAYS[1], client=client, yf_fallback=fake_yf
        )
    assert sorted(fallback_calls["tickers"]) == ["QQQ", "^VIX"]
    assert sorted(out["ticker"].tolist()) == ["QQQ", "SPY", "^VIX"]
    assert any("[PRICE_SOURCE] alpaca fallback" in r.message for r in caplog.records)


def test_no_silent_data_loss_without_fallback_raises():
    client = FakeAlpacaClient(error=RuntimeError("alpaca down"))

    def no_data(tickers, period, interval):
        raise RuntimeError("offline")

    with pytest.raises(RuntimeError):
        ps.alpaca_download_prices(["AAPL"], client=client, yf_fallback=no_data)


# ---------------------------------------------------------------------------
# 4. Chunking
# ---------------------------------------------------------------------------


def test_chunking_over_200_symbols():
    symbols = [f"T{i:03d}" for i in range(450)]
    bars = {s: _default_bars()["AAPL"] for s in symbols}
    client = FakeAlpacaClient(bars)
    out = ps.alpaca_download_prices(symbols, period="1mo", client=client)
    sizes = [len(req.symbol_or_symbols) for req in client.requests]
    assert sizes == [200, 200, 50]
    assert out["ticker"].nunique() == 450


def test_open_prices_chunking():
    symbols = [f"T{i:03d}" for i in range(201)]
    bars = {s: [_default_bars()["AAPL"][1]] for s in symbols}
    client = FakeAlpacaClient(bars)
    out = ps.alpaca_fetch_open_prices(symbols, DAYS[1], client=client)
    sizes = [len(req.symbol_or_symbols) for req in client.requests]
    assert sizes == [200, 1]
    assert len(out) == 201


# ---------------------------------------------------------------------------
# 5. Shadow compare script
# ---------------------------------------------------------------------------


def _read_artifact(root: Path, report_date: str) -> dict:
    path = root / "workflow" / report_date / shadow.ARTIFACT_NAME
    assert path.exists(), f"missing artifact {path}"
    return json.loads(path.read_text())


def test_shadow_skips_without_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    rc = shadow.main(
        ["--date", "2026-07-08", "--tickers", "AAPL,MSFT", "--output-root", str(tmp_path)]
    )
    assert rc == 0
    payload = _read_artifact(tmp_path, "2026-07-08")
    assert payload["status"] == "SKIPPED"
    assert "ALPACA_API_KEY_ID" in payload["reason"]
    assert payload["n_symbols_requested"] == 2


def test_shadow_skips_on_fetch_error(monkeypatch, tmp_path):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "k")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "s")

    def boom(_tickers):
        raise ConnectionError("no network")

    monkeypatch.setattr(shadow, "_fetch_both", boom)
    rc = shadow.main(
        ["--date", "2026-07-08", "--tickers", "AAPL", "--output-root", str(tmp_path)]
    )
    assert rc == 0
    payload = _read_artifact(tmp_path, "2026-07-08")
    assert payload["status"] == "SKIPPED"
    assert "ConnectionError" in payload["reason"]


def test_shadow_artifact_shape_ok_path(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "k")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "s")

    yf_hist = _long_form(["AAPL", "MSFT", "GONE"])
    alp_hist = _long_form(["AAPL", "MSFT"])
    # introduce a known close divergence on MSFT's last session (+1%)
    mask = (alp_hist["ticker"] == "MSFT") & (alp_hist["date"] == pd.Timestamp(DAYS[1]))
    alp_hist.loc[mask, "close"] = alp_hist.loc[mask, "close"] * 1.01
    open_session = DAYS[1]
    yf_open = pd.DataFrame(
        [{"ticker": "AAPL", "open": 100.0, "price_date": open_session},
         {"ticker": "MSFT", "open": 200.0, "price_date": open_session}],
        columns=ps.OPEN_PRICE_COLUMNS,
    )
    alp_open = pd.DataFrame(
        [{"ticker": "AAPL", "open": 100.5, "price_date": open_session},
         {"ticker": "MSFT", "open": 200.0, "price_date": open_session}],
        columns=ps.OPEN_PRICE_COLUMNS,
    )
    monkeypatch.setattr(
        shadow, "_fetch_both",
        lambda _tickers: (yf_hist, alp_hist, yf_open, alp_open, open_session),
    )

    rc = shadow.main(
        ["--date", "2026-07-08", "--tickers", "AAPL,MSFT,GONE", "--output-root", str(tmp_path)]
    )
    assert rc == 0
    payload = _read_artifact(tmp_path, "2026-07-08")
    assert payload["status"] == "OK"
    assert payload["open_session"] == open_session
    assert payload["missing_from_alpaca"] == ["GONE"]
    assert payload["missing_from_yfinance"] == []
    per = payload["per_symbol"]
    assert per["AAPL"]["close_max_abs_dev_pct"] == 0.0
    assert per["MSFT"]["close_max_abs_dev_pct"] == pytest.approx(1.0, abs=1e-6)
    assert per["AAPL"]["open_dev_pct"] == pytest.approx(0.5, abs=1e-6)
    summary = payload["summary"]
    assert summary["close"]["n"] == 2
    assert summary["close"]["max_pct"] == pytest.approx(1.0, abs=1e-6)
    assert summary["open"]["max_pct"] == pytest.approx(0.5, abs=1e-6)
    assert summary["worst_close_symbols"][0]["ticker"] == "MSFT"
    line = capsys.readouterr().out.strip()
    assert line.startswith("price_source_shadow status=OK")
    assert "missing_alpaca=1" in line


def test_shadow_universe_derivation_caps_symbols():
    tickers = shadow._universe_tickers(REPO_ROOT, 120)
    assert tickers[0] == "SPY"
    assert len(tickers) <= 120
    assert len(tickers) == len(set(tickers))


# ---------------------------------------------------------------------------
# 6. Cron hook syntax
# ---------------------------------------------------------------------------


def test_cron_precompute_bash_syntax():
    script = REPO_ROOT / "scripts" / "cron_precompute.sh"
    proc = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    text = script.read_text()
    assert "CAERUS_PRICE_SHADOW" in text
    assert "price_source_shadow_compare" in text
    assert "|| true" in text
