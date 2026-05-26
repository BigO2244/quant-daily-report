import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from scripts.research.check_research_source_readiness import inspect_source_readiness, render_markdown


TRADE_DATE = "2026-05-26"
BEFORE_HYDRATION = datetime.fromisoformat("2026-05-26T15:46:00-04:00")
AFTER_HYDRATION = datetime.fromisoformat("2026-05-26T18:31:00-04:00")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_ready_fixture(root: Path, *, trade_date: str = TRADE_DATE) -> None:
    shadow_dir = root / "outputs" / "shadow_candidates" / trade_date
    _write_json(
        shadow_dir / "shadow_performance.json",
        {
            "trade_date": trade_date,
            "data_status": "OK",
            "data_reason": "OK",
        },
    )
    _write_json(
        shadow_dir / "comparison.json",
        {
            "trade_date": trade_date,
            "status": "OK",
            "strategies": {
                "caerus_polaris": {"nav": 1.0},
                "caerus_orion": {"nav": 1.1},
            },
        },
    )
    _write_json(
        root / "outputs" / "price_hydration" / trade_date / "status.json",
        {
            "trade_date": trade_date,
            "status": "OK",
            "max_cache_date": trade_date,
        },
    )


def _run_cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.research.check_research_source_readiness",
            "--repo-root",
            str(root),
            *args,
        ],
        check=False,
        text=True,
        capture_output=True,
    )


def test_ready_source_reports_ready(tmp_path):
    _write_ready_fixture(tmp_path)

    payload = inspect_source_readiness(repo_root=tmp_path, trade_date=TRADE_DATE, now=AFTER_HYDRATION)

    assert payload["source_readiness"] == "READY"
    assert payload["shadow_data_status"] == "OK"
    assert payload["comparison_status"] == "OK"
    assert payload["strategy_count"] == 2
    assert payload["price_hydration_status"] == "OK"
    assert payload["hydration_state_classification"] == "healthy"
    assert payload["blocking_reasons"] == []


def test_price_cache_stale_source_reports_incomplete(tmp_path):
    _write_ready_fixture(tmp_path)
    _write_json(
        tmp_path / "outputs" / "shadow_candidates" / TRADE_DATE / "shadow_performance.json",
        {
            "trade_date": TRADE_DATE,
            "data_status": "NO_DATA",
            "data_reason": "PRICE_CACHE_STALE",
        },
    )

    payload = inspect_source_readiness(repo_root=tmp_path, trade_date=TRADE_DATE, now=BEFORE_HYDRATION)

    assert payload["source_readiness"] == "INCOMPLETE"
    assert payload["shadow_data_status"] == "NO_DATA"
    assert payload["shadow_data_reason"] == "PRICE_CACHE_STALE"
    assert "shadow_performance.data_reason=PRICE_CACHE_STALE" in payload["blocking_reasons"]
    assert "post-close hydration" in payload["recommended_next_action"]


def test_missing_price_hydration_status_reports_incomplete(tmp_path):
    _write_ready_fixture(tmp_path)
    hydration_path = tmp_path / "outputs" / "price_hydration" / TRADE_DATE / "status.json"
    hydration_path.unlink()

    payload = inspect_source_readiness(repo_root=tmp_path, trade_date=TRADE_DATE, now=BEFORE_HYDRATION)

    assert payload["source_readiness"] == "INCOMPLETE"
    assert payload["price_hydration_status"] == "MISSING"
    assert payload["cache_lag_interpretation"] == "waiting_for_post_close"
    assert "missing price hydration status" in payload["blocking_reasons"]


def test_source_readiness_explains_waiting_for_post_close(tmp_path):
    _write_ready_fixture(tmp_path)
    (tmp_path / "outputs" / "price_hydration" / TRADE_DATE / "status.json").unlink()
    _write_json(
        tmp_path / "outputs" / "price_hydration" / "2026-05-22" / "status.json",
        {
            "trade_date": "2026-05-22",
            "status": "OK",
            "max_cache_date": "2026-05-22",
        },
    )
    _write_json(
        tmp_path / "outputs" / "shadow_candidates" / TRADE_DATE / "shadow_performance.json",
        {
            "trade_date": TRADE_DATE,
            "data_status": "NO_DATA",
            "data_reason": "PRICE_CACHE_STALE",
        },
    )

    payload = inspect_source_readiness(repo_root=tmp_path, trade_date=TRADE_DATE, now=BEFORE_HYDRATION)

    assert payload["source_readiness"] == "INCOMPLETE"
    assert payload["hydration_state_classification"] == "waiting_for_post_close"
    assert payload["hydration_window_passed"] is False
    assert "not occurred yet" in payload["readiness_explanation"]
    assert "18:30 ET" in payload["recommended_next_action"]


def test_source_readiness_explains_stale_after_hydration_window(tmp_path):
    _write_ready_fixture(tmp_path)
    (tmp_path / "outputs" / "price_hydration" / TRADE_DATE / "status.json").unlink()
    _write_json(
        tmp_path / "outputs" / "price_hydration" / "2026-05-22" / "status.json",
        {
            "trade_date": "2026-05-22",
            "status": "OK",
            "max_cache_date": "2026-05-22",
        },
    )

    payload = inspect_source_readiness(repo_root=tmp_path, trade_date=TRADE_DATE, now=AFTER_HYDRATION)

    assert payload["hydration_state_classification"] == "stale_but_recoverable"
    assert payload["hydration_window_passed"] is True
    assert "stale, missing, or partial" in payload["readiness_explanation"]


def test_comparison_no_data_reports_incomplete(tmp_path):
    _write_ready_fixture(tmp_path)
    _write_json(
        tmp_path / "outputs" / "shadow_candidates" / TRADE_DATE / "comparison.json",
        {
            "trade_date": TRADE_DATE,
            "status": "NO_DATA",
            "strategies": {"caerus_polaris": {"nav": 1.0}},
        },
    )

    payload = inspect_source_readiness(repo_root=tmp_path, trade_date=TRADE_DATE)

    assert payload["source_readiness"] == "INCOMPLETE"
    assert payload["comparison_status"] == "NO_DATA"
    assert "comparison.status=NO_DATA" in payload["blocking_reasons"]


def test_empty_comparison_strategies_reports_incomplete(tmp_path):
    _write_ready_fixture(tmp_path)
    _write_json(
        tmp_path / "outputs" / "shadow_candidates" / TRADE_DATE / "comparison.json",
        {
            "trade_date": TRADE_DATE,
            "status": "OK",
            "strategies": {},
        },
    )

    payload = inspect_source_readiness(repo_root=tmp_path, trade_date=TRADE_DATE)

    assert payload["source_readiness"] == "INCOMPLETE"
    assert payload["strategy_count"] == 0
    assert "comparison.strategies is empty" in payload["blocking_reasons"]


def test_strict_exits_nonzero_when_incomplete(tmp_path):
    _write_ready_fixture(tmp_path)
    _write_json(
        tmp_path / "outputs" / "shadow_candidates" / TRADE_DATE / "shadow_performance.json",
        {
            "trade_date": TRADE_DATE,
            "data_status": "NO_DATA",
            "data_reason": "PRICE_CACHE_STALE",
        },
    )

    result = _run_cli(tmp_path, "--trade-date", TRADE_DATE, "--json", "--strict")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["source_readiness"] == "INCOMPLETE"
    assert "shadow_performance.data_status=NO_DATA" in payload["blocking_reasons"]


def test_markdown_and_json_include_blocking_reasons(tmp_path):
    _write_ready_fixture(tmp_path)
    (tmp_path / "outputs" / "price_hydration" / TRADE_DATE / "status.json").unlink()

    payload = inspect_source_readiness(repo_root=tmp_path, trade_date=TRADE_DATE)
    markdown = render_markdown(payload)
    result = _run_cli(tmp_path, "--trade-date", TRADE_DATE, "--json")
    json_payload = json.loads(result.stdout)

    assert "## Blocking Reasons" in markdown
    assert "Hydration classification" in markdown
    assert "missing price hydration status" in markdown
    assert json_payload["blocking_reasons"] == ["missing price hydration status"]
    assert "hydration_health" in json_payload
