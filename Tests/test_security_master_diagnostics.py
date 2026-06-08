from __future__ import annotations

import csv
import json
from pathlib import Path

from research_registry.research.security_master_diagnostics import build_security_master_diagnostics


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_security_master_diagnostics_missing_env_credentials(tmp_path: Path) -> None:
    payload = build_security_master_diagnostics(
        trade_date="2026-06-08",
        repo_root=tmp_path,
        check_live=True,
        env={},
    )

    assert payload["refresh_diagnostic"]["auth_status"] == "MISSING_CREDENTIALS"
    assert "MISSING_ALPACA_CREDENTIALS" in payload["reason_codes"]


def test_security_master_diagnostics_classifies_401_without_secret_leakage(tmp_path: Path) -> None:
    env = {"ALPACA_API_KEY_ID": "KEY123456", "ALPACA_API_SECRET_KEY": "SECRET123456", "ALPACA_PAPER": "1"}

    def _probe() -> list[dict]:
        raise RuntimeError("401 Unauthorized for SECRET123456")

    payload = build_security_master_diagnostics(
        trade_date="2026-06-08",
        repo_root=tmp_path,
        check_live=True,
        env=env,
        alpaca_probe=_probe,
    )
    text = json.dumps(payload, sort_keys=True)

    assert payload["refresh_diagnostic"]["auth_status"] == "UNAUTHORIZED"
    assert "ALPACA_401_UNAUTHORIZED" in payload["reason_codes"]
    assert "SECRET123456" not in text
    assert "KEY123456" not in text


def test_security_master_diagnostics_stale_artifact(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "data" / "security_master" / "ticker_universe_latest.json",
        {"asof_date": "2026-06-01", "symbols": [{"symbol": "AAPL"}]},
    )

    payload = build_security_master_diagnostics(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["security_master_artifact"]["status"] == "STALE"
    assert "SECURITY_MASTER_STALE" in payload["reason_codes"]


def test_security_master_diagnostics_alias_exception_and_backlog(tmp_path: Path) -> None:
    _write_csv(tmp_path / "data" / "universe.csv", [{"ticker": "BK", "sector": "Financials"}])
    _write_json(tmp_path / "data" / "security_master" / "manual_aliases.json", {"aliases": {"BK": "BNY"}})
    _write_json(tmp_path / "data" / "ticker_exceptions.json", {"aliases": {"BK": "BNY"}, "ignore": []})

    payload = build_security_master_diagnostics(trade_date="2026-06-08", repo_root=tmp_path)
    bk = payload["alias_governance"]["bk_bny"]

    assert bk["price_provider_exception_configured"] is True
    assert bk["universe_migration_backlog"] is True
    assert bk["execution_blocker_if_unresolved"] is False
    assert "BK_BNY_UNIVERSE_MIGRATION_BACKLOG" in payload["reason_codes"]
