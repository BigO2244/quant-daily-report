from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.research.check_precompute_semantic_validation import inspect_precompute_semantics, render_markdown


TRADE_DATE = "2026-05-26"
REQUIRED = ("contract.json", "daily_snapshot.json", "signals.json", "planned_execution_payload.json")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_bundle(bundle: Path, *, strategy: str = "caerus_polaris", malformed_order: bool = False) -> None:
    base = {"trade_date": TRADE_DATE, "run_id": "run-1", "workflow_kind": "live", "live_strategy_id": strategy}
    for name in REQUIRED:
        payload = dict(base)
        if name == "planned_execution_payload.json":
            payload["orders"] = [
                {"symbol": "AAPL", "side": "buy", "qty": 1},
                {"symbol": "MSFT", "side": "sell"} if malformed_order else {"ticker": "MSFT", "action": "sell", "quantity": 2},
            ]
        _write_json(bundle / name, payload)


def test_complete_polaris_bundle_is_advisory_ok(tmp_path: Path) -> None:
    bundle = tmp_path / "outputs" / "precompute" / TRADE_DATE
    _write_bundle(bundle)

    payload = inspect_precompute_semantics(bundle_dir=bundle, trade_date=TRADE_DATE)

    assert payload["status"] == "OK"
    assert payload["blocking"] is False
    assert payload["runtime_effect"] == "none"
    assert payload["baseline_bundle_status"] == "OK"


def test_missing_required_file_makes_semantics_not_assessable(tmp_path: Path) -> None:
    bundle = tmp_path / "outputs" / "precompute" / TRADE_DATE
    _write_bundle(bundle)
    (bundle / "signals.json").unlink()

    payload = inspect_precompute_semantics(bundle_dir=bundle, trade_date=TRADE_DATE)

    assert payload["status"] == "WARN"
    assert payload["blocking"] is False
    assert payload["baseline_bundle_status"] == "FAILED"
    assert "missing:signals.json" in payload["baseline_validation_failures"]


def test_shadow_strategy_surface_is_advisory_failure_not_blocking(tmp_path: Path) -> None:
    bundle = tmp_path / "outputs" / "precompute" / TRADE_DATE
    _write_bundle(bundle, strategy="caerus_orion")

    payload = inspect_precompute_semantics(bundle_dir=bundle, trade_date=TRADE_DATE)

    assert payload["status"] == "FAIL_ADVISORY"
    assert payload["blocking"] is False
    assert any(check["name"] == "strategy_surface" and check["status"] == "FAIL_ADVISORY" for check in payload["checks"])


def test_malformed_order_shape_warns(tmp_path: Path) -> None:
    bundle = tmp_path / "outputs" / "precompute" / TRADE_DATE
    _write_bundle(bundle, malformed_order=True)

    payload = inspect_precompute_semantics(bundle_dir=bundle, trade_date=TRADE_DATE)
    markdown = render_markdown(payload)

    assert payload["status"] == "WARN"
    assert "planned_order_shape" in markdown
    assert "Blocking: False" in markdown


def test_cli_strict_exits_nonzero_for_warning(tmp_path: Path) -> None:
    bundle = tmp_path / "outputs" / "precompute" / TRADE_DATE
    _write_bundle(bundle, malformed_order=True)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.research.check_precompute_semantic_validation",
            "--bundle-dir",
            str(bundle),
            "--trade-date",
            TRADE_DATE,
            "--json",
            "--strict",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blocking"] is False
