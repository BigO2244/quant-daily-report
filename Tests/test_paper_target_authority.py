from __future__ import annotations

import json
import copy
from pathlib import Path

import core.paper_target_authority as paper_target_authority
from core.paper_target_authority import (
    seal_paper_target_bundle,
    validate_sealed_paper_target_bundle,
)
from core.sleeve_control_plane import SleeveControlRegistry, dispatch_all_sleeves
from core.strategy_registry import StrategyRegistry
from scripts.certify_execution_readiness import certify_execution_readiness
from Tests.test_execution_readiness_certification import FakeBroker, _account
from Tests.test_live_pilot_build_plan_from_precompute import (
    _bundle,
    _orion_shadow,
    _write_sleeve_evaluations,
)


def _sealed_fixture(tmp_path: Path) -> Path:
    payload_path = _bundle(
        tmp_path,
        trade_date="2026-08-14",
        signals=[
            {"ticker": "AMGN", "target_weight": 0.5, "sleeve": "sleeve_quality"},
            {"ticker": "BAC", "target_weight": 0.5, "sleeve": "sleeve_quality"},
        ],
        payload_trades=[
                {
                "ticker": "AMGN",
                "side": "BUY",
                "shares": 2,
                "entry_price": 400.0,
                "notional": 800.0,
            }
        ],
    )
    _orion_shadow(
        tmp_path,
        trade_date="2026-08-14",
        weights={"AAPL": 0.5, "MSFT": 0.5},
    )
    _write_sleeve_evaluations(payload_path, tmp_path)
    seal_paper_target_bundle(
        bundle_dir=payload_path.parent,
        trade_date="2026-08-14",
        repo_root=tmp_path,
        sealed_at="2026-08-14T11:00:00+00:00",
    )
    return payload_path


def test_seal_quarantines_research_and_publishes_one_target_hash(tmp_path: Path) -> None:
    payload_path = _sealed_fixture(tmp_path)
    bundle = payload_path.parent
    package = json.loads((bundle / "paper_target_package.json").read_text())
    signals = json.loads((bundle / "signals.json").read_text())
    handoff = json.loads(payload_path.read_text())
    contract = json.loads((bundle / "contract.json").read_text())

    assert {row["ticker"] for row in signals["signals"]} == {"AAPL", "MSFT"}
    assert handoff["trades"] == []
    assert handoff["precompute_execution_authority"] is False
    assert handoff["exact_orders_deferred_to_0935"] is True
    assert {
        package["approved_target_hash"],
        signals["approved_target_hash"],
        handoff["approved_target_hash"],
        contract["approved_target_hash"],
    } == {package["decision_package"]["content_hash"]}
    research_path = tmp_path / handoff["research_precompute"][
        "planned_execution_payload_path"
    ]
    assert json.loads(research_path.read_text())["trades"][0]["ticker"] == "AMGN"
    assert validate_sealed_paper_target_bundle(
        bundle_dir=bundle,
        trade_date="2026-08-14",
        repo_root=tmp_path,
    ) == []


def test_sealed_target_tamper_fails_closed(tmp_path: Path) -> None:
    payload_path = _sealed_fixture(tmp_path)
    signals_path = payload_path.with_name("signals.json")
    signals = json.loads(signals_path.read_text())
    signals["signals"][0]["target_weight"] = 0.01
    signals_path.write_text(json.dumps(signals) + "\n", encoding="utf-8")

    failures = validate_sealed_paper_target_bundle(
        bundle_dir=payload_path.parent,
        trade_date="2026-08-14",
        repo_root=tmp_path,
    )

    assert "paper_target:file_hash_mismatch:signals" in failures


def test_seal_is_independent_of_mutable_shadow_publication(tmp_path: Path) -> None:
    payload_path = _sealed_fixture(tmp_path)
    bundle = payload_path.parent
    package = json.loads((bundle / "paper_target_package.json").read_text())
    source_ref = package["source_strategy_artifact"]
    sealed_source = tmp_path / source_ref["path"]
    mutable_source = (
        tmp_path
        / "outputs"
        / "shadow_candidates"
        / "2026-08-14"
        / "caerus_orion.json"
    )

    assert sealed_source == bundle / "sealed_source_caerus_orion.json"
    assert sealed_source.read_bytes() == mutable_source.read_bytes()

    mutable_source.write_text('{"rebuilt": true}\n', encoding="utf-8")
    assert validate_sealed_paper_target_bundle(
        bundle_dir=bundle,
        trade_date="2026-08-14",
        repo_root=tmp_path,
    ) == []

    sealed_source.write_text('{"tampered": true}\n', encoding="utf-8")
    failures = validate_sealed_paper_target_bundle(
        bundle_dir=bundle,
        trade_date="2026-08-14",
        repo_root=tmp_path,
    )
    assert "paper_target:source_strategy_hash_mismatch" in failures


def test_readiness_certifies_target_not_fake_preopen_orders(tmp_path: Path) -> None:
    payload_path = _sealed_fixture(tmp_path)
    broker = FakeBroker(_account())

    result = certify_execution_readiness(
        trade_date="2026-08-14",
        planned_payload_path=payload_path,
        mode="paper",
        no_submit=True,
        write_artifact=False,
        broker=broker,
        repo_root=tmp_path,
    )

    assert result["schema_version"] == "execution_readiness_certification.v2"
    assert result["readiness_status"] == "PASS"
    assert result["target_name_count"] == 2
    assert result["expected_submissions"] is None
    assert result["exact_orders_deferred_to_0935"] is True
    assert broker.submit_calls == 0


def test_allocator_seal_supports_governed_multiple_capital_sleeves(
    tmp_path: Path, monkeypatch
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    registry_payload = json.loads(
        (repo_root / "config" / "research" / "strategy_registry.json").read_text()
    )
    registry_payload = copy.deepcopy(registry_payload)
    control_payload = registry_payload["sleeve_control_plane"]
    control_payload["strategy_overrides"]["caerus_lyra"].update(
        {
            "capital_eligible": True,
            "execution_eligible": True,
            "evaluation_only": False,
        }
    )
    control_payload["paper_allocation_policy"]["sleeve_risk_budgets"] = {
        "caerus_orion": 0.6,
        "caerus_lyra": 0.4,
    }
    orion = next(
        row for row in registry_payload["strategies"] if row["strategy_id"] == "caerus_orion"
    )
    lyra = next(
        row for row in registry_payload["strategies"] if row["strategy_id"] == "caerus_lyra"
    )
    lyra.update(
        {
            "status": "paper",
            "execution_impact": "PAPER",
            "eligible_for_promotion": False,
            "paper_execution": {
                **copy.deepcopy(orion["paper_execution"]),
                "source_variant": lyra["shadow_tracking"]["source_variant"],
            },
        }
    )
    registry_path = tmp_path / "config" / "research" / "strategy_registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(json.dumps(registry_payload), encoding="utf-8")
    manifest_path = tmp_path / "research_registry" / "sleeves" / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        (repo_root / "research_registry" / "sleeves" / "manifest.json").read_text(),
        encoding="utf-8",
    )
    control = SleeveControlRegistry.from_path(
        registry_path, enforce_manifest_parity=False
    )
    strategies = StrategyRegistry.from_path(registry_path)
    monkeypatch.setattr(
        paper_target_authority, "load_sleeve_control_registry", lambda: control
    )
    monkeypatch.setattr(
        paper_target_authority,
        "load_strategy_registry_for_repo",
        lambda _root: strategies,
    )

    payload_path = _bundle(tmp_path, trade_date="2026-08-14", signals=[])
    _orion_shadow(
        tmp_path,
        trade_date="2026-08-14",
        weights={"AAPL": 0.5, "MSFT": 0.5},
    )
    lyra_path = (
        tmp_path
        / "outputs"
        / "shadow_candidates"
        / "2026-08-14"
        / "caerus_lyra.json"
    )
    lyra_path.write_text(
        json.dumps(
            {
                "trade_date": "2026-08-14",
                "effective_trade_date": "2026-08-14",
                "strategy_slug": "caerus_lyra",
                    "source_variant": lyra["shadow_tracking"]["source_variant"],
                    "decision_eligible": True,
                    "observation_status": "OK",
                    "data_status": "OK",
                    "target_weights": {"AAPL": 0.5, "GOOG": 0.5},
            }
        ),
        encoding="utf-8",
    )
    daily_snapshot = {
        "asof": "2026-08-14",
        "sleeve_allocations": {
            key: 0.0 for key in control.functional_allocation_keys()
        },
        "allocation_diagnostics": {"sleeve_1": {"cash_routing": []}},
        "holdings": [],
    }
    sleeve_payload = dispatch_all_sleeves(
        trade_date="2026-08-14",
        run_id="multi-capital",
        daily_snapshot=daily_snapshot,
        runtime_root=tmp_path,
        registry=control,
    )
    payload_path.with_name("sleeve_evaluations.json").write_text(
        json.dumps(sleeve_payload), encoding="utf-8"
    )

    package = seal_paper_target_bundle(
        bundle_dir=payload_path.parent,
        trade_date="2026-08-14",
        repo_root=tmp_path,
        sealed_at="2026-08-14T11:00:00+00:00",
    )

    assert package["approved_sleeve"] == "caerus_paper_portfolio"
    assert package["capital_sleeves"] == ["caerus_lyra", "caerus_orion"]
    weights = {row["symbol"]: row["target_weight"] for row in package["target_rows"]}
    assert weights == {"AAPL": 0.475, "GOOG": 0.19, "MSFT": 0.285}
    aapl = next(row for row in package["target_rows"] if row["symbol"] == "AAPL")
    assert {row["sleeve_id"] for row in aapl["sleeve_contributions"]} == {
        "caerus_orion",
        "caerus_lyra",
    }
