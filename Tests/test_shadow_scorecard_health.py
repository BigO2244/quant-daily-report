from __future__ import annotations

import json
import shutil
from pathlib import Path

from paper.trading_calendar import prev_trading_day
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
    prior_date = prev_trading_day(latest_date)
    rows = [
        "date,caerus_polaris,caerus_orion,caerus_lyra,spy_benchmark",
        f"{prior_date},1.10,1.20,1.30,1.05",
        f"{latest_date},1.20,1.25,1.30,1.05",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_dated_artifacts(
    root: Path,
    trade_date: str,
    *,
    scorecard_status: str = "OK",
    data_status: str = "OK",
    data_reason: str | None = None,
    valid_days: int = 17,
) -> None:
    dated = root / "outputs" / "shadow_candidates" / trade_date
    evaluation = _evaluation(
        trade_date=trade_date,
        valid_days=valid_days,
        status=scorecard_status,
        data_status=data_status,
        data_reason=data_reason,
    )
    comparison = {"trade_date": trade_date, "status": "OK"}
    if data_reason:
        comparison = {"trade_date": trade_date, "status": "NO_DATA", "reason_code": data_reason}
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


def _post_baseline_check(payload: dict) -> dict:
    return next(check for check in payload["checks"] if check["name"] == "no_post_baseline_bad_reasons")


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


def test_shadow_scorecard_health_simultaneous_scale_reset_is_corrupt(tmp_path: Path) -> None:
    _write_artifacts(tmp_path)
    nav_path = tmp_path / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv"
    nav_path.write_text(
        "\n".join(
            [
                "date,caerus_polaris,caerus_orion,caerus_lyra,spy_benchmark",
                "2026-05-11,38.0,170.0,171.0,5.0",
                "2026-05-12,1.2,1.25,1.3,1.05",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    payload = build_health_payload(
        repo_root=tmp_path,
        baseline_date="2026-05-11",
        baseline_valid_days=16,
        expected_date="2026-05-12",
        strict=True,
    )

    assert payload["status"] == "FAIL"
    assert payload["performance_integrity"]["status"] == "CORRUPT"
    assert payload["performance_integrity"]["reason_code"] == "SHADOW_NAV_CHAIN_RESET"
    assert any(check["name"] == "performance_integrity_valid" and not check["passed"] for check in payload["checks"])


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


def test_shadow_scorecard_health_skips_memorial_day_2026(tmp_path: Path) -> None:
    _write_artifacts(tmp_path, trade_date="2026-05-26", valid_days=17, max_cache_date="2026-05-26")
    _write_dated_artifacts(
        tmp_path,
        "2026-05-25",
        data_status="NO_DATA",
        data_reason="PRICE_CACHE_STALE",
        valid_days=16,
    )

    payload = build_health_payload(
        repo_root=tmp_path,
        baseline_date="2026-05-22",
        baseline_valid_days=16,
        expected_date="2026-05-26",
        strict=True,
    )

    assert payload["status"] == "OK"
    assert payload["post_baseline_issues"] == []
    assert {"date": "2026-05-25", "reason": "EXPECTED_NON_TRADING_DATE"} in payload["post_baseline_non_trading_dates"]
    assert _post_baseline_check(payload)["passed"] is True


def test_shadow_scorecard_health_adjacent_trading_session_stale_data_fails(tmp_path: Path) -> None:
    _write_artifacts(tmp_path, trade_date="2026-05-26", valid_days=18, max_cache_date="2026-05-26")
    _write_dated_artifacts(
        tmp_path,
        "2026-05-22",
        data_status="NO_DATA",
        data_reason="PRICE_CACHE_STALE",
        valid_days=16,
    )

    payload = build_health_payload(
        repo_root=tmp_path,
        baseline_date="2026-05-21",
        baseline_valid_days=16,
        expected_date="2026-05-26",
        strict=True,
    )

    assert payload["status"] == "FAIL"
    assert any(issue["date"] == "2026-05-22" and "PRICE_CACHE_STALE" in issue["reason"] for issue in payload["post_baseline_issues"])
    assert not any(issue["date"] == "2026-05-25" for issue in payload["post_baseline_issues"])


def test_shadow_scorecard_health_adjacent_trading_session_missing_dir_fails(tmp_path: Path) -> None:
    _write_artifacts(tmp_path, trade_date="2026-05-26", valid_days=18, max_cache_date="2026-05-26")
    shutil.rmtree(tmp_path / "outputs" / "shadow_candidates" / "2026-05-26")

    payload = build_health_payload(
        repo_root=tmp_path,
        baseline_date="2026-05-22",
        baseline_valid_days=16,
        expected_date="2026-05-26",
        strict=True,
    )

    assert payload["status"] == "FAIL"
    assert {"date": "2026-05-26", "reason": "missing-shadow-dir"} in payload["post_baseline_issues"]


def test_shadow_scorecard_health_skips_good_friday_weekday(tmp_path: Path) -> None:
    _write_artifacts(tmp_path, trade_date="2026-04-06", valid_days=17, max_cache_date="2026-04-06")
    _write_dated_artifacts(
        tmp_path,
        "2026-04-03",
        data_status="NO_DATA",
        data_reason="PRICE_CACHE_STALE",
        valid_days=16,
    )

    payload = build_health_payload(
        repo_root=tmp_path,
        baseline_date="2026-04-02",
        baseline_valid_days=16,
        expected_date="2026-04-06",
        strict=True,
    )

    assert payload["status"] == "OK"
    assert {"date": "2026-04-03", "reason": "EXPECTED_NON_TRADING_DATE"} in payload["post_baseline_non_trading_dates"]
    assert payload["post_baseline_issues"] == []


def test_shadow_scorecard_health_output_classification_is_deterministic(tmp_path: Path) -> None:
    _write_artifacts(tmp_path, trade_date="2026-05-26", valid_days=17, max_cache_date="2026-05-26")
    _write_dated_artifacts(tmp_path, "2026-05-25", data_status="NO_DATA", data_reason="PRICE_CACHE_STALE")

    kwargs = {
        "repo_root": tmp_path,
        "baseline_date": "2026-05-22",
        "baseline_valid_days": 16,
        "expected_date": "2026-05-26",
        "strict": True,
    }
    first = build_health_payload(**kwargs)
    second = build_health_payload(**kwargs)

    assert first["post_baseline_issues"] == second["post_baseline_issues"]
    assert first["post_baseline_non_trading_dates"] == second["post_baseline_non_trading_dates"]
    assert _post_baseline_check(first) == _post_baseline_check(second)


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
