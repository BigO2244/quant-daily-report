from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts import backfill_shadow_artifacts as backfill


def _write_nav(path: Path, dates: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", *backfill.MODEL_SLUGS])
        writer.writeheader()
        for idx, date in enumerate(dates, start=1):
            writer.writerow({"date": date, **{slug: str(float(idx)) for slug in backfill.MODEL_SLUGS}})


def _signals(dates: list[str]) -> pd.DataFrame:
    rows = []
    for date in dates:
        for ticker in ("SPY", "AAPL"):
            rows.append({"date": date, "ticker": ticker, "close": 100.0})
    return pd.DataFrame(rows)


def test_backfill_plan_processes_dates_chronologically(tmp_path: Path) -> None:
    output = tmp_path / "outputs" / "shadow_candidates"
    _write_nav(output / "performance" / "shadow_nav_series.csv", ["2026-04-27"])

    rows = backfill._build_plan(
        output_root=output,
        signals=_signals(["2026-04-28", "2026-04-29", "2026-04-30"]),
        start_date="2026-04-28",
        end_date="2026-04-30",
        anchor_date="2026-04-27",
    )

    assert [row["date"] for row in rows] == ["2026-04-28", "2026-04-29", "2026-04-30"]
    assert all(row["planned_action"] == "refresh_artifacts_and_append_nav" for row in rows)


def test_backfill_dry_run_does_not_create_backup_or_call_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_nav(Path("outputs/shadow_candidates/performance/shadow_nav_series.csv"), ["2026-04-27"])
    monkeypatch.setattr(
        backfill,
        "_load_signals",
        lambda **_: (_signals(["2026-04-28"]), {"coverage": {"end_date": "2026-04-28"}}),
    )

    def fail_refresh(_: list[str]) -> int:
        raise AssertionError("dry-run must not refresh artifacts")

    monkeypatch.setattr(backfill.refresh_shadow_scorecard_artifacts, "main", fail_refresh)

    rc = backfill.main(
        [
            "--start-date",
            "2026-04-28",
            "--end-date",
            "2026-04-28",
            "--anchor-date",
            "2026-04-27",
            "--artifact-only",
            "--dry-run",
            "--run-id",
            "test-run",
        ]
    )

    assert rc == 0
    assert not Path("outputs/recovery_backups/test-run").exists()
    assert next(Path("outputs/diagnostics").glob("shadow_backfill_plan_*.csv")).exists()


def test_backfill_strict_refuses_missing_anchor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    Path("outputs/shadow_candidates/performance").mkdir(parents=True)
    monkeypatch.setattr(
        backfill,
        "_load_signals",
        lambda **_: (_signals(["2026-04-28"]), {"coverage": {"end_date": "2026-04-28"}}),
    )

    with pytest.raises(SystemExit, match="anchor date 2026-04-27"):
        backfill.main(
            [
                "--start-date",
                "2026-04-28",
                "--end-date",
                "2026-04-28",
                "--anchor-date",
                "2026-04-27",
                "--artifact-only",
                "--strict",
                "--run-id",
                "test-run",
            ]
        )


def test_backfill_backup_manifest_hashes_existing_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    output = Path("outputs/shadow_candidates")
    dated = output / "2026-04-28"
    dated.mkdir(parents=True)
    (dated / "shadow_performance.json").write_text('{"status": "NO_PRIOR"}', encoding="utf-8")
    _write_nav(output / "performance" / "shadow_nav_series.csv", ["2026-04-27"])

    backup_dir, manifest = backfill._create_backup(
        backup_root=Path("outputs/recovery_backups"),
        run_id="test-run",
        output_root=output,
        affected_dates=["2026-04-28"],
        cache_path=Path("outputs/research/flow_detection_v1/price_panel.parquet"),
        price_cache_max_date="2026-05-11",
        anchor_date="2026-04-27",
    )

    assert backup_dir == Path("outputs/recovery_backups/test-run")
    manifest_path = backup_dir / "manifest.json"
    assert manifest_path.exists()
    written = json.loads(manifest_path.read_text())
    assert "outputs/shadow_candidates/2026-04-28/shadow_performance.json" in written["files_backed_up"]
    assert written["last_valid_anchor_date"] == "2026-04-27"
