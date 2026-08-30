from __future__ import annotations

import gzip
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from projects.alpha_lab.experiments import run_hyp_2026_015_data_gate as gate


def _write_json(path: Path, payload: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def test_sec_header_parses_event_time_identity_and_eastern_acceptance() -> None:
    header = b"""<SEC-DOCUMENT>0000123456-20-000001.txt
<SEC-HEADER>
<ACCEPTANCE-DATETIME>20200203170102
ACCESSION NUMBER: 0000123456-20-000001
CONFORMED SUBMISSION TYPE: 8-K
ITEM INFORMATION: Results of Operations and Financial Condition
CENTRAL INDEX KEY: 0001234567
STANDARD INDUSTRIAL CLASSIFICATION: SEMICONDUCTORS [3674]
</SEC-HEADER>
"""
    parsed = gate._parse_sec_header(header)

    assert parsed["accession"] == "0000123456-20-000001"
    assert parsed["cik"] == "0001234567"
    assert parsed["sic"] == "3674"
    assert parsed["sic_count"] == 1
    assert parsed["item_2_02"] is True
    assert parsed["acceptance"] == datetime(
        2020, 2, 3, 22, 1, 2, tzinfo=timezone.utc
    )


def test_source_preflight_allows_addendum_materiality_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "outputs/research/alpha_lab/data_spine/sec_original/test"
    _write_json(
        bundle / "data/status/part_00000_status.json",
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
    inventory_path = bundle / "data/inventory/part_00000_inventory.jsonl.gz"
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(inventory_path, "wt", encoding="utf-8") as stream:
        for index in range(999):
            stream.write(
                json.dumps(
                    {
                        "accession_number": f"{index:010d}-20-000001",
                        "source_sha256": "a" * 64,
                        "acceptance_parse_status": "PASS",
                        "acceptance_datetime_utc": "2020-01-01T00:00:00Z",
                    }
                )
                + "\n"
            )
    manifest = {
        "metadata": {
            "candidate_count": 1000,
            "hydrated_count": 999,
            "acceptance_timestamp_pass_count": 999,
            "error_count": 1,
        },
        "files": [],
    }
    manifest["bundle_hash"] = gate.canonical_hash(manifest)
    manifest_hash = _write_json(bundle / "manifest.json", manifest)
    monkeypatch.setattr(
        gate,
        "SOURCE_MANIFEST_RELATIVE_PATH",
        str((bundle / "manifest.json").relative_to(tmp_path)),
    )
    monkeypatch.setattr(gate, "SOURCE_MANIFEST_SHA256", manifest_hash)
    monkeypatch.setattr(gate, "SOURCE_BUNDLE_SHA256", manifest["bundle_hash"])

    result = gate._source_preflight(tmp_path)

    assert result["gate_pass"] is True
    assert result["coverage"] == pytest.approx(0.999)
    assert result["excluded_accessions"] == ["0001549848-14-000068"]
    assert result["inventory_census_failures"] == []


def test_item_tape_excludes_missing_original_and_stops_at_challenge_boundary(
    tmp_path: Path,
) -> None:
    tape = tmp_path / "events.jsonl.gz"
    rows = [
        {
            "event_id": "0000000001-19-000001",
            "issuer_cik": "1",
            "form_type": "8-K",
            "acceptance_datetime_utc": "2019-01-02T12:00:00Z",
            "items": "2.02,9.01",
        },
        {
            "event_id": "0000000002-20-000002",
            "issuer_cik": "2",
            "form_type": "8-K",
            "acceptance_datetime_utc": "2020-01-02T12:00:00Z",
            "items": "2.02",
        },
        {
            "event_id": "0000000003-25-000003",
            "issuer_cik": "3",
            "form_type": "8-K",
            "acceptance_datetime_utc": "2025-01-02T12:00:00Z",
            "items": "2.02",
        },
    ]
    with gzip.open(tape, "wt", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")

    events, audit = gate._load_earnings_events(
        tape, {"0000000002-20-000002"}
    )

    assert [row["event_id"] for row in events] == ["0000000001-19-000001"]
    assert audit["deterministically_excluded_missing_original_rows"] == [
        {
            "event_id": "0000000002-20-000002",
            "issuer_cik": "0000000002",
            "accepted_date": "2020-01-02",
            "acceptance_datetime_utc": datetime(
                2020, 1, 2, 12, 0, tzinfo=timezone.utc
            ),
        }
    ]
    assert audit["challenge_boundary_encountered_then_scan_stopped"] is True


def test_reaction_session_obeys_frozen_0930_boundary() -> None:
    sessions = [date(2020, 2, 3), date(2020, 2, 4), date(2020, 2, 5)]
    before_open = datetime(2020, 2, 3, 14, 29, 59, tzinfo=timezone.utc)
    at_open = datetime(2020, 2, 3, 14, 30, 0, tzinfo=timezone.utc)

    assert gate._reaction_session(before_open, sessions) == date(2020, 2, 3)
    assert gate._reaction_session(at_open, sessions) == date(2020, 2, 4)


def _identity_fixture() -> dict[str, object]:
    memberships = {
        "SEC:R": [
            {
                "membership_start_date": "2010-01-01",
                "membership_end_date": "2024-12-31",
            }
        ],
        "SEC:P": [
            {
                "membership_start_date": "2010-01-01",
                "membership_end_date": "2024-12-31",
            }
        ],
        "SEC:C": [
            {
                "membership_start_date": "2010-01-01",
                "membership_end_date": "2024-12-31",
            }
        ],
    }
    return {
        "by_cik": {
            "0000000001": [
                {
                    "security_id": "SEC:R",
                    "effective_start": "2010-01-01",
                    "effective_end": "2024-12-31",
                }
            ],
            "0000000002": [
                {
                    "security_id": "SEC:P",
                    "effective_start": "2010-01-01",
                    "effective_end": "2024-12-31",
                }
            ],
            "0000000003": [
                {
                    "security_id": "SEC:C",
                    "effective_start": "2010-01-01",
                    "effective_end": "2024-12-31",
                }
            ],
        },
        "memberships": memberships,
        "security_by_id": {"SEC:R": {}, "SEC:P": {}, "SEC:C": {}},
    }


def test_same_session_sic_cluster_preserves_all_reporter_ids_without_aggregation() -> None:
    accepted = datetime(2020, 2, 3, 12, 0, tzinfo=timezone.utc)
    events = [
        {
            "event_id": "0000000001-20-000001",
            "issuer_cik": "0000000001",
            "form_type": "8-K",
            "acceptance": accepted,
        },
        {
            "event_id": "0000000001-20-000002",
            "issuer_cik": "0000000001",
            "form_type": "8-K",
            "acceptance": accepted,
        },
    ]
    headers = [
        {
            "event_id": row["event_id"],
            "cik": "0000000001",
            "sic": "3674",
            "sic_count": 1,
            "acceptance": accepted,
            "form_type": "8-K",
            "item_2_02": True,
            "source_sha256": "a" * 64,
        }
        for row in events
    ] + [
        {
            "event_id": "0000000002-19-000001",
            "cik": "0000000002",
            "sic": "3674",
            "sic_count": 1,
            "acceptance": datetime(2019, 11, 1, tzinfo=timezone.utc),
            "form_type": "8-K",
            "item_2_02": True,
            "source_sha256": "b" * 64,
        },
        {
            "event_id": "0000000003-19-000001",
            "cik": "0000000003",
            "sic": "3699",
            "sic_count": 1,
            "acceptance": datetime(2019, 11, 1, tzinfo=timezone.utc),
            "form_type": "8-K",
            "item_2_02": True,
            "source_sha256": "c" * 64,
        },
    ]
    sessions = [
        date(2020, 2, 3),
        date(2020, 2, 4),
        date(2020, 2, 5),
        date(2020, 2, 6),
        date(2020, 2, 7),
        date(2020, 2, 10),
    ]

    clusters, audit = gate._build_structural_clusters(
        events=events,
        headers=headers,
        identity=_identity_fixture(),
        sessions=sessions,
    )

    assert audit["reporter_included"] == 2
    assert len(clusters) == 1
    assert clusters[0]["reporter_event_ids"] == [
        "0000000001-20-000001",
        "0000000001-20-000002",
    ]
    assert clusters[0]["peers"][0]["security_id"] == "SEC:P"
    assert clusters[0]["peers"][0]["causal_sic_source"]["event_id"] == (
        "0000000002-19-000001"
    )
    assert clusters[0]["controls"][0]["security_id"] == "SEC:C"
    assert clusters[0]["reporters"] == [
        {
            "accessions": [
                "0000000001-20-000001",
                "0000000001-20-000002",
            ],
            "cik": "0000000001",
            "security_id": "SEC:R",
        }
    ]


def test_append_only_bundle_is_deterministic_and_create_only(tmp_path: Path) -> None:
    repo = tmp_path
    clusters = [
        {
            "cluster_id": "HYP015-test",
            "reaction_session": date(2020, 1, 2),
            "entry_session": date(2020, 1, 3),
            "exit_session": date(2020, 1, 9),
            "sic": "3674",
            "reporter_event_ids": ["event-a", "event-b"],
            "reporter_ciks": ["0000000001"],
            "reporter_security_ids": ["SEC:R"],
            "peers": [{"security_id": "SEC:P"}],
            "controls": [],
            "included_peers": [
                {
                    "security_id": "SEC:P",
                    "overlap_entry_session": date(2020, 1, 3),
                    "overlap_exit_session": date(2020, 1, 9),
                    "relevance": "FOUR_DIGIT_PEER",
                }
            ],
            "included_controls": [],
            "reporter_lineage_pass": True,
            "structural_breadth_pass": False,
        }
    ]
    result = {
        "outcome": "BLOCKED_DATA",
        "reporter_reaction_accessed": False,
        "forward_return_accessed": False,
    }

    run_dir, manifest = gate._write_append_only_bundle(
        repo_root=repo,
        run_id="run-v2",
        result=result,
        clusters=clusters,
        exclusions=[],
    )

    assert (run_dir / "manifest.json").is_file()
    assert manifest["reporter_reaction_accessed"] is False
    assert manifest["forward_return_accessed"] is False
    with pytest.raises(FileExistsError):
        gate._write_append_only_bundle(
            repo_root=repo,
            run_id="run-v2",
            result=result,
            clusters=clusters,
            exclusions=[],
        )


def test_gate_summary_labels_structural_counts_as_pre_signal() -> None:
    controls = gate._gate_summary(
        {"coverage": 0.9995, "inventory_census_failures": []},
        {"coverage": 1.0, "failures": {}},
        {
            "reporter_attempted": 1000,
            "reporter_included": 1000,
            "peer_mapping_rate": 0.995,
            "control_mapping_rate": 0.995,
        },
        {
            "path_failures": {},
            "peer_path_coverage": 0.995,
            "control_path_coverage": 0.995,
            "validation_structural_cluster_count": 150,
            "validation_structural_unique_peer_count": 100,
            "validation_structural_four_digit_sic_count": 20,
        },
        {"selection_gate_pass": True, "selection_gate_reasons": []},
        [
                {
                    "emitted_evaluator_eligible": True,
                    "reporter_lineage_pass": True,
                "included_peers": [
                    {
                        "included": True,
                        "terminal_disposition": "COMPLETE_THROUGH_EXIT",
                        "terminal_outcome_required": False,
                    }
                ],
                "included_controls": [],
                "potential_overlap_inputs_complete": True,
                }
            ],
        {"duplicate_event_ids": []},
        {"coverage": 0.9995, "sic_coverage": 0.995},
    )

    assert all(item["status"] == "PASS" for item in controls)


def test_addendum_verification_binds_pre_record_body_and_full_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    relative = "hypotheses/addendum.md"
    body = "HYP-2026-015 Addendum 001 99.9% deterministic no-return\n"
    content = body + "## Addendum record\nowner-approved\n"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(gate, "ADDENDUM_RELATIVE_PATH", relative)
    monkeypatch.setattr(
        gate, "ADDENDUM_BODY_SHA256", hashlib.sha256(body.encode()).hexdigest()
    )
    monkeypatch.setattr(
        gate, "ADDENDUM_FULL_FILE_SHA256", hashlib.sha256(content.encode()).hexdigest()
    )

    record = gate._verify_addendum(tmp_path)

    assert record["frozen_body_sha256"] == hashlib.sha256(body.encode()).hexdigest()
    assert record["full_file_sha256"] == hashlib.sha256(content.encode()).hexdigest()


def test_lineage_requires_exact_path_metadata_and_rejects_terminal_event() -> None:
    sessions = [date(2020, 1, 2), date(2020, 1, 3)]
    rows = {
        ("SEC:P", value): {
            "row_present": True,
            "open_valid": True,
            "closeadj_valid": True,
            "available_at": datetime(2020, 1, 2, tzinfo=timezone.utc),
            "corporate_action_present": False,
            "corporate_action_lineage_present": True,
            "terminal_value_present": False,
        }
        for value in sessions
    }
    assert gate._lineage_complete(
        rows, "SEC:P", sessions, require_entry_open=True
    )
    assert not gate._lineage_complete(
        rows,
        "SEC:P",
        sessions,
        require_entry_open=True,
        selection_deadline=datetime(2020, 1, 1, 23, 59, tzinfo=timezone.utc),
    )
    rows[("SEC:P", sessions[-1])]["terminal_value_present"] = True
    assert not gate._lineage_complete(
        rows, "SEC:P", sessions, require_entry_open=True
    )


def test_missingness_enforces_material_stratum_and_ignores_universe_ineligibility() -> None:
    candidates = []
    for index in range(100):
        candidate = {
            "cik": f"{index:010d}",
            "security_id": f"SEC:{index}",
            "mapping_status": "UNIQUE",
            "causal_sic_source": {
                "event_id": f"source-{index}",
                "source_path": f"source/{index}.txt",
            },
        }
        if index < 2:
            candidate.update(
                {
                    "exclusion_class": "LINEAGE_MISSING",
                    "exclusion_reason": "peer_holding_path_missing",
                }
            )
        elif index == 2:
            candidate.update(
                {
                    "exclusion_class": "UNIVERSE_INELIGIBLE",
                    "exclusion_reason": "peer_price_floor_fail",
                }
            )
        else:
            candidate["included"] = True
        candidates.append(candidate)
    cluster = {
        "cluster_id": "HYP015-test",
        "potential_cluster_key": "2020-01-02::3674::event-a",
        "reaction_session": date(2020, 1, 2),
        "sic": "3674",
        "reporter_event_ids": [],
        "reporter_ciks": [],
        "reporter_lineage_pass": True,
        "emitted_evaluator_eligible": True,
        "peers": candidates,
        "controls": [],
    }

    exclusions, audit = gate._build_exclusions_and_missingness(
        source_errors=[],
        event_audit={"deterministically_excluded_missing_original_rows": []},
        included_events=[],
        headers=[],
        clusters=[cluster],
        checked_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        sessions=[date(2020, 1, 2), date(2020, 1, 3)],
    )

    assert audit["structural_missing"] == 2
    assert "material_year_stratum_below_99_percent::2020" in audit[
        "selection_gate_reasons"
    ]
    universe = next(item for item in exclusions if item["issuer_cik"] == "0000000002")
    assert universe["adverse_sensitivity_eligible"] is False
    assert universe["source_status"] == "UNIVERSE_INELIGIBLE"


def test_manifest_emits_only_evaluator_eligible_clusters_with_integration_fields() -> None:
    base = {
        "cluster_id": "HYP015-test",
        "potential_cluster_key": "2020-01-02::3674::event-a",
        "reaction_session": date(2020, 1, 2),
        "entry_session": date(2020, 1, 3),
        "exit_session": date(2020, 1, 9),
        "sic": "3674",
        "reporter_event_ids": ["event-a"],
        "reporter_ciks": ["0000000001"],
        "reporter_security_ids": ["SEC:R"],
        "reporters": [
            {
                "accessions": ["event-a"],
                "cik": "0000000001",
                "security_id": "SEC:R",
            }
        ],
        "peers": [{"security_id": "SEC:P"}],
        "controls": [{"security_id": "SEC:C"}],
        "included_peers": [
            {
                "security_id": "SEC:P",
                "overlap_entry_session": date(2020, 1, 3),
                "overlap_exit_session": date(2020, 1, 9),
                "relevance": "FOUR_DIGIT_PEER",
            }
        ],
        "included_controls": [
            {
                "security_id": "SEC:C",
                "overlap_entry_session": date(2020, 1, 3),
                "overlap_exit_session": date(2020, 1, 9),
                "relevance": "TWO_DIGIT_INDUSTRY_CONTROL",
            }
        ],
        "peer_report_during_hold_security_ids": ["SEC:P"],
        "reporter_lineage_pass": True,
        "structural_breadth_pass": True,
        "emitted_evaluator_eligible": True,
    }
    excluded = {**base, "cluster_id": "HYP015-excluded", "emitted_evaluator_eligible": False}

    rows = list(gate._manifest_rows([base, excluded]))

    assert len(rows) == 1
    assert rows[0]["reporters"][0]["accessions"] == ["event-a"]
    assert rows[0]["included_peer_security_ids"] == ["SEC:P"]
    assert rows[0]["industry_control_security_ids"] == ["SEC:C"]
    assert rows[0]["peer_report_during_hold_security_ids"] == ["SEC:P"]


def test_validation_missing_source_exclusion_has_causal_reaction_mapping() -> None:
    accepted = datetime(2020, 1, 2, 22, 0, tzinfo=timezone.utc)
    exclusions, audit = gate._build_exclusions_and_missingness(
        source_errors=[
            {
                "accession": "0000000001-20-000001",
                "source_filename": "edgar/missing.txt",
                "error_type": "NOT_FOUND",
            }
        ],
        event_audit={
            "deterministically_excluded_missing_original_rows": [
                {
                    "event_id": "0000000001-20-000001",
                    "issuer_cik": "0000000001",
                    "accepted_date": "2020-01-02",
                    "acceptance_datetime_utc": accepted,
                }
            ]
        },
        included_events=[],
        headers=[],
        clusters=[],
        checked_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        sessions=[date(2020, 1, 2), date(2020, 1, 3)],
    )

    assert exclusions[0]["adverse_sensitivity_eligible"] is True
    assert exclusions[0]["reaction_session"] == date(2020, 1, 3)
    assert exclusions[0]["reaction_quarter"] == date(2020, 1, 1)
    assert exclusions[0]["sic4"] == "UNKNOWN"
    assert "adverse_sensitivity_mapping_unproven" not in audit[
        "selection_gate_reasons"
    ]
