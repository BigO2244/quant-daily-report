from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from projects.alpha_lab.experiments import run_hyp_2026_015_data_gate as gate


def _write(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, payload: object) -> str:
    return _write(
        path,
        (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    )


def _fixture_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    spec_body = "# HYP-2026-015 test\n\n"
    spec_hash = hashlib.sha256(spec_body.encode()).hexdigest()
    _write(
        tmp_path / gate.SPEC_RELATIVE_PATH,
        (spec_body + "## Freeze record\n- test\n").encode(),
    )
    monkeypatch.setattr(gate, "SPEC_SHA256", spec_hash)

    bundle_root = tmp_path / "outputs/research/alpha_lab/data_spine/sec_original_filings_stream/test"
    source_manifest = {
        "bundle_hash": "bundle-test",
        "metadata": {
            "candidate_count": 2,
            "hydrated_count": 1,
            "acceptance_timestamp_pass_count": 1,
            "error_count": 1,
        },
    }
    monkeypatch.setattr(
        gate,
        "SOURCE_MANIFEST_RELATIVE_PATH",
        str((bundle_root / "manifest.json").relative_to(tmp_path)),
    )
    monkeypatch.setattr(gate, "SOURCE_BUNDLE_SHA256", "bundle-test")
    source_hash = _write_json(bundle_root / "manifest.json", source_manifest)
    monkeypatch.setattr(gate, "SOURCE_MANIFEST_SHA256", source_hash)
    _write_json(
        bundle_root / "data/status/part_00000_status.json",
        {
            "error_count": 1,
            "errors": [
                {
                    "error_type": "RuntimeError",
                    "source_filename": (
                        "edgar/data/1549848/000154984814000068/"
                        "0001549848-14-000068.txt"
                    ),
                }
            ],
        },
    )

    tape_path = tmp_path / "outputs/research/cygnus/tape.jsonl.gz"
    tape_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(tape_path, "wt", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "event_id": "0001549848-14-000068",
                    "issuer_cik": "0001549848",
                    "form_type": "8-K",
                    "acceptance_datetime_utc": "2014-08-11T20:00:00+00:00",
                    "source_document": "https://example.invalid/filing.txt",
                    "source_sha256": None,
                    "event_class": "EARNINGS_RESULTS_8K_ITEM_2_02",
                }
            )
            + "\n"
        )
    earnings_path = tmp_path / gate.EARNINGS_READINESS_RELATIVE_PATH
    earnings_hash = _write_json(
        earnings_path,
        {"data_files": [{"path": str(tape_path.relative_to(tmp_path))}]},
    )
    monkeypatch.setattr(gate, "EARNINGS_READINESS_SHA256", earnings_hash)

    prices_path = tmp_path / gate.PRICES_READINESS_RELATIVE_PATH
    prices_hash = _write_json(
        prices_path,
        {"data_files": [{"path": "not-opened.parquet", "sha256": "panel-test"}]},
    )
    monkeypatch.setattr(gate, "PRICES_READINESS_SHA256", prices_hash)
    monkeypatch.setattr(gate, "PRICES_PANEL_SHA256", "panel-test")
    return tmp_path


def test_gate_stops_at_missing_item_202_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = _fixture_repo(tmp_path, monkeypatch)
    packet = gate.run_gate(
        repo_root=repo_root,
        run_id="test-hyp-015",
        checked_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
    )

    result = packet["result"]
    assert result["outcome"] == "BLOCKED_DATA"
    assert result["source_gate"]["source_coverage"] == "1/2"
    assert result["source_gate"]["item_2_02_discovery_row_count"] == 1
    assert result["source_gate"]["missing_originals_present_in_item_2_02_tape"][0][
        "event_id"
    ] == "0001549848-14-000068"
    assert all(
        item["status"] == "NOT_INSPECTED_SOURCE_GATE_FAILED"
        for item in result["deferred_controls"]
    )
    assert result["return_data_accessed"] is False
    assert result["challenge_period_accessed"] is False
    assert result["trading_behavior_changed"] is False

    run_dir = Path(packet["run_dir"])
    assert (run_dir / "result.json").is_file()
    assert (run_dir / "receipt.json").is_file()
    events = (run_dir / "events.jsonl").read_text().splitlines()
    assert len(events) == 2


def test_gate_is_create_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = _fixture_repo(tmp_path, monkeypatch)
    checked_at = datetime(2026, 8, 30, tzinfo=timezone.utc)
    gate.run_gate(repo_root=repo_root, run_id="same-run", checked_at=checked_at)
    with pytest.raises(FileExistsError):
        gate.run_gate(repo_root=repo_root, run_id="same-run", checked_at=checked_at)


def test_gate_rejects_frozen_manifest_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = _fixture_repo(tmp_path, monkeypatch)
    source_path = repo_root / gate.SOURCE_MANIFEST_RELATIVE_PATH
    source_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="frozen input hash mismatch"):
        gate.run_gate(
            repo_root=repo_root,
            run_id="drift-run",
            checked_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        )
