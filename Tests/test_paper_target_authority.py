from __future__ import annotations

import json
from pathlib import Path

from core.paper_target_authority import (
    seal_paper_target_bundle,
    validate_sealed_paper_target_bundle,
)
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
