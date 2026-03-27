from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.benchmark_tracking import (
    _portfolio_value_from_summary,
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


def _write_nested_broker(path: Path, equity: float) -> None:
    path.write_text(
        json.dumps(
            {
                "account": {
                    "equity": equity,
                    "portfolio_value": equity,
                    "cash": 1000.0,
                }
            }
        ),
        encoding="utf-8",
    )


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

    def test_object_format_returns_records(self, tmp_path: Path) -> None:
        p = tmp_path / "b.json"
        p.write_text(
            json.dumps(
                {
                    "inception_date": "2026-01-01",
                    "records": [{"date": "2026-01-01"}],
                }
            ),
            encoding="utf-8",
        )
        assert load_existing_benchmark(p) == [{"date": "2026-01-01"}]


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

    def test_reads_nested_account_equity(self, tmp_path: Path) -> None:
        p = tmp_path / "snap.json"
        _write_nested_broker(p, 99853.01)
        assert read_broker_equity(p) == 99853.01


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


class TestPortfolioValueFromSummary:
    def test_prefers_broker_preflight_equity_over_benchmark_placeholder(self) -> None:
        summary = {
            "benchmark": {"portfolio_value": 10000.0},
            "broker_context": {
                "broker_preflight_equity": "9569.05",
                "broker_equity_at_planning": 9568.25,
            },
            "portfolio_state": {
                "cash_after": 4090.95,
                "portfolio_market_value": 5473.12,
            },
        }

        assert _portfolio_value_from_summary(summary) == pytest.approx(9569.05)


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
        assert len(data["records"]) == 1
        assert data["records"][0]["date"] == "2026-03-18"

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
        records = data["records"]
        assert len(records) == 2
        assert records[1]["date"] == "2026-03-18"
        assert records[1]["portfolio_return_daily"] == pytest.approx(0.01, abs=1e-6)
        assert records[1]["spy_return_daily"] == pytest.approx(0.01, abs=1e-6)

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
        assert len(data["records"]) == 1

    def test_idempotent_same_day_recovers_history_from_run_summaries(self, tmp_path: Path) -> None:
        benchmark = tmp_path / "outputs" / "benchmark" / "benchmark_vs_spy.json"
        benchmark.parent.mkdir(parents=True, exist_ok=True)
        benchmark.write_text(
            json.dumps(
                {
                    "inception_date": "2026-03-27",
                    "records": [
                        {
                            "date": "2026-03-27",
                            "portfolio_value": 100500.0,
                            "portfolio_return_daily": 0.0,
                            "portfolio_return_cum": 0.0,
                            "spy_price": 505.0,
                            "spy_return_daily": 0.0,
                            "spy_return_cum": 0.0,
                            "excess_return_daily": 0.0,
                            "excess_return_cum": 0.0,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        run_root = tmp_path / "outputs" / "runs" / "2026-03-26T093500-0400_abc123"
        run_root.mkdir(parents=True, exist_ok=True)
        (run_root / "trading_day_summary.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-03-26",
                    "generated_at": "2026-03-26T13:35:00+00:00",
                    "broker_context": {"broker_preflight_equity": 100000.0},
                    "benchmark": {"spy_value": 500.0},
                }
            ),
            encoding="utf-8",
        )

        result = update_benchmark_vs_spy(
            trade_date="2026-03-27",
            broker_snapshot_path=tmp_path / "missing_snapshot.json",
            benchmark_path=benchmark,
            workspace_root=tmp_path,
        )

        assert result is None
        data = json.loads(benchmark.read_text(encoding="utf-8"))
        records = data["records"]
        assert data["inception_date"] == "2026-03-27"
        assert [record["date"] for record in records] == ["2026-03-26", "2026-03-27"]
        assert records[1]["portfolio_return_daily"] == pytest.approx(0.005, abs=1e-6)
        assert records[1]["spy_return_daily"] == pytest.approx(0.01, abs=1e-6)
        assert records[1]["excess_return_daily"] == pytest.approx(-0.005, abs=1e-6)

    def test_recovery_uses_benchmark_close_history_when_summary_missing_spy_value(
        self,
        tmp_path: Path,
    ) -> None:
        benchmark = tmp_path / "benchmark_vs_spy.json"
        perf_dir = tmp_path / "outputs" / "perf"
        perf_dir.mkdir(parents=True, exist_ok=True)
        (perf_dir / "benchmark_close_history.csv").write_text(
            "date,spy_close\n2026-03-18,505.0\n",
            encoding="utf-8",
        )

        run_root = tmp_path / "outputs" / "runs" / "2026-03-18T093500-0400_abc123"
        run_root.mkdir(parents=True, exist_ok=True)
        (run_root / "trading_day_summary.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-03-18",
                    "generated_at": "2026-03-18T13:35:00+00:00",
                    "broker_context": {"broker_preflight_equity": 101250.0},
                    "benchmark": {},
                }
            ),
            encoding="utf-8",
        )

        result = update_benchmark_vs_spy(
            trade_date="2026-03-18",
            broker_snapshot_path=tmp_path / "missing_snapshot.json",
            benchmark_path=benchmark,
            workspace_root=tmp_path,
        )

        assert result is None
        data = json.loads(benchmark.read_text(encoding="utf-8"))
        records = data["records"]
        assert len(records) == 1
        assert records[0]["date"] == "2026-03-18"
        assert records[0]["portfolio_value"] == 101250.0
        assert records[0]["spy_price"] == 505.0

    def test_recovery_repairs_portfolio_value_but_keeps_existing_spy_close(
        self,
        tmp_path: Path,
    ) -> None:
        benchmark = tmp_path / "outputs" / "benchmark" / "benchmark_vs_spy.json"
        benchmark.parent.mkdir(parents=True, exist_ok=True)
        benchmark.write_text(
            json.dumps(
                {
                    "inception_date": "2026-03-27",
                    "records": [
                        {
                            "date": "2026-03-27",
                            "portfolio_value": 10000.0,
                            "portfolio_return_daily": 0.0,
                            "portfolio_return_cum": 0.0,
                            "spy_price": 640.825,
                            "spy_return_daily": 0.0,
                            "spy_return_cum": 0.0,
                            "excess_return_daily": 0.0,
                            "excess_return_cum": 0.0,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        run_root = tmp_path / "outputs" / "runs" / "2026-03-27T094322-0400_abc123"
        run_root.mkdir(parents=True, exist_ok=True)
        (run_root / "trading_day_summary.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-03-27",
                    "generated_at": "2026-03-27T13:43:42.533675+00:00",
                    "benchmark": {"portfolio_value": 10000.0, "spy_value": 645.09},
                    "broker_context": {"broker_preflight_equity": "9569.05"},
                }
            ),
            encoding="utf-8",
        )

        result = update_benchmark_vs_spy(
            trade_date="2026-03-27",
            broker_snapshot_path=tmp_path / "missing_snapshot.json",
            benchmark_path=benchmark,
            workspace_root=tmp_path,
        )

        assert result is None
        data = json.loads(benchmark.read_text(encoding="utf-8"))
        records = data["records"]
        assert len(records) == 1
        assert records[0]["portfolio_value"] == pytest.approx(9569.05)
        assert records[0]["spy_price"] == pytest.approx(640.825)

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
        dates = [r["date"] for r in data["records"]]
        assert dates == ["2026-03-14", "2026-03-17", "2026-03-18"]

    def test_run_root_fallback_to_pretrade_snapshot(self, tmp_path: Path) -> None:
        run_root = tmp_path / "runs" / "2026-03-18T093500"
        pretrade = run_root / "broker" / "pretrade_account_snapshot.json"
        pretrade.parent.mkdir(parents=True, exist_ok=True)
        _write_nested_broker(pretrade, 101250.0)
        benchmark = tmp_path / "benchmark_vs_spy.json"

        with _mock_spy(505.0):
            result = update_benchmark_vs_spy(
                trade_date="2026-03-18",
                broker_snapshot_path=tmp_path / "missing_latest.json",
                benchmark_path=benchmark,
                run_root=run_root,
            )

        assert result is not None
        assert result["portfolio_value"] == 101250.0
        data = json.loads(benchmark.read_text(encoding="utf-8"))
        assert data["records"][0]["date"] == "2026-03-18"
        assert data["records"][0]["spy_price"] == 505.0


# ---------------------------------------------------------------------------
# write_benchmark_artifact
# ---------------------------------------------------------------------------


class TestWriteBenchmarkArtifact:
    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        p = tmp_path / "nested" / "dir" / "bench.json"
        write_benchmark_artifact([{"date": "2026-01-01"}], p)
        assert p.exists()
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["records"] == [{"date": "2026-01-01"}]

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        p = tmp_path / "bench.json"
        p.write_text("[]", encoding="utf-8")
        write_benchmark_artifact([{"date": "2026-01-02"}], p)
        data = json.loads(p.read_text(encoding="utf-8"))
        assert len(data["records"]) == 1
        assert data["records"][0]["date"] == "2026-01-02"
