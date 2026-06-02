from __future__ import annotations

import json
from pathlib import Path

from research.position_sizing_research import build_position_sizing_research


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_inputs(root: Path, trade_date: str) -> None:
    holdings = [
        ("AAA", 0.5, 0.04),
        ("BBB", 0.1, 0.01),
        ("CCC", 0.1, -0.02),
        ("DDD", 0.1, 0.02),
        ("EEE", 0.1, 0.03),
        ("FFF", 0.1, 0.00),
    ]
    _write_json(
        root / "outputs" / "shadow_candidates" / trade_date / "comparison.json",
        {"strategies": {"caerus_lyra": {"holdings": [{"ticker": symbol, "target_weight": weight} for symbol, weight, _ret in holdings]}}},
    )
    _write_json(
        root / "outputs" / "attribution" / trade_date / "position_attribution.json",
        {
            "date": trade_date,
            "positions": [
                {"strategy": "caerus_lyra", "symbol": symbol, "weight": weight, "return_pct": ret}
                for symbol, weight, ret in holdings
            ],
        },
    )


def test_position_sizing_equal_weight_baseline_is_deterministic(tmp_path):
    trade_date = "2026-06-02"
    _write_inputs(tmp_path, trade_date)

    first = build_position_sizing_research(trade_date=trade_date, repo_root=tmp_path)
    second = build_position_sizing_research(trade_date=trade_date, repo_root=tmp_path)
    equal = first["strategies"]["caerus_lyra"]["alternatives"]["equal_weight"]

    assert first == second
    assert first["available"] is True
    assert list(equal["weights"]) == ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    assert equal["weights"]["AAA"] == equal["weights"]["FFF"]


def test_position_sizing_concentration_cap_reduces_overweight_name(tmp_path):
    trade_date = "2026-06-02"
    _write_inputs(tmp_path, trade_date)

    payload = build_position_sizing_research(trade_date=trade_date, repo_root=tmp_path)

    capped = payload["strategies"]["caerus_lyra"]["alternatives"]["concentration_capped_proxy"]
    current = payload["strategies"]["caerus_lyra"]["alternatives"]["current_model_weights"]
    assert capped["weights"]["AAA"] < current["weights"]["AAA"]
    assert capped["top3_weight"] <= current["top3_weight"]


def test_position_sizing_missing_returns_handled(tmp_path):
    trade_date = "2026-06-02"
    _write_json(
        tmp_path / "outputs" / "shadow_candidates" / trade_date / "comparison.json",
        {"strategies": {"caerus_lyra": {"holdings": [{"ticker": "AAA", "target_weight": 1.0}]}}},
    )

    payload = build_position_sizing_research(trade_date=trade_date, repo_root=tmp_path)

    assert payload["available"] is False
    assert "position_returns_missing" in payload["reason_codes"]


def test_position_sizing_missing_holdings_does_not_touch_execution_files(tmp_path):
    payload = build_position_sizing_research(trade_date="2026-06-02", repo_root=tmp_path)

    assert payload["available"] is False
    assert not (tmp_path / "outputs" / "precompute").exists()
