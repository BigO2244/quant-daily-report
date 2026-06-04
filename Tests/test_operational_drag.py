from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from research.operational_drag import (
    build_actual_nav,
    build_benchmark_nav,
    build_intended_nav,
    build_operational_drag,
    build_operational_drag_analysis,
    build_operational_drag_attribution,
    build_stable_window_analysis,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
TRADE_DATE = "2026-06-02"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_price_panel_parquet(path: Path, rows: list[dict]) -> None:
    pd = pytest.importorskip("pandas")
    try:
        import pyarrow  # noqa: F401
    except Exception:
        pytest.skip("pyarrow is required for parquet fixture coverage")
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path)


def _fixture_repo(tmp_path: Path, *, missing_bbb_price: bool = False, missing_spy_end: bool = False) -> Path:
    root = tmp_path / "repo"
    _write_json(
        root / "outputs" / "precompute" / "2026-06-01" / "daily_snapshot.json",
        {
            "asof": "2026-06-01 00:00:00",
            "equity": 10000.0,
            "target_cash_weight": 0.10,
            "orders": [
                {"ticker": "AAA", "target_weight": 0.50, "execution_price": 100.0},
                {"ticker": "BBB", "target_weight": 0.40, "execution_price": 50.0},
            ],
        },
    )
    _write_json(
        root / "outputs" / "precompute" / "2026-06-01" / "planned_execution_payload.json",
        {
            "trade_date": "2026-06-01",
            "run_id": "2026-06-01:main:caerus_polaris",
            "equity": 10000.0,
            "buys": 2,
            "execution_eligible_trades_count": 2,
            "submitted_count": 0,
            "trades": [
                {"ticker": "AAA", "side": "BUY", "shares": 50, "entry_price": 100.0, "notional": 5000.0},
                {"ticker": "BBB", "side": "BUY", "shares": 80, "entry_price": 50.0, "notional": 4000.0},
            ],
        },
    )
    price_rows = [
        {"date": "2026-06-01", "symbol": "AAA", "close": 100.0},
        {"date": "2026-06-01", "symbol": "BBB", "close": 50.0},
        {"date": "2026-06-02", "symbol": "AAA", "close": 110.0},
    ]
    if not missing_bbb_price:
        price_rows.append({"date": "2026-06-02", "symbol": "BBB", "close": 45.0})
    _write_csv(root / "outputs" / "prices" / "close_history.csv", price_rows)
    _write_csv(
        root / "outputs" / "perf" / "live_overlay_nav_series.csv",
        [
            {
                "date": "2026-06-01",
                "equity": 10000.0,
                "cash": 5000.0,
                "gross_exposure": 0.50,
                "net_exposure": 0.50,
                "return_1d": "",
                "turnover_dollars": "",
                "turnover_pct": "",
                "turnover": "",
            },
            {
                "date": "2026-06-02",
                "equity": 10050.0,
                "cash": 5000.0,
                "gross_exposure": 0.50,
                "net_exposure": 0.50,
                "return_1d": "",
                "turnover_dollars": "",
                "turnover_pct": "",
                "turnover": "",
            },
        ],
    )
    _write_json(
        root / "outputs" / "broker_snapshot" / "broker_snapshot_2026-06-02.json",
        {
            "trade_date": "2026-06-02",
            "portfolio_value": "10050.0",
            "cash": "5000.0",
            "positions_current": [
                {"symbol": "AAA", "qty": "20", "current_price": "110", "market_value": "2200"},
                {"symbol": "BBB", "qty": "30", "current_price": "45", "market_value": "1350"},
            ],
        },
    )
    benchmark_rows = [{"date": "2026-06-01", "spy_close": 100.0, "spy_return": ""}]
    if not missing_spy_end:
        benchmark_rows.append({"date": "2026-06-02", "spy_close": 101.0, "spy_return": ""})
    _write_csv(root / "outputs" / "perf" / "live_overlay_benchmark_close_history.csv", benchmark_rows)
    return root


def test_intended_nav_can_be_built_from_target_holdings_fixture(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)

    intended = build_intended_nav(trade_date=TRADE_DATE, repo_root=root, date_axis=["2026-06-01", "2026-06-02"])

    assert intended["available"] is True
    assert intended["intended_equity_value"] == 10100.0
    assert intended["intended_cash"] == 1000.0
    assert intended["intended_return_daily"] == 0.01
    assert {row["symbol"] for row in intended["intended_positions"]} == {"AAA", "BBB"}
    assert intended["plan_source"] == "daily_snapshot_target_weights"


def test_actual_nav_can_be_built_from_nav_and_broker_fixture(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)

    actual = build_actual_nav(trade_date=TRADE_DATE, repo_root=root)

    assert actual["available"] is True
    assert actual["actual_equity_value"] == 10050.0
    assert actual["actual_return_daily"] == 0.005
    assert actual["actual_gross_exposure"] == 0.5
    assert {row["symbol"] for row in actual["actual_positions"]} == {"AAA", "BBB"}


def test_price_panel_parquet_reader_succeeds_on_fixture(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    (root / "outputs" / "prices" / "close_history.csv").unlink()
    parquet_path = root / "outputs" / "research" / "flow_detection_v1" / "price_panel.parquet"
    _write_price_panel_parquet(
        parquet_path,
        [
            {"date": "2026-06-01", "ticker": "AAA", "close": 100.0},
            {"date": "2026-06-01", "ticker": "BBB", "close": 50.0},
            {"date": "2026-06-02", "ticker": "AAA", "close": 110.0},
            {"date": "2026-06-02", "ticker": "BBB", "close": 45.0},
        ],
    )

    intended = build_intended_nav(trade_date=TRADE_DATE, repo_root=root, date_axis=["2026-06-01", "2026-06-02"])

    assert intended["available"] is True
    assert intended["intended_equity_value"] == 10100.0
    assert str(parquet_path) in intended["source_diagnostics"]["price"]["selected_paths"]
    assert "price_history_missing" not in intended["reason_codes"]


def test_price_matrix_csv_reader_succeeds_on_fixture(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    (root / "outputs" / "prices" / "close_history.csv").unlink()
    matrix_path = root / "alpha_stack_cache" / "csv_export" / "prices_matrix.csv"
    _write_csv(
        matrix_path,
        [
            {"Date": "2026-06-01", "AAA": 100.0, "BBB": 50.0, "SPY": 100.0},
            {"Date": "2026-06-02", "AAA": 110.0, "BBB": 45.0, "SPY": 101.0},
        ],
    )

    intended = build_intended_nav(trade_date=TRADE_DATE, repo_root=root, date_axis=["2026-06-01", "2026-06-02"])

    assert intended["available"] is True
    assert intended["intended_equity_value"] == 10100.0
    assert str(matrix_path) in intended["source_diagnostics"]["price"]["selected_paths"]
    assert "price_history_missing" not in intended["reason_codes"]


def test_spy_price_is_found_from_price_panel(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    (root / "outputs" / "perf" / "live_overlay_benchmark_close_history.csv").unlink()
    parquet_path = root / "outputs" / "research" / "flow_detection_v1" / "price_panel.parquet"
    _write_price_panel_parquet(
        parquet_path,
        [
            {"date": "2026-06-01", "ticker": "SPY", "close": 100.0},
            {"date": "2026-06-02", "ticker": "SPY", "close": 101.0},
        ],
    )

    benchmark = build_benchmark_nav(
        trade_date=TRADE_DATE,
        repo_root=root,
        aligned_dates=["2026-06-01", "2026-06-02"],
    )

    assert benchmark["available"] is True
    assert benchmark["spy_price"] == 101.0
    assert str(parquet_path) in benchmark["source_artifacts"]
    assert "missing_spy_price:2026-06-02" not in benchmark["reason_codes"]


def test_spy_series_aligns_by_date_and_reports_missing_dates(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path, missing_spy_end=True)

    benchmark = build_benchmark_nav(
        trade_date=TRADE_DATE,
        repo_root=root,
        aligned_dates=["2026-06-01", "2026-06-02"],
    )

    assert benchmark["available"] is True
    assert [row["date"] for row in benchmark["timeseries"]] == ["2026-06-01"]
    assert "missing_spy_price:2026-06-02" in benchmark["reason_codes"]


def test_actual_positions_are_read_from_run_scoped_reconciliation(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    (root / "outputs" / "broker_snapshot" / "broker_snapshot_2026-06-02.json").unlink()
    recon_path = (
        root
        / "outputs"
        / "runs"
        / "2026-06-02T093504-0400_fixture"
        / "broker"
        / "recon_posttrade_2026-06-02.json"
    )
    _write_json(
        recon_path,
        {
            "trade_date": "2026-06-02",
            "broker_equity": 10050.0,
            "broker_cash": 5000.0,
            "verdict": "PASS",
            "drift_status": "OK_RECONCILED",
            "actual_positions": {"AAA": 20.0, "BBB": 30.0},
        },
    )

    actual = build_actual_nav(trade_date=TRADE_DATE, repo_root=root)

    assert {row["symbol"] for row in actual["actual_positions"]} == {"AAA", "BBB"}
    assert "actual_positions_from_reconciled_posttrade" in actual["reason_codes"]
    assert str(recon_path) in actual["source_diagnostics"]["positions"]["selected_paths"]


def test_operational_drag_equals_intended_minus_actual(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    analysis = build_operational_drag_analysis(trade_date=TRADE_DATE, repo_root=root, write=False)

    latest = analysis["operational_drag"]["latest"]

    assert latest["intended_return_daily"] == 0.01
    assert latest["actual_return_daily"] == 0.005
    assert latest["daily_operational_drag"] == 0.005
    assert latest["cumulative_operational_drag"] == 0.005


def test_source_selection_diagnostics_list_selected_paths(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    analysis = build_operational_drag_analysis(trade_date=TRADE_DATE, repo_root=root, write=False)

    diagnostics = analysis["operational_drag"]["source_diagnostics"]

    assert diagnostics["intended"]["price"]["selected_paths"]
    assert diagnostics["actual"]["nav"]["selected_paths"]
    assert diagnostics["actual"]["positions"]["selected_paths"]
    assert diagnostics["benchmark"]["benchmark"]["selected_paths"]


def test_operational_drag_confidence_improves_with_aligned_observations(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    analysis = build_operational_drag_analysis(trade_date=TRADE_DATE, repo_root=root, write=False)

    assert analysis["available"] is True
    assert analysis["confidence"] == "MEDIUM"
    assert "missing_spy_price:2026-06-02" not in analysis["reason_codes"]
    assert "price_history_missing" not in analysis["reason_codes"]


def test_underdeployment_cash_drag_is_classified(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    analysis = build_operational_drag_analysis(trade_date=TRADE_DATE, repo_root=root, write=False)

    categories = {row["category"] for row in analysis["operational_drag_attribution"]["attributions"]}

    assert "under_deployment_cash_drag" in categories
    assert analysis["operational_drag"]["latest"]["actual_underdeployment"] > 0.39


def test_missing_price_emits_reason_codes_instead_of_fake_nav(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path, missing_bbb_price=True)

    intended = build_intended_nav(trade_date=TRADE_DATE, repo_root=root, date_axis=["2026-06-01", "2026-06-02"])
    final_row = intended["timeseries"][-1]

    assert final_row["date"] == "2026-06-02"
    assert final_row["intended_equity_value"] is None
    assert final_row["missing_symbols"] == ["BBB"]
    assert "missing_price:BBB" in final_row["reason_codes"]


def test_stable_window_analysis_marks_unavailable_windows(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    analysis = build_operational_drag_analysis(trade_date=TRADE_DATE, repo_root=root, write=False)
    windows = {row["window"]: row for row in analysis["stable_window_analysis"]["windows"]}

    assert windows["since_2026_05_28"]["available"] is True
    assert windows["since_2026_05_28"]["operational_drag"] == 0.005
    assert windows["since_2026_04_15"]["available"] is True
    assert "window_start_missing_using_first_available" in windows["since_2026_04_15"]["reason_codes"]

    unavailable = build_stable_window_analysis(trade_date=TRADE_DATE, operational_drag={"timeseries": []})
    assert unavailable["available"] is False
    assert all(not row["available"] for row in unavailable["windows"])


def test_cli_writes_all_expected_artifacts(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    output_root = tmp_path / "drag_out"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "research.operational_drag",
            "--date",
            TRADE_DATE,
            "--repo-root",
            str(root),
            "--output-root",
            str(output_root),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    out_dir = output_root / TRADE_DATE
    for name in [
        "intended_nav.json",
        "intended_nav_timeseries.csv",
        "actual_nav.json",
        "actual_nav_timeseries.csv",
        "benchmark_nav.json",
        "operational_drag.json",
        "operational_drag_timeseries.csv",
        "operational_drag_attribution.json",
        "stable_window_analysis.json",
        "stable_window_analysis.md",
    ]:
        assert (out_dir / name).exists(), name


# ---------------------------------------------------------------------------
# FR-058 — Actual NAV Refresh for Operational Drag
# ---------------------------------------------------------------------------

_RUN_SNAPSHOT_FIELDS = ["date", "equity", "cash", "gross_exposure", "net_exposure"]


def _write_run_nav_snapshot(root: Path, run_id: str, rows: list[dict]) -> Path:
    path = root / "outputs" / "runs" / run_id / "snapshots" / "nav_timeseries.csv"
    _write_csv(path, [{field: row.get(field, "") for field in _RUN_SNAPSHOT_FIELDS} for row in rows])
    return path


def _single_day_live_overlay(root: Path) -> None:
    """Truncate the live-overlay NAV series so it ends before the trade date."""
    _write_csv(
        root / "outputs" / "perf" / "live_overlay_nav_series.csv",
        [{"date": "2026-06-01", "equity": 10000.0, "cash": 5000.0, "gross_exposure": 0.50, "net_exposure": 0.50}],
    )


def test_actual_nav_diagnostics_expose_actual_nav_block_and_provenance(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)

    actual = build_actual_nav(trade_date=TRADE_DATE, repo_root=root)

    diag = actual["source_diagnostics"]["actual_nav"]
    assert set(diag) >= {"candidate_paths", "selected_paths", "max_available_date", "failed_paths"}
    assert diag["max_available_date"] == TRADE_DATE
    assert actual["actual_nav_max_available_date"] == TRADE_DATE
    # Latest date is supplied by the broker-authoritative live-overlay series.
    assert "actual_nav_from_live_overlay" in actual["reason_codes"]
    # Series reaches the trade date, so it is not flagged stale.
    assert "actual_nav_stale" not in actual["reason_codes"]


def test_actual_nav_stale_reason_when_series_ends_before_trade_date(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    _single_day_live_overlay(root)

    actual = build_actual_nav(trade_date=TRADE_DATE, repo_root=root)

    assert actual["timeseries"][-1]["date"] == "2026-06-01"
    assert actual["source_diagnostics"]["actual_nav"]["max_available_date"] == "2026-06-01"
    assert "actual_nav_stale" in actual["reason_codes"]
    assert "actual_nav_from_live_overlay" in actual["reason_codes"]


def test_run_snapshot_extends_actual_nav_through_trade_date(tmp_path: Path) -> None:
    """Freshest-source selection: a run snapshot dated at the trade date extends
    coverage past a stale live-overlay series, eliminating actual_nav_stale and
    pushing operational-drag latest.date to the trade date."""
    root = _fixture_repo(tmp_path)
    _single_day_live_overlay(root)
    _write_run_nav_snapshot(
        root,
        "2026-06-02T120000-0400_freshrun",
        [
            {"date": "2026-06-01", "equity": 10000.0, "cash": 5000.0, "gross_exposure": 0.50, "net_exposure": 0.50},
            {"date": "2026-06-02", "equity": 10050.0, "cash": 5000.0, "gross_exposure": 0.50, "net_exposure": 0.50},
        ],
    )

    actual = build_actual_nav(trade_date=TRADE_DATE, repo_root=root)
    assert actual["timeseries"][-1]["date"] == TRADE_DATE
    assert actual["source_diagnostics"]["actual_nav"]["max_available_date"] == TRADE_DATE
    assert "actual_nav_stale" not in actual["reason_codes"]
    assert "actual_nav_from_run_snapshot" in actual["reason_codes"]

    analysis = build_operational_drag_analysis(trade_date=TRADE_DATE, repo_root=root, write=False)
    assert analysis["operational_drag"]["latest"]["date"] == TRADE_DATE


def test_higher_confidence_source_wins_same_date_conflict(tmp_path: Path) -> None:
    """When live-overlay and a run snapshot both cover a date, the
    broker-authoritative live-overlay value wins (not first-file-found)."""
    root = _fixture_repo(tmp_path)  # live-overlay has 2026-06-02 equity = 10050
    _write_run_nav_snapshot(
        root,
        "2026-06-02T120000-0400_conflict",
        [{"date": "2026-06-02", "equity": 9999.0, "cash": 5000.0, "gross_exposure": 0.50, "net_exposure": 0.50}],
    )

    actual = build_actual_nav(trade_date=TRADE_DATE, repo_root=root)
    trade_row = next(row for row in actual["timeseries"] if row["date"] == TRADE_DATE)
    assert trade_row["actual_equity_value"] == 10050.0
    assert trade_row["broker_source"].endswith("live_overlay_nav_series.csv")


def test_actual_nav_missing_reason_when_no_source(tmp_path: Path) -> None:
    empty_root = tmp_path / "empty"
    empty_root.mkdir()

    actual = build_actual_nav(trade_date=TRADE_DATE, repo_root=empty_root)

    assert actual["available"] is False
    assert "actual_nav_missing" in actual["reason_codes"]
    assert "actual_nav" in actual["source_diagnostics"]
    assert actual["source_diagnostics"]["actual_nav"]["max_available_date"] is None
