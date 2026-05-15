from __future__ import annotations

import json
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from core.portfolio_learning_report import (
    BANNED_LANGUAGE,
    build_portfolio_learning_report,
    write_portfolio_learning_artifacts,
)
from scripts import send_portfolio_learning_review as script

ET = ZoneInfo("America/New_York")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_hydration_status(
    root: Path,
    *,
    trade_date: str = "2026-05-04",
    status: str = "OK",
    max_cache_date: str | None = "2026-05-04",
    download_attempted: bool = True,
) -> None:
    _write_json(
        root / "outputs" / "price_hydration" / trade_date / "status.json",
        {
            "run_timestamp": "2026-05-04T23:00:00Z",
            "as_of_date": trade_date,
            "status": status,
            "max_cache_date": max_cache_date,
            "reason": "test hydration status",
            "notes": "test",
            "download_attempted": download_attempted,
            "provider": "yfinance",
        },
    )


def _write_sample_artifacts(root: Path, *, trade_date: str = "2026-05-04", stale: bool = False) -> None:
    dated = root / "outputs" / "shadow_candidates" / trade_date
    reason = "PRICE_CACHE_STALE" if stale else None
    _write_json(
        dated / "shadow_evaluation.json",
        {
            "trade_date": trade_date,
            "strategies": {
                "caerus_polaris": {
                    "data_status": "NO_DATA" if stale else "OK",
                    "data_reason": reason,
                    "daily_return": 0.0068,
                    "cumulative_return": 0.0347,
                    "excess_return_vs_spy": -0.0764,
                    "rolling_count_of_valid_days": 12,
                    "constituent_change_count": 2,
                },
                "caerus_orion": {
                    "data_status": "OK",
                    "daily_return": 0.01,
                    "cumulative_return": 0.052,
                    "excess_return_vs_spy": -0.0591,
                    "rolling_count_of_valid_days": 12,
                    "constituent_change_count": 4,
                },
                "caerus_lyra": {
                    "data_status": "OK",
                    "daily_return": -0.002,
                    "cumulative_return": 0.02,
                    "excess_return_vs_spy": -0.0911,
                    "rolling_count_of_valid_days": 8,
                    "constituent_change_count": 7,
                },
                "spy_benchmark": {
                    "data_status": "OK",
                    "daily_return": 0.0028,
                    "cumulative_return": 0.1111,
                    "excess_return_vs_spy": 0.0,
                    "rolling_count_of_valid_days": 12,
                },
            },
        },
    )
    _write_json(
        dated / "comparison.json",
        {
            "trade_date": trade_date,
            "status": "NO_DATA" if stale else "OK",
            "reason_code": reason,
            "strategies": {
                slug: {
                    "expected_turnover": turnover,
                    "weight_concentration": {"top3_concentration": top3},
                }
                for slug, turnover, top3 in [
                    ("caerus_polaris", 0.12, 0.42),
                    ("caerus_orion", 0.18, 0.55),
                    ("caerus_lyra", 0.31, 0.61),
                ]
            },
        },
    )
    (dated / "comparison.md").write_text("# Shadow Candidates Comparison\n", encoding="utf-8")
    _write_json(
        dated / "feedback_loop_summary.json",
        {
            "trade_date": trade_date,
            "status": "PARTIAL",
            "strategies": {
                "polaris": {
                    "decision_trace_status": "OK",
                    "attribution_status": "OK",
                    "stability_status": "OK",
                    "regime_status": "NO_REGIME_DATA",
                    "learning_readiness": "MEDIUM",
                    "primary_learning_gap": "regime data unavailable",
                },
                "orion": {
                    "decision_trace_status": "OK",
                    "attribution_status": "OK",
                    "stability_status": "OK",
                    "regime_status": "OK",
                    "learning_readiness": "HIGH",
                    "primary_learning_gap": "none",
                },
                "lyra": {
                    "decision_trace_status": "NO_PRIOR",
                    "attribution_status": "UNAVAILABLE",
                    "stability_status": "PARTIAL",
                    "regime_status": "NO_REGIME_DATA",
                    "learning_readiness": "LOW",
                    "primary_learning_gap": "insufficient 10d valid history",
                },
            },
            "system_learning_summary": {"ready_for_promotion_logic": False},
        },
    )
    for short in ("polaris", "orion", "lyra"):
        strategy_dir = dated / short
        _write_json(strategy_dir / "decision_trace.json", {"status": "OK", "selected_positions": []})
        _write_json(
            strategy_dir / "attribution.json",
            {
                "status": "OK" if short != "lyra" else "UNAVAILABLE",
                "position_contribution": [
                    {"ticker": "AAA", "contribution": 0.001, "contribution_status": "OK"},
                    {"ticker": "BBB", "contribution": -0.0005, "contribution_status": "OK"},
                ],
                "decision_contribution": {"status": "OK"},
                "signal_contribution": {"status": "UNAVAILABLE", "signals": {}},
            },
        )
        _write_json(
            strategy_dir / "stability_analysis.json",
            {
                "status": "OK" if short != "lyra" else "PARTIAL",
                "rolling_windows": {
                    "10d": {
                        "valid_days": 12 if short != "lyra" else 8,
                        "avg_turnover": 0.2,
                        "max_turnover": 0.4,
                        "avg_top_3_concentration": 0.62 if short == "lyra" else 0.45,
                        "constituent_change_count": 3,
                    }
                },
                "flags": ["INSUFFICIENT_VALID_DAYS", "HIGH_CONCENTRATION"] if short == "lyra" else [],
            },
        )
        _write_json(
            strategy_dir / "regime_performance.json",
            {
                "status": "NO_REGIME_DATA" if short != "orion" else "OK",
                "current_regime": {"regime": "LOW"},
                "performance_by_regime": {"LOW": {"valid_days": 3, "return": 0.01}},
            },
        )
    _write_json(
        root / "outputs" / "latest_run.json",
        {"run_id": "run-1", "status": "success", "workflow_stage": "execution"},
    )


def test_builds_report_from_complete_sample_artifacts(tmp_path: Path) -> None:
    _write_sample_artifacts(tmp_path)

    report = build_portfolio_learning_report(
        repo_root=tmp_path,
        trade_date="2026-05-04",
        shadow_dir=tmp_path / "outputs" / "shadow_candidates",
    )

    assert report.status == "OK"
    assert "Executive Summary" in report.body_text
    assert "Portfolio Scoreboard" in report.body_text
    assert "Orion" in report.body_text
    assert "<table>" in report.body_html
    assert "<pre" not in report.body_html
    assert "<h2>Portfolio Scoreboard</h2>" in report.body_html
    assert "<h2>Learning Readiness</h2>" in report.body_html
    assert report.payload["portfolio_scoreboard"][0]["name"] == "Polaris"


def test_missing_feedback_loop_summary_is_optional_health_not_scoreboard_failure(tmp_path: Path) -> None:
    _write_sample_artifacts(tmp_path)
    (tmp_path / "outputs" / "shadow_candidates" / "2026-05-04" / "feedback_loop_summary.json").unlink()

    report = build_portfolio_learning_report(
        repo_root=tmp_path,
        trade_date="2026-05-04",
        shadow_dir=tmp_path / "outputs" / "shadow_candidates",
    )

    assert report.status == "OK"
    assert "feedback_loop_summary.json" in report.payload["artifact_health"]["missing"]
    assert "feedback_loop_summary.json" in report.payload["artifact_health"]["optional_missing"]
    assert report.payload["artifact_health"]["required_missing"] == []


def test_missing_required_shadow_evaluation_still_blocks_health(tmp_path: Path) -> None:
    _write_sample_artifacts(tmp_path)
    (tmp_path / "outputs" / "shadow_candidates" / "2026-05-04" / "shadow_evaluation.json").unlink()

    report = build_portfolio_learning_report(
        repo_root=tmp_path,
        trade_date="2026-05-04",
        shadow_dir=tmp_path / "outputs" / "shadow_candidates",
    )

    assert report.status == "NO_DATA"
    assert "shadow_evaluation.json" in report.payload["artifact_health"]["required_missing"]


def test_missing_hydration_status_replaces_price_cache_stale_watch_item(tmp_path: Path) -> None:
    _write_sample_artifacts(tmp_path, stale=True)

    report = build_portfolio_learning_report(
        repo_root=tmp_path,
        trade_date="2026-05-04",
        shadow_dir=tmp_path / "outputs" / "shadow_candidates",
    )

    assert "HYDRATION_NOT_RUN" in "\n".join(report.payload["watch_items"])
    assert "HYDRATION_NOT_RUN" in report.body_text
    assert "PRICE_CACHE_STALE" not in report.body_text
    assert "Operator Diagnosis" in report.body_text
    assert "Data freshness watch" in report.body_text


def test_hydration_ok_suppresses_generic_price_cache_stale(tmp_path: Path) -> None:
    _write_sample_artifacts(tmp_path, stale=True)
    _write_hydration_status(tmp_path)

    report = build_portfolio_learning_report(
        repo_root=tmp_path,
        trade_date="2026-05-04",
        shadow_dir=tmp_path / "outputs" / "shadow_candidates",
        generated_at_et=dt.datetime(2026, 5, 4, 18, 30, tzinfo=ET),
    )

    assert report.payload["artifact_health"]["raw_stale_reasons"] == ["PRICE_CACHE_STALE"]
    assert report.payload["artifact_health"]["stale_reasons"] == []
    assert "PRICE_CACHE_STALE" not in "\n".join(report.payload["watch_items"])
    assert "PRICE_CACHE_STALE" not in report.body_text


def test_failed_hydration_reports_hydration_failed(tmp_path: Path) -> None:
    _write_sample_artifacts(tmp_path, stale=True)
    _write_hydration_status(tmp_path, status="FAILED", max_cache_date="2026-05-01")

    report = build_portfolio_learning_report(
        repo_root=tmp_path,
        trade_date="2026-05-04",
        shadow_dir=tmp_path / "outputs" / "shadow_candidates",
        generated_at_et=dt.datetime(2026, 5, 4, 18, 30, tzinfo=ET),
    )

    assert "HYDRATION_FAILED" in report.body_text
    assert "PRICE_CACHE_STALE" not in report.body_text


def test_provider_data_lag_after_post_close_attempt(tmp_path: Path) -> None:
    _write_sample_artifacts(tmp_path, stale=True)
    _write_hydration_status(tmp_path, status="PARTIAL", max_cache_date="2026-05-01", download_attempted=True)

    report = build_portfolio_learning_report(
        repo_root=tmp_path,
        trade_date="2026-05-04",
        shadow_dir=tmp_path / "outputs" / "shadow_candidates",
        generated_at_et=dt.datetime(2026, 5, 4, 18, 30, tzinfo=ET),
    )

    assert "PROVIDER_DATA_LAG" in report.body_text
    assert "PRICE_CACHE_STALE" not in report.body_text


def test_current_session_incomplete_before_market_close(tmp_path: Path) -> None:
    _write_sample_artifacts(tmp_path, stale=True)

    report = build_portfolio_learning_report(
        repo_root=tmp_path,
        trade_date="2026-05-04",
        shadow_dir=tmp_path / "outputs" / "shadow_candidates",
        generated_at_et=dt.datetime(2026, 5, 4, 8, 30, tzinfo=ET),
    )

    assert "CURRENT_SESSION_INCOMPLETE" in report.body_text
    assert "PRICE_CACHE_STALE" not in report.body_text


def test_banned_language_does_not_appear(tmp_path: Path) -> None:
    _write_sample_artifacts(tmp_path)

    report = build_portfolio_learning_report(
        repo_root=tmp_path,
        trade_date="2026-05-04",
        shadow_dir=tmp_path / "outputs" / "shadow_candidates",
    )
    combined = json.dumps(report.payload, sort_keys=True).lower() + report.body_text.lower() + report.body_html.lower()

    assert not any(term in combined for term in BANNED_LANGUAGE)


def test_dry_run_does_not_send_email(tmp_path: Path, monkeypatch) -> None:
    _write_sample_artifacts(tmp_path)
    monkeypatch.setattr(script, "_REPO_ROOT", tmp_path)
    sent = {"called": False}

    def _fake_send(*_args, **_kwargs):
        sent["called"] = True

    monkeypatch.setitem(__import__("sys").modules, "core.quant_report", type("_M", (), {"send_email": _fake_send}))

    rc = script.main(["--trade-date", "2026-05-04", "--dry-run"])

    assert rc == 0
    assert sent["called"] is False


def test_json_and_markdown_outputs_are_deterministic(tmp_path: Path) -> None:
    _write_sample_artifacts(tmp_path)
    report = build_portfolio_learning_report(
        repo_root=tmp_path,
        trade_date="2026-05-04",
        shadow_dir=tmp_path / "outputs" / "shadow_candidates",
    )

    first = write_portfolio_learning_artifacts(report=report, output_dir=tmp_path / "outputs" / "portfolio_learning")
    first_json = first[0].read_text()
    first_md = first[1].read_text()
    second = write_portfolio_learning_artifacts(report=report, output_dir=tmp_path / "outputs" / "portfolio_learning")

    assert second[0].read_text() == first_json
    assert second[1].read_text() == first_md


def test_email_body_includes_major_sections(tmp_path: Path) -> None:
    _write_sample_artifacts(tmp_path)

    report = build_portfolio_learning_report(
        repo_root=tmp_path,
        trade_date="2026-05-04",
        shadow_dir=tmp_path / "outputs" / "shadow_candidates",
    )

    for section in (
        "Executive Summary",
        "Operator Diagnosis",
        "Portfolio Scoreboard",
        "Learning Readiness",
        "Stability Review",
        "Attribution Review",
        "Regime Review",
        "Watch Items",
        "Next Observations",
    ):
        assert section in report.body_text


def test_monday_morning_default_resolves_to_prior_friday() -> None:
    monday_morning = dt.datetime(2026, 5, 4, 8, 15, tzinfo=ET)

    assert script.resolve_as_of_trade_date(now=monday_morning) == "2026-05-01"


def test_explicit_trade_date_is_respected() -> None:
    monday_morning = dt.datetime(2026, 5, 4, 8, 15, tzinfo=ET)

    assert script.resolve_as_of_trade_date(explicit_trade_date="2026-05-04", now=monday_morning) == "2026-05-04"


def test_report_distinguishes_generated_date_from_as_of_date(tmp_path: Path) -> None:
    _write_sample_artifacts(tmp_path, trade_date="2026-05-01")

    report = build_portfolio_learning_report(
        repo_root=tmp_path,
        trade_date="2026-05-01",
        shadow_dir=tmp_path / "outputs" / "shadow_candidates",
        report_generated_date="2026-05-04",
    )

    assert report.payload["as_of_trade_date"] == "2026-05-01"
    assert report.payload["report_generated_date"] == "2026-05-04"
    assert "as of 2026-05-01" in report.subject
    assert "Report generated date: 2026-05-04" in report.body_text


def test_default_does_not_show_price_cache_stale_merely_because_today_has_not_closed(tmp_path: Path, monkeypatch) -> None:
    _write_sample_artifacts(tmp_path, trade_date="2026-05-01", stale=False)
    _write_sample_artifacts(tmp_path, trade_date="2026-05-04", stale=True)
    monkeypatch.setattr(script, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(script, "current_et", lambda now=None: dt.datetime(2026, 5, 4, 8, 15, tzinfo=ET))

    rc = script.main(["--output-dir", str(tmp_path / "outputs" / "portfolio_learning"), "--dry-run"])

    assert rc == 0
    md = (tmp_path / "outputs" / "portfolio_learning" / "2026-05-01" / "weekly_portfolio_learning.md").read_text()
    assert "PRICE_CACHE_STALE" not in md
    assert "as of 2026-05-01" in md
