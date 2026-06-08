from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

from research_registry.research.strategy_differentiation_deep_dive import build_strategy_differentiation_deep_dive


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_registry(root: Path, strategies: list[str] | None = None) -> None:
    strategies = strategies or ["caerus_polaris", "caerus_orion", "caerus_lyra", "caerus_phoenix"]
    entries = []
    for idx, strategy in enumerate(strategies):
        short = strategy.replace("caerus_", "")
        entries.append(
            {
                "strategy_id": strategy,
                "display_name": short.title(),
                "short_name": short,
                "strategy_type": "security_selection",
                "family": "crisis_reversal" if strategy == "caerus_phoenix" else "core_momentum",
                "status": "paper" if strategy == "caerus_polaris" else "shadow" if strategy in {"caerus_orion", "caerus_lyra"} else "research",
                "role": "baseline" if strategy == "caerus_polaris" else "challenger",
                "eligible_for_shadow": True,
                "eligible_for_promotion": strategy in {"caerus_orion", "caerus_lyra"},
                "benchmark": "SPY",
                "execution_impact": "NON_EXECUTIONAL",
                "display_order": idx + 1,
                "capabilities": {"produces_holdings": True, "produces_nav": True, "produces_attribution": True},
                "shadow_tracking": {"enabled": True, "source_variant": short},
            }
        )
    _write_json(root / "config" / "research" / "strategy_registry.json", {"schema_version": "caerus_strategy_registry_v1", "strategies": entries})


def _snapshot(holdings: list[tuple[str, float]], turnover: float = 0.1) -> dict:
    return {
        "holdings": [
            {"ticker": ticker, "target_weight": weight, "sector": "Tech" if ticker in {"AAA", "BBB"} else "Health"}
            for ticker, weight in holdings
        ],
        "target_weights": {ticker: weight for ticker, weight in holdings},
        "expected_turnover": turnover,
    }


def _write_snapshot(root: Path, strategy: str, holdings: list[tuple[str, float]], turnover: float = 0.1) -> None:
    _write_json(root / "outputs" / "shadow_candidates" / "2026-06-08" / f"{strategy}.json", _snapshot(holdings, turnover))


def _write_nav(root: Path, *, identical_lyra_orion: bool = True) -> None:
    path = root / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    start = dt.date(2026, 4, 1)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "caerus_polaris", "caerus_orion", "caerus_lyra", "caerus_phoenix"])
        writer.writeheader()
        polaris = orion = lyra = phoenix = 1.0
        for idx in range(45):
            day = start + dt.timedelta(days=idx)
            polaris *= 1.001 + (0.0001 if idx % 2 else 0)
            orion *= 1.002 + (0.0002 if idx % 3 else 0)
            if identical_lyra_orion:
                lyra = orion
            else:
                lyra *= 0.999 - (0.0001 if idx % 2 else 0)
            phoenix *= 1.0 + (0.01 if idx % 2 else -0.008)
            writer.writerow(
                {
                    "date": day.isoformat(),
                    "caerus_polaris": f"{polaris:.8f}",
                    "caerus_orion": f"{orion:.8f}",
                    "caerus_lyra": f"{lyra:.8f}",
                    "caerus_phoenix": f"{phoenix:.8f}",
                }
            )


def _pair(payload: dict, left: str, right: str) -> dict:
    wanted = {left, right}
    return next(row for row in payload["pairwise"] if {row["left_strategy_id"], row["right_strategy_id"]} == wanted)


def test_strategy_differentiation_lyra_orion_near_duplicate_fixture(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    _write_snapshot(tmp_path, "caerus_lyra", [("AAA", 0.5), ("BBB", 0.5)])
    _write_snapshot(tmp_path, "caerus_orion", [("AAA", 0.5), ("BBB", 0.5)])
    _write_nav(tmp_path, identical_lyra_orion=True)

    payload = build_strategy_differentiation_deep_dive(trade_date="2026-06-08", repo_root=tmp_path)
    pair = _pair(payload, "caerus_lyra", "caerus_orion")

    assert pair["redundancy_classification"] == "NEAR_DUPLICATE"
    assert pair["active_share"]["active_share"] == 0.0
    assert payload["retirement_watchlist"][0]["decision_grade"] is False


def test_strategy_differentiation_distinct_phoenix_fixture(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    _write_snapshot(tmp_path, "caerus_polaris", [("AAA", 0.5), ("BBB", 0.5)])
    _write_snapshot(tmp_path, "caerus_phoenix", [("CCC", 0.5), ("DDD", 0.5)])
    _write_nav(tmp_path, identical_lyra_orion=False)

    payload = build_strategy_differentiation_deep_dive(trade_date="2026-06-08", repo_root=tmp_path)
    pair = _pair(payload, "caerus_polaris", "caerus_phoenix")

    assert pair["redundancy_classification"] == "DISTINCT"
    assert pair["holdings_overlap"]["common_symbols"] == []


def test_strategy_differentiation_missing_strategy_history(tmp_path: Path) -> None:
    _write_registry(tmp_path, ["caerus_polaris", "caerus_phoenix"])
    _write_snapshot(tmp_path, "caerus_polaris", [("AAA", 1.0)])

    payload = build_strategy_differentiation_deep_dive(trade_date="2026-06-08", repo_root=tmp_path)
    pair = _pair(payload, "caerus_polaris", "caerus_phoenix")

    assert pair["redundancy_classification"] == "INSUFFICIENT_EVIDENCE"
    assert "CAERUS_PHOENIX_SNAPSHOT_MISSING" in pair["reason_codes"]


def test_strategy_differentiation_identical_strategy_case(tmp_path: Path) -> None:
    _write_registry(tmp_path, ["caerus_polaris", "caerus_orion"])
    _write_snapshot(tmp_path, "caerus_polaris", [("AAA", 1.0)])
    _write_snapshot(tmp_path, "caerus_orion", [("AAA", 1.0)])

    payload = build_strategy_differentiation_deep_dive(trade_date="2026-06-08", repo_root=tmp_path)
    pair = _pair(payload, "caerus_polaris", "caerus_orion")

    assert pair["redundancy_classification"] == "NEAR_DUPLICATE"
    assert pair["holdings_overlap"]["weight_overlap"] == 1.0


def test_strategy_differentiation_no_overlap_case(tmp_path: Path) -> None:
    _write_registry(tmp_path, ["caerus_polaris", "caerus_orion"])
    _write_snapshot(tmp_path, "caerus_polaris", [("AAA", 1.0)])
    _write_snapshot(tmp_path, "caerus_orion", [("ZZZ", 1.0)])

    payload = build_strategy_differentiation_deep_dive(trade_date="2026-06-08", repo_root=tmp_path)
    pair = _pair(payload, "caerus_polaris", "caerus_orion")

    assert pair["redundancy_classification"] == "DISTINCT"
    assert "NO_HOLDINGS_OVERLAP" in pair["holdings_overlap"]["reason_codes"]


def test_strategy_differentiation_deterministic_pair_ordering(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    _write_snapshot(tmp_path, "caerus_lyra", [("BBB", 0.5), ("AAA", 0.5)])
    _write_snapshot(tmp_path, "caerus_orion", [("AAA", 0.5), ("BBB", 0.5)])

    first = build_strategy_differentiation_deep_dive(trade_date="2026-06-08", repo_root=tmp_path, write=False)
    second = build_strategy_differentiation_deep_dive(trade_date="2026-06-08", repo_root=tmp_path, write=False)

    assert [row["pair_id"] for row in first["pairwise"]] == sorted(row["pair_id"] for row in first["pairwise"])
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_strategy_differentiation_sparse_evidence_prevents_decision_grade_retirement(tmp_path: Path) -> None:
    _write_registry(tmp_path, ["caerus_lyra", "caerus_orion"])
    _write_snapshot(tmp_path, "caerus_lyra", [("AAA", 1.0)])
    _write_snapshot(tmp_path, "caerus_orion", [("AAA", 1.0)])

    payload = build_strategy_differentiation_deep_dive(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["decision_grade_retirement_recommendation"] is False
    assert payload["retirement_watchlist"][0]["decision_grade"] is False
    assert "WATCHLIST_ONLY_NOT_RETIREMENT_RECOMMENDATION" in payload["retirement_watchlist"][0]["reason_codes"]
