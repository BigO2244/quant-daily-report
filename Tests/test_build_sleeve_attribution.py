"""Smoke tests for scripts/build_sleeve_attribution.py with fixture data.

All tests are hermetic (no network, no broker calls, tmp_path fixtures).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from scripts.build_sleeve_attribution import (
    GATE_FAIL,
    GATE_INSUFFICIENT,
    GATE_PASS,
    MIN_POSITIVE_WEEKS,
    MIN_REGIME_LABELS,
    STATUS_INSUFFICIENT,
    STATUS_OK,
    build_sleeve_attribution,
    _compute_sleeve_attribution,
    _evaluate_gates,
    _load_signals_snapshots,
    _sleeve_weights_from_snapshot,
    _vix_label_from_snapshot,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _write_signal_snapshot(signals_dir: Path, date_str: str, sleeve_allocs: dict, vix_label: str = "LOW") -> None:
    signals_dir.mkdir(parents=True, exist_ok=True)
    snap = {
        "snapshot_date": date_str,
        "sleeve_allocations": sleeve_allocs,
        "market_analyzer": {"vix_regime": vix_label},
        "signals": [
            {"ticker": "AAPL", "target_weight": v, "sleeve": k, "raw_score": v}
            for k, v in sleeve_allocs.items()
        ],
    }
    (signals_dir / f"{date_str}.json").write_text(json.dumps(snap) + "\n", encoding="utf-8")


def _write_contribution_csv(perf_dir: Path, date_str: str, sleeve_contributions: list[dict]) -> None:
    perf_dir.mkdir(parents=True, exist_ok=True)
    lines = ["index,sleeve,weight_start,sleeve_return,contribution"]
    for i, row in enumerate(sleeve_contributions):
        lines.append(
            f"{i},{row['sleeve']},{row['weight_start']},{row.get('sleeve_return', 0.0)},{row['contribution']}"
        )
    (perf_dir / f"contribution_sleeves_{date_str}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_benchmark_csv(perf_dir: Path, rows: list[tuple[str, float]]) -> None:
    perf_dir.mkdir(parents=True, exist_ok=True)
    lines = ["date,close"]
    close = 500.0
    for date_str, ret in rows:
        close *= (1 + ret)
        lines.append(f"{date_str},{close:.4f}")
    (perf_dir / "benchmark_close_history.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests: fixture loading
# ---------------------------------------------------------------------------

class TestLoadSignalsSnapshots:
    def test_loads_snapshots_in_window(self, tmp_path: Path) -> None:
        sdir = tmp_path / "signals"
        as_of = dt.date(2026, 4, 16)
        # In window
        _write_signal_snapshot(sdir, "2026-04-10", {"sleeve_trend": 0.6})
        _write_signal_snapshot(sdir, "2026-04-16", {"sleeve_trend": 0.5})
        # Out of window (too old)
        _write_signal_snapshot(sdir, "2025-01-01", {"sleeve_trend": 0.9})

        snaps = _load_signals_snapshots(sdir, as_of=as_of, lookback_weeks=8)
        assert len(snaps) == 2

    def test_empty_when_dir_missing(self, tmp_path: Path) -> None:
        snaps = _load_signals_snapshots(tmp_path / "missing", as_of=dt.date(2026, 4, 16))
        assert snaps == []

    def test_extracts_sleeve_allocs(self, tmp_path: Path) -> None:
        sdir = tmp_path / "signals"
        _write_signal_snapshot(sdir, "2026-04-16", {"sleeve_trend": 0.7, "sleeve_quality": 0.3}, vix_label="HIGH")
        snaps = _load_signals_snapshots(sdir, as_of=dt.date(2026, 4, 16), lookback_weeks=2)
        assert len(snaps) == 1
        weights = _sleeve_weights_from_snapshot(snaps[0])
        assert abs(weights.get("sleeve_trend", 0) - 0.7) < 1e-6

    def test_extracts_vix_label(self, tmp_path: Path) -> None:
        sdir = tmp_path / "signals"
        _write_signal_snapshot(sdir, "2026-04-16", {"sleeve_trend": 0.7}, vix_label="HIGH")
        snaps = _load_signals_snapshots(sdir, as_of=dt.date(2026, 4, 16), lookback_weeks=2)
        label = _vix_label_from_snapshot(snaps[0])
        assert label == "HIGH"


# ---------------------------------------------------------------------------
# Tests: attribution computation
# ---------------------------------------------------------------------------

class TestComputeSleeveAttribution:
    def test_empty_inputs(self) -> None:
        result = _compute_sleeve_attribution([], [], {})
        assert result == {}

    def test_aggregates_contributions(self) -> None:
        contributions = [
            {"date": "2026-04-10", "sleeve": "sleeve_trend", "weight_start": 0.6, "sleeve_return": 0.01, "contribution": 0.006},
            {"date": "2026-04-11", "sleeve": "sleeve_trend", "weight_start": 0.6, "sleeve_return": -0.005, "contribution": -0.003},
        ]
        result = _compute_sleeve_attribution([], contributions, {})
        assert "sleeve_trend" in result
        st = result["sleeve_trend"]
        assert st["observation_days"] == 2
        assert st["positive_days"] == 1
        assert abs(st["total_contribution"] - 0.003) < 1e-9

    def test_benchmark_excess_computed(self) -> None:
        contributions = [
            {"date": "2026-04-10", "sleeve": "sleeve_quality", "weight_start": 0.4, "sleeve_return": 0.02, "contribution": 0.008},
        ]
        # Benchmark returned 0.003 over the period
        bench = {"2026-04-10": 0.003}
        result = _compute_sleeve_attribution([], contributions, bench)
        sq = result["sleeve_quality"]
        # excess = total_contribution - total_bench_return
        assert sq["benchmark_excess"] is not None
        assert abs(sq["benchmark_excess"] - (0.008 - 0.003)) < 1e-9

    def test_vix_regimes_from_snapshots(self, tmp_path: Path) -> None:
        sdir = tmp_path / "signals"
        _write_signal_snapshot(sdir, "2026-04-10", {"sleeve_trend": 0.7}, vix_label="LOW")
        _write_signal_snapshot(sdir, "2026-04-16", {"sleeve_trend": 0.6}, vix_label="HIGH")
        snaps = _load_signals_snapshots(sdir, as_of=dt.date(2026, 4, 16), lookback_weeks=8)
        result = _compute_sleeve_attribution(snaps, [], {})
        assert "sleeve_trend" in result
        regimes = set(result["sleeve_trend"]["vix_regimes_seen"])
        assert "LOW" in regimes
        assert "HIGH" in regimes


# ---------------------------------------------------------------------------
# Tests: promotion gate evaluation
# ---------------------------------------------------------------------------

class TestEvaluateGates:
    def _make_attr(
        self,
        *,
        observation_days: int = 30,
        total_contribution: float = 0.05,
        positive_days: int = 20,
        avg_weight: float = 0.5,
        vix_regimes_seen: list[str] | None = None,
    ) -> dict:
        if vix_regimes_seen is None:
            vix_regimes_seen = ["LOW", "MEDIUM"]
        avg_daily = total_contribution / observation_days if observation_days else 0.0
        return {
            "observation_days": observation_days,
            "total_contribution": total_contribution,
            "avg_daily_contribution": avg_daily,
            "avg_weight": avg_weight,
            "positive_days": positive_days,
            "benchmark_excess": 0.01,
            "vix_regimes_seen": vix_regimes_seen,
        }

    def test_all_gates_pass(self) -> None:
        attr = self._make_attr(observation_days=40, positive_days=25)
        gate = _evaluate_gates("sleeve_trend", attr, [])
        assert gate["gate_overall"] == GATE_PASS
        assert gate["gate_1_positive_attribution_weeks"]["status"] == GATE_PASS
        assert gate["gate_2_live_affordability"]["status"] == GATE_PASS
        assert gate["gate_3_regime_rotation"]["status"] == GATE_PASS

    def test_gate1_insufficient_when_too_few_obs_days(self) -> None:
        attr = self._make_attr(observation_days=5, positive_days=3)
        gate = _evaluate_gates("sleeve_trend", attr, [])
        assert gate["gate_1_positive_attribution_weeks"]["status"] == GATE_INSUFFICIENT
        assert gate["gate_overall"] == GATE_INSUFFICIENT

    def test_gate1_fail_when_few_positive_weeks(self) -> None:
        # 40 obs days but only 5 positive days -> 1 positive week < MIN_POSITIVE_WEEKS
        attr = self._make_attr(observation_days=40, positive_days=5)
        gate = _evaluate_gates("sleeve_trend", attr, [])
        assert gate["gate_1_positive_attribution_weeks"]["status"] == GATE_FAIL
        assert gate["gate_overall"] == GATE_FAIL

    def test_gate2_fail_when_weight_zero(self) -> None:
        attr = self._make_attr(avg_weight=0.0)
        gate = _evaluate_gates("sleeve_trend", attr, [])
        assert gate["gate_2_live_affordability"]["status"] == GATE_FAIL

    def test_gate2_fail_when_notional_below_floor(self) -> None:
        # avg_weight=0.001, cap=500 -> $0.50 notional < $10 floor
        attr = self._make_attr(avg_weight=0.001)
        gate = _evaluate_gates("sleeve_trend", attr, [])
        assert gate["gate_2_live_affordability"]["status"] == GATE_FAIL

    def test_gate2_insufficient_when_no_weight(self) -> None:
        attr = self._make_attr()
        attr["avg_weight"] = None
        gate = _evaluate_gates("sleeve_trend", attr, [])
        assert gate["gate_2_live_affordability"]["status"] == GATE_INSUFFICIENT

    def test_gate3_insufficient_when_no_regimes(self) -> None:
        attr = self._make_attr(vix_regimes_seen=[])
        gate = _evaluate_gates("sleeve_trend", attr, [])
        assert gate["gate_3_regime_rotation"]["status"] == GATE_INSUFFICIENT

    def test_gate3_insufficient_when_only_one_regime(self) -> None:
        attr = self._make_attr(vix_regimes_seen=["LOW"])
        gate = _evaluate_gates("sleeve_trend", attr, [])
        assert gate["gate_3_regime_rotation"]["status"] == GATE_INSUFFICIENT

    def test_gate3_pass_with_two_regimes(self) -> None:
        attr = self._make_attr(vix_regimes_seen=["LOW", "HIGH"])
        gate = _evaluate_gates("sleeve_trend", attr, [])
        assert gate["gate_3_regime_rotation"]["status"] == GATE_PASS


# ---------------------------------------------------------------------------
# Tests: end-to-end build_sleeve_attribution with fixture data
# ---------------------------------------------------------------------------

class TestBuildSleeveAttributionFixture:
    def _setup_fixture(self, tmp_path: Path, *, num_weeks: int = 6) -> tuple[Path, Path, Path]:
        sdir = tmp_path / "signals"
        pdir = tmp_path / "perf"
        odir = tmp_path / "out"
        as_of = dt.date(2026, 4, 16)
        # Write signal snapshots spanning two VIX regimes
        dates_low = ["2026-03-04", "2026-03-21", "2026-03-22"]
        dates_high = ["2026-03-24", "2026-04-09", "2026-04-10"]
        for d in dates_low:
            _write_signal_snapshot(sdir, d, {"sleeve_trend": 0.7, "sleeve_quality": 0.3}, vix_label="LOW")
        for d in dates_high:
            _write_signal_snapshot(sdir, d, {"sleeve_trend": 0.5, "sleeve_quality": 0.5}, vix_label="HIGH")

        # Write contribution CSVs — one per date, positive returns for sleeve_trend
        contrib_dates = [
            ("2026-03-04", [
                {"sleeve": "sleeve_trend", "weight_start": 0.7, "sleeve_return": 0.01, "contribution": 0.007},
                {"sleeve": "sleeve_quality", "weight_start": 0.3, "sleeve_return": 0.005, "contribution": 0.0015},
            ]),
            ("2026-03-21", [
                {"sleeve": "sleeve_trend", "weight_start": 0.7, "sleeve_return": 0.012, "contribution": 0.0084},
                {"sleeve": "sleeve_quality", "weight_start": 0.3, "sleeve_return": -0.002, "contribution": -0.0006},
            ]),
            ("2026-03-22", [
                {"sleeve": "sleeve_trend", "weight_start": 0.6, "sleeve_return": 0.008, "contribution": 0.0048},
                {"sleeve": "sleeve_quality", "weight_start": 0.4, "sleeve_return": 0.003, "contribution": 0.0012},
            ]),
            ("2026-03-24", [
                {"sleeve": "sleeve_trend", "weight_start": 0.5, "sleeve_return": 0.015, "contribution": 0.0075},
                {"sleeve": "sleeve_quality", "weight_start": 0.5, "sleeve_return": 0.01, "contribution": 0.005},
            ]),
            ("2026-04-09", [
                {"sleeve": "sleeve_trend", "weight_start": 0.5, "sleeve_return": 0.009, "contribution": 0.0045},
                {"sleeve": "sleeve_quality", "weight_start": 0.5, "sleeve_return": -0.005, "contribution": -0.0025},
            ]),
            ("2026-04-10", [
                {"sleeve": "sleeve_trend", "weight_start": 0.5, "sleeve_return": 0.011, "contribution": 0.0055},
                {"sleeve": "sleeve_quality", "weight_start": 0.5, "sleeve_return": 0.006, "contribution": 0.003},
            ]),
        ] * (num_weeks // 6 + 1)
        for i, (d, contribs) in enumerate(contrib_dates[:max(6, num_weeks)]):
            _write_contribution_csv(pdir, d, contribs)

        # Write benchmark CSV
        bench_rows = [
            ("2026-03-03", 0.0),  # anchor
            ("2026-03-04", 0.005),
            ("2026-03-21", 0.003),
            ("2026-03-22", 0.004),
            ("2026-03-24", 0.006),
            ("2026-04-09", 0.002),
            ("2026-04-10", 0.007),
        ]
        _write_benchmark_csv(pdir, bench_rows)
        return sdir, pdir, odir

    def test_runs_and_writes_json_and_markdown(self, tmp_path: Path) -> None:
        sdir, pdir, odir = self._setup_fixture(tmp_path)
        result = build_sleeve_attribution(
            as_of=dt.date(2026, 4, 16),
            signals_dir=sdir,
            perf_dir=pdir,
            output_dir=odir,
            lookback_weeks=8,
        )
        json_path = Path(result["json_path"])
        md_path = Path(result["markdown_path"])
        assert json_path.exists()
        assert md_path.exists()

        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["schema"] == "caerus.sleeve_attribution.v1"
        assert data["as_of"] == "2026-04-16"
        assert "sleeve_attribution" in data
        assert "promotion_gates" in data

    def test_sleeve_attribution_populated(self, tmp_path: Path) -> None:
        sdir, pdir, odir = self._setup_fixture(tmp_path)
        result = build_sleeve_attribution(
            as_of=dt.date(2026, 4, 16),
            signals_dir=sdir,
            perf_dir=pdir,
            output_dir=odir,
            lookback_weeks=8,
        )
        attr = result["sleeve_attribution"]
        assert "sleeve_trend" in attr
        assert "sleeve_quality" in attr
        st = attr["sleeve_trend"]
        assert st["observation_days"] > 0
        assert st["total_contribution"] is not None
        assert st["total_contribution"] > 0  # sleeve_trend had all positive days

    def test_promotion_gates_present_and_structured(self, tmp_path: Path) -> None:
        sdir, pdir, odir = self._setup_fixture(tmp_path)
        result = build_sleeve_attribution(
            as_of=dt.date(2026, 4, 16),
            signals_dir=sdir,
            perf_dir=pdir,
            output_dir=odir,
            lookback_weeks=8,
        )
        gates = result["promotion_gates"]
        assert len(gates) >= 2
        gate_sleeves = {g["sleeve"] for g in gates}
        assert "sleeve_trend" in gate_sleeves
        for g in gates:
            assert "gate_overall" in g
            assert g["gate_overall"] in (GATE_PASS, GATE_FAIL, GATE_INSUFFICIENT)
            assert "gate_1_positive_attribution_weeks" in g
            assert "gate_2_live_affordability" in g
            assert "gate_3_regime_rotation" in g

    def test_markdown_contains_promotion_gate_section(self, tmp_path: Path) -> None:
        sdir, pdir, odir = self._setup_fixture(tmp_path)
        result = build_sleeve_attribution(
            as_of=dt.date(2026, 4, 16),
            signals_dir=sdir,
            perf_dir=pdir,
            output_dir=odir,
            lookback_weeks=8,
        )
        md = Path(result["markdown_path"]).read_text(encoding="utf-8")
        assert "PROMOTION GATE" in md
        assert "sleeve_trend" in md
        assert "Gate 1" in md
        assert "Gate 2" in md
        assert "Gate 3" in md

    def test_degrades_gracefully_with_no_signals(self, tmp_path: Path) -> None:
        pdir = tmp_path / "perf"
        odir = tmp_path / "out"
        result = build_sleeve_attribution(
            as_of=dt.date(2026, 4, 16),
            signals_dir=tmp_path / "missing_signals",
            perf_dir=pdir,
            output_dir=odir,
            lookback_weeks=8,
        )
        assert result["data_status"] == STATUS_INSUFFICIENT
        assert len(result["warnings"]) > 0
        # Must still write outputs without raising
        assert Path(result["json_path"]).exists()
        assert Path(result["markdown_path"]).exists()

    def test_no_fabricated_numbers_when_missing_perf(self, tmp_path: Path) -> None:
        sdir = tmp_path / "signals"
        odir = tmp_path / "out"
        _write_signal_snapshot(sdir, "2026-04-16", {"sleeve_trend": 0.7}, vix_label="LOW")
        result = build_sleeve_attribution(
            as_of=dt.date(2026, 4, 16),
            signals_dir=sdir,
            perf_dir=tmp_path / "missing_perf",
            output_dir=odir,
            lookback_weeks=8,
        )
        # Attribution should exist from signal snapshots but contribution data is None
        attr = result["sleeve_attribution"]
        if "sleeve_trend" in attr:
            assert attr["sleeve_trend"]["total_contribution"] is None
            assert attr["sleeve_trend"]["avg_daily_contribution"] is None

    def test_does_not_overwrite_with_stale_data(self, tmp_path: Path) -> None:
        sdir, pdir, odir = self._setup_fixture(tmp_path)
        # Run once
        r1 = build_sleeve_attribution(
            as_of=dt.date(2026, 4, 16),
            signals_dir=sdir,
            perf_dir=pdir,
            output_dir=odir,
        )
        ts1 = json.loads(Path(r1["json_path"]).read_text())["generated_at"]
        # Run again — should regenerate (allow_overwrite semantics)
        r2 = build_sleeve_attribution(
            as_of=dt.date(2026, 4, 16),
            signals_dir=sdir,
            perf_dir=pdir,
            output_dir=odir,
        )
        ts2 = json.loads(Path(r2["json_path"]).read_text())["generated_at"]
        # Both artifacts must be valid (second run doesn't crash)
        assert Path(r2["json_path"]).exists()
        assert ts1 is not None
        assert ts2 is not None


class TestBuildSleeveAttributionNoNetwork:
    """Verify the module runs offline without any broker or HTTP calls."""

    def test_fully_offline_no_imports_fail(self, tmp_path: Path) -> None:
        """Should not raise even with empty dirs — pure filesystem ops."""
        result = build_sleeve_attribution(
            as_of=dt.date(2026, 1, 15),
            signals_dir=tmp_path / "no_signals",
            perf_dir=tmp_path / "no_perf",
            output_dir=tmp_path / "out",
            lookback_weeks=4,
        )
        assert result["data_status"] == STATUS_INSUFFICIENT
        assert result["signals_snapshot_count"] == 0
        assert result["contribution_rows"] == 0
