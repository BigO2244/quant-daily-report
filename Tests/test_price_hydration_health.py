import json
import subprocess
import sys
from pathlib import Path

from scripts.research.check_price_hydration_health import inspect_hydration_health, render_markdown


TRADE_DATE = "2026-05-26"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_shadow_date(root: Path, trade_date: str = TRADE_DATE) -> None:
    (root / "outputs" / "shadow_candidates" / trade_date).mkdir(parents=True, exist_ok=True)


def _write_hydration(root: Path, payload: dict, trade_date: str = TRADE_DATE) -> None:
    _write_json(root / "outputs" / "price_hydration" / trade_date / "status.json", payload)


def _run_cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.research.check_price_hydration_health",
            "--repo-root",
            str(root),
            *args,
        ],
        check=False,
        text=True,
        capture_output=True,
    )


def test_healthy_hydration_reports_ready(tmp_path):
    _write_shadow_date(tmp_path)
    _write_hydration(
        tmp_path,
        {
            "status": "OK",
            "hydrated_at": "2026-05-26T22:30:00Z",
            "max_cache_date": TRADE_DATE,
            "symbols_expected": ["AAPL", "MSFT"],
            "symbols_present": ["AAPL", "MSFT"],
        },
    )

    payload = inspect_hydration_health(repo_root=tmp_path, trade_date=TRADE_DATE)

    assert payload["hydration_status"] == "OK"
    assert payload["hydration_interpretation"] == "ready"
    assert payload["partial_or_complete"] == "COMPLETE"
    assert payload["stale_days"] == 0
    assert payload["symbols_missing_count"] == 0


def test_stale_hydration_reports_stale_days(tmp_path):
    _write_shadow_date(tmp_path)
    _write_hydration(
        tmp_path,
        {
            "status": "OK",
            "max_cache_date": "2026-05-24",
            "symbols_expected": ["AAPL"],
            "symbols_present": ["AAPL"],
        },
    )

    payload = inspect_hydration_health(repo_root=tmp_path, trade_date=TRADE_DATE)

    assert payload["hydration_interpretation"] == "stale_but_recoverable"
    assert payload["stale_days"] == 2
    assert "approved post-close hydration" in payload["recommended_next_action"]


def test_partial_hydration_reports_missing_symbols(tmp_path):
    _write_shadow_date(tmp_path)
    _write_hydration(
        tmp_path,
        {
            "status": "PARTIAL",
            "max_cache_date": TRADE_DATE,
            "symbols_expected": ["AAPL", "MSFT", "NVDA"],
            "symbols_present": ["AAPL"],
        },
    )

    payload = inspect_hydration_health(repo_root=tmp_path, trade_date=TRADE_DATE)

    assert payload["hydration_interpretation"] == "partial"
    assert payload["partial_or_complete"] == "PARTIAL"
    assert payload["symbols_expected"] == 3
    assert payload["symbols_present"] == 1
    assert payload["symbols_missing_count"] == 2
    assert payload["missing_symbols_sample"] == ["MSFT", "NVDA"]


def test_missing_hydration_artifact_uses_last_successful_context(tmp_path):
    _write_shadow_date(tmp_path)
    _write_hydration(
        tmp_path,
        {
            "status": "OK",
            "hydrated_at": "2026-05-24T22:30:00Z",
            "max_cache_date": "2026-05-24",
        },
        trade_date="2026-05-24",
    )

    payload = inspect_hydration_health(repo_root=tmp_path, trade_date=TRADE_DATE)

    assert payload["hydration_status"] == "MISSING"
    assert payload["partial_or_complete"] == "MISSING"
    assert payload["hydration_interpretation"] == "stale_but_recoverable"
    assert payload["stale_days"] == 2
    assert payload["last_successful_hydration"]["trade_date"] == "2026-05-24"


def test_markdown_and_json_output_include_hydration_fields(tmp_path):
    _write_shadow_date(tmp_path)
    _write_hydration(
        tmp_path,
        {
            "status": "PARTIAL",
            "max_cache_date": TRADE_DATE,
            "missing_symbols": ["MSFT"],
        },
    )

    payload = inspect_hydration_health(repo_root=tmp_path, trade_date=TRADE_DATE)
    markdown = render_markdown(payload)
    result = _run_cli(tmp_path, "--trade-date", TRADE_DATE, "--json")
    json_payload = json.loads(result.stdout)

    assert "Price Hydration Health" in markdown
    assert "MSFT" in markdown
    assert json_payload["hydration_interpretation"] == "partial"
    assert json_payload["symbols_missing_count"] == 1


def test_strict_mode_exits_nonzero_when_not_ready(tmp_path):
    _write_shadow_date(tmp_path)

    result = _run_cli(tmp_path, "--trade-date", TRADE_DATE, "--json", "--strict")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["hydration_status"] == "MISSING"
    assert payload["hydration_interpretation"] in {"waiting_for_post_close", "stale_but_recoverable"}

