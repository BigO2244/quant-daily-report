from __future__ import annotations

import json
from pathlib import Path

from scripts.send_shadow_cio_report import build_report


TRADE_DATE = "2026-01-13"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def _evaluation(*, no_data: bool = False, valid_days: int = 10) -> dict:
    status = "NO_DATA" if no_data else "OK"
    reason = "PRICE_CACHE_STALE" if no_data else None
    daily_returns = {
        "caerus_polaris": 0.01,
        "caerus_orion": -0.002,
        "caerus_lyra": 0.012,
        "spy_benchmark": 0.004,
    }
    return {
        "trade_date": TRADE_DATE,
        "benchmark_symbol": "SPY",
        "strategies": {
            slug: {
                "strategy_name": name,
                "data_status": status,
                "data_reason": reason,
                "daily_return": daily_returns[slug] if not no_data else None,
                "cumulative_return": cumulative_return,
                "excess_return_vs_spy": 0.0 if slug == "spy_benchmark" else cumulative_return - 0.03,
                "rolling_count_of_valid_days": valid_days if not no_data else 0,
            }
            for slug, name, cumulative_return in [
                ("caerus_polaris", "Caerus Polaris", 0.10),
                ("caerus_orion", "Caerus Orion", 0.05),
                ("caerus_lyra", "Caerus Lyra", 0.12),
                ("spy_benchmark", "SPY", 0.03),
            ]
        },
    }


def _write_shadow_artifacts(tmp_path: Path, *, no_data: bool = False, valid_days: int = 10) -> None:
    latest = tmp_path / "outputs" / "shadow_candidates" / "latest"
    _write_json(latest / "shadow_evaluation.json", _evaluation(no_data=no_data, valid_days=valid_days))
    comparison = {"trade_date": TRADE_DATE, "status": "NO_DATA", "reason_code": "PRICE_CACHE_STALE"} if no_data else {"trade_date": TRADE_DATE, "status": "OK"}
    _write_json(latest / "comparison.json", comparison)
    performance = tmp_path / "outputs" / "shadow_candidates" / "performance"
    performance.mkdir(parents=True, exist_ok=True)
    (performance / "shadow_nav_series.csv").write_text(
        "\n".join(
            [
                "date,caerus_polaris,caerus_orion,caerus_lyra,spy_benchmark",
                "2026-01-02,1.00,1.00,1.00,1.00",
                "2026-01-05,1.01,1.00,1.02,1.00",
                "2026-01-06,1.02,1.01,1.04,1.01",
                "2026-01-07,1.03,1.02,1.06,1.01",
                "2026-01-08,1.04,1.03,1.08,1.02",
                "2026-01-09,1.06,1.04,1.10,1.02",
                "2026-01-12,1.08,1.045,1.11,1.025",
                "2026-01-13,1.10,1.05,1.12,1.03",
            ]
        )
        + "\n"
    )


def _write_hydration_status(tmp_path: Path, *, trade_date: str, shadow_refresh_status: str = "OK") -> None:
    _write_json(
        tmp_path / "outputs" / "price_hydration" / trade_date / "status.json",
        {
            "status": "OK",
            "as_of_date": trade_date,
            "max_cache_date": trade_date,
            "shadow_refresh": {
                "status": shadow_refresh_status,
                "reason": None if shadow_refresh_status == "OK" else "PRICE_CACHE_STALE",
                "nav_series_latest_date": "2026-01-13",
            },
        },
    )


def test_shadow_cio_report_renders_readable_scorecard(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path)

    report = build_report(tmp_path)

    assert report.publication_withheld is False
    assert report.subject == f"Caerus Model Scorecard \u2014 {TRADE_DATE}"
    assert "=== DAILY MODEL SCORECARD ===" in report.body
    assert "=== PERFORMANCE SNAPSHOT ===" in report.body
    assert "Model | Daily | 7-Day | YTD (from 2026-01-02) | Excess vs SPY (YTD)" in report.body
    assert "Leader: Lyra (+12.00% YTD)" in report.body
    assert "Runner-up: Polaris (+10.00% YTD)" in report.body
    assert "Laggard: Orion (+5.00% YTD)" in report.body
    assert "Data through: 2026-01-13" in report.body
    assert "Latest source path: " in report.body
    assert "outputs/shadow_candidates/latest" in report.body
    assert "Latest source date: 2026-01-13" in report.body
    assert "Requested/report date: 2026-01-13" in report.body
    assert "Lyra | +0.90% | +9.80% | +12.00% | +9.00%" in report.body
    assert "SPY -> +3.00%" in report.body
    assert "Advisory research labels only; no promotion, retirement, allocation, or lifecycle action is authorized by this report." in report.body
    assert "{" not in report.body
    assert "}" not in report.body


def test_shadow_cio_report_ranking_order_uses_ytd_return(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path)

    body = build_report(tmp_path).body

    assert body.index("1. Lyra -> +12.00% YTD") < body.index("2. Polaris -> +10.00% YTD")
    assert body.index("2. Polaris -> +10.00% YTD") < body.index("3. Orion -> +5.00% YTD")


def test_shadow_cio_report_withholds_when_shadow_refresh_failed(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path)
    _write_hydration_status(tmp_path, trade_date=TRADE_DATE, shadow_refresh_status="FAILED")

    report = build_report(tmp_path)

    assert report.publication_withheld is True
    assert report.publication_withheld_reason == "FAILED_REFRESH"
    assert "=== MODEL SCORECARD: PUBLICATION WITHHELD ===" in report.body
    assert "Reason: FAILED_REFRESH" in report.body
    assert "Latest valid NAV date: 2026-01-13" in report.body
    assert "Requested report date: 2026-01-13" in report.body
    assert "No rankings, promotion signals, or CIO performance conclusions were generated." in report.body
    assert "=== DATA HEALTH ===" in report.body
    assert "Leader:" not in report.body
    assert "=== RANKING ===" not in report.body
    assert "=== PROMOTION SIGNAL ===" not in report.body
    assert "leads the model set" not in report.body


def test_shadow_cio_report_withholds_when_nav_age_exceeds_tolerance(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path)
    latest = tmp_path / "outputs" / "shadow_candidates" / "latest"
    evaluation = _evaluation(no_data=True)
    evaluation["trade_date"] = "2026-01-16"
    _write_json(latest / "shadow_evaluation.json", evaluation)
    _write_json(latest / "comparison.json", {"trade_date": "2026-01-16", "status": "NO_DATA", "reason_code": "PRICE_CACHE_STALE"})

    report = build_report(tmp_path)

    assert report.publication_withheld is True
    assert report.publication_withheld_reason == "NAV_STALE"
    assert "Reason: NAV_STALE" in report.body
    assert "Latest valid NAV date: 2026-01-13" in report.body
    assert "Requested report date: 2026-01-16" in report.body
    assert "Leader:" not in report.body
    assert "=== RANKING ===" not in report.body
    assert "=== PROMOTION SIGNAL ===" not in report.body


def test_shadow_cio_report_fallback_return_is_not_rankable_or_promotable(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path)
    latest = tmp_path / "outputs" / "shadow_candidates" / "latest"
    evaluation = _evaluation()
    evaluation["strategies"]["caerus_orion_alpha"] = {
        "strategy_name": "Orion_Alpha",
        "data_status": "OK",
        "data_reason": None,
        "daily_return": 0.0,
        "cumulative_return": 1.50,
        "excess_return_vs_spy": 1.47,
        "rolling_count_of_valid_days": 0,
    }
    _write_json(latest / "shadow_evaluation.json", evaluation)

    report = build_report(tmp_path)

    assert report.publication_withheld is False
    assert "Orion_Alpha | +0.00% | N/A | +150.00% | +147.00%" in report.body
    assert "Leader: Lyra (+12.00% YTD)" in report.body
    assert "Leader: Orion_Alpha" not in report.body
    assert "Orion_Alpha ->" not in report.body
    assert "- Orion_Alpha: NOT_READY - Strong YTD, but only 0 valid days." in report.body


def test_shadow_cio_report_calculates_excess_vs_spy_ytd(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path)

    body = build_report(tmp_path).body

    assert "Polaris | +1.85% | +8.91% | +10.00% | +7.00%" in body
    assert "Orion | +0.48% | +5.00% | +5.00% | +2.00%" in body
    assert "SPY | +0.49% | +3.00% | +3.00% | +0.00%" in body


def test_shadow_cio_report_handles_no_data_price_cache_stale(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path, no_data=True)

    report = build_report(tmp_path)

    assert "N/A (stale)" not in report.body
    assert "Polaris | +1.85% | +8.91% | +10.00% | +7.00%" in report.body
    assert "PRICE_CACHE_STALE" in report.body
    assert "=== DATA HEALTH ===" in report.body
    assert "- Stale" in report.body
    assert "raw" not in report.body.lower()


def test_shadow_cio_report_blocks_promotion_when_valid_days_under_ten_and_stale(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path, valid_days=7)
    comparison_path = tmp_path / "outputs" / "shadow_candidates" / "latest" / "comparison.json"
    _write_json(comparison_path, {"trade_date": TRADE_DATE, "status": "OK", "reason_code": "PRICE_CACHE_STALE"})
    performance_path = tmp_path / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv"
    performance_path.write_text(
        "\n".join(
            [
                "date,caerus_polaris,caerus_orion,caerus_lyra,spy_benchmark",
                "2026-01-02,1.00,1.00,1.00,1.00",
                "2026-01-05,1.01,1.02,1.02,1.00",
                "2026-01-06,1.02,1.04,1.04,1.01",
                "2026-01-07,1.03,1.06,1.06,1.01",
                "2026-01-08,1.04,1.08,1.08,1.02",
                "2026-01-09,1.06,1.10,1.10,1.02",
                "2026-01-12,1.08,1.12,1.11,1.025",
                "2026-01-13,1.10,1.14,1.12,1.03",
            ]
        )
        + "\n"
    )

    body = build_report(tmp_path).body

    assert "- Lyra: NOT_READY - Strong YTD, but only 7 valid days and current data is stale." in body
    assert "- Orion: NOT_READY - Strong YTD, but only 7 valid days and current data is stale." in body
    assert "PROMOTE_CANDIDATE" not in body


def test_shadow_cio_report_caps_stale_strong_candidate_at_watch(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path, valid_days=10)
    comparison_path = tmp_path / "outputs" / "shadow_candidates" / "latest" / "comparison.json"
    _write_json(comparison_path, {"trade_date": TRADE_DATE, "status": "OK", "reason_code": "PRICE_CACHE_STALE"})

    body = build_report(tmp_path).body

    assert "- Lyra: WATCH - Strong YTD, but current data is stale." in body
    assert "PROMOTE_CANDIDATE" not in body


def test_shadow_cio_report_handles_missing_artifacts_without_crashing(tmp_path: Path) -> None:
    report = build_report(tmp_path)

    assert report.body
    assert "Shadow performance is not decision-useful yet" in report.body
    assert report.data_health == "Stale"


def test_shadow_cio_report_labels_since_inception_when_ytd_history_unavailable(tmp_path: Path) -> None:
    latest = tmp_path / "outputs" / "shadow_candidates" / "latest"
    evaluation = _evaluation()
    evaluation["trade_date"] = "2026-01-03"
    _write_json(latest / "shadow_evaluation.json", evaluation)
    _write_json(latest / "comparison.json", {"trade_date": "2026-01-03", "status": "OK"})
    performance = tmp_path / "outputs" / "shadow_candidates" / "performance"
    performance.mkdir(parents=True, exist_ok=True)
    (performance / "shadow_nav_series.csv").write_text(
        "\n".join(
            [
                "date,caerus_polaris,caerus_orion,caerus_lyra,spy_benchmark",
                "2025-12-30,1.00,1.00,1.00,1.00",
                "2025-12-31,1.05,1.03,1.07,1.01",
            ]
        )
        + "\n"
    )

    body = build_report(tmp_path).body

    assert "Data through: 2025-12-31" in body
    assert "Since Observation Inception (from 2025-12-30) through 2025-12-31" in body
    assert "Excess vs SPY (Since Observation Inception)" in body


def test_shadow_cio_report_labels_may_observation_window_as_since_inception(tmp_path: Path) -> None:
    latest = tmp_path / "outputs" / "shadow_candidates" / "latest"
    evaluation = _evaluation(valid_days=23)
    evaluation["trade_date"] = "2026-06-12"
    _write_json(latest / "shadow_evaluation.json", evaluation)
    _write_json(latest / "comparison.json", {"trade_date": "2026-06-12", "status": "OK"})
    performance = tmp_path / "outputs" / "shadow_candidates" / "performance"
    performance.mkdir(parents=True, exist_ok=True)
    (performance / "shadow_nav_series.csv").write_text(
        "\n".join(
            [
                "date,caerus_polaris,caerus_orion,caerus_lyra,spy_benchmark",
                "2026-05-12,1.00,1.00,1.00,1.00",
                "2026-06-11,1.08,1.12,1.09,1.00",
                "2026-06-12,1.10,1.19,1.12,1.01",
            ]
        )
        + "\n"
    )

    body = build_report(tmp_path).body

    assert "YTD (from 2026-05-12)" not in body
    assert "Model | Daily | 7-Day | Since Observation Inception (from 2026-05-12) | Excess vs SPY (Since Observation Inception)" in body
    assert "Daily, 7-Day, and Since Observation Inception are anchored to Data through: 2026-06-12." in body


def test_shadow_cio_report_anchors_all_windows_to_latest_valid_nav_date(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path)
    latest = tmp_path / "outputs" / "shadow_candidates" / "latest"
    evaluation = _evaluation(no_data=True)
    evaluation["trade_date"] = "2026-01-14"
    _write_json(latest / "shadow_evaluation.json", evaluation)
    _write_json(latest / "comparison.json", {"trade_date": "2026-01-14", "status": "NO_DATA", "reason_code": "PRICE_CACHE_STALE"})

    report = build_report(tmp_path)

    assert report.trade_date == "2026-01-14"
    assert report.as_of_date == "2026-01-13"
    assert "Data through: 2026-01-13" in report.body
    assert "Current trade date not yet available; report uses latest fully available shadow data." in report.body
    assert "Model | Daily | 7-Day (through 2026-01-13) | YTD (from 2026-01-02) through 2026-01-13 | Excess vs SPY (YTD)" in report.body
    assert "Polaris | +1.85% | +8.91% | +10.00% | +7.00%" in report.body
    assert "N/A (stale)" not in report.body


def test_shadow_cio_report_suppresses_windows_and_rankings_when_nav_chain_resets(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path)
    performance_path = tmp_path / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv"
    performance_path.write_text(
        "\n".join(
            [
                "date,caerus_polaris,caerus_orion,caerus_lyra,spy_benchmark",
                "2026-01-09,38.0,170.0,171.0,5.0",
                "2026-01-12,38.2,170.2,171.2,5.01",
                "2026-01-13,1.10,1.05,1.12,1.03",
            ]
        )
        + "\n"
    )

    report = build_report(tmp_path)

    assert report.data_health == "Fresh but corrupt"
    assert report.publication_withheld is True
    assert report.publication_withheld_reason == "ARTIFACT_CORRUPT"
    assert "SHADOW_NAV_CHAIN_RESET" in report.data_health_reason
    assert "Reason: ARTIFACT_CORRUPT" in report.body
    assert "=== PERFORMANCE SNAPSHOT ===" not in report.body
    assert "=== RANKING ===" not in report.body
    assert "=== PROMOTION SIGNAL ===" not in report.body
    assert "PROMOTE_CANDIDATE" not in report.body
    assert "Publication withheld. No CIO performance conclusions were generated." in report.body
