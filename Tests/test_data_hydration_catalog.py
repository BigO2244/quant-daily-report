from __future__ import annotations

from research_data.catalog import REQUIRED_CATALOG_FIELDS, catalog_entries, write_catalog
from scripts.data_hydration.validate_research_data_catalog import validate_catalog_artifact


def test_catalog_contains_required_fr_dh_datasets() -> None:
    entries = catalog_entries()
    ids = {entry["dataset_id"] for entry in entries}

    assert len(entries) >= 20
    assert {
        "ohlcv_prices",
        "security_master_pit",
        "corporate_actions",
        "dataset_freshness",
        "fundamentals_pit",
        "fundamental_features",
        "macro_regime_features",
        "macro_rates",
        "yield_curve",
        "credit_spreads",
        "vix_volatility_regime",
        "insider_form4",
        "sec_8k_events",
        "sec_10q_10k_metadata",
        "etf_index_constituents",
        "short_interest",
        "options_iv_open_interest",
        "analyst_estimate_revisions",
        "news_metadata",
        "news_sentiment_embeddings",
        "institutional_13f",
        "alternative_datasets",
    } <= ids


def test_catalog_entries_have_required_fields() -> None:
    for entry in catalog_entries():
        for field in REQUIRED_CATALOG_FIELDS:
            assert field in entry, f"{entry.get('dataset_id')} missing {field}"
            assert entry[field] not in (None, "", []), f"{entry.get('dataset_id')} empty {field}"


def test_p1_catalog_entries_mark_read_only_observe_state() -> None:
    entries = {entry["dataset_id"]: entry for entry in catalog_entries()}

    for dataset_id in ("ohlcv_prices", "security_master_pit", "corporate_actions", "dataset_freshness"):
        assert entries[dataset_id]["status"] == "OBSERVE_ONLY"


def test_p2_normalized_catalog_entries_mark_read_only_observe_state() -> None:
    entries = {entry["dataset_id"]: entry for entry in catalog_entries()}

    for dataset_id in (
        "fundamentals_pit",
        "macro_rates",
        "yield_curve",
        "credit_spreads",
        "vix_volatility_regime",
        "insider_form4",
        "sec_8k_events",
        "sec_10q_10k_metadata",
    ):
        assert entries[dataset_id]["status"] == "OBSERVE_ONLY"


def test_feature_catalog_entries_mark_read_only_observe_state() -> None:
    entries = {entry["dataset_id"]: entry for entry in catalog_entries()}

    assert entries["fundamental_features"]["status"] == "OBSERVE_ONLY"
    assert entries["fundamental_features"]["canonical_artifact_name"] == "features.json"
    assert entries["macro_regime_features"]["status"] == "OBSERVE_ONLY"
    assert entries["macro_regime_features"]["canonical_artifact_name"] == "features.json"


def test_p3_normalized_catalog_entries_mark_read_only_observe_state() -> None:
    entries = {entry["dataset_id"]: entry for entry in catalog_entries()}

    for dataset_id in ("etf_index_constituents", "institutional_13f", "news_metadata"):
        assert entries[dataset_id]["status"] == "OBSERVE_ONLY"


def test_catalog_artifact_validator_accepts_generated_catalog(tmp_path) -> None:
    path = tmp_path / "data" / "manifests" / "research_data_catalog.json"
    write_catalog(path)

    assert validate_catalog_artifact(path) == []
