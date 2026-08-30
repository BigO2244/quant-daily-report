from __future__ import annotations

import hashlib
import json
from pathlib import Path

from core.governed_universe_freeze import build_governed_universe_freeze
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


def _seed_data_control(
    root: Path,
    *,
    trade_date: str,
    effective_date: str,
    prior_date: str,
    prospective_freeze: dict | None = None,
    freeze_sha256: str | None = None,
    evaluated_at: str | None = None,
) -> None:
    bundle = root / "outputs" / "precompute" / trade_date
    prior_path = root / "outputs" / "shadow_candidates" / prior_date / "caerus_orion.json"
    prior_lineage = {
        "effective_trade_date": prior_date,
        "market_data_hash": "a",
        "feature_hash": "b",
        "full_rank_history_hash": "c",
        "rank_table_hash": "d",
    }
    _write(prior_path, {"decision_lineage": prior_lineage})
    lineage = {
        "effective_trade_date": effective_date,
        "market_data_asof": effective_date,
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
            "path": str(prior_path.relative_to(root)),
            "sha256": _hash(prior_path),
            "decision_lineage_hash": hashlib.sha256(
                json.dumps(prior_lineage, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            ).hexdigest(),
        },
    }
    _write(bundle / "contract.json", contract)
    _write(bundle / "paper_target_package.json", {"decision_lineage": lineage})
    _write(bundle / "signals.json", {"decision_lineage": lineage})
    universe_path = root / "data" / "universe.csv"
    universe_path.parent.mkdir(parents=True, exist_ok=True)
    universe_path.write_text("ticker,sector\nAAA,One\n\nBBB,Two\n", encoding="utf-8")
    universe = {
        "method": "legacy_current_universe",
        "source": "data/universe.csv",
        "source_available": True,
        "snapshot_hash": _hash(universe_path),
        "member_count": 2,
    }
    if prospective_freeze is not None:
        freeze_path = root / "docs" / "evidence" / "prospective_freeze.json"
        _write(freeze_path, prospective_freeze)
        universe["prospective_freeze"] = {
            "path": str(freeze_path.relative_to(root)),
            "sha256": freeze_sha256 or _hash(freeze_path),
            "exists": True,
        }
    _write(
        bundle / "sleeve_evaluations.json",
        {
            "envelopes": [
                {
                    "sleeve_id": "caerus_orion",
                    "reason_codes": ["NON_DECISION_GRADE_UNIVERSE"],
                    "evaluation": {"evaluated_at": evaluated_at} if evaluated_at else {},
                    "universe": universe,
                    "opportunity": {
                        "decision_eligible": True,
                        "freshness_status": "VERIFIED",
                    },
                }
            ]
        },
    )


def _prospective_freeze(root: Path, *, effective_from: str = "2026-08-31T00:00:00-04:00") -> dict:
    return build_governed_universe_freeze(
        universe_path=root / "data" / "universe.csv",
        generated_at="2026-08-30T11:30:00+00:00",
        effective_from=effective_from,
        source_revision="fixture-revision",
        no_retroactive_use_before=effective_from[:10],
        freeze_namespace="test-prospective-v1",
    )


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
    _seed_data_control(
        tmp_path,
        trade_date=TRADE_DATE,
        effective_date=EFFECTIVE_DATE,
        prior_date=PRIOR_DATE,
    )
    result = certify_window(repo_root=tmp_path, through_date=TRADE_DATE, sessions=1)
    data = result["sessions"][0]["controls"]["data_freshness_pit_validity"]
    compute = result["sessions"][0]["controls"]["compute_recomputed"]
    assert data["pass"] is False
    assert "prospective_universe_freeze_reference_missing" in data["reasons"]
    assert "pit_universe_not_decision_grade" in data["reasons"]
    assert "non_decision_grade_universe_reason_code" in data["reasons"]
    assert compute["pass"] is True


def test_valid_prospective_freeze_certifies_data_without_rewriting_legacy_label(tmp_path: Path) -> None:
    trade_date = "2026-08-31"
    _seed_data_control(
        tmp_path,
        trade_date=trade_date,
        effective_date="2026-08-28",
        prior_date="2026-08-27",
    )
    freeze = _prospective_freeze(tmp_path)
    _seed_data_control(
        tmp_path,
        trade_date=trade_date,
        effective_date="2026-08-28",
        prior_date="2026-08-27",
        prospective_freeze=freeze,
        evaluated_at="2026-08-31T11:00:00+00:00",
    )

    data = certify_window(
        repo_root=tmp_path, through_date=trade_date, sessions=1
    )["sessions"][0]["controls"]["data_freshness_pit_validity"]

    assert data["pass"] is True
    assert data["reasons"] == []
    assert "docs/evidence/prospective_freeze.json" in data["evidence"]


def test_prospective_freeze_is_never_retroactive(tmp_path: Path) -> None:
    _seed_data_control(
        tmp_path,
        trade_date=TRADE_DATE,
        effective_date=EFFECTIVE_DATE,
        prior_date=PRIOR_DATE,
    )
    freeze = _prospective_freeze(tmp_path)
    _seed_data_control(
        tmp_path,
        trade_date=TRADE_DATE,
        effective_date=EFFECTIVE_DATE,
        prior_date=PRIOR_DATE,
        prospective_freeze=freeze,
        evaluated_at="2026-08-31T11:00:00+00:00",
    )

    data = certify_window(
        repo_root=tmp_path, through_date=TRADE_DATE, sessions=1
    )["sessions"][0]["controls"]["data_freshness_pit_validity"]

    assert data["pass"] is False
    assert "prospective_universe_freeze_not_yet_effective" in data["reasons"]


def test_wrong_or_stale_prospective_freeze_fails_closed(tmp_path: Path) -> None:
    trade_date = "2026-08-31"
    _seed_data_control(
        tmp_path,
        trade_date=trade_date,
        effective_date="2026-08-28",
        prior_date="2026-08-27",
    )
    freeze = _prospective_freeze(tmp_path)
    _seed_data_control(
        tmp_path,
        trade_date=trade_date,
        effective_date="2026-08-28",
        prior_date="2026-08-27",
        prospective_freeze=freeze,
        freeze_sha256="0" * 64,
        evaluated_at="2026-08-31T11:00:00+00:00",
    )
    wrong_hash = certify_window(
        repo_root=tmp_path, through_date=trade_date, sessions=1
    )["sessions"][0]["controls"]["data_freshness_pit_validity"]
    assert wrong_hash["pass"] is False
    assert "prospective_universe_freeze_missing_or_hash_mismatch" in wrong_hash["reasons"]

    stale = _prospective_freeze(
        tmp_path, effective_from="2026-09-01T00:00:00-04:00"
    )
    _seed_data_control(
        tmp_path,
        trade_date=trade_date,
        effective_date="2026-08-28",
        prior_date="2026-08-27",
        prospective_freeze=stale,
        evaluated_at="2026-09-01T11:00:00+00:00",
    )
    stale_result = certify_window(
        repo_root=tmp_path, through_date=trade_date, sessions=1
    )["sessions"][0]["controls"]["data_freshness_pit_validity"]
    assert stale_result["pass"] is False
    assert "prospective_universe_freeze_not_yet_effective" in stale_result["reasons"]
