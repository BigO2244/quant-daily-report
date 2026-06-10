from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.backfill_portfolio_history import (
    flag_large_moves,
    reconcile,
    reconstruct_nav_series,
    run_backfill,
)

FIXED_GENERATED_AT = "2026-06-10T00:00:00+00:00"


def _ts(date_str: str) -> int:
    if "T" not in date_str:
        # Alpaca's daily portfolio-history timestamp for a market session is
        # after the New York close, which may be the following UTC date.
        return int(datetime.fromisoformat(f"{date_str}T20:00:00-05:00").timestamp())
    return int(datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc).timestamp())


def _history(*pairs: tuple[str, float]) -> dict:
    return {
        "timestamp": [_ts(d) for d, _ in pairs],
        "equity": [e for _, e in pairs],
    }


def _seed_existing_nav(repo: Path, *rows: tuple[str, float]) -> None:
    out = repo / "outputs" / "portfolio_history"
    out.mkdir(parents=True, exist_ok=True)
    lines = ["date,equity,cash,gross_exposure,net_exposure,return_1d,turnover_dollars,turnover_pct,cumulative_return,source"]
    for date, equity in rows:
        lines.append(f"{date},{equity},,,,,,,,outputs/perf/live_overlay_nav_series.csv")
    (out / "nav.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_reconstruct_nav_series_orders_and_derives_returns() -> None:
    rows = reconstruct_nav_series(
        _history(("2026-03-04", 10000.0), ("2026-03-03", 10000.0), ("2026-03-05", 10100.0)),
        inception="2026-03-03",
    )
    assert [r["date"] for r in rows] == ["2026-03-03", "2026-03-04", "2026-03-05"]
    assert rows[0]["return_1d"] is None
    assert rows[2]["return_1d"] == pytest.approx((10100.0 / 10000.0) - 1.0)
    assert rows[2]["cumulative_return"] == pytest.approx((10100.0 / 10000.0) - 1.0)
    assert rows[0]["source"] == "alpaca_portfolio_history_backfill"


def test_reconstruct_uses_new_york_session_date_for_after_close_timestamps() -> None:
    # Alpaca daily portfolio-history bars can be timestamped after market close
    # in New York, which is the following UTC calendar date. The row belongs to
    # the market session date, not the UTC date.
    rows = reconstruct_nav_series(
        {"timestamp": [_ts("2026-03-04T01:00:00")], "equity": [10000.0]},
        inception="2026-03-03",
    )

    assert rows[0]["date"] == "2026-03-03"


def test_reconstruct_drops_pre_inception_and_nonpositive() -> None:
    rows = reconstruct_nav_series(
        _history(("2026-02-28", 9999.0), ("2026-03-03", 10000.0), ("2026-03-04", 0.0)),
        inception="2026-03-03",
    )
    assert [r["date"] for r in rows] == ["2026-03-03"]


def test_reconcile_flags_only_beyond_one_bp() -> None:
    series = [{"date": "2026-03-03", "equity": 10000.0}, {"date": "2026-03-04", "equity": 9999.0}]
    # within 1bp on 03-03 (0.5bp), beyond on 03-04 (~10bp)
    result = reconcile(series, {"2026-03-03": 10000.5, "2026-03-04": 9989.0}, label="nav.csv")
    assert result["compared"] == 2
    assert result["matches"] == 1
    assert len(result["discrepancies"]) == 1
    assert result["discrepancies"][0]["date"] == "2026-03-04"


def test_flag_large_moves() -> None:
    series = [
        {"date": "2026-03-03", "return_1d": None},
        {"date": "2026-03-04", "return_1d": 0.02},
        {"date": "2026-03-05", "return_1d": -0.061},
    ]
    flags = flag_large_moves(series)
    assert [f["date"] for f in flags] == ["2026-03-05"]


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    _seed_existing_nav(tmp_path, ("2026-03-03", 10000.0), ("2026-03-04", 10000.0))

    def fake_fetch(*, date_end: str, inception: str) -> dict:
        return _history(("2026-03-03", 10000.0), ("2026-03-04", 10000.0))

    manifest = run_backfill(
        repo_root=tmp_path,
        date_end="2026-03-04",
        write=False,
        fetch_fn=fake_fetch,
        generated_at=FIXED_GENERATED_AT,
    )
    assert manifest["mode"] == "dry_run"
    assert manifest["reconciliation"]["reconciled_clean"] is True
    assert not (tmp_path / "outputs" / "portfolio_history" / "backfill_manifest.json").exists()
    # existing nav.csv untouched (still has the live_overlay source)
    nav_text = (tmp_path / "outputs" / "portfolio_history" / "nav.csv").read_text(encoding="utf-8")
    assert "live_overlay_nav_series" in nav_text


def test_write_mode_backs_up_and_logs_restatement_on_discrepancy(tmp_path: Path) -> None:
    # Existing 03-04 equity disagrees with the broker by ~3% -> restatement.
    _seed_existing_nav(tmp_path, ("2026-03-03", 10000.0), ("2026-03-04", 9700.0))

    def fake_fetch(*, date_end: str, inception: str) -> dict:
        return _history(("2026-03-03", 10000.0), ("2026-03-04", 10000.0))

    manifest = run_backfill(
        repo_root=tmp_path,
        date_end="2026-03-04",
        write=True,
        fetch_fn=fake_fetch,
        generated_at=FIXED_GENERATED_AT,
    )
    out = tmp_path / "outputs" / "portfolio_history"
    assert manifest["mode"] == "write"
    assert (out / "backfill_manifest.json").exists()
    assert (out / "nav.csv.pre_backfill.bak").exists()  # prior preserved
    restatements = json.loads((out / "restatements.json").read_text(encoding="utf-8"))
    assert len(restatements) == 1
    assert restatements[0]["date"] == "2026-03-04"
    assert restatements[0]["old_value"] == 9700.0
    assert restatements[0]["new_value"] == 10000.0
    # nav.csv now broker-authoritative
    nav_text = (out / "nav.csv").read_text(encoding="utf-8")
    assert "alpaca_portfolio_history_backfill" in nav_text


def test_one_time_guard_refuses_second_write(tmp_path: Path) -> None:
    _seed_existing_nav(tmp_path, ("2026-03-03", 10000.0))

    def fake_fetch(*, date_end: str, inception: str) -> dict:
        return _history(("2026-03-03", 10000.0))

    run_backfill(repo_root=tmp_path, date_end="2026-03-03", write=True,
                 fetch_fn=fake_fetch, generated_at=FIXED_GENERATED_AT)
    with pytest.raises(SystemExit):
        run_backfill(repo_root=tmp_path, date_end="2026-03-03", write=True,
                     fetch_fn=fake_fetch, generated_at=FIXED_GENERATED_AT)
    # --force overrides
    manifest = run_backfill(repo_root=tmp_path, date_end="2026-03-03", write=True, force=True,
                            fetch_fn=fake_fetch, generated_at=FIXED_GENERATED_AT)
    assert manifest["mode"] == "write"
