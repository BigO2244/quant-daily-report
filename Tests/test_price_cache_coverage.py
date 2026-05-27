from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from scripts.research.check_price_cache_coverage import inspect_price_cache_coverage, render_markdown


def _write_universe(path: Path, symbols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ticker\n" + "\n".join(symbols) + "\n", encoding="utf-8")


def _write_panel(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def test_ready_cache_coverage_reports_no_missing_or_stale_symbols(tmp_path: Path) -> None:
    _write_universe(tmp_path / "data" / "universe.csv", ["AAPL", "MSFT"])
    _write_panel(
        tmp_path / "outputs" / "research" / "flow_detection_v1" / "price_panel.parquet",
        [
            {"date": "2026-05-26", "ticker": "AAPL", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 100},
            {"date": "2026-05-26", "ticker": "MSFT", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 100},
        ],
    )

    payload = inspect_price_cache_coverage(repo_root=tmp_path, trade_date="2026-05-26")

    assert payload["coverage_status"] == "READY"
    assert payload["symbols_missing_count"] == 0
    assert payload["stale_symbols_count"] == 0
    assert payload["runtime_effect"] == "none"
    assert payload["sidecar_status"] == "advisory_preview_only"


def test_missing_and_stale_symbols_report_incomplete(tmp_path: Path) -> None:
    _write_universe(tmp_path / "data" / "universe.csv", ["AAPL", "MSFT", "NVDA"])
    _write_panel(
        tmp_path / "outputs" / "research" / "flow_detection_v1" / "price_panel.parquet",
        [
            {"date": "2026-05-24", "ticker": "AAPL", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 100},
            {"date": "2026-05-26", "ticker": "MSFT", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 100},
        ],
    )

    payload = inspect_price_cache_coverage(repo_root=tmp_path, trade_date="2026-05-26")

    assert payload["coverage_status"] == "INCOMPLETE"
    assert payload["symbols_missing_sample"] == ["NVDA"]
    assert payload["stale_symbols_sample"] == ["AAPL"]


def test_missing_cache_is_missing_without_writing_sidecar(tmp_path: Path) -> None:
    _write_universe(tmp_path / "data" / "universe.csv", ["AAPL", "MSFT"])

    payload = inspect_price_cache_coverage(repo_root=tmp_path, trade_date="2026-05-26")

    assert payload["coverage_status"] == "MISSING"
    assert payload["cache_exists"] is False
    assert payload["symbols_missing_count"] == 2
    assert not (tmp_path / "outputs" / "research" / "flow_detection_v1").exists()


def test_ignored_tickers_are_excluded_from_expected_symbols(tmp_path: Path) -> None:
    _write_universe(tmp_path / "data" / "universe.csv", ["AAPL", "BRK.B"])
    (tmp_path / "data" / "ticker_exceptions.json").write_text(
        json.dumps({"ignore": ["BRK.B"], "aliases": {}, "notes": {}}),
        encoding="utf-8",
    )
    _write_panel(
        tmp_path / "outputs" / "research" / "flow_detection_v1" / "price_panel.parquet",
        [{"date": "2026-05-26", "ticker": "AAPL", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 100}],
    )

    payload = inspect_price_cache_coverage(repo_root=tmp_path, trade_date="2026-05-26")

    assert payload["coverage_status"] == "READY"
    assert payload["expected_symbols"] == 1
    assert payload["ignored_tickers"] == ["BRK.B"]


def test_markdown_and_strict_mode(tmp_path: Path) -> None:
    _write_universe(tmp_path / "data" / "universe.csv", ["AAPL"])
    payload = inspect_price_cache_coverage(repo_root=tmp_path, trade_date="2026-05-26")
    markdown = render_markdown(payload)

    assert "Price Cache Coverage" in markdown
    assert "advisory_preview_only" in markdown

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.research.check_price_cache_coverage",
            "--repo-root",
            str(tmp_path),
            "--trade-date",
            "2026-05-26",
            "--strict",
            "--json",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert json.loads(result.stdout)["coverage_status"] == "MISSING"
