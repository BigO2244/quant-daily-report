from __future__ import annotations

import csv
import json
from pathlib import Path

from research_registry.research.portfolio_history_freshness import build_portfolio_history_freshness


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_portfolio_history_freshness_ready_with_fresh_history(tmp_path: Path) -> None:
    _write_json(tmp_path / "outputs" / "portfolio_history" / "summary.json", {"as_of_date": "2026-06-08", "counts": {}})
    _write_csv(tmp_path / "outputs" / "portfolio_history" / "nav.csv", [{"date": "2026-06-08", "equity": "100"}])

    payload = build_portfolio_history_freshness(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["freshness_status"] == "READY"
    assert payload["latest_portfolio_history_date"] == "2026-06-08"
    assert payload["downstream_impact"]["tournament_confidence"] == "HIGH"
    assert (tmp_path / "outputs" / "model_quality" / "2026-06-08" / "portfolio_history_freshness.json").exists()


def test_portfolio_history_freshness_stale_history(tmp_path: Path) -> None:
    _write_json(tmp_path / "outputs" / "portfolio_history" / "summary.json", {"as_of_date": "2026-04-09", "counts": {}})
    _write_csv(tmp_path / "outputs" / "portfolio_history" / "nav.csv", [{"date": "2026-04-09", "equity": "100"}])

    payload = build_portfolio_history_freshness(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["freshness_status"] == "STALE"
    assert "PORTFOLIO_HISTORY_STALE" in payload["reason_codes"]
    assert payload["downstream_impact"]["promotion_readiness_confidence"] == "LOW"


def test_portfolio_history_freshness_missing_history(tmp_path: Path) -> None:
    payload = build_portfolio_history_freshness(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["freshness_status"] == "MISSING"
    assert "PORTFOLIO_HISTORY_MISSING" in payload["reason_codes"]
    assert payload["downstream_impact"]["operational_drag_confidence"] == "UNAVAILABLE"


def test_portfolio_history_freshness_malformed_history(tmp_path: Path) -> None:
    summary = tmp_path / "outputs" / "portfolio_history" / "summary.json"
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text("{not-json", encoding="utf-8")

    payload = build_portfolio_history_freshness(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["freshness_status"] == "UNKNOWN"
    assert "SUMMARY_PARSE_ERROR" in payload["reason_codes"]


def test_portfolio_history_freshness_auth_failure_diagnostic(tmp_path: Path) -> None:
    payload = build_portfolio_history_freshness(
        trade_date="2026-06-08",
        repo_root=tmp_path,
        broker_auth_status="AUTH_FAILED",
    )

    assert payload["freshness_status"] == "AUTH_FAILED"
    assert "BROKER_HISTORY_UNAVAILABLE" in payload["reason_codes"]


def test_portfolio_history_freshness_deterministic_output(tmp_path: Path) -> None:
    _write_json(tmp_path / "outputs" / "portfolio_history" / "summary.json", {"as_of_date": "2026-06-08", "counts": {}})
    _write_csv(tmp_path / "outputs" / "portfolio_history" / "transactions.csv", [{"filled_at": "2026-06-08T14:00:00Z", "ticker": "AAA"}])
    _write_csv(tmp_path / "outputs" / "portfolio_history" / "nav.csv", [{"date": "2026-06-08", "equity": "100"}])

    first = build_portfolio_history_freshness(trade_date="2026-06-08", repo_root=tmp_path, write=False)
    second = build_portfolio_history_freshness(trade_date="2026-06-08", repo_root=tmp_path, write=False)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
