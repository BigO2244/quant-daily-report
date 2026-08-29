from __future__ import annotations

import hashlib
import json
from pathlib import Path

from core.trading_integrity_certification import (
    CONTROL_NAMES,
    certify_window,
    trading_sessions_ending,
)


TRADE_DATE = "2026-08-28"
EFFECTIVE_DATE = "2026-08-27"
PRIOR_DATE = "2026-08-26"


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_trading_sessions_ending_excludes_weekends() -> None:
    assert trading_sessions_ending("2026-08-30", 3) == [
        "2026-08-26",
        "2026-08-27",
        "2026-08-28",
    ]


def test_missing_evidence_fails_all_controls(tmp_path: Path) -> None:
    result = certify_window(repo_root=tmp_path, through_date=TRADE_DATE, sessions=1)
    row = result["sessions"][0]
    assert result["status"] == "RED"
    assert result["trading_integrity_rate"] == 0.0
    assert row["certified"] is False
    assert set(row["controls"]) == set(CONTROL_NAMES)
    assert all(item["pass"] is False for item in row["controls"].values())


def test_non_decision_grade_universe_blocks_data_control(tmp_path: Path) -> None:
    bundle = tmp_path / "outputs" / "precompute" / TRADE_DATE
    prior_path = tmp_path / "outputs" / "shadow_candidates" / PRIOR_DATE / "caerus_orion.json"
    prior_lineage = {
        "effective_trade_date": PRIOR_DATE,
        "market_data_hash": "a",
        "feature_hash": "b",
        "full_rank_history_hash": "c",
        "rank_table_hash": "d",
    }
    _write(prior_path, {"decision_lineage": prior_lineage})
    lineage = {
        "effective_trade_date": EFFECTIVE_DATE,
        "market_data_asof": EFFECTIVE_DATE,
        "market_data_hash": "e",
        "feature_hash": "f",
        "full_rank_history_hash": "g",
        "rank_table_hash": "h",
        "coverage": {"status": "OK"},
    }
    contract = {
        "decision_lineage": lineage,
        "decision_freshness_status": "VERIFIED",
        "prior_decision_lineage": {
            "path": str(prior_path.relative_to(tmp_path)),
            "sha256": _hash(prior_path),
            "decision_lineage_hash": hashlib.sha256(
                json.dumps(prior_lineage, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            ).hexdigest(),
        },
    }
    _write(bundle / "contract.json", contract)
    _write(bundle / "paper_target_package.json", {"decision_lineage": lineage})
    _write(bundle / "signals.json", {"decision_lineage": lineage})
    _write(
        bundle / "sleeve_evaluations.json",
        {
            "envelopes": [
                {
                    "sleeve_id": "caerus_orion",
                    "reason_codes": ["NON_DECISION_GRADE_UNIVERSE"],
                    "universe": {
                        "method": "legacy_current_universe",
                        "source_available": True,
                    },
                    "opportunity": {
                        "decision_eligible": True,
                        "freshness_status": "VERIFIED",
                    },
                }
            ]
        },
    )
    result = certify_window(repo_root=tmp_path, through_date=TRADE_DATE, sessions=1)
    data = result["sessions"][0]["controls"]["data_freshness_pit_validity"]
    compute = result["sessions"][0]["controls"]["compute_recomputed"]
    assert data["pass"] is False
    assert "pit_universe_not_decision_grade" in data["reasons"]
    assert "non_decision_grade_universe_reason_code" in data["reasons"]
    assert compute["pass"] is True
