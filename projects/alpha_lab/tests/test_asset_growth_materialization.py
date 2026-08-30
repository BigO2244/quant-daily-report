from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from projects.alpha_lab.data_spine import asset_growth
from projects.alpha_lab.data_spine.asset_growth import (
    OUTPUT_FIELDS,
    build_feature_rows,
    materialize_asset_growth_features,
)
from projects.alpha_lab.factory import canonical_hash


def _asset_row(
    *,
    cik: str = "123",
    accession: str,
    fiscal_period_end: str,
    value: float,
    filed: str,
    available_at: str,
    form: str = "10-K",
) -> dict:
    return {
        "cik": cik,
        "source_fact": "Assets",
        "unit": "USD",
        "value": value,
        "fiscal_period_end": fiscal_period_end,
        "filed": filed,
        "available_at": available_at,
        "accession_number": accession,
        "form": form,
        "fiscal_period": "FY",
        "frame": "",
    }


def _three_year_events(*, cik: str = "123") -> list[dict]:
    return [
        _asset_row(
            cik=cik,
            accession="A2018",
            fiscal_period_end="2018-12-31",
            value=100.0,
            filed="2019-02-15",
            available_at="2019-02-19T14:30:00+00:00",
        ),
        _asset_row(
            cik=cik,
            accession="A2019",
            fiscal_period_end="2018-12-31",
            value=100.0,
            filed="2020-02-14",
            available_at="2020-02-18T14:30:00+00:00",
        ),
        _asset_row(
            cik=cik,
            accession="A2019",
            fiscal_period_end="2019-12-31",
            value=110.0,
            filed="2020-02-14",
            available_at="2020-02-18T14:30:00+00:00",
        ),
        _asset_row(
            cik=cik,
            accession="A2020",
            fiscal_period_end="2018-12-31",
            value=100.0,
            filed="2021-02-12",
            available_at="2021-02-16T14:30:00+00:00",
        ),
        _asset_row(
            cik=cik,
            accession="A2020",
            fiscal_period_end="2019-12-31",
            value=110.0,
            filed="2021-02-12",
            available_at="2021-02-16T14:30:00+00:00",
        ),
        _asset_row(
            cik=cik,
            accession="A2020",
            fiscal_period_end="2020-12-31",
            value=132.0,
            filed="2021-02-12",
            available_at="2021-02-16T14:30:00+00:00",
        ),
    ]


def _master(*rows: tuple[str, str, str, str]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "security_id": security_id,
                "cik": cik,
                "effective_start": start,
                "effective_end": end,
            }
            for security_id, cik, start, end in rows
        ]
    )


def test_builds_filing_time_growth_with_90_day_floor_and_one_to_many_identity() -> None:
    assets = pd.DataFrame(_three_year_events())
    master = _master(
        ("SEC-A", "123", "2010-01-01", ""),
        ("SEC-B", "123", "2020-01-01", ""),
        ("EXPIRED", "123", "2010-01-01", "2020-12-31"),
    )

    rows, quality = build_feature_rows(assets, master)

    assert list(rows.columns) == list(OUTPUT_FIELDS)
    assert set(rows["security_id"]) == {"SEC-A", "SEC-B"}
    assert set(rows["fiscal_period_end"]) == {"2020-12-31"}
    # 2020-12-31 + 90 days is 2021-03-31; the availability floor dominates
    # the earlier February filing timestamp.
    assert set(rows["available_at"]) == {"2021-03-31T13:30:00+00:00"}
    assert rows["asset_growth_1y"].iloc[0] == pytest.approx(0.2)
    assert rows["asset_growth_2y"].iloc[0] == pytest.approx(0.32)
    assert json.loads(rows["source_accessions"].iloc[0]) == ["A2020"]
    assert quality["feature_rows"] == 2
    assert quality["challenge_rows_used"] is False
    assert quality["challenge_return_data_accessed"] is False


def test_amendment_changes_only_later_available_feature_version() -> None:
    records = _three_year_events()
    for period, value in [
        ("2018-12-31", 100.0),
        ("2019-12-31", 120.0),
        ("2020-12-31", 132.0),
    ]:
        records.append(
            _asset_row(
                accession="A2020-AMEND",
                fiscal_period_end=period,
                value=value,
                filed="2021-05-03",
                available_at="2021-05-04T13:30:00+00:00",
                form="10-K/A",
            )
        )
    rows, _ = build_feature_rows(
        pd.DataFrame(records),
        _master(("SEC-A", "123", "2010-01-01", "")),
    )

    assert len(rows) == 2
    assert rows.iloc[0]["available_at"] == "2021-03-31T13:30:00+00:00"
    assert rows.iloc[0]["asset_growth_1y"] == pytest.approx(0.2)
    assert rows.iloc[1]["available_at"] == "2021-05-04T13:30:00+00:00"
    assert rows.iloc[1]["asset_growth_1y"] == pytest.approx(0.1)
    assert json.loads(rows.iloc[1]["source_accessions"]) == ["A2020-AMEND"]


def test_conflicting_values_within_accession_period_fail_closed() -> None:
    records = _three_year_events()
    duplicate = dict(records[-1])
    duplicate["value"] = 133.0
    records.append(duplicate)
    with pytest.raises(ValueError, match="conflicting annual asset values"):
        build_feature_rows(
            pd.DataFrame(records),
            _master(("SEC-A", "123", "2010-01-01", "")),
        )


def test_missing_predecessor_identity_and_challenge_rows_are_not_emitted() -> None:
    records = _three_year_events(cik="999")
    records.append(
        _asset_row(
            cik="123",
            accession="CHALLENGE",
            fiscal_period_end="2024-12-31",
            value=200.0,
            filed="2025-02-14",
            available_at="2025-02-18T14:30:00+00:00",
        )
    )
    rows, quality = build_feature_rows(
        pd.DataFrame(records),
        _master(("SEC-A", "123", "2010-01-01", "")),
    )
    assert rows.empty
    assert quality["unmapped_identity_events_excluded"] == 0
    assert quality["complete_events_without_master_cik_excluded"] == 1
    assert quality["challenge_cutoff_events_excluded"] == 1
    assert quality["challenge_rows_used"] is False
    assert quality["challenge_return_data_accessed"] is False


def test_same_effective_amendment_wins_even_when_accession_sort_is_reversed() -> None:
    records = _three_year_events()
    for record in records[-3:]:
        record["accession_number"] = "Z-BASE"
    for period, value in [
        ("2018-12-31", 100.0),
        ("2019-12-31", 120.0),
        ("2020-12-31", 132.0),
    ]:
        records.append(
            _asset_row(
                accession="A-AMEND",
                fiscal_period_end=period,
                value=value,
                filed="2021-02-12",
                available_at="2021-02-16T14:30:00+00:00",
                form="10-K/A",
            )
        )
    rows, _ = build_feature_rows(
        pd.DataFrame(records),
        _master(("SEC-A", "123", "2010-01-01", "")),
    )

    assert len(rows) == 1
    assert rows.iloc[0]["asset_growth_1y"] == pytest.approx(0.1)
    assert json.loads(rows.iloc[0]["source_accessions"]) == ["A-AMEND"]


def test_causally_early_availability_is_conservatively_floored() -> None:
    records = _three_year_events()
    records[0]["available_at"] = "2019-02-15T14:30:00+00:00"
    rows, quality = build_feature_rows(
        pd.DataFrame(records),
        _master(("SEC-A", "123", "2010-01-01", "")),
    )
    assert len(rows) == 1
    assert quality["causally_early_rows_adjusted"] == 1


def test_positive_and_zero_values_in_same_accession_fail_closed() -> None:
    records = _three_year_events()
    duplicate = dict(records[-1])
    duplicate["value"] = 0.0
    records.append(duplicate)
    with pytest.raises(ValueError, match="conflicting annual asset values"):
        build_feature_rows(
            pd.DataFrame(records),
            _master(("SEC-A", "123", "2010-01-01", "")),
        )


def test_fiscal_period_after_filing_is_excluded_and_counted() -> None:
    records = _three_year_events()
    records.append(
        _asset_row(
            accession="IMPOSSIBLE",
            fiscal_period_end="2022-12-31",
            value=150.0,
            filed="2022-12-01",
            available_at="2022-12-02T14:30:00+00:00",
        )
    )
    rows, quality = build_feature_rows(
        pd.DataFrame(records),
        _master(("SEC-A", "123", "2010-01-01", "")),
    )

    assert len(rows) == 1
    assert quality["impossible_period_rows_excluded"] == 1


def test_conflicting_overlapping_security_identity_fails_closed() -> None:
    with pytest.raises(ValueError, match="conflicting CIKs"):
        build_feature_rows(
            pd.DataFrame(_three_year_events()),
            _master(
                ("SEC-A", "123", "2010-01-01", "2022-12-31"),
                ("SEC-A", "456", "2020-01-01", ""),
            ),
        )


def _write_upstream_authorities(tmp_path: Path, source: Path, master: Path) -> None:
    source_manifest = source.with_name("sec_companyfacts_compact_manifest.json")
    source_manifest.write_text(
        json.dumps(
            {
                "schema_version": "caerus_alpha_lab_sec_facts_compact_v1",
                "output": "outputs/research/alpha_lab/shared/sec_companyfacts_compact.csv.gz",
                "availability_rule": "next_session_open_after_SEC_filed_date",
                "selected_facts": ["assets"],
                "output_bytes": source.stat().st_size,
                "output_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    security_path = (
        tmp_path / "outputs/research/alpha_lab/provider_readiness/pit_security_master_v1.json"
    )
    security_path.parent.mkdir(parents=True, exist_ok=True)
    unsigned = {
        "provider_id": "caerus.fr068",
        "dataset_id": "effective_dated_security_identity",
        "status": "READY",
        "data_files": [
            {
                "path": "data/pit_universe/security_master.csv",
                "bytes": master.stat().st_size,
                "sha256": hashlib.sha256(master.read_bytes()).hexdigest(),
            }
        ],
        "schema_validation_status": "PASS",
        "historical_point_in_time_verified": True,
        "blockers": [],
    }
    payload = dict(unsigned)
    payload["evidence_hash"] = canonical_hash(unsigned)
    security_path.write_text(json.dumps(payload), encoding="utf-8")
    membership = tmp_path / "data/pit_universe/membership_universe.csv"
    pd.DataFrame(
        [
            {
                "security_id": "SEC-A",
                "ticker": "TEST",
                "membership_start_date": "2010-01-01",
                "membership_end_date": "",
                "membership_family": "test_frozen_membership",
                "source": "TEST",
                "confidence": "HIGH",
            }
        ]
    ).to_csv(membership, index=False)
    membership_path = (
        tmp_path / "outputs/research/alpha_lab/provider_readiness/pit_membership_v1.json"
    )
    membership_record = {
        "path": "data/pit_universe/membership_universe.csv",
        "bytes": membership.stat().st_size,
        "sha256": hashlib.sha256(membership.read_bytes()).hexdigest(),
    }
    membership_unsigned = {
        "provider_id": "caerus.fr068",
        "dataset_id": "survivorship_free_universe_membership",
        "status": "READY",
        "data_files": [membership_record],
        "schema_validation_status": "PASS",
        "historical_point_in_time_verified": True,
        "blockers": [],
    }
    membership_payload = dict(membership_unsigned)
    membership_payload["evidence_hash"] = canonical_hash(membership_unsigned)
    membership_path.write_text(json.dumps(membership_payload), encoding="utf-8")


def test_materializer_writes_hash_bound_certified_parquet_without_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "outputs/research/alpha_lab/shared/sec_companyfacts_compact.csv.gz"
    source.parent.mkdir(parents=True)
    source_rows = []
    for row in _three_year_events():
        source_rows.append(
            {
                "cik": row["cik"],
                "entity_name": "Issuer",
                "logical_fact": "assets",
                "taxonomy": "us-gaap",
                "source_fact": row["source_fact"],
                "unit": row["unit"],
                "value": row["value"],
                "start": "",
                "end": row["fiscal_period_end"],
                "filed": row["filed"],
                "available_at": row["available_at"],
                "accession_number": row["accession_number"],
                "form": row["form"],
                "fiscal_year": row["fiscal_period_end"][:4],
                "fiscal_period": "FY",
                "frame": "",
            }
        )
    pd.DataFrame(source_rows).to_csv(source, index=False, compression="gzip")
    master = tmp_path / "data/pit_universe/security_master.csv"
    master.parent.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "security_id": "SEC-A",
                "cik": "123",
                "effective_start": "2010-01-01",
                "effective_end": "",
            }
        ]
    ).to_csv(master, index=False)
    _write_upstream_authorities(tmp_path, source, master)
    monkeypatch.setattr(asset_growth, "MIN_MAPPING_COVERAGE", 0.0)
    monkeypatch.setattr(asset_growth, "MIN_UNIQUE_SECURITIES", 1)
    monkeypatch.setattr(asset_growth, "MIN_SECURITIES_PER_YEAR", 1)
    monkeypatch.setattr(asset_growth, "MIN_MEMBERSHIP_COVERAGE", 1.0)
    monkeypatch.setattr(asset_growth, "COVERAGE_YEARS", ("2021",))

    result = materialize_asset_growth_features(repo_root=tmp_path)

    output = Path(result["output_path"])
    certification_path = Path(result["certification_path"])
    assert output.is_file()
    assert hashlib.sha256(output.read_bytes()).hexdigest() == result["output_sha256"]
    certification = json.loads(certification_path.read_text(encoding="utf-8"))
    unsigned = dict(certification)
    evidence_hash = unsigned.pop("evidence_hash")
    assert evidence_hash == canonical_hash(unsigned)
    assert certification["status"] == "READY"
    assert certification["historical_point_in_time_verified"] is True
    assert certification["challenge_source_scanned"] is True
    assert certification["challenge_rows_used"] is False
    assert certification["challenge_return_data_accessed"] is False
    assert {item["path"] for item in certification["upstream_sources"]} == {
        "data/pit_universe/security_master.csv",
        "outputs/research/alpha_lab/shared/sec_companyfacts_compact.csv.gz",
        "outputs/research/alpha_lab/shared/sec_companyfacts_compact_manifest.json",
        "outputs/research/alpha_lab/provider_readiness/pit_security_master_v1.json",
        "data/pit_universe/membership_universe.csv",
        "outputs/research/alpha_lab/provider_readiness/pit_membership_v1.json",
    }
    assert set(pd.read_parquet(output).columns) == set(OUTPUT_FIELDS)


def test_upstream_hash_mismatch_fails_before_materialization(tmp_path: Path) -> None:
    source = tmp_path / "outputs/research/alpha_lab/shared/sec_companyfacts_compact.csv.gz"
    source.parent.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                **row,
                "logical_fact": "assets",
                "end": row["fiscal_period_end"],
            }
            for row in _three_year_events()
        ]
    ).to_csv(source, index=False, compression="gzip")
    master = tmp_path / "data/pit_universe/security_master.csv"
    master.parent.mkdir(parents=True)
    _master(("SEC-A", "123", "2010-01-01", "")).to_csv(master, index=False)
    _write_upstream_authorities(tmp_path, source, master)
    manifest = source.with_name("sec_companyfacts_compact_manifest.json")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["output_sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="hash does not match"):
        materialize_asset_growth_features(repo_root=tmp_path)


def test_blocked_coverage_never_occupies_canonical_paths(tmp_path: Path) -> None:
    source = tmp_path / "outputs/research/alpha_lab/shared/sec_companyfacts_compact.csv.gz"
    source.parent.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                **row,
                "logical_fact": "assets",
                "end": row["fiscal_period_end"],
            }
            for row in _three_year_events()
        ]
    ).to_csv(source, index=False, compression="gzip")
    master = tmp_path / "data/pit_universe/security_master.csv"
    master.parent.mkdir(parents=True)
    _master(("SEC-A", "123", "2010-01-01", "")).to_csv(master, index=False)
    _write_upstream_authorities(tmp_path, source, master)

    with pytest.raises(RuntimeError, match="coverage gates failed before canonical publication"):
        materialize_asset_growth_features(repo_root=tmp_path)
    assert not (tmp_path / asset_growth.DEFAULT_OUTPUT).exists()
    assert not (tmp_path / "outputs/research/alpha_lab/provider_readiness/pit_asset_growth_features_v1.json").exists()


def test_certification_failure_rolls_back_new_canonical_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "outputs/research/alpha_lab/shared/sec_companyfacts_compact.csv.gz"
    source.parent.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                **row,
                "logical_fact": "assets",
                "end": row["fiscal_period_end"],
            }
            for row in _three_year_events()
        ]
    ).to_csv(source, index=False, compression="gzip")
    master = tmp_path / "data/pit_universe/security_master.csv"
    master.parent.mkdir(parents=True)
    _master(("SEC-A", "123", "2010-01-01", "")).to_csv(master, index=False)
    _write_upstream_authorities(tmp_path, source, master)
    monkeypatch.setattr(asset_growth, "MIN_MAPPING_COVERAGE", 0.0)
    monkeypatch.setattr(asset_growth, "MIN_UNIQUE_SECURITIES", 1)
    monkeypatch.setattr(asset_growth, "MIN_SECURITIES_PER_YEAR", 1)
    monkeypatch.setattr(asset_growth, "MIN_MEMBERSHIP_COVERAGE", 1.0)
    monkeypatch.setattr(asset_growth, "COVERAGE_YEARS", ("2021",))

    def fail_certification(*args, **kwargs):
        raise RuntimeError("injected certification failure")

    monkeypatch.setattr(asset_growth, "_atomic_certification", fail_certification)
    with pytest.raises(RuntimeError, match="injected certification failure"):
        materialize_asset_growth_features(repo_root=tmp_path)
    assert not (tmp_path / asset_growth.DEFAULT_OUTPUT).exists()
