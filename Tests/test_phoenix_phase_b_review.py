from __future__ import annotations

import csv
import json
from pathlib import Path

from research_registry.research.phoenix_phase_b_review import build_phoenix_phase_b_review


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_nav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "phoenix_nav"])
        writer.writeheader()
        writer.writerows(
            [
                {"date": "2026-06-01", "phoenix_nav": "1.00"},
                {"date": "2026-06-02", "phoenix_nav": "0.94"},
                {"date": "2026-06-03", "phoenix_nav": "1.03"},
            ]
        )


def _phoenix(active: bool, tickers: list[str], reason_codes: list[str] | None = None) -> dict:
    return {
        "active": active,
        "reason_codes": reason_codes or (["crisis_reversal_dislocation"] if active else ["NO_CRISIS_REGIME"]),
        "target_candidates": [
            {
                "ticker": ticker,
                "target_weight": 0.5,
                "phoenix_score": 0.9 - idx * 0.1,
                "sector": "Tech" if ticker != "CCC" else "Health",
                "reason_codes": ["oversold_reversal"],
            }
            for idx, ticker in enumerate(tickers)
        ],
    }


def _shadow(tickers: list[str]) -> dict:
    return {
        "holdings": [{"ticker": ticker, "target_weight": 1.0 / len(tickers)} for ticker in tickers],
        "target_weights": {ticker: 1.0 / len(tickers) for ticker in tickers},
    }


def _base_history(root: Path, *, include_regime: bool = True, include_price: bool = True) -> None:
    _write_json(root / "outputs" / "model_quality" / "2026-06-01" / "phoenix_research.json", _phoenix(True, ["AAA", "BBB"]))
    _write_json(root / "outputs" / "model_quality" / "2026-06-02" / "phoenix_research.json", _phoenix(False, []))
    _write_json(root / "outputs" / "model_quality" / "2026-06-03" / "phoenix_research.json", _phoenix(True, ["CCC"]))
    for date, regime in (("2026-06-01", "HIGH"), ("2026-06-02", "LOW"), ("2026-06-03", "CRISIS")):
        if include_regime:
            _write_json(root / "outputs" / "vix_regime" / date / "regime_current.json", {"date": date, "regime": regime, "vix": 25.0})
    for date in ("2026-06-01", "2026-06-03"):
        _write_json(root / "outputs" / "shadow_candidates" / date / "caerus_polaris.json", _shadow(["AAA", "ZZZ"]))
        _write_json(root / "outputs" / "shadow_candidates" / date / "caerus_orion.json", _shadow(["YYY"]))
        _write_json(root / "outputs" / "shadow_candidates" / date / "caerus_lyra.json", _shadow(["CCC", "XXX"]))
    if include_price:
        _write_nav(root / "outputs" / "research" / "phoenix" / "performance" / "phoenix_nav_series.csv")


def test_phoenix_phase_b_populated_history(tmp_path: Path) -> None:
    _base_history(tmp_path)

    payload = build_phoenix_phase_b_review(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["strategy_id"] == "caerus_phoenix"
    assert payload["governance_label"] == "RESEARCH_ONLY"
    assert payload["execution_impact"] == "NON_EXECUTIONAL"
    assert payload["active_days"] == 2
    assert payload["inactive_days"] == 1
    assert payload["candidate_count_distribution"] == [{"candidate_count": 0, "days": 1}, {"candidate_count": 1, "days": 1}, {"candidate_count": 2, "days": 1}]
    assert payload["top_candidates"][0]["ticker"] == "AAA"
    assert payload["regime_summary"]["available"] is True
    assert payload["drawdown_recovery_summary"]["available"] is True
    assert (tmp_path / "outputs" / "model_quality" / "2026-06-08" / "phoenix_phase_b_review.json").exists()


def test_phoenix_phase_b_no_history_degrades(tmp_path: Path) -> None:
    payload = build_phoenix_phase_b_review(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["active_days"] == 0
    assert payload["decision_grade"] is False
    assert "PHOENIX_HISTORY_MISSING" in payload["reason_codes"]


def test_phoenix_phase_b_active_inactive_mixed_history(tmp_path: Path) -> None:
    _base_history(tmp_path)

    payload = build_phoenix_phase_b_review(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["review_window"]["observation_days"] == 3
    assert [row["candidate_count"] for row in payload["candidate_count_distribution"]] == [0, 1, 2]


def test_phoenix_phase_b_missing_regime_data(tmp_path: Path) -> None:
    _base_history(tmp_path, include_regime=False)

    payload = build_phoenix_phase_b_review(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["regime_summary"]["available"] is False
    assert "REGIME_DATA_MISSING" in payload["reason_codes"]


def test_phoenix_phase_b_missing_price_data(tmp_path: Path) -> None:
    _base_history(tmp_path, include_price=False)

    payload = build_phoenix_phase_b_review(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["drawdown_recovery_summary"]["available"] is False
    assert "PRICE_DATA_MISSING" in payload["reason_codes"]


def test_phoenix_phase_b_deterministic_ordering(tmp_path: Path) -> None:
    _base_history(tmp_path)

    first = build_phoenix_phase_b_review(trade_date="2026-06-08", repo_root=tmp_path, write=False)
    second = build_phoenix_phase_b_review(trade_date="2026-06-08", repo_root=tmp_path, write=False)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_phoenix_phase_b_sparse_evidence_not_decision_grade(tmp_path: Path) -> None:
    _base_history(tmp_path)

    payload = build_phoenix_phase_b_review(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["decision_grade"] is False
    assert any(code.startswith("SPARSE_PHOENIX_HISTORY") for code in payload["reason_codes"])
