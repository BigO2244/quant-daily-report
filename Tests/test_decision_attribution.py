from __future__ import annotations

import json
from pathlib import Path

from research.attribution.decision import build_decision_attribution


def _write_shadow_candidate(root: Path, trade_date: str, strategy: str, holdings: list[dict]) -> Path:
    path = root / "outputs" / "shadow_candidates" / trade_date / f"{strategy}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    rank_table = [
        {
            "ticker": row["ticker"],
            "momentum_score": row.get("momentum_score"),
            "momentum_rank": row.get("momentum_rank"),
            "is_selected": True,
        }
        for row in holdings
    ]
    path.write_text(
        json.dumps(
            {
                "strategy_slug": strategy,
                "trade_date": trade_date,
                "holdings": holdings,
                "rank_table": rank_table,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _write_portfolio_holdings(root: Path, trade_date: str, strategies: dict) -> Path:
    path = root / "outputs" / "portfolio_history" / trade_date / "holdings_snapshot.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "test",
                "trade_date": trade_date,
                "strategies": strategies,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _write_attribution(root: Path, trade_date: str, positions: list[dict]) -> Path:
    path = root / "outputs" / "attribution" / trade_date / "position_attribution.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "position_pnl_attribution_phase_a_v1",
                "date": trade_date,
                "positions": positions,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def test_normal_decision_reconstruction_joins_realized_outcomes(tmp_path):
    trade_date = "2026-06-01"
    _write_shadow_candidate(
        tmp_path,
        trade_date,
        "caerus_polaris",
        [
            {"ticker": "AAA", "target_weight": 0.6, "momentum_rank": 1, "momentum_score": 2.0},
            {"ticker": "BBB", "target_weight": 0.4, "momentum_rank": 2, "momentum_score": 1.0},
        ],
    )
    _write_attribution(
        tmp_path,
        trade_date,
        [
            {
                "strategy": "caerus_polaris",
                "symbol": "AAA",
                "return_pct": 0.1,
                "pnl_contribution_pct": 0.06,
                "reason_codes": ["ok"],
                "source_artifacts": ["price_source"],
            },
            {
                "strategy": "caerus_polaris",
                "symbol": "BBB",
                "return_pct": -0.05,
                "pnl_contribution_pct": -0.02,
                "reason_codes": ["ok"],
                "source_artifacts": ["price_source"],
            },
        ],
    )

    result = build_decision_attribution(trade_date=trade_date, repo_root=tmp_path)
    records = result["decision_attribution"]["decisions"]
    signal_summary = result["signal_outcome_summary"]["signals"]
    strategy_summary = result["strategy_decision_summary"]["strategies"][0]

    assert result["decision_attribution"]["reason_codes"] == ["ok"]
    assert [row["symbol"] for row in records] == ["AAA", "BBB"]
    assert records[0]["rank"] == 1.0
    assert records[0]["weight"] == 0.6
    assert records[0]["signal_snapshot"]["momentum_score"] == 2.0
    assert records[0]["realized_return"] == 0.1
    assert records[0]["pnl_contribution"] == 0.06
    assert records[0]["confidence"] == "MEDIUM"

    by_signal = {row["signal_name"]: row for row in signal_summary}
    assert by_signal["momentum_score"]["observations"] == 2
    assert by_signal["momentum_score"]["average_score"] == 1.5
    assert by_signal["momentum_score"]["average_realized_return"] == 0.025
    assert by_signal["momentum_score"]["hit_rate"] == 0.5
    assert by_signal["momentum_score"]["confidence"] == "LOW"
    assert "insufficient_observations" in by_signal["momentum_score"]["reason_codes"]

    assert strategy_summary["strategy"] == "caerus_polaris"
    assert strategy_summary["decisions_analyzed"] == 2
    assert strategy_summary["average_realized_return"] == 0.025
    assert strategy_summary["average_pnl_contribution"] == 0.02
    assert strategy_summary["hit_rate"] == 0.5
    assert strategy_summary["top_decision"]["symbol"] == "AAA"
    assert strategy_summary["worst_decision"]["symbol"] == "BBB"

    out_dir = tmp_path / "outputs" / "decision_attribution" / trade_date
    for name in (
        "decision_attribution.json",
        "signal_outcome_summary.json",
        "strategy_decision_summary.json",
    ):
        json.loads((out_dir / name).read_text())


def test_missing_signal_data_marks_low_confidence(tmp_path):
    trade_date = "2026-06-01"
    _write_portfolio_holdings(
        tmp_path,
        trade_date,
        {"caerus_orion": {"holdings": [{"ticker": "AAA", "target_weight": 1.0}]}},
    )
    _write_attribution(
        tmp_path,
        trade_date,
        [
            {
                "strategy": "caerus_orion",
                "symbol": "AAA",
                "return_pct": 0.02,
                "pnl_contribution_pct": 0.02,
                "reason_codes": ["ok"],
            }
        ],
    )

    result = build_decision_attribution(trade_date=trade_date, repo_root=tmp_path)
    record = result["decision_attribution"]["decisions"][0]

    assert record["signal_snapshot"] == {}
    assert record["confidence"] == "LOW"
    assert record["reason_codes"] == ["missing_rank", "signal_snapshot_missing"]
    assert result["signal_outcome_summary"]["signals"] == []
    assert "signal_snapshot_missing" in result["decision_attribution"]["reason_codes"]


def test_missing_attribution_data_keeps_decisions_with_reason_codes(tmp_path):
    trade_date = "2026-06-01"
    _write_shadow_candidate(
        tmp_path,
        trade_date,
        "caerus_lyra",
        [{"ticker": "AAA", "target_weight": 1.0, "momentum_rank": 1, "momentum_score": 3.0}],
    )

    result = build_decision_attribution(trade_date=trade_date, repo_root=tmp_path)
    record = result["decision_attribution"]["decisions"][0]
    strategy_summary = result["strategy_decision_summary"]["strategies"][0]

    assert record["symbol"] == "AAA"
    assert record["realized_return"] is None
    assert record["pnl_contribution"] is None
    assert record["confidence"] == "LOW"
    assert "attribution_source_missing" in record["reason_codes"]
    assert "missing_realized_outcome" in record["reason_codes"]
    assert strategy_summary["confidence"] == "LOW"
    assert strategy_summary["top_decision"] is None
    assert "no_realized_outcomes" in strategy_summary["reason_codes"]


def test_decisions_are_sorted_deterministically(tmp_path):
    trade_date = "2026-06-01"
    _write_shadow_candidate(
        tmp_path,
        trade_date,
        "caerus_polaris",
        [
            {"ticker": "CCC", "target_weight": 0.3, "momentum_rank": 3, "momentum_score": 1.0},
            {"ticker": "AAA", "target_weight": 0.3, "momentum_rank": 1, "momentum_score": 3.0},
            {"ticker": "BBB", "target_weight": 0.3, "momentum_rank": 1, "momentum_score": 2.0},
        ],
    )
    _write_attribution(
        tmp_path,
        trade_date,
        [
            {"strategy": "caerus_polaris", "symbol": "CCC", "return_pct": 0.03, "pnl_contribution_pct": 0.009, "reason_codes": ["ok"]},
            {"strategy": "caerus_polaris", "symbol": "AAA", "return_pct": 0.01, "pnl_contribution_pct": 0.003, "reason_codes": ["ok"]},
            {"strategy": "caerus_polaris", "symbol": "BBB", "return_pct": 0.02, "pnl_contribution_pct": 0.006, "reason_codes": ["ok"]},
        ],
    )

    result = build_decision_attribution(trade_date=trade_date, repo_root=tmp_path)

    assert [row["symbol"] for row in result["decision_attribution"]["decisions"]] == ["AAA", "BBB", "CCC"]


def test_confidence_moves_to_medium_only_when_complete_and_sufficient(tmp_path):
    trade_date = "2026-06-01"
    _write_shadow_candidate(
        tmp_path,
        trade_date,
        "caerus_orion",
        [
            {"ticker": "AAA", "target_weight": 0.3, "momentum_rank": 1, "momentum_score": 3.0},
            {"ticker": "BBB", "target_weight": 0.3, "momentum_rank": 2, "momentum_score": 2.0},
            {"ticker": "CCC", "target_weight": 0.3, "momentum_rank": 3, "momentum_score": 1.0},
        ],
    )
    _write_attribution(
        tmp_path,
        trade_date,
        [
            {"strategy": "caerus_orion", "symbol": "AAA", "return_pct": 0.03, "pnl_contribution_pct": 0.009, "reason_codes": ["ok"]},
            {"strategy": "caerus_orion", "symbol": "BBB", "return_pct": -0.02, "pnl_contribution_pct": -0.006, "reason_codes": ["ok"]},
            {"strategy": "caerus_orion", "symbol": "CCC", "return_pct": 0.01, "pnl_contribution_pct": 0.003, "reason_codes": ["ok"]},
        ],
    )

    result = build_decision_attribution(trade_date=trade_date, repo_root=tmp_path)
    by_signal = {row["signal_name"]: row for row in result["signal_outcome_summary"]["signals"]}
    strategy_summary = result["strategy_decision_summary"]["strategies"][0]

    assert all(row["confidence"] == "MEDIUM" for row in result["decision_attribution"]["decisions"])
    assert by_signal["momentum_score"]["confidence"] == "MEDIUM"
    assert by_signal["momentum_score"]["reason_codes"] == ["ok"]
    assert strategy_summary["confidence"] == "MEDIUM"
    assert strategy_summary["reason_codes"] == ["ok"]
