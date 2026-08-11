from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pandas as pd

from scripts.live_vs_shadow_reconciliation import build_reconciliation, render_markdown, write_artifacts


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
                    "caerus_orion": {
                        "strategy_name": "Caerus Orion",
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
    (day / "caerus_orion.json").write_text(
        json.dumps(
            {
                "strategy_name": "Caerus Orion",
                "strategy_slug": "caerus_orion",
                "source_variant": "h2_rank_decay_exit_h6_top5",
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
                    "execution_target_strategy_id": live_strategy_id,
                    "execution_target_source": f"{day}/signals.json",
                    "execution_target_type": "precompute_signals",
                    "shadow_baseline_strategy": "caerus_orion",
                    "shadow_baseline_source": f"shadow/{date}/caerus_orion.json",
                    "live_tracks_shadow_baseline": tracks_shadow,
                    "paper_tracks_shadow_baseline": tracks_shadow,
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
    assert payload["live_strategy_id"] == "caerus_orion"
    assert payload["shadow_baseline_strategy"] == "caerus_orion"
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


def test_first_orion_run_is_green_initializing_when_package_lineage_and_target_attainment_are_verified(
    tmp_path: Path,
) -> None:
    trade_date = "2026-04-24"
    shadow_dir = tmp_path / "outputs" / "shadow_candidates"
    _write_shadow_day(
        shadow_dir,
        trade_date,
        polaris_return=0.01,
        spy_return=0.001,
        weights={"AAA": 0.5, "BBB": 0.5},
    )
    source_path = shadow_dir / trade_date / "caerus_orion.json"
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    positions = tmp_path / "positions.json"
    _write_positions(positions, {"AAA": 0.5, "BBB": 0.5})
    run_root = tmp_path / "outputs" / "paper_lane" / "runs" / "orion-first"
    _write_json(
        run_root / "execution_payload.json",
        {
            "approved_execution_package": {
                "content_hash": "approved-hash",
                "approved_target_rows": [
                    {"symbol": "AAA", "target_weight": 0.5},
                    {"symbol": "BBB", "target_weight": 0.5},
                ],
                "approved_cash_weight": 0.0,
            },
            "decision_source_artifact": {
                "path": str(source_path),
                "sha256": source_hash,
                "strategy_id": "caerus_orion",
            },
        },
    )
    _write_json(
        run_root / "audit" / f"execution_target_attainment_{trade_date}.json",
        {"status": "OK_TARGET_ATTAINED"},
    )
    _write_json(
        tmp_path / "outputs" / "workflow" / trade_date / "execution.json",
        {"trade_date": trade_date, "run_root": str(run_root), "status": "success"},
    )

    payload = build_reconciliation(
        trade_date=trade_date,
        shadow_dir=shadow_dir,
        precompute_dir=tmp_path / "outputs" / "precompute",
        live_nav_path=tmp_path / "missing.csv",
        broker_positions_path=positions,
    )

    assert payload["status"] == "ALIGNED_INITIALIZING"
    assert payload["immutable_lineage"]["verified"] is True
    assert "IMMUTABLE_LINEAGE_VERIFIED" in payload["reason_codes"]
    assert "TARGET_ATTAINED" in payload["reason_codes"]


def test_prior_session_decision_source_drives_shadow_reconciliation(
    tmp_path: Path,
) -> None:
    trade_date = "2026-08-10"
    source_date = "2026-08-07"
    shadow_dir = tmp_path / "outputs" / "shadow_candidates"
    _write_shadow_day(
        shadow_dir,
        source_date,
        polaris_return=0.01,
        spy_return=0.001,
        weights={"AAA": 0.5, "BBB": 0.5},
    )
    source_path = shadow_dir / source_date / "caerus_orion.json"
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    positions = tmp_path / "positions.json"
    _write_positions(positions, {"AAA": 0.5, "BBB": 0.5})
    run_root = tmp_path / "outputs" / "paper_lane" / "runs" / "orion-monday"
    _write_json(
        run_root / "execution_payload.json",
        {
            "approved_execution_package": {
                "content_hash": "approved-hash",
                "approved_target_rows": [
                    {"symbol": "AAA", "target_weight": 0.5},
                    {"symbol": "BBB", "target_weight": 0.5},
                ],
                "approved_cash_weight": 0.0,
            },
            "decision_source_artifact": {
                "path": str(source_path.relative_to(tmp_path)),
                "sha256": source_hash,
                "strategy_id": "caerus_orion",
                "trade_date": source_date,
                "source_trade_date": source_date,
                "decision_trade_date": trade_date,
                "effective_trade_date": trade_date,
                "source_trading_session_lag": 1,
            },
        },
    )
    _write_json(
        run_root / "audit" / f"execution_target_attainment_{trade_date}.json",
        {"status": "OK_TARGET_ATTAINED"},
    )
    _write_json(
        tmp_path / "outputs" / "workflow" / trade_date / "execution.json",
        {"trade_date": trade_date, "run_root": str(run_root), "status": "success"},
    )

    payload = build_reconciliation(
        trade_date=trade_date,
        shadow_dir=shadow_dir,
        precompute_dir=tmp_path / "outputs" / "precompute",
        live_nav_path=tmp_path / "missing.csv",
        broker_positions_path=positions,
    )

    assert payload["status"] == "ALIGNED_INITIALIZING"
    assert payload["sources"]["shadow_baseline_path"] == str(source_path)
    assert payload["immutable_lineage"]["verified"] is True
    assert payload["immutable_lineage"]["decision_source_expected_sha256"] == source_hash
    assert payload["immutable_lineage"]["shadow_source_actual_sha256"] == source_hash
    assert payload["target_vs_target"]["overlap_weight"] == 1.0


def test_first_clean_policy_run_starts_new_epoch_and_preserves_legacy_history(
    tmp_path: Path,
) -> None:
    old_date = "2026-04-23"
    trade_date = "2026-04-24"
    shadow_dir = tmp_path / "outputs" / "shadow_candidates"
    _write_shadow_day(
        shadow_dir,
        old_date,
        polaris_return=-0.40,
        spy_return=0.001,
        weights={"AAA": 1.0},
    )
    _write_shadow_day(
        shadow_dir,
        trade_date,
        polaris_return=0.01,
        spy_return=0.001,
        weights={"AAA": 1.0},
    )
    source_path = shadow_dir / trade_date / "caerus_orion.json"
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    live_nav = tmp_path / "outputs" / "perf" / "live_overlay_nav_series.csv"
    _write_live_nav(live_nav, [(old_date, 0.50), (trade_date, 0.01)])
    positions = tmp_path / "positions.json"
    _write_positions(positions, {"AAA": 1.0})
    policy = {
        "schema_version": "caerus.target_attainment_policy.v1",
        "account_scope": "PAPER",
        "share_mode": "WHOLE_SHARES",
        "target_cash_weight": 0.05,
        "minimum_cash_weight": 0.025,
        "fixed_drift_tolerance": 0.02,
        "nearest_feasible_required": True,
        "comparison_epoch_policy": "FIRST_CLEAN_POST_FIX_PAPER_RUN",
        "strict_green_propagation": True,
        "owner_approved_at": "2026-08-11",
    }
    run_root = tmp_path / "outputs" / "paper_lane" / "runs" / "clean-epoch"
    _write_json(
        run_root / "execution_payload.json",
        {
            "approved_execution_package": {
                "content_hash": "approved-clean-hash",
                "approved_target_rows": [
                    {"symbol": "AAA", "target_weight": 0.95}
                ],
                "approved_cash_weight": 0.05,
                "constraints": {"target_attainment_policy": policy},
            },
            "target_attainment_policy": policy,
            "decision_source_artifact": {
                "path": str(source_path),
                "sha256": source_hash,
                "strategy_id": "caerus_orion",
            },
        },
    )
    _write_json(
        run_root / "audit" / f"execution_target_attainment_{trade_date}.json",
        {"status": "OK_NEAREST_FEASIBLE"},
    )
    _write_json(run_root / "live_pilot_reconciliation.json", {"status": "CLEAN"})
    _write_json(run_root / "audit" / "execution_integrity.json", {"status": "OK"})
    _write_json(run_root / "equality_gate.json", {"decision": "WOULD_PROCEED"})
    _write_json(
        tmp_path / "outputs" / "workflow" / trade_date / "execution.json",
        {"trade_date": trade_date, "run_root": str(run_root), "status": "success"},
    )

    payload = build_reconciliation(
        trade_date=trade_date,
        shadow_dir=shadow_dir,
        precompute_dir=tmp_path / "outputs" / "precompute",
        live_nav_path=live_nav,
        broker_positions_path=positions,
    )

    epoch_path = (
        tmp_path
        / "outputs"
        / "reconciliation"
        / "live_vs_shadow"
        / "comparison_epoch.json"
    )
    assert epoch_path.exists()
    assert payload["status"] == "ALIGNED_INITIALIZING"
    assert payload["comparison_start_date"] == trade_date
    assert payload["number_of_valid_days"] == 1
    assert payload["returns"]["live_minus_shadow_polaris"] == 0.0
    assert payload["comparison_epoch"]["legacy_history"]["preserved"] is True


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
    assert "different strategy paths" in markdown
