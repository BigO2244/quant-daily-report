"""Regression tests for the LIVE broad-targets path (2026-07-09 incident follow-up).

The live pilot must rebalance to the BROAD precompute universe (signals_broad.json)
by affordability, while paper keeps reading the concentrated signals.json. These
tests cover the two seams: bundle emission and live-builder source selection.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import precompute_contract
from scripts.live_pilot_build_plan_from_precompute import _resolve_signals_path


def _write_bundle(tmp_path: Path, monkeypatch, *, with_broad: bool):
    monkeypatch.setattr(
        precompute_contract, "precompute_bundle_dir", lambda _td: tmp_path
    )
    broad = {"signals": [{"ticker": "AAA", "target_weight": 0.1}]} if with_broad else None
    precompute_contract.write_precompute_bundle(
        trade_date="2026-07-10",
        run_id="run-1",
        mode="LIVE_PILOT",
        daily_snapshot={"x": 1},
        signals_payload={"signals": [{"ticker": "AAA", "target_weight": 0.5}]},
        execution_payload={"trades": []},
        signals_broad_payload=broad,
        allow_overwrite=True,
    )


def test_bundle_emits_signals_broad_when_provided(tmp_path, monkeypatch):
    _write_bundle(tmp_path, monkeypatch, with_broad=True)
    broad_path = tmp_path / "signals_broad.json"
    assert broad_path.exists(), "signals_broad.json should be written when payload given"
    payload = json.loads(broad_path.read_text())
    assert payload["signals"][0]["ticker"] == "AAA"
    assert payload["signals"][0]["target_weight"] == 0.1  # the BROAD weight, not 0.5
    contract = json.loads((tmp_path / "contract.json").read_text())
    assert contract["files"].get("signals_broad") == "signals_broad.json"


def test_bundle_omits_signals_broad_when_absent(tmp_path, monkeypatch):
    _write_bundle(tmp_path, monkeypatch, with_broad=False)
    assert not (tmp_path / "signals_broad.json").exists()
    contract = json.loads((tmp_path / "contract.json").read_text())
    assert "signals_broad" not in contract["files"]


def _make_bundle_dir(tmp_path: Path, *, broad: bool):
    (tmp_path / "signals.json").write_text("{}")
    if broad:
        (tmp_path / "signals_broad.json").write_text("{}")
    return tmp_path / "planned_execution_payload.json"


def test_live_builder_prefers_broad_when_present(tmp_path, monkeypatch):
    monkeypatch.delenv("CAERUS_LIVE_PILOT_USE_BROAD_TARGETS", raising=False)
    payload_path = _make_bundle_dir(tmp_path, broad=True)
    resolved = _resolve_signals_path(payload_path, {})
    assert resolved.name == "signals_broad.json"


def test_live_builder_falls_back_to_signals_when_no_broad(tmp_path, monkeypatch):
    monkeypatch.delenv("CAERUS_LIVE_PILOT_USE_BROAD_TARGETS", raising=False)
    payload_path = _make_bundle_dir(tmp_path, broad=False)
    resolved = _resolve_signals_path(payload_path, {})
    assert resolved.name == "signals.json"


def test_live_builder_broad_toggle_off_forces_signals(tmp_path, monkeypatch):
    monkeypatch.setenv("CAERUS_LIVE_PILOT_USE_BROAD_TARGETS", "0")
    payload_path = _make_bundle_dir(tmp_path, broad=True)
    resolved = _resolve_signals_path(payload_path, {})
    assert resolved.name == "signals.json"
