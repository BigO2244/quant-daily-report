from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research.execution_payload_audit import (  # noqa: E402
    SCHEMA_VERSION,
    VERDICT_DATE_MISMATCH,
    VERDICT_EMPTY_BUT_VALID,
    VERDICT_PACKET_WRONG_DATE,
    VERDICT_TRULY_MISSING,
    build_execution_payload_audit,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def test_truly_missing_when_no_artifacts(tmp_path):
    p = build_execution_payload_audit(trade_date="2026-06-02", repo_root=tmp_path)
    assert p["verdict"] == VERDICT_TRULY_MISSING


def test_empty_but_valid_when_payload_has_zero_orders(tmp_path):
    _write_json(tmp_path / "outputs" / "precompute" / "2026-06-02" / "planned_execution_payload.json", {"orders": [], "trades": []})
    p = build_execution_payload_audit(trade_date="2026-06-02", repo_root=tmp_path)
    assert p["verdict"] == VERDICT_EMPTY_BUT_VALID


def test_payload_present_with_orders(tmp_path):
    _write_json(tmp_path / "outputs" / "precompute" / "2026-06-02" / "planned_execution_payload.json", {"orders": [{"ticker": "AAA"}]})
    p = build_execution_payload_audit(trade_date="2026-06-02", repo_root=tmp_path)
    assert p["verdict"] == "PAYLOAD_PRESENT_AND_NONEMPTY"


def test_packet_consuming_wrong_date(tmp_path):
    _write_json(tmp_path / "outputs" / "precompute" / "2026-04-30" / "planned_execution_payload.json", {"orders": [{"ticker": "AAA"}]})
    p = build_execution_payload_audit(trade_date="2026-06-02", repo_root=tmp_path)
    assert p["verdict"] == VERDICT_PACKET_WRONG_DATE
    assert "2026-04-30" in p["root_cause"]


def test_schema_and_artifacts_written(tmp_path):
    p = build_execution_payload_audit(trade_date="2026-06-02", repo_root=tmp_path)
    assert p["schema_version"] == SCHEMA_VERSION
    assert (tmp_path / "outputs" / "research" / "execution_payload_audit" / "2026-06-02" / "execution_payload_audit.json").exists()
