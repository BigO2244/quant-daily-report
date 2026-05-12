from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_shadow_promotion_readiness import build_promotion_audit, main


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _strategy(
    *,
    cumulative: float,
    excess: float,
    valid_days: int,
    status: str = "OK",
    data_status: str = "OK",
    data_reason: str | None = None,
    max_drawdown: float = -0.02,
    volatility: float = 0.15,
    turnover: float = 0.10,
    concentration: float = 0.40,
) -> dict:
    return {
        "status": status,
        "data_status": data_status,
        "data_reason": data_reason,
        "daily_return": 0.01,
        "cumulative_return": cumulative,
        "excess_return_vs_spy": excess,
        "rolling_count_of_valid_days": valid_days,
        "realized_volatility_ann": volatility,
        "max_drawdown": max_drawdown,
        "avg_turnover": turnover,
        "avg_top_3_concentration": concentration,
    }


def _write_nav(path: Path, *, through_date: str = "2026-05-12") -> None:
    rows = [
        "date,caerus_polaris,caerus_orion,caerus_lyra,spy_benchmark",
        "2026-05-11,1.10,1.20,1.30,1.05",
    ]
    if through_date >= "2026-05-12":
        rows.append("2026-05-12,1.20,1.30,1.40,1.06")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_artifacts(
    root: Path,
    *,
    trade_date: str = "2026-05-12",
    valid_days: int = 31,
    shadow_refresh_status: str = "OK",
    scorecard_stale: bool = False,
    forward_days: int = 1,
) -> None:
    latest = root / "outputs" / "shadow_candidates" / "latest"
    data_status = "NO_DATA" if scorecard_stale else "OK"
    data_reason = "PRICE_CACHE_STALE" if scorecard_stale else None
    evaluation = {
        "trade_date": trade_date,
        "benchmark_symbol": "SPY",
        "strategies": {
            "caerus_polaris": _strategy(cumulative=0.20, excess=0.15, valid_days=valid_days, data_status=data_status, data_reason=data_reason),
            "caerus_orion": _strategy(cumulative=0.26, excess=0.21, valid_days=valid_days, data_status=data_status, data_reason=data_reason),
            "caerus_lyra": _strategy(cumulative=0.32, excess=0.27, valid_days=valid_days, data_status=data_status, data_reason=data_reason),
            "spy_benchmark": _strategy(cumulative=0.05, excess=0.0, valid_days=valid_days, data_status=data_status, data_reason=data_reason),
        },
    }
    _write_json(latest / "shadow_evaluation.json", evaluation)
    comparison = {"trade_date": trade_date, "status": "OK"}
    if scorecard_stale:
        comparison = {"trade_date": trade_date, "status": "NO_DATA", "reason_code": "PRICE_CACHE_STALE"}
    _write_json(latest / "comparison.json", comparison)
    _write_nav(root / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv", through_date=trade_date)
    dates = ["2026-05-12"] if forward_days else []
    for date in dates:
        _write_json(root / "outputs" / "shadow_candidates" / date / "shadow_evaluation.json", evaluation | {"trade_date": date})
        _write_json(root / "outputs" / "shadow_candidates" / date / "comparison.json", comparison | {"trade_date": date})
        _write_json(
            root / "outputs" / "shadow_candidates" / date / "shadow_performance.json",
            {
                "trade_date": date,
                "status": "OK" if not scorecard_stale else "BROKEN_CHAIN",
                "data_status": data_status,
                "data_reason": data_reason,
                "strategies": {
                    slug: {"daily_return": 0.01, "nav": 1.0}
                    for slug in ("caerus_polaris", "caerus_orion", "caerus_lyra", "spy_benchmark")
                },
            },
        )
    _write_json(
        root / "outputs" / "price_hydration" / trade_date / "status.json",
        {
            "status": "OK",
            "max_cache_date": trade_date,
            "shadow_refresh": {"status": shadow_refresh_status, "reason": None if shadow_refresh_status == "OK" else "NO_PRIOR"},
        },
    )


def _audit(root: Path, **kwargs) -> dict:
    defaults = {
        "repo_root": root,
        "baseline_date": "2026-05-11",
        "baseline_valid_days": 16,
        "recovery_date": "2026-05-12",
        "recovery_data_through": "2026-05-11",
        "expected_date": "2026-05-12",
        "min_valid_days": 30,
        "min_forward_clean_days": 5,
        "drawdown_tolerance": 0.02,
        "volatility_tolerance": 0.05,
        "max_top3_concentration": 0.60,
        "turnover_multiple": 1.50,
        "turnover_additive_tolerance": 0.05,
        "anomalous_day_share": 0.50,
    }
    defaults.update(kwargs)
    return build_promotion_audit(**defaults)


def test_challenger_not_eligible_below_30_valid_days(tmp_path: Path) -> None:
    _write_artifacts(tmp_path, valid_days=16)

    payload = _audit(tmp_path)

    assert payload["strategies"]["caerus_orion"]["promotion_classification"] == "WATCHLIST"
    assert "min_valid_days" in payload["strategies"]["caerus_orion"]["failed_criteria"]


def test_challenger_not_eligible_with_stale_scorecard(tmp_path: Path) -> None:
    _write_artifacts(tmp_path, valid_days=31, scorecard_stale=True)

    payload = _audit(tmp_path)

    assert payload["strategies"]["caerus_lyra"]["promotion_classification"] == "NOT_READY"
    assert "scorecard_fresh" in payload["strategies"]["caerus_lyra"]["failed_criteria"]
    assert "no_bad_freshness_reasons" in payload["strategies"]["caerus_lyra"]["failed_criteria"]


def test_challenger_not_eligible_when_shadow_refresh_not_ok(tmp_path: Path) -> None:
    _write_artifacts(tmp_path, valid_days=31, shadow_refresh_status="FAILED")

    payload = _audit(tmp_path)

    assert payload["health_status"] == "WARN"
    assert payload["strategies"]["caerus_orion"]["promotion_classification"] == "NOT_READY"


def test_strong_challenger_watchlist_when_forward_window_insufficient(tmp_path: Path) -> None:
    _write_artifacts(tmp_path, valid_days=31, forward_days=1)

    payload = _audit(tmp_path)

    assert payload["strategies"]["caerus_lyra"]["promotion_classification"] == "WATCHLIST"
    assert "forward_clean_days" in payload["strategies"]["caerus_lyra"]["failed_criteria"]


def test_polaris_is_baseline_not_promotion_candidate(tmp_path: Path) -> None:
    _write_artifacts(tmp_path, valid_days=31)

    payload = _audit(tmp_path)

    assert payload["strategies"]["caerus_polaris"]["role"] == "BASELINE"
    assert payload["strategies"]["caerus_polaris"]["promotion_classification"] == "BASELINE"


def test_recovered_historical_days_alone_are_not_sufficient(tmp_path: Path) -> None:
    _write_artifacts(tmp_path, valid_days=31, forward_days=0, trade_date="2026-05-11")

    payload = _audit(tmp_path, expected_date="2026-05-11")

    assert payload["forward_clean_days_after_recovery"] == 0
    assert payload["strategies"]["caerus_orion"]["promotion_classification"] == "WATCHLIST"
    assert "forward_clean_days" in payload["strategies"]["caerus_orion"]["failed_criteria"]
    assert "Forward clean observation window has not yet been established." in payload["operator_summary"]


def test_promotion_readiness_main_writes_artifacts(tmp_path: Path) -> None:
    _write_artifacts(tmp_path, valid_days=16)

    rc = main(["--repo-root", str(tmp_path), "--expected-date", "2026-05-12"])

    assert rc == 0
    assert list((tmp_path / "outputs" / "diagnostics").glob("shadow_promotion_readiness_*.json"))
    assert list((tmp_path / "outputs" / "diagnostics").glob("shadow_promotion_readiness_*.md"))
