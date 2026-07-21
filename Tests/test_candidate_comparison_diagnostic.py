from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.candidate_comparison_diagnostic import SCHEMA_VERSION, build_candidate_comparison, main


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_universe(root: Path) -> None:
    path = root / "data" / "universe.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ticker", "sector"])
        writer.writeheader()
        writer.writerows(
            [
                {"ticker": "MU", "sector": "Information Technology"},
                {"ticker": "INTC", "sector": "Information Technology"},
                {"ticker": "META", "sector": "Communication Services"},
            ]
        )


def _build_fixture(root: Path) -> None:
    _write_universe(root)
    _write_json(
        root / "data" / "security_master" / "2026-07-20" / "ticker_universe.json",
        {
            "asof_date": "2026-07-20",
            "symbols": [
                {"symbol": ticker, "status": "active", "tradable": True}
                for ticker in ("CRDO", "NBIS", "MU", "INTC", "META")
            ],
        },
    )
    # Newest attempt is stale and must not displace the prior valid score date.
    _write_json(
        root / "outputs" / "shadow_candidates" / "2026-07-20" / "comparison.json",
        {"trade_date": "2026-07-20", "status": "NO_DATA", "reason_code": "PRICE_CACHE_STALE"},
    )
    for strategy in ("caerus_polaris", "caerus_orion", "caerus_lyra"):
        _write_json(
            root / "outputs" / "shadow_candidates" / "2026-07-17" / f"{strategy}.json",
            {
                "strategy_slug": strategy,
                "target_weights": {"MU": 0.2, "INTC": 0.2},
                "rank_table": [
                    {"ticker": "MU", "momentum_score": 4.481668, "momentum_rank": 2, "is_selected": True},
                    {"ticker": "INTC", "momentum_score": 2.476473, "momentum_rank": 4, "is_selected": True},
                ],
            },
        )
    _write_json(
        root / "outputs" / "precompute" / "2026-07-20" / "signals.json",
        {
            "signals": [
                {"ticker": "FTNT", "target_weight": 0.3},
                {"ticker": "META", "target_weight": 0.2},
                {"ticker": "CASH", "target_weight": 0.05},
            ]
        },
    )
    _write_json(
        root / "outputs" / "precompute" / "2026-07-20" / "planned_execution_payload.json",
        {"trades": [{"ticker": "META", "side": "BUY"}]},
    )
    _write_json(
        root
        / "outputs"
        / "paper_lane"
        / "runs"
        / "2026-07-20T100000_paper"
        / "live_pilot_broker_snapshot_post.json",
        {"captured_at": "2026-07-20T14:03:28Z", "positions": [{"symbol": "INTC", "qty": "2"}]},
    )


def _by_ticker(payload: dict, ticker: str) -> dict:
    return next(row for row in payload["candidates"] if row["ticker"] == ticker)


def test_comparison_distinguishes_security_master_from_model_universe(tmp_path: Path) -> None:
    _build_fixture(tmp_path)
    payload = build_candidate_comparison(
        tickers=["CRDO", "NBIS", "MU", "INTC", "META"],
        repo_root=tmp_path,
        as_of="2026-07-20",
    )

    assert payload["schema_version"] == SCHEMA_VERSION
    assert _by_ticker(payload, "CRDO")["security_master_tradable"] is True
    assert _by_ticker(payload, "CRDO")["universe_eligible"] is False
    assert _by_ticker(payload, "CRDO")["primary_blocker"] == "NOT_IN_ACTIVE_MODEL_UNIVERSE"
    assert _by_ticker(payload, "MU")["universe_eligible"] is True


def test_uses_latest_valid_shadow_after_stale_attempt(tmp_path: Path) -> None:
    _build_fixture(tmp_path)
    payload = build_candidate_comparison(tickers=["MU", "INTC"], repo_root=tmp_path)

    assert payload["artifact_dates"]["latest_shadow_attempt"]["date"] == "2026-07-20"
    assert payload["artifact_dates"]["latest_shadow_attempt"]["reason_code"] == "PRICE_CACHE_STALE"
    assert payload["artifact_dates"]["latest_valid_shadow"] == "2026-07-17"
    assert _by_ticker(payload, "MU")["latest_score"] == 4.481668
    assert _by_ticker(payload, "MU")["sleeve_rank"] == 2
    assert set(_by_ticker(payload, "MU")["best_sleeves"]) == {
        "caerus_polaris",
        "caerus_orion",
        "caerus_lyra",
    }


def test_reports_final_target_buy_and_current_holding_without_inference(tmp_path: Path) -> None:
    _build_fixture(tmp_path)
    payload = build_candidate_comparison(tickers=["META", "INTC"], repo_root=tmp_path)

    meta = _by_ticker(payload, "META")
    assert meta["latest_score"] is None
    assert meta["sleeve_rank"] is None
    assert meta["final_rank"] == 2
    assert meta["production_target"] is True
    assert meta["planned_next_buy"] is True
    assert meta["primary_blocker"] == "NONE_PLANNED_BUY"

    intel = _by_ticker(payload, "INTC")
    assert intel["current_holding"] is True
    assert intel["production_target"] is False
    assert intel["primary_blocker"] == "SHADOW_ONLY_NOT_IN_PRODUCTION_TARGET"


def test_builder_does_not_mutate_artifacts(tmp_path: Path) -> None:
    _build_fixture(tmp_path)
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    build_candidate_comparison(tickers=["MU"], repo_root=tmp_path)

    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_cli_prints_json_and_creates_no_output(capsys, tmp_path: Path) -> None:
    _build_fixture(tmp_path)
    assert main(["MU", "INTC", "--repo-root", str(tmp_path), "--as-of", "2026-07-20"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "READ_ONLY_EXISTING_ARTIFACTS"
    assert payload["tickers"] == ["MU", "INTC"]
    assert not (tmp_path / "outputs" / "diagnostics").exists()


def test_missing_artifacts_fail_to_partial_without_inventing_scores(tmp_path: Path) -> None:
    _write_universe(tmp_path)

    payload = build_candidate_comparison(tickers=["META"], repo_root=tmp_path)
    meta = _by_ticker(payload, "META")

    assert payload["status"] == "PARTIAL"
    assert set(payload["missing_sources"]) >= {
        "security_master",
        "valid_shadow",
        "precompute",
        "broker_snapshot",
    }
    assert meta["latest_score"] is None
    assert meta["sleeve_rank"] is None
    assert meta["primary_blocker"] == "NO_VALID_SHADOW_ARTIFACT"
