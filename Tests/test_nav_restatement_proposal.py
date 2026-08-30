from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from scripts.build_nav_restatement_proposal import build_proposal
from scripts.build_portfolio_history import NAV_FIELDS


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _seed(repo: Path) -> tuple[Path, Path]:
    base = repo / "outputs" / "portfolio_history" / "nav.csv"
    source = repo / "outputs" / "ledger" / "paper" / "daily_nav.csv"
    base_rows = []
    for date, equity, cash, source_name in [
        ("2026-06-08", 100.0, 20.0, "legacy"),
        ("2026-06-09", 101.0, 21.0, "legacy"),
        ("2026-06-10", 102.0, 22.0, "legacy"),
    ]:
        row = {field: "" for field in NAV_FIELDS}
        row.update({"date": date, "equity": equity, "cash": cash, "source": source_name})
        base_rows.append(row)
    _write_csv(base, NAV_FIELDS, base_rows)
    _write_csv(
        source,
        ["date", "equity", "source"],
        [
            {"date": "2026-06-08", "equity": 100.0, "source": "broker"},
            {"date": "2026-06-09", "equity": 102.0, "source": "broker"},
            {"date": "2026-06-10", "equity": 103.0, "source": "broker"},
        ],
    )
    return base, source


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_dry_run_is_non_mutating_and_reports_hash_bound_conflicts(tmp_path: Path) -> None:
    base, _ = _seed(tmp_path)
    before = base.read_bytes()

    manifest = build_proposal(repo_root=tmp_path, generated_at="2026-08-30T12:00:00+00:00")

    assert manifest["conflict_count"] == 2
    assert manifest["conflict_dates"] == ["2026-06-09", "2026-06-10"]
    assert manifest["mode"] == "dry_run"
    assert manifest["authority"] == "PROPOSAL_ONLY"
    assert manifest["consumer_switch_authorized"] is False
    assert base.read_bytes() == before
    assert not (tmp_path / "outputs" / "portfolio_history" / "restatement_proposals").exists()


def test_write_requires_both_reviewed_input_hashes(tmp_path: Path) -> None:
    _seed(tmp_path)
    with pytest.raises(RuntimeError, match="requires both reviewed"):
        build_proposal(repo_root=tmp_path, write_proposal=True)


def test_hash_mismatch_fails_closed_without_writes(tmp_path: Path) -> None:
    _seed(tmp_path)
    dry_run = build_proposal(repo_root=tmp_path)
    with pytest.raises(RuntimeError, match="base NAV hash changed"):
        build_proposal(
            repo_root=tmp_path,
            write_proposal=True,
            expected_base_sha256="0" * 64,
            expected_source_sha256=dry_run["source"]["sha256"],
        )
    assert not (tmp_path / "outputs" / "portfolio_history" / "restatement_proposals").exists()


def test_write_creates_immutable_projection_and_never_changes_base(tmp_path: Path) -> None:
    base, _ = _seed(tmp_path)
    before = base.read_bytes()
    dry_run = build_proposal(repo_root=tmp_path)

    manifest = build_proposal(
        repo_root=tmp_path,
        generated_at="2026-08-30T12:00:00+00:00",
        write_proposal=True,
        expected_base_sha256=dry_run["base"]["sha256"],
        expected_source_sha256=dry_run["source"]["sha256"],
    )

    proposal = Path(manifest["proposal_directory"])
    assert base.read_bytes() == before
    assert (proposal / "manifest.json").is_file()
    assert (proposal / "dispositions.jsonl").is_file()
    projection = proposal / "nav_as_restated.csv"
    assert _hash(projection) == manifest["projection_sha256"]
    records = [json.loads(line) for line in (proposal / "dispositions.jsonl").read_text().splitlines()]
    assert len(records) == 2
    assert {record["status"] for record in records} == {"PROPOSED_NOT_ACCEPTED"}
    with projection.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[1]["equity"] == "102.0"
    assert rows[1]["cash"] == "21.0"
    assert float(rows[1]["return_1d"]) == pytest.approx(0.02)
    assert rows[1]["source"] == "restatement_proposal:broker"

    repeated = build_proposal(
        repo_root=tmp_path,
        generated_at="2026-08-31T12:00:00+00:00",
        write_proposal=True,
        expected_base_sha256=dry_run["base"]["sha256"],
        expected_source_sha256=dry_run["source"]["sha256"],
    )
    assert repeated["proposal_id"] == manifest["proposal_id"]
    assert repeated["generated_at"] == "2026-08-30T12:00:00+00:00"
    assert base.read_bytes() == before


def test_duplicate_source_date_fails_closed(tmp_path: Path) -> None:
    _, source = _seed(tmp_path)
    with source.open("a", encoding="utf-8") as handle:
        handle.write("2026-06-10,104.0,broker\n")
    with pytest.raises(RuntimeError, match="duplicate date"):
        build_proposal(repo_root=tmp_path)
