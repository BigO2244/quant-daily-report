from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path

import pytest

from core.sleeve_control_plane import (
    BATCH_SCHEMA_VERSION,
    SleeveControlRegistry,
    SleeveRegistryIntegrityError,
    dispatch_all_sleeves,
    load_sleeve_control_registry,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REPO_ROOT / "config" / "research" / "strategy_registry.json"
MANIFEST_PATH = REPO_ROOT / "research_registry" / "sleeves" / "manifest.json"
FUNCTIONAL_KEYS = {
    "sleeve_trend",
    "sleeve_2",
    "sleeve_quality",
    "sleeve_mean_reversion",
    "sleeve_defensive_etf",
}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _runtime_fixture(root: Path, trade_date: str = "2026-08-12") -> dict:
    universe = root / "data" / "universe.csv"
    universe.parent.mkdir(parents=True, exist_ok=True)
    universe.write_text("ticker\nAAA\nBBB\n", encoding="utf-8")
    shadow_root = root / "outputs" / "shadow_candidates" / trade_date
    for sleeve_id in (
        "caerus_polaris",
        "caerus_polaris_alpha",
        "caerus_orion",
        "caerus_orion_alpha",
        "caerus_lyra",
    ):
        _write_json(
            shadow_root / f"{sleeve_id}.json",
            {
                "trade_date": trade_date,
                "effective_trade_date": trade_date,
                "strategy_slug": sleeve_id,
                "source_variant": "fixture",
                "target_weights": {"AAA": 0.6, "BBB": 0.4},
            },
        )
    _write_json(
        shadow_root / "shadow_performance.json",
        {
            "trade_date": trade_date,
            "strategies": {
                "spy_benchmark": {"nav": 1.2, "daily_return": 0.01},
            },
        },
    )
    _write_json(
        root
        / "outputs"
        / "research"
        / "phoenix"
        / trade_date
        / "phoenix_holdings.json",
        {"status": "OK", "holdings": [{"ticker": "AAA"}]},
    )
    _write_json(
        root
        / "outputs"
        / "research"
        / "argo"
        / trade_date
        / "argo_recommendations.json",
        {"status": "OK", "recommendations": []},
    )
    daily_snapshot = {
        "asof": trade_date,
        "sleeve_allocations": {
            "sleeve_trend": 0.4,
            "sleeve_2": 0.2,
            "sleeve_quality": 0.2,
            "sleeve_mean_reversion": 0.15,
            "sleeve_defensive_etf": 0.0,
        },
        "allocation_diagnostics": {"sleeve_1": {"cash_routing": []}},
        "holdings": [],
    }
    _write_json(
        root / "outputs" / "precompute" / trade_date / "daily_snapshot.json",
        daily_snapshot,
    )
    return daily_snapshot


def _copy_registry(tmp_path: Path) -> tuple[dict, Path]:
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    path = tmp_path / "config" / "research" / "strategy_registry.json"
    _write_json(path, payload)
    return payload, path


def _by_id(payload: dict) -> dict[str, dict]:
    return {item["sleeve_id"]: item for item in payload["envelopes"]}


def test_canonical_registry_maps_every_named_and_functional_sleeve() -> None:
    registry = load_sleeve_control_registry()

    assert len(registry.definitions) == 15
    assert registry.functional_allocation_keys() == FUNCTIONAL_KEYS
    assert [item.sleeve_id for item in registry.definitions if item.capital_eligible] == [
        "caerus_orion"
    ]
    assert [item.sleeve_id for item in registry.definitions if item.execution_eligible] == [
        "caerus_orion"
    ]
    assert all(
        item.evaluation_only
        for item in registry.definitions
        if item.sleeve_id != "caerus_orion"
    )


def test_dispatcher_emits_terminal_envelope_for_every_non_frozen_sleeve(
    tmp_path: Path,
) -> None:
    snapshot = _runtime_fixture(tmp_path)
    payload = dispatch_all_sleeves(
        trade_date="2026-08-12",
        run_id="fixture-run",
        daily_snapshot=snapshot,
        runtime_root=tmp_path,
        now=dt.datetime(2026, 8, 12, 12, tzinfo=dt.timezone.utc),
    )

    assert payload["schema_version"] == BATCH_SCHEMA_VERSION
    assert payload["all_non_frozen_evaluated"] is True
    assert payload["summary"]["expected_count"] == 15
    assert payload["summary"]["envelope_count"] == 15
    assert set(payload["expected_non_frozen_sleeve_ids"]) == {
        item["sleeve_id"] for item in payload["envelopes"]
    }
    assert all(
        item["evaluation"]["status"]
        in {"OK", "NO_OPPORTUNITY", "BLOCKED", "FAILED"}
        for item in payload["envelopes"]
    )
    assert payload["summary"]["capital_eligible_sleeve_ids"] == ["caerus_orion"]
    assert payload["summary"]["execution_eligible_sleeve_ids"] == ["caerus_orion"]

    envelopes = _by_id(payload)
    assert envelopes["caerus_orion"]["evaluation"]["status"] == "OK"
    assert envelopes["caerus_orion"]["eligibility"]["capital_eligible"] is True
    assert envelopes["sleeve_defensive_etf"]["evaluation"]["status"] == "NO_OPPORTUNITY"
    assert envelopes["caerus_cygnus"]["evaluation"]["status"] == "BLOCKED"
    assert envelopes["caerus_cassiopeia"]["evaluation"]["status"] == "BLOCKED"
    assert envelopes["caerus_phoenix"]["provenance"]["source_artifacts"][0]["sha256"]
    assert envelopes["caerus_polaris"]["universe"]["snapshot_hash"]


def test_shadow_benchmark_stale_cache_is_explicitly_unavailable(tmp_path: Path) -> None:
    snapshot = _runtime_fixture(tmp_path)
    performance_path = (
        tmp_path
        / "outputs"
        / "shadow_candidates"
        / "2026-08-12"
        / "shadow_performance.json"
    )
    payload = json.loads(performance_path.read_text(encoding="utf-8"))
    payload.update({"data_status": "NO_DATA", "data_reason": "PRICE_CACHE_STALE"})
    _write_json(performance_path, payload)

    result = dispatch_all_sleeves(
        trade_date="2026-08-12",
        run_id="stale-benchmark",
        daily_snapshot=snapshot,
        runtime_root=tmp_path,
    )

    benchmark = _by_id(result)["spy_benchmark"]
    assert benchmark["evaluation"]["status"] == "FAILED"
    assert "PRICE_CACHE_STALE" in benchmark["reason_codes"]


def test_shadow_source_cannot_fall_back_beyond_prior_trading_day(tmp_path: Path) -> None:
    snapshot = _runtime_fixture(tmp_path)
    current = (
        tmp_path
        / "outputs"
        / "shadow_candidates"
        / "2026-08-12"
        / "caerus_lyra.json"
    )
    old = current.parent.parent / "2026-08-10" / current.name
    old.parent.mkdir(parents=True)
    current.replace(old)

    result = dispatch_all_sleeves(
        trade_date="2026-08-12",
        run_id="old-shadow-source",
        daily_snapshot=snapshot,
        runtime_root=tmp_path,
    )

    lyra = _by_id(result)["caerus_lyra"]
    assert lyra["evaluation"]["status"] == "BLOCKED"
    assert "SOURCE_DEPENDENCY_BLOCKED" in lyra["reason_codes"]


def test_missing_runner_is_visible_as_blocked_envelope(tmp_path: Path) -> None:
    payload, path = _copy_registry(tmp_path)
    payload["sleeve_control_plane"]["strategy_overrides"]["caerus_phoenix"][
        "runner"
    ] = "missing_fixture_runner"
    _write_json(path, payload)
    registry = SleeveControlRegistry.from_path(path, enforce_manifest_parity=False)
    snapshot = _runtime_fixture(tmp_path)

    result = dispatch_all_sleeves(
        trade_date="2026-08-12",
        run_id="fixture-run",
        daily_snapshot=snapshot,
        runtime_root=tmp_path,
        registry=registry,
    )

    phoenix = _by_id(result)["caerus_phoenix"]
    assert phoenix["evaluation"]["status"] == "BLOCKED"
    assert "RUNNER_NOT_REGISTERED" in phoenix["reason_codes"]
    assert result["summary"]["envelope_count"] == result["summary"]["expected_count"]


def test_paper_authority_uses_prior_decision_eligible_snapshot_when_current_is_pending(
    tmp_path: Path,
) -> None:
    snapshot = _runtime_fixture(tmp_path)
    current_path = (
        tmp_path
        / "outputs"
        / "shadow_candidates"
        / "2026-08-12"
        / "caerus_orion.json"
    )
    current = json.loads(current_path.read_text(encoding="utf-8"))
    current.update(
        {
            "observation_status": "PENDING_SESSION_CLOSE",
            "decision_eligible": False,
        }
    )
    _write_json(current_path, current)
    prior = dict(current)
    prior.update(
        {
            "trade_date": "2026-08-11",
            "effective_trade_date": "2026-08-11",
            "observation_status": "COMPLETE",
            "decision_eligible": True,
        }
    )
    prior_path = (
        tmp_path
        / "outputs"
        / "shadow_candidates"
        / "2026-08-11"
        / "caerus_orion.json"
    )
    _write_json(prior_path, prior)

    result = dispatch_all_sleeves(
        trade_date="2026-08-12",
        run_id="fixture-run",
        daily_snapshot=snapshot,
        runtime_root=tmp_path,
    )
    orion = _by_id(result)["caerus_orion"]

    assert orion["evaluation"]["status"] == "OK"
    assert orion["eligibility"]["evaluation_usable_for_capital"] is True
    assert orion["opportunity"]["effective_trade_date"] == "2026-08-11"
    assert orion["provenance"]["source_artifacts"][0]["path"].endswith(
        "outputs/shadow_candidates/2026-08-11/caerus_orion.json"
    )


def test_frozen_sleeve_is_explicitly_excluded_with_reason(tmp_path: Path) -> None:
    payload, path = _copy_registry(tmp_path)
    phoenix = payload["sleeve_control_plane"]["strategy_overrides"]["caerus_phoenix"]
    phoenix["frozen"] = True
    phoenix["frozen_reason"] = "owner_frozen_pending_review"
    _write_json(path, payload)
    registry = SleeveControlRegistry.from_path(path, enforce_manifest_parity=False)

    result = dispatch_all_sleeves(
        trade_date="2026-08-12",
        run_id="fixture-run",
        daily_snapshot=_runtime_fixture(tmp_path),
        runtime_root=tmp_path,
        registry=registry,
    )

    assert result["summary"]["expected_count"] == 14
    assert "caerus_phoenix" not in _by_id(result)
    assert result["frozen_sleeves"] == [
        {
            "sleeve_id": "caerus_phoenix",
            "reason": "owner_frozen_pending_review",
        }
    ]


def test_unregistered_nonzero_allocatable_sleeve_fails_closed(tmp_path: Path) -> None:
    snapshot = _runtime_fixture(tmp_path)
    snapshot["sleeve_allocations"]["rogue_sleeve"] = 0.1

    with pytest.raises(
        SleeveRegistryIntegrityError,
        match="unregistered allocatable sleeves: rogue_sleeve",
    ):
        dispatch_all_sleeves(
            trade_date="2026-08-12",
            run_id="fixture-run",
            daily_snapshot=snapshot,
            runtime_root=tmp_path,
        )


def test_no_opportunity_is_distinct_from_failed_evaluation(tmp_path: Path) -> None:
    snapshot = _runtime_fixture(tmp_path)
    snapshot["sleeve_allocations"].pop("sleeve_quality")

    result = dispatch_all_sleeves(
        trade_date="2026-08-12",
        run_id="fixture-run",
        daily_snapshot=snapshot,
        runtime_root=tmp_path,
    )
    envelopes = _by_id(result)

    assert envelopes["sleeve_defensive_etf"]["evaluation"]["status"] == "NO_OPPORTUNITY"
    assert envelopes["sleeve_quality"]["evaluation"]["status"] == "FAILED"
    assert "RUNNER_EXCEPTION" in envelopes["sleeve_quality"]["reason_codes"]


def test_registry_manifest_parity_corruption_fails_closed(tmp_path: Path) -> None:
    _, registry_path = _copy_registry(tmp_path)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    polaris = next(
        item for item in manifest["sleeves"] if item["strategy_id"] == "caerus_polaris"
    )
    polaris["strategy_registry_status"] = "paper"
    manifest_path = tmp_path / "research_registry" / "sleeves" / "manifest.json"
    _write_json(manifest_path, manifest)

    with pytest.raises(SleeveRegistryIntegrityError, match="caerus_polaris"):
        SleeveControlRegistry.from_path(
            registry_path,
            manifest_path=manifest_path,
            enforce_manifest_parity=True,
        )


def test_partially_configured_second_capital_sleeve_is_registry_corruption(tmp_path: Path) -> None:
    payload, path = _copy_registry(tmp_path)
    lyra = payload["sleeve_control_plane"]["strategy_overrides"]["caerus_lyra"]
    lyra["capital_eligible"] = True
    _write_json(path, payload)

    with pytest.raises(SleeveRegistryIntegrityError, match="eligibility must match"):
        SleeveControlRegistry.from_path(path, enforce_manifest_parity=False)


def test_governed_multi_sleeve_capital_configuration_needs_no_code_change(
    tmp_path: Path,
) -> None:
    payload, path = _copy_registry(tmp_path)
    lyra_override = payload["sleeve_control_plane"]["strategy_overrides"][
        "caerus_lyra"
    ]
    lyra_override.update(
        {
            "capital_eligible": True,
            "execution_eligible": True,
            "evaluation_only": False,
        }
    )
    lyra_row = next(
        row for row in payload["strategies"] if row["strategy_id"] == "caerus_lyra"
    )
    lyra_row.update({"status": "paper", "execution_impact": "PAPER"})
    payload["sleeve_control_plane"]["paper_allocation_policy"][
        "sleeve_risk_budgets"
    ] = {"caerus_orion": 0.6, "caerus_lyra": 0.4}
    _write_json(path, payload)

    registry = SleeveControlRegistry.from_path(
        path, enforce_manifest_parity=False
    )

    assert {
        item.sleeve_id for item in registry.definitions if item.capital_eligible
    } == {"caerus_orion", "caerus_lyra"}
