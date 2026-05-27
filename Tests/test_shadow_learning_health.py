from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.research.check_shadow_learning_health import inspect_shadow_learning_health, render_markdown


TRADE_DATE = "2026-05-04"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_learning_fixture(root: Path, *, missing_required: bool = False, stale: bool = False) -> None:
    dated = root / "outputs" / "shadow_candidates" / TRADE_DATE
    if not missing_required:
        _write_json(
            dated / "shadow_evaluation.json",
            {
                "trade_date": TRADE_DATE,
                "strategies": {
                    "caerus_polaris": {
                        "data_status": "NO_DATA" if stale else "OK",
                        "data_reason": "PRICE_CACHE_STALE" if stale else None,
                        "daily_return": 0.01,
                    },
                    "caerus_orion": {"data_status": "OK", "daily_return": 0.02},
                    "caerus_lyra": {"data_status": "OK", "daily_return": -0.01},
                },
            },
        )
    _write_json(
        dated / "comparison.json",
        {
            "trade_date": TRADE_DATE,
            "status": "NO_DATA" if stale else "OK",
            "reason_code": "PRICE_CACHE_STALE" if stale else None,
            "strategies": {},
        },
    )
    (dated / "comparison.md").write_text("# Comparison\n", encoding="utf-8")
    _write_json(
        dated / "feedback_loop_summary.json",
        {
            "trade_date": TRADE_DATE,
            "status": "PARTIAL",
            "strategies": {
                "polaris": {"learning_readiness": "HIGH", "primary_learning_gap": "none"},
                "orion": {"learning_readiness": "MEDIUM", "primary_learning_gap": "partial attribution"},
                "lyra": {"learning_readiness": "LOW", "primary_learning_gap": "insufficient history"},
            },
        },
    )
    for short in ("polaris", "orion", "lyra"):
        strategy_dir = dated / short
        for name in ("decision_trace.json", "attribution.json", "stability_analysis.json", "regime_performance.json"):
            _write_json(strategy_dir / name, {"status": "OK"})


def test_ready_learning_health_reports_low_strategy_readiness(tmp_path: Path) -> None:
    _write_learning_fixture(tmp_path)

    payload = inspect_shadow_learning_health(repo_root=tmp_path, trade_date=TRADE_DATE)

    assert payload["learning_health"] == "READY"
    assert payload["status"] == "OK"
    assert payload["required_missing"] == []
    assert payload["low_learning_readiness_strategies"] == ["Lyra"]
    assert payload["runtime_effect"] == "none"


def test_missing_required_artifact_is_incomplete(tmp_path: Path) -> None:
    _write_learning_fixture(tmp_path, missing_required=True)

    payload = inspect_shadow_learning_health(repo_root=tmp_path, trade_date=TRADE_DATE)

    assert payload["learning_health"] == "INCOMPLETE"
    assert "shadow_evaluation.json" in payload["required_missing"]
    assert "required learning source artifacts missing" in payload["blocking_reasons"]


def test_stale_learning_artifacts_report_hydration_next_action(tmp_path: Path) -> None:
    _write_learning_fixture(tmp_path, stale=True)

    payload = inspect_shadow_learning_health(repo_root=tmp_path, trade_date=TRADE_DATE)
    markdown = render_markdown(payload)

    assert payload["learning_health"] == "INCOMPLETE"
    assert payload["stale_reasons"]
    assert "Refresh post-close shadow artifacts" in payload["recommended_next_action"]
    assert "Shadow Learning Health" in markdown
    assert "PRICE_CACHE_STALE" not in markdown


def test_latest_resolves_latest_shadow_candidate_date(tmp_path: Path) -> None:
    _write_learning_fixture(tmp_path)
    _write_json(tmp_path / "outputs" / "shadow_candidates" / "2026-05-01" / "comparison.json", {"status": "OK"})

    payload = inspect_shadow_learning_health(repo_root=tmp_path, latest=True)

    assert payload["trade_date"] == TRADE_DATE


def test_strict_mode_exits_nonzero_when_incomplete(tmp_path: Path) -> None:
    _write_learning_fixture(tmp_path, missing_required=True)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.research.check_shadow_learning_health",
            "--repo-root",
            str(tmp_path),
            "--trade-date",
            TRADE_DATE,
            "--strict",
            "--json",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["learning_health"] == "INCOMPLETE"
