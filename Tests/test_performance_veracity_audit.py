from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.performance_veracity_audit import (
    audit_dated_shadow_chain,
    audit_execution_assumptions,
    audit_lookahead_bias,
    audit_nav_series,
    run_audit,
)


MODEL_SLUGS = ("caerus_polaris", "caerus_orion", "caerus_lyra", "spy_benchmark")


def _write_nav(root: Path) -> None:
    path = root / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "date,caerus_polaris,caerus_orion,caerus_lyra,spy_benchmark",
                "2026-01-02,1.00,1.00,1.00,1.00",
                "2026-01-05,1.10,1.20,1.30,1.05",
                "2026-01-06,1.21,1.32,1.43,1.00",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _performance_payload(date: str, previous: dict[str, float], returns: dict[str, float], *, status: str = "OK", data_status: str = "OK", data_reason: str | None = None) -> dict:
    strategies = {}
    for slug in MODEL_SLUGS:
        prev = previous[slug]
        ret = returns[slug]
        nav = prev if data_status == "NO_DATA" else round(prev * (1.0 + ret), 10)
        strategies[slug] = {
            "strategy_name": slug,
            "daily_return": 0.0 if data_status == "NO_DATA" else ret,
            "previous_nav": prev,
            "nav": nav,
            "weights_count": 1,
        }
    return {
        "trade_date": date,
        "previous_trade_date": None,
        "status": status,
        "data_status": data_status,
        "data_reason": data_reason,
        "return_convention": "weights_as_of_t",
        "strategies": strategies,
    }


def _write_performance(root: Path, date: str, payload: dict) -> None:
    path = root / "outputs" / "shadow_candidates" / date / "shadow_performance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_source_files(root: Path) -> None:
    run = root / "research" / "shadow_tracking" / "run.py"
    engine = root / "research" / "alpha_lab_v2" / "engine.py"
    signals = root / "research" / "alpha_lab_v1" / "signals.py"
    run.parent.mkdir(parents=True, exist_ok=True)
    engine.parent.mkdir(parents=True, exist_ok=True)
    signals.parent.mkdir(parents=True, exist_ok=True)
    run.write_text(
        '"return_convention": "weights_as_of_t"\ncompute_returns_for_trade_date(panel=panel, trade_date=trade_date)\n',
        encoding="utf-8",
    )
    engine.write_text(
        'frame["date"] <= pd.Timestamp(trade_date)\nreturns_matrix = prices.pct_change().shift(-1)\ntransaction_cost_bps = 10\n',
        encoding="utf-8",
    )
    signals.write_text("shift(21)\nshift(126)\nshift(252)\n", encoding="utf-8")


def test_nav_series_recomputes_ytd_and_drawdown(tmp_path: Path) -> None:
    _write_nav(tmp_path)

    payload = audit_nav_series(tmp_path, through_date="2026-01-06", ytd_year="2026")

    assert payload["classification"] in {"VERIFIED", "PARTIAL_CONFIDENCE"}
    assert payload["strategies"]["caerus_polaris"]["ytd_return"]["value"] == pytest.approx(0.21)
    assert payload["strategies"]["spy_benchmark"]["drawdown"]["max_drawdown"] < 0


def test_dated_chain_invalidates_hidden_no_prior_and_stale(tmp_path: Path) -> None:
    previous = {slug: 1.0 for slug in MODEL_SLUGS}
    _write_performance(tmp_path, "2026-01-05", _performance_payload("2026-01-05", previous, {slug: 0.1 for slug in MODEL_SLUGS}))
    _write_performance(
        tmp_path,
        "2026-01-06",
        _performance_payload(
            "2026-01-06",
            {slug: 1.1 for slug in MODEL_SLUGS},
            {slug: 0.0 for slug in MODEL_SLUGS},
            status="NO_PRIOR",
            data_status="NO_DATA",
            data_reason="PRICE_CACHE_STALE",
        ),
    )

    payload = audit_dated_shadow_chain(tmp_path, through_date="2026-01-06", ytd_year="2026")

    assert payload["classification"] == "INVALIDATED"
    assert any(check["name"] == "hidden_reinitialization_2026-01-06" for check in payload["checks"])
    assert payload["bad_reasons"]


def test_source_reviews_flag_same_day_leakage(tmp_path: Path) -> None:
    _write_source_files(tmp_path)

    execution = audit_execution_assumptions(tmp_path)
    lookahead = audit_lookahead_bias(tmp_path)

    assert execution["classification"] == "INVALIDATED"
    assert lookahead["classification"] == "INVALIDATED"
    assert any(not check["passed"] and check["severity"] == "FAIL" for check in lookahead["checks"])


def test_run_audit_writes_additive_artifacts(tmp_path: Path) -> None:
    _write_nav(tmp_path)
    _write_source_files(tmp_path)
    previous = {slug: 1.0 for slug in MODEL_SLUGS}
    _write_performance(tmp_path, "2026-01-05", _performance_payload("2026-01-05", previous, {slug: 0.1 for slug in MODEL_SLUGS}))

    summary, written = run_audit(
        repo_root=tmp_path,
        output_root=tmp_path / "outputs" / "audits" / "performance_veracity",
        run_id="test-run",
        as_of_date="2026-01-06",
        ytd_year="2026",
    )

    assert summary["overall_classification"] == "INVALIDATED"
    names = {path.name for path in written}
    assert "audit_summary.json" in names
    assert "audit_findings.md" in names
    assert "lookahead_bias_review.json" in names
