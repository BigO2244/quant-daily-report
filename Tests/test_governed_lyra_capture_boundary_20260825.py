from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import run_governed_lyra_capture_20260825 as subject
from Tests.test_generic_lyra_v2_producer import _path_sources


ROOT = Path(__file__).resolve().parents[1]


def _config(paths: dict) -> dict[str, str]:
    return {
        "CAERUS_GOVERNED_LYRA_CAPTURE_ENABLED": "1",
        "CAERUS_LYRA_CAPTURE_SOURCE_SESSION_MANIFEST": str(
            paths["source_session_manifest_path"]
        ),
        "CAERUS_LYRA_CAPTURE_EVALUATION_BATCH": str(
            paths["evaluation_batch_path"]
        ),
        "CAERUS_LYRA_CAPTURE_LEGACY_DECISION_BATCH": str(
            paths["legacy_decision_batch_path"]
        ),
        "CAERUS_LYRA_CAPTURE_CURRENT_SOURCE": str(paths["lyra_source_path"]),
        "CAERUS_LYRA_CAPTURE_PRIOR_SOURCE": str(
            paths["prior_lyra_source_path"]
        ),
        "CAERUS_LYRA_CAPTURE_UNIVERSE_FREEZE": str(
            paths["universe_freeze_path"]
        ),
        "CAERUS_LYRA_CAPTURE_UNIVERSE": str(paths["universe_path"]),
        "CAERUS_LYRA_CAPTURE_RISK_POLICY": str(
            paths["forecast_risk_policy_path"]
        ),
        "CAERUS_LYRA_CAPTURE_RISK_POLICY_PROPOSAL": str(
            paths["forecast_risk_policy_proposal_path"]
        ),
        "CAERUS_LYRA_CAPTURE_RISK_POLICY_OWNER_DECISION": str(
            paths["forecast_risk_policy_owner_decision_path"]
        ),
        "CAERUS_LYRA_CAPTURE_LIVE_OWNER_DECISION": str(
            paths["live_owner_decision_path"]
        ),
        "CAERUS_LYRA_CAPTURE_PRICE_PANEL": str(paths["price_panel_path"]),
        "CAERUS_LYRA_CAPTURE_OUTPUT_ROOT": str(paths["output_root"]),
    }


def test_disabled_default_exits_cleanly_without_input_read_or_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(**kwargs):
        raise AssertionError("disabled boundary must not call capture")

    monkeypatch.setattr(subject, "capture_from_explicit_paths", forbidden)
    result = subject.run_governed_lyra_capture_boundary(
        config={"CAERUS_GOVERNED_LYRA_CAPTURE_ENABLED": "0"},
        now=dt.datetime(2026, 8, 25, 12, 15, tzinfo=dt.timezone.utc),
    )
    assert result["status"] == "DISABLED_NO_WRITE"
    assert result["input_read_performed"] is False
    assert result["write_performed"] is False
    assert result["broker_call_performed"] is False
    assert result["submission_allowed"] is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "now",
    [
        dt.datetime(2026, 8, 24, 12, 15, tzinfo=dt.timezone.utc),
        dt.datetime(2026, 8, 25, 12, 14, 59, tzinfo=dt.timezone.utc),
        dt.datetime(2027, 8, 25, 12, 15, tzinfo=dt.timezone.utc),
    ],
)
def test_enabled_capture_is_exact_date_and_time_bound(
    tmp_path: Path, now: dt.datetime,
) -> None:
    paths, _ = _path_sources(tmp_path)
    with pytest.raises(subject.GovernedLyraCaptureBoundaryError, match="only on"):
        subject.run_governed_lyra_capture_boundary(
            config=_config(paths), now=now,
        )
    assert not paths["output_root"].exists()


def test_enabled_boundary_writes_only_idempotent_advisory_artifacts(
    tmp_path: Path,
) -> None:
    paths, rows = _path_sources(tmp_path)

    def loader(path, *, symbols, data_as_of):
        assert data_as_of == "2026-08-24"
        return [row for row in rows if row["ticker"] in set(symbols)]

    now = dt.datetime(2026, 8, 25, 12, 15, tzinfo=dt.timezone.utc)
    first = subject.run_governed_lyra_capture_boundary(
        config=_config(paths), now=now, price_row_loader=loader,
    )
    second = subject.run_governed_lyra_capture_boundary(
        config=_config(paths), now=now, price_row_loader=loader,
    )
    assert first == second
    assert first["status"] == "CAPTURED_IMMUTABLE_ADVISORY_NO_SUBMIT"
    assert first["execution_session"] == "2026-08-25"
    assert first["signal_as_of"] == "2026-08-24"
    assert len(first["persisted_paths"]) == 12
    assert all(Path(path).is_file() for path in first["persisted_paths"])
    assert first["broker_call_performed"] is False
    assert first["broker_write_performed"] is False
    assert first["submission_allowed"] is False
    assert first["activation_authority"] is False
    assert first["execution_authority"] is False


def test_enabled_config_rejects_placeholders_and_command_syntax(
    tmp_path: Path,
) -> None:
    paths, _ = _path_sources(tmp_path)
    config = _config(paths)
    config["CAERUS_LYRA_CAPTURE_RISK_POLICY"] = (
        "REPLACE_WITH_ABSOLUTE_APPROVED_POLICY_PATH"
    )
    with pytest.raises(subject.GovernedLyraCaptureBoundaryError, match="unresolved"):
        subject.run_governed_lyra_capture_boundary(
            config=config,
            now=dt.datetime(2026, 8, 25, 12, 15, tzinfo=dt.timezone.utc),
        )

    config_path = tmp_path / "bad.env"
    config_path.write_text(
        "CAERUS_GOVERNED_LYRA_CAPTURE_ENABLED=$(touch /tmp/forbidden)\n"
    )
    with pytest.raises(subject.GovernedLyraCaptureBoundaryError, match="not allowed"):
        subject.read_literal_config(config_path)


def test_template_and_wrapper_are_inert_and_credential_free(tmp_path: Path) -> None:
    template = (
        ROOT / "config/templates/governed_lyra_capture_20260825.env.example"
    ).read_text()
    python_wrapper = (
        ROOT / "scripts/run_governed_lyra_capture_20260825.py"
    ).read_text()
    shell_wrapper = (
        ROOT / "scripts/cron_governed_lyra_capture_20260825.sh"
    ).read_text()
    assert "CAERUS_GOVERNED_LYRA_CAPTURE_ENABLED=0" in template
    assert "APCA_API" not in template + python_wrapper + shell_wrapper
    assert "ALPACA" not in template + python_wrapper + shell_wrapper
    assert "brokers" not in python_wrapper
    assert "generic_live_v1_submission" not in python_wrapper
    assert "crontab" not in shell_wrapper

    config = tmp_path / "disabled.env"
    config.write_text("CAERUS_GOVERNED_LYRA_CAPTURE_ENABLED=0\n")
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/run_governed_lyra_capture_20260825.py"),
         "--config", str(config)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["status"] == "DISABLED_NO_WRITE"
