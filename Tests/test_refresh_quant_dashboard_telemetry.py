from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import scripts.refresh_quant_dashboard as rqd

REPORT_DATE = "2026-06-04"


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


# --- failure classification -------------------------------------------------

def test_classify_broker_failure_auth():
    assert rqd._classify_broker_failure(RuntimeError('get_account failed: {"message": "unauthorized."}')) == "alpaca_auth_failed"
    assert rqd._classify_broker_failure(RuntimeError("HTTP 401")) == "alpaca_auth_failed"
    assert rqd._classify_broker_failure(RuntimeError("403 forbidden")) == "alpaca_auth_failed"


def test_classify_broker_failure_generic():
    assert rqd._classify_broker_failure(RuntimeError("connection timed out")) == "live_broker_refresh_failed"


# --- stale artifact evaluation ---------------------------------------------

def _fresh_artifacts(root: Path, date: str) -> None:
    _write_csv(
        root / "outputs" / "perf" / "live_overlay_nav_series.csv",
        [{"date": "2026-06-01", "equity": 10000.0}, {"date": date, "equity": 10100.0}],
    )
    _write_json(root / "outputs" / "broker_snapshot" / f"broker_snapshot_{date}.json", {"trade_date": date})
    _write_json(root / "outputs" / "broker" / f"recon_posttrade_{date}.json", {"trade_date": date})


def test_staleness_clean_when_artifacts_current(tmp_path: Path):
    root = tmp_path / "repo"
    _fresh_artifacts(root, REPORT_DATE)

    out = rqd.evaluate_live_telemetry_staleness(repo_root=root, report_date=REPORT_DATE)

    assert out["reason_codes"] == []
    assert out["nav_latest_date"] == REPORT_DATE
    assert out["broker_snapshot_latest_date"] == REPORT_DATE
    assert out["recon_latest_date"] == REPORT_DATE


def test_staleness_flags_missing_artifacts(tmp_path: Path):
    root = tmp_path / "empty"
    root.mkdir()

    out = rqd.evaluate_live_telemetry_staleness(repo_root=root, report_date=REPORT_DATE)

    assert set(out["reason_codes"]) == {"nav_artifact_stale", "broker_snapshot_stale", "recon_artifact_stale"}


def test_staleness_flags_old_nav_beyond_tolerance(tmp_path: Path):
    root = tmp_path / "repo"
    _fresh_artifacts(root, REPORT_DATE)
    # Overwrite NAV so its latest date is 2026-05-20 (well beyond the 4-day tolerance).
    _write_csv(
        root / "outputs" / "perf" / "live_overlay_nav_series.csv",
        [{"date": "2026-05-20", "equity": 10000.0}],
    )

    out = rqd.evaluate_live_telemetry_staleness(repo_root=root, report_date=REPORT_DATE, max_age_days=4)

    assert "nav_artifact_stale" in out["reason_codes"]
    assert "broker_snapshot_stale" not in out["reason_codes"]
    assert out["nav_latest_date"] == "2026-05-20"


def test_staleness_tolerates_weekend_gap(tmp_path: Path):
    root = tmp_path / "repo"
    _fresh_artifacts(root, "2026-06-01")  # 3 calendar days before report date

    out = rqd.evaluate_live_telemetry_staleness(repo_root=root, report_date="2026-06-04", max_age_days=4)

    assert out["reason_codes"] == []


def test_staleness_accepts_canonical_run_reconciliation(tmp_path: Path):
    root = tmp_path / "repo"
    _fresh_artifacts(root, REPORT_DATE)
    for legacy in (root / "outputs" / "broker").glob("recon_posttrade_*.json"):
        legacy.unlink()
    run_root = root / "outputs" / "paper_lane" / "runs" / "run-current"
    _write_json(
        root / "outputs" / "workflow" / REPORT_DATE / "execution.json",
        {
            "trade_date": REPORT_DATE,
            "status": "success",
            "run_root": str(run_root),
        },
    )
    _write_json(run_root / "live_pilot_reconciliation.json", {"status": "CLEAN"})

    out = rqd.evaluate_live_telemetry_staleness(
        repo_root=root, report_date=REPORT_DATE
    )

    assert "recon_artifact_stale" not in out["reason_codes"]
    assert out["recon_latest_date"] == REPORT_DATE


# --- main() exit behavior ---------------------------------------------------

def _patch_common(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(rqd, "_resolve_report_date", lambda *_a, **_k: REPORT_DATE)


def test_main_require_live_broker_exits_nonzero_on_auth_failure(tmp_path, monkeypatch, capsys):
    _patch_common(monkeypatch, tmp_path)

    def _raise(**_kwargs):
        raise RuntimeError('Alpaca get_account failed: {"message": "unauthorized."}')

    rebuilt = {"called": False}
    monkeypatch.setattr(rqd, "refresh_live_broker_artifacts", _raise)
    monkeypatch.setattr(rqd, "rebuild_dashboard", lambda **_k: rebuilt.update(called=True) or {})
    monkeypatch.setattr(sys, "argv", ["refresh_quant_dashboard.py", "--repo-root", str(tmp_path), "--require-live-broker"])

    rc = rqd.main()
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["live_status"]["status"] == "failed"
    assert "alpaca_auth_failed" in payload["live_status"]["reason_codes"]
    assert "live_broker_required_failed" in payload["live_status"]["reason_codes"]
    assert rebuilt["called"] is False  # fail-fast: dashboard not rebuilt on hard failure
    health = json.loads(
        (
            tmp_path
            / "outputs"
            / "health"
            / "caerus_dashboard_refresh"
            / "latest"
            / "refresh_status.json"
        ).read_text()
    )
    assert health["status"] == "FAILED"
    assert health["exit_code"] == 1


def test_main_default_swallows_failure_but_surfaces_status(tmp_path, monkeypatch, capsys):
    _patch_common(monkeypatch, tmp_path)

    def _raise(**_kwargs):
        raise RuntimeError('Alpaca get_account failed: {"message": "unauthorized."}')

    rebuilt = {"called": False}
    monkeypatch.setattr(rqd, "refresh_live_broker_artifacts", _raise)
    monkeypatch.setattr(rqd, "rebuild_dashboard", lambda **_k: rebuilt.update(called=True) or {})
    monkeypatch.setattr(sys, "argv", ["refresh_quant_dashboard.py", "--repo-root", str(tmp_path)])

    rc = rqd.main()
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0  # default stays resilient (dashboard still rebuilds)
    assert payload["live_status"]["status"] == "failed"
    assert "alpaca_auth_failed" in payload["live_status"]["reason_codes"]
    assert rebuilt["called"] is True


def test_dashboard_publish_exception_writes_failed_service_health(
    tmp_path, monkeypatch, capsys
):
    _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rqd,
        "refresh_live_broker_artifacts",
        lambda **_k: {"report_date": REPORT_DATE},
    )
    monkeypatch.setattr(
        rqd,
        "rebuild_dashboard",
        lambda **_k: (_ for _ in ()).throw(RuntimeError("disk full")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["refresh_quant_dashboard.py", "--repo-root", str(tmp_path)],
    )

    rc = rqd.main()
    payload = json.loads(capsys.readouterr().out)
    health = json.loads(
        (
            tmp_path
            / "outputs"
            / "health"
            / "caerus_dashboard_refresh"
            / "latest"
            / "refresh_status.json"
        ).read_text()
    )

    assert rc == 1
    assert payload["dashboard"] is None
    assert health["status"] == "FAILED"
    assert "dashboard_publish_missing" in health["reason_codes"]
