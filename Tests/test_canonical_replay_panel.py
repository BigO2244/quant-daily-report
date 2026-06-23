from __future__ import annotations

import csv
from pathlib import Path

from research.canonical_replay_panel import build_canonical_price_panel


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})


def _seed_repo(tmp_path: Path) -> Path:
    root = tmp_path
    pit = root / "data" / "pit_universe"
    _write_csv(
        pit / "security_master.csv",
        ["security_id", "permaticker", "ticker", "name", "exchange", "category", "isdelisted",
         "firstpricedate", "lastpricedate", "relatedtickers", "confidence", "source"],
        [
            {"security_id": "SHARADAR:1", "permaticker": "1", "ticker": "AAPL", "name": "AAPL",
             "exchange": "NASDAQ", "category": "Domestic Common Stock", "isdelisted": "N",
             "firstpricedate": "2010-01-01", "lastpricedate": "2026-01-01", "confidence": "HIGH",
             "source": "fixture"},
            {"security_id": "SHARADAR:2", "permaticker": "2", "ticker": "TWTR", "name": "TWTR",
             "exchange": "NYSE", "category": "Domestic Common Stock", "isdelisted": "Y",
             "firstpricedate": "2013-11-07", "lastpricedate": "2022-10-27", "confidence": "HIGH",
             "source": "fixture"},
        ],
    )
    _write_csv(
        pit / "membership_universe_large_cap.csv",
        ["security_id", "ticker", "membership_family", "membership_start_date", "membership_end_date",
         "scale_source", "source", "confidence"],
        [
            {"security_id": "SHARADAR:1", "ticker": "AAPL", "membership_family": "caerus_large_cap",
             "membership_start_date": "2010-01-01", "membership_end_date": "",
             "scale_source": "scalemarketcap", "source": "fixture", "confidence": "HIGH"},
            {"security_id": "SHARADAR:2", "ticker": "TWTR", "membership_family": "caerus_large_cap",
             "membership_start_date": "2013-11-07", "membership_end_date": "2022-10-27",
             "scale_source": "scalemarketcap", "source": "fixture", "confidence": "HIGH"},
        ],
    )
    sep = root / "data" / "research_cache" / "sharadar_sep"
    _write_csv(
        sep / "AAPL.csv",
        ["date", "closeadj", "close"],
        [
            {"date": "2012-01-02", "closeadj": 10, "close": 10},
            {"date": "2014-01-02", "closeadj": 20, "close": 20},
            {"date": "2023-01-03", "closeadj": 30, "close": 30},
        ],
    )
    _write_csv(
        sep / "TWTR.csv",
        ["date", "closeadj", "close"],
        [
            {"date": "2012-01-02", "closeadj": 1, "close": 1},
            {"date": "2014-01-02", "closeadj": 2, "close": 2},
            {"date": "2023-01-03", "closeadj": 3, "close": 3},
        ],
    )
    return root


def test_canonical_price_panel_is_security_id_keyed_and_membership_filtered(tmp_path: Path) -> None:
    root = _seed_repo(tmp_path)
    result = build_canonical_price_panel(repo_root=root, start_date="2012-01-01", end_date="2023-12-31")
    panel = result.panel

    assert "security_id" in panel.columns
    assert "ticker" not in panel.columns
    assert panel.duplicated(["date", "security_id"]).sum() == 0
    assert set(panel["display_ticker"]) == {"AAPL", "TWTR"}
    assert set(panel["security_id"]) == {"SHARADAR:1", "SHARADAR:2"}
    assert panel[(panel["display_ticker"] == "TWTR") & (panel["date"] == "2023-01-03")].empty
    assert result.manifest["identity_key"] == "security_id"
    assert result.manifest["ticker_role"] == "display_only"
    assert result.manifest["membership_scale_precision"] == "PIT_APPROXIMATE_SCALE"
    assert result.manifest["membership_certification_status"] == "FAIL"
    assert result.manifest["membership_certification_methods"] == ["CURRENT_SCALE_APPROXIMATION"]
    assert result.manifest["decision_grade_blockers"] == [
        "CURRENT_SCALE_MEMBERSHIP_NOT_DECISION_GRADE",
        "PIT_DATE_EFFECTIVE_LARGE_CAP_MEMBERSHIP_REQUIRED",
    ]


def test_canonical_price_panel_records_missing_price_files(tmp_path: Path) -> None:
    root = _seed_repo(tmp_path)
    (root / "data" / "research_cache" / "sharadar_sep" / "TWTR.csv").unlink()
    result = build_canonical_price_panel(repo_root=root, start_date="2014-01-01", end_date="2014-12-31")
    assert result.manifest["missing_price_file_count"] == 1
    assert result.manifest["missing_price_file_sample"] == ["TWTR"]
    assert set(result.panel["display_ticker"]) == {"AAPL"}
