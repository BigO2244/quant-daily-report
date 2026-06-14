from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.restate_shadow_nav_same_day import (
    BENCHMARK_SLUG,
    MODEL_SLUGS,
    RETURN_CONVENTION,
    build_restatement,
    replace_active,
    sha256_file,
    write_staging,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_price_panel(path: Path) -> None:
    rows = [
        {"date": "2026-06-01", "ticker": "AAA", "open": 100, "high": 100, "low": 100, "close": 100.0, "volume": 1},
        {"date": "2026-06-02", "ticker": "AAA", "open": 101, "high": 101, "low": 101, "close": 101.0, "volume": 1},
        {"date": "2026-06-03", "ticker": "AAA", "open": 103.02, "high": 103.02, "low": 103.02, "close": 103.02, "volume": 1},
        {"date": "2026-06-01", "ticker": "SPY", "open": 500, "high": 500, "low": 500, "close": 500.0, "volume": 1},
        {"date": "2026-06-02", "ticker": "SPY", "open": 505, "high": 505, "low": 505, "close": 505.0, "volume": 1},
        {"date": "2026-06-03", "ticker": "SPY", "open": 515.1, "high": 515.1, "low": 515.1, "close": 515.1, "volume": 1},
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def _write_dated_artifacts(output_root: Path, *, bad_return: bool = False) -> None:
    daily_returns = {"2026-06-02": 0.01, "2026-06-03": 0.02}
    previous_nav = {slug: 1.0 for slug in (*MODEL_SLUGS, BENCHMARK_SLUG)}
    for date, daily_return in daily_returns.items():
        dated_dir = output_root / date
        for slug in MODEL_SLUGS:
            _write_json(
                dated_dir / f"{slug}.json",
                {
                    "strategy_slug": slug,
                    "trade_date": date,
                    "target_weights": {"AAA": 1.0},
                },
            )
        strategies = {}
        for slug in (*MODEL_SLUGS, BENCHMARK_SLUG):
            recorded = 0.50 if bad_return and date == "2026-06-03" and slug == MODEL_SLUGS[0] else daily_return
            nav = round(previous_nav[slug] * (1.0 + recorded), 10)
            strategies[slug] = {
                "daily_return": recorded,
                "previous_nav": previous_nav[slug],
                "nav": nav,
            }
            previous_nav[slug] = nav
        _write_json(
            dated_dir / "shadow_performance.json",
            {
                "trade_date": date,
                "previous_trade_date": "2026-06-01" if date == "2026-06-02" else "2026-06-02",
                "status": "OK",
                "data_status": "OK",
                "return_convention": "weights_as_of_t",
                "strategies": strategies,
            },
        )


def _write_no_data_artifact(output_root: Path, date: str) -> None:
    _write_json(
        output_root / date / "shadow_performance.json",
        {
            "trade_date": date,
            "previous_trade_date": "2026-06-03",
            "status": "OK",
            "data_status": "NO_DATA",
            "data_reason": "NO_DATA_FOR_TRADE_DATE",
            "strategies": {},
        },
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_build_restatement_reconstructs_same_day_operational_nav(tmp_path: Path) -> None:
    output_root = tmp_path / "outputs" / "shadow_candidates"
    price_path = tmp_path / "outputs" / "research" / "flow_detection_v1" / "price_panel.parquet"
    _write_price_panel(price_path)
    _write_dated_artifacts(output_root)

    rows, summary, manifest, validation_rows = build_restatement(
        repo_root=tmp_path,
        output_root=output_root,
        price_cache_path=price_path,
        start_date=None,
        end_date=None,
        tolerance=1e-9,
    )

    assert [row["date"] for row in rows] == ["2026-06-02", "2026-06-03"]
    assert rows[-1][MODEL_SLUGS[0]] == 1.0302
    assert rows[-1][BENCHMARK_SLUG] == 1.0302
    assert summary["return_convention"] == RETURN_CONVENTION
    assert summary["legacy_shadow_nav_series_status"] == "SUPERSEDED_BY_OWNER_DECISION"
    assert manifest["daily_return_validation_status"] == "PASS"
    assert {row["status"] for row in validation_rows} == {"EXACT_MATCH"}


def test_build_restatement_skips_non_trading_no_data_artifact(tmp_path: Path) -> None:
    output_root = tmp_path / "outputs" / "shadow_candidates"
    price_path = tmp_path / "outputs" / "research" / "flow_detection_v1" / "price_panel.parquet"
    _write_price_panel(price_path)
    _write_dated_artifacts(output_root)
    _write_no_data_artifact(output_root, "2026-06-04")

    rows, _summary, manifest, _validation_rows = build_restatement(
        repo_root=tmp_path,
        output_root=output_root,
        price_cache_path=price_path,
        start_date=None,
        end_date=None,
        tolerance=1e-9,
    )

    assert [row["date"] for row in rows] == ["2026-06-02", "2026-06-03"]
    assert manifest["skipped_records"] == [
        {
            "date": "2026-06-04",
            "status": "OK",
            "data_status": "NO_DATA",
            "reason": "non-trading NO_DATA artifact skipped; no price returns for date",
        }
    ]


def test_build_restatement_blocks_when_daily_return_is_not_pit_reconstructable(tmp_path: Path) -> None:
    output_root = tmp_path / "outputs" / "shadow_candidates"
    price_path = tmp_path / "outputs" / "research" / "flow_detection_v1" / "price_panel.parquet"
    _write_price_panel(price_path)
    _write_dated_artifacts(output_root, bad_return=True)

    with pytest.raises(RuntimeError, match="daily return validation blocked restatement"):
        build_restatement(
            repo_root=tmp_path,
            output_root=output_root,
            price_cache_path=price_path,
            start_date=None,
            end_date=None,
            tolerance=1e-9,
        )


def test_replace_active_creates_backup_and_replaces_only_performance_artifacts(tmp_path: Path) -> None:
    output_root = tmp_path / "outputs" / "shadow_candidates"
    price_path = tmp_path / "outputs" / "research" / "flow_detection_v1" / "price_panel.parquet"
    _write_price_panel(price_path)
    _write_dated_artifacts(output_root)
    active_nav = output_root / "performance" / "shadow_nav_series.csv"
    active_summary = output_root / "performance" / "shadow_summary.json"
    active_nav.parent.mkdir(parents=True, exist_ok=True)
    active_nav.write_text(
        "date,caerus_polaris,caerus_orion,caerus_lyra,spy_benchmark\n"
        "2026-06-01,99,99,99,99\n",
        encoding="utf-8",
    )
    active_summary.write_text('{"legacy": true}\n', encoding="utf-8")
    before_nav_sha = sha256_file(active_nav)
    before_summary_sha = sha256_file(active_summary)

    rows, summary, manifest, validation_rows = build_restatement(
        repo_root=tmp_path,
        output_root=output_root,
        price_cache_path=price_path,
        start_date=None,
        end_date=None,
        tolerance=1e-9,
    )
    staging = write_staging(
        staging_dir=tmp_path / "staging",
        rows=rows,
        summary=summary,
        manifest=manifest,
        validation_rows=validation_rows,
    )
    replacement = replace_active(
        repo_root=tmp_path,
        output_root=output_root,
        backup_root=tmp_path / "outputs" / "recovery_backups",
        staging_info=staging,
        manifest=manifest,
        expected_existing_nav_sha256=before_nav_sha,
        expected_existing_summary_sha256=before_summary_sha,
    )

    assert sha256_file(active_nav) == staging["nav_sha256"]
    assert sha256_file(active_summary) == staging["summary_sha256"]
    assert Path(replacement["backup_dir"]).joinpath("backup_manifest.json").exists()
    assert (output_root / "performance" / "shadow_nav_restatement_manifest.json").exists()
    assert _read_csv(active_nav)[-1]["date"] == "2026-06-03"
