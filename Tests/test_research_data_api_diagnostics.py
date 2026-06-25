from __future__ import annotations

from pathlib import Path

import pytest

from research_data import (
    load_data_trust_summary,
    load_dataset,
    load_dataset_diagnostics,
    load_dataset_with_diagnostics,
    load_prices,
    load_research_data_observability,
)
from research_data.hydration import write_json


def _seed_price_artifact(root: Path) -> None:
    write_json(
        root / "data/normalized/prices/ohlcv_prices.json",
        {
            "schema_version": "ohlcv_prices_normalized_v1",
            "row_count": 3,
            "rows": [
                {
                    "security_id": "YAHOO:SPY",
                    "trade_date": "2026-06-23",
                    "close": 599.0,
                    "as_of_date": "2026-06-23",
                },
                {
                    "security_id": "YAHOO:SPY",
                    "trade_date": "2026-06-24",
                    "close": 600.0,
                    "as_of_date": "2026-06-24",
                },
                {
                    "security_id": "YAHOO:QQQ",
                    "trade_date": "2026-06-24",
                    "close": 530.0,
                    "as_of_date": "2026-06-25",
                },
            ],
        },
    )


def _seed_observability(root: Path) -> None:
    write_json(
        root / "data/manifests/research_data_observability.json",
        {
            "schema_version": "research_data_observability_v1",
            "generated_at": "2026-06-24T12:00:00Z",
            "as_of_date": "2026-06-24",
            "datasets": [
                {
                    "dataset_id": "ohlcv_prices",
                    "dataset_name": "OHLCV prices",
                    "tier": "Tier 1",
                    "domain": "prices",
                    "readiness_status": "OBSERVE_ONLY",
                    "validation_status": "PASS",
                    "freshness_status": "OK",
                    "PIT_safe_status": "PIT_SAFE_SAMPLE_AS_OF_DATED",
                    "lineage_status": "LINEAGE_RECORDED",
                    "artifact_exists": True,
                    "row_count": 1,
                }
            ],
        },
    )


def test_load_dataset_with_diagnostics_returns_rows_and_observability(tmp_path: Path) -> None:
    _seed_price_artifact(tmp_path)
    _seed_observability(tmp_path)

    payload = load_dataset_with_diagnostics("ohlcv_prices", repo_root=tmp_path)

    assert payload["rows"][0]["security_id"] == "YAHOO:SPY"
    assert payload["diagnostics"]["diagnostics_status"] == "OK"
    assert payload["diagnostics"]["validation_status"] == "PASS"
    assert payload["diagnostics"]["PIT_safe_status"] == "PIT_SAFE_SAMPLE_AS_OF_DATED"


def test_load_dataset_supports_pit_date_security_and_field_filters(tmp_path: Path) -> None:
    _seed_price_artifact(tmp_path)

    rows = load_prices(
        repo_root=tmp_path,
        as_of_date="2026-06-24",
        start_date="2026-06-24",
        end_date="2026-06-24",
        security_ids=["YAHOO:SPY"],
        fields=["security_id", "close"],
    )

    assert rows == [{"security_id": "YAHOO:SPY", "close": 600.0}]


def test_load_dataset_with_diagnostics_applies_row_filters(tmp_path: Path) -> None:
    _seed_price_artifact(tmp_path)
    _seed_observability(tmp_path)

    payload = load_dataset_with_diagnostics("ohlcv_prices", repo_root=tmp_path, security_ids=["YAHOO:QQQ"])

    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["security_id"] == "YAHOO:QQQ"
    assert payload["diagnostics"]["diagnostics_status"] == "OK"


def test_load_dataset_returns_empty_for_optional_unknown_dataset(tmp_path: Path) -> None:
    assert load_dataset("unknown_dataset", repo_root=tmp_path, required=False) == []


def test_load_dataset_diagnostics_can_fail_soft_when_manifest_missing(tmp_path: Path) -> None:
    diagnostics = load_dataset_diagnostics("ohlcv_prices", repo_root=tmp_path, required=False)

    assert diagnostics["diagnostics_status"] == "MISSING_OBSERVABILITY"


def test_load_dataset_diagnostics_fails_closed_when_required_row_missing(tmp_path: Path) -> None:
    _seed_observability(tmp_path)

    with pytest.raises(KeyError):
        load_dataset_diagnostics("unknown_dataset", repo_root=tmp_path)


def test_load_observability_and_data_trust_summary(tmp_path: Path) -> None:
    _seed_observability(tmp_path)
    write_json(
        tmp_path / "outputs/data_trust/data_trust_summary.json",
        {
            "schema_version": "research_data_trust_summary_v1",
            "readiness_status": "WARN",
        },
    )

    assert load_research_data_observability(repo_root=tmp_path)["schema_version"] == "research_data_observability_v1"
    assert load_data_trust_summary(repo_root=tmp_path)["readiness_status"] == "WARN"
