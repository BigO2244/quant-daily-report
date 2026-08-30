from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from core.cio_daily_brief import (
    build_cio_daily_brief,
    content_hash,
    persist_brief_bundle,
    render_cio_daily_brief,
)

DATE = "2026-08-28"


def _control(passed: bool = True, reason: str = "") -> dict:
    return {"pass": passed, "reasons": [reason] if reason else [], "evidence": []}


def _certification(
    *, certified: int = 20, failed: dict[str, str] | None = None
) -> dict:
    controls = {
        name: _control(name not in (failed or {}), (failed or {}).get(name, ""))
        for name in (
            "data_freshness_pit_validity",
            "compute_recomputed",
            "decision_from_certified_compute",
            "precompute_immutable_hashed",
            "execution_consumed_exact_artifact",
            "broker_reconciliation",
        )
    }
    passed = sum(row["pass"] for row in controls.values())
    return {
        "schema_version": "caerus.trading_integrity_certification.v1",
        "through_date": DATE,
        "certified_sessions": certified,
        "expected_sessions": 20,
        "sessions": [
            {
                "trade_date": DATE,
                "certified": passed == 6,
                "controls_passed": passed,
                "controls_expected": 6,
                "controls": controls,
            }
        ],
    }


def _lane(lane_id: str, kind: str, strategy: str, **updates) -> dict:
    value = {
        "lane_id": lane_id,
        "lane_kind": kind,
        "strategy_ids": [strategy],
        "declared_state": "ACTIVE",
        "operating_status": "ACTIVE",
        "authority": {"status": "PROVED"},
        "runtime_gates": {"status": "PASS"},
        "schedule": {"status": "INSTALLED"},
        "broker_truth": {"status": "PASS", "as_of": "2026-08-28T23:00:00Z"},
        "latest_execution": {
            "status": "COMPLETE",
            "reconciliation_status": "ALIGNED",
            "latest_completed_session": DATE,
        },
    }
    value.update(updates)
    return value


def _operating() -> dict:
    value = {
        "schema_version": "caerus.operating_truth.v1",
        "context_integrity": {"status": "PASS", "conflicts": []},
        "lanes": [
            _lane("lyra_live", "LIVE", "caerus_lyra"),
            _lane("orion_paper", "PAPER", "caerus_orion"),
            _lane(
                "shadow",
                "SHADOW",
                "caerus_polaris",
                broker_truth={"status": "NONCAPITAL"},
                latest_execution={"status": "NOT_APPLICABLE"},
            ),
        ],
    }
    value["content_hash"] = content_hash(value)
    return value


def _sources() -> list[dict]:
    return [
        {
            "kind": "trading_integrity",
            "path": "cert.json",
            "status": "AVAILABLE",
            "sha256": "a" * 64,
        },
        {
            "kind": "operating_truth",
            "path": "ops.json",
            "status": "AVAILABLE",
            "sha256": "b" * 64,
        },
    ]


def _build(**updates) -> dict:
    values = {
        "report_date": DATE,
        "certification": _certification(),
        "operating_truth": _operating(),
        "sources": _sources(),
    }
    values.update(updates)
    return build_cio_daily_brief(**values)


def test_alpha_is_unavailable_and_attention_is_capped() -> None:
    payload = _build()
    assert payload["operations"]["status"] == "YELLOW"
    assert payload["alpha"]["status"] == "UNAVAILABLE"
    for field in ("improving", "deteriorating", "new_shadow", "killed"):
        assert payload["alpha"][field]["status"] == "UNAVAILABLE"
    assert payload["alpha"]["research_throughput"]["count"] is None
    assert len(payload["cio_attention"]) <= 3


def test_missing_inputs_fail_closed_and_throughput_is_unavailable() -> None:
    payload = _build(certification=None, operating_truth=None)
    assert payload["operations"]["status"] == "RED"
    assert payload["alpha"]["research_throughput"] == {
        "status": "UNAVAILABLE",
        "month": "2026-08",
        "count": None,
        "reason": "canonical_authenticated_research_authority_not_implemented",
    }
    assert len(payload["operations"]["exceptions"]) == 3
    assert "UNAVAILABLE" in render_cio_daily_brief(payload)


def test_historical_or_pit_gap_is_yellow_but_current_lineage_failure_is_red() -> None:
    yellow = _build(
        certification=_certification(
            certified=0,
            failed={"data_freshness_pit_validity": "pit_universe_not_decision_grade"},
        )
    )
    red = _build(
        certification=_certification(
            certified=19,
            failed={
                "execution_consumed_exact_artifact": "exact_execution_payload_missing"
            },
        )
    )
    assert yellow["operations"]["status"] == "YELLOW"
    assert red["operations"]["status"] == "RED"


def test_generated_at_and_evidence_timestamp_do_not_create_capital_change() -> None:
    first = _build()
    later = _operating()
    later["generated_at_utc"] = "2099-01-01T00:00:00Z"
    later["lanes"][0]["broker_truth"]["as_of"] = "2026-08-29T23:00:00Z"
    later["content_hash"] = content_hash(later)
    second = _build(operating_truth=later, previous_brief=first)
    assert all(not rows for rows in second["capital"]["meaningful_changes"].values())


def test_semantic_lane_change_is_reported_once() -> None:
    first = _build()
    changed = _operating()
    changed["lanes"][1]["operating_status"] = "ACTIVE_WITH_EXCEPTION"
    changed["content_hash"] = content_hash(changed)
    second = _build(operating_truth=changed, previous_brief=first)
    assert len(second["capital"]["meaningful_changes"]["PAPER"]) == 1
    third = _build(operating_truth=changed, previous_brief=second)
    assert third["capital"]["meaningful_changes"]["PAPER"] == []


def test_context_conflict_degrades_instead_of_publishing_capital_claim() -> None:
    operating = _operating()
    operating["context_integrity"] = {
        "status": "CONTEXT_CONFLICT",
        "conflicts": ["bad claim"],
    }
    operating["content_hash"] = content_hash(operating)
    payload = _build(operating_truth=operating)
    assert payload["operations"]["status"] == "RED"
    assert payload["capital"]["status"] == "UNAVAILABLE"


def test_bundle_is_hash_bound_deterministic_and_immutable(tmp_path: Path) -> None:
    payload = _build()
    manifest = persist_brief_bundle(output_root=tmp_path, payload=payload)
    target = tmp_path / DATE
    first = {path.name: path.read_bytes() for path in target.iterdir()}
    assert manifest["content_hash"] == content_hash(manifest)
    for item in manifest["artifacts"]:
        assert hashlib.sha256(first[item["name"]]).hexdigest() == item["sha256"]
    persist_brief_bundle(output_root=tmp_path, payload=payload)
    assert first == {path.name: path.read_bytes() for path in target.iterdir()}
    (target / "brief.md").write_text("mutated", encoding="utf-8")
    with pytest.raises(FileExistsError):
        persist_brief_bundle(output_root=tmp_path, payload=payload)


def test_cli_persists_bundle_with_alpha_unavailable(tmp_path: Path) -> None:
    certification = tmp_path / "cert.json"
    operating = tmp_path / "ops.json"
    certification.write_text(json.dumps(_certification()), encoding="utf-8")
    operating.write_text(json.dumps(_operating()), encoding="utf-8")
    arbitrary = (
        tmp_path / "outputs/research/alpha_lab/ledger/research_projection.v1.json"
    )
    arbitrary.parent.mkdir(parents=True)
    arbitrary.write_text(
        json.dumps(
            {
                "families": [{"trend": "IMPROVING", "terminal_verdict": "KILL"}],
                "lifecycle_events": [{"target_state": "SHADOW"}],
            }
        ),
        encoding="utf-8",
    )
    from scripts.build_cio_daily_brief import main

    output = tmp_path / "briefs"
    assert (
        main(
            [
                "--repo-root",
                str(tmp_path),
                "--report-date",
                DATE,
                "--certification",
                str(certification),
                "--operating-truth",
                str(operating),
                "--output-root",
                str(output),
            ]
        )
        == 0
    )
    payload = json.loads((output / DATE / "brief.json").read_text(encoding="utf-8"))
    assert payload["alpha"]["research_throughput"]["status"] == "UNAVAILABLE"
    assert payload["alpha"]["new_shadow"]["status"] == "UNAVAILABLE"
    assert payload["alpha"]["killed"]["status"] == "UNAVAILABLE"
    assert "research_projection" not in {row["kind"] for row in payload["sources"]}
    assert (output / DATE / "manifest.json").is_file()


def test_cli_has_no_research_projection_authority(tmp_path: Path) -> None:
    from scripts.build_cio_daily_brief import main

    with pytest.raises(SystemExit):
        main(
            [
                "--repo-root",
                str(tmp_path),
                "--report-date",
                DATE,
                "--research-projection",
                str(tmp_path / "arbitrary.json"),
            ]
        )
