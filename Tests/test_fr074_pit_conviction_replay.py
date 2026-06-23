from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.research.build_fr074_pit_conviction_replay import (
    DecisionSnapshot,
    PolicySpec,
    allocate_from_scores,
    candidate_table,
    discover_shadow_snapshots,
    replay_policy,
)


STRATEGIES = ("caerus_polaris", "caerus_orion", "caerus_lyra")


def _strategy_payload(trade_date: str, weights: dict[str, float], rank_order: list[str]) -> dict[str, object]:
    return {
        "trade_date": trade_date,
        "effective_trade_date": trade_date,
        "strategy_slug": "fixture",
        "target_weights": weights,
        "holdings": [
            {
                "ticker": ticker,
                "target_weight": weight,
                "momentum_rank": float(rank_order.index(ticker) + 1),
                "momentum_score": float(10 - rank_order.index(ticker)),
            }
            for ticker, weight in weights.items()
        ],
        "rank_table": [
            {
                "ticker": ticker,
                "momentum_rank": float(index + 1),
                "momentum_score": float(10 - index),
                "is_selected": ticker in weights,
            }
            for index, ticker in enumerate(rank_order)
        ],
    }


def _snapshot(trade_date: str, rank_order: list[str] | None = None) -> DecisionSnapshot:
    rank_order = rank_order or ["AAA", "BBB", "CCC", "DDD", "EEE"]
    payloads = {
        "caerus_polaris": _strategy_payload(trade_date, {"AAA": 0.5, "BBB": 0.5}, rank_order),
        "caerus_orion": _strategy_payload(trade_date, {"AAA": 1.0}, rank_order),
        "caerus_lyra": _strategy_payload(trade_date, {"CCC": 1.0}, rank_order),
    }
    return DecisionSnapshot(trade_date=trade_date, strategy_payloads=payloads, source_paths={})


def _write_snapshot(root: Path, trade_date: str, complete: bool = True) -> None:
    dated = root / "outputs" / "shadow_candidates" / trade_date
    dated.mkdir(parents=True)
    strategies = STRATEGIES if complete else STRATEGIES[:2]
    for strategy in strategies:
        (dated / f"{strategy}.json").write_text(
            json.dumps(_strategy_payload(trade_date, {"AAA": 1.0}, ["AAA", "BBB", "CCC"])),
            encoding="utf-8",
        )


def test_discover_shadow_snapshots_requires_complete_strategy_artifacts(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, "2026-01-02", complete=True)
    _write_snapshot(tmp_path, "2026-01-03", complete=False)

    snapshots, inventory = discover_shadow_snapshots(tmp_path)

    assert [snapshot.trade_date for snapshot in snapshots] == ["2026-01-02"]
    by_date = {row["trade_date"]: row for row in inventory}
    assert by_date["2026-01-02"]["complete_for_replay"] is True
    assert by_date["2026-01-03"]["complete_for_replay"] is False


def test_candidate_table_uses_best_rank_and_max_score_across_sleeves() -> None:
    snapshot = _snapshot("2026-01-02")

    candidates = candidate_table(snapshot).set_index("ticker")

    assert float(candidates.loc["AAA", "best_rank"]) == 1.0
    assert float(candidates.loc["AAA", "max_score"]) == 10.0
    assert int(candidates.loc["AAA", "source_count"]) == 3
    assert int(candidates.loc["AAA", "selected_source_count"]) == 2


def test_allocate_from_scores_respects_cap_floor_and_cash_when_cap_binds() -> None:
    scores = pd.Series({"AAA": 100.0, "BBB": 20.0, "CCC": 5.0})

    weights = allocate_from_scores(scores, max_position_weight=0.20, min_position_weight=0.10)

    assert not weights.empty
    assert float(weights.max()) <= 0.20 + 1e-12
    assert float(weights.min()) >= 0.10 - 1e-12
    assert float(weights.sum()) <= 0.60 + 1e-12


def test_replay_policy_carries_prior_artifact_weights_until_next_snapshot() -> None:
    snapshots = [
        _snapshot("2026-01-02", ["AAA", "BBB", "CCC"]),
        _snapshot("2026-01-06", ["BBB", "AAA", "CCC"]),
    ]
    close = pd.DataFrame(
        {
            "AAA": [100.0, 101.0, 102.0, 103.0, 104.0],
            "BBB": [100.0, 99.0, 98.0, 120.0, 121.0],
            "CCC": [100.0, 100.0, 100.0, 100.0, 100.0],
        },
        index=pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]),
    )
    spec = PolicySpec(
        policy_id="conviction_rank_weighted_cap40_min0",
        policy_family="conviction",
        method="rank_weighted",
        max_position_weight=0.40,
        min_position_weight=0.0,
    )

    replay = replay_policy(snapshots, close, spec=spec, transaction_cost_bps=0.0)

    weights = replay["weights"]
    turnover = replay["turnover"]
    assert list(weights.index.strftime("%Y-%m-%d")) == ["2026-01-02", "2026-01-05", "2026-01-06"]
    assert weights.loc[pd.Timestamp("2026-01-05"), "AAA"] == weights.loc[pd.Timestamp("2026-01-02"), "AAA"]
    assert turnover.loc[pd.Timestamp("2026-01-05")] == 0.0
    assert turnover.loc[pd.Timestamp("2026-01-06")] > 0.0
