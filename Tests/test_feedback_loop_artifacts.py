from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from core.feedback_loop_artifacts import write_feedback_loop_artifacts


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _strategy_payload(slug: str, weights: dict[str, float], turnover: float = 0.2, top3: float = 0.7) -> dict:
    return {
        "strategy_name": {"caerus_polaris": "Caerus Polaris", "caerus_orion": "Caerus Orion", "caerus_lyra": "Caerus Lyra"}[slug],
        "strategy_slug": slug,
        "target_weights": weights,
        "expected_turnover": turnover,
        "weight_concentration": {"top3_concentration": top3},
        "holdings": [
            {
                "ticker": ticker,
                "target_weight": weight,
                "momentum_rank": idx,
                "momentum_score": 1.0 / idx,
            }
            for idx, (ticker, weight) in enumerate(weights.items(), start=1)
        ],
    }


def _base_shadow_files(root: Path, trade_date: str = "2026-05-04", previous: str | None = "2026-05-03") -> Path:
    output_root = root / "outputs" / "shadow_candidates"
    dated = output_root / trade_date
    for slug in ("caerus_polaris", "caerus_orion", "caerus_lyra"):
        _write_json(dated / f"{slug}.json", _strategy_payload(slug, {"AAA": 0.4, "BBB": 0.3, "CCC": 0.2}))
        if previous:
            _write_json(output_root / previous / f"{slug}.json", _strategy_payload(slug, {"AAA": 0.3, "DDD": 0.3, "CCC": 0.2}))
    _write_json(
        dated / "delta.json",
        {
            "trade_date": trade_date,
            "previous_date": previous,
            "status": "OK" if previous else "NO_PRIOR",
            "strategies": {
                slug: {
                    "adds": ["BBB"],
                    "removes": ["DDD"],
                    "unchanged": ["AAA", "CCC"],
                    "increases": [{"ticker": "AAA", "delta_weight": 0.1}],
                    "decreases": [],
                }
                for slug in ("caerus_polaris", "caerus_orion", "caerus_lyra")
            },
        },
    )
    _write_json(
        dated / "shadow_performance.json",
        {
            "trade_date": trade_date,
            "previous_trade_date": previous,
            "status": "OK" if previous else "NO_PRIOR",
            "data_status": "OK",
            "return_convention": "weights_as_of_t",
            "strategies": {
                "caerus_polaris": {"daily_return": 0.01, "nav": 1.01},
                "caerus_orion": {"daily_return": 0.02, "nav": 1.02},
                "caerus_lyra": {"daily_return": -0.01, "nav": 0.99},
                "spy_benchmark": {"daily_return": 0.005, "nav": 1.005},
            },
        },
    )
    _write_json(
        dated / "shadow_evaluation.json",
        {
            "trade_date": trade_date,
            "strategies": {
                "caerus_polaris": {"rolling_count_of_valid_days": 11, "constituent_change_count": 22},
                "caerus_orion": {"rolling_count_of_valid_days": 11, "constituent_change_count": 2},
                "caerus_lyra": {"rolling_count_of_valid_days": 11, "constituent_change_count": 2},
            },
        },
    )
    return output_root


def _panel() -> pd.DataFrame:
    rows = []
    for ticker, end in {"AAA": 101.0, "BBB": 99.0, "CCC": 102.0, "SPY": 100.5}.items():
        rows.append({"date": "2026-05-03", "ticker": ticker, "close": 100.0})
        rows.append({"date": "2026-05-04", "ticker": ticker, "close": end})
    return pd.DataFrame(rows)


def test_feedback_artifacts_write_current_and_prior_decision_trace(tmp_path: Path) -> None:
    output_root = _base_shadow_files(tmp_path)

    summary = write_feedback_loop_artifacts(output_root=output_root, trade_date="2026-05-04", panel=_panel(), repo_root=tmp_path)

    decision = json.loads((output_root / "2026-05-04" / "polaris" / "decision_trace.json").read_text())
    assert decision["status"] == "OK"
    assert decision["prior_positions_available"] is True
    assert decision["changes_vs_prior"]["new_entries"] == ["BBB"]
    assert decision["portfolio_summary"]["position_count"] == 3
    assert decision["signal_detail_status"] == "PARTIAL"
    assert summary["system_learning_summary"]["ready_for_promotion_logic"] is False


def test_feedback_decision_trace_handles_missing_prior_as_no_prior(tmp_path: Path) -> None:
    output_root = _base_shadow_files(tmp_path, previous=None)

    write_feedback_loop_artifacts(output_root=output_root, trade_date="2026-05-04", panel=_panel(), repo_root=tmp_path)

    decision = json.loads((output_root / "2026-05-04" / "orion" / "decision_trace.json").read_text())
    assert decision["status"] == "NO_PRIOR"
    assert decision["prior_positions_available"] is False


def test_feedback_attribution_unavailable_without_asset_returns(tmp_path: Path) -> None:
    output_root = _base_shadow_files(tmp_path)

    write_feedback_loop_artifacts(output_root=output_root, trade_date="2026-05-04", panel=None, repo_root=tmp_path)

    attribution = json.loads((output_root / "2026-05-04" / "lyra" / "attribution.json").read_text())
    assert attribution["status"] == "UNAVAILABLE"
    assert attribution["decision_contribution"]["status"] == "UNAVAILABLE"


def test_feedback_stability_flags_are_deterministic(tmp_path: Path) -> None:
    output_root = _base_shadow_files(tmp_path)

    write_feedback_loop_artifacts(output_root=output_root, trade_date="2026-05-04", panel=_panel(), repo_root=tmp_path)

    stability = json.loads((output_root / "2026-05-04" / "polaris" / "stability_analysis.json").read_text())
    assert "INSUFFICIENT_VALID_DAYS" in stability["flags"]
    assert "HIGH_CONCENTRATION" in stability["flags"]
    assert "HIGH_CONSTITUENT_CHURN" in stability["flags"]


def test_feedback_regime_writes_no_regime_data_when_missing(tmp_path: Path) -> None:
    output_root = _base_shadow_files(tmp_path)

    write_feedback_loop_artifacts(output_root=output_root, trade_date="2026-05-04", panel=_panel(), repo_root=tmp_path)

    regime = json.loads((output_root / "2026-05-04" / "polaris" / "regime_performance.json").read_text())
    assert regime["status"] == "NO_REGIME_DATA"


def test_feedback_summary_never_enables_promotion_logic(tmp_path: Path) -> None:
    output_root = _base_shadow_files(tmp_path)

    summary = write_feedback_loop_artifacts(output_root=output_root, trade_date="2026-05-04", panel=_panel(), repo_root=tmp_path)

    assert summary["system_learning_summary"]["ready_for_promotion_logic"] is False


def test_feedback_loop_writes_compact_rolling_index(tmp_path: Path) -> None:
    output_root = _base_shadow_files(tmp_path)

    write_feedback_loop_artifacts(output_root=output_root, trade_date="2026-05-04", panel=_panel(), repo_root=tmp_path)
    write_feedback_loop_artifacts(output_root=output_root, trade_date="2026-05-04", panel=_panel(), repo_root=tmp_path)

    csv_text = (output_root / "performance" / "feedback_loop_rolling_index.csv").read_text(encoding="utf-8")
    payload = json.loads((output_root / "performance" / "feedback_loop_rolling_index.json").read_text(encoding="utf-8"))

    assert "trade_date,strategy_slug,strategy,daily_return,turnover,top_3_concentration,valid_days,attribution_status,regime,learning_readiness" in csv_text
    assert payload["schema_version"] == "feedback_loop_rolling_index_v1"
    assert payload["row_count"] == 3
    assert {row["strategy_slug"] for row in payload["rows"]} == {"caerus_polaris", "caerus_orion", "caerus_lyra"}
