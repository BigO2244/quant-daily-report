from __future__ import annotations

from pathlib import Path
from datetime import date, timedelta

from research_data.hydration import write_json
from research_data.core_momentum_parity import build_core_momentum_parity_summary
from research_data.parity import build_sleeve_parity_report


def _seed_migration(root: Path) -> None:
    write_json(
        root / "outputs/research/data_migration/2026-06-24/migration_readiness.json",
        {
            "schema_version": "sleeve_migration_readiness_v1",
            "as_of_date": "2026-06-24",
            "sleeves": [
                {
                    "sleeve_id": "orion",
                    "strategy_id": "caerus_orion",
                    "family": "core_momentum",
                    "lifecycle_stage": "shadow_observed",
                    "required_dataset_ids": ["ohlcv_prices", "security_master_pit", "corporate_actions", "dataset_freshness"],
                    "migration_readiness_status": "READY_OBSERVE_ONLY",
                    "dataset_requirements": [],
                },
                {
                    "sleeve_id": "polaris",
                    "strategy_id": "caerus_polaris",
                    "family": "core_momentum",
                    "lifecycle_stage": "paper_observed",
                    "required_dataset_ids": ["ohlcv_prices", "security_master_pit", "corporate_actions", "dataset_freshness"],
                    "migration_readiness_status": "READY_OBSERVE_ONLY",
                    "dataset_requirements": [],
                },
            ],
        },
    )


def _seed_legacy(root: Path) -> None:
    write_json(
        root / "outputs/shadow_candidates/2026-06-20/caerus_polaris.json",
        {
            "strategy_slug": "caerus_polaris",
            "trade_date": "2026-06-20",
            "effective_trade_date": "2026-06-20",
            "holdings": [
                {"ticker": "AAA", "target_weight": 0.6, "momentum_rank": 1, "momentum_score": 2.0},
                {"ticker": "BBB", "target_weight": 0.4, "momentum_rank": 2, "momentum_score": 1.5},
            ],
        },
    )


def _seed_normalized(root: Path, *, full_coverage: bool) -> None:
    price_symbols = ["AAA", "BBB"] if full_coverage else ["SPY"]
    security_symbols = ["AAA", "BBB"] if full_coverage else ["ZZZ"]
    price_rows = []
    for symbol in price_symbols:
        start = date(2025, 6, 1)
        for idx in range(270 if full_coverage else 1):
            close = 100.0 + idx * (2.0 if symbol == "AAA" else 1.0)
            price_rows.append(
                {
                    "security_id": f"TEST:{symbol}",
                    "source_symbol": symbol,
                    "trade_date": (start + timedelta(days=idx)).isoformat(),
                    "as_of_date": "2026-06-24",
                    "close": close,
                    "close_adjusted": close,
                }
            )
    write_json(
        root / "data/normalized/prices/ohlcv_prices.json",
        {
            "dataset_id": "ohlcv_prices",
            "coverage": {"covered_symbols": price_symbols},
            "rows": price_rows,
        },
    )
    write_json(
        root / "data/normalized/security_master/security_master.json",
        {
            "dataset_id": "security_master_pit",
            "rows": [
                {"security_id": f"TEST:{symbol}", "ticker": symbol, "as_of_date": "2026-06-24", "is_active": True}
                for symbol in security_symbols
            ],
        },
    )
    write_json(
        root / "data/normalized/corporate_actions/actions.json",
        {
            "dataset_id": "corporate_actions",
            "coverage": {"query_covered_symbols": ["AAA", "BBB"] if full_coverage else ["AAA"], "covered_symbols": ["AAA", "BBB"] if full_coverage else ["AAA"]},
            "rows": [{"security_id": "TEST:AAA", "source_symbol": "AAA", "as_of_date": "2026-06-24"}],
        },
    )
    freshness_rows = [
        {"dataset_id": "ohlcv_prices", "as_of_date": "2026-06-24"},
        {"dataset_id": "security_master_pit", "as_of_date": "2026-06-24"},
        {"dataset_id": "corporate_actions", "as_of_date": "2026-06-24"},
    ]
    write_json(root / "data/normalized/freshness/dataset_freshness.json", {"dataset_id": "dataset_freshness", "rows": freshness_rows})


def test_sleeve_parity_selects_polaris_baseline_and_blocks_missing_canonical_coverage(tmp_path: Path) -> None:
    _seed_migration(tmp_path)
    _seed_legacy(tmp_path)
    _seed_normalized(tmp_path, full_coverage=False)

    payload = build_sleeve_parity_report(repo_root=tmp_path, as_of_date="2026-06-24")

    assert payload["selected_sleeve"]["sleeve_id"] == "polaris"
    assert payload["parity_status"] == "BLOCKED"
    assert payload["recommendation"] == "BLOCKED_CANONICAL_INPUT_COVERAGE"
    assert payload["fail_reasons"] == ["FR_DH_INPUT_COVERAGE_INSUFFICIENT"]
    assert payload["broker_submission_invoked"] is False
    assert payload["sleeve_runtime_invoked"] is False
    assert payload["allocation_mutation_invoked"] is False
    assert payload["signal_parity"]["signal_replay_supported"] is False
    assert payload["output_parity"]["missing_symbol_count"] == 2
    assert (tmp_path / "outputs/research/data_migration/2026-06-24/sleeve_parity_polaris.json").exists()


def test_sleeve_parity_runs_canonical_signal_adapter_when_input_coverage_is_complete(tmp_path: Path) -> None:
    _seed_migration(tmp_path)
    _seed_legacy(tmp_path)
    _seed_normalized(tmp_path, full_coverage=True)

    payload = build_sleeve_parity_report(repo_root=tmp_path, as_of_date="2026-06-24", sleeve_id="polaris")

    assert payload["input_parity_status"] == "PASS"
    assert payload["parity_status"] == "WARN"
    assert payload["recommendation"] == "PARITY_PASS_WITH_WARNINGS"
    assert payload["signal_parity"]["signal_replay_supported"] is True
    assert payload["signal_parity"]["signal_parity_status"] == "PASS"
    assert [row["input_parity_status"] for row in payload["per_symbol_diagnostics"]] == ["PASS", "PASS"]


def test_sleeve_parity_uses_orion_rank_decay_adapter(tmp_path: Path) -> None:
    _seed_migration(tmp_path)
    symbols = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    write_json(
        tmp_path / "outputs/shadow_candidates/2026-06-24/caerus_orion.json",
        {
            "strategy_slug": "caerus_orion",
            "trade_date": "2026-06-24",
            "effective_trade_date": "2026-06-24",
            "holdings": [
                {"ticker": "AAA", "target_weight": 0.2, "momentum_rank": 1, "momentum_score": 6.0},
                {"ticker": "BBB", "target_weight": 0.2, "momentum_rank": 2, "momentum_score": 5.0},
                {"ticker": "CCC", "target_weight": 0.2, "momentum_rank": 3, "momentum_score": 4.0},
                {"ticker": "DDD", "target_weight": 0.2, "momentum_rank": 4, "momentum_score": 3.0},
                {"ticker": "FFF", "target_weight": 0.2, "momentum_rank": 6, "momentum_score": 1.0},
            ],
            "rank_table": [
                {"ticker": "AAA", "momentum_rank": 1, "momentum_score": 6.0, "is_selected": True},
                {"ticker": "BBB", "momentum_rank": 2, "momentum_score": 5.0, "is_selected": True},
                {"ticker": "CCC", "momentum_rank": 3, "momentum_score": 4.0, "is_selected": True},
                {"ticker": "DDD", "momentum_rank": 4, "momentum_score": 3.0, "is_selected": True},
                {"ticker": "EEE", "momentum_rank": 5, "momentum_score": 2.0, "is_selected": False},
                {"ticker": "FFF", "momentum_rank": 6, "momentum_score": 1.0, "is_selected": True},
            ],
            "target_weights": {"AAA": 0.2, "BBB": 0.2, "CCC": 0.2, "DDD": 0.2, "FFF": 0.2},
        },
    )
    price_rows = []
    start = date(2025, 6, 1)
    rates = {"AAA": 0.010, "BBB": 0.009, "CCC": 0.008, "DDD": 0.007, "EEE": 0.006, "FFF": 0.005}
    for symbol in symbols:
        for idx in range(390):
            close = 100.0 * ((1.0 + rates[symbol]) ** idx)
            price_rows.append(
                {
                    "security_id": f"TEST:{symbol}",
                    "source_symbol": symbol,
                    "trade_date": (start + timedelta(days=idx)).isoformat(),
                    "as_of_date": "2026-06-24",
                    "close": close,
                    "close_adjusted": close,
                }
            )
    write_json(
        tmp_path / "data/normalized/prices/ohlcv_prices.json",
        {"dataset_id": "ohlcv_prices", "coverage": {"covered_symbols": symbols}, "rows": price_rows},
    )
    write_json(
        tmp_path / "data/normalized/security_master/security_master.json",
        {
            "dataset_id": "security_master_pit",
            "coverage": {"covered_symbols": symbols},
            "security_master_grade": {"status": "PIT_GRADE", "pit_grade_row_count": len(symbols), "current_reference_row_count": 0},
            "rows": [{"security_id": f"TEST:{symbol}", "ticker": symbol, "as_of_date": "2026-06-24", "is_active": True} for symbol in symbols],
        },
    )
    write_json(
        tmp_path / "data/normalized/corporate_actions/actions.json",
        {"dataset_id": "corporate_actions", "coverage": {"query_covered_symbols": symbols}, "rows": []},
    )
    write_json(
        tmp_path / "data/normalized/freshness/dataset_freshness.json",
        {
            "dataset_id": "dataset_freshness",
            "rows": [
                {"dataset_id": "ohlcv_prices", "as_of_date": "2026-06-24"},
                {"dataset_id": "security_master_pit", "as_of_date": "2026-06-24"},
                {"dataset_id": "corporate_actions", "as_of_date": "2026-06-24"},
            ],
        },
    )

    payload = build_sleeve_parity_report(repo_root=tmp_path, as_of_date="2026-06-24", sleeve_id="orion")

    assert payload["input_parity_status"] == "PASS"
    assert payload["signal_parity"]["adapter_name"] == "orion_rank_decay_canonical_momentum_v1_observe_only"
    assert payload["signal_parity"]["signal_parity_status"] == "PASS"
    assert payload["signal_parity"]["canonical_selected_symbols"] == ["AAA", "BBB", "CCC", "DDD", "FFF"]
    assert "FFF" in payload["signal_parity"]["rank_decay_kept_symbols"]
    assert "EEE" not in payload["signal_parity"]["canonical_selected_symbols"]
    assert payload["output_parity"]["output_parity_status"] == "PASS"


def test_sleeve_parity_records_missing_freshness_for_required_inputs(tmp_path: Path) -> None:
    _seed_migration(tmp_path)
    _seed_legacy(tmp_path)
    _seed_normalized(tmp_path, full_coverage=True)
    write_json(tmp_path / "data/normalized/freshness/dataset_freshness.json", {"dataset_id": "dataset_freshness", "rows": []})

    payload = build_sleeve_parity_report(repo_root=tmp_path, as_of_date="2026-06-24", sleeve_id="polaris")

    assert "DATASET_FRESHNESS_DOES_NOT_COVER_REQUIRED_INPUTS" in payload["fail_reasons"]
    assert payload["legacy_vs_fr_dh_inputs"]["missing_freshness_dataset_ids"] == [
        "corporate_actions",
        "ohlcv_prices",
        "security_master_pit",
    ]


def test_core_momentum_parity_summary_writes_daily_monitoring_artifacts(tmp_path: Path) -> None:
    _seed_core_momentum_ready_migration(tmp_path)
    _seed_core_momentum_legacy_candidates(tmp_path)
    _seed_normalized(tmp_path, full_coverage=True)

    payload = build_core_momentum_parity_summary(repo_root=tmp_path, as_of_date="2026-06-24")

    assert payload["schema_version"] == "core_momentum_parity_summary_v1"
    assert payload["overall_status"] == "PASS"
    assert payload["sleeve_count"] == 3
    assert payload["pass_count"] == 3
    assert payload["broker_submission_invoked"] is False
    assert payload["sleeve_runtime_invoked"] is False
    assert payload["allocation_mutation_invoked"] is False
    assert payload["warning_reasons"] == []
    assert payload["fail_reasons"] == []
    rows = {row["sleeve_id"]: row for row in payload["sleeves"]}
    assert set(rows) == {"polaris", "lyra", "orion"}
    for row in rows.values():
        assert row["parity_status"] == "PASS"
        assert row["input_parity_status"] == "PASS"
        assert row["signal_parity_status"] == "PASS"
        assert row["output_parity_status"] == "PASS"
        assert row["freshness_status"] == "OK"
        assert row["pit_security_master_status"] == "PIT_GRADE"
        assert row["missing_symbols"] == []
        assert row["broker_submission_invoked"] is False
    assert (tmp_path / "outputs/research/data_migration/2026-06-24/core_momentum_parity_summary.json").exists()
    markdown = (tmp_path / "outputs/research/data_migration/2026-06-24/core_momentum_parity_summary.md").read_text(encoding="utf-8")
    assert "Core Momentum FR-DH Parity Summary" in markdown
    assert "Runtime impact: read-only summary artifact only" in markdown


def _seed_core_momentum_ready_migration(root: Path) -> None:
    required = ["ohlcv_prices", "security_master_pit", "corporate_actions", "dataset_freshness"]

    def sleeve(sleeve_id: str, strategy_id: str) -> dict[str, object]:
        return {
            "sleeve_id": sleeve_id,
            "strategy_id": strategy_id,
            "family": "core_momentum",
            "lifecycle_stage": "paper_observed" if sleeve_id == "polaris" else "shadow_observed",
            "required_dataset_ids": required,
            "migration_readiness_status": "READY_OBSERVE_ONLY",
            "blocking_dataset_ids": [],
            "warning_dataset_ids": [],
            "dataset_requirements": [
                _ready_dataset_requirement("ohlcv_prices", "PIT_SAFE_SAMPLE_AS_OF_DATED"),
                _ready_dataset_requirement("security_master_pit", "PIT_GRADE_SHARADAR_TICKERS_DATE_WINDOWS"),
                _ready_dataset_requirement("corporate_actions", "PIT_SAFE_PUBLIC_CHART_EVENTS_AS_OF_DATED_NEEDS_VENDOR_AUDIT"),
                _ready_dataset_requirement("dataset_freshness", "PIT_SAFE_INTERNAL_RUN_METADATA"),
            ],
            "symbol_coverage": {
                "status": "READY",
                "required_symbols": ["AAA", "BBB"],
                "coverage_by_dataset": {
                    "ohlcv_prices": {"artifact_exists": True, "covered_symbols": ["AAA", "BBB"], "missing_symbols": [], "coverage_pct": 1.0, "row_count": 540},
                    "security_master_pit": {
                        "artifact_exists": True,
                        "covered_symbols": ["AAA", "BBB"],
                        "missing_symbols": [],
                        "coverage_pct": 1.0,
                        "row_count": 2,
                        "pit_grade_status": "PIT_GRADE",
                        "pit_grade_row_count": 2,
                        "current_reference_row_count": 0,
                    },
                    "corporate_actions": {"artifact_exists": True, "covered_symbols": ["AAA", "BBB"], "missing_symbols": [], "coverage_pct": 1.0, "row_count": 1},
                    "dataset_freshness": {
                        "artifact_exists": True,
                        "covered_dataset_ids": ["ohlcv_prices", "security_master_pit", "corporate_actions"],
                        "missing_dataset_ids": [],
                        "coverage_pct": 1.0,
                        "row_count": 3,
                    },
                },
                "dataset_requirements": [],
            },
        }

    write_json(
        root / "outputs/research/data_migration/2026-06-24/migration_readiness.json",
        {
            "schema_version": "sleeve_migration_readiness_v1",
            "as_of_date": "2026-06-24",
            "sleeves": [
                sleeve("polaris", "caerus_polaris"),
                sleeve("lyra", "caerus_lyra"),
                sleeve("orion", "caerus_orion"),
            ],
        },
    )


def _ready_dataset_requirement(dataset_id: str, pit_status: str) -> dict[str, object]:
    return {
        "dataset_id": dataset_id,
        "requirement_status": "READY",
        "readiness_status": "OBSERVE_ONLY",
        "validation_status": "PASS",
        "freshness_status": "OK",
        "PIT_safe_status": pit_status,
        "lineage_status": "LINEAGE_RECORDED",
        "artifact_exists": True,
        "row_count": 1,
        "reason": "Dataset has observe-only canonical artifact and no blocking diagnostic.",
    }


def _seed_core_momentum_legacy_candidates(root: Path) -> None:
    for slug in ("caerus_polaris", "caerus_lyra", "caerus_orion"):
        write_json(
            root / f"outputs/shadow_candidates/2026-06-24/{slug}.json",
            {
                "strategy_slug": slug,
                "trade_date": "2026-06-24",
                "effective_trade_date": "2026-06-24",
                "holdings": [
                    {"ticker": "AAA", "target_weight": 0.5, "momentum_rank": 1, "momentum_score": 2.0},
                    {"ticker": "BBB", "target_weight": 0.5, "momentum_rank": 2, "momentum_score": 1.5},
                ],
                "rank_table": [
                    {"ticker": "AAA", "momentum_rank": 1, "momentum_score": 2.0, "is_selected": True},
                    {"ticker": "BBB", "momentum_rank": 2, "momentum_score": 1.5, "is_selected": True},
                ],
                "target_weights": {"AAA": 0.5, "BBB": 0.5},
            },
        )
