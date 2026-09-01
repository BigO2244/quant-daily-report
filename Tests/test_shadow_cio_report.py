from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.send_shadow_cio_report as shadow_cio_report
from scripts.send_shadow_cio_report import _render_operating_capital_state, build_report


TRADE_DATE = "2026-01-13"


def test_cio_capital_state_keeps_live_paper_and_shadow_separate(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "outputs/operating_state/current/operating_truth.json",
        {
            "context_integrity": {"status": "PASS"},
            "lanes": [
                {"lane_kind": "LIVE", "strategy_ids": ["caerus_lyra"], "operating_status": "ACTIVE", "authority": {"status": "PROVED"}},
                {"lane_kind": "PAPER", "strategy_ids": ["caerus_orion"], "operating_status": "ACTIVE", "authority": {"status": "PROVED"}},
                {"lane_kind": "SHADOW", "strategy_ids": ["caerus_lyra", "caerus_orion"], "operating_status": "ACTIVE", "authority": {"status": "PROVED"}},
            ],
        },
    )
    text = _render_operating_capital_state(tmp_path)
    assert "LIVE: Lyra — ACTIVE" in text
    assert "PAPER: Orion — ACTIVE" in text
    assert "SHADOW: Lyra, Orion — ACTIVE" in text
    assert "Context integrity: PASS" in text


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
    comparison["broker_context"] = {
        "positions_count": 3,
        "strategy_overlap": {"caerus_polaris": {"overlap_names_count": 0}},
    }
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
    _sync_dated_evidence(tmp_path, performance / "shadow_nav_series.csv")


def _sync_dated_evidence(tmp_path: Path, nav_path: Path) -> None:
    with nav_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    previous, current = rows[-2], rows[-1]
    strategies = {}
    for slug, raw_nav in current.items():
        if slug == "date" or raw_nav in (None, "") or previous.get(slug) in (None, ""):
            continue
        prior_nav = float(previous[slug])
        nav = float(raw_nav)
        strategies[slug] = {
            "daily_return": nav / prior_nav - 1.0,
            "previous_nav": prior_nav,
            "nav": nav,
        }
    dated = tmp_path / "outputs" / "shadow_candidates" / current["date"]
    latest_evaluation = json.loads(
        (tmp_path / "outputs" / "shadow_candidates" / "latest" / "shadow_evaluation.json").read_text(
            encoding="utf-8"
        )
    )
    evaluated = latest_evaluation["strategies"]
    baseline_cumulative = float(evaluated["caerus_polaris"]["cumulative_return"])
    spy_cumulative = float(evaluated["spy_benchmark"]["cumulative_return"])
    _write_json(
        dated / "shadow_performance.json",
        {
            "trade_date": current["date"],
            "previous_trade_date": previous["date"],
            "status": "OK",
            "data_status": "OK",
            "return_convention": "weights_as_of_t",
            "strategies": strategies,
        },
    )
    _write_json(
        dated / "promotion_readiness.json",
        {
            "trade_date": current["date"],
            "governance_label": "RESEARCH_ONLY",
            "strategies": {
                "caerus_orion": {
                    "readiness_state": "CONTINUE_SHADOW",
                    "confidence": "MODERATE",
                    "reason_codes": ["drawdown_risk"],
                    "valid_observation_windows": evaluated["caerus_orion"]["rolling_count_of_valid_days"],
                    "cumulative_excess_vs_polaris": evaluated["caerus_orion"]["cumulative_return"] - baseline_cumulative,
                    "cumulative_excess_vs_spy": evaluated["caerus_orion"]["cumulative_return"] - spy_cumulative,
                },
                "caerus_lyra": {
                    "readiness_state": "CONTINUE_SHADOW",
                    "confidence": "MODERATE",
                    "reason_codes": ["insufficient_excess_return"],
                    "valid_observation_windows": evaluated["caerus_lyra"]["rolling_count_of_valid_days"],
                    "cumulative_excess_vs_polaris": evaluated["caerus_lyra"]["cumulative_return"] - baseline_cumulative,
                    "cumulative_excess_vs_spy": evaluated["caerus_lyra"]["cumulative_return"] - spy_cumulative,
                },
            },
        },
    )
    paper_path = tmp_path / "outputs" / "perf" / "live_overlay_nav_series.csv"
    paper_path.parent.mkdir(parents=True, exist_ok=True)
    equity = 10_000.0
    paper_rows = []
    for index, row in enumerate(rows):
        prior_equity = equity
        if index:
            equity += 10.0
        paper_rows.append(
            {
                "date": row["date"],
                "equity": equity,
                "return_1d": "" if index == 0 else equity / prior_equity - 1.0,
            }
        )
    with paper_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "equity", "return_1d"])
        writer.writeheader()
        writer.writerows(paper_rows)


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
    assert "=== SHADOW MODEL PERFORMANCE (HYPOTHETICAL) ===" in report.body
    assert "=== PAPER ACCOUNT PERFORMANCE (BROKER-DERIVED) ===" in report.body
    assert "SHADOW HYPOTHETICAL: model returns are not Paper account P&L" in report.body
    assert "Model | Daily | 7-Day | YTD (from 2026-01-02) | Excess vs SPY (YTD)" in report.body
    assert "Leader: Lyra (+12.00% YTD)" in report.body
    assert "Runner-up: Polaris (+10.00% YTD)" in report.body
    assert "Laggard: Orion (+5.00% YTD)" in report.body
    assert "Data through: 2026-01-13" in report.body
    assert "Latest source path: " in report.body
    assert "outputs/shadow_candidates/latest" in report.body
    assert "Latest source date: 2026-01-13" in report.body
    assert "Requested/report date: 2026-01-13" in report.body
    assert "Lyra | +0.90% | +12.00% | +12.00% | +9.00%" in report.body
    assert "Paper | +0.10% | +0.70% | +0.70% (2026-01-02 through 2026-01-13)" in report.body
    assert "SPY -> +3.00%" in report.body
    assert "No promotion, retirement, allocation, or lifecycle action is authorized by this report." in report.body
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
    assert "=== CANONICAL PROMOTION READINESS (RESEARCH ONLY) ===" not in report.body
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
    assert "=== CANONICAL PROMOTION READINESS (RESEARCH ONLY) ===" not in report.body


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
    assert "- Orion_Alpha: NOT_READY - Canonical dated promotion-readiness evidence is unavailable." in report.body


def test_shadow_cio_report_calculates_excess_vs_spy_ytd(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path)

    body = build_report(tmp_path).body

    assert "Polaris | +1.85% | +10.00% | +10.00% | +7.00%" in body
    assert "Orion | +0.48% | +5.00% | +5.00% | +2.00%" in body
    assert "SPY | +0.49% | +3.00% | +3.00% | +0.00%" in body


def test_shadow_cio_report_handles_no_data_price_cache_stale(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path, no_data=True)

    report = build_report(tmp_path)

    assert "N/A (stale)" not in report.body
    assert "Polaris | +1.85% | +10.00% | +10.00% | +7.00%" in report.body
    assert "PRICE_CACHE_STALE" in report.body
    assert "=== DATA HEALTH ===" in report.body
    assert "- Stale" in report.body
    assert "raw:" not in report.body.lower()


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
    _sync_dated_evidence(tmp_path, performance_path)

    body = build_report(tmp_path).body

    assert "- Lyra: CONTINUE_SHADOW - Confidence: MODERATE; evidence: insufficient_excess_return." in body
    assert "- Orion: CONTINUE_SHADOW - Confidence: MODERATE; evidence: drawdown_risk." in body
    assert "PROMOTE_CANDIDATE" not in body


def test_shadow_cio_report_caps_stale_strong_candidate_at_watch(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path, valid_days=10)
    comparison_path = tmp_path / "outputs" / "shadow_candidates" / "latest" / "comparison.json"
    _write_json(comparison_path, {"trade_date": TRADE_DATE, "status": "OK", "reason_code": "PRICE_CACHE_STALE"})

    body = build_report(tmp_path).body

    assert "- Lyra: CONTINUE_SHADOW - Confidence: MODERATE; evidence: insufficient_excess_return." in body
    assert "PROMOTE_CANDIDATE" not in body


def test_shadow_cio_report_handles_missing_artifacts_without_crashing(tmp_path: Path) -> None:
    report = build_report(tmp_path)

    assert report.body
    assert "Publication withheld. No CIO performance conclusions were generated." in report.body
    assert report.publication_withheld_reason == "DAILY_RETURN_UNRECONCILED"
    assert "corrupt" in report.data_health.lower()


def test_shadow_cio_report_withholds_when_dated_daily_return_does_not_reconcile(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path)
    evidence_path = (
        tmp_path / "outputs" / "shadow_candidates" / TRADE_DATE / "shadow_performance.json"
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["strategies"]["caerus_lyra"]["daily_return"] += 0.08
    _write_json(evidence_path, evidence)

    report = build_report(tmp_path)

    assert report.publication_withheld is True
    assert report.publication_withheld_reason == "DAILY_RETURN_UNRECONCILED"
    assert "does not reconcile to canonical NAV" in report.body
    assert "Leader:" not in report.body


def test_shadow_cio_report_withholds_when_return_convention_is_not_canonical(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path)
    evidence_path = (
        tmp_path / "outputs" / "shadow_candidates" / TRADE_DATE / "shadow_performance.json"
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["return_convention"] = "unknown"
    _write_json(evidence_path, evidence)

    report = build_report(tmp_path)

    assert report.publication_withheld is True
    assert report.publication_withheld_reason == "DAILY_RETURN_UNRECONCILED"
    assert "SHADOW_RETURN_CONVENTION_INVALID" in report.body


def test_shadow_cio_report_suppresses_stale_readiness_metrics_candidate_by_candidate(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path)
    readiness_path = (
        tmp_path / "outputs" / "shadow_candidates" / TRADE_DATE / "promotion_readiness.json"
    )
    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    readiness["strategies"]["caerus_lyra"]["valid_observation_windows"] -= 1
    _write_json(readiness_path, readiness)

    body = build_report(tmp_path).body

    assert "- Lyra: NOT_READY - Canonical dated promotion-readiness evidence is unavailable." in body
    assert "- Orion: CONTINUE_SHADOW - Confidence: MODERATE; evidence: drawdown_risk." in body


def test_shadow_cio_report_renders_reconciled_material_move_attribution(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path)
    nav_path = tmp_path / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv"
    nav_path.write_text(nav_path.read_text(encoding="utf-8").replace("1.10,1.05,1.12,1.03", "1.10,1.05,1.22,1.03"), encoding="utf-8")
    _sync_dated_evidence(tmp_path, nav_path)
    evidence_path = (
        tmp_path / "outputs" / "shadow_candidates" / TRADE_DATE / "shadow_performance.json"
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    daily_return = evidence["strategies"]["caerus_lyra"]["daily_return"]
    evidence["strategies"]["caerus_lyra"].update(
        {
            "gross_exposure": 1.0,
            "cash_weight": 0.0,
            "daily_attribution": [
                {
                    "ticker": "MU",
                    "target_weight": 1.0,
                    "close_to_close_return": daily_return,
                    "contribution": daily_return,
                }
            ],
        }
    )
    _write_json(evidence_path, evidence)

    body = build_report(tmp_path).body

    assert "=== MATERIAL DAILY-MOVE ATTRIBUTION ===" in body
    assert "- Lyra +9.91%: MU +9.91%." in body


def test_shadow_cio_report_discloses_corrupt_paper_return_instead_of_displaying_it(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path)
    paper_path = tmp_path / "outputs" / "perf" / "live_overlay_nav_series.csv"
    rows = list(csv.DictReader(paper_path.open(newline="", encoding="utf-8")))
    rows[-1]["return_1d"] = "0.08"
    with paper_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "equity", "return_1d"])
        writer.writeheader()
        writer.writerows(rows)

    report = build_report(tmp_path)

    assert report.paper_snapshot.status == "CORRUPT"
    assert "Paper performance unavailable: CORRUPT" in report.body
    assert "Paper | +8.00%" not in report.body


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
    _sync_dated_evidence(tmp_path, performance / "shadow_nav_series.csv")

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
    _sync_dated_evidence(tmp_path, performance / "shadow_nav_series.csv")

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
    assert "Polaris | +1.85% | +10.00% | +10.00% | +7.00%" in report.body
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
    assert "=== SHADOW MODEL PERFORMANCE (HYPOTHETICAL) ===" not in report.body
    assert "=== RANKING ===" not in report.body
    assert "=== CANONICAL PROMOTION READINESS (RESEARCH ONLY) ===" not in report.body
    assert "PROMOTE_CANDIDATE" not in report.body
    assert "Publication withheld. No CIO performance conclusions were generated." in report.body


def test_shadow_cio_main_best_effort_send_is_nonfatal_only_for_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        shadow_cio_report,
        "build_report",
        lambda _root: SimpleNamespace(subject="subject", body="body"),
    )
    monkeypatch.setattr(shadow_cio_report, "_load_dotenv", lambda _root: None)

    def _reject(**_kwargs) -> None:
        raise RuntimeError("smtp rejected")

    monkeypatch.setattr("core.quant_report.send_email", _reject)

    assert (
        shadow_cio_report.main(
            ["--repo-root", str(tmp_path), "--best-effort-send"]
        )
        == 0
    )
    assert "[SHADOW_CIO_REPORT][WARN]" in capsys.readouterr().err


def test_shadow_cio_main_default_send_remains_fail_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        shadow_cio_report,
        "build_report",
        lambda _root: SimpleNamespace(subject="subject", body="body"),
    )
    monkeypatch.setattr(shadow_cio_report, "_load_dotenv", lambda _root: None)

    def _reject(**_kwargs) -> None:
        raise RuntimeError("smtp rejected")

    monkeypatch.setattr("core.quant_report.send_email", _reject)

    with pytest.raises(RuntimeError, match="smtp rejected"):
        shadow_cio_report.main(["--repo-root", str(tmp_path)])
