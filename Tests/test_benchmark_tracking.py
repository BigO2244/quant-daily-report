from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.benchmark_tracking import (
    _recompute_returns,
    load_existing_benchmark,
    read_broker_equity,
    update_benchmark_vs_spy,
    write_benchmark_artifact,
)


@pytest.fixture()
def tmp_paths(tmp_path: Path):
    broker = tmp_path / "broker_snapshot_latest.json"
    benchmark = tmp_path / "benchmark_vs_spy.json"
    return broker, benchmark


def _write_broker(path: Path, equity: float) -> None:
    path.write_text(json.dumps({"equity": equity, "cash": 1000.0}), encoding="utf-8")


def _mock_spy(price: float):
    return patch("core.benchmark_tracking.fetch_spy_close", return_value=price)


# ---------------------------------------------------------------------------
# load_existing_benchmark
# ---------------------------------------------------------------------------


class TestLoadExistingBenchmark:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert load_existing_benchmark(tmp_path / "nope.json") == []

    def test_valid_file(self, tmp_path: Path) -> None:
        p = tmp_path / "b.json"
        p.write_text('[{"date": "2026-01-01"}]', encoding="utf-8")
        assert load_existing_benchmark(p) == [{"date": "2026-01-01"}]

    def test_corrupt_file_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("NOT JSON", encoding="utf-8")
        assert load_existing_benchmark(p) == []


# ---------------------------------------------------------------------------
# read_broker_equity
# ---------------------------------------------------------------------------


class TestReadBrokerEquity:
    def test_reads_equity(self, tmp_path: Path) -> None:
        p = tmp_path / "snap.json"
        _write_broker(p, 99853.01)
        assert read_broker_equity(p) == 99853.01

    def test_missing_file(self, tmp_path: Path) -> None:
        assert read_broker_equity(tmp_path / "nope.json") is None

    def test_missing_equity_field(self, tmp_path: Path) -> None:
        p = tmp_path / "snap.json"
        p.write_text('{"cash": 1000}', encoding="utf-8")
        assert read_broker_equity(p) is None


# ---------------------------------------------------------------------------
# _recompute_returns
# ---------------------------------------------------------------------------


class TestRecomputeReturns:
    def test_single_record_zeros(self) -> None:
        records = [{"date": "2026-01-02", "portfolio_value": 100000, "spy_price": 500}]
        result = _recompute_returns(records)
        assert result[0]["portfolio_return_daily"] == 0.0
        assert result[0]["portfolio_return_cum"] == 0.0
        assert result[0]["spy_return_daily"] == 0.0
        assert result[0]["excess_return_daily"] == 0.0

    def test_two_records_correct_returns(self) -> None:
        records = [
            {"date": "2026-01-02", "portfolio_value": 100000, "spy_price": 500.0},
            {"date": "2026-01-03", "portfolio_value": 101000, "spy_price": 505.0},
        ]
        result = _recompute_returns(records)
        assert result[1]["portfolio_return_daily"] == pytest.approx(0.01, abs=1e-6)
        assert result[1]["spy_return_daily"] == pytest.approx(0.01, abs=1e-6)
        assert result[1]["excess_return_daily"] == pytest.approx(0.0, abs=1e-6)
        assert result[1]["portfolio_return_cum"] == pytest.approx(0.01, abs=1e-6)

    def test_three_records_cumulative(self) -> None:
        records = [
            {"date": "2026-01-02", "portfolio_value": 100000, "spy_price": 500.0},
            {"date": "2026-01-03", "portfolio_value": 102000, "spy_price": 505.0},
            {"date": "2026-01-06", "portfolio_value": 103000, "spy_price": 502.0},
        ]
        result = _recompute_returns(records)
        # Cum return should be vs first record, not previous
        assert result[2]["portfolio_return_cum"] == pytest.approx(0.03, abs=1e-6)
        assert result[2]["spy_return_cum"] == pytest.approx(0.004, abs=1e-6)

    def test_empty_records(self) -> None:
        assert _recompute_returns([]) == []


# ---------------------------------------------------------------------------
# update_benchmark_vs_spy (end-to-end)
# ---------------------------------------------------------------------------


class TestUpdateBenchmarkVsSpy:
    def test_fresh_file_creation(self, tmp_paths) -> None:
        broker, benchmark = tmp_paths
        _write_broker(broker, 100000.0)
        with _mock_spy(500.0):
            result = update_benchmark_vs_spy(
                trade_date="2026-03-18",
                broker_snapshot_path=broker,
                benchmark_path=benchmark,
            )
        assert result is not None
        assert result["date"] == "2026-03-18"
        assert result["portfolio_value"] == 100000.0
        assert result["spy_price"] == 500.0
        assert result["portfolio_return_daily"] == 0.0

        data = json.loads(benchmark.read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["date"] == "2026-03-18"

    def test_append_new_day(self, tmp_paths) -> None:
        broker, benchmark = tmp_paths
        _write_broker(broker, 101000.0)
        existing = [
            {
                "date": "2026-03-17",
                "portfolio_value": 100000.0,
                "spy_price": 500.0,
                "portfolio_return_daily": 0.0,
                "portfolio_return_cum": 0.0,
                "spy_return_daily": 0.0,
                "spy_return_cum": 0.0,
                "excess_return_daily": 0.0,
                "excess_return_cum": 0.0,
            }
        ]
        benchmark.write_text(json.dumps(existing), encoding="utf-8")

        with _mock_spy(505.0):
            result = update_benchmark_vs_spy(
                trade_date="2026-03-18",
                broker_snapshot_path=broker,
                benchmark_path=benchmark,
            )
        assert result is not None
        data = json.loads(benchmark.read_text(encoding="utf-8"))
        assert len(data) == 2
        assert data[1]["date"] == "2026-03-18"
        assert data[1]["portfolio_return_daily"] == pytest.approx(0.01, abs=1e-6)
        assert data[1]["spy_return_daily"] == pytest.approx(0.01, abs=1e-6)

    def test_idempotent_same_day(self, tmp_paths) -> None:
        broker, benchmark = tmp_paths
        _write_broker(broker, 100000.0)
        existing = [
            {
                "date": "2026-03-18",
                "portfolio_value": 100000.0,
                "spy_price": 500.0,
                "portfolio_return_daily": 0.0,
                "portfolio_return_cum": 0.0,
                "spy_return_daily": 0.0,
                "spy_return_cum": 0.0,
                "excess_return_daily": 0.0,
                "excess_return_cum": 0.0,
            }
        ]
        benchmark.write_text(json.dumps(existing), encoding="utf-8")

        with _mock_spy(500.0):
            result = update_benchmark_vs_spy(
                trade_date="2026-03-18",
                broker_snapshot_path=broker,
                benchmark_path=benchmark,
            )
        assert result is None
        data = json.loads(benchmark.read_text(encoding="utf-8"))
        assert len(data) == 1

    def test_missing_broker_snapshot_nonblocking(self, tmp_paths) -> None:
        _, benchmark = tmp_paths
        broker = Path("/nonexistent/snap.json")
        with _mock_spy(500.0):
            result = update_benchmark_vs_spy(
                trade_date="2026-03-18",
                broker_snapshot_path=broker,
                benchmark_path=benchmark,
            )
        assert result is None
        assert not benchmark.exists()

    def test_spy_fetch_failure_nonblocking(self, tmp_paths) -> None:
        broker, benchmark = tmp_paths
        _write_broker(broker, 100000.0)
        with _mock_spy(None):
            result = update_benchmark_vs_spy(
                trade_date="2026-03-18",
                broker_snapshot_path=broker,
                benchmark_path=benchmark,
            )
        assert result is None
        assert not benchmark.exists()

    def test_records_sorted_by_date(self, tmp_paths) -> None:
        broker, benchmark = tmp_paths
        _write_broker(broker, 102000.0)
        # Existing has a gap — add a date between
        existing = [
            {
                "date": "2026-03-14",
                "portfolio_value": 100000.0,
                "spy_price": 500.0,
                "portfolio_return_daily": 0.0,
                "portfolio_return_cum": 0.0,
                "spy_return_daily": 0.0,
                "spy_return_cum": 0.0,
                "excess_return_daily": 0.0,
                "excess_return_cum": 0.0,
            },
            {
                "date": "2026-03-18",
                "portfolio_value": 103000.0,
                "spy_price": 510.0,
                "portfolio_return_daily": 0.0,
                "portfolio_return_cum": 0.0,
                "spy_return_daily": 0.0,
                "spy_return_cum": 0.0,
                "excess_return_daily": 0.0,
                "excess_return_cum": 0.0,
            },
        ]
        benchmark.write_text(json.dumps(existing), encoding="utf-8")

        with _mock_spy(505.0):
            result = update_benchmark_vs_spy(
                trade_date="2026-03-17",
                broker_snapshot_path=broker,
                benchmark_path=benchmark,
            )
        assert result is not None
        data = json.loads(benchmark.read_text(encoding="utf-8"))
        dates = [r["date"] for r in data]
        assert dates == ["2026-03-14", "2026-03-17", "2026-03-18"]


# ---------------------------------------------------------------------------
# write_benchmark_artifact
# ---------------------------------------------------------------------------


class TestWriteBenchmarkArtifact:
    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        p = tmp_path / "nested" / "dir" / "bench.json"
        write_benchmark_artifact([{"date": "2026-01-01"}], p)
        assert p.exists()
        assert json.loads(p.read_text(encoding="utf-8")) == [{"date": "2026-01-01"}]

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        p = tmp_path / "bench.json"
        p.write_text("[]", encoding="utf-8")
        write_benchmark_artifact([{"date": "2026-01-02"}], p)
        data = json.loads(p.read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["date"] == "2026-01-02"
