from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research.security_master_reconciliation import (  # noqa: E402
    SCHEMA_VERSION,
    build_security_master_reconciliation,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_master(root: Path, symbols: list[str], asof: str = "2026-06-02") -> None:
    _write_json(
        root / "data" / "security_master" / "ticker_universe_latest.json",
        {"asof_date": asof, "records": [{"symbol": s} for s in symbols]},
    )


def _write_aliases(root: Path, aliases: dict[str, str]) -> None:
    _write_json(root / "data" / "security_master" / "manual_aliases.json", {"aliases": aliases})


def _write_shadow(root: Path, trade_date: str, holdings: dict[str, list[str]]) -> None:
    strategies = {
        s: {"holdings": [{"ticker": t, "target_weight": 1 / len(syms)} for t in syms]}
        for s, syms in holdings.items()
    }
    _write_json(root / "outputs" / "shadow_candidates" / trade_date / "comparison.json", {
        "trade_date": trade_date, "strategies": strategies,
    })


def test_master_missing_returns_unavailable(tmp_path):
    p = build_security_master_reconciliation(trade_date="2026-06-02", repo_root=tmp_path)
    assert p["available"] is False
    assert "security_master_unavailable" in p["reason_codes"]


def test_all_symbols_in_master_status_ok(tmp_path):
    _write_master(tmp_path, ["AAA", "BBB", "CCC"])
    _write_shadow(tmp_path, "2026-06-02", {"caerus_lyra": ["AAA", "BBB"], "caerus_orion": ["BBB", "CCC"], "caerus_polaris": ["AAA"]})
    p = build_security_master_reconciliation(trade_date="2026-06-02", repo_root=tmp_path)
    assert p["available"] is True
    assert p["unknown_symbols"] == []
    statuses = {row["symbol"]: row["status"] for row in p["symbol_checks"]}
    assert all(s == "ok" for s in statuses.values())


def test_unknown_symbol_is_flagged(tmp_path):
    _write_master(tmp_path, ["AAA", "BBB"])
    _write_shadow(tmp_path, "2026-06-02", {"caerus_lyra": ["AAA", "ZZZ"]})
    p = build_security_master_reconciliation(trade_date="2026-06-02", repo_root=tmp_path)
    assert "ZZZ" in p["unknown_symbols"]
    assert "unknown_symbols_present" in p["reason_codes"]


def test_alias_resolution(tmp_path):
    _write_master(tmp_path, ["BNY"])
    _write_aliases(tmp_path, {"BK": "BNY"})
    _write_shadow(tmp_path, "2026-06-02", {"caerus_lyra": ["BK"]})
    p = build_security_master_reconciliation(trade_date="2026-06-02", repo_root=tmp_path)
    check = next(c for c in p["symbol_checks"] if c["symbol"] == "BK")
    assert check["resolved_symbol"] == "BNY"
    assert check["in_master"] is True


def test_inactive_alias_flagged(tmp_path):
    _write_master(tmp_path, ["AAA"])
    _write_aliases(tmp_path, {"OLD": "MISSING"})
    _write_shadow(tmp_path, "2026-06-02", {"caerus_lyra": ["AAA"]})
    p = build_security_master_reconciliation(trade_date="2026-06-02", repo_root=tmp_path)
    assert any(a["original"] == "OLD" for a in p["inactive_aliases"])
    assert "inactive_aliases_present" in p["reason_codes"]


def test_schema_and_artifacts_written(tmp_path):
    _write_master(tmp_path, ["AAA"])
    _write_shadow(tmp_path, "2026-06-02", {"caerus_polaris": ["AAA"]})
    p = build_security_master_reconciliation(trade_date="2026-06-02", repo_root=tmp_path)
    assert p["schema_version"] == SCHEMA_VERSION
    assert (tmp_path / "outputs" / "research" / "security_master_reconciliation" / "2026-06-02" / "security_master_reconciliation.json").exists()


def test_skips_empty_strategies_comparison(tmp_path):
    """A comparison.json with strategies={} (status/error payload) should
    NOT be selected; the loader should fall back to an older valid one."""
    _write_master(tmp_path, ["AAA", "BBB"])
    _write_shadow(tmp_path, "2026-05-01", {"caerus_lyra": ["AAA"]})
    _write_json(
        tmp_path / "outputs" / "shadow_candidates" / "2026-05-30" / "comparison.json",
        {"trade_date": "2026-05-30", "strategies": {}, "status": "error"},
    )
    p = build_security_master_reconciliation(trade_date="2026-06-02", repo_root=tmp_path)
    assert p["coverage"]["shadow_holdings_date"] == "2026-05-01"
    assert p["coverage"]["holdings_symbol_count"] == 1
