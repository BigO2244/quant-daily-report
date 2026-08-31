from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.strategy_registry import (
    SCHEMA_VERSION,
    StrategyRegistryEntry,
    load_strategy_registry_for_repo,
    load_strategy_registry,
)
from research.shadow_tracking.strategies import build_shadow_definitions


def test_default_registry_preserves_current_active_shadow_strategies() -> None:
    registry = load_strategy_registry()

    assert registry.active_shadow_security_selection_ids() == (
        "caerus_polaris",
        "caerus_polaris_alpha",
        "caerus_orion",
        "caerus_orion_alpha",
        "caerus_lyra",
    )
    assert registry.baseline_strategy_id() == "caerus_polaris"
    assert registry.paper_execution_strategy_id() == "caerus_orion"
    assert registry.paper_execution_config()["target_cash_weight"] == 0.05
    assert (
        registry.paper_execution_config()["source_session_policy"]
        == "SAME_OR_PREVIOUS_TRADING_SESSION"
    )
    assert registry.paper_execution_config()["max_source_trading_session_lag"] == 1
    policy = registry.paper_execution_config()["target_attainment_policy"]
    assert policy["account_scope"] == "PAPER"
    assert policy["share_mode"] == "FRACTIONAL_SHARES"
    assert policy["target_cash_weight"] == 0.05
    assert policy["minimum_cash_weight"] == 0.025
    assert policy["fixed_drift_tolerance"] == 0.02
    assert policy["nearest_feasible_required"] is False
    assert policy["comparison_epoch_policy"] == "FIRST_CLEAN_POST_FIX_PAPER_RUN"
    assert policy["strict_green_propagation"] is True
    assert registry.promotion_candidate_ids() == (
        "caerus_polaris_alpha",
        "caerus_orion_alpha",
        "caerus_lyra",
    )
    assert "caerus_orion" in registry.research_challenger_ids()


def test_repo_registry_loader_prefers_repo_local_config(tmp_path: Path) -> None:
    path = tmp_path / "config" / "research" / "strategy_registry.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "strategies": [
                    {
                        "strategy_id": "caerus_test_control",
                        "display_name": "Caerus Test Control",
                        "strategy_type": "security_selection",
                        "family": "core_momentum",
                        "status": "paper",
                        "role": "baseline",
                        "eligible_for_shadow": True,
                        "eligible_for_promotion": False,
                        "benchmark": "SPY",
                        "execution_impact": "NON_EXECUTIONAL",
                        "shadow_tracking": {"enabled": True, "source_variant": "test"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    registry = load_strategy_registry_for_repo(tmp_path)

    assert registry.active_shadow_security_selection_ids() == ("caerus_test_control",)
    assert registry.baseline_strategy_id() == "caerus_test_control"


def test_default_registry_keeps_future_research_and_overlay_entries_inactive() -> None:
    registry = load_strategy_registry()

    phoenix = registry.require("caerus_phoenix")
    cygnus = registry.require("caerus_cygnus")
    cassiopeia = registry.require("caerus_cassiopeia")
    argo = registry.require("caerus_argo")

    assert phoenix.status == "research"
    assert cygnus.status == "research"
    assert cassiopeia.status == "research"
    assert cassiopeia.strategy_type == "security_selection"
    assert cassiopeia.is_security_selection is True
    assert cassiopeia.capabilities == {
        "produces_holdings": True,
        "produces_nav": True,
        "produces_attribution": True,
        "produces_promotion_metrics": False,
        "produces_regime_overlay": False,
    }
    assert phoenix.strategy_id not in registry.active_shadow_security_selection_ids()
    assert cygnus.strategy_id not in registry.active_shadow_security_selection_ids()
    assert cassiopeia.strategy_id not in registry.active_shadow_security_selection_ids()

    assert argo.strategy_type == "meta_model"
    assert argo.is_meta_model is True
    assert argo.strategy_id not in registry.active_shadow_security_selection_ids()
    assert argo.capabilities == {
        "produces_holdings": False,
        "produces_nav": False,
        "produces_attribution": False,
        "produces_promotion_metrics": False,
        "produces_regime_overlay": True,
        "produces_model_selection": True,
    }


def test_shadow_definitions_are_registry_driven_without_changing_current_specs() -> None:
    definitions = build_shadow_definitions()
    by_slug = {definition.strategy_slug: definition for definition in definitions}

    assert tuple(by_slug) == (
        "caerus_polaris",
        "caerus_polaris_alpha",
        "caerus_orion",
        "caerus_orion_alpha",
        "caerus_lyra",
    )
    assert by_slug["caerus_polaris"].strategy_name == "Caerus Polaris"
    assert by_slug["caerus_polaris"].spec.top_n == 10
    assert by_slug["caerus_polaris_alpha"].strategy_name == "Polaris_Alpha"
    assert by_slug["caerus_polaris_alpha"].spec.top_n == 4
    assert by_slug["caerus_polaris_alpha"].spec.max_position_weight == 0.20
    assert by_slug["caerus_orion"].spec.top_n == 5
    assert by_slug["caerus_orion"].spec.use_rank_decay_exit is True
    assert by_slug["caerus_orion_alpha"].strategy_name == "Orion_Alpha"
    assert by_slug["caerus_orion_alpha"].spec.top_n == 3
    assert by_slug["caerus_orion_alpha"].spec.use_rank_decay_exit is True
    assert by_slug["caerus_orion_alpha"].spec.max_position_weight == 0.25
    assert by_slug["caerus_lyra"].spec.rebalance_mode == "weekly"
    assert by_slug["caerus_lyra"].spec.use_rank_decay_exit is False


def test_shadow_definitions_respect_observation_start_dates() -> None:
    before_start = tuple(definition.strategy_slug for definition in build_shadow_definitions(trade_date="2026-06-22"))
    on_start = tuple(definition.strategy_slug for definition in build_shadow_definitions(trade_date="2026-06-23"))

    assert before_start == (
        "caerus_polaris",
        "caerus_orion",
        "caerus_lyra",
    )
    assert on_start == (
        "caerus_polaris",
        "caerus_polaris_alpha",
        "caerus_orion",
        "caerus_orion_alpha",
        "caerus_lyra",
    )


def test_registry_validation_rejects_overlay_holdings_capability() -> None:
    payload = {
        "strategy_id": "caerus_bad_overlay",
        "display_name": "Bad Overlay",
        "strategy_type": "overlay",
        "family": "regime_overlay",
        "status": "research",
        "eligible_for_shadow": False,
        "eligible_for_promotion": False,
        "benchmark": None,
        "execution_impact": "NON_EXECUTIONAL",
        "capabilities": {
            "produces_holdings": True,
            "produces_nav": False,
        },
    }

    with pytest.raises(ValueError, match="overlay strategies must not declare holdings"):
        StrategyRegistryEntry.from_payload(payload)


def test_registry_loader_rejects_duplicate_ids(tmp_path: Path) -> None:
    entry = {
        "strategy_id": "caerus_duplicate",
        "display_name": "Duplicate",
        "strategy_type": "security_selection",
        "family": "core_momentum",
        "status": "research",
        "eligible_for_shadow": False,
        "eligible_for_promotion": False,
        "benchmark": "SPY",
        "execution_impact": "NON_EXECUTIONAL",
    }
    path = tmp_path / "strategy_registry.json"
    path.write_text(
        json.dumps({"schema_version": SCHEMA_VERSION, "strategies": [entry, dict(entry)]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate strategy_id"):
        load_strategy_registry(path)


def test_paper_execution_config_rejects_invalid_target_attainment_policy(
    tmp_path: Path,
) -> None:
    payload = json.loads(
        Path("config/research/strategy_registry.json").read_text(encoding="utf-8")
    )
    orion = next(
        row for row in payload["strategies"] if row["strategy_id"] == "caerus_orion"
    )
    orion["paper_execution"]["target_attainment_policy"][
        "strict_green_propagation"
    ] = False
    registry_path = tmp_path / "strategy_registry.json"
    registry_path.write_text(json.dumps(payload), encoding="utf-8")

    registry = load_strategy_registry(registry_path)
    with pytest.raises(ValueError, match="strict green propagation"):
        registry.paper_execution_config()
