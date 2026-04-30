from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.live_vs_shadow_reconciliation import build_reconciliation, render_markdown, write_artifacts


def _write_shadow_day(root: Path, date: str, *, polaris_return: float, spy_return: float, weights: dict[str, float]) -> None:
    day = root / date
    day.mkdir(parents=True, exist_ok=True)
    status = "NO_PRIOR" if date == "2026-04-23" else "OK"
    (day / "shadow_performance.json").write_text(
        json.dumps(
            {
                "trade_date": date,
                "status": status,
                "data_status": "OK",
                "return_convention": "weights_as_of_t",
                "strategies": {
                    "caerus_polaris": {
                        "strategy_name": "Caerus Polaris",
                        "daily_return": polaris_return,
                        "nav": 1.0 + polaris_return,
                    },
                    "spy_benchmark": {
                        "strategy_name": "SPY",
                        "daily_return": spy_return,
                        "nav": 1.0 + spy_return,
                    },
                },
            }
        )
    )
    (day / "caerus_polaris.json").write_text(
        json.dumps(
            {
                "strategy_name": "Caerus Polaris",
                "strategy_slug": "caerus_polaris",
                "trade_date": date,
                "target_weights": weights,
            }
        )
    )


def _write_live_nav(path: Path, rows: list[tuple[str, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"date": date, "return_1d": ret, "equity": 10000.0} for date, ret in rows]).to_csv(path, index=False)


def _write_positions(path: Path, weights: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    total = 10000.0
    payload = {
        "positions": [
            {
                "symbol": symbol,
                "market_value": weight * total,
                "qty": 1,
                "current_price": weight * total,
            }
            for symbol, weight in weights.items()
        ]
    }
    path.write_text(json.dumps(payload))


def _write_live_targets(root: Path, date: str, weights: dict[str, float], *, live_strategy_id: str = "growth_engine_v4", tracks_shadow: bool = False) -> None:
    day = root / date
    day.mkdir(parents=True, exist_ok=True)
    (day / "signals.json").write_text(
        json.dumps(
            {
                "snapshot_date": date,
                "strategy_identity": {
                    "live_strategy_id": live_strategy_id,
                    "execution_target_source": f"{day}/signals.json",
                    "execution_target_type": "precompute_signals",
                    "shadow_baseline_strategy": "caerus_polaris",
                    "shadow_baseline_source": f"shadow/{date}/caerus_polaris.json",
                    "live_tracks_shadow_baseline": tracks_shadow,
                },
                "signals": [
                    {"ticker": symbol, "target_weight": weight}
                    for symbol, weight in weights.items()
                ],
            }
        )
    )


def _base_case(tmp_path: Path, *, live_returns: list[float], shadow_returns: list[float], live_weights: dict[str, float], shadow_weights: dict[str, float]):
    shadow_dir = tmp_path / "shadow"
    dates = ["2026-04-23", "2026-04-24"]
    for date, shadow_return in zip(dates, shadow_returns):
        _write_shadow_day(shadow_dir, date, polaris_return=shadow_return, spy_return=0.001, weights=shadow_weights)
    live_nav = tmp_path / "live_nav.csv"
    _write_live_nav(live_nav, list(zip(dates, live_returns)))
    positions = tmp_path / "positions.json"
    _write_positions(positions, live_weights)
    return build_reconciliation(
        trade_date="2026-04-24",
        shadow_dir=shadow_dir,
        precompute_dir=tmp_path / "missing_precompute",
        live_nav_path=live_nav,
        broker_positions_path=positions,
    )


def test_reconciled_case(tmp_path: Path) -> None:
    payload = _base_case(
        tmp_path,
        live_returns=[0.010, 0.010],
        shadow_returns=[0.009, 0.010],
        live_weights={"AAA": 0.5, "BBB": 0.5},
        shadow_weights={"AAA": 0.5, "BBB": 0.5},
    )
    assert payload["status"] == "RECONCILED"
    assert payload["classification"] == "RECONCILED"
    assert payload["live_strategy_id"] == "growth_engine_v4"
    assert payload["shadow_baseline_strategy"] == "caerus_polaris"
    assert payload["generated_at"].endswith("Z")
    assert "RETURNS_RECONCILED" in payload["reason_codes"]
    assert "HOLDINGS_RECONCILED" in payload["reason_codes"]


def test_minor_drift_case(tmp_path: Path) -> None:
    payload = _base_case(
        tmp_path,
        live_returns=[0.010, 0.012],
        shadow_returns=[0.006, 0.006],
        live_weights={"AAA": 0.7, "CCC": 0.3},
        shadow_weights={"AAA": 0.7, "BBB": 0.3},
    )
    assert payload["status"] == "MINOR_DRIFT"
    assert payload["holdings"]["overlap_weight"] >= 0.60


def test_major_drift_due_to_returns(tmp_path: Path) -> None:
    payload = _base_case(
        tmp_path,
        live_returns=[0.030, 0.030],
        shadow_returns=[0.000, 0.000],
        live_weights={"AAA": 0.5, "BBB": 0.5},
        shadow_weights={"AAA": 0.5, "BBB": 0.5},
    )
    assert payload["status"] == "MAJOR_DRIFT"
    assert "RETURNS_DRIFT" in payload["reason_codes"]


def test_major_drift_due_to_holdings_overlap(tmp_path: Path) -> None:
    payload = _base_case(
        tmp_path,
        live_returns=[0.010, 0.010],
        shadow_returns=[0.010, 0.010],
        live_weights={"AAA": 0.4, "CCC": 0.6},
        shadow_weights={"AAA": 0.4, "BBB": 0.6},
    )
    assert payload["status"] == "MAJOR_DRIFT"
    assert payload["holdings"]["overlap_weight"] < 0.60
    assert "HOLDINGS_DRIFT" in payload["reason_codes"]


def test_not_comparable_due_to_missing_live_data(tmp_path: Path) -> None:
    shadow_dir = tmp_path / "shadow"
    _write_shadow_day(shadow_dir, "2026-04-24", polaris_return=0.01, spy_return=0.001, weights={"AAA": 1.0})
    positions = tmp_path / "positions.json"
    _write_positions(positions, {"AAA": 1.0})
    payload = build_reconciliation(
        trade_date="2026-04-24",
        shadow_dir=shadow_dir,
        live_nav_path=tmp_path / "missing.csv",
        broker_positions_path=positions,
    )
    assert payload["status"] == "NOT_COMPARABLE"
    assert "MISSING_LIVE_DATA" in payload["reason_codes"]


def test_not_comparable_due_to_missing_shadow_data(tmp_path: Path) -> None:
    live_nav = tmp_path / "live_nav.csv"
    _write_live_nav(live_nav, [("2026-04-23", 0.01), ("2026-04-24", 0.01)])
    positions = tmp_path / "positions.json"
    _write_positions(positions, {"AAA": 1.0})
    payload = build_reconciliation(
        trade_date="2026-04-24",
        shadow_dir=tmp_path / "missing_shadow",
        live_nav_path=live_nav,
        broker_positions_path=positions,
    )
    assert payload["status"] == "NOT_COMPARABLE"
    assert "MISSING_SHADOW_DATA" in payload["reason_codes"]


def test_markdown_and_artifact_writing(tmp_path: Path) -> None:
    payload = _base_case(
        tmp_path,
        live_returns=[0.010, 0.010],
        shadow_returns=[0.009, 0.010],
        live_weights={"AAA": 0.5, "BBB": 0.5},
        shadow_weights={"AAA": 0.5, "BBB": 0.5},
    )
    markdown = render_markdown(payload)
    assert "# Live vs Shadow Reconciliation" in markdown
    assert "## Executive Summary" in markdown
    assert "## Return Comparison" in markdown
    assert "## Holdings Reconciliation" in markdown
    json_path, md_path = write_artifacts(payload, tmp_path / "out")
    assert json_path.exists()
    assert md_path.exists()
    latest_json = tmp_path / "out" / "latest" / "live_vs_shadow_reconciliation.json"
    latest_md = tmp_path / "out" / "latest" / "live_vs_shadow_reconciliation.md"
    assert latest_json.exists()
    assert latest_md.exists()
    latest_payload = json.loads(latest_json.read_text())
    assert latest_payload["trade_date"] == "2026-04-24"
    assert latest_payload["classification"] == "RECONCILED"
    assert "- Generated at:" in latest_md.read_text()


def test_latest_artifacts_do_not_move_backwards(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    newer = _base_case(
        tmp_path / "newer",
        live_returns=[0.010, 0.010],
        shadow_returns=[0.009, 0.010],
        live_weights={"AAA": 0.5, "BBB": 0.5},
        shadow_weights={"AAA": 0.5, "BBB": 0.5},
    )
    newer["trade_date"] = "2026-04-25"
    write_artifacts(newer, out_dir)

    older = dict(newer)
    older["trade_date"] = "2026-04-24"
    write_artifacts(older, out_dir)

    latest_json = out_dir / "latest" / "live_vs_shadow_reconciliation.json"
    latest_payload = json.loads(latest_json.read_text())
    assert latest_payload["trade_date"] == "2026-04-25"


def test_strategy_mismatch_classification_and_target_comparison(tmp_path: Path) -> None:
    shadow_dir = tmp_path / "shadow"
    for date in ["2026-04-23", "2026-04-24"]:
        _write_shadow_day(
            shadow_dir,
            date,
            polaris_return=0.01,
            spy_return=0.001,
            weights={"AAA": 0.5, "BBB": 0.5},
        )
    live_nav = tmp_path / "live_nav.csv"
    _write_live_nav(live_nav, [("2026-04-23", 0.01), ("2026-04-24", 0.01)])
    positions = tmp_path / "positions.json"
    _write_positions(positions, {"AAA": 0.5, "BBB": 0.5})
    precompute_dir = tmp_path / "precompute"
    _write_live_targets(precompute_dir, "2026-04-24", {"CCC": 0.7, "AAA": 0.3})

    payload = build_reconciliation(
        trade_date="2026-04-24",
        shadow_dir=shadow_dir,
        precompute_dir=precompute_dir,
        live_nav_path=live_nav,
        broker_positions_path=positions,
    )

    assert payload["status"] == "NOT_ALIGNED"
    assert payload["strategy_alignment"]["status"] == "STRATEGY_MISMATCH"
    assert "DIFFERENT_STRATEGY_PATH" in payload["reason_codes"]
    assert payload["target_vs_target"]["live_only_symbols"] == ["CCC"]
    assert payload["target_vs_target"]["shadow_only_symbols"] == ["BBB"]
    markdown = render_markdown(payload)
    assert "## Strategy Identity" in markdown
    assert "## Target vs Target Comparison" in markdown
    assert "not intended to track Shadow Polaris" in markdown
