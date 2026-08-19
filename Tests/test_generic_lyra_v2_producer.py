from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from core.generic_lyra_v2_producer import (
    GenericLyraV2ProducerError,
    build_generic_lyra_v2_decision_batch,
    generic_lyra_v2_readiness_path,
    validate_governed_lyra_v2_decision,
)
from core.lyra_governed_evidence import (
    CAPACITY_FORMULA,
    LIQUIDITY_FORMULA,
    RISK_FORMULA,
    LYRA_RISK_POLICY_SCHEMA,
    LYRA_RISK_POLICY_PROPOSAL_SCHEMA,
    LYRA_RISK_POLICY_OWNER_DECISION_SCHEMA,
    build_lyra_market_data_snapshot,
)
from core.governed_universe_freeze import read_governed_universe_symbols
from core.lyra_target_selection import build_lyra_target_selection_evidence
from core.portfolio_operating_model import content_hash
from core.sleeve_decision import seal_sleeve_decision
from scripts.capture_generic_lyra_v2 import (
    capture_from_explicit_paths,
    file_sha256,
    recompute_capture_from_explicit_paths,
)
from core.governed_xnys_calendar import previous_xnys_session


ROOT = Path(__file__).resolve().parents[1]
FREEZE = json.loads(
    (ROOT / "docs/evidence/lyra_governed_universe_freeze_2026-08-19.json").read_text()
)
SYMBOLS = ["DELL", "INTC", "MU", "STX", "WDC"]
EVALUATION_FILE_HASH = "a" * 64
LYRA_SOURCE_HASH = "b" * 64
LEGACY_DECISION_FILE_HASH = "c" * 64
PRIOR_SOURCE_HASH = "d" * 64
PRICE_SOURCE_HASH = "e" * 64


def _shadow(effective_date: str, *, symbols=SYMBOLS) -> dict:
    weights = {symbol: 0.2 for symbol in symbols}
    return {
        "trade_date": effective_date,
        "effective_trade_date": effective_date,
        "strategy_slug": "caerus_lyra",
        "source_variant": "h1_weekly_h6_top5",
        "target_weights": weights,
        "expected_turnover": 999.0,
        "rank_table": [
            {
                "ticker": symbol, "momentum_score": float(10 - index),
                "momentum_rank": float(index), "is_selected": True,
            }
            for index, symbol in enumerate(symbols, start=1)
        ],
        "holdings": [
            {"ticker": symbol, "target_weight": 0.2} for symbol in symbols
        ],
    }


def _sources() -> dict:
    session = {
        "schema_version": "caerus.session_manifest.v1",
        "session_id": "session:2026-08-25:source",
        "trade_date": "2026-08-25", "run_id": "run:source",
        "as_of": "2026-08-25T11:05:00+00:00",
        "created_at": "2026-08-25T11:05:00+00:00",
        "inputs": [
            {
                "name": "sleeve_evaluations", "path": "evaluations.json",
                "sha256": EVALUATION_FILE_HASH, "required": True,
                "exists": True, "as_of": "2026-08-24",
                "freshness_status": "FRESH",
            },
            {
                "name": "sleeve_source:caerus_lyra:0", "path": "lyra.json",
                "sha256": LYRA_SOURCE_HASH, "required": False,
                "exists": True, "as_of": "2026-08-24",
                "freshness_status": "FRESH",
            },
        ],
    }
    session["content_hash"] = content_hash(session)
    envelope = {
        "schema_version": "caerus_sleeve_evaluation_v1",
        "trade_date": "2026-08-25", "run_id": "run:source",
        "sleeve_id": "caerus_lyra",
        "lifecycle": {"status": "shadow", "frozen": False},
        "evaluation": {
            "status": "OK", "runner": "shadow_snapshot",
            "evaluated_at": "2026-08-25T11:04:00+00:00",
        },
        "opportunity": {
            "available": True, "decision_eligible": True,
            "effective_trade_date": "2026-08-24",
        },
        "provenance": {
            "source_artifacts": [{"sha256": LYRA_SOURCE_HASH}],
        },
        "reason_codes": ["EVALUATION_ONLY", "NON_DECISION_GRADE_UNIVERSE"],
    }
    evaluation = {
        "schema_version": "caerus_all_sleeve_evaluation_v1",
        "trade_date": "2026-08-25", "run_id": "run:source",
        "generated_at": "2026-08-25T11:04:00+00:00",
        "all_non_frozen_evaluated": True,
        "expected_non_frozen_sleeve_ids": ["caerus_lyra"],
        "envelopes": [envelope],
    }
    targets = [
        {"symbol": symbol, "target_weight": 0.2, "source_target_weight": 0.2}
        for symbol in SYMBOLS
    ]
    legacy = {
        "schema_version": "caerus.sleeve_decision.v1",
        "trade_date": "2026-08-25", "session_id": session["session_id"],
        "session_hash": session["content_hash"], "sleeve_id": "caerus_lyra",
        "outcome": "RECOMMENDATION", "target_rows": targets,
        "reason_codes": ["EVALUATION_ONLY", "NON_DECISION_GRADE_UNIVERSE"],
    }
    legacy["content_hash"] = content_hash(legacy)
    legacy_batch = {
        "schema_version": "caerus.sleeve_decision_batch.v1",
        "trade_date": "2026-08-25", "session_id": session["session_id"],
        "session_hash": session["content_hash"], "decisions": [legacy],
    }
    legacy_batch["content_hash"] = content_hash(legacy_batch["decisions"])
    observation_dates = ["2026-08-24"]
    while len(observation_dates) < 253:
        observation_dates.append(previous_xnys_session(observation_dates[-1]))
    observation_dates.reverse()
    universe_symbols = read_governed_universe_symbols(
        freeze=FREEZE, universe_path=ROOT / "data/universe.csv",
        session_as_of="2026-08-25T11:05:00+00:00",
    )
    price_rows = []
    for day_index, date in enumerate(observation_dates):
        for symbol_index, symbol in enumerate(universe_symbols):
            if symbol in SYMBOLS:
                slope = 0.0030 - SYMBOLS.index(symbol) * 0.0001
            else:
                slope = 0.0002 - symbol_index * 0.0000001
            close = 100.0 * ((1.0 + slope) ** day_index)
            price_rows.append({
                "date": date, "ticker": symbol, "close": close,
                "volume": 1_000_000 + symbol_index * 10_000,
            })
    selection = build_lyra_target_selection_evidence(
        execution_session="2026-08-25", signal_as_of="2026-08-24",
        captured_at="2026-08-25T11:06:00+00:00",
        source_path="outputs/research/flow_detection_v1/price_panel.parquet",
        source_sha256=PRICE_SOURCE_HASH,
        universe_freeze_hash=FREEZE["content_hash"],
        universe_source_hash=FREEZE["source_sha256"],
        frozen_universe_symbols=universe_symbols, price_rows=price_rows,
    )
    market = build_lyra_market_data_snapshot(
        trade_date="2026-08-25", data_as_of="2026-08-24",
        captured_at="2026-08-25T11:06:00+00:00",
        source_path="outputs/research/flow_detection_v1/price_panel.parquet",
        source_sha256=PRICE_SOURCE_HASH, required_symbols=SYMBOLS,
        price_rows=price_rows,
    )
    risk_policy_proposal = {
        "schema_version": LYRA_RISK_POLICY_PROPOSAL_SCHEMA,
        "proposal_id": "lyra-risk-policy-proposal:test-v1",
        "proposed_at": "2026-08-19T11:00:00+00:00",
        "proposed_by": "CAERUS_OPERATING_MODEL_MIGRATION",
        "policy_terms": {
            "sleeve_id": "caerus_lyra", "metric": "annualized_volatility",
            "formula_id": RISK_FORMULA, "lookback_sessions": 20,
            "minimum_price_observations": 21, "annualization_factor": 252,
            "liquidity_formula_id": LIQUIDITY_FORMULA,
            "liquidity_lookback_sessions": 20,
            "minimum_mean_dollar_volume_usd": 20_000_000.0,
            "maximum_order_participation_rate": 0.01,
            "maximum_liquidation_participation_rate": 0.05,
            "capacity_formula_id": CAPACITY_FORMULA,
            "minimum_capacity_multiple": 20.0,
            "capital_reference_usd": 460.0,
            "turnover_formula_id": "FULL_L1_TARGET_WEIGHT_CHANGE_V1",
            "calendar_policy_id": "XNYS_US_EQUITIES_HOLIDAY_RULES_V1",
            "effective_from": "2026-08-19", "execution_authority": False,
            "activation_authority": False,
        },
        "execution_authority": False, "activation_authority": False,
    }
    risk_policy_proposal["content_hash"] = content_hash(risk_policy_proposal)
    risk_policy_owner_decision = {
        "schema_version": LYRA_RISK_POLICY_OWNER_DECISION_SCHEMA,
        "owner_decision_id": "owner-decision:lyra-risk-policy:test-v1",
        "proposal_id": risk_policy_proposal["proposal_id"],
        "proposal_hash": risk_policy_proposal["content_hash"],
        "decision": "APPROVE", "owner": "Brett Olson",
        "decided_at": "2026-08-19T12:00:00+00:00",
        "expires_at": "2026-08-26T20:00:00+00:00",
        "execution_authority": False, "activation_authority": False,
    }
    risk_policy_owner_decision["content_hash"] = content_hash(
        risk_policy_owner_decision
    )
    risk_policy = {
        "schema_version": LYRA_RISK_POLICY_SCHEMA,
        "policy_id": "lyra-risk-policy:test-owner-approved-v1",
        "status": "APPROVED", "sleeve_id": "caerus_lyra",
        "metric": "annualized_volatility", "formula_id": RISK_FORMULA,
        "lookback_sessions": 20, "minimum_price_observations": 21,
        "annualization_factor": 252,
        "liquidity_formula_id": LIQUIDITY_FORMULA,
        "liquidity_lookback_sessions": 20,
        "minimum_mean_dollar_volume_usd": 20_000_000.0,
        "maximum_order_participation_rate": 0.01,
        "maximum_liquidation_participation_rate": 0.05,
        "capacity_formula_id": CAPACITY_FORMULA,
        "minimum_capacity_multiple": 20.0,
        "capital_reference_usd": 460.0,
        "turnover_formula_id": "FULL_L1_TARGET_WEIGHT_CHANGE_V1",
        "calendar_policy_id": "XNYS_US_EQUITIES_HOLIDAY_RULES_V1",
        "approved_by": "OWNER",
        "approved_at": "2026-08-19T12:00:00+00:00",
        "effective_from": "2026-08-19",
        "owner_decision_hash": risk_policy_owner_decision["content_hash"],
        "execution_authority": False,
    }
    risk_policy["content_hash"] = content_hash(risk_policy)
    return {
        "source_session_manifest": session,
        "evaluation_batch": evaluation,
        "evaluation_file_hash": EVALUATION_FILE_HASH,
        "legacy_decision_batch": legacy_batch,
        "legacy_decision_file_hash": LEGACY_DECISION_FILE_HASH,
        "lyra_source": _shadow("2026-08-24"),
        "lyra_source_hash": LYRA_SOURCE_HASH,
        "prior_lyra_source": _shadow("2026-08-17"),
        "prior_lyra_source_hash": PRIOR_SOURCE_HASH,
        "universe_freeze": copy.deepcopy(FREEZE),
        "universe_path": ROOT / "data/universe.csv",
        "market_data_snapshot": market,
        "target_selection_evidence": selection,
        "forecast_risk_policy": risk_policy,
        "forecast_risk_policy_proposal": risk_policy_proposal,
        "forecast_risk_policy_owner_decision": risk_policy_owner_decision,
        "session_as_of": "2026-08-25T11:05:00+00:00",
        "generated_at": "2026-08-25T11:06:00+00:00",
    }


def _path_sources(tmp_path: Path) -> tuple[dict, list[dict]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    arguments = _sources()
    paths = {
        "evaluation_batch_path": tmp_path / "sleeve_evaluations.json",
        "lyra_source_path": tmp_path / "caerus_lyra.json",
        "prior_lyra_source_path": tmp_path / "caerus_lyra_prior.json",
        "universe_freeze_path": tmp_path / "universe_freeze.json",
        "forecast_risk_policy_path": tmp_path / "risk_policy.json",
        "forecast_risk_policy_proposal_path": tmp_path / "risk_policy_proposal.json",
        "forecast_risk_policy_owner_decision_path": (
            tmp_path / "risk_policy_owner_decision.json"
        ),
        "source_session_manifest_path": tmp_path / "session_manifest.json",
        "legacy_decision_batch_path": tmp_path / "sleeve_decisions.json",
        "price_panel_path": tmp_path / "price_panel.parquet",
    }
    paths["lyra_source_path"].write_text(json.dumps(arguments["lyra_source"]))
    paths["prior_lyra_source_path"].write_text(json.dumps(arguments["prior_lyra_source"]))
    lyra_hash = file_sha256(paths["lyra_source_path"])
    evaluation = copy.deepcopy(arguments["evaluation_batch"])
    evaluation["envelopes"][0]["provenance"]["source_artifacts"][0]["sha256"] = lyra_hash
    paths["evaluation_batch_path"].write_text(json.dumps(evaluation))
    session = copy.deepcopy(arguments["source_session_manifest"])
    session["inputs"][0]["sha256"] = file_sha256(paths["evaluation_batch_path"])
    session["inputs"][1]["sha256"] = lyra_hash
    session["content_hash"] = content_hash(
        {key: value for key, value in session.items() if key != "content_hash"}
    )
    paths["source_session_manifest_path"].write_text(json.dumps(session))
    legacy = copy.deepcopy(arguments["legacy_decision_batch"])
    legacy["session_id"] = session["session_id"]
    legacy["session_hash"] = session["content_hash"]
    legacy["decisions"][0]["session_id"] = session["session_id"]
    legacy["decisions"][0]["session_hash"] = session["content_hash"]
    legacy["decisions"][0]["content_hash"] = content_hash({
        key: value for key, value in legacy["decisions"][0].items()
        if key != "content_hash"
    })
    legacy["content_hash"] = content_hash(legacy["decisions"])
    paths["legacy_decision_batch_path"].write_text(json.dumps(legacy))
    paths["universe_freeze_path"].write_text(json.dumps(FREEZE))
    paths["forecast_risk_policy_path"].write_text(
        json.dumps(arguments["forecast_risk_policy"])
    )
    paths["forecast_risk_policy_proposal_path"].write_text(
        json.dumps(arguments["forecast_risk_policy_proposal"])
    )
    paths["forecast_risk_policy_owner_decision_path"].write_text(
        json.dumps(arguments["forecast_risk_policy_owner_decision"])
    )
    paths["price_panel_path"].write_bytes(b"explicit-price-panel-fixture")
    universe_path = tmp_path / "universe.csv"
    universe_path.write_bytes((ROOT / "data/universe.csv").read_bytes())
    paths.update({
        "execution_session": "2026-08-25", "signal_as_of": "2026-08-24",
        "session_as_of": "2026-08-25T11:05:00+00:00",
        "captured_at": "2026-08-25T11:06:00+00:00",
        "universe_path": universe_path,
        "output_root": tmp_path / "output",
    })
    rows = [
        {"date": observation["date"], "ticker": history["symbol"],
         "close": observation["close"], "volume": 2_000_000.0}
        for history in arguments["target_selection_evidence"]["close_histories"]
        for observation in history["observations"]
    ]
    return paths, rows


def test_current_governed_capture_derives_all_decision_grade_evidence() -> None:
    result = build_generic_lyra_v2_decision_batch(**_sources())
    decision = result["decision"]
    assert result["status"] == "READY_NO_SUBMIT"
    assert result["execution_session"] == "2026-08-25"
    assert result["signal_as_of"] == "2026-08-24"
    assert result["effective_target_date"] == "2026-08-24"
    assert result["readiness"]["status"] == "READY_FOR_EXACT_PLAN_NO_SUBMIT"
    assert decision["decision_grade"] == "READY"
    assert decision["forecast_risk"]["formula_id"] == RISK_FORMULA
    assert decision["forecast_risk"]["lookback_sessions"] == 20
    assert decision["capacity"]["formula_id"] == CAPACITY_FORMULA
    assert decision["capacity"]["maximum_deployable_capital_usd"] >= 20 * 460
    assert decision["capacity"]["liquidity_evidence"]["formula_id"] == LIQUIDITY_FORMULA
    assert decision["liquidity_status"] == "PASS"
    assert decision["expected_turnover"] == 0.0
    assert not ({"EVALUATION_ONLY", "NON_DECISION_GRADE_UNIVERSE"} & set(decision["reason_codes"]))
    assert result["submission_allowed"] is False
    assert generic_lyra_v2_readiness_path(
        output_root="outputs/generic_lyra_v2", readiness=result["readiness"],
    ).name == f"readiness-{result['readiness']['content_hash']}.json"


def test_turnover_matches_canonical_full_l1_semantics() -> None:
    arguments = _sources()
    arguments["prior_lyra_source"] = _shadow(
        "2026-08-17", symbols=["AAPL", "GOOG", "META", "MSFT", "NVDA"]
    )
    decision = build_generic_lyra_v2_decision_batch(**arguments)["decision"]
    assert decision["expected_turnover"] == 2.0
    assert "TURNOVER_FULL_L1_FORMULA_BOUND" in decision["reason_codes"]


@pytest.mark.parametrize("field", ["annualized_volatility", "formula_id", "status"])
def test_resealed_forecast_risk_bypass_is_rejected(field: str) -> None:
    decision = build_generic_lyra_v2_decision_batch(**_sources())["decision"]
    changed = copy.deepcopy(decision)
    risk = changed["forecast_risk"]
    risk[field] = {"annualized_volatility": 0.0001, "formula_id": "PLACEHOLDER", "status": "FAIL"}[field]
    risk["content_hash"] = content_hash({k: v for k, v in risk.items() if k != "content_hash"})
    changed = seal_sleeve_decision(changed)
    with pytest.raises(Exception):
        validate_governed_lyra_v2_decision(changed)


def test_resealed_liquidity_and_capacity_bypass_is_rejected() -> None:
    decision = build_generic_lyra_v2_decision_batch(**_sources())["decision"]
    changed = copy.deepcopy(decision)
    liquidity = changed["capacity"]["liquidity_evidence"]
    liquidity["symbol_results"][0]["mean_dollar_volume_20"] = 9_999_999_999.0
    liquidity["content_hash"] = content_hash(
        {key: value for key, value in liquidity.items() if key != "content_hash"}
    )
    changed["capacity"]["content_hash"] = content_hash(
        {key: value for key, value in changed["capacity"].items() if key != "content_hash"}
    )
    changed = seal_sleeve_decision(changed)
    with pytest.raises(Exception, match="not recomputed"):
        validate_governed_lyra_v2_decision(changed)


def test_legacy_relabel_and_stale_market_data_fail_closed() -> None:
    arguments = _sources()
    arguments["evaluation_batch"]["envelopes"][0]["reason_codes"] = []
    with pytest.raises(GenericLyraV2ProducerError, match="blockers are not explicit"):
        build_generic_lyra_v2_decision_batch(**arguments)
    arguments = _sources()
    arguments["market_data_snapshot"]["data_as_of"] = "2026-08-17"
    arguments["market_data_snapshot"]["content_hash"] = content_hash(
        {key: value for key, value in arguments["market_data_snapshot"].items() if key != "content_hash"}
    )
    with pytest.raises(Exception):
        build_generic_lyra_v2_decision_batch(**arguments)


def test_source_session_and_target_tampering_fail_closed() -> None:
    arguments = _sources()
    arguments["source_session_manifest"]["inputs"][0]["sha256"] = "f" * 64
    arguments["source_session_manifest"]["content_hash"] = content_hash(
        {key: value for key, value in arguments["source_session_manifest"].items() if key != "content_hash"}
    )
    with pytest.raises(GenericLyraV2ProducerError, match="session/evaluation"):
        build_generic_lyra_v2_decision_batch(**arguments)
    arguments = _sources()
    arguments["legacy_decision_batch"]["decisions"][0]["target_rows"][0]["target_weight"] = 0.1
    with pytest.raises(GenericLyraV2ProducerError, match="lineage differs|hash differs"):
        build_generic_lyra_v2_decision_batch(**arguments)


def test_explicit_path_capture_is_no_write_by_default_and_idempotent_when_enabled(
    tmp_path: Path,
) -> None:
    paths, rows = _path_sources(tmp_path)

    def loader(path, *, symbols, data_as_of):
        assert Path(path) == paths["price_panel_path"]
        assert data_as_of == "2026-08-24"
        return [row for row in rows if row["ticker"] in set(symbols)]

    dry_run = capture_from_explicit_paths(**paths, price_row_loader=loader)
    assert dry_run["capture_result"]["status"] == "READY_NO_SUBMIT"
    assert dry_run["persisted_paths"] == []
    assert not paths["output_root"].exists()
    first = capture_from_explicit_paths(
        **paths, price_row_loader=loader, write_advisory_artifacts=True,
    )
    second = capture_from_explicit_paths(
        **paths, price_row_loader=loader, write_advisory_artifacts=True,
    )
    assert first["persisted_paths"] == second["persisted_paths"]
    assert len(first["persisted_paths"]) == 12
    assert all(Path(path).is_file() for path in first["persisted_paths"])
    assert first["broker_write_performed"] is False


def test_runtime_raw_source_recompute_rejects_changed_bytes(tmp_path: Path) -> None:
    paths, rows = _path_sources(tmp_path)

    def loader(path, *, symbols, data_as_of):
        return [row for row in rows if row["ticker"] in set(symbols)]

    expected = capture_from_explicit_paths(
        **paths, price_row_loader=loader
    )["capture_result"]
    recompute_args = {
        key: value for key, value in paths.items()
        if key not in {"output_root"}
    }
    proof = recompute_capture_from_explicit_paths(
        expected_capture=expected, **recompute_args, price_row_loader=loader,
    )
    assert proof["status"] == "PASS_NO_WRITE"
    assert proof["expected_capture_hash"] == expected["content_hash"]
    assert proof["broker_write_performed"] is False
    assert [row["name"] for row in proof["source_files"]] == sorted(
        {
            "source_session_manifest", "evaluation_batch",
            "legacy_decision_batch", "lyra_source", "prior_lyra_source",
            "universe_freeze", "universe_bytes", "forecast_risk_policy",
            "forecast_risk_policy_proposal",
            "forecast_risk_policy_owner_decision", "price_panel",
        }
    )
    assert all(Path(row["path"]).is_absolute() for row in proof["source_files"])

    changed = json.loads(paths["lyra_source_path"].read_text())
    changed["rank_table"][0]["momentum_score"] += 1.0
    paths["lyra_source_path"].write_text(json.dumps(changed))
    with pytest.raises(Exception):
        recompute_capture_from_explicit_paths(
            expected_capture=expected, **recompute_args, price_row_loader=loader,
        )


def test_runtime_raw_source_recompute_rejects_panel_and_universe_bytes(
    tmp_path: Path,
) -> None:
    paths, rows = _path_sources(tmp_path)

    def loader(path, *, symbols, data_as_of):
        return [row for row in rows if row["ticker"] in set(symbols)]

    expected = capture_from_explicit_paths(
        **paths, price_row_loader=loader
    )["capture_result"]
    recompute_args = {
        key: value for key, value in paths.items() if key != "output_root"
    }
    paths["price_panel_path"].write_bytes(b"changed-price-panel-bytes")
    with pytest.raises(Exception):
        recompute_capture_from_explicit_paths(
            expected_capture=expected, **recompute_args, price_row_loader=loader,
        )

    paths, rows = _path_sources(tmp_path / "universe-case")
    expected = capture_from_explicit_paths(
        **paths, price_row_loader=lambda path, *, symbols, data_as_of: [
            row for row in rows if row["ticker"] in set(symbols)
        ],
    )["capture_result"]
    recompute_args = {
        key: value for key, value in paths.items() if key != "output_root"
    }
    paths["universe_path"].write_text(
        paths["universe_path"].read_text() + "\nZZZZ\n", encoding="utf-8"
    )
    with pytest.raises(Exception):
        recompute_capture_from_explicit_paths(
            expected_capture=expected, **recompute_args,
            price_row_loader=lambda path, *, symbols, data_as_of: [
                row for row in rows if row["ticker"] in set(symbols)
            ],
        )


def test_freeze_must_be_effective_by_signal_date() -> None:
    arguments = _sources()
    freeze = arguments["universe_freeze"]
    freeze["effective_from"] = "2026-08-25T00:00:00-04:00"
    freeze["no_retroactive_use_before"] = "2026-08-25"
    freeze["content_hash"] = content_hash({
        key: value for key, value in freeze.items() if key != "content_hash"
    })
    with pytest.raises(GenericLyraV2ProducerError, match="signal predates"):
        build_generic_lyra_v2_decision_batch(**arguments)


def test_unapproved_risk_policy_and_resealed_rank_fail_closed() -> None:
    arguments = _sources()
    arguments["forecast_risk_policy"]["status"] = "PROPOSED"
    arguments["forecast_risk_policy"]["content_hash"] = content_hash({
        key: value for key, value in arguments["forecast_risk_policy"].items()
        if key != "content_hash"
    })
    with pytest.raises(Exception, match="risk policy semantics"):
        build_generic_lyra_v2_decision_batch(**arguments)
    arguments = _sources()
    selection = arguments["target_selection_evidence"]
    selection["ranked_candidates"][0]["momentum_score"] += 1.0
    selection["content_hash"] = content_hash({
        key: value for key, value in selection.items() if key != "content_hash"
    })
    with pytest.raises(Exception, match="not recomputed"):
        build_generic_lyra_v2_decision_batch(**arguments)


def test_resealed_risk_policy_owner_approval_bypasses_fail_closed() -> None:
    arguments = _sources()
    arguments["forecast_risk_policy"]["owner_decision_hash"] = "9" * 64
    arguments["forecast_risk_policy"]["content_hash"] = content_hash({
        key: value for key, value in arguments["forecast_risk_policy"].items()
        if key != "content_hash"
    })
    with pytest.raises(Exception, match="owner approval binding"):
        build_generic_lyra_v2_decision_batch(**arguments)

    arguments = _sources()
    proposal = arguments["forecast_risk_policy_proposal"]
    proposal["policy_terms"]["formula_id"] = "UNAPPROVED_PLACEHOLDER"
    proposal["content_hash"] = content_hash({
        key: value for key, value in proposal.items() if key != "content_hash"
    })
    owner_decision = arguments["forecast_risk_policy_owner_decision"]
    owner_decision["proposal_hash"] = proposal["content_hash"]
    owner_decision["content_hash"] = content_hash({
        key: value for key, value in owner_decision.items() if key != "content_hash"
    })
    arguments["forecast_risk_policy"]["owner_decision_hash"] = owner_decision[
        "content_hash"
    ]
    arguments["forecast_risk_policy"]["content_hash"] = content_hash({
        key: value for key, value in arguments["forecast_risk_policy"].items()
        if key != "content_hash"
    })
    with pytest.raises(Exception, match="proposal terms differ"):
        build_generic_lyra_v2_decision_batch(**arguments)

    arguments = _sources()
    proposal = arguments["forecast_risk_policy_proposal"]
    proposal["policy_terms"]["maximum_order_participation_rate"] = 0.02
    proposal["content_hash"] = content_hash({
        key: value for key, value in proposal.items() if key != "content_hash"
    })
    with pytest.raises(Exception, match="proposal terms differ"):
        build_generic_lyra_v2_decision_batch(**arguments)
