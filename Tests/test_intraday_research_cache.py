"""Targeted coverage for the research-only intraday minute-bar cache.

These tests intentionally never hit the network or the Alpaca SDK — they
inject a fake fetcher into ``collect_intraday_cache`` so the determinism,
immutability, and provenance guarantees can be exercised offline.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from scripts.research import intraday_research_cache as irc


ET = ZoneInfo("America/New_York")
TRADE_DATE = "2026-03-24"  # Tuesday, trading day
NON_TRADING_DATE = "2026-03-28"  # Saturday


def _write_plan(tmp_path: Path, tickers: list[str]) -> Path:
    plan_dir = tmp_path / "outputs" / "precompute" / TRADE_DATE
    plan_dir.mkdir(parents=True)
    plan_path = plan_dir / "planned_execution_payload.json"
    payload = {
        "trade_date": TRADE_DATE,
        "plan_only": True,
        "pricing_source": "PREV_CLOSE",
        "trades": [
            {"ticker": t, "side": "BUY", "shares": 1, "entry_price": 100.0}
            for t in tickers
        ],
    }
    plan_path.write_text(json.dumps(payload), encoding="utf-8")
    return plan_path


def _fake_bars_for(symbol: str, trade_date: str) -> list[dict]:
    """Three deterministic minute bars at 09:30, 09:31, 09:32 ET."""
    day = dt.date.fromisoformat(trade_date)
    base = dt.datetime.combine(day, dt.time(9, 30), tzinfo=ET)
    bars = []
    for offset in range(3):
        ts = (base + dt.timedelta(minutes=offset)).astimezone(dt.timezone.utc)
        bars.append(
            {
                "timestamp": ts.isoformat().replace("+00:00", "Z"),
                "open": 100.0 + offset,
                "high": 100.5 + offset,
                "low": 99.5 + offset,
                "close": 100.25 + offset,
                "volume": 1000 + offset,
                "trade_count": 10 + offset,
                "vwap": 100.1 + offset,
            }
        )
    return bars


def _make_fetcher(missing: set[str] | None = None, errors: set[str] | None = None, retrieved_at: str = "2026-05-29T14:00:00Z"):
    missing = missing or set()
    errors = errors or set()

    def _fetch(req: irc.BarFetchRequest):
        if req.symbol in errors:
            raise RuntimeError(f"simulated_vendor_failure:{req.symbol}")
        if req.symbol in missing:
            return None
        bars = _fake_bars_for(req.symbol, req.trade_date)
        return irc._bars_to_frame(req.symbol, req.trade_date, bars, req.feed, retrieved_at)

    return _fetch


# ---------------------------------------------------------------------------
# Canonical contract (Phase 1 lock)
# ---------------------------------------------------------------------------


def test_canonical_contract_constants_are_locked():
    assert irc.CANONICAL_WINDOW_START_ET == dt.time(9, 25)
    assert irc.CANONICAL_WINDOW_END_ET == dt.time(10, 30)
    assert irc.CANONICAL_FEED == "iex"
    assert irc.CACHE_KEY_VERSION == "intraday_bars_v1_iex_0925_1030"
    derived = irc.derive_cache_key_version(
        irc.CANONICAL_FEED, irc.CANONICAL_WINDOW_START_ET, irc.CANONICAL_WINDOW_END_ET
    )
    assert derived == irc.CACHE_KEY_VERSION


def test_non_canonical_inputs_get_a_different_cache_key_version():
    other = irc.derive_cache_key_version("sip", dt.time(9, 30), dt.time(10, 0))
    assert other != irc.CACHE_KEY_VERSION
    assert other == "intraday_bars_v1_sip_0930_1000"


def test_cache_path_embeds_cache_key_version():
    p = irc.cache_path_for("aapl", "2026-03-24", Path("/tmp/cache"))
    assert p == Path("/tmp/cache/intraday_bars_v1_iex_0925_1030/AAPL/2026-03-24.parquet")
    # Non-canonical caller writes under a sibling directory and physically
    # cannot pollute the canonical cache.
    p2 = irc.cache_path_for(
        "aapl",
        "2026-03-24",
        Path("/tmp/cache"),
        cache_key_version="intraday_bars_v1_sip_0930_1000",
    )
    assert p2.parent.parent.name == "intraday_bars_v1_sip_0930_1000"
    assert p2 != p


def test_cli_does_not_expose_feed_or_window_flags():
    parser = irc.build_parser()
    flag_names = {a.option_strings[0] for a in parser._actions if a.option_strings}
    assert "--feed" not in flag_names
    assert "--window-start" not in flag_names
    assert "--window-end" not in flag_names


# ---------------------------------------------------------------------------
# Plan discovery
# ---------------------------------------------------------------------------


def test_load_plan_symbols_dedupes_and_sorts(tmp_path):
    plan_path = _write_plan(tmp_path, ["MSFT", "aapl", "MSFT", "GOOG"])
    symbols = irc.load_plan_symbols(plan_path)
    assert symbols == ["AAPL", "GOOG", "MSFT"]


def test_load_plan_symbols_missing_plan_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        irc.load_plan_symbols(tmp_path / "nope.json")


# ---------------------------------------------------------------------------
# End-to-end collector behavior
# ---------------------------------------------------------------------------


def test_collect_writes_parquet_and_status(tmp_path):
    plan_path = _write_plan(tmp_path, ["AAPL", "MSFT"])
    cache_root = tmp_path / "data" / "research_cache" / "intraday"
    status_root = tmp_path / "outputs" / "research" / "intraday_collection"

    result = irc.collect_intraday_cache(
        trade_date=TRADE_DATE,
        plan_path=plan_path,
        cache_root=cache_root,
        status_root=status_root,
        fetcher=_make_fetcher(),
        now=dt.datetime(2026, 5, 29, 14, 15, tzinfo=dt.timezone.utc),
    )

    assert result.overall_status == "OK"
    assert result.cache_key_version == irc.CACHE_KEY_VERSION
    assert result.counts == {"fetched": 2, "cached": 0, "missing": 0, "errors": 0}

    aapl_path = cache_root / irc.CACHE_KEY_VERSION / "AAPL" / f"{TRADE_DATE}.parquet"
    msft_path = cache_root / irc.CACHE_KEY_VERSION / "MSFT" / f"{TRADE_DATE}.parquet"
    assert aapl_path.exists() and msft_path.exists()

    frame = pd.read_parquet(aapl_path)
    assert list(frame.columns) == irc.PARQUET_COLUMNS
    assert frame["symbol"].unique().tolist() == ["AAPL"]
    assert frame["trade_date"].unique().tolist() == [TRADE_DATE]
    assert frame.shape[0] == 3
    assert frame["source"].unique().tolist() == [irc.SOURCE_LABEL]
    assert frame["feed"].unique().tolist() == [irc.CANONICAL_FEED]
    assert frame["retrieved_at"].nunique() == 1

    status = json.loads((status_root / TRADE_DATE / "status.json").read_text())
    assert status["schema_version"] == irc.SCHEMA_VERSION
    assert status["cache_key_version"] == irc.CACHE_KEY_VERSION
    assert status["trade_date"] == TRADE_DATE
    assert status["symbols_requested"] == ["AAPL", "MSFT"]
    assert status["overall_status"] == "OK"
    assert status["intraday_source"] == irc.SOURCE_LABEL
    assert status["feed"] == irc.CANONICAL_FEED
    assert status["counts"] == {"fetched": 2, "cached": 0, "missing": 0, "errors": 0}
    statuses = {row["symbol"]: row["status"] for row in status["symbol_results"]}
    assert statuses == {"AAPL": "fetched", "MSFT": "fetched"}


def test_rerun_is_idempotent_and_does_not_modify_cache(tmp_path):
    """Second run must mark every symbol ``cached`` and leave parquet bytes untouched."""
    plan_path = _write_plan(tmp_path, ["AAPL", "MSFT"])
    cache_root = tmp_path / "data" / "research_cache" / "intraday"
    status_root = tmp_path / "outputs" / "research" / "intraday_collection"

    # First run: fetches and writes.
    irc.collect_intraday_cache(
        trade_date=TRADE_DATE,
        plan_path=plan_path,
        cache_root=cache_root,
        status_root=status_root,
        fetcher=_make_fetcher(retrieved_at="2026-05-29T14:00:00Z"),
        now=dt.datetime(2026, 5, 29, 14, 15, tzinfo=dt.timezone.utc),
    )

    aapl_path = cache_root / irc.CACHE_KEY_VERSION / "AAPL" / f"{TRADE_DATE}.parquet"
    msft_path = cache_root / irc.CACHE_KEY_VERSION / "MSFT" / f"{TRADE_DATE}.parquet"
    digests_before = {
        p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (aapl_path, msft_path)
    }
    mtimes_before = {p: p.stat().st_mtime_ns for p in (aapl_path, msft_path)}

    # Second run with a fetcher that would BLOW UP if called — proves the
    # cache was not re-fetched.
    def _exploding(req):
        raise AssertionError(f"fetcher should not be called on rerun for {req.symbol}")

    result = irc.collect_intraday_cache(
        trade_date=TRADE_DATE,
        plan_path=plan_path,
        cache_root=cache_root,
        status_root=status_root,
        fetcher=_exploding,
        now=dt.datetime(2026, 5, 29, 18, 0, tzinfo=dt.timezone.utc),
    )

    assert result.counts == {"fetched": 0, "cached": 2, "missing": 0, "errors": 0}
    assert result.overall_status == "OK"

    digests_after = {
        p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (aapl_path, msft_path)
    }
    mtimes_after = {p: p.stat().st_mtime_ns for p in (aapl_path, msft_path)}
    assert digests_before == digests_after
    assert mtimes_before == mtimes_after


def test_status_artifact_differs_only_in_generated_at(tmp_path):
    """Deterministic rerun: status JSON's only varying field is ``generated_at``."""
    plan_path = _write_plan(tmp_path, ["AAPL"])
    cache_root = tmp_path / "cache"
    status_root = tmp_path / "status"

    irc.collect_intraday_cache(
        trade_date=TRADE_DATE,
        plan_path=plan_path,
        cache_root=cache_root,
        status_root=status_root,
        fetcher=_make_fetcher(retrieved_at="2026-05-29T14:00:00Z"),
        now=dt.datetime(2026, 5, 29, 14, 15, tzinfo=dt.timezone.utc),
    )
    first = json.loads((status_root / TRADE_DATE / "status.json").read_text())

    irc.collect_intraday_cache(
        trade_date=TRADE_DATE,
        plan_path=plan_path,
        cache_root=cache_root,
        status_root=status_root,
        fetcher=_make_fetcher(retrieved_at="2099-01-01T00:00:00Z"),  # would change rows if called
        now=dt.datetime(2026, 5, 30, 11, 0, tzinfo=dt.timezone.utc),
    )
    second = json.loads((status_root / TRADE_DATE / "status.json").read_text())

    assert first["generated_at"] != second["generated_at"]
    # On rerun, ``counts`` and per-symbol ``status`` legitimately switch from
    # ``fetched`` to ``cached`` (and ``retrieved_at`` is no longer recorded
    # because the fetcher was not called). After normalizing those expected
    # run-state fields, the rest of the payload must be byte-identical.
    for blob in (first, second):
        blob.pop("generated_at", None)
        blob.pop("counts", None)
        for row in blob["symbol_results"]:
            row.pop("retrieved_at", None)
            row.pop("status", None)
    assert first == second


def test_non_canonical_call_writes_to_isolated_directory(tmp_path):
    """A function-level override (e.g., a test/study calling with a non-canonical
    window) must NOT shadow or contaminate the canonical cache."""
    plan_path = _write_plan(tmp_path, ["AAPL"])
    cache_root = tmp_path / "cache"
    status_root = tmp_path / "status"

    # Canonical run first.
    irc.collect_intraday_cache(
        trade_date=TRADE_DATE,
        plan_path=plan_path,
        cache_root=cache_root,
        status_root=status_root,
        fetcher=_make_fetcher(retrieved_at="2026-05-29T14:00:00Z"),
        now=dt.datetime(2026, 5, 29, 14, 15, tzinfo=dt.timezone.utc),
    )
    canonical_path = cache_root / irc.CACHE_KEY_VERSION / "AAPL" / f"{TRADE_DATE}.parquet"
    assert canonical_path.exists()
    canonical_digest = hashlib.sha256(canonical_path.read_bytes()).hexdigest()
    canonical_mtime = canonical_path.stat().st_mtime_ns

    # Non-canonical run (different feed + window) — must write elsewhere.
    result = irc.collect_intraday_cache(
        trade_date=TRADE_DATE,
        plan_path=plan_path,
        cache_root=cache_root,
        status_root=status_root,
        fetcher=_make_fetcher(retrieved_at="2099-12-31T00:00:00Z"),
        feed="sip",
        window_start=dt.time(9, 30),
        window_end=dt.time(10, 0),
        now=dt.datetime(2026, 5, 29, 14, 30, tzinfo=dt.timezone.utc),
    )
    assert result.cache_key_version == "intraday_bars_v1_sip_0930_1000"
    other_path = cache_root / "intraday_bars_v1_sip_0930_1000" / "AAPL" / f"{TRADE_DATE}.parquet"
    assert other_path.exists()
    assert other_path != canonical_path

    # Canonical bytes/mtime are untouched.
    assert hashlib.sha256(canonical_path.read_bytes()).hexdigest() == canonical_digest
    assert canonical_path.stat().st_mtime_ns == canonical_mtime


def test_missing_data_recorded_without_writing_parquet(tmp_path):
    plan_path = _write_plan(tmp_path, ["AAPL", "ZZZZ"])
    cache_root = tmp_path / "cache"
    status_root = tmp_path / "status"

    result = irc.collect_intraday_cache(
        trade_date=TRADE_DATE,
        plan_path=plan_path,
        cache_root=cache_root,
        status_root=status_root,
        fetcher=_make_fetcher(missing={"ZZZZ"}),
        now=dt.datetime(2026, 5, 29, 14, 15, tzinfo=dt.timezone.utc),
    )

    assert result.counts == {"fetched": 1, "cached": 0, "missing": 1, "errors": 0}
    assert result.overall_status == "PARTIAL"
    keyed_root = cache_root / irc.CACHE_KEY_VERSION
    assert (keyed_root / "AAPL" / f"{TRADE_DATE}.parquet").exists()
    assert not (keyed_root / "ZZZZ" / f"{TRADE_DATE}.parquet").exists()
    assert not (keyed_root / "ZZZZ").exists()

    status = json.loads((status_root / TRADE_DATE / "status.json").read_text())
    by_symbol = {row["symbol"]: row for row in status["symbol_results"]}
    assert by_symbol["ZZZZ"]["status"] == "missing"
    assert by_symbol["ZZZZ"]["reason"] == "no_bars_returned"


def test_vendor_error_does_not_abort_run(tmp_path):
    plan_path = _write_plan(tmp_path, ["AAPL", "OOPS"])
    cache_root = tmp_path / "cache"
    status_root = tmp_path / "status"

    result = irc.collect_intraday_cache(
        trade_date=TRADE_DATE,
        plan_path=plan_path,
        cache_root=cache_root,
        status_root=status_root,
        fetcher=_make_fetcher(errors={"OOPS"}),
        now=dt.datetime(2026, 5, 29, 14, 15, tzinfo=dt.timezone.utc),
    )

    assert result.counts["fetched"] == 1
    assert result.counts["errors"] == 1
    assert result.overall_status == "FAILED"
    keyed_root = cache_root / irc.CACHE_KEY_VERSION
    assert (keyed_root / "AAPL" / f"{TRADE_DATE}.parquet").exists()
    status = json.loads((status_root / TRADE_DATE / "status.json").read_text())
    by_symbol = {row["symbol"]: row for row in status["symbol_results"]}
    assert by_symbol["OOPS"]["status"] == "error"
    assert "simulated_vendor_failure" in by_symbol["OOPS"]["reason"]


def test_symbols_override_bypasses_plan(tmp_path):
    cache_root = tmp_path / "cache"
    status_root = tmp_path / "status"

    result = irc.collect_intraday_cache(
        trade_date=TRADE_DATE,
        symbols_override=["spy", "QQQ", "spy"],
        cache_root=cache_root,
        status_root=status_root,
        fetcher=_make_fetcher(),
        now=dt.datetime(2026, 5, 29, 14, 15, tzinfo=dt.timezone.utc),
    )

    assert result.plan_source == "override"
    assert result.counts == {"fetched": 2, "cached": 0, "missing": 0, "errors": 0}
    keyed_root = cache_root / irc.CACHE_KEY_VERSION
    assert (keyed_root / "SPY" / f"{TRADE_DATE}.parquet").exists()
    assert (keyed_root / "QQQ" / f"{TRADE_DATE}.parquet").exists()


def test_cache_path_and_window_helpers():
    assert irc.cache_path_for("aapl", "2026-03-24", Path("/tmp/cache")) == Path(
        "/tmp/cache/intraday_bars_v1_iex_0925_1030/AAPL/2026-03-24.parquet"
    )
    start, end = irc._window_for("2026-03-24", dt.time(9, 25), dt.time(10, 30))
    assert start.tzinfo is not None and end.tzinfo is not None
    assert start.hour == 9 and start.minute == 25
    assert end.hour == 10 and end.minute == 30


# ---------------------------------------------------------------------------
# Trading-day guard
# ---------------------------------------------------------------------------


def test_collect_refuses_non_trading_day(tmp_path):
    plan_path = _write_plan(tmp_path, ["AAPL"])
    with pytest.raises(ValueError, match="trade_date_is_not_trading_day"):
        irc.collect_intraday_cache(
            trade_date=NON_TRADING_DATE,
            plan_path=plan_path,
            cache_root=tmp_path / "cache",
            status_root=tmp_path / "status",
            fetcher=_make_fetcher(),
            now=dt.datetime(2026, 5, 29, 14, 15, tzinfo=dt.timezone.utc),
        )


def test_collect_can_skip_trading_day_guard_for_tests(tmp_path):
    plan_path = _write_plan(tmp_path, ["AAPL"])
    result = irc.collect_intraday_cache(
        trade_date=NON_TRADING_DATE,
        plan_path=plan_path,
        cache_root=tmp_path / "cache",
        status_root=tmp_path / "status",
        fetcher=_make_fetcher(),
        now=dt.datetime(2026, 5, 29, 14, 15, tzinfo=dt.timezone.utc),
        require_trading_day=False,
    )
    assert result.overall_status == "OK"


def test_cli_refuses_non_trading_day(capsys, tmp_path):
    rc = irc.main(["--trade-date", NON_TRADING_DATE, "--cache-root", str(tmp_path / "c"), "--status-root", str(tmp_path / "s")])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["overall_status"] == "REFUSED"
    assert out["reason"] == "trade_date_is_not_trading_day"


# ---------------------------------------------------------------------------
# Execution-path isolation
# ---------------------------------------------------------------------------


EXECUTION_PATH_SENTINELS = [
    Path("scripts/cron_execute.sh"),
    Path("scripts/cron_precompute.sh"),
    Path("scripts/cron_research.sh"),
    Path("scripts/crontab.txt"),
    Path("scripts/run_precomputed_alpaca_execution.py"),
    Path("core/timing_policy.py"),
    Path("brokers/alpaca_broker.py"),
    Path("reconciliation.py"),
]


def test_does_not_touch_execution_path_artifacts(tmp_path):
    """The collector must leave every execution-path sentinel byte-unchanged.

    Sentinel set covers the scheduler (cron_*.sh + crontab.txt), the executor
    (run_precomputed_alpaca_execution.py + core/timing_policy.py), the
    broker (brokers/alpaca_broker.py), and reconciliation (reconciliation.py)
    — i.e. exactly the surfaces the spec forbids us from modifying.
    """
    repo_root = Path(__file__).resolve().parents[1]
    sentinels = [repo_root / p for p in EXECUTION_PATH_SENTINELS if (repo_root / p).exists()]
    assert len(sentinels) >= 7, (
        "expected the execution-path sentinel set to be substantially populated; "
        f"only found {len(sentinels)} of {len(EXECUTION_PATH_SENTINELS)}"
    )
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sentinels}

    plan_path = _write_plan(tmp_path, ["AAPL"])
    irc.collect_intraday_cache(
        trade_date=TRADE_DATE,
        plan_path=plan_path,
        cache_root=tmp_path / "cache",
        status_root=tmp_path / "status",
        fetcher=_make_fetcher(),
        now=dt.datetime(2026, 5, 29, 14, 15, tzinfo=dt.timezone.utc),
    )

    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sentinels}
    assert before == after
