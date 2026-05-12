from __future__ import annotations

import json
from pathlib import Path

from scripts.check_shadow_scorecard_health import build_health_payload, main


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _evaluation(*, trade_date: str = "2026-05-12", valid_days: int = 17, status: str = "OK", data_status: str = "OK", data_reason: str | None = None) -> dict:
    return {
        "trade_date": trade_date,
        "benchmark_symbol": "SPY",
        "strategies": {
            slug: {
                "strategy_name": name,
                "status": status,
                "data_status": data_status,
                "data_reason": data_reason,
                "daily_return": 0.01,
                "cumulative_return": cumulative,
                "excess_return_vs_spy": 0.0 if slug == "spy_benchmark" else cumulative - 0.05,
                "rolling_count_of_valid_days": valid_days,
            }
            for slug, name, cumulative in [
                ("caerus_polaris", "Caerus Polaris", 0.20),
                ("caerus_orion", "Caerus Orion", 0.25),
                ("caerus_lyra", "Caerus Lyra", 0.30),
                ("spy_benchmark", "SPY", 0.05),
            ]
        },
    }


def _write_nav(path: Path, *, latest_date: str = "2026-05-12") -> None:
    rows = [
        "date,caerus_polaris,caerus_orion,caerus_lyra,spy_benchmark",
        "2026-05-11,1.10,1.20,1.30,1.05",
    ]
    if latest_date >= "2026-05-12":
        rows.append("2026-05-12,1.20,1.25,1.30,1.05")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_artifacts(
    root: Path,
    *,
    trade_date: str = "2026-05-12",
    valid_days: int = 17,
    scorecard_status: str = "OK",
    data_status: str = "OK",
    data_reason: str | None = None,
    shadow_refresh_status: str = "OK",
    shadow_refresh_reason: str | None = None,
    max_cache_date: str = "2026-05-12",
) -> None:
    latest = root / "outputs" / "shadow_candidates" / "latest"
    evaluation = _evaluation(
        trade_date=trade_date,
        valid_days=valid_days,
        status=scorecard_status,
        data_status=data_status,
        data_reason=data_reason,
    )
    _write_json(latest / "shadow_evaluation.json", evaluation)
    comparison = {"trade_date": trade_date, "status": "OK"}
    if data_reason:
        comparison = {"trade_date": trade_date, "status": "NO_DATA", "reason_code": data_reason}
    _write_json(latest / "comparison.json", comparison)
    _write_nav(root / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv", latest_date=trade_date)
    dated = root / "outputs" / "shadow_candidates" / trade_date
    _write_json(dated / "shadow_evaluation.json", evaluation)
    _write_json(
        dated / "shadow_performance.json",
        {
            "trade_date": trade_date,
            "status": scorecard_status,
            "data_status": data_status,
            "data_reason": data_reason,
            "strategies": {slug: {"nav": 1.0, "daily_return": 0.01} for slug in ("caerus_polaris", "caerus_orion", "caerus_lyra", "spy_benchmark")},
        },
    )
    _write_json(dated / "comparison.json", comparison)
    _write_json(
        root / "outputs" / "price_hydration" / trade_date / "status.json",
        {
            "status": "OK",
            "max_cache_date": max_cache_date,
            "shadow_refresh": {
                "status": shadow_refresh_status,
                "reason": shadow_refresh_reason,
            },
        },
    )


def test_shadow_scorecard_health_fresh_passes_strict(tmp_path: Path) -> None:
    _write_artifacts(tmp_path)

    payload = build_health_payload(
        repo_root=tmp_path,
        baseline_date="2026-05-11",
        baseline_valid_days=16,
        expected_date="2026-05-12",
        strict=True,
    )

    assert payload["status"] == "OK"
    assert payload["valid_days"]["caerus_orion"] == 17
    assert payload["data_through_date"] == "2026-05-12"


def test_shadow_scorecard_health_stale_fails_strict(tmp_path: Path) -> None:
    _write_artifacts(tmp_path, data_status="NO_DATA", data_reason="PRICE_CACHE_STALE", valid_days=16)

    payload = build_health_payload(
        repo_root=tmp_path,
        baseline_date="2026-05-11",
        baseline_valid_days=16,
        expected_date="2026-05-12",
        strict=True,
    )

    assert payload["status"] == "FAIL"
    assert any(check["name"] == "scorecard_fresh" and not check["passed"] for check in payload["checks"])
    assert any("PRICE_CACHE_STALE" in issue["reason"] for issue in payload["post_baseline_issues"])


def test_shadow_scorecard_health_valid_day_regression_fails(tmp_path: Path) -> None:
    _write_artifacts(tmp_path, valid_days=15)

    payload = build_health_payload(
        repo_root=tmp_path,
        baseline_date="2026-05-11",
        baseline_valid_days=16,
        expected_date="2026-05-12",
        strict=True,
    )

    assert payload["status"] == "FAIL"
    assert any(check["name"] == "valid_days_advanced" and not check["passed"] for check in payload["checks"])


def test_shadow_scorecard_health_equal_baseline_ok_when_no_new_date_expected(tmp_path: Path) -> None:
    _write_artifacts(tmp_path, trade_date="2026-05-11", valid_days=16, max_cache_date="2026-05-11")

    payload = build_health_payload(
        repo_root=tmp_path,
        baseline_date="2026-05-11",
        baseline_valid_days=16,
        expected_date="2026-05-11",
        strict=True,
    )

    assert payload["status"] == "OK"
    assert any(check["name"] == "valid_days_at_or_above_baseline" and check["passed"] for check in payload["checks"])


def test_shadow_scorecard_health_shadow_refresh_failure_fails(tmp_path: Path) -> None:
    _write_artifacts(tmp_path, shadow_refresh_status="FAILED", shadow_refresh_reason="NO_PRIOR")

    payload = build_health_payload(
        repo_root=tmp_path,
        baseline_date="2026-05-11",
        baseline_valid_days=16,
        expected_date="2026-05-12",
        strict=True,
    )

    assert payload["status"] == "FAIL"
    assert any(check["name"] == "shadow_refresh_ok" and not check["passed"] for check in payload["checks"])


def test_shadow_scorecard_health_main_writes_artifacts(tmp_path: Path) -> None:
    _write_artifacts(tmp_path)

    rc = main(
        [
            "--repo-root",
            str(tmp_path),
            "--baseline-date",
            "2026-05-11",
            "--baseline-valid-days",
            "16",
            "--expected-date",
            "2026-05-12",
            "--strict",
        ]
    )

    assert rc == 0
    assert list((tmp_path / "outputs" / "diagnostics").glob("shadow_scorecard_health_*.json"))
    assert list((tmp_path / "outputs" / "diagnostics").glob("shadow_scorecard_health_*.md"))
