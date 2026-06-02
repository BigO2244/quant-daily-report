from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research.concentration_diagnostic import (  # noqa: E402
    CLASSIFY_CLEAN,
    CLASSIFY_CONFIGURATION,
    CLASSIFY_TEMPORARY,
    CLASSIFY_VIOLATION,
    SCHEMA_VERSION,
    build_concentration_diagnostic,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_risk(root: Path, trade_date: str, strategies: dict[str, dict]) -> None:
    _write_json(root / "outputs" / "research" / "risk_coverage" / trade_date / "risk_coverage.json", {
        "available": True, "strategies": {
            name: {**{"available": True, "strategy": name}, **row} for name, row in strategies.items()
        },
    })


def test_clean_strategy(tmp_path):
    _write_risk(tmp_path, "2026-06-02", {
        "caerus_polaris": {"position_count": 20, "max_single_name_weight": 0.05, "top3_concentration": 0.15, "top5_concentration": 0.25, "top10_concentration": 0.50, "sector_concentration": 0.30},
    })
    p = build_concentration_diagnostic(trade_date="2026-06-02", repo_root=tmp_path)
    assert p["strategies"][0]["classification"] == CLASSIFY_CLEAN


def test_designed_by_construction(tmp_path):
    _write_risk(tmp_path, "2026-06-02", {
        "caerus_lyra": {"position_count": 5, "max_single_name_weight": 0.20, "top3_concentration": 0.60, "top5_concentration": 1.0, "top10_concentration": 1.0, "sector_concentration": 0.40},
    })
    p = build_concentration_diagnostic(trade_date="2026-06-02", repo_root=tmp_path)
    assert p["strategies"][0]["classification"] == CLASSIFY_CONFIGURATION


def test_actual_violation_when_above_floor(tmp_path):
    _write_risk(tmp_path, "2026-06-02", {
        "caerus_lyra": {"position_count": 10, "max_single_name_weight": 0.30, "top3_concentration": 0.55, "top5_concentration": 0.70, "top10_concentration": 1.0, "sector_concentration": 0.40},
    })
    p = build_concentration_diagnostic(trade_date="2026-06-02", repo_root=tmp_path)
    assert p["strategies"][0]["classification"] == CLASSIFY_VIOLATION


def test_temporary_violation_small_excess(tmp_path):
    _write_risk(tmp_path, "2026-06-02", {
        "caerus_lyra": {"position_count": 12, "max_single_name_weight": 0.12, "top3_concentration": 0.30, "top5_concentration": 0.50, "top10_concentration": 0.85, "sector_concentration": 0.30},
    })
    p = build_concentration_diagnostic(trade_date="2026-06-02", repo_root=tmp_path)
    assert p["strategies"][0]["classification"] == CLASSIFY_TEMPORARY


def test_missing_risk_coverage(tmp_path):
    p = build_concentration_diagnostic(trade_date="2026-06-02", repo_root=tmp_path)
    assert p["available"] is False
    assert "missing_risk_coverage" in p["reason_codes"]


def test_schema_and_artifacts(tmp_path):
    _write_risk(tmp_path, "2026-06-02", {"caerus_polaris": {"position_count": 10, "max_single_name_weight": 0.10, "top3_concentration": 0.30, "top5_concentration": 0.50, "top10_concentration": 1.0, "sector_concentration": 0.30}})
    p = build_concentration_diagnostic(trade_date="2026-06-02", repo_root=tmp_path)
    assert p["schema_version"] == SCHEMA_VERSION
    assert (tmp_path / "outputs" / "research" / "concentration_diagnostic" / "2026-06-02" / "concentration_diagnostic.json").exists()
